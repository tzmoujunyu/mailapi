"""Small persistent stores for user-facing coding plans and relay accounts."""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit


MAX_NAME_LENGTH = 120
MAX_NOTE_LENGTH = 500
MAX_VALUE_LENGTH = 4096
MAX_URL_LENGTH = 2048


class ResourceStoreError(ValueError):
    """Raised when a coding-plan or relay-account record is invalid."""


class ResourceStore:
    """Persist a small list of named records with one secret field."""

    def __init__(self, path: str | Path, secret_field: str, value_label: str) -> None:
        self.path = Path(path)
        self.secret_field = secret_field
        self.value_label = value_label
        self._lock = threading.RLock()
        self._items = self._load()

    def _load(self) -> List[Dict[str, str]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResourceStoreError(f"{self.value_label}存储格式无效") from error
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            raise ResourceStoreError(f"{self.value_label}存储格式无效")

        items: List[Dict[str, str]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("id") or "").strip()
            name = str(raw.get("name") or "").strip()
            if not item_id or not name:
                continue
            value = raw.get(self.secret_field)
            if not isinstance(value, str) or not value:
                continue
            item: Dict[str, str] = {
                "id": item_id,
                "name": name[:MAX_NAME_LENGTH],
                "note": str(raw.get("note") or "")[:MAX_NOTE_LENGTH],
                self.secret_field: value[:MAX_VALUE_LENGTH],
            }
            if self.secret_field == "password":
                item["account"] = str(raw.get("account") or "")[:MAX_NAME_LENGTH]
                try:
                    item["url"] = self._url(raw.get("url"))
                except ResourceStoreError:
                    item["url"] = ""
            items.append(item)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return items

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        descriptor, temporary = tempfile.mkstemp(
            prefix=f"{self.path.name}.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump({"version": 1, "items": self._items}, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _text(value: Any, label: str, maximum: int, *, required: bool = False) -> str:
        if not isinstance(value, str):
            if required:
                raise ResourceStoreError(f"{label}不能为空")
            return ""
        result = value.strip()
        if required and not result:
            raise ResourceStoreError(f"{label}不能为空")
        if len(result) > maximum:
            raise ResourceStoreError(f"{label}不能超过 {maximum} 个字符")
        return result

    @classmethod
    def _url(cls, value: Any) -> str:
        value = cls._text(value, "中转站网址", MAX_URL_LENGTH, required=True)
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and (
                parsed.port is None or 0 < parsed.port <= 65535
            )
        except ValueError:
            valid = False
        if not valid or any(char.isspace() or ord(char) < 32 or char == "\\" for char in value):
            raise ResourceStoreError("中转站网址须为完整的 HTTP/HTTPS 地址，例如 https://example.com")
        return value

    def _record(self, payload: Any, existing: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        if not isinstance(payload, dict):
            raise ResourceStoreError(f"{self.value_label}格式无效")
        current = existing or {}
        name = self._text(payload.get("name", current.get("name")), "名称", MAX_NAME_LENGTH, required=True)
        note = self._text(payload.get("note", current.get("note", "")), "备注", MAX_NOTE_LENGTH)
        value = payload.get(self.secret_field, current.get(self.secret_field, ""))
        if value == "" and existing is not None:
            value = current.get(self.secret_field, "")
        value = self._text(value, self.value_label, MAX_VALUE_LENGTH, required=True)
        item: Dict[str, str] = {
            "id": str(current.get("id") or secrets.token_hex(8)),
            "name": name,
            "note": note,
            self.secret_field: value,
        }
        if self.secret_field == "password":
            item["account"] = self._text(
                payload.get("account", current.get("account", "")),
                "账号",
                MAX_NAME_LENGTH,
                required=True,
            )
            item["url"] = self._url(payload.get("url", current.get("url")))
        return item

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return "未设置"
        if len(value) <= 6:
            return "••••••"
        return f"{value[:3]}{'•' * 4}{value[-3:]}"

    def _public(self, item: Dict[str, str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": item["id"],
            "name": item["name"],
            "note": item.get("note", ""),
        }
        if self.secret_field == "api_key":
            masked = self._mask(item["api_key"])
            result.update({"api_key": masked, "api_key_masked": masked, "has_api_key": True})
        else:
            result.update({"account": item.get("account", ""), "url": item.get("url", "")})
            masked = self._mask(item["password"])
            result.update({"password": masked, "password_masked": masked, "has_password": True})
        return result

    def list_public(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._public(item) for item in self._items]

    def get_secret(self, item_id: str) -> Optional[str]:
        with self._lock:
            item = next((item for item in self._items if item["id"] == item_id), None)
            return item.get(self.secret_field) if item else None

    def create(self, payload: Any) -> Dict[str, Any]:
        with self._lock:
            item = self._record(payload)
            self._items = [*self._items, item]
            self._save()
            return self._public(item)

    def update(self, item_id: str, payload: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            current = next((item for item in self._items if item["id"] == item_id), None)
            if current is None:
                return None
            updated = self._record(payload, current)
            self._items = [updated if item["id"] == item_id else item for item in self._items]
            self._save()
            return self._public(updated)

    def delete(self, item_id: str) -> bool:
        with self._lock:
            remaining = [item for item in self._items if item["id"] != item_id]
            if len(remaining) == len(self._items):
                return False
            self._items = remaining
            self._save()
            return True
