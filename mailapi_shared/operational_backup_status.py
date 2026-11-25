"""Explicit test-only backup scheduler and safe administrator status providers."""
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import threading

from .operational_backups import BackupManager, _path, read_status, record_initialization_failure


def _timestamp(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Backup timestamps must include a timezone')
    return parsed.isoformat()


class BackupScheduler:
    def __init__(self, config_path):
        self.config_path = Path(config_path)
        self.lock = threading.Lock()
        self.failed = False
        self.stop_event = None
        self.task = None

    def _config(self):
        path = _path(self.config_path)
        config = json.loads(path.read_text())
        if config.get('environment') != 'test':
            raise ValueError('Only test backup configuration is accepted')
        return config

    def tick(self):
        config = None
        try:
            config = self._config()
            result = BackupManager(config).run_due()
            with self.lock:
                self.failed = False
            return result
        except BlockingIOError:
            return {}  # Another scheduler/process already owns this job.
        except Exception:
            with self.lock:
                previously_failed = self.failed
                self.failed = True
            if config and not previously_failed:
                record_initialization_failure(config, 'backup')
            return {'success': False}

    def _state(self):
        with self.lock:
            if self.failed:
                raise ValueError('Backup scheduler is unavailable')
        config = self._config()
        state = read_status(_path(config['backup_root']))
        latest = {}
        for event in state.get('events', []):
            if event['action'] in {'backup', 'integrity'}:
                if type(event['success']) is not bool:
                    raise ValueError('Invalid backup status')
                _timestamp(event['time'])
                latest[event['action']] = event['success']
        successful = state.get('last_success', {})
        return {'configured': True, 'available': True, 'fixture_only': config.get('fixture_only') is True,
                'last_backup_at': _timestamp(successful.get('backup')),
                'last_drill_at': _timestamp(successful.get('integrity'))}, latest

    def status(self):
        try:
            return self._state()[0]
        except Exception:
            try:
                fixture_only = self._config().get('fixture_only') is True
            except Exception:
                fixture_only = False
            return {'configured': True, 'available': False, 'fixture_only': fixture_only,
                    'last_backup_at': None, 'last_drill_at': None}

    def notices(self):
        error = {'key': 'backup:status', 'kind': 'backup', 'label': '备份配置或状态读取失败，请管理员检查', 'active': False}
        try:
            unused, latest = self._state()
        except Exception:
            return [error | {'active': True}]
        return [error, {'key': 'backup:backup', 'kind': 'backup', 'label': '备份失败，请管理员检查备份任务',
                        'active': latest.get('backup') is False},
                {'key': 'backup:integrity', 'kind': 'backup', 'label': '隔离恢复校验失败，请管理员检查备份',
                 'active': latest.get('integrity') is False}]

    async def _loop(self):
        while not self.stop_event.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def start(self):
        if self.task and not self.task.done():
            return
        self.stop_event = asyncio.Event()
        self.task = asyncio.create_task(self._loop())

    async def stop(self):
        if self.task:
            self.stop_event.set()
            # Let an in-flight snapshot finish cleanup instead of abandoning its thread.
            await self.task
            self.task = None


def configure(app, env=None, config_path=None):
    """Install only for test mode plus an explicit config path; never enable production."""
    env = os.environ if env is None else env
    app.state.backup_status_provider = lambda: {'configured': False, 'fixture_only': False}
    app.state.backup_notice_provider = lambda: []
    path = config_path or env.get('MAILAPI_BACKUP_CONFIG')
    if env.get('MAILAPI_MODE') != 'test' or not path:
        return None
    scheduler = BackupScheduler(path)
    app.state.backup_scheduler = scheduler
    app.state.backup_status_provider = scheduler.status
    app.state.backup_notice_provider = scheduler.notices
    app.router.add_event_handler('startup', scheduler.start)
    app.router.add_event_handler('shutdown', scheduler.stop)
    return scheduler
