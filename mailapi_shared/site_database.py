"""SQLite persistence for website accounts, groups and sessions."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .site_accounts import SiteAccountError, SiteAccountStore
from .credential_crypto import check_database_crypto


DATABASE_NAME = "site_accounts.sqlite3"
LEGACY_NAME = "site_accounts.json"
SCHEMA = """
PRAGMA user_version = 1;
CREATE TABLE users (
    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, position INTEGER NOT NULL,
    details TEXT NOT NULL
);
CREATE TABLE groups (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL
);
CREATE TABLE group_members (
    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE group_resources (
    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    resource_key TEXT NOT NULL, position INTEGER NOT NULL,
    PRIMARY KEY (group_id, resource_key)
);
CREATE TABLE sessions (
    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL, mfa_verified INTEGER
);
"""


class SQLiteSiteAccountStore(SiteAccountStore):
    def __init__(self, path, *, require_admin_mfa=False, keyring=None):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise SiteAccountError("找不到 SQLite 网站账户库，请先执行离线迁移")
        path.chmod(0o600)
        self._keyring = keyring
        super().__init__(path, require_admin_mfa=require_admin_mfa)

    def _connect(self):
        connection = sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _read_snapshot(self):
        with closing(self._connect()) as db:
            return read_site_snapshot(db, keyring=self._keyring)

    def _write_snapshot(self, data):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._write_changes(db, self._data, data, keyring=self._keyring)

    @staticmethod
    def _write_changes(db, old, new, *, keyring=None):
        for table, column in (("sessions", "digest"), ("groups", "id"), ("users", "id")):
            db.executemany(f"DELETE FROM {table} WHERE {column} = ?",
                           ((key,) for key in old[table].keys() - new[table].keys()))
        old_positions = {key: index for index, key in enumerate(old["users"])}
        for position, (user_id, user) in enumerate(new["users"].items()):
            if old["users"].get(user_id) == user and old_positions[user_id] == position:
                continue
            details = {key: value for key, value in user.items() if key not in {"id", "username"}}
            if keyring and "totp_secret" in details:
                details["totp_secret"] = keyring.seal(details["totp_secret"], "site", user_id, "totp_secret")
            db.execute("""INSERT INTO users VALUES (?, ?, ?, ?)
                          ON CONFLICT(id) DO UPDATE SET username=excluded.username,
                          position=excluded.position, details=excluded.details""",
                       (user_id, user["username"], position, json.dumps(details, ensure_ascii=False)))
        old_positions = {key: index for index, key in enumerate(old["groups"])}
        for position, (group_id, group) in enumerate(new["groups"].items()):
            previous = old["groups"].get(group_id, {})
            if previous == group and old_positions[group_id] == position:
                continue
            db.execute("""INSERT INTO groups VALUES (?, ?, ?)
                          ON CONFLICT(id) DO UPDATE SET name=excluded.name, position=excluded.position""",
                       (group_id, group["name"], position))
            for field, table in (("member_ids", "group_members"), ("resource_keys", "group_resources")):
                if group[field] == previous.get(field):
                    continue
                db.execute(f"DELETE FROM {table} WHERE group_id = ?", (group_id,))
                db.executemany(f"INSERT INTO {table} VALUES (?, ?, ?)",
                               ((group_id, value, index) for index, value in enumerate(group[field])))
        for digest, session in new["sessions"].items():
            if old["sessions"].get(digest) == session:
                continue
            db.execute("""INSERT INTO sessions VALUES (?, ?, ?, ?)
                          ON CONFLICT(digest) DO UPDATE SET user_id=excluded.user_id,
                          expires_at=excluded.expires_at, mfa_verified=excluded.mfa_verified""",
                       (digest, session["user_id"], session["expires_at"], session.get("mfa_verified")))


def read_site_snapshot(db, *, keyring=None):
    with db:
        db.execute("BEGIN")
        check_database_crypto(db, keyring, "site")
        users = {}
        for user_id, username, details in db.execute("SELECT id, username, details FROM users ORDER BY position"):
            users[user_id] = {**json.loads(details), "id": user_id, "username": username}
            if keyring and "totp_secret" in users[user_id]:
                users[user_id]["totp_secret"] = keyring.open(users[user_id]["totp_secret"], "site", user_id, "totp_secret")
        groups = {
            group_id: {"id": group_id, "name": name, "member_ids": [], "resource_keys": []}
            for group_id, name in db.execute("SELECT id, name FROM groups ORDER BY position")
        }
        for table, column, field in (("group_members", "user_id", "member_ids"),
                                     ("group_resources", "resource_key", "resource_keys")):
            for group_id, value in db.execute(f"SELECT group_id, {column} FROM {table} ORDER BY position"):
                groups[group_id][field].append(value)
        sessions = {}
        for digest, user_id, expiry, mfa in db.execute("SELECT digest, user_id, expires_at, mfa_verified FROM sessions"):
            sessions[digest] = {"user_id": user_id, "expires_at": expiry}
            if mfa is not None:
                sessions[digest]["mfa_verified"] = bool(mfa)
        return {"version": 1, "users": users, "groups": groups, "sessions": sessions}


def open_site_accounts(directory, *, backend="json", require_admin_mfa=False, keyring=None):
    directory = Path(directory)
    if backend == "sqlite":
        return SQLiteSiteAccountStore(directory / DATABASE_NAME, require_admin_mfa=require_admin_mfa, keyring=keyring)
    if backend != "json":
        raise SiteAccountError("网站账户存储须为 json 或 sqlite")
    return SiteAccountStore(directory / LEGACY_NAME, require_admin_mfa=require_admin_mfa)


def write_site_database(destination, data, *, keyring=None):
    SiteAccountStore.validate_snapshot(data)
    for user in data["users"].values():
        SiteAccountStore._normalize_user(user)
    descriptor, name = tempfile.mkstemp(prefix=".site-accounts-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript(SCHEMA)
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                db.execute("BEGIN IMMEDIATE")
                if keyring:
                    keyring.initialize_database(db, "site")
                SQLiteSiteAccountStore._write_changes(db, {"users": {}, "groups": {}, "sessions": {}}, data, keyring=keyring)
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall():
                raise SiteAccountError("SQLite 网站账户库完整性检查失败")
        restored = SQLiteSiteAccountStore(temporary, keyring=keyring)._data
        if restored != data or any(list(restored[key]) != list(data[key]) for key in ("users", "groups")):
            raise SiteAccountError("SQLite 数据与源文件不一致")
        os.link(temporary, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink()


def migrate_site_accounts(directory):
    """Create a verified database from the offline JSON snapshot without changing the source."""
    directory = Path(directory).resolve()
    destination = directory / DATABASE_NAME
    if destination.exists() or destination.is_symlink():
        raise SiteAccountError("SQLite 网站账户库已存在")
    data = json.loads((directory / LEGACY_NAME).read_text(encoding="utf-8"))
    write_site_database(destination, data)
    return {
        "users": len(data["users"]), "groups": len(data["groups"]), "sessions": len(data["sessions"]),
        "memberships": sum(len(group["member_ids"]) for group in data["groups"].values()),
        "grants": sum(len(group["resource_keys"]) for group in data["groups"].values()),
    }


def main():
    parser = argparse.ArgumentParser(description="在 MailAPI 停止写入时迁移网站账户 JSON")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        counts = migrate_site_accounts(args.data_dir)
    except (OSError, sqlite3.Error, SiteAccountError, ValueError, KeyError, TypeError):
        parser.exit(1, "网站账户迁移失败，请检查源文件格式、数据库是否已存在和文件权限。\n")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
