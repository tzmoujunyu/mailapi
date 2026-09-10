"""ChatGPT login credentials, shared by normalized email across integrations."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from account_passwords import AccountPasswordError, MAX_PASSWORD_LENGTH


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

    def get(self, email, field):
        with self._lock:
            return self._data["items"].get(self.email_key(email), {}).get(field)

    def set(self, email, field, value):
        email = self.email_key(email)
        if not email or "@" not in email:
            raise AccountPasswordError("请先完成账号授权，获取有效邮箱")
        if field not in {"password", "secret"}:
            raise AccountPasswordError("未知登录信息字段")
        if not isinstance(value, str) or not value or len(value) > MAX_PASSWORD_LENGTH:
            raise AccountPasswordError("登录信息不能为空且不能超过 4096 字符")
        with self._lock:
            data = {**self._data, "items": {**self._data["items"]}}
            data["items"][email] = {**data["items"].get(email, {}), field: value}
            self._save(data)

    def delete(self, email, field=None):
        email = self.email_key(email)
        with self._lock:
            data = {**self._data, "items": {**self._data["items"]}}
            record = dict(data["items"].get(email, {}))
            if field is None:
                record.clear()
            else:
                record.pop(field, None)
            if record:
                data["items"][email] = record
            else:
                data["items"].pop(email, None)
            self._save(data)

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
            self._save({"version": 1, "migrated": True, "items": items})
