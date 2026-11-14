"""SQLite resource metadata with versioned, private JSON credential files."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from urllib.parse import urlsplit

from .credential_crypto import CredentialCryptoError, check_database_crypto


DATABASE_NAME = "resources.sqlite3"
SECRET_DIRECTORY = "resource_secrets"
FIELDS = {
    "account_credentials.json": {"key", "login", "listed", "mailbox_ids", "mailbox_addresses",
                                 "codex_email", "subscription_started_on", "subscription_expires_on"},
    "coding_plans.json": {"id", "name", "note", "provider"},
    "relay_accounts.json": {"id", "name", "note", "account", "url", "mailbox_key", "mail_rule_id"},
    "gmail_accounts.json": {"name", "email", "enabled", "listed", "note", "provider", "resource_id",
                            "token_file", "credential_file", "config_file", "session_file", "delivery_mode"},
    "codex_accounts.json": {"id", "label", "email", "subject", "chatgpt_account_id", "workspace_id",
                            "chatgpt_plan_type", "account_id_hint", "auth_revision", "access_token_expires_at",
                            "created_at", "updated_at", "last_refresh_at", "status", "status_reason",
                            "usage_status", "reauth_required", "reset_credits_last_refresh_at"},
}
GPT = "account_credentials.json"
ENCRYPTED_FIELDS = {GPT: {"password", "secret"}, "coding_plans.json": {"api_key"}, "relay_accounts.json": {"password"}}
ENCRYPTED_RESTORE_FIELDS = {"login", "password", "secret", "subscription"}
SCHEMA = """
PRAGMA user_version = 1;
CREATE TABLE pools (
    name TEXT PRIMARY KEY, header TEXT, credential_file TEXT
);
CREATE TABLE records (
    pool TEXT NOT NULL REFERENCES pools(name), record_id TEXT NOT NULL,
    position INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (pool, record_id)
);
CREATE TABLE gpt_history (
    account_key TEXT NOT NULL, event_id TEXT NOT NULL,
    position INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (account_key, event_id)
);
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _split(pool, document):
    if document is None:
        return None, [], {}, []
    if isinstance(document, list):
        document = {"items": document}
    items = document["items"]
    if (pool == GPT and not isinstance(items, dict)) or (pool != GPT and not isinstance(items, list)):
        raise ValueError("无效的资源记录")
    header_fields = {"version", "migrated", "pool_migrated", "security"} if pool == GPT else {"version"}
    header = {key: value for key, value in document.items() if key in header_fields}
    private = {key: value for key, value in document.items() if key not in header_fields | {"items", "history"}}
    if pool == GPT and "history" in document:
        header["history_keys"] = list(document["history"])
    records, hidden_items = [], {}
    id_field = "name" if pool == "gmail_accounts.json" else "id"
    entries = items.items() if pool == GPT else ((item[id_field], item) for item in items)
    for record_id, item in entries:
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("无效的资源 ID")
        public = {key: value for key, value in item.items() if key in FIELDS[pool]}
        if pool == "relay_accounts.json" and isinstance(public.get("url"), str):
            try:
                # URLs can include login information or query credentials.
                parsed = urlsplit(public["url"])
                sensitive_url = parsed.username is not None or parsed.query or parsed.fragment
            except ValueError:
                sensitive_url = True
            if sensitive_url:
                public.pop("url")
        records.append((record_id, public))
        hidden = {key: value for key, value in item.items() if key not in public}
        if hidden:
            hidden_items[record_id] = hidden
    if hidden_items:
        private["items"] = hidden_items
    history = [(key, event["id"], position, _json(event)) for key, events in
               document.get("history", {}).items() for position, event in enumerate(events)] if pool == GPT else []
    return header, records, private, history


def _credential_scope(pool, record_id, record, field):
    # Legacy GPT records can exist before a resource key has been assigned.
    return ("resource", pool, "id" if record.get("key") else "record", record.get("key") or record_id, field)


class ResourceDatabase:
    def __init__(self, directory, *, keyring=None, read_only=False, oauth_encrypted=False):
        directory = Path(directory)
        self.path = directory / DATABASE_NAME
        self.secrets = directory / SECRET_DIRECTORY
        self.keyring = keyring
        self._read_only = read_only
        self.oauth_encrypted = oauth_encrypted
        if self.path.is_symlink() or not self.path.is_file() or self.secrets.is_symlink() or not self.secrets.is_dir():
            raise ValueError("找不到完整的资源数据库，请先执行离线迁移")
        if not read_only:
            self.path.chmod(0o600)
            self.secrets.chmod(0o700)
        with closing(self._connect()) as db:
            check_database_crypto(db, keyring, "resources")
            marker = db.execute("SELECT 1 FROM sqlite_schema WHERE name='oauth_storage' AND type='table'").fetchone()
            if bool(marker) != oauth_encrypted or (oauth_encrypted and not keyring):
                raise CredentialCryptoError("资源数据库与 OAuth 加密配置不匹配")
            if oauth_encrypted:
                row = db.execute("SELECT marker FROM oauth_storage").fetchone()
                if not row or keyring.open(json.loads(row[0]), "oauth-profile") != "encrypted":
                    raise CredentialCryptoError("OAuth 加密标记无效")
            if {row[0] for row in db.execute("SELECT name FROM pools")} != FIELDS.keys():
                raise ValueError("资源数据库的资源池不完整")

    def _connect(self):
        mode = "ro" if self._read_only else "rw"
        db = sqlite3.connect(self.path.absolute().as_uri() + "?mode=" + mode, uri=True)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _read_private(self, pool, filename):
        if filename is None:
            return {}
        if len(filename) != 37 or not filename.endswith(".json") or any(
            char not in "0123456789abcdef" for char in filename[:-5]
        ):
            raise ValueError("资源凭据路径无效")
        path = self.secrets / filename
        if path.is_symlink():
            raise ValueError("资源凭据文件不能为符号链接")
        if not self._read_only:
            path.chmod(0o600)
        value = json.loads(path.read_text(encoding="utf-8"))
        if self.oauth_encrypted:
            return self.keyring.open(value, "resource-private", pool, filename)
        return value

    def _load(self, db, pool):
        header, filename = db.execute("SELECT header, credential_file FROM pools WHERE name = ?", (pool,)).fetchone()
        if header is None:
            return None
        data = json.loads(header)
        private = self._read_private(pool, filename)
        values = private.pop("items", {})
        records = {}
        for record_id, record in db.execute("SELECT record_id, data FROM records WHERE pool = ? ORDER BY position", (pool,)):
            fields = json.loads(record)
            hidden = values.pop(record_id, {})
            if self.keyring:
                for field in ENCRYPTED_FIELDS.get(pool, ()):
                    if field in hidden:
                        raise CredentialCryptoError("在旧资源文件中发现未迁移的凭据")
                    if field in fields:
                        fields[field] = self.keyring.open(fields[field], *_credential_scope(pool, record_id, fields, field))
            records[record_id] = {**fields, **hidden}
        if values:
            raise ValueError("资源与文件中的凭据记录不一致")
        if pool == GPT and "history_keys" in data:
            history = {key: [] for key in data.pop("history_keys")}
            for key, event in db.execute("SELECT account_key, data FROM gpt_history ORDER BY position"):
                history[key].append(json.loads(event))
            data["history"] = history
        if pool == GPT and self.keyring:
            points = private.get("restore_points", {})
            if any(points_by_field.keys() & ENCRYPTED_RESTORE_FIELDS for points_by_field in points.values()):
                raise CredentialCryptoError("在旧资源文件中发现未迁移的恢复点")
            for key, field, event_id, value in db.execute("SELECT account_key, field, event_id, value FROM encrypted_restore_points"):
                snapshot = self.keyring.open(json.loads(value), "restore", key, field, event_id)
                points.setdefault(key, {})[field] = {"event_id": event_id, "snapshot": snapshot}
            if points:
                private["restore_points"] = points
        return {**private, **data, "items": records if pool == GPT else list(records.values())}

    def load(self, pool):
        with closing(self._connect()) as db, db:
            # Block a writer from retiring a credential revision while it is read.
            db.execute("BEGIN" if self._read_only else "BEGIN IMMEDIATE")
            return self._load(db, pool)

    def _write_private(self, pool, private):
        filename = secrets.token_hex(16) + ".json"
        if self.oauth_encrypted:
            private = self.keyring.seal(private, "resource-private", pool, filename)
        path = self.secrets / filename
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(_json(private))
                output.flush()
                os.fsync(output.fileno())
            _sync_directory(self.secrets)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return filename

    def prune_private_files(self):
        # Called before app writes begin. A killed writer may leave an unreferenced
        # revision, but never retires the file referenced by the committed state.
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            active = {row[0] for row in db.execute("SELECT credential_file FROM pools")}
            for path in self.secrets.glob("*.json"):
                if path.name not in active and path.is_file() and not path.is_symlink():
                    path.unlink()

    def _seal_credentials(self, db, pool, records, private):
        # Keep existing envelopes when writing unchanged credentials.
        slots = ENCRYPTED_FIELDS.get(pool, ())
        if slots:
            existing = {key: json.loads(record) for key, record in
                        db.execute("SELECT record_id, data FROM records WHERE pool = ?", (pool,))}
            items = private.get("items", {})
            for key, data in records:
                hidden = items.get(key, {})
                old = existing.get(key, {})
                for field in hidden.keys() & slots:
                    value = hidden.pop(field)
                    scope = _credential_scope(pool, key, data, field)
                    envelope = old.get(field)
                    if (envelope is None or scope != _credential_scope(pool, key, old, field)
                            or self.keyring.open(envelope, *scope) != value):
                        envelope = self.keyring.seal(value, *scope)
                    data[field] = envelope
                if not hidden:
                    items.pop(key, None)
            if not items:
                private.pop("items", None)

        if pool != GPT:
            return
        existing = {(key, field): (event_id, value) for key, field, event_id, value in
                    db.execute("SELECT account_key, field, event_id, value FROM encrypted_restore_points")}
        if "restore_points" in private:
            private["restore_points"] = {
                key: dict(fields) for key, fields in private["restore_points"].items()
            }
        points = private.get("restore_points", {})
        for key, fields in list(points.items()):
            for field in fields.keys() & ENCRYPTED_RESTORE_FIELDS:
                point = fields.pop(field)
                event_id, snapshot = point["event_id"], point["snapshot"]
                previous = existing.pop((key, field), None)
                scope = ("restore", key, field, event_id)
                if previous and previous[0] == event_id and self.keyring.open(json.loads(previous[1]), *scope) == snapshot:
                    continue
                db.execute("""INSERT INTO encrypted_restore_points VALUES (?, ?, ?, ?)
                              ON CONFLICT(account_key, field) DO UPDATE SET event_id=excluded.event_id, value=excluded.value""",
                           (key, field, event_id, _json(self.keyring.seal(snapshot, *scope))))
            if not fields:
                points.pop(key)
        db.executemany("DELETE FROM encrypted_restore_points WHERE account_key = ? AND field = ?", existing)

    def save(self, pool, document):
        header, records, private, history = _split(pool, document)
        if len({key for key, _ in records}) != len(records):
            raise ValueError("资源 ID 重复")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if self.keyring:
                self._seal_credentials(db, pool, records, private)
            old_file, = db.execute("SELECT credential_file FROM pools WHERE name = ?", (pool,)).fetchone()
            new_file = old_file
            if private != self._read_private(pool, old_file):
                new_file = self._write_private(pool, private) if private else None
            db.execute("UPDATE pools SET header = ?, credential_file = ? WHERE name = ?",
                       (None if header is None else _json(header), new_file, pool))
            existing = {row[0] for row in db.execute("SELECT record_id FROM records WHERE pool = ?", (pool,))}
            db.executemany("DELETE FROM records WHERE pool = ? AND record_id = ?",
                           ((pool, key) for key in existing - {key for key, _ in records}))
            db.executemany("""INSERT INTO records VALUES (?, ?, ?, ?)
                           ON CONFLICT(pool, record_id) DO UPDATE SET position=excluded.position, data=excluded.data
                           WHERE records.position != excluded.position OR records.data != excluded.data""",
                           ((pool, key, index, _json(data)) for index, (key, data) in enumerate(records)))
            if pool == GPT:
                existing_events = set(db.execute("SELECT account_key, event_id FROM gpt_history"))
                db.executemany("DELETE FROM gpt_history WHERE account_key = ? AND event_id = ?",
                               existing_events - {(key, event_id) for key, event_id, _, _ in history})
                db.executemany("""INSERT INTO gpt_history VALUES (?, ?, ?, ?)
                               ON CONFLICT(account_key, event_id) DO UPDATE SET position=excluded.position, data=excluded.data
                               WHERE gpt_history.position != excluded.position OR gpt_history.data != excluded.data""", history)
        if old_file and old_file != new_file:
            try:
                (self.secrets / old_file).unlink()
            except OSError:
                pass

    def export_json(self, destination, *, include_restore_points=True):
        if self.keyring:
            raise CredentialCryptoError("加密资源库仅支持加密备份，禁止导出明文 JSON")
        destination = Path(destination)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for pool in FIELDS:
                data = self._load(db, pool)
                if data is None:
                    continue
                if pool == GPT and not include_restore_points:
                    data.pop("restore_points", None)
                descriptor = os.open(destination / pool, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    output.write(_json(data))
                    output.flush()
                    os.fsync(output.fileno())
        _sync_directory(destination)


def create_resource_database(directory, *, keyring=None, oauth_encrypted=False):
    (directory / SECRET_DIRECTORY).mkdir(mode=0o700)
    path = directory / DATABASE_NAME
    path.touch(mode=0o600, exist_ok=False)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SCHEMA)
        if keyring:
            keyring.initialize_database(db, "resources")
            db.execute("""CREATE TABLE encrypted_restore_points (
                           account_key TEXT NOT NULL, field TEXT NOT NULL, event_id TEXT NOT NULL,
                           value TEXT NOT NULL, PRIMARY KEY (account_key, field))""")
        if oauth_encrypted:
            db.execute("CREATE TABLE oauth_storage (marker TEXT NOT NULL)")
            db.execute("INSERT INTO oauth_storage VALUES (?)", (_json(keyring.seal("encrypted", "oauth-profile")),))
        db.executemany("INSERT INTO pools (name) VALUES (?)", ((name,) for name in FIELDS))
    return ResourceDatabase(directory, keyring=keyring, oauth_encrypted=oauth_encrypted)


def migrate_resources(directory):
    directory = Path(directory).resolve()
    db_path = directory / DATABASE_NAME
    secret_path = directory / SECRET_DIRECTORY
    if any(path.exists() or path.is_symlink() for path in (db_path, secret_path)):
        raise ValueError("资源数据库或凭据目录已存在")
    with tempfile.TemporaryDirectory(prefix=".resource-import-", dir=directory) as temporary:
        stage = Path(temporary)
        staged_db = stage / DATABASE_NAME
        store = create_resource_database(stage)
        counts = {}
        for pool in FIELDS:
            path = directory / pool
            if not path.is_file():
                continue
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, list):
                document = {"items": document}
            store.save(pool, document)
            restored = store.load(pool)
            if restored != document or (pool == GPT and list(restored["items"]) != list(document["items"])):
                raise ValueError("导入前后的资源数据不一致")
            counts[pool] = len(document["items"])
            if pool == GPT:
                counts["history_events"] = sum(map(len, document.get("history", {}).values()))
        with closing(store._connect()) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("资源数据库完整性检查失败")
        os.rename(stage / SECRET_DIRECTORY, secret_path)
        try:
            _sync_directory(directory)
            os.link(staged_db, db_path)
        except OSError:
            os.rename(secret_path, stage / SECRET_DIRECTORY)
            raise
        _sync_directory(directory)
        return counts


def main():
    parser = argparse.ArgumentParser(description="在 MailAPI 停写时导入资源元数据")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--export-to", type=Path, help="将 SQLite 与文件中的资源导出到已有空目录")
    args = parser.parse_args()
    try:
        if args.export_to:
            if any(args.export_to.iterdir()):
                raise ValueError("导出目录须为空")
            ResourceDatabase(args.data_dir).export_json(args.export_to)
            return
        print(_json(migrate_resources(args.data_dir)))
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError, KeyError):
        parser.exit(1, "资源迁移或导出失败，请检查数据格式、目标路径和文件权限。\n")


if __name__ == "__main__":
    main()
