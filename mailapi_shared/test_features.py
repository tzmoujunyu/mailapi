"""Test-only API2API and finance features.

This module is intentionally mounted only when ``MAILAPI_MODE=test``. It uses
its own SQLite database under the test data directory so experiments cannot
write production resource pools or website-account data.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Optional
from urllib.parse import quote, urlsplit
import zipfile

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from .credential_crypto import CredentialCryptoError, CredentialKeyring
from .site_accounts import ADMIN_ROLES


MAX_API_KEY_NAME = 80
MAX_MODEL_NAME = 120
MAX_FINANCE_PROJECT = 20
MAX_FINANCE_NOTE = 500
MAX_FINANCE_FILE_BYTES = 10 * 1024 * 1024
MAX_FINANCE_AMOUNT_CENTS = 100_000_000
ALLOWED_FINANCE_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
API_KEY_PATTERN = re.compile(r"^mk_[A-Za-z0-9_-]{32,}$")
SCHEMA = """
PRAGMA user_version = 1;
CREATE TABLE IF NOT EXISTS api2api_config (
    id INTEGER PRIMARY KEY CHECK (id = 1), base_url TEXT NOT NULL,
    upstream_key TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
    model_allowlist TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api2api_keys (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE, key_prefix TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api2api_keys_user ON api2api_keys(user_id);
CREATE TABLE IF NOT EXISTS api2api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key_id TEXT NOT NULL,
    user_id TEXT NOT NULL, model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL, total_tokens INTEGER NOT NULL,
    status_code INTEGER NOT NULL, request_ms INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api2api_usage_user_time ON api2api_usage(user_id, created_at);
CREATE TABLE IF NOT EXISTS finance_applications (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, username TEXT NOT NULL,
    user_note TEXT NOT NULL, groups_json TEXT NOT NULL, project TEXT NOT NULL,
    amount_cents INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, reviewed_at TEXT, reviewer_id TEXT, reviewer_name TEXT,
    review_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_finance_user_time ON finance_applications(user_id, created_at);
CREATE TABLE IF NOT EXISTS finance_files (
    id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES finance_applications(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, stored_name TEXT NOT NULL, original_name TEXT NOT NULL,
    content_type TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finance_announcements (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL
);
"""


class TestFeatureError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_component(value: Any, fallback: str = "未命名", limit: int = 80) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", "_", text).strip("._")
    return (text or fallback)[:limit]


def _content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"


class TestFeatureStore:
    def __init__(self, data_dir: str | Path, keyring: Optional[CredentialKeyring]):
        self.root = Path(data_dir).resolve()
        self.path = self.root / "test_features.sqlite3"
        self.files = self.root / "finance_files"
        self.keyring = keyring
        self._lock = __import__("threading").RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.files.mkdir(mode=0o700, exist_ok=True)
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA synchronous = NORMAL")
        return db

    def _initialize(self):
        self.path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.path, 0o600)
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(finance_applications)")}
            if "updated_at" not in columns:
                db.execute("ALTER TABLE finance_applications ADD COLUMN updated_at TEXT")
                db.execute("UPDATE finance_applications SET updated_at=created_at WHERE updated_at IS NULL")

    @staticmethod
    def _text(value: Any, label: str, maximum: int, *, required: bool = False) -> str:
        if not isinstance(value, str):
            if required:
                raise TestFeatureError(f"{label}不能为空")
            return ""
        text = value.strip()
        if required and not text:
            raise TestFeatureError(f"{label}不能为空")
        if len(text) > maximum:
            raise TestFeatureError(f"{label}不能超过 {maximum} 个字符")
        return text

    @staticmethod
    def _base_url(value: Any) -> str:
        value = TestFeatureStore._text(value, "NewAPI 地址", 2048, required=True).rstrip("/")
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise TestFeatureError("NewAPI 地址须为不含账号、查询参数和片段的 HTTP/HTTPS 地址")
        return value

    def _seal_upstream_key(self, value: str) -> str:
        if not self.keyring:
            raise TestFeatureError("测试版 API2API 需要配置独立的测试加密密钥", 409)
        envelope = self.keyring.seal(value, "test-feature", "api2api", "upstream-key")
        return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))

    def _open_upstream_key(self, value: str) -> str:
        if not self.keyring:
            raise CredentialCryptoError("测试版缺少 API2API 测试加密密钥")
        return str(self.keyring.open(json.loads(value), "test-feature", "api2api", "upstream-key"))

    def configure_api2api(self, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = self._base_url(payload.get("base_url"))
        enabled = payload.get("enabled", True) is True
        models = payload.get("model_allowlist", [])
        if not isinstance(models, list) or any(not isinstance(model, str) for model in models):
            raise TestFeatureError("模型限制必须是文本列表")
        model_allowlist = []
        for model in models:
            model = self._text(model, "模型名", MAX_MODEL_NAME, required=True)
            if model not in model_allowlist:
                model_allowlist.append(model)
        if len(model_allowlist) > 100:
            raise TestFeatureError("模型限制最多 100 个")
        with self._lock, closing(self._connect()) as db, db:
            current = db.execute("SELECT upstream_key FROM api2api_config WHERE id=1").fetchone()
            raw_key = payload.get("upstream_key")
            if raw_key in (None, "") and current:
                sealed = str(current[0])
            else:
                raw_key = self._text(raw_key, "NewAPI API Key", 4096, required=True)
                sealed = self._seal_upstream_key(raw_key)
            db.execute(
                """INSERT INTO api2api_config(id, base_url, upstream_key, enabled, model_allowlist, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url,
                   upstream_key=excluded.upstream_key, enabled=excluded.enabled,
                   model_allowlist=excluded.model_allowlist, updated_at=excluded.updated_at""",
                (base_url, sealed, int(enabled), json.dumps(model_allowlist, ensure_ascii=False), _now()),
            )
        return self.api2api_config_public()

    def api2api_config(self) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM api2api_config WHERE id=1").fetchone()
        if not row:
            return None
        return {
            "base_url": row["base_url"],
            "upstream_key": self._open_upstream_key(row["upstream_key"]),
            "enabled": bool(row["enabled"]),
            "model_allowlist": json.loads(row["model_allowlist"]),
        }

    def api2api_config_public(self) -> dict[str, Any]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM api2api_config WHERE id=1").fetchone()
        if not row:
            return {"configured": False, "base_url": "", "enabled": False, "model_allowlist": [], "has_upstream_key": False}
        return {
            "configured": True,
            "base_url": row["base_url"],
            "enabled": bool(row["enabled"]),
            "model_allowlist": json.loads(row["model_allowlist"]),
            "has_upstream_key": bool(row["upstream_key"]),
        }

    def create_api_key(self, user_id: str, name: Any) -> dict[str, Any]:
        name = self._text(name, "API Key 名称", MAX_API_KEY_NAME, required=True)
        raw = "mk_" + secrets.token_urlsafe(32)
        item_id = secrets.token_hex(12)
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO api2api_keys(id, user_id, name, key_hash, key_prefix, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, user_id, name, hashlib.sha256(raw.encode()).hexdigest(), raw[:12], now),
            )
        return {"id": item_id, "name": name, "api_key": raw, "key_prefix": raw[:12], "created_at": now}

    def authenticate_api_key(self, raw: str) -> Optional[dict[str, Any]]:
        if not isinstance(raw, str) or not API_KEY_PATTERN.fullmatch(raw):
            return None
        digest = hashlib.sha256(raw.encode()).hexdigest()
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            row = db.execute("SELECT * FROM api2api_keys WHERE key_hash=? AND enabled=1", (digest,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE api2api_keys SET last_used_at=? WHERE id=?", (now, row["id"]))
        return dict(row)

    def list_api_keys(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            if user_id:
                rows = db.execute("SELECT * FROM api2api_keys WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM api2api_keys ORDER BY created_at DESC").fetchall()
        return [{
            "id": row["id"], "user_id": row["user_id"], "name": row["name"],
            "key_prefix": row["key_prefix"], "enabled": bool(row["enabled"]),
            "created_at": row["created_at"], "last_used_at": row["last_used_at"],
        } for row in rows]

    def revoke_api_key(self, key_id: str, user_id: Optional[str] = None) -> bool:
        with self._lock, closing(self._connect()) as db, db:
            if user_id:
                result = db.execute("UPDATE api2api_keys SET enabled=0 WHERE id=? AND user_id=?", (key_id, user_id))
            else:
                result = db.execute("UPDATE api2api_keys SET enabled=0 WHERE id=?", (key_id,))
            return result.rowcount == 1

    def record_usage(self, *, key_id: str, user_id: str, model: str, prompt_tokens: int,
                     completion_tokens: int, total_tokens: int, status_code: int,
                     request_ms: int, error: str = "") -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                """INSERT INTO api2api_usage(key_id,user_id,model,prompt_tokens,completion_tokens,total_tokens,
                   status_code,request_ms,error,created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key_id, user_id, model[:MAX_MODEL_NAME], max(0, prompt_tokens), max(0, completion_tokens),
                 max(0, total_tokens), status_code, max(0, request_ms), error[:200], _now()),
            )

    def usage_summary(self, user_id: Optional[str] = None) -> dict[str, Any]:
        with closing(self._connect()) as db:
            where, args = ("WHERE user_id=?", (user_id,)) if user_id else ("", ())
            summary = db.execute(
                f"SELECT COUNT(*) requests, COALESCE(SUM(prompt_tokens),0) prompt, COALESCE(SUM(completion_tokens),0) completion, COALESCE(SUM(total_tokens),0) total FROM api2api_usage {where}",
                args,
            ).fetchone()
            rows = db.execute(
                f"SELECT model, prompt_tokens, completion_tokens, total_tokens, status_code, request_ms, error, created_at FROM api2api_usage {where} ORDER BY id DESC LIMIT 100",
                args,
            ).fetchall()
        return {
            "requests": int(summary["requests"]), "prompt_tokens": int(summary["prompt"]),
            "completion_tokens": int(summary["completion"]), "total_tokens": int(summary["total"]),
            "items": [dict(row) for row in rows],
        }

    def usage_by_user(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT user_id, COUNT(*) requests, COALESCE(SUM(prompt_tokens),0) prompt,
                   COALESCE(SUM(completion_tokens),0) completion, COALESCE(SUM(total_tokens),0) total
                   FROM api2api_usage GROUP BY user_id ORDER BY total DESC"""
            ).fetchall()
        return [{"user_id": row["user_id"], "requests": row["requests"], "prompt_tokens": row["prompt"],
                 "completion_tokens": row["completion"], "total_tokens": row["total"]} for row in rows]

    def create_finance_application(self, *, user_id: str, username: str, user_note: str,
                                   groups: list[str], project: str, amount_cents: int,
                                   files: list[dict[str, Any]]) -> dict[str, Any]:
        app_id = secrets.token_hex(12)
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                """INSERT INTO finance_applications(id,user_id,username,user_note,groups_json,project,amount_cents,status,created_at,updated_at,review_note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, '')""",
                (app_id, user_id, username, user_note, json.dumps(groups, ensure_ascii=False), project, amount_cents, now, now),
            )
            for item in files:
                db.execute(
                    """INSERT INTO finance_files(id,application_id,kind,stored_name,original_name,content_type,size,sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["id"], app_id, item["kind"], item["stored_name"], item["original_name"],
                     item["content_type"], item["size"], item["sha256"]),
                )
        return self.finance_application(app_id)

    def finance_application(self, app_id: str) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM finance_applications WHERE id=?", (app_id,)).fetchone()
            if not row:
                return None
            files = db.execute("SELECT * FROM finance_files WHERE application_id=? ORDER BY kind", (app_id,)).fetchall()
        result = dict(row)
        result["groups"] = json.loads(result.pop("groups_json"))
        result["amount"] = f"{result.pop('amount_cents') / 100:.2f}"
        result["files"] = [{"id": item["id"], "kind": item["kind"], "stored_name": item["stored_name"],
                            "original_name": item["original_name"], "content_type": item["content_type"],
                            "size": item["size"], "sha256": item["sha256"]} for item in files]
        return result

    def list_finance_applications(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            if user_id:
                rows = db.execute("SELECT id FROM finance_applications WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
            else:
                rows = db.execute("SELECT id FROM finance_applications ORDER BY created_at DESC").fetchall()
        return [self.finance_application(row["id"]) for row in rows]

    def review_finance_application(self, app_id: str, status: str, reviewer_id: str, reviewer_name: str, note: str) -> Optional[dict[str, Any]]:
        if status not in {"approved", "rejected", "pending"}:
            raise TestFeatureError("审核状态无效")
        with self._lock, closing(self._connect()) as db, db:
            result = db.execute(
                "UPDATE finance_applications SET status=?,updated_at=?,reviewed_at=?,reviewer_id=?,reviewer_name=?,review_note=? WHERE id=? AND status='pending'",
                (status, _now(), _now(), reviewer_id, reviewer_name, self._text(note, "审核备注", MAX_FINANCE_NOTE), app_id),
            )
            if result.rowcount != 1:
                return None
        return self.finance_application(app_id)

    def update_finance_application(self, app_id: str, user_id: str, project: str,
                                   amount_cents: int, replacements: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
        now = _now()
        old_stored_names = []
        with self._lock, closing(self._connect()) as db, db:
            row = db.execute("SELECT user_id,status FROM finance_applications WHERE id=?", (app_id,)).fetchone()
            if not row or row["user_id"] != user_id or row["status"] != "pending":
                return None
            db.execute("UPDATE finance_applications SET project=?,amount_cents=?,updated_at=? WHERE id=?",
                       (project, amount_cents, now, app_id))
            for kind, item in replacements.items():
                previous = db.execute("SELECT stored_name FROM finance_files WHERE application_id=? AND kind=?",
                                      (app_id, kind)).fetchone()
                if previous:
                    old_stored_names.append(previous["stored_name"])
                db.execute("DELETE FROM finance_files WHERE application_id=? AND kind=?", (app_id, kind))
                db.execute(
                    """INSERT INTO finance_files(id,application_id,kind,stored_name,original_name,content_type,size,sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item["id"], app_id, kind, item["stored_name"], item["original_name"], item["content_type"], item["size"], item["sha256"]),
                )
        for stored_name in old_stored_names:
            (self.files / stored_name).unlink(missing_ok=True)
        return self.finance_application(app_id)

    def cancel_finance_application(self, app_id: str, user_id: str) -> Optional[dict[str, Any]]:
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            result = db.execute(
                "UPDATE finance_applications SET status='cancelled',updated_at=? WHERE id=? AND user_id=? AND status='pending'",
                (now, app_id, user_id),
            )
            if result.rowcount != 1:
                return None
        return self.finance_application(app_id)

    def announcements(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        where = "" if include_disabled else "WHERE enabled=1"
        with closing(self._connect()) as db:
            rows = db.execute(f"SELECT * FROM finance_announcements {where} ORDER BY pinned DESC, updated_at DESC").fetchall()
        return [dict(row) | {"pinned": bool(row["pinned"]), "enabled": bool(row["enabled"])} for row in rows]

    def save_announcement(self, *, announcement_id: Optional[str], title: Any, body: Any, pinned: Any, actor: str) -> dict[str, Any]:
        title = self._text(title, "公告标题", 120, required=True)
        body = self._text(body, "公告内容", 5000, required=True)
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            if announcement_id:
                result = db.execute("UPDATE finance_announcements SET title=?,body=?,pinned=?,updated_at=? WHERE id=?",
                                    (title, body, int(pinned is True), now, announcement_id))
                if result.rowcount != 1:
                    return None
                item_id = announcement_id
            else:
                item_id = secrets.token_hex(12)
                db.execute("INSERT INTO finance_announcements(id,title,body,pinned,enabled,created_at,updated_at,created_by) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
                            (item_id, title, body, int(pinned is True), now, now, actor))
        return next(item for item in self.announcements(include_disabled=True) if item["id"] == item_id)

    def delete_announcement(self, announcement_id: str) -> bool:
        with self._lock, closing(self._connect()) as db, db:
            return db.execute("DELETE FROM finance_announcements WHERE id=?", (announcement_id,)).rowcount == 1


def _parse_amount(value: Any) -> int:
    try:
        amount = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise TestFeatureError("报销金额格式无效") from None
    cents = int(amount * 100)
    if cents <= 0 or cents > MAX_FINANCE_AMOUNT_CENTS:
        raise TestFeatureError("报销金额须大于 0 且不超过 1000000.00")
    return cents


def _usage_from_payload(payload: Any) -> tuple[int, int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    return max(0, prompt), max(0, completion), max(0, total)


class _StreamUsage:
    def __init__(self):
        self.buffer = b""
        self.usage = (0, 0, 0)

    def feed(self, chunk: bytes):
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            value = line[5:].strip()
            if value in {b"", b"[DONE]"}:
                continue
            try:
                self.usage = _usage_from_payload(json.loads(value))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue


def install_test_features(app, static_dir: Path, data_dir: str, keyring: Optional[CredentialKeyring]):
    store = TestFeatureStore(data_dir, keyring)
    router = APIRouter()
    client_holder: dict[str, Optional[httpx.AsyncClient]] = {"client": None}

    def current_user(request: Request):
        user = getattr(request.state, "site_user", None)
        if not user:
            raise HTTPException(401, "请先登录")
        return user

    def require_admin(request: Request):
        user = current_user(request)
        if user.get("role") not in ADMIN_ROLES:
            raise HTTPException(403, "需要管理员权限")
        return user

    def user_snapshot(request: Request):
        user = current_user(request)
        account = request.app.state.site_accounts.get_user(user["id"], include_note=True)
        groups = request.app.state.site_accounts.user_groups(user["id"])
        return account or user, [group["name"] for group in groups]

    def hydrate_application(request: Request, item: dict[str, Any]) -> dict[str, Any]:
        """Resolve current account and group metadata whenever an application is read."""
        account = request.app.state.site_accounts.get_user(item["user_id"], include_note=True)
        if not account:
            return item
        current = dict(item)
        current["username"] = account["username"]
        current["user_note"] = account.get("note", "")
        current["groups"] = [group["name"] for group in request.app.state.site_accounts.user_groups(item["user_id"])]
        return current

    def public_application(request: Request, item: Optional[dict[str, Any]]):
        if item is None:
            return None
        user = current_user(request)
        if user.get("role") not in ADMIN_ROLES and item["user_id"] != user["id"]:
            raise HTTPException(404, "未找到报销申请")
        item = hydrate_application(request, item)
        result = {key: value for key, value in item.items() if key != "user_id"}
        result["files"] = [{key: value for key, value in file_item.items() if key not in {"stored_name", "sha256"}}
                           for file_item in item["files"]]
        return result

    async def save_finance_uploads(uploads: list[tuple[str, UploadFile]]) -> list[dict[str, Any]]:
        saved = []
        try:
            for kind, upload in uploads:
                content_type = (upload.content_type or "").lower()
                suffix = ALLOWED_FINANCE_TYPES.get(content_type)
                if not suffix:
                    guessed_type = mimetypes.guess_type(upload.filename or "")[0] or ""
                    suffix = ALLOWED_FINANCE_TYPES.get(guessed_type)
                    if suffix:
                        content_type = guessed_type
                if not suffix:
                    raise TestFeatureError("仅支持 PDF、JPG、PNG 或 WebP 文件")
                content = await upload.read(MAX_FINANCE_FILE_BYTES + 1)
                if len(content) > MAX_FINANCE_FILE_BYTES:
                    raise TestFeatureError("单个报销文件不能超过 10 MB")
                file_id = secrets.token_hex(12)
                stored = f"{file_id}{suffix}"
                path = store.files / stored
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content)
                saved.append({"id": file_id, "kind": kind, "stored_name": stored,
                              "original_name": upload.filename or stored, "content_type": content_type,
                              "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        except Exception:
            for item in saved:
                (store.files / item["stored_name"]).unlink(missing_ok=True)
            raise
        return saved

    @router.get("/test-features")
    def test_features_page(request: Request):
        page = (static_dir / "test-features.html").read_text(encoding="utf-8")
        from .accounts import themed_page
        return HTMLResponse(themed_page(page, request), headers={"Cache-Control": "no-store, private"})

    @router.get("/api/test-features/status")
    def feature_status(request: Request):
        user = current_user(request)
        return {"role": user["role"], "api2api": store.api2api_config_public()}

    @router.get("/api/api2api/keys")
    def list_user_keys(request: Request):
        user = current_user(request)
        return {"items": store.list_api_keys(user["id"])}

    @router.post("/api/api2api/keys", status_code=201)
    async def create_user_key(request: Request):
        user = current_user(request)
        payload = await request.json()
        return {"item": store.create_api_key(user["id"], payload.get("name"))}

    @router.delete("/api/api2api/keys/{key_id}")
    def revoke_user_key(key_id: str, request: Request):
        user = current_user(request)
        if not store.revoke_api_key(key_id, user["id"]):
            raise HTTPException(404, "未找到 API Key")
        return {"ok": True}

    @router.get("/api/api2api/usage")
    def user_api_usage(request: Request):
        user = current_user(request)
        return store.usage_summary(user["id"])

    @router.get("/api/admin/api2api/config")
    def admin_api_config(request: Request):
        require_admin(request)
        return store.api2api_config_public()

    @router.put("/api/admin/api2api/config")
    async def save_api_config(request: Request):
        require_admin(request)
        try:
            return store.configure_api2api(await request.json())
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error

    @router.get("/api/admin/api2api/keys")
    def admin_api_keys(request: Request):
        require_admin(request)
        return {"items": store.list_api_keys()}

    @router.delete("/api/admin/api2api/keys/{key_id}")
    def admin_revoke_api_key(key_id: str, request: Request):
        require_admin(request)
        if not store.revoke_api_key(key_id):
            raise HTTPException(404, "未找到 API Key")
        return {"ok": True}

    @router.get("/api/admin/api2api/usage")
    def admin_api_usage(request: Request):
        require_admin(request)
        return {"items": store.usage_by_user()}

    @router.api_route("/api2api/v1/{proxy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def api2api_proxy(proxy_path: str, request: Request):
        if request.method == "OPTIONS":
            return Response(status_code=204)
        authorization = request.headers.get("authorization", "")
        raw_key = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        key = store.authenticate_api_key(raw_key)
        if not key:
            return JSONResponse({"error": {"message": "Invalid API key", "type": "authentication_error"}}, status_code=401)
        config = store.api2api_config()
        if not config or not config["enabled"] or not config["upstream_key"]:
            return JSONResponse({"error": {"message": "API2API is not configured", "type": "configuration_error"}}, status_code=503)
        body = await request.body()
        model = ""
        try:
            payload = json.loads(body) if body else {}
            model = str(payload.get("model") or "")[:MAX_MODEL_NAME] if isinstance(payload, dict) else ""
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if config["model_allowlist"] and model and model not in config["model_allowlist"]:
            return JSONResponse({"error": {"message": "Model is not enabled for this API2API endpoint", "type": "invalid_request_error"}}, status_code=403)
        if client_holder["client"] is None:
            client_holder["client"] = httpx.AsyncClient(
                limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
                timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=False,
            )
        target = config["base_url"].rstrip("/") + "/" + proxy_path.lstrip("/")
        headers = {"Authorization": f"Bearer {config['upstream_key']}", "Accept": request.headers.get("accept", "*/*"), "Accept-Encoding": "identity"}
        if request.headers.get("content-type"):
            headers["Content-Type"] = request.headers["content-type"]
        started = time.perf_counter()
        try:
            upstream_request = client_holder["client"].build_request(request.method, target, params=list(request.query_params.multi_items()), headers=headers, content=body)
            upstream = await client_holder["client"].send(upstream_request, stream=True)
            response_type = upstream.headers.get("content-type", "")
            response_headers = {name: value for name, value in upstream.headers.items() if name.lower() in {"content-type", "cache-control"}}
            if "text/event-stream" in response_type:
                collector = _StreamUsage()

                async def stream_body():
                    try:
                        async for chunk in upstream.aiter_bytes():
                            collector.feed(chunk)
                            yield chunk
                    finally:
                        await upstream.aclose()
                        prompt, completion, total = collector.usage
                        store.record_usage(key_id=key["id"], user_id=key["user_id"], model=model,
                                           prompt_tokens=prompt, completion_tokens=completion, total_tokens=total,
                                           status_code=upstream.status_code, request_ms=int((time.perf_counter() - started) * 1000),
                                           error="" if upstream.status_code < 400 else "upstream_error")
                return StreamingResponse(stream_body(), status_code=upstream.status_code, headers=response_headers)
            content = await upstream.aread()
            await upstream.aclose()
            try:
                response_payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError):
                response_payload = {}
            prompt, completion, total = _usage_from_payload(response_payload)
            store.record_usage(key_id=key["id"], user_id=key["user_id"], model=model,
                               prompt_tokens=prompt, completion_tokens=completion, total_tokens=total,
                               status_code=upstream.status_code, request_ms=int((time.perf_counter() - started) * 1000),
                               error="" if upstream.status_code < 400 else "upstream_error")
            return Response(content=content, status_code=upstream.status_code, headers=response_headers)
        except httpx.HTTPError as error:
            store.record_usage(key_id=key["id"], user_id=key["user_id"], model=model,
                               prompt_tokens=0, completion_tokens=0, total_tokens=0, status_code=502,
                               request_ms=int((time.perf_counter() - started) * 1000), error=type(error).__name__)
            return JSONResponse({"error": {"message": "Upstream API unavailable", "type": "upstream_error"}}, status_code=502)

    @router.get("/api/finance/announcements")
    def list_announcements(request: Request):
        current_user(request)
        return {"items": store.announcements()}

    @router.get("/api/finance/applications")
    def list_finance(request: Request):
        user = current_user(request)
        return {"items": [public_application(request, item) for item in store.list_finance_applications(user["id"])]}

    @router.post("/api/finance/applications", status_code=201)
    async def create_finance(request: Request, project: str = Form(...), amount: str = Form(...), invoice: UploadFile = File(...), payment_record: Optional[UploadFile] = File(None)):
        account, groups = user_snapshot(request)
        try:
            project = store._text(project, "报销项目", MAX_FINANCE_PROJECT, required=True)
            amount_cents = _parse_amount(amount)
            uploads = [("invoice", invoice)] + ([('payment', payment_record)] if payment_record else [])
            saved = await save_finance_uploads(uploads)
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        try:
            item = store.create_finance_application(user_id=account["id"], username=account["username"],
                                                    user_note=account.get("note", ""), groups=groups,
                                                    project=project, amount_cents=amount_cents, files=saved)
        except Exception:
            for entry in saved:
                (store.files / entry["stored_name"]).unlink(missing_ok=True)
            raise
        return {"item": public_application(request, item)}

    @router.put("/api/finance/applications/{app_id}")
    async def update_finance(app_id: str, request: Request, project: str = Form(...), amount: str = Form(...), invoice: Optional[UploadFile] = File(None), payment_record: Optional[UploadFile] = File(None)):
        user = current_user(request)
        try:
            project = store._text(project, "报销项目", MAX_FINANCE_PROJECT, required=True)
            amount_cents = _parse_amount(amount)
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        existing = store.finance_application(app_id)
        if not existing or existing["user_id"] != user["id"]:
            raise HTTPException(404, "未找到报销申请")
        if existing["status"] != "pending":
            raise HTTPException(409, "只有待审核申请可以修改")
        uploads = []
        if invoice:
            uploads.append(("invoice", invoice))
        if payment_record:
            uploads.append(("payment", payment_record))
        try:
            saved = await save_finance_uploads(uploads)
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        try:
            updated = store.update_finance_application(app_id, user["id"], project, amount_cents, {item["kind"]: item for item in saved})
        except Exception:
            for item in saved:
                (store.files / item["stored_name"]).unlink(missing_ok=True)
            raise
        if not updated:
            for item in saved:
                (store.files / item["stored_name"]).unlink(missing_ok=True)
            raise HTTPException(409, "申请状态已变化，请刷新后重试")
        return {"item": public_application(request, updated)}

    @router.post("/api/finance/applications/{app_id}/cancel")
    def cancel_finance(app_id: str, request: Request):
        user = current_user(request)
        item = store.cancel_finance_application(app_id, user["id"])
        if not item:
            raise HTTPException(409, "只有本人提交的待审核申请可以撤销")
        return {"item": public_application(request, item)}

    @router.get("/api/admin/finance/applications")
    def admin_finance(request: Request):
        require_admin(request)
        return {"items": [public_application(request, item) for item in store.list_finance_applications()]}

    @router.get("/api/admin/finance/announcements")
    def admin_announcements(request: Request):
        require_admin(request)
        return {"items": store.announcements(include_disabled=True)}

    @router.post("/api/admin/finance/applications/{app_id}/review")
    async def review_finance(app_id: str, request: Request):
        reviewer = require_admin(request)
        payload = await request.json()
        try:
            item = store.review_finance_application(app_id, payload.get("status"), reviewer["id"], reviewer["username"], payload.get("note", ""))
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        if not item:
            raise HTTPException(404, "未找到报销申请")
        return {"item": item}

    @router.post("/api/admin/finance/announcements", status_code=201)
    async def create_announcement(request: Request):
        admin = require_admin(request)
        payload = await request.json()
        try:
            item = store.save_announcement(announcement_id=None, title=payload.get("title"), body=payload.get("body"), pinned=payload.get("pinned"), actor=admin["username"])
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        return {"item": item}

    @router.put("/api/admin/finance/announcements/{announcement_id}")
    async def update_announcement(announcement_id: str, request: Request):
        admin = require_admin(request)
        payload = await request.json()
        try:
            item = store.save_announcement(announcement_id=announcement_id, title=payload.get("title"), body=payload.get("body"), pinned=payload.get("pinned"), actor=admin["username"])
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        if not item:
            raise HTTPException(404, "未找到公告")
        return {"item": item}

    @router.delete("/api/admin/finance/announcements/{announcement_id}")
    def delete_announcement(announcement_id: str, request: Request):
        require_admin(request)
        if not store.delete_announcement(announcement_id):
            raise HTTPException(404, "未找到公告")
        return {"ok": True}

    def get_allowed_application(request: Request, app_id: str):
        item = store.finance_application(app_id)
        if not item:
            raise HTTPException(404, "未找到报销申请")
        public_application(request, item)
        return hydrate_application(request, item)

    def application_folder(item):
        group = "、".join(item["groups"]) if item["groups"] else "未分组"
        identity = item["username"] + (f"({item['user_note']})" if item["user_note"] else "")
        return f"{_safe_component(group)}_{_safe_component(identity)}"

    def application_filename(item, file_item):
        group = "、".join(item["groups"]) if item["groups"] else "未分组"
        identity = item["username"] + (f"({item['user_note']})" if item["user_note"] else "")
        label = "发票" if file_item["kind"] == "invoice" else "支付记录"
        suffix = Path(file_item["stored_name"]).suffix.lower() or ".bin"
        return f"{_safe_component(group)}_{_safe_component(identity)}_{_safe_component(item['project'])}_{label}{suffix}"

    def zip_applications(items: list[dict[str, Any]]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            used = set()
            for item in items:
                folder = application_folder(item)
                for file_item in item["files"]:
                    path = store.files / file_item["stored_name"]
                    if not path.is_file():
                        continue
                    filename = application_filename(item, file_item)
                    archive_name = f"{folder}/{filename}"
                    if archive_name in used:
                        archive_name = f"{folder}/{Path(filename).stem}_{item['id'][:8]}{Path(filename).suffix}"
                    used.add(archive_name)
                    archive.writestr(archive_name, path.read_bytes())
        return output.getvalue()

    @router.get("/api/finance/applications/{app_id}/download")
    def download_finance_file(app_id: str, request: Request, kind: str):
        item = get_allowed_application(request, app_id)
        file_item = next((entry for entry in item["files"] if entry["kind"] == kind), None)
        if not file_item:
            raise HTTPException(404, "未找到文件")
        path = store.files / file_item["stored_name"]
        if not path.is_file():
            raise HTTPException(410, "文件已丢失")
        return FileResponse(path, media_type=file_item["content_type"], filename=application_filename(item, file_item))

    @router.get("/api/finance/applications/{app_id}/download.zip")
    def download_finance_zip(app_id: str, request: Request):
        item = get_allowed_application(request, app_id)
        data = zip_applications([item])
        filename = datetime.now().strftime("%Y%m%d") + "报销申请.zip"
        return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={"Content-Disposition": _content_disposition(filename)})

    @router.post("/api/admin/finance/download.zip")
    async def download_finance_batch(request: Request):
        require_admin(request)
        payload = await request.json()
        ids = payload.get("application_ids") if isinstance(payload, dict) else None
        if not isinstance(ids, list) or not ids or any(not isinstance(value, str) for value in ids):
            raise HTTPException(400, "请选择报销申请")
        items = [store.finance_application(value) for value in ids]
        if any(item is None for item in items):
            raise HTTPException(404, "报销申请不存在")
        items = [hydrate_application(request, item) for item in items]
        data = zip_applications(items)
        filename = datetime.now().strftime("%Y%m%d") + "报销申请.zip"
        return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={"Content-Disposition": _content_disposition(filename)})

    app.include_router(router)

    @app.on_event("shutdown")
    async def close_test_feature_client():
        client = client_holder["client"]
        if client is not None:
            await client.aclose()

    return store
