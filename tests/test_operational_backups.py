from datetime import datetime, timedelta, timezone
import io as stream_io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from mailapi_shared import operational_backup_io as io
from mailapi_shared.operational_backups import BackupManager, read_status


class OperationalBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mailapi-backup-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir(mode=0o700)
        for path in [self.data / name for name in ('site_accounts.sqlite3', 'resources.sqlite3', 'test_features.sqlite3')] + [self.root / 'newapi.db']:
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY, value TEXT)')
                db.execute("INSERT INTO entries(value) VALUES ('fixture')")
        self.invoice = self.data / 'finance_files' / 'invoice.pdf'
        self.invoice.parent.mkdir()
        self.invoice.write_bytes(b'%PDF-fixture-invoice-private')
        self.payment = self.invoice.with_name('payment.png')
        self.payment.write_bytes(b'fixture-payment-private')
        self.recovery = self.root / 'credential-key.json'
        self.recovery.write_text('{"fixture":"application-recovery-secret"}')
        self.key = self.root / 'backup.key'
        self.key.write_bytes(os.urandom(32))
        self.key.chmod(0o600)
        extra = self.root / 'external-tips'
        extra.mkdir()
        (extra / 'tips.json').write_text('{"content":"fixture tip"}')
        self.config = {'environment': 'test', 'namespace': 'fixture', 'data_root': str(self.data),
                       'newapi_sqlite': str(self.root / 'newapi.db'), 'recovery_keys': {'credential.json': str(self.recovery)},
                       'extra_paths': {'codex-tips': str(extra)}, 'encryption_key_file': str(self.key),
                       'backup_root': str(self.root / 'backups'), 'restore_root': str(self.root / 'restore')}
        self.manager = BackupManager(self.config)
        self.now = datetime(2026, 11, 23, 19, tzinfo=timezone.utc)

    def hashes(self):
        return {str(path.relative_to(self.root)): io.digest(path) for path in
                [*self.data.rglob('*'), self.recovery, self.root / 'newapi.db'] if path.is_file()}

    def test_four_databases_files_external_keys_and_tips_restore_without_source_changes(self):
        before = self.hashes()
        result = self.manager.create(self.now)
        self.assertEqual(result['databases'], 4)
        archive = self.manager.directory / result['name']
        encrypted = archive.read_bytes()
        self.assertNotIn(b'fixture-invoice-private', encrypted)
        self.assertNotIn(b'application-recovery-secret', encrypted)
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        restored = self.manager.restore_root / 'manual'
        self.manager.restore(restored)
        self.assertEqual((restored / 'data/finance_files/invoice.pdf').read_bytes(), self.invoice.read_bytes())
        self.assertEqual((restored / 'data/finance_files/payment.png').read_bytes(), self.payment.read_bytes())
        self.assertEqual((restored / 'recovery/credential.json').read_bytes(), self.recovery.read_bytes())
        self.assertTrue((restored / 'extras/codex-tips/tips.json').exists())
        self.assertFalse(any(path.is_file() and path.read_bytes() == self.key.read_bytes() for path in restored.rglob('*')))
        with sqlite3.connect(restored / 'newapi/database.sqlite3') as db:
            self.assertEqual(db.execute('SELECT value FROM entries').fetchone()[0], 'fixture')
        self.manager.verify(self.now)
        self.assertEqual([p.name for p in self.manager.restore_root.iterdir()], ['manual'])
        self.assertEqual(self.hashes(), before)
        self.assertEqual(len(read_status(self.manager.directory)['events']), 2)

    def test_file_changes_retry_complete_snapshot(self):
        original = io.shutil.copyfile
        calls = []
        def copy(source, target):
            result = original(source, target)
            if source == self.invoice:
                calls.append(source)
                if len(calls) == 1:
                    self.invoice.write_bytes(b'updated invoice')
            return result
        with patch.object(io.shutil, 'copyfile', side_effect=copy):
            self.manager.create(self.now)
        self.assertEqual(len(calls), 2)
        self.manager.restore(self.manager.restore_root / 'check')
        self.assertEqual((self.manager.restore_root / 'check/data/finance_files/invoice.pdf').read_bytes(), b'updated invoice')

    def test_temporary_file_removed_during_inventory_retries_without_false_type_error(self):
        temporary = self.data / '.authorization-refresh.tmp'
        temporary.write_bytes(b'in-progress')
        original = Path.lstat
        removed = []
        def stat_path(path, *args, **kwargs):
            if path == temporary and not removed:
                removed.append(True)
                path.unlink()
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', stat_path):
            self.manager.create(self.now)
        self.assertEqual(removed, [True])
        destination = self.manager.restore_root / 'without-temporary'
        self.manager.restore(destination)
        self.assertFalse((destination / 'data/.authorization-refresh.tmp').exists())
        self.assertEqual((destination / 'data/finance_files/invoice.pdf').read_bytes(), self.invoice.read_bytes())

    def test_actual_nonregular_source_is_still_rejected(self):
        os.mkfifo(self.data / 'unexpected-pipe')
        with self.assertRaisesRegex(ValueError, 'non-regular'):
            self.manager.create(self.now)

    def test_database_changes_retry_and_capture_latest_values(self):
        original = io.digest
        mutations = []
        def digest(path):
            if path == self.invoice and not mutations:
                mutations.append(True)
                with sqlite3.connect(self.data / 'test_features.sqlite3') as db:
                    db.execute("UPDATE entries SET value='changed'")
            return original(path)
        with patch.object(io, 'digest', side_effect=digest):
            self.manager.create(self.now)
        self.manager.restore(self.manager.restore_root / 'latest')
        with sqlite3.connect(self.manager.restore_root / 'latest/data/test_features.sqlite3') as db:
            self.assertEqual(db.execute('SELECT value FROM entries').fetchone()[0], 'changed')

    def test_open_wal_database_includes_uncheckpointed_commits(self):
        source = self.data / 'test_features.sqlite3'
        db = sqlite3.connect(source)
        self.addCleanup(db.close)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute("UPDATE entries SET value='committed in WAL'")
        db.commit()
        self.assertTrue(Path(str(source) + '-wal').exists())
        self.manager.create(self.now)
        destination = self.manager.restore_root / 'wal'
        self.manager.restore(destination)
        with sqlite3.connect(destination / 'data/test_features.sqlite3') as restored:
            self.assertEqual(restored.execute('SELECT value FROM entries').fetchone()[0], 'committed in WAL')
        self.assertFalse(list(destination.rglob('*-wal')))

    def test_atomic_database_replacement_retries_snapshot(self):
        replacement = self.root / 'replacement.db'
        with sqlite3.connect(replacement) as db:
            db.execute('CREATE TABLE entries(value TEXT)')
            db.execute("INSERT INTO entries VALUES ('replacement')")
        original = io.shutil.copyfile
        copies = []
        def copy(source, target):
            result = original(source, target)
            if source == self.invoice:
                copies.append(True)
                if len(copies) == 1:
                    replacement.replace(self.data / 'resources.sqlite3')
            return result
        with patch.object(io.shutil, 'copyfile', side_effect=copy):
            self.manager.create(self.now)
        self.assertEqual(len(copies), 2)
        self.manager.restore(self.manager.restore_root / 'replacement')
        with sqlite3.connect(self.manager.restore_root / 'replacement/data/resources.sqlite3') as db:
            self.assertEqual(db.execute('SELECT value FROM entries').fetchone()[0], 'replacement')

    def test_continuous_changes_fail_without_success_archive_and_emit_safe_event(self):
        original = io.shutil.copyfile
        def copy(source, target):
            result = original(source, target)
            if source == self.invoice:
                self.invoice.write_bytes(self.invoice.read_bytes() + b'x')
            return result
        with patch.object(io.shutil, 'copyfile', side_effect=copy), self.assertRaises(RuntimeError):
            self.manager.create(self.now)
        self.assertFalse(list(self.manager.directory.glob('*.mbak')))
        self.assertFalse(list(self.manager.directory.glob('.snapshot-*')))
        event = read_status(self.manager.directory)['events'][0]
        self.assertFalse(event['success'])
        self.assertEqual(event['action'], 'backup')
        self.assertNotIn(str(self.root), json.dumps(event))
        self.assertEqual(event['id'], read_status(self.manager.directory)['events'][0]['id'])

    def test_tampering_is_rejected_before_any_restore_is_published(self):
        self.manager.create(self.now)
        catalog = self.manager._catalog()
        archive = self.manager.directory / catalog[0]['name']
        data = bytearray(archive.read_bytes())
        data[-30] ^= 1
        archive.write_bytes(data)
        catalog[0]['sha256'] = io.digest(archive)  # Even a changed local checksum cannot bypass GCM.
        io.private_json(self.manager.directory / 'catalog.json', catalog)
        with self.assertRaises(Exception):
            self.manager.verify(self.now)
        self.assertEqual(list(self.manager.restore_root.iterdir()), [])
        self.assertFalse(read_status(self.manager.directory)['events'][-1]['success'])

    def test_wrong_key_fails_authentication(self):
        self.manager.create(self.now)
        self.key.write_bytes(os.urandom(32))
        other = BackupManager(self.config)
        with self.assertRaises(Exception):
            other.restore(other.restore_root / 'wrong-key')
        self.assertFalse((other.restore_root / 'wrong-key').exists())

    def test_restore_survives_loss_of_original_data_and_application_keys(self):
        self.manager.create(self.now)
        shutil.rmtree(self.data)
        shutil.rmtree(self.root / 'external-tips')
        self.recovery.unlink()
        (self.root / 'newapi.db').unlink()
        recovery = BackupManager(self.config, restore_only=True)
        target = recovery.restore_root / 'lost-source'
        recovery.restore(target)
        self.assertTrue((target / 'recovery/credential.json').exists())
        self.assertTrue((target / 'newapi/database.sqlite3').exists())
        recovery.verify(self.now)
        with self.assertRaises(ValueError):
            recovery.create(self.now)

    def test_only_owned_unchanged_expired_archives_are_removed(self):
        first = self.manager.create(self.now - timedelta(days=33))['name']
        second = self.manager.create(self.now - timedelta(days=32))['name']
        changed = self.manager.directory / second
        changed.write_bytes(changed.read_bytes() + b'changed')
        unknown = self.manager.directory / ('mailapi-' + 'a' * 32 + '.mbak')
        unknown.write_bytes(b'unknown archive')
        self.manager.create(self.now)
        self.assertFalse((self.manager.directory / first).exists())
        self.assertTrue(changed.exists())
        self.assertEqual(unknown.read_bytes(), b'unknown archive')

    def test_scheduler_daily_and_weekly_beijing_due_are_idempotent(self):
        saturday = datetime(2026, 11, 20, 19, tzinfo=timezone.utc)
        self.manager.create(saturday)
        self.manager.verify(saturday)
        self.assertEqual(self.manager.run_due(datetime(2026, 11, 21, 18, 59, tzinfo=timezone.utc)), {})
        result = self.manager.run_due(datetime(2026, 11, 21, 19, tzinfo=timezone.utc))
        self.assertEqual(set(result), {'backup'})
        result = self.manager.run_due(datetime(2026, 11, 21, 19, 30, tzinfo=timezone.utc))
        self.assertEqual(set(result), {'integrity'})
        self.assertEqual(self.manager.run_due(datetime(2026, 11, 21, 19, 31, tzinfo=timezone.utc)), {})

    def test_scheduler_failure_is_recorded_once_per_period(self):
        self.manager.create(self.now - timedelta(hours=1))
        self.manager.verify(self.now - timedelta(hours=1))
        with patch.object(io, 'snapshot', side_effect=RuntimeError('private path secret')):
            result = self.manager.run_due(self.now)
            self.assertEqual(result, {'backup': {'success': False}})
            self.assertEqual(self.manager.run_due(self.now + timedelta(minutes=1)), {})

    def test_missing_db_source_symlink_and_hard_link_to_backup_key_fail(self):
        missing = self.data / 'resources.sqlite3'
        saved = missing.read_bytes()
        missing.unlink()
        with self.assertRaises(ValueError):
            self.manager.create(self.now)
        missing.write_bytes(saved)
        link = self.data / 'secret-link'
        link.symlink_to(self.key)
        with self.assertRaises(ValueError):
            self.manager.create(self.now)
        link.unlink()
        os.link(self.key, link)
        with self.assertRaises(ValueError):
            self.manager.create(self.now)
        self.assertFalse(list(self.manager.directory.glob('*.mbak')))

    def test_invalid_configuration_and_restore_destinations_are_rejected(self):
        for changes in ({'environment': 'production'}, {'recovery_keys': {}},
                        {'backup_root': str(self.data / 'backups')},
                        {'encryption_key_file': str(self.recovery)},
                        {'extra_paths': {'../outside': str(self.recovery)}},
                        {'recovery_keys': {'nested': str(self.invoice)}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                BackupManager({**self.config, **changes})
        self.manager.create(self.now)
        for destination in (self.data, self.root / 'outside', self.manager.restore_root):
            with self.assertRaises(ValueError):
                self.manager.restore(destination)
        child = self.manager.restore_root / 'existing'
        child.mkdir()
        with self.assertRaises(ValueError):
            self.manager.restore(child)

    def test_archive_path_traversal_and_symlinks_are_rejected(self):
        for index, name in enumerate(('../escape', '/absolute', 'safe-link')):
            archive = self.root / f'unsafe-{index}.tar'
            with tarfile.open(archive, 'w') as tar:
                member = tarfile.TarInfo(name)
                if name == 'safe-link':
                    member.type = tarfile.SYMTYPE
                    member.linkname = '../escape'
                else:
                    member.size = 1
                tar.addfile(member, stream_io.BytesIO(b'x'))
            with self.assertRaises(ValueError):
                io.extract_verified(archive, self.root / f'extracted-{index}')
        self.assertFalse((self.root / 'escape').exists())

    def test_cli_runs_against_fixture_only(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.config))
        command = [sys.executable, 'scripts/manage_backups.py', '--config', str(config)]
        for action in ('create', 'verify', 'status'):
            result = subprocess.run(command + [action], capture_output=True, text=True, check=True)
            self.assertIsInstance(json.loads(result.stdout), dict)
        self.key.unlink()
        result = subprocess.run(command + ['create'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(str(self.root), result.stdout)
        self.assertFalse(read_status(self.manager.directory)['events'][-1]['success'])


if __name__ == '__main__':
    unittest.main()
