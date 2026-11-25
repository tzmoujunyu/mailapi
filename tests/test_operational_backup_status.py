import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mailapi_shared.operational_backups import BackupManager, read_status
from mailapi_shared.operational_backup_status import configure


def fixture(root):
    data = root / 'data'
    data.mkdir()
    for path in [data / name for name in ('site_accounts.sqlite3', 'resources.sqlite3', 'test_features.sqlite3')] + [root / 'newapi.db']:
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE fixture(value TEXT)')
            db.execute("INSERT INTO fixture VALUES ('isolated scheduler')")
    (data / 'invoice.pdf').write_bytes(b'fixture invoice')
    (data / 'payment.png').write_bytes(b'fixture payment')
    recovery = root / 'application.key'
    recovery.write_bytes(os.urandom(32))
    key = root / 'backup.key'
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    config = {'environment': 'test', 'fixture_only': True, 'namespace': 'scheduler-fixture', 'data_root': str(data),
              'newapi_sqlite': str(root / 'newapi.db'), 'recovery_keys': {'application.key': str(recovery)},
              'encryption_key_file': str(key), 'backup_root': str(root / 'backups'), 'restore_root': str(root / 'restore')}
    path = root / 'config.json'
    path.write_text(json.dumps(config))
    return path, config


class BackupStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='mailapi-scheduler-fixture-'))
        self.path, self.config = fixture(Path(self.temporary).resolve())
        self.app = FastAPI()

    def configured(self):
        return configure(self.app, {'MAILAPI_MODE': 'test', 'MAILAPI_BACKUP_CONFIG': str(self.path)})

    def test_disabled_without_test_mode_and_explicit_configuration(self):
        for env in ({}, {'MAILAPI_MODE': 'test'}, {'MAILAPI_MODE': 'production', 'MAILAPI_BACKUP_CONFIG': str(self.path)}):
            with patch('mailapi_shared.operational_backup_status.BackupManager') as manager:
                self.assertIsNone(configure(self.app, env))
                manager.assert_not_called()
            self.assertEqual(self.app.state.backup_status_provider(), {'configured': False, 'fixture_only': False})
            self.assertEqual(self.app.state.backup_notice_provider(), [])
        self.assertEqual(self.app.router.on_startup, [])
        self.assertFalse(Path(self.config['backup_root']).exists())

    def test_real_startup_tick_creates_and_verifies_fixture_then_stops(self):
        scheduler = self.configured()
        with TestClient(self.app):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = self.app.state.backup_status_provider()
                if state.get('last_drill_at'):
                    break
                time.sleep(.01)
            self.assertTrue(state['last_backup_at'])
            self.assertTrue(state['last_drill_at'])
            self.assertTrue(state['available'])
            self.assertTrue(state['fixture_only'])
            self.assertFalse(any(row['active'] for row in self.app.state.backup_notice_provider()))
        self.assertIsNone(scheduler.task)
        manager = BackupManager(self.config, restore_only=True)
        result = manager.restore(manager.restore_root / 'manual')
        self.assertEqual(result['databases'], 4)
        self.assertEqual((manager.restore_root / 'manual/data/invoice.pdf').read_bytes(), b'fixture invoice')
        self.assertEqual(len(read_status(manager.directory)['events']), 2)

    def test_failure_recovery_and_corrupt_status_use_safe_stable_observations(self):
        scheduler = self.configured()
        self.assertEqual(set(scheduler.tick()), {'backup', 'integrity'})
        manager = BackupManager(self.config)
        now = datetime.now(timezone.utc)
        manager._event('backup', False, now)
        manager._event('integrity', False, now)
        rows = {row['key']: row for row in scheduler.notices()}
        self.assertTrue(rows['backup:backup']['active'])
        self.assertTrue(rows['backup:integrity']['active'])
        manager.create(now)
        manager.verify(now)
        self.assertFalse(any(row['active'] for row in scheduler.notices()))
        status = manager.directory / 'status.json'
        saved = status.read_bytes()
        status.write_text('{broken secret path')
        notices = scheduler.notices()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['key'], 'backup:status')
        self.assertTrue(notices[0]['active'])
        self.assertNotIn('secret path', json.dumps(notices))
        self.assertNotIn(str(self.path.parent), json.dumps(scheduler.status()))
        self.assertFalse(scheduler.status()['available'])
        self.assertTrue(scheduler.status()['fixture_only'])
        status.write_bytes(saved)
        self.assertFalse(any(row['active'] for row in scheduler.notices()))

    def test_fixture_flag_requires_explicit_true(self):
        scheduler = self.configured()
        self.config.pop('fixture_only')
        for value in (None, False, 'true', True):
            config = self.config if value is None else self.config | {'fixture_only': value}
            self.path.write_text(json.dumps(config))
            self.assertIs(scheduler.status()['fixture_only'], value is True)

    def test_invalid_configuration_never_breaks_application_startup(self):
        self.path.write_text('{invalid private details')
        scheduler = self.configured()
        self.assertEqual(scheduler.tick(), {'success': False})
        self.assertTrue(scheduler.notices()[0]['active'])
        self.assertEqual(scheduler.status(), {'configured': True, 'available': False, 'fixture_only': False,
                                            'last_backup_at': None, 'last_drill_at': None})
        self.assertFalse(Path(self.config['backup_root']).exists())


class BackupLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_does_not_block_loop_and_shutdown_waits_for_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='mailapi-scheduler-fixture-') as directory:
            path, unused = fixture(Path(directory).resolve())
            scheduler = configure(FastAPI(), {'MAILAPI_MODE': 'test'}, config_path=path)
            entered, release = threading.Event(), threading.Event()
            def work():
                entered.set()
                release.wait(3)
                return {}
            with patch.object(scheduler, 'tick', side_effect=work):
                await scheduler.start()
                await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), timeout=3)
                await asyncio.wait_for(asyncio.sleep(.01), timeout=.5)
                stopping = asyncio.create_task(scheduler.stop())
                await asyncio.sleep(.01)
                self.assertFalse(stopping.done())
                release.set()
                await asyncio.wait_for(stopping, timeout=2)
            self.assertIsNone(scheduler.task)


if __name__ == '__main__':
    unittest.main()
