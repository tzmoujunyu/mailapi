"""Opt-in test backup jobs. No application routes or production scheduling."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from zoneinfo import ZoneInfo

from . import operational_backup_io as io

UTC = timezone.utc
BEIJING = ZoneInfo('Asia/Shanghai')
DB_NAMES = ('site_accounts.sqlite3', 'resources.sqlite3', 'test_features.sqlite3')


def _path(value):
    path = Path(value).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Symbolic links are not supported')
    return path.resolve()


def _overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def read_status(directory):
    """Safe administrator notification payload: IDs/times/actions, never secrets."""
    path = Path(directory) / 'status.json'
    if not path.exists():
        return {'format': 1, 'events': []}
    raw = json.loads(path.read_text())
    return {'format': 1, 'events': [{key: event[key] for key in
            ('id', 'time', 'action', 'success', 'summary')} for event in raw.get('events', [])],
            'last_success': raw.get('last_success', {}), 'last_attempt': raw.get('last_attempt', {})}


def _append_event(directory, action, success, now):
    status = read_status(directory)
    event = {'id': uuid.uuid4().hex, 'time': now.isoformat(), 'action': action, 'success': success,
             'summary': ('备份完成' if action == 'backup' else '隔离恢复校验完成') if success else
                        ('备份失败，请管理员检查备份任务' if action == 'backup' else '隔离恢复校验失败，请管理员检查备份')}
    status['events'] = (status['events'] + [event])[-100:]
    status.setdefault('last_attempt', {})[action] = event['time']
    if success:
        status.setdefault('last_success', {})[action] = event['time']
    io.private_json(directory / 'status.json', status)
    return event


def record_initialization_failure(config, action):
    """Report a missing key/source on later runs, only to an already owned root."""
    try:
        directory = _path(config['backup_root'])
        if config.get('environment') != 'test' or directory.stat().st_mode & 0o077:
            return False
        if json.loads((directory / 'owner.json').read_text()) != {'format': 1, 'namespace': config['namespace']}:
            return False
        with open(directory / 'job.lock', 'a', opener=lambda p, f: os.open(p, f, 0o600)) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _append_event(directory, action, False, datetime.now(UTC))
        return True
    except Exception:
        return False  # Invalid/unknown destinations must never be modified.


class BackupManager:
    def __init__(self, config, *, restore_only=False):
        if config.get('environment') != 'test':
            raise ValueError('Only explicitly configured test environments are supported')
        self.namespace = config['namespace']
        self.restore_only = restore_only
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', self.namespace):
            raise ValueError('Invalid backup namespace')
        self.data = _path(config['data_root'])
        self.directory = _path(config['backup_root'])
        self.restore_root = _path(config['restore_root'])
        self.key_file = _path(config['encryption_key_file'])
        newapi = _path(config['newapi_sqlite'])
        if not restore_only and (not self.data.is_dir() or not newapi.is_file()):
            raise ValueError('Required data sources are missing')
        if newapi in [self.data / name for name in DB_NAMES]:
            raise ValueError('NewAPI must have its own SQLite database')
        self.sources = {'data': self.data}
        self.databases = ['data/' + name for name in DB_NAMES]
        if newapi.is_relative_to(self.data):
            self.databases.append('data/' + newapi.relative_to(self.data).as_posix())
        else:
            self.sources['newapi/database.sqlite3'] = newapi
            self.databases.append('newapi/database.sqlite3')
        recovery_keys = config.get('recovery_keys', {})
        if not recovery_keys:
            raise ValueError('Application recovery keys must be configured explicitly')
        for section, prefix in (('recovery_keys', 'recovery'), ('extra_paths', 'extras')):
            for name, value in config.get(section, {}).items():
                io.safe_name(name)
                path = _path(value)
                if not restore_only and (not path.exists() or (section == 'recovery_keys' and not path.is_file())):
                    raise ValueError('An explicitly configured source is missing')
                if any(_overlap(path, source) for source in self.sources.values()):
                    raise ValueError('Source and external recovery mappings must not overlap')
                self.sources[prefix + '/' + name] = path
        protected = [*self.sources.values(), self.directory, self.restore_root, self.key_file]
        if any(_overlap(protected[i], other) for i in range(len(protected)) for other in protected[i + 1:]):
            raise ValueError('Sources, backup storage, restore storage and encryption key must be separate')
        if not self.key_file.is_file() or self.key_file.stat().st_mode & 0o077:
            raise ValueError('Backup encryption key must be a private file')
        self.key = self.key_file.read_bytes()
        if len(self.key) != 32:
            raise ValueError('Backup encryption key must contain exactly 32 random bytes')
        for root in (self.directory, self.restore_root):
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.stat().st_mode & 0o077:
                raise ValueError('Backup and restore directories must be private')
        owner = self.directory / 'owner.json'
        if owner.exists():
            if json.loads(owner.read_text()) != {'format': 1, 'namespace': self.namespace}:
                raise ValueError('Backup directory belongs to another namespace')
        elif any(self.directory.iterdir()):
            raise ValueError('Use a new empty backup directory')
        else:
            io.private_json(owner, {'format': 1, 'namespace': self.namespace})

    @contextmanager
    def _lock(self, name='job.lock'):
        with open(self.directory / name, 'a', opener=lambda p, f: os.open(p, f, 0o600)) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def _event(self, action, success, now):
        return _append_event(self.directory, action, success, now)

    def _catalog(self):
        path = self.directory / 'catalog.json'
        return json.loads(path.read_text()) if path.exists() else []

    def _archive(self, entry):
        name = entry['name']
        if not re.fullmatch(r'mailapi-[0-9a-f]{32}\.mbak', name):
            raise ValueError('Invalid registered backup name')
        path = self.directory / name
        if path.is_symlink() or not path.is_file() or io.digest(path) != entry['sha256']:
            raise ValueError('Registered backup integrity mismatch')
        return path

    def _retention(self, now):
        kept = []
        for entry in self._catalog():
            if datetime.fromisoformat(entry['time']) < now - timedelta(days=30):
                try:
                    self._archive(entry).unlink()
                except (ValueError, FileNotFoundError):
                    kept.append(entry)  # Unknown, changed or missing archives are never deleted.
            else:
                kept.append(entry)
        io.private_json(self.directory / 'catalog.json', kept)

    def create(self, now=None):
        if self.restore_only:
            raise ValueError('Restore-only configuration cannot create backups')
        now = now or datetime.now(UTC)
        with self._lock():
            try:
                # Reject hard links to the backup key as well as path overlap.
                files, unused = io.inventory(self.sources)
                if any(path.samefile(self.key_file) for path in files.values()):
                    raise ValueError('Backup encryption key must not enter the archive')
                with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=self.directory) as temporary:
                    stage = Path(temporary)
                    manifest = io.snapshot(self.sources, self.databases, stage / 'files')
                    io.encrypt_archive(stage / 'files', stage / 'encrypted', self.key)
                    # Only publish archives that can authenticate, restore and pass integrity checks.
                    io.decrypt_archive(stage / 'encrypted', stage / 'verify.tar', self.key)
                    io.extract_verified(stage / 'verify.tar', stage / 'verified')
                    name = 'mailapi-' + uuid.uuid4().hex + '.mbak'
                    entry = {'name': name, 'time': now.isoformat(), 'sha256': io.digest(stage / 'encrypted')}
                    (stage / 'encrypted').replace(self.directory / name)
                    io.private_json(self.directory / 'catalog.json', self._catalog() + [entry])
                self._retention(now)
                self._event('backup', True, now)
                return {'name': name, 'databases': len(manifest['databases']), 'files': len(manifest['files'])}
            except Exception:
                self._event('backup', False, now)
                raise

    def _restore(self, destination, entry):
        if destination.exists() or not destination.is_relative_to(self.restore_root) or destination == self.restore_root:
            raise ValueError('Restore requires a new directory inside the isolated restore root')
        if not destination.parent.is_dir():
            raise ValueError('Restore parent must already exist')
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=self.restore_root) as temporary:
            stage = Path(temporary)
            io.decrypt_archive(self._archive(entry), stage / 'archive.tar', self.key)
            result = io.extract_verified(stage / 'archive.tar', stage / 'files')
            (stage / 'files').rename(destination)
        return result

    def restore(self, destination, name=None):
        with self._lock():
            entries = [entry for entry in self._catalog() if name is None or entry['name'] == name]
            if not entries:
                raise ValueError('No registered backup is available')
            return self._restore(_path(destination), entries[-1])

    def verify(self, now=None):
        now = now or datetime.now(UTC)
        with self._lock():
            try:
                entries = self._catalog()
                if not entries:
                    raise ValueError('No registered backup is available')
                with tempfile.TemporaryDirectory(prefix='integrity-', dir=self.restore_root) as directory:
                    result = self._restore(Path(directory) / 'restored', entries[-1])
                self._event('integrity', True, now)
                return result
            except Exception:
                self._event('integrity', False, now)
                raise

    def run_due(self, now=None):
        """Idempotent daily 03:00 / Sunday 03:30 Beijing scheduler tick."""
        with self._lock('scheduler.lock'):
            return self._run_due(now)

    def _run_due(self, now):
        now = now or datetime.now(UTC)
        local = now.astimezone(BEIJING)
        daily = local.replace(hour=3, minute=0, second=0, microsecond=0)
        if local < daily:
            daily -= timedelta(days=1)
        weekly = local.replace(hour=3, minute=30, second=0, microsecond=0) - timedelta(days=(local.weekday() + 1) % 7)
        if local < weekly:
            weekly -= timedelta(days=7)
        results = {}
        for action, due, method in (('backup', daily, self.create), ('integrity', weekly, self.verify)):
            status = read_status(self.directory)
            last_time = status.get('last_attempt', {}).get(action)
            last = datetime.fromisoformat(last_time) if last_time else None
            if last is None or last < due:
                try:
                    results[action] = method(now)
                except Exception:
                    results[action] = {'success': False}
        return results
