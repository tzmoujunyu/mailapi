from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import pyotp


MAX_PASSWORD_LENGTH = 4096


class AccountPasswordError(ValueError):
    pass


def normalize_totp_secret(value: Any) -> str:
    if not isinstance(value, str):
        raise AccountPasswordError("请求缺少 2FA 密钥")
    secret = value.strip()
    if not secret:
        raise AccountPasswordError("2FA 密钥不能为空")
    if len(secret) > MAX_PASSWORD_LENGTH:
        raise AccountPasswordError("2FA 密钥过长")
    secret = re.sub(r"\s+", "", secret).upper().rstrip("=")
    try:
        if not re.fullmatch(r"[A-Z2-7]+", secret):
            raise ValueError("invalid Base32")
        base64.b32decode(secret + "=" * (-len(secret) % 8))
    except (ValueError, binascii.Error):
        raise AccountPasswordError("2FA 密钥格式无效，请填写由 A-Z 和 2-7 组成的密钥，不是六位验证码") from None
    return secret


def generate_totp_code(secret: str, timestamp: float) -> Dict[str, Any]:
    totp = pyotp.TOTP(normalize_totp_secret(secret))
    return {
        "code": totp.at(timestamp),
        "expires_at": (int(timestamp) // totp.interval + 1) * totp.interval,
    }


class AccountPasswordStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items = self._load()

    def _load(self) -> Dict[str, Dict[str, str]]:
        if not self.path.is_file():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        raw_items = value.get("items") if isinstance(value, dict) else None
        if not isinstance(raw_items, dict):
            raise AccountPasswordError("GPT 密码存储格式无效")

        items: Dict[str, Dict[str, str]] = {}
        for account_name, record in raw_items.items():
            if not isinstance(account_name, str) or not isinstance(record, dict):
                continue
            email = record.get("email")
            password = record.get("password")
            if isinstance(email, str) and isinstance(password, str) and password:
                items[account_name] = {"email": email, "password": password}
        os.chmod(self.path, 0o600)
        return items

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(
            f"{self.path.name}.tmp.{os.getpid()}.{threading.get_ident()}"
        )
        try:
            with temporary.open("w", encoding="utf-8") as file:
                os.chmod(temporary, 0o600)
                json.dump({"version": 1, "items": self._items}, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _validate(account_name: str, email: str, password: str) -> None:
        if not account_name or not email.strip():
            raise AccountPasswordError("账号标识或邮箱为空")
        if not password:
            raise AccountPasswordError("GPT 密码不能为空")
        if len(password) > MAX_PASSWORD_LENGTH:
            raise AccountPasswordError("GPT 密码过长")

    def get(self, account_name: str, email: str) -> Optional[str]:
        with self._lock:
            record = self._items.get(account_name)
            if not record or record["email"].strip().lower() != email.strip().lower():
                return None
            return record["password"]

    def set(self, account_name: str, email: str, password: str) -> None:
        self._validate(account_name, email, password)
        with self._lock:
            self._items[account_name] = {
                "email": email.strip(),
                "password": password,
            }
            self._save()

    def delete(self, account_name: str) -> bool:
        with self._lock:
            existed = self._items.pop(account_name, None) is not None
            if existed:
                self._save()
            return existed

    def import_legacy_environment(
        self,
        accounts: Iterable[Mapping[str, Any]],
        environment: Mapping[str, str],
    ) -> int:
        imported = 0
        with self._lock:
            for account in accounts:
                account_name = str(account.get("name") or "")
                email = str(account.get("email") or "")
                match = re.fullmatch(r"account-(\d+)", account_name)
                if (
                    not match
                    or email in {"未配置", "等待授权"}
                    or self.get(account_name, email) is not None
                ):
                    continue
                password = environment.get(f"ACCOUNT_PASSWORD_{match.group(1)}", "")
                if not password:
                    continue
                self._validate(account_name, email, password)
                self._items[account_name] = {"email": email.strip(), "password": password}
                imported += 1
            if imported:
                self._save()
        return imported
