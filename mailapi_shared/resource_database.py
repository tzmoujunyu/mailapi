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


class ResourceDatabase:
    def __init__(self, directory):
        directory = Path(directory)
        self.path = directory / DATABASE_NAME
        self.secrets = directory / SECRET_DIRECTORY
        if self.path.is_symlink() or not self.path.is_file() or self.secrets.is_symlink() or not self.secrets.is_dir():
            raise ValueError("找不到完整的资源数据库，请先执行离线迁移")
        self.path.chmod(0o600)
        self.secrets.chmod(0o700)
        with closing(self._connect()) as db:
            if db.execute("PRAGMA user_version").fetchone() != (1,) or {
                row[0] for row in db.execute("SELECT name FROM pools")
            } != FIELDS.keys():
                raise ValueError("资源数据库版本或资源池不完整")

    def _connect(self):
        db = sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def _read_private(self, filename):
        if filename is None:
            return {}
        if len(filename) != 37 or not filename.endswith(".json") or any(
            char not in "0123456789abcdef" for char in filename[:-5]
        ):
            raise ValueError("资源凭据路径无效")
        path = self.secrets / filename
        if path.is_symlink():
            raise ValueError("资源凭据文件不能为符号链接")
        path.chmod(0o600)
        return json.loads(path.read_text(encoding="utf-8"))

    def _load(self, db, pool):
        header, filename = db.execute("SELECT header, credential_file FROM pools WHERE name = ?", (pool,)).fetchone()
        if header is None:
            return None
        data = json.loads(header)
        private = self._read_private(filename)
        values = private.pop("items", {})
        records = {}
        for record_id, record in db.execute("SELECT record_id, data FROM records WHERE pool = ? ORDER BY position", (pool,)):
            records[record_id] = {**json.loads(record), **values.pop(record_id, {})}
        if values:
            raise ValueError("资源与文件中的凭据记录不一致")
        if pool == GPT and "history_keys" in data:
            history = {key: [] for key in data.pop("history_keys")}
            for key, event in db.execute("SELECT account_key, data FROM gpt_history ORDER BY position"):
                history[key].append(json.loads(event))
            data["history"] = history
        return {**private, **data, "items": records if pool == GPT else list(records.values())}

    def load(self, pool):
        with closing(self._connect()) as db, db:
            # Block a writer from retiring a credential revision while it is read.
            db.execute("BEGIN IMMEDIATE")
            return self._load(db, pool)

    def _write_private(self, private):
        filename = secrets.token_hex(16) + ".json"
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

    def save(self, pool, document):
        header, records, private, history = _split(pool, document)
        if len({key for key, _ in records}) != len(records):
            raise ValueError("资源 ID 重复")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old_file, = db.execute("SELECT credential_file FROM pools WHERE name = ?", (pool,)).fetchone()
            new_file = old_file
            if private != self._read_private(old_file):
                new_file = self._write_private(private) if private else None
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


def migrate_resources(directory):
    directory = Path(directory).resolve()
    db_path = directory / DATABASE_NAME
    secret_path = directory / SECRET_DIRECTORY
    if any(path.exists() or path.is_symlink() for path in (db_path, secret_path)):
        raise ValueError("资源数据库或凭据目录已存在")
    with tempfile.TemporaryDirectory(prefix=".resource-import-", dir=directory) as temporary:
        stage = Path(temporary)
        (stage / SECRET_DIRECTORY).mkdir(mode=0o700)
        staged_db = stage / DATABASE_NAME
        staged_db.touch(mode=0o600, exist_ok=False)
        with closing(sqlite3.connect(staged_db)) as db, db:
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO pools (name) VALUES (?)", ((name,) for name in FIELDS))
        store = ResourceDatabase(stage)
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
