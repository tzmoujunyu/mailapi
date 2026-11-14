"""Offline migration, rotation and backups for isolated test data only."""
import argparse
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from cryptography.fernet import Fernet

from .account_credentials import open_legacy_restore_point
from .configuration import dotenv_entries
from .credential_crypto import CredentialCryptoError, CredentialKeyring
from .oauth_files import OAuthFiles
from .resource_database import DATABASE_NAME as RESOURCE_NAME
from .resource_database import ENCRYPTED_RESTORE_FIELDS, FIELDS, GPT, SECRET_DIRECTORY
from .resource_database import ResourceDatabase, create_resource_database
from .site_database import DATABASE_NAME as SITE_NAME
from .site_database import read_site_snapshot, write_site_database
from .site_accounts import SiteAccountStore


REMAINING_DATA = {
    "codex_auth", "codex_auth.json", "gmail_tokens", "outlook_tokens", "proton_sessions", "proton.env",
    "auth_artifact_versions", "auth_artifact_versions.json", "codex_tips.json", "codex_tip_images",
    "history", "history_id", "mail_filters.json", "mail_relay_secret", "credentials.json",
}
LEGACY_RESTORE_KEY = "account_credentials.restore.key"
OAUTH_DIRECTORIES = {"gmail_tokens", "outlook_tokens", "codex_auth"}
OAUTH_PROVIDERS = {"gmail", "outlook", "codex"}
LEGACY_OAUTH = re.compile(r"(?:token_account\d+\.json|codex_auth\.json)(?:\.bak\..+)?")


def _paths(source, destination):
    source, destination = source.expanduser().absolute(), destination.expanduser().absolute()
    project = Path(__file__).resolve().parents[1]
    configured = [os.getenv("MAILAPI_DATA_DIR")]
    configured.extend(value for key, value in dotenv_entries(project / ".env") if key == "MAILAPI_DATA_DIR")
    protected = [project, *((project / Path(value).expanduser()).resolve() for value in configured if value)]
    if source.is_symlink() or not source.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError("需要已存在的隔离源目录和全新的目标目录")
    source, destination = source.resolve(), destination.resolve()
    if (any(path.is_relative_to(root) or root.is_relative_to(path)
            for path in (source, destination) for root in protected)
            or source.is_relative_to(destination) or destination.is_relative_to(source)):
        raise ValueError("测试源和目标目录须位于项目和正式数据目录外，且互不包含")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("隔离数据目录不能含有符号链接")
    if any((source / (name + suffix)).exists()
           for name in (SITE_NAME, RESOURCE_NAME) for suffix in ("-journal", "-wal", "-shm")):
        raise ValueError("请停止测试进程并关闭两个 SQLite 库后再操作")
    return source, destination


@contextmanager
def _stage(destination):
    with tempfile.TemporaryDirectory(prefix=".mailapi-test-credentials-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "data"
        stage.mkdir(mode=0o700)
        yield stage
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("测试目标目录已存在")
        os.rename(stage, destination)


def _read_db(path):
    if not path.is_file():
        raise ValueError("隔离目录缺少 SQLite 文件")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def _check_integrity(db):
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("SQLite 数据库完整性或外键检查失败")


def _copy_entry(source, target):
    if source.is_dir():
        target.mkdir(mode=0o700)
        for entry in source.iterdir():
            _copy_entry(entry, target / entry.name)
    elif source.is_file():
        shutil.copyfile(source, target)
        target.chmod(0o600)
    else:
        raise ValueError("隔离数据含有非普通文件")


def _copy_remaining(source, target, *, skip_oauth=False):
    for entry in source.iterdir():
        legacy = LEGACY_OAUTH.fullmatch(entry.name)
        if skip_oauth and (entry.name in OAUTH_DIRECTORIES or legacy):
            continue
        if skip_oauth and entry.name == "auth_artifact_versions":
            for child in entry.iterdir():
                if child.name not in OAUTH_PROVIDERS:
                    archive = target / entry.name
                    archive.mkdir(mode=0o700, exist_ok=True)
                    _copy_entry(child, archive / child.name)
        elif entry.name in REMAINING_DATA or legacy:
            _copy_entry(entry, target / entry.name)


def _copy_oauth(source, target, mailboxes, *, reader, writer, original_root=None, rollback_root=None):
    """Copy or reseal every managed artifact and make mailbox paths portable."""
    directories = [source / name for name in OAUTH_DIRECTORIES]
    directories += [source / "auth_artifact_versions" / provider for provider in OAUTH_PROVIDERS]
    paths = {path.relative_to(source) for directory in directories for path in directory.rglob("*") if path.is_file()}
    paths.update(path.relative_to(source) for path in source.iterdir()
                 if path.is_file() and LEGACY_OAUTH.fullmatch(path.name))
    for item in (mailboxes or {}).get("items", []):
        provider = item.get("provider") or "gmail"
        configured = item.get("token_file")
        if provider not in {"gmail", "outlook"} or not configured:
            continue
        path = Path(configured)
        if path.is_absolute():
            try:
                path = path.relative_to(original_root or source)
            except ValueError:
                raise CredentialCryptoError("请先把测试邮箱的 token 路径改到隔离源目录内") from None
        if (not path.parts or ".." in path.parts or not
                (path.parts[0] == provider + "_tokens" and len(path.parts) > 1
                 or len(path.parts) == 1 and re.fullmatch(r"token_account\d+\.json", path.name))):
            raise CredentialCryptoError("旧邮箱 token 须放到 Gmail 或 Outlook 目录")
        item["token_file"] = str(rollback_root / path) if rollback_root else path.as_posix()

    for relative in sorted(paths):
        origin = source / relative
        out = target / relative
        raw = reader.read_bytes(origin) if reader else origin.read_bytes()
        if writer:
            writer.write_bytes(out, raw)
            if writer.read_bytes(out) != raw:
                raise CredentialCryptoError("迁移前后的 OAuth 文件内容不匹配")
        elif rollback_root:
            out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        else:
            out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _copy_entry(origin, out)
        info = origin.stat()
        os.utime(out, ns=(info.st_atime_ns, info.st_mtime_ns))


def _check_legacy_points(document, key_path, *, convert=False):
    cipher = None
    needs_key = False
    for account_id, fields in document.get("restore_points", {}).items():
        for field, point in fields.items():
            if "sealed" not in point:
                continue
            if cipher is None:
                if not key_path.is_file():
                    raise CredentialCryptoError("缺少旧测试恢复点的 Fernet 密钥")
                cipher = Fernet(key_path.read_bytes())
            snapshot = open_legacy_restore_point(cipher, account_id, field, point["event_id"], point["sealed"])
            if convert and field in ENCRYPTED_RESTORE_FIELDS:
                point["snapshot"] = snapshot
                del point["sealed"]
            else:
                needs_key = True
    return needs_key


def rewrite(source, destination, key_file, *, old_key_file=None, production=False, oauth=False,
            enable_oauth=False, original_root=None):
    source, destination = _paths(source, destination)
    roots = (source, destination)
    keyring = CredentialKeyring(key_file, data_dirs=roots, production=production)
    old = CredentialKeyring(old_key_file, data_dirs=roots, production=production) if old_key_file else None
    if enable_oauth and (not oauth or not old or old.active != keyring.active):
        raise CredentialCryptoError("启用 OAuth 加密时须沿用源数据库的密钥")
    if old and not enable_oauth and not keyring.rotation_from(old):
        raise CredentialCryptoError("轮换须使用相同测试环境标识、新的密钥 ID 和新密钥")
    with closing(_read_db(source / SITE_NAME)) as db:
        _check_integrity(db)
        site = read_site_snapshot(db, keyring=old)
    source_oauth = oauth and old is not None and not enable_oauth
    resources = ResourceDatabase(source, keyring=old, read_only=True, oauth_encrypted=source_oauth)
    with closing(resources._connect()) as db:
        _check_integrity(db)
    with _stage(destination) as stage:
        _copy_remaining(source, stage, skip_oauth=oauth)
        write_site_database(stage / SITE_NAME, site, keyring=keyring)
        output = create_resource_database(stage, keyring=keyring, oauth_encrypted=oauth)
        for pool in FIELDS:
            data = resources.load(pool)
            if oauth and pool == "gmail_accounts.json":
                _copy_oauth(source, stage, data,
                            reader=OAuthFiles(source, old) if source_oauth else None,
                            writer=OAuthFiles(stage, keyring),
                            original_root=original_root.resolve() if original_root else None)
            if pool == GPT and data is not None:
                legacy_key = source / LEGACY_RESTORE_KEY
                if _check_legacy_points(data, legacy_key, convert=old is None):
                    _copy_entry(legacy_key, stage / LEGACY_RESTORE_KEY)
            output.save(pool, data)
            if output.load(pool) != data:
                raise ValueError("加密资源与源数据不一致")
        with closing(output._connect()) as db:
            _check_integrity(db)


def copy_backup(source, destination, key_file, *, production=False, oauth=False):
    """Create or restore a stopped encrypted snapshot using its matching key."""
    source, destination = _paths(source, destination)
    ring = CredentialKeyring(key_file, data_dirs=(source, destination), production=production)
    with _stage(destination) as stage:
        _copy_remaining(source, stage, skip_oauth=oauth)
        _copy_entry(source / SECRET_DIRECTORY, stage / SECRET_DIRECTORY)
        if (source / LEGACY_RESTORE_KEY).is_file():
            _copy_entry(source / LEGACY_RESTORE_KEY, stage / LEGACY_RESTORE_KEY)
        for name in (SITE_NAME, RESOURCE_NAME):
            target = stage / name
            target.touch(mode=0o600)
            with closing(_read_db(source / name)) as db, closing(sqlite3.connect(target)) as output:
                db.backup(output)
                _check_integrity(output)
        with closing(_read_db(stage / SITE_NAME)) as output:
            SiteAccountStore.validate_snapshot(read_site_snapshot(output, keyring=ring))
        resources = ResourceDatabase(stage, keyring=ring, read_only=True, oauth_encrypted=oauth)
        for pool in FIELDS:
            data = resources.load(pool)
            if oauth and pool == "gmail_accounts.json":
                _copy_oauth(source, stage, data, reader=OAuthFiles(source, ring), writer=None)
            if pool == GPT and data is not None:
                _check_legacy_points(data, stage / LEGACY_RESTORE_KEY)


def rollback_oauth(source, destination, key_file, *, runtime_root, production=False):
    """Rebuild the previous OAuth layout from a stopped copy with current tokens."""
    source, destination = _paths(source, destination)
    ring = CredentialKeyring(key_file, data_dirs=(source, destination, runtime_root), production=production)
    resources = ResourceDatabase(source, keyring=ring, read_only=True, oauth_encrypted=True)
    with _stage(destination) as stage:
        _copy_remaining(source, stage, skip_oauth=True)
        output = create_resource_database(stage, keyring=ring)
        for pool in FIELDS:
            data = resources.load(pool)
            if pool == "gmail_accounts.json":
                _copy_oauth(source, stage, data, reader=OAuthFiles(source, ring), writer=None,
                            rollback_root=runtime_root.resolve())
            output.save(pool, data)
            if output.load(pool) != data:
                raise ValueError("回滚资源与源数据不一致")
        with closing(output._connect()) as db:
            _check_integrity(db)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "rotate", "backup", "restore", "encrypt-oauth"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--old-key-file", type=Path)
    parser.add_argument("--production-key", action="store_true", help="use a production key on a stopped, isolated copy")
    parser.add_argument("--oauth", action="store_true", help="include encrypted OAuth files in the isolated copy")
    parser.add_argument("--original-runtime", type=Path, help="original absolute root referenced by a copied snapshot")
    args = parser.parse_args()
    if (args.action == "rotate") != bool(args.old_key_file):
        parser.error("仅轮换需要 --old-key-file")
    if args.original_runtime and args.action != "encrypt-oauth":
        parser.error("仅加密现有 OAuth 副本可指定 --original-runtime")
    os.umask(0o077)
    try:
        if args.action in {"backup", "restore"}:
            copy_backup(args.source, args.destination, args.key_file, production=args.production_key, oauth=args.oauth)
        else:
            enabling = args.action == "encrypt-oauth"
            rewrite(args.source, args.destination, args.key_file,
                    old_key_file=args.key_file if enabling else args.old_key_file,
                    production=args.production_key, oauth=args.oauth or enabling, enable_oauth=enabling,
                    original_root=args.original_runtime)
    except CredentialCryptoError as error:
        parser.exit(1, f"{error}\n")
    except (OSError, sqlite3.Error, ValueError, KeyError, AttributeError, TypeError):
        parser.exit(1, "测试凭据操作失败，请检查隔离目录、数据库版本和文件权限。\n")
    print("隔离测试数据已校验并写入目标目录。")


if __name__ == "__main__":
    main()
