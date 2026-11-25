"""Private snapshot and authenticated archive IO; never modifies source files."""
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tarfile
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b'MAILAPI-BACKUP-1\0'
CHUNK = 1024 * 1024


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute() or
            any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ValueError('Invalid archive mapping')
    return path


def private_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with open(temporary, 'w', encoding='utf-8', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def inventory(sources):
    files = {}
    for prefix, source in sources.items():
        paths = sorted(source.rglob('*')) if source.is_dir() else [source]
        for path in paths:
            mode = path.lstat().st_mode  # A vanished temporary file must trigger a whole-snapshot retry.
            if stat.S_ISLNK(mode):
                raise ValueError('Symbolic links are not supported')
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError('Source contains a non-regular file')
            name = prefix + '/' + path.relative_to(source).as_posix() if source.is_dir() else prefix
            safe_name(name)
            if name in files:
                raise ValueError('Duplicate archive mapping')
            files[name] = path
    databases = []
    for name, path in files.items():
        with path.open('rb') as stream:
            if stream.read(16) == b'SQLite format 3\0':
                databases.append(name)
    companions = {name + suffix for name in databases for suffix in ('-wal', '-shm', '-journal')}
    return {name: path for name, path in files.items() if name not in companions}, sorted(databases)


def database_integrity(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite integrity verification failed')


def snapshot(sources, required_databases, destination, *, attempts=3, excluded_file=None):
    for attempt in range(attempts):
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(mode=0o700)
        try:
            files, databases = inventory(sources)
            if excluded_file and any(path.samefile(excluded_file) for path in files.values()):
                raise ValueError('Backup encryption key must not enter the archive')
            if not set(required_databases).issubset(databases):
                raise ValueError('A required SQLite database is missing or invalid')
            with ExitStack() as stack:
                readers = [stack.enter_context(closing(sqlite3.connect(
                    files[name].resolve().as_uri() + '?mode=ro', uri=True, timeout=5))) for name in databases]
                identities = [(files[name].stat().st_dev, files[name].stat().st_ino) for name in databases]
                before = [db.execute('PRAGMA data_version').fetchone()[0] for db in readers]
                hashes = {name: digest(path) for name, path in files.items() if name not in databases}
                deadline = time.monotonic() + 60
                def progress(*unused):
                    if time.monotonic() > deadline:
                        raise TimeoutError('Snapshot copy timed out')
                for name, db in zip(databases, readers):
                    target = destination / name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with closing(sqlite3.connect(target)) as writer:
                        db.backup(writer, pages=256, sleep=.05, progress=progress)
                        writer.execute('PRAGMA journal_mode=DELETE')  # Standalone copy; never alter source WAL mode.
                    target.chmod(0o600)
                for name in hashes:
                    target = destination / name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.copyfile(files[name], target)
                    target.chmod(0o600)
                final_files, final_databases = inventory(sources)
                final_hashes = {name: digest(path) for name, path in final_files.items() if name not in final_databases}
                after = [db.execute('PRAGMA data_version').fetchone()[0] for db in readers]
                final_identities = [(final_files[name].stat().st_dev, final_files[name].stat().st_ino) for name in final_databases]
                stable = (before == after and identities == final_identities and databases == final_databases and hashes == final_hashes and
                          all(digest(destination / name) == value for name, value in hashes.items()))
            if stable:
                for name in databases:
                    database_integrity(destination / name)
                manifest = {'format': 1, 'databases': databases,
                            'files': {name: digest(destination / name) for name in files}}
                private_json(destination / 'manifest.json', manifest)
                return manifest
        except FileNotFoundError:
            pass  # Concurrent file replacement/deletion: repeat the whole snapshot.
    raise RuntimeError('Source kept changing; no consistent backup was created')


def encrypt_archive(snapshot_dir, output, key):
    """Stream tar into GCM without a second plaintext archive on disk."""
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    with open(output, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        stream.write(MAGIC + nonce)
        class Sink:
            def write(self, data):
                stream.write(encryptor.update(data))
                return len(data)
        with tarfile.open(fileobj=Sink(), mode='w|') as archive:
            for path in sorted(snapshot_dir.rglob('*')):
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(snapshot_dir).as_posix(), recursive=False)
        stream.write(encryptor.finalize())
        stream.write(encryptor.tag)
        stream.flush()
        os.fsync(stream.fileno())


def decrypt_archive(archive, plaintext, key):
    with archive.open('rb') as source:
        if source.read(len(MAGIC)) != MAGIC:
            raise ValueError('Unknown backup format')
        nonce = source.read(12)
        size = archive.stat().st_size - len(MAGIC) - 12 - 16
        if size < 0:
            raise ValueError('Truncated backup')
        source.seek(-16, os.SEEK_END)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, source.read(16))).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        source.seek(len(MAGIC) + 12)
        with open(plaintext, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as target:
            while size:
                chunk = source.read(min(CHUNK, size))
                if not chunk:
                    raise ValueError('Truncated backup')
                size -= len(chunk)
                target.write(decryptor.update(chunk))
            target.write(decryptor.finalize())  # Authenticate before parsing or extracting.


def extract_verified(plaintext, destination):
    destination.mkdir(mode=0o700)
    names = set()
    with tarfile.open(plaintext, 'r:') as archive:
        for member in archive:
            safe_name(member.name)
            if not member.isfile() or member.name in names:
                raise ValueError('Unsafe archive member')
            names.add(member.name)
            target = destination / member.name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.extractfile(member) as source, open(
                    target, 'xb', opener=lambda p, f: os.open(p, f, 0o600)) as output:
                shutil.copyfileobj(source, output, CHUNK)
    manifest = json.loads((destination / 'manifest.json').read_text())
    if manifest.get('format') != 1 or names != set(manifest['files']) | {'manifest.json'}:
        raise ValueError('Archive manifest mismatch')
    for name, expected in manifest['files'].items():
        safe_name(name)
        if digest(destination / name) != expected:
            raise ValueError('Backup file integrity verification failed')
    for name in manifest['databases']:
        if name not in manifest['files']:
            raise ValueError('Database missing from manifest')
        database_integrity(destination / name)
    return {'files': len(manifest['files']), 'databases': len(manifest['databases'])}
