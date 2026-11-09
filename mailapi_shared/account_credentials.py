"""Independent GPT accounts, optional credentials and mailbox bindings."""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from calendar import monthrange
from datetime import date
from pathlib import Path

from .account_passwords import AccountPasswordError, MAX_PASSWORD_LENGTH, normalize_totp_secret
from .gpt_history import describe_change, event, history_page


ACCOUNT_FIELDS = {"login", "password", "secret", "subscription_started_on", "subscription_expires_on"}


class AccountCredentialStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = {"version": 1, "migrated": False, "items": {}}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if self._data.get("version") != 1 or not isinstance(self._data.get("items"), dict):
                raise AccountPasswordError("账号登录信息存储格式无效")
            os.chmod(self.path, 0o600)

    @staticmethod
    def email_key(email):
        return str(email or "").strip().lower()

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self._data = data
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save_change(self, data, key, actor, descriptions):
        descriptions = [description for description in descriptions if description]
        if key and actor and descriptions:
            history = data.get("history", {})
            entries = [*history.get(key, []), event(actor, descriptions)]
            data = {**data, "history": {**history, key: entries}}
        self._save(data)

    def record_change(self, key, actor, *descriptions):
        if key and actor and any(descriptions):
            with self._lock:
                self._save_change(self._data, key, actor, descriptions)

    def history(self, key, query="", source="", offset=0, limit=50):
        with self._lock:
            entries = self._data.get("history", {}).get(key, [])
        return history_page(entries, query, source, offset, limit)

    def edit_lock(self):
        return self._lock

    def review_change(self, key, event_id, actor):
        with self._lock:
            history = self._data.get("history", {})
            entries = history.get(key, [])
            original = next((item for item in entries if item["id"] == event_id), None)
            if not original or original["source"] not in {"user", "system"}:
                return False
            if not any(item.get("review_of") == event_id for item in entries):
                review = event(actor, [f"审核了 {original['actor']} 的修改", *original["descriptions"]],
                               review_of=event_id)
                self._save({**self._data, "history": {**history, key: [*entries, review]}})
            return True

    def get(self, email, field):
        with self._lock:
            return self._data["items"].get(self.email_key(email), {}).get(field)

    @staticmethod
    def subscription_end(start):
        started = date.fromisoformat(start)
        year = started.year + (started.month == 12)
        month = started.month % 12 + 1
        return date(year, month, min(started.day, monthrange(year, month)[1])).isoformat()

    def _public(self, identity, record):
        start = record.get("subscription_started_on", "")
        entries = self._data.get("history", {}).get(record["key"], [])
        return {
            "key": record["key"],
            "login": record.get("login", identity),
            "mailbox_ids": list(record.get("mailbox_ids", [])),
            "listed": record.get("listed") is not False,
            "has_gpt_password": bool(record.get("password")),
            "has_totp_secret": bool(record.get("secret")),
            "subscription_started_on": start,
            "subscription_expires_on": record.get("subscription_expires_on", ""),
            "subscription_ends_on": AccountCredentialStore.subscription_end(start) if start else "",
            "revision": next((item["id"] for item in reversed(entries) if item["source"] in {"admin", "user"}), ""),
        }

    @property
    def pool_ready(self):
        with self._lock:
            return bool(self._data.get("pool_migrated"))

    def list_accounts(self):
        with self._lock:
            return [self._public(identity, record) for identity, record in self._data["items"].items()
                    if record.get("key")]

    def account_by_key(self, key):
        with self._lock:
            return next((self._public(identity, record) for identity, record in self._data["items"].items()
                         if record.get("key") == key), None)

    def account_for_login(self, login):
        identity = self.email_key(login)
        with self._lock:
            record = self._data["items"].get(identity)
            return self._public(identity, record) if record and record.get("key") else None

    def account_for_codex_email(self, email):
        identity = self.email_key(email)
        with self._lock:
            return next((self._public(login, record) for login, record in self._data["items"].items()
                         if identity and record.get("key") and record.get("codex_email") == identity), None)

    def migrate_pool(self, mail_accounts, codex_accounts):
        with self._lock:
            if self._data.get("pool_migrated"):
                return
            items = {identity: dict(record) for identity, record in self._data["items"].items()}
            sources = list(codex_accounts) + list(mail_accounts)
            codex_emails = {self.email_key(item.get("email")) for item in codex_accounts}
            for source in sources:
                login = str(source.get("email") or "").strip()
                if "@" in login:
                    identity = self.email_key(login)
                    record = items.setdefault(identity, {})
                    record.setdefault("login", login)
            for identity, record in items.items():
                if not record.get("key") and "@" not in identity:
                    continue
                record.setdefault("key", "email:" + identity)
                record.setdefault("login", identity)
                if identity in codex_emails:
                    record.setdefault("codex_email", identity)
                record.setdefault("mailbox_ids", [
                    mailbox["name"] for mailbox in mail_accounts
                    if self.email_key(mailbox.get("email")) == identity
                ])
                record.setdefault("mailbox_addresses", {
                    mailbox["name"]: self.email_key(mailbox.get("email")) for mailbox in mail_accounts
                    if mailbox["name"] in record["mailbox_ids"]
                })
            self._save({**self._data, "items": items, "pool_migrated": True})

    def save_account(self, key, payload, valid_mailboxes, *, actor=None):
        if not isinstance(payload, dict) or set(payload) - (ACCOUNT_FIELDS | {"listed", "mailbox_ids"}):
            raise AccountPasswordError("GPT 账号包含无效字段")
        with self._lock:
            current = self.account_by_key(key) if key else None
            if key and current is None:
                raise AccountPasswordError("未找到 GPT 账号")
            login = payload.get("login", current["login"] if current else "")
            if not isinstance(login, str) or not 1 <= len(login.strip()) <= 120 or any(ord(char) < 32 for char in login):
                raise AccountPasswordError("GPT 登录账号须为 1–120 个字符")
            login = login.strip()
            identity = self.email_key(login)
            old_identity = self.email_key(current["login"]) if current else identity
            if identity in self._data["items"] and (not current or identity != old_identity):
                raise AccountPasswordError("GPT 登录账号已存在")
            previous = self._data["items"].get(old_identity, {}) if current else {}
            record = dict(previous) if current else {
                "key": "account:" + secrets.token_hex(16), "mailbox_ids": [], "listed": True,
            }
            record["login"] = login
            for field, label in (("subscription_started_on", "开始"), ("subscription_expires_on", "到期")):
                if field not in payload:
                    continue
                value = payload[field]
                try:
                    if not isinstance(value, str) or (value and date.fromisoformat(value).isoformat() != value):
                        raise ValueError
                    if field == "subscription_started_on" and value:
                        self.subscription_end(value)
                except (ValueError, OverflowError):
                    raise AccountPasswordError(f"订阅{label}日期须为有效的 YYYY-MM-DD 日期") from None
                record[field] = value
            if "subscription_expires_on" in payload and "subscription_started_on" not in payload:
                record.pop("subscription_started_on", None)
            if "listed" in payload:
                if not isinstance(payload["listed"], bool):
                    raise AccountPasswordError("上架状态必须为布尔值")
                record["listed"] = payload["listed"]
            if "mailbox_ids" in payload:
                values = payload["mailbox_ids"]
                if not isinstance(values, list) or any(not isinstance(value, str) or value not in valid_mailboxes for value in values):
                    raise AccountPasswordError("监听邮箱包含无效邮箱，请重新选择")
                addresses = {self.email_key(valid_mailboxes[value]) for value in values}
                for other in self._data["items"].values():
                    if other.get("key") == record["key"]:
                        continue
                    if addresses.intersection(other.get("mailbox_addresses", {}).values()):
                        raise AccountPasswordError("所选邮箱已关联其他 GPT 账号，请先解除原关联")
                record["mailbox_ids"] = list(dict.fromkeys(values))
                record["mailbox_addresses"] = {value: self.email_key(valid_mailboxes[value]) for value in values}
            for field in ("password", "secret"):
                if field not in payload:
                    continue
                value = payload[field]
                if not isinstance(value, str) or len(value) > MAX_PASSWORD_LENGTH:
                    raise AccountPasswordError("登录信息须为字符串且不能超过 4096 字符")
                if value:
                    record[field] = normalize_totp_secret(value) if field == "secret" else value
            if current and record == previous:
                return current
            items = dict(self._data["items"])
            if current and identity != old_identity:
                items = {identity if name == old_identity else name: value for name, value in items.items()}
            items[identity] = record
            changes = [describe_change(field, previous.get(field) or "", record.get(field) or "")
                       for field in ("login", "password", "secret", "subscription_started_on", "subscription_expires_on")]
            self._save_change({**self._data, "items": items}, record["key"], actor, changes)
            return self._public(identity, record)

    def ensure_account(self, login, *, actor=None):
        with self._lock:
            current = self.account_for_login(login)
            return current or self.save_account(None, {"login": login}, set(), actor=actor)

    def ensure_codex_account(self, email, *, actor=None):
        identity = self.email_key(email)
        if "@" not in identity:
            return
        with self._lock:
            current = self.account_for_codex_email(email) or self.account_for_login(email)
            current = current or self.ensure_account(email, actor=actor)
            login = self.email_key(current["login"])
            if self._data["items"][login].get("codex_email") == identity:
                return current
            items = dict(self._data["items"])
            items[login] = {**items[login], "codex_email": identity}
            self._save({**self._data, "items": items})
            return current

    def linked_mailbox_ids(self, login, mail_accounts):
        with self._lock:
            record = self._data["items"].get(self.email_key(login), {})
            expected = record.get("mailbox_addresses", {})
            return [mailbox["name"] for mailbox in mail_accounts
                    if mailbox["name"] in record.get("mailbox_ids", [])
                    and expected.get(mailbox["name"]) == self.email_key(mailbox.get("email"))]

    def detach_mailbox(self, mailbox_id):
        with self._lock:
            items = dict(self._data["items"])
            changed = False
            for identity, original in self._data["items"].items():
                if mailbox_id not in original.get("mailbox_ids", []):
                    continue
                record = dict(original)
                record["mailbox_ids"] = [value for value in original["mailbox_ids"] if value != mailbox_id]
                record["mailbox_addresses"] = {
                    key: value for key, value in original.get("mailbox_addresses", {}).items() if key != mailbox_id
                }
                items[identity] = record
                changed = True
            if changed:
                self._save({**self._data, "items": items})

    def set(self, email, field, value, *, actor=None):
        email = self.email_key(email)
        if field not in {"password", "secret", "listed"}:
            raise AccountPasswordError("未知登录信息字段")
        if field == "listed":
            if not isinstance(value, bool):
                raise AccountPasswordError("上架状态必须为布尔值")
        elif not isinstance(value, str) or not value or len(value) > MAX_PASSWORD_LENGTH:
            raise AccountPasswordError("登录信息不能为空且不能超过 4096 字符")
        with self._lock:
            if not email or ("@" not in email and not self._data["items"].get(email, {}).get("key")):
                raise AccountPasswordError("请先添加 GPT 账号")
            data = {**self._data, "items": {**self._data["items"]}}
            previous = data["items"].get(email, {})
            data["items"][email] = {**previous, field: value}
            changes = [describe_change(field, previous.get(field), value)] if field in ACCOUNT_FIELDS else []
            self._save_change(data, previous.get("key"), actor, changes)

    def delete(self, email, field=None, *, actor=None):
        email = self.email_key(email)
        with self._lock:
            data = {**self._data, "items": {**self._data["items"]}}
            previous = data["items"].get(email, {})
            record = dict(previous)
            if field is None:
                record.clear()
            else:
                record.pop(field, None)
            if record:
                data["items"][email] = record
            else:
                data["items"].pop(email, None)
            fields = (field,) if field in ACCOUNT_FIELDS else ("login",) if field is None else ()
            changes = [describe_change(name, previous.get(name), None) for name in fields if previous.get(name)]
            self._save_change(data, previous.get("key"), actor, changes)

    def migrate(self, accounts, passwords, totp, environment):
        """Run once; preserve legacy files and never restore deliberately deleted secrets."""
        with self._lock:
            if self._data.get("migrated"):
                return
            items = {email: dict(record) for email, record in self._data["items"].items()}
            for account in accounts:
                name, email = account["name"], self.email_key(account["email"])
                if "@" not in email:
                    continue
                password = passwords.get(name, email)
                if password is None and name.startswith("account-") and name[8:].isdigit():
                    password = environment.get(f"ACCOUNT_PASSWORD_{name[8:]}")
                for field, value in (("password", password), ("secret", totp.get(name, email))):
                    if value:
                        items.setdefault(email, {}).setdefault(field, value)
            self._save({**self._data, "migrated": True, "items": items})
