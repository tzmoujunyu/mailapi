"""Test-only API2API and finance features.

This module is intentionally mounted only when ``MAILAPI_MODE=test``. It uses
its own SQLite database under the test data directory so experiments cannot
write production resource pools or website-account data.
"""
from __future__ import annotations

import asyncio
import anyio
import csv
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import tempfile
from typing import Any, Optional
from urllib.parse import quote, urlsplit
import zipfile

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

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
API_KEY_PATTERN = re.compile(r"^(?:mk_|sk-)[A-Za-z0-9_-]{24,256}$")


@lru_cache(maxsize=256)
def _api_ip_networks(value):
    if not isinstance(value,str) or len(value)>8192:
        raise ValueError('Invalid IP policy')
    entries = [line.strip() for line in value.splitlines() if line.strip()]
    if len(entries)>100 or any('%' in line for line in entries):
        raise ValueError('Invalid IP policy')
    networks = []
    for entry in entries:
        network = ipaddress.ip_network(entry,strict=False)
        if network.version==6 and network.prefixlen>=96 and network.network_address.ipv4_mapped:
            network = ipaddress.ip_network((network.network_address.ipv4_mapped,network.prefixlen-96),strict=False)
        if network not in networks:
            networks.append(network)
    return tuple(networks)


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
CREATE TABLE IF NOT EXISTS api2api_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, completed_at TEXT,
    actor_id TEXT NOT NULL, actor_name TEXT NOT NULL, action TEXT NOT NULL,
    owner_id TEXT NOT NULL DEFAULT '', key_id TEXT NOT NULL DEFAULT '',
    key_name TEXT NOT NULL DEFAULT '', key_prefix TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_api2api_audit_time ON api2api_audit(created_at,id);
CREATE TABLE IF NOT EXISTS newapi_users (
    origin TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT NOT NULL,
    credential TEXT NOT NULL, remote_id INTEGER, initialized INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(origin, user_id)
);
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
CREATE TABLE IF NOT EXISTS finance_versions (
    application_id TEXT NOT NULL REFERENCES finance_applications(id), version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL, PRIMARY KEY(application_id, version)
);
CREATE TABLE IF NOT EXISTS finance_events (
    id TEXT PRIMARY KEY, application_id TEXT NOT NULL, version INTEGER NOT NULL,
    action TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    supersedes_event_id TEXT REFERENCES finance_events(id),
    FOREIGN KEY(application_id, version) REFERENCES finance_versions(application_id, version)
);
CREATE INDEX IF NOT EXISTS idx_finance_events_time ON finance_events(created_at);
CREATE INDEX IF NOT EXISTS idx_finance_events_application ON finance_events(application_id);
CREATE TABLE IF NOT EXISTS finance_invoice_details (
    application_id TEXT PRIMARY KEY REFERENCES finance_applications(id), file_id TEXT NOT NULL,
    invoice_key TEXT NOT NULL, fields_json TEXT NOT NULL, recognition_json TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'unverified', verification_note TEXT NOT NULL DEFAULT '',
    verified_by TEXT NOT NULL DEFAULT '', verified_by_name TEXT NOT NULL DEFAULT '', verified_at TEXT,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_finance_invoice_key ON finance_invoice_details(invoice_key);
CREATE INDEX IF NOT EXISTS idx_finance_invoice_hash ON finance_files(kind,sha256);
CREATE TABLE IF NOT EXISTS finance_invoice_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL REFERENCES finance_applications(id),
    version INTEGER NOT NULL, file_id TEXT NOT NULL, fields_json TEXT NOT NULL,
    status TEXT NOT NULL, note TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS finance_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL REFERENCES finance_applications(id),
    event_id TEXT REFERENCES finance_events(id), kind TEXT NOT NULL, audience TEXT NOT NULL, user_id TEXT NOT NULL,
    payload_json TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_finance_notifications_application ON finance_notifications(application_id,kind,id);
CREATE TABLE IF NOT EXISTS finance_notification_reads (
    user_id TEXT NOT NULL, notification_id INTEGER NOT NULL REFERENCES finance_notifications(id),
    PRIMARY KEY(user_id,notification_id)
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
            for table, fields in {
                'api2api_config': [('engine', "TEXT NOT NULL DEFAULT 'legacy'"),
                                  ('management_username', "TEXT NOT NULL DEFAULT ''"),
                                  ('management_password', "TEXT NOT NULL DEFAULT ''"),
                                  ('initial_quota', 'INTEGER NOT NULL DEFAULT 0')],
                'api2api_keys': [('provider', "TEXT NOT NULL DEFAULT 'legacy'"),
                                ('remote_id', 'INTEGER'), ('engine_origin', "TEXT NOT NULL DEFAULT ''"),
                                ('revoked','INTEGER NOT NULL DEFAULT 0'),
                                ('caller_ip_allowlist',"TEXT NOT NULL DEFAULT ''")],
            }.items():
                existing = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                for name, definition in fields:
                    if name not in existing:
                        db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
                        if table == 'api2api_keys' and name == 'revoked':
                            db.execute('UPDATE api2api_keys SET revoked=1 WHERE enabled=0')
            columns = {row[1] for row in db.execute("PRAGMA table_info(finance_applications)")}
            if 'supersedes_event_id' not in {row[1] for row in db.execute('PRAGMA table_info(finance_events)')}:
                db.execute('ALTER TABLE finance_events ADD COLUMN supersedes_event_id TEXT REFERENCES finance_events(id)')
            if "updated_at" not in columns:
                db.execute("ALTER TABLE finance_applications ADD COLUMN updated_at TEXT")
                db.execute("UPDATE finance_applications SET updated_at=created_at WHERE updated_at IS NULL")
            for name, definition in (("version", "INTEGER NOT NULL DEFAULT 1"),
                                     ("expense_group_id", "TEXT NOT NULL DEFAULT ''"),
                                     ("expense_group_name", "TEXT NOT NULL DEFAULT '未分组'")):
                if name not in columns:
                    db.execute(f"ALTER TABLE finance_applications ADD COLUMN {name} {definition}")
            # Legacy rows have no reliable group id. Preserve a single recorded name;
            # never guess which group paid a legacy multi-group application.
            for row in db.execute("SELECT * FROM finance_applications WHERE id NOT IN (SELECT application_id FROM finance_versions)").fetchall():
                groups = json.loads(row["groups_json"])
                name = groups[0] if len(groups) == 1 else ("待确认" if groups else "未分组")
                db.execute("UPDATE finance_applications SET expense_group_name=? WHERE id=?", (name, row["id"]))
                self._finance_event(db, row["id"], "legacy", row["user_id"], row["username"], "旧申请导入", row["created_at"],notify=False)
                if row["status"] in {"approved", "rejected"}:
                    self._finance_event(db, row["id"], row["status"], row["reviewer_id"] or "", row["reviewer_name"] or "", row["review_note"], row["reviewed_at"] or row["updated_at"],notify=False)

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
        value = TestFeatureStore._text(value, "网关地址", 2048, required=True).rstrip("/")
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
        if not valid or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise TestFeatureError("网关地址须为不含账号、查询参数和片段的 HTTP/HTTPS 地址")
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
        engine = payload.get('engine', 'legacy')
        if engine not in {'legacy', 'newapi'}:
            raise TestFeatureError('未知的 API 引擎')
        initial_quota = payload.get('initial_quota', 0)
        if type(initial_quota) is not int or not 0 <= initial_quota <= 1_000_000_000:
            raise TestFeatureError('初始额度须为 0–1000000000 的整数')
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
            current = db.execute("SELECT * FROM api2api_config WHERE id=1").fetchone()
            username, management_password = '', ''
            if engine == 'newapi':
                parsed = urlsplit(base_url)
                try:
                    loopback = ipaddress.ip_address(parsed.hostname).is_loopback
                except ValueError:
                    loopback = parsed.hostname == 'localhost'
                if parsed.scheme != 'https' and not loopback:
                    raise TestFeatureError('网关管理连接须使用 HTTPS，本机回环测试除外')
                if not base_url.endswith('/v1'):
                    raise TestFeatureError('网关地址须以 /v1 结尾')
                username = self._text(payload.get('management_username'), '网关管理账户', 80, required=True)
                password = payload.get('management_password')
                if password:
                    if not isinstance(password, str) or len(password) > 4096:
                        raise TestFeatureError('网关管理凭据长度无效')
                    management_password = self.seal_gateway_secret(password, 'management:'+base_url+':'+username)
                elif current and current['engine'] == engine and current['base_url'] == base_url and current['management_username'] == username:
                    management_password = current['management_password']
                else:
                    raise TestFeatureError('首次连接或更换网关时须填写独立管理凭据')
            raw_key = payload.get("upstream_key")
            if engine == 'newapi':
                sealed = self._seal_upstream_key('')
            elif raw_key in (None, "") and current and current['engine'] == 'legacy':
                sealed = str(current['upstream_key'])
            else:
                raw_key = self._text(raw_key, "网关 API Key", 4096, required=True)
                sealed = self._seal_upstream_key(raw_key)
            db.execute(
                """INSERT INTO api2api_config(id, base_url, upstream_key, enabled, model_allowlist, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url,
                   upstream_key=excluded.upstream_key, enabled=excluded.enabled,
                   model_allowlist=excluded.model_allowlist, updated_at=excluded.updated_at""",
                (base_url, sealed, int(enabled), json.dumps(model_allowlist, ensure_ascii=False), _now()),
            )
            db.execute('UPDATE api2api_config SET engine=?,management_username=?,management_password=?,initial_quota=? WHERE id=1',
                       (engine, username, management_password, initial_quota))
        return self.api2api_config_public()

    def api2api_config(self) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM api2api_config WHERE id=1").fetchone()
        if not row:
            return None
        return {
            'engine': row['engine'],
            "base_url": row["base_url"],
            "upstream_key": self._open_upstream_key(row["upstream_key"]),
            "enabled": bool(row["enabled"]),
            "model_allowlist": json.loads(row["model_allowlist"]),
            'management_username': row['management_username'],
            'management_password': self.open_gateway_secret(row['management_password'], 'management:'+row['base_url']+':'+row['management_username']) if row['management_password'] else '',
            'initial_quota': row['initial_quota'],
        }

    def api2api_config_public(self) -> dict[str, Any]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM api2api_config WHERE id=1").fetchone()
        if not row:
            return {"configured": False, 'engine':'legacy', "base_url": "", "enabled": False, "model_allowlist": [], "has_upstream_key": False}
        return {
            'engine': row['engine'],
            "configured": True,
            "base_url": row["base_url"],
            "enabled": bool(row["enabled"]),
            "model_allowlist": json.loads(row["model_allowlist"]),
            "has_upstream_key": bool(row["upstream_key"]),
            'management_username': row['management_username'],
            'has_management_password': bool(row['management_password']),
            'initial_quota': row['initial_quota'],
        }

    def seal_gateway_secret(self, value, scope):
        if not self.keyring:
            raise TestFeatureError('网关接入需要独立测试加密密钥', 409)
        return json.dumps(self.keyring.seal(value, 'test-feature', scope, 'secret'), separators=(',', ':'))

    def open_gateway_secret(self, value, scope):
        if not self.keyring:
            raise CredentialCryptoError('缺少测试加密密钥')
        return self.keyring.open(json.loads(value), 'test-feature', scope, 'secret')

    def gateway_user(self, origin, user_id, *, create=True):
        scope = 'newapi-user:'+origin+':'+user_id
        with self._lock, closing(self._connect()) as db, db:
            row = db.execute('SELECT * FROM newapi_users WHERE origin=? AND user_id=?', (origin,user_id)).fetchone()
            if not row and not create:
                return None
            if not row:
                username = 'mw_'+hashlib.sha256(user_id.encode()).hexdigest()[:16]
                credential = self.seal_gateway_secret(secrets.token_urlsafe(40), scope)
                db.execute('INSERT INTO newapi_users(origin,user_id,username,credential) VALUES(?,?,?,?)', (origin,user_id,username,credential))
                row = db.execute('SELECT * FROM newapi_users WHERE origin=? AND user_id=?', (origin,user_id)).fetchone()
        return {**dict(row), 'password':self.open_gateway_secret(row['credential'],scope)}

    def finish_gateway_user(self, origin, user_id, remote_id):
        with self._lock, closing(self._connect()) as db, db:
            db.execute('UPDATE newapi_users SET remote_id=?, initialized=1 WHERE origin=? AND user_id=?', (remote_id,origin,user_id))

    def register_gateway_key(self, user_id, name, item_id, remote_id, origin, raw, caller_ips=''):
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute('INSERT INTO api2api_keys(id,user_id,name,key_hash,key_prefix,created_at,provider,remote_id,engine_origin,caller_ip_allowlist) VALUES(?,?,?,?,?,?,\'newapi\',?,?,?)',
                       (item_id,user_id,name,hashlib.sha256(raw.encode()).hexdigest(),raw[:12],now,remote_id,origin,caller_ips))
        return {'id':item_id,'name':name,'api_key':raw,'key_prefix':raw[:12],'provider':'newapi','created_at':now}

    def gateway_key(self, key_id, user_id=None):
        with closing(self._connect()) as db:
            query, args = 'SELECT * FROM api2api_keys WHERE id=?', [key_id]
            if user_id is not None:
                query += ' AND user_id=?'
                args.append(user_id)
            row = db.execute(query,args).fetchone()
        return dict(row) if row else None

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
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM api2api_keys WHERE key_hash=? AND enabled=1 AND revoked=0", (digest,)).fetchone()
            if not row:
                return None
        return dict(row)

    @staticmethod
    def normalize_api_key_ips(value):
        try:
            networks = _api_ip_networks(value)
        except (ValueError,TypeError):
            raise TestFeatureError('IP 白名单格式无效：每行填写一个 IPv4、IPv6 地址或 CIDR 网段，最多 100 条') from None
        return '\n'.join(str(network.network_address) if network.prefixlen==network.max_prefixlen else str(network) for network in networks)

    def set_api_key_ips(self, key_id, user_id, value, *, expected):
        value = self.normalize_api_key_ips(value)
        with self._lock, closing(self._connect()) as db, db:
            return db.execute('''UPDATE api2api_keys SET caller_ip_allowlist=?
                WHERE id=? AND user_id=? AND revoked=0 AND caller_ip_allowlist=?''',
                (value,key_id,user_id,expected)).rowcount==1

    def update_api_key_settings(self, key_id, user_id, name, caller_ips, *, expected):
        with self._lock, closing(self._connect()) as db, db:
            return db.execute('''UPDATE api2api_keys SET name=?,caller_ip_allowlist=?
                WHERE id=? AND user_id=? AND revoked=0 AND name=? AND caller_ip_allowlist=?''',
                (name,caller_ips,key_id,user_id,expected['name'],expected['caller_ip_allowlist'])).rowcount==1

    @staticmethod
    def api_key_ip_allowed(key, host):
        policy = key.get('caller_ip_allowlist','')
        if policy=='':
            return True
        try:
            if not isinstance(host,str) or '%' in host:
                return False
            address = ipaddress.ip_address(host)
            if address.version==6 and address.ipv4_mapped:
                address = address.ipv4_mapped
            return any(address.version==network.version and address in network for network in _api_ip_networks(policy))
        except (ValueError,TypeError):
            return False

    def list_api_keys(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            if user_id:
                rows = db.execute("SELECT * FROM api2api_keys WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM api2api_keys ORDER BY created_at DESC").fetchall()
        return [{
            "id": row["id"], "user_id": row["user_id"], "name": row["name"],
            "key_prefix": row["key_prefix"], "enabled": bool(row["enabled"]),
            'revoked':bool(row['revoked']),
            'allowed_ips':row['caller_ip_allowlist'],
            "created_at": row["created_at"], "last_used_at": row["last_used_at"],
            'provider':row['provider'], 'remote_id':row['remote_id'], 'engine_origin':row['engine_origin'],
        } for row in rows]

    def revoke_api_key(self, key_id: str, user_id: Optional[str] = None) -> bool:
        with self._lock, closing(self._connect()) as db, db:
            if user_id:
                result = db.execute("UPDATE api2api_keys SET enabled=0,revoked=1 WHERE id=? AND user_id=?", (key_id, user_id))
            else:
                result = db.execute("UPDATE api2api_keys SET enabled=0,revoked=1 WHERE id=?", (key_id,))
            return result.rowcount == 1

    def set_api_key_enabled(self, key_id, user_id, enabled):
        with self._lock, closing(self._connect()) as db, db:
            return db.execute('UPDATE api2api_keys SET enabled=? WHERE id=? AND user_id=? AND revoked=0',
                              (int(enabled),key_id,user_id)).rowcount == 1

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
            db.execute('UPDATE api2api_keys SET last_used_at=? WHERE id=?', (_now(),key_id))

    def begin_api_audit(self, actor, action, key=None):
        key = key or {}
        name = re.sub(r'(?:sk-|mk_)[A-Za-z0-9_-]{24,}', '[已隐藏]', str(key.get('name','')))[:80]
        with self._lock, closing(self._connect()) as db, db:
            return db.execute('''INSERT INTO api2api_audit(created_at,actor_id,actor_name,action,owner_id,key_id,key_name,key_prefix)
                                 VALUES(?,?,?,?,?,?,?,?)''',
                              (_now(),actor['id'],actor.get('username',actor['id']),action,
                               key.get('user_id',actor['id'] if not key.get('id') else ''),key.get('id','')[:64],name,key.get('key_prefix','')[:12])).lastrowid

    def finish_api_audit(self, event_id, outcome, key=None):
        with self._lock, closing(self._connect()) as db, db:
            if key:
                db.execute('UPDATE api2api_audit SET key_id=?,key_prefix=? WHERE id=?',
                           (key['id'],key.get('key_prefix','')[:12],event_id))
            db.execute('UPDATE api2api_audit SET outcome=?,completed_at=? WHERE id=?', (outcome,_now(),event_id))

    def api_audit_page(self, *, page=1, page_size=20, query='', action='', outcome='', start='', end=''):
        filters, args = [], []
        if query:
            filters.append('(actor_name LIKE ? OR key_name LIKE ? OR key_id LIKE ?)')
            escaped = query.replace('!','!!').replace('%','!%').replace('_','!_')
            filters[-1] = filters[-1].replace('LIKE ?', "LIKE ? ESCAPE '!'")
            args.extend(['%'+escaped+'%']*3)
        for name,value in (('action',action),('outcome',outcome)):
            if value:
                filters.append(name+'=?'); args.append(value)
        if start:
            filters.append('created_at>=?'); args.append(start)
        if end:
            filters.append('created_at<=?'); args.append(end)
        where = ' WHERE '+' AND '.join(filters) if filters else ''
        with closing(self._connect()) as db:
            total = db.execute('SELECT COUNT(*) FROM api2api_audit'+where,args).fetchone()[0]
            rows = db.execute('SELECT * FROM api2api_audit'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',
                              [*args,page_size,(page-1)*page_size]).fetchall()
        return {'items':[dict(row) for row in rows],'total':total,'page':page,'page_size':page_size}

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

    @staticmethod
    def _finance_row(db, app_id):
        row = db.execute("SELECT * FROM finance_applications WHERE id=?", (app_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["groups"] = json.loads(result.pop("groups_json"))
        result["amount"] = f"{result['amount_cents'] / 100:.2f}"
        result["files"] = [dict(item) for item in db.execute("SELECT * FROM finance_files WHERE application_id=? ORDER BY kind", (app_id,))]
        invoice = db.execute('SELECT * FROM finance_invoice_details WHERE application_id=?',(app_id,)).fetchone()
        result['invoice'] = ({'file_id':invoice['file_id'],'fields':json.loads(invoice['fields_json']),
            'recognition':json.loads(invoice['recognition_json']),
            'verification':{'status':invoice['verification_status'],'note':invoice['verification_note'],
                'actor_name':invoice['verified_by_name'],'created_at':invoice['verified_at'],'revision':invoice['revision']}} if invoice else None)
        return result

    @staticmethod
    def _assert_invoice_available(db, app_id, fields, digest):
        from .finance_invoices import invoice_key
        key = invoice_key(fields) if fields else ''
        duplicate = db.execute('''SELECT a.id FROM finance_applications a
            LEFT JOIN finance_invoice_details d ON d.application_id=a.id
            LEFT JOIN finance_files f ON f.application_id=a.id AND f.kind='invoice'
            WHERE a.id<>? AND a.status IN ('pending','approved')
            AND ((?<>'' AND d.invoice_key=?) OR (?<>'' AND f.sha256=?)) LIMIT 1''',
            (app_id,key,key,digest,digest)).fetchone()
        if duplicate:
            raise TestFeatureError('该发票已被待审核或已通过的申请占用，不能重复报销',409)

    def invoice_is_duplicate(self, fields, digest, *, exclude=''):
        with closing(self._connect()) as db:
            try:
                self._assert_invoice_available(db,exclude,fields,digest)
            except TestFeatureError:
                return True
        return False

    @staticmethod
    def _save_finance_invoice(db, app_id, file, fields, recognition):
        from .finance_invoices import invoice_key
        old = db.execute('SELECT file_id,fields_json FROM finance_invoice_details WHERE application_id=?',(app_id,)).fetchone()
        encoded = json.dumps(fields,ensure_ascii=False,sort_keys=True)
        if old and old['file_id'] == file['id'] and json.loads(old['fields_json']) == fields:
            return
        db.execute('''INSERT INTO finance_invoice_details(application_id,file_id,invoice_key,fields_json,recognition_json)
            VALUES(?,?,?,?,?) ON CONFLICT(application_id) DO UPDATE SET file_id=excluded.file_id,
            invoice_key=excluded.invoice_key,fields_json=excluded.fields_json,recognition_json=excluded.recognition_json,
            verification_status='unverified',verification_note='',verified_by='',verified_by_name='',verified_at=NULL,
            revision=finance_invoice_details.revision+1''',
            (app_id,file['id'],invoice_key(fields),encoded,json.dumps(recognition,ensure_ascii=False)))

    def invoice_verifications(self, app_id):
        with closing(self._connect()) as db:
            rows = db.execute('SELECT * FROM finance_invoice_verifications WHERE application_id=? ORDER BY id DESC',(app_id,)).fetchall()
        return [{key:row[key] for key in row.keys() if key not in {'fields_json','actor_id'}} | {'fields':json.loads(row['fields_json'])} for row in rows]

    def verify_finance_invoice(self, app_id, actor, status, note, *, version, revision, fields=None):
        if status not in {'verified','inconsistent','not_found','voided','unavailable'}:
            raise TestFeatureError('发票查验结果无效')
        note = self._text(note,'查验说明',MAX_FINANCE_NOTE,required=True)
        if type(version) is not int or type(revision) is not int:
            raise TestFeatureError('请提供当前申请和查验版本')
        with self._lock, closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            item = self._finance_row(db,app_id)
            if not item or item['version'] != version:
                return None
            if not item['invoice']:
                if revision != 0 or not fields:
                    raise TestFeatureError('历史申请须先补录发票信息')
                file = next((file for file in item['files'] if file['kind'] == 'invoice'),None)
                if not file:
                    raise TestFeatureError('未找到发票附件',404)
                if item['status'] in {'pending','approved'}:
                    self._assert_invoice_available(db,app_id,fields,file['sha256'])
                self._save_finance_invoice(db,app_id,file,fields,{'method':'legacy_manual','fields':{}})
                item = self._finance_row(db,app_id)
            elif fields is not None and fields != item['invoice']['fields']:
                raise TestFeatureError('发票信息已变化，请刷新后重试',409)
            if item['invoice']['verification']['revision'] != revision:
                return None
            invoice = item['invoice'];now = _now()
            db.execute('''INSERT INTO finance_invoice_verifications(application_id,version,file_id,fields_json,status,note,actor_id,actor_name,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',(app_id,version,invoice['file_id'],json.dumps(invoice['fields'],ensure_ascii=False),status,note,actor['id'],actor['username'],now))
            db.execute('''UPDATE finance_invoice_details SET verification_status=?,verification_note=?,verified_by=?,verified_by_name=?,verified_at=?,revision=revision+1
                WHERE application_id=?''',(status,note,actor['id'],actor['username'],now,app_id))
        return self.finance_application(app_id)

    def _finance_event(self, db, app_id, action, actor_id, actor_name, note="", when=None, *, supersedes_event_id=None, notify=True):
        item = self._finance_row(db, app_id)
        event_id, when = secrets.token_hex(12), when or _now()
        db.execute("INSERT OR IGNORE INTO finance_versions VALUES (?, ?, ?)",
                   (app_id, item["version"], json.dumps(item, ensure_ascii=False)))
        db.execute("INSERT INTO finance_events(id,application_id,version,action,actor_id,actor_name,note,created_at,supersedes_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (event_id, app_id, item["version"], action, actor_id, actor_name, note, when, supersedes_event_id))
        if notify:
            from .finance_notifications import record_event
            record_event(db,item,event_id,action,actor_name,note,when,re_review=bool(supersedes_event_id))

    def create_finance_application(self, *, user_id: str, username: str, user_note: str,
                                   groups: list[str], project: str, amount_cents: int,
                                   files: list[dict[str, Any]], expense_group_id: str = "",
                                   expense_group_name: str = "未分组", invoice_fields=None, recognition=None) -> dict[str, Any]:
        app_id, now = secrets.token_hex(12), _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            invoice = next((file for file in files if file['kind'] == 'invoice'),None)
            self._assert_invoice_available(db,app_id,invoice_fields,invoice['sha256'] if invoice else '')
            db.execute(
                """INSERT INTO finance_applications(id,user_id,username,user_note,groups_json,project,amount_cents,status,created_at,updated_at,review_note,expense_group_id,expense_group_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, '', ?, ?)""",
                (app_id, user_id, username, user_note, json.dumps(groups, ensure_ascii=False), project, amount_cents, now, now, expense_group_id, expense_group_name),
            )
            for item in files:
                self._insert_finance_file(db, app_id, item["kind"], item)
            if invoice_fields and invoice:
                self._save_finance_invoice(db,app_id,invoice,invoice_fields,recognition or {})
            self._finance_event(db, app_id, "submitted", user_id, username)
        return self.finance_application(app_id)

    @staticmethod
    def _insert_finance_file(db, app_id, kind, item):
        db.execute("INSERT INTO finance_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (item["id"], app_id, kind, item["stored_name"], item["original_name"], item["content_type"], item["size"], item["sha256"]))

    def finance_application(self, app_id: str) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as db:
            return self._finance_row(db, app_id)

    def finance_history(self, app_id: Optional[str] = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute("""SELECT e.*, v.snapshot_json, prior.action AS previous_status,
                                 (e.action IN ('approved','rejected') AND a.status=e.action AND a.version=e.version
                                  AND e.rowid=(SELECT MAX(last.rowid) FROM finance_events last WHERE last.application_id=e.application_id)) AS is_current_review
                                 FROM finance_events e JOIN finance_versions v
                                 ON e.application_id=v.application_id AND e.version=v.version
                                 JOIN finance_applications a ON a.id=e.application_id
                                 LEFT JOIN finance_events prior ON prior.id=e.supersedes_event_id"""
                              + (" WHERE e.application_id=?" if app_id else "") + " ORDER BY e.created_at DESC, e.rowid DESC",
                              (app_id,) if app_id else ()).fetchall()
        return [{key: row[key] for key in row.keys() if key not in {"snapshot_json","is_current_review"}}
                | {"is_current_review":bool(row['is_current_review']), "snapshot": json.loads(row["snapshot_json"])} for row in rows]

    def list_finance_applications(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            rows = db.execute("SELECT id FROM finance_applications" + (" WHERE user_id=?" if user_id else "") + " ORDER BY created_at DESC, rowid DESC", (user_id,) if user_id else ()).fetchall()
            return [self._finance_row(db, row["id"]) for row in rows]

    def review_finance_application(self, app_id: str, status: str, reviewer_id: str, reviewer_name: str, note: str,
                                   expected_version: Optional[int] = None) -> Optional[dict[str, Any]]:
        if status not in {"approved", "rejected"}:
            raise TestFeatureError("审核状态无效")
        note = self._text(note, "审核备注", MAX_FINANCE_NOTE, required=status == "rejected")
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self._finance_row(db, app_id)
            if not row or row["status"] != "pending" or (expected_version is not None and row["version"] != expected_version):
                return None
            if status == 'approved':
                invoice = next((file for file in row['files'] if file['kind'] == 'invoice'),None)
                self._assert_invoice_available(db,app_id,row['invoice']['fields'] if row['invoice'] else None,invoice['sha256'] if invoice else '')
            db.execute("UPDATE finance_applications SET status=?,updated_at=?,reviewed_at=?,reviewer_id=?,reviewer_name=?,review_note=? WHERE id=?",
                       (status, _now(), _now(), reviewer_id, reviewer_name, note, app_id))
            self._finance_event(db, app_id, status, reviewer_id, reviewer_name, note)
        return self.finance_application(app_id)

    def re_review_finance_application(self, app_id, status, reviewer_id, reviewer_name, note, *, expected_version, event_id):
        if status not in {'approved','rejected'}:
            raise TestFeatureError('审核状态无效')
        note = self._text(note,'改审原因',MAX_FINANCE_NOTE,required=True)
        if type(expected_version) is not int or expected_version < 1 or not isinstance(event_id,str) or not event_id:
            raise TestFeatureError('请提供当前资料版本及审批记录')
        with self._lock, closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            item = self._finance_row(db,app_id)
            latest = db.execute('SELECT id,action,version FROM finance_events WHERE application_id=? ORDER BY rowid DESC LIMIT 1',(app_id,)).fetchone()
            if (not item or item['status'] not in {'approved','rejected'} or item['version'] != expected_version
                    or not latest or latest['id'] != event_id or latest['version'] != expected_version or latest['action'] != item['status']):
                return None
            if item['status'] == status:
                raise TestFeatureError('请选择不同的审核结果')
            if status == 'approved':
                invoice = next((file for file in item['files'] if file['kind'] == 'invoice'),None)
                self._assert_invoice_available(db,app_id,item['invoice']['fields'] if item['invoice'] else None,invoice['sha256'] if invoice else '')
            now = _now()
            db.execute('UPDATE finance_applications SET status=?,updated_at=?,reviewed_at=?,reviewer_id=?,reviewer_name=?,review_note=? WHERE id=?',
                       (status,now,now,reviewer_id,reviewer_name,note,app_id))
            self._finance_event(db,app_id,status,reviewer_id,reviewer_name,note,supersedes_event_id=event_id)
        return self.finance_application(app_id)

    def update_finance_application(self, app_id: str, user_id: str, project: str,
                                   amount_cents: int, replacements: dict[str, dict[str, Any]], *,
                                   expense_group: Optional[tuple[str, str]] = None,
                                   expected_version: Optional[int] = None, invoice_fields=None, recognition=None) -> Optional[dict[str, Any]]:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self._finance_row(db, app_id)
            if (not row or row["user_id"] != user_id or row["status"] not in {"pending", "rejected"}
                    or (expected_version is not None and row["version"] != expected_version)):
                return None
            invoice = replacements.get('invoice') or next((file for file in row['files'] if file['kind'] == 'invoice'),None)
            fields = invoice_fields or (row['invoice']['fields'] if row['invoice'] else None)
            self._assert_invoice_available(db,app_id,fields,invoice['sha256'] if invoice else '')
            group_id, group_name = expense_group or (row["expense_group_id"], row["expense_group_name"])
            db.execute("""UPDATE finance_applications SET project=?,amount_cents=?,updated_at=?,version=version+1,
                          status='pending',reviewed_at=NULL,reviewer_id=NULL,reviewer_name=NULL,review_note='',
                          expense_group_id=?,expense_group_name=? WHERE id=?""",
                       (project, amount_cents, _now(), group_id, group_name, app_id))
            for kind, item in replacements.items():
                db.execute("DELETE FROM finance_files WHERE application_id=? AND kind=?", (app_id, kind))
                self._insert_finance_file(db, app_id, kind, item)
            if fields and invoice:
                self._save_finance_invoice(db,app_id,invoice,fields,recognition if recognition is not None else (row['invoice']['recognition'] if row['invoice'] else {}))
            # Physical files referenced by older versions remain available for audit/download.
            self._finance_event(db, app_id, "resubmitted" if row["status"] == "rejected" else "edited", user_id, row["username"])
        return self.finance_application(app_id)

    def cancel_finance_application(self, app_id: str, user_id: str, expected_version: Optional[int] = None) -> Optional[dict[str, Any]]:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = self._finance_row(db, app_id)
            if (not row or row["user_id"] != user_id or row["status"] != "pending"
                    or (expected_version is not None and row["version"] != expected_version)):
                return None
            db.execute("UPDATE finance_applications SET status='cancelled',updated_at=? WHERE id=?", (_now(), app_id))
            self._finance_event(db, app_id, "cancelled", user_id, row["username"])
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
    if not amount.is_finite():
        raise TestFeatureError("报销金额格式无效")
    cents = int(amount * 100)
    if cents <= 0 or cents > MAX_FINANCE_AMOUNT_CENTS:
        raise TestFeatureError("报销金额须大于 0 且不超过 1000000.00")
    return cents


def _usage_from_payload(payload: Any) -> tuple[int, int, int]:
    if isinstance(payload, dict) and isinstance(payload.get('response'),dict):
        payload = payload['response']
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0
    try:
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        if max(prompt,completion,total)>2**63-1:
            return 0,0,0
    except (ValueError,TypeError,OverflowError):
        return 0,0,0
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
                payload = json.loads(value)
                source = payload.get('response',payload) if isinstance(payload,dict) else {}
                if isinstance(source,dict) and isinstance(source.get('usage'),dict):
                    self.usage = _usage_from_payload(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                continue
        if len(self.buffer)>1024*1024:
            self.buffer = b''


def install_test_features(app, static_dir: Path, data_dir: str, keyring: Optional[CredentialKeyring]):
    store = TestFeatureStore(data_dir, keyring)
    from .newapi_gateway import NewAPIGateway, NewAPIError
    gateway = NewAPIGateway(store)
    app.state.newapi_gateway = gateway

    @app.exception_handler(NewAPIError)
    async def newapi_failure(request, error):
        return JSONResponse({'detail':str(error)},status_code=error.status_code)

    @app.exception_handler(TestFeatureError)
    async def feature_failure(request, error):
        return JSONResponse({'detail':str(error)},status_code=error.status_code)
    from .site_announcements import install_site_announcements
    install_site_announcements(app, store)
    from .finance_notifications import install_finance_notifications
    install_finance_notifications(app, store)
    router = APIRouter()
    client_holder: dict[str, Optional[httpx.AsyncClient]] = {"client": None}
    invoice_limiter = anyio.CapacityLimiter(1)

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

    async def gateway_payload(request):
        body = await request.body()
        if len(body)>65536:
            raise HTTPException(413,'请求内容过长')
        try:
            payload = json.loads(body)
        except (ValueError,UnicodeDecodeError,RecursionError):
            raise HTTPException(400,'请求必须是 JSON 对象') from None
        if not isinstance(payload,dict):
            raise HTTPException(400,'请求必须是 JSON 对象')
        return payload

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

    def expense_group(request: Request, selected: Optional[str], existing=None):
        groups = request.app.state.site_accounts.user_groups(current_user(request)["id"])
        if existing and (selected is None or selected == existing["expense_group_id"]):
            return existing["expense_group_id"], existing["expense_group_name"]
        if not groups:
            if selected:
                raise HTTPException(400, "费用归属必须是当前账户所属分组")
            return "", "未分组"
        if len(groups) == 1 and not selected:
            return groups[0]["id"], groups[0]["name"]
        group = next((group for group in groups if group["id"] == selected), None)
        if not group:
            raise HTTPException(400, "请选择一个所属分组作为费用归属")
        return group["id"], group["name"]

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

    async def recognize_invoice(file):
        from .finance_invoices import recognize_file
        return await anyio.to_thread.run_sync(lambda: recognize_file(store.files / file['stored_name']), limiter=invoice_limiter)

    async def prepare_invoice(saved, raw_fields, existing=None):
        from .finance_invoices import confirmed_fields
        replacement = next((file for file in saved if file['kind'] == 'invoice'),None)
        file = replacement or next((file for file in (existing or {}).get('files',[]) if file['kind'] == 'invoice'),None)
        if not file:
            raise TestFeatureError('未找到发票附件',404)
        old = (existing or {}).get('invoice')
        recognition = await recognize_invoice(file) if replacement or not old else old['recognition']
        if recognition.get('multiple'):
            raise TestFeatureError('一笔申请仅支持一张发票，请拆分文件后提交')
        try:
            if raw_fields is not None:
                if len(raw_fields) > 4096:
                    raise ValueError('发票信息过长')
                fields = json.loads(raw_fields)
            else:
                fields = old['fields'] if old and not replacement else recognition['fields']
            return confirmed_fields(fields), recognition
        except (ValueError,TypeError) as error:
            raise TestFeatureError(str(error) if not isinstance(error,json.JSONDecodeError) else '发票信息格式无效') from None

    @router.get("/test-features")
    def test_features_page(request: Request):
        page = (static_dir / "test-features.html").read_text(encoding="utf-8")
        from .accounts import themed_page
        return HTMLResponse(themed_page(page, request), headers={"Cache-Control": "no-store, private"})

    @router.get("/api/test-features/status")
    def feature_status(request: Request):
        user = current_user(request)
        config = store.api2api_config_public()
        return {"role": user["role"], "api2api": {key:config.get(key) for key in ('engine','configured','enabled')}}

    @router.get("/api/test-ui/views/{name}.js")
    def workspace_controller(name: str, request: Request):
        from fastapi.responses import Response
        from .test_navigation import PAGES, page_controller
        user = current_user(request)
        if name not in {*PAGES, "editor"}:
            raise HTTPException(404, "页面不存在")
        if (name in {"admin", "editor"} and user["role"] != "admin") or (
            name == "access" and user["role"] not in ADMIN_ROLES
        ):
            raise HTTPException(403, "需要管理员权限")
        return Response(page_controller(static_dir, name), media_type="application/javascript",
                        headers={"Cache-Control": "no-store, private"})

    @router.get("/api/test-ui/preferences")
    def interface_preferences(request: Request):
        return request.app.state.site_accounts.interface_preferences(current_user(request)["id"])

    @router.put("/api/test-ui/preferences")
    async def save_interface_preferences(request: Request):
        from .accounts import read_payload
        from .site_accounts import INTERFACE_OPTIONS
        payload = await read_payload(request, set(INTERFACE_OPTIONS))
        return request.app.state.site_accounts.set_interface_preferences(current_user(request)["id"], payload)

    async def audited_key_change(user, action, key, operation):
        event_id = store.begin_api_audit(user,action,key)
        try:
            result = await operation()
        except Exception as error:
            outcome = 'failed' if getattr(error,'status_code',500)<500 else 'uncertain'
            store.finish_api_audit(event_id,outcome)
            raise
        store.finish_api_audit(event_id,'succeeded',result if action=='created' else None)
        return result

    @router.get('/api/admin/api2api/audit')
    def api_audit(request: Request, page: int=1, page_size: int=20, q: str='', action: str='', outcome: str='', start: str='', end: str=''):
        require_admin(request)
        if not 1<=page<=1_000_000_000 or page_size not in {20,50,100} or len(q)>120:
            raise HTTPException(400,'日志分页或搜索条件无效')
        if action not in {'','created','revealed','enabled','disabled','updated','revoked'} or outcome not in {'','pending','succeeded','failed','uncertain'}:
            raise HTTPException(400,'日志筛选条件无效')
        dates = []
        for value in (start,end):
            if value:
                try:
                    if datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise HTTPException(400,'日志日期须包含时区') from None
                value = datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
            dates.append(value)
        if all(dates) and dates[0]>dates[1]:
            raise HTTPException(400,'开始日期不能晚于结束日期')
        data = store.api_audit_page(page=page,page_size=page_size,query=q,action=action,outcome=outcome,start=dates[0],end=dates[1])
        for row in data['items']:
            owner = request.app.state.site_accounts.get_user(row['owner_id']) if row['owner_id'] else None
            row['owner_name'] = owner['username'] if owner else row['owner_id']
        return JSONResponse(data,headers={'Cache-Control':'no-store, private'})

    @router.get("/api/api2api/keys")
    async def list_user_keys(request: Request):
        user = current_user(request)
        if store.api2api_config_public().get('engine') == 'newapi':
            return {'items':await gateway.list_keys(user['id'])}
        rows = store.list_api_keys(user['id'])
        return {'items':[{**row,'available_here':False,
                          'revocable':not row['revoked'], 'can_copy':False,
                          'can_toggle':False, 'enabled':False} for row in rows]}

    @router.post("/api/api2api/keys", status_code=201)
    async def create_user_key(request: Request):
        user = current_user(request)
        payload = await gateway_payload(request)
        if set(payload)-{'name','models','expired_at','allowed_ips','unlimited_quota','remaining_quota','allow_user_quota_query'}:
            raise HTTPException(400,'密钥限制字段无效')
        name = store._text(payload.get('name'),'API Key 名称',80,required=True)
        item = await audited_key_change(user,'created',{'name':name},lambda:gateway.create_key(user['id'],name,payload))
        return JSONResponse({'item':item},status_code=201,headers={'Cache-Control':'no-store, private','Pragma':'no-cache'})

    @router.post('/api/api2api/keys/{key_id}/reveal')
    async def reveal_user_key(key_id: str, request: Request):
        user = current_user(request)
        key = store.gateway_key(key_id,user['id']) or {'id':key_id}
        raw = await audited_key_change(user,'revealed',key,lambda:gateway.reveal_key(key_id,user['id']))
        return JSONResponse({'api_key':raw},headers={'Cache-Control':'no-store, private','Pragma':'no-cache'})

    @router.put('/api/api2api/keys/{key_id}/enabled')
    async def set_user_key_enabled(key_id: str, request: Request):
        user = current_user(request)
        payload = await gateway_payload(request)
        gateway.config()
        key = store.gateway_key(key_id,user['id']) or {'id':key_id}
        await audited_key_change(user,'enabled' if payload.get('enabled') is True else 'disabled',key,
                                 lambda:gateway.set_key_enabled(key_id,user['id'],payload.get('enabled')))
        return {'ok':True}

    @router.put('/api/api2api/keys/{key_id}/limits')
    async def update_user_key_limits(key_id: str, request: Request):
        user = current_user(request)
        payload = await gateway_payload(request)
        if set(payload)-{'models','expired_at','revision','allowed_ips','name','unlimited_quota',
                         'remaining_quota','expected_remaining_quota','allow_user_quota_query'}:
            raise HTTPException(400,'密钥限制字段无效')
        key = store.gateway_key(key_id,user['id']) or {'id':key_id}
        return await audited_key_change(user,'updated',key,lambda:gateway.update_key_limits(key_id,user['id'],payload))

    @router.delete("/api/api2api/keys/{key_id}")
    async def revoke_user_key(key_id: str, request: Request):
        user = current_user(request)
        key = store.gateway_key(key_id,user['id']) or {'id':key_id}
        await audited_key_change(user,'revoked',key,lambda:gateway.revoke_key(key_id,user['id']))
        return {"ok": True}

    @router.get("/api/api2api/usage")
    def user_api_usage(request: Request):
        user = current_user(request)
        return store.usage_summary(user["id"])

    @router.post('/api/api2api/models')
    async def available_api_models(request: Request):
        return JSONResponse(await gateway.models(current_user(request)['id']),headers={'Cache-Control':'no-store, private'})

    @router.get('/api/api2api/models/performance')
    async def api_model_performance(request: Request, model: str=Query(...,min_length=1,max_length=120)):
        return JSONResponse(await gateway.model_performance(current_user(request)['id'],model),headers={'Cache-Control':'no-store, private'})

    def api_log_filters(request, page, page_size, start_timestamp, end_timestamp, log_type, *, export=False):
        allowed = {'page','page_size','start_timestamp','end_timestamp','type','model_name','key_id','group','request_id','upstream_request_id'}
        if export:
            allowed.add('format')
        if set(request.query_params)-allowed or not 1<=page<=1_000_000_000 or page_size not in {20,50,100}:
            raise HTTPException(400,'日志分页或搜索条件无效')
        if start_timestamp<0 or end_timestamp<0 or (end_timestamp and start_timestamp>end_timestamp) or log_type not in range(8):
            raise HTTPException(400,'日志日期或类型无效')
        filters = {'start_timestamp':start_timestamp,'end_timestamp':end_timestamp,'type':log_type}
        for name,limit in (('model_name',120),('key_id',64),('group',64),('request_id',64),('upstream_request_id',128)):
            value = request.query_params.get(name,'').strip()
            if len(value)>limit:
                raise HTTPException(400,'日志搜索条件过长')
            if value:
                filters[name] = value
        return filters

    @router.get('/api/api2api/logs')
    async def user_api_logs(request: Request, page: int=1, page_size: int=100, start_timestamp: int=0,
                            end_timestamp: int=0, type: int=0):
        user = current_user(request)
        filters = api_log_filters(request,page,page_size,start_timestamp,end_timestamp,type)
        return JSONResponse(await gateway.log_page(user['id'],filters,page,page_size),headers={'Cache-Control':'no-store, private'})

    @router.get('/api/api2api/logs/export')
    async def export_api_logs(request: Request, start_timestamp: int=0, end_timestamp: int=0, log_type: int=Query(0,alias='type'), format: str='csv'):
        user = current_user(request)
        filters = api_log_filters(request,1,100,start_timestamp,end_timestamp,log_type,export=True)
        if format not in {'csv','jsonl'}:
            raise HTTPException(400,'导出格式无效')
        filters['end_timestamp'] = min(end_timestamp or int(time.time())-1,int(time.time())-1)
        if start_timestamp>filters['end_timestamp']:
            raise HTTPException(400,'开始日期不能晚于结束日期')
        lock = gateway.locks.setdefault(('log-export',user['id']),asyncio.Lock())
        if lock.locked():
            raise HTTPException(409,'已有日志导出正在进行，请稍后重试')
        path, handed_off = None, False
        async with lock:
            try:
                directory = store.root/'api_log_exports'
                directory.mkdir(mode=0o700,exist_ok=True)
                with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8-sig' if format=='csv' else 'utf-8',
                        newline='',prefix='usage-',suffix='.'+format,dir=directory,delete=False) as file:
                    path = Path(file.name)
                    os.chmod(path,0o600)
                    columns = ['id','created_at','type','token_name','model_name','is_stream','prompt_tokens',
                               'completion_tokens','quota','charged_amount','currency','use_time','request_id','upstream_request_id','billing']
                    writer = csv.writer(file) if format=='csv' else None
                    if writer:
                        writer.writerow(columns)
                    page, count, expected, previous, display = 1, 0, None, None, None
                    while True:
                        if await request.is_disconnected():
                            raise HTTPException(499,'导出已取消')
                        data = await gateway.log_page(user['id'],filters,page,100,include_summary=page==1)
                        if expected is None:
                            expected = data['total']
                            display = data['display']
                        if data['total']!=expected or (not data['items'] and count<expected):
                            raise HTTPException(409,'导出期间日志发生变化，请重试')
                        for row in data['items']:
                            if type(row.get('id')) is not int or (previous is not None and row['id']>=previous):
                                raise HTTPException(409,'日志顺序已变化，请重试导出')
                            previous = row['id']
                            amount = Decimal(str(row['quota']))/Decimal(str(display['divisor'])) if type(row.get('quota')) in {int,float} else None
                            row = {**row,'charged_amount':str(amount) if amount is not None else None,
                                   'currency':display['symbol'] if display['unit']=='currency' else 'quota'}
                            if writer:
                                values = [json.dumps(row[name],ensure_ascii=False) if isinstance(row.get(name),dict) else row.get(name,'') for name in columns]
                                writer.writerow(["'"+value if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) else value for value in values])
                            else:
                                file.write(json.dumps(row,ensure_ascii=False)+'\n')
                            count += 1
                        if count>expected:
                            raise HTTPException(409,'导出期间日志数量发生变化，请重试')
                        if count>=expected:
                            break
                        page += 1
                response = FileResponse(path,filename='api-usage-logs.'+format,
                    media_type='text/csv' if format=='csv' else 'application/x-ndjson',
                    headers={'Cache-Control':'no-store, private','X-Export-Count':str(count)},
                    background=BackgroundTask(path.unlink,missing_ok=True))
                handed_off = True
                return response
            finally:
                if path and not handed_off:
                    path.unlink(missing_ok=True)

    @router.get('/api/api2api/engine')
    async def user_engine_info(request: Request):
        user = current_user(request)
        if store.api2api_config_public().get('engine') != 'newapi':
            return {'engine':'newapi','linked':False,'models':[]}
        return await gateway.info(user['id'])

    @router.get("/api/admin/api2api/config")
    def admin_api_config(request: Request):
        require_admin(request)
        return store.api2api_config_public()

    @router.put("/api/admin/api2api/config")
    async def save_api_config(request: Request):
        require_admin(request)
        try:
            payload = await gateway_payload(request)
            if payload.get('engine', 'newapi') != 'newapi':
                raise TestFeatureError('本站固定使用网关原生密钥，不支持切换转发模式')
            payload['engine'] = 'newapi'
            if urlsplit(str(payload.get('base_url',''))).netloc == request.url.netloc:
                raise TestFeatureError('网关不能指向网站自身')
            return store.configure_api2api(payload)
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error

    @router.post('/api/admin/api2api/verify')
    async def verify_api_engine(request: Request):
        require_admin(request)
        return await gateway.verify()

    @router.get('/api/admin/api2api/channels')
    async def list_api_channels(request: Request):
        require_admin(request)
        return {'items':await gateway.channels()}

    @router.post('/api/admin/api2api/channels',status_code=201)
    async def add_api_channel(request: Request):
        require_admin(request)
        try:
            payload = await gateway_payload(request)
            target = urlsplit(str(payload.get('base_url','')))
            if target.netloc == request.url.netloc:
                raise TestFeatureError('上游不能指向网站自身')
            return await gateway.add_channel(payload)
        except TestFeatureError as error:
            raise HTTPException(error.status_code,str(error)) from error

    @router.put('/api/admin/api2api/users/{user_id}/quota')
    async def change_api_quota(user_id: str, request: Request):
        require_admin(request)
        if not request.app.state.site_accounts.get_user(user_id):
            raise HTTPException(404,'未找到网站账户')
        payload = await gateway_payload(request)
        return await gateway.set_quota(user_id,payload.get('quota'))

    @router.get("/api/admin/api2api/keys")
    def admin_api_keys(request: Request):
        require_admin(request)
        return {"items": store.list_api_keys()}

    @router.delete("/api/admin/api2api/keys/{key_id}")
    async def admin_revoke_api_key(key_id: str, request: Request):
        user = require_admin(request)
        key = store.gateway_key(key_id) or {'id':key_id}
        await audited_key_change(user,'revoked',key,lambda:gateway.revoke_key(key_id))
        return {"ok": True}

    @router.get("/api/admin/api2api/usage")
    def admin_api_usage(request: Request):
        require_admin(request)
        rows = {row['user_id']:row for row in store.usage_by_user()}
        for key in store.list_api_keys():
            rows.setdefault(key['user_id'],{'user_id':key['user_id'],'requests':0,'prompt_tokens':0,'completion_tokens':0,'total_tokens':0})
        for row in rows.values():
            account = request.app.state.site_accounts.get_user(row['user_id'],include_note=True)
            row['username'] = account['username'] if account else '已删除账户'
            row['user_note'] = account.get('note','') if account else ''
        return {"items": list(rows.values())}

    @router.api_route("/api2api/v1/{proxy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def api2api_proxy(proxy_path: str, request: Request):
        if request.method == "OPTIONS":
            return Response(status_code=204)
        authorization = request.headers.get("authorization", "")
        raw_key = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
        key = store.authenticate_api_key(raw_key)
        if not key:
            return JSONResponse({"error": {"message": "Invalid API key", "type": "authentication_error"}}, status_code=401)
        owner = request.app.state.site_accounts.get_user(key['user_id'],include_status=True)
        if not owner or owner.get('status','active') != 'active':
            return JSONResponse({'error':{'message':'API owner is unavailable','type':'authentication_error'}},status_code=401)
        config = store.api2api_config_public()
        if not config.get('configured') or not config['enabled'] or config.get('engine') != 'newapi':
            return JSONResponse({"error": {"message": "API2API is not configured", "type": "configuration_error"}}, status_code=503)
        if key['provider'] != 'newapi' or key['engine_origin'] != config['base_url'][:-3]:
            return JSONResponse({'error':{'message':'API key does not belong to the active gateway','type':'authentication_error'}},status_code=401)
        # request.client is the socket peer, or the address accepted by the
        # existing trusted-proxy boundary. Never read caller-supplied IP headers.
        if not store.api_key_ip_allowed(key,request.client.host if request.client else None):
            return JSONResponse({'error':{'message':'This API key is not allowed from your IP address',
                'type':'permission_error','code':'ip_not_allowed'}},status_code=403)
        if '\\' in proxy_path or any(part in {'.','..'} for part in proxy_path.split('/')):
            return JSONResponse({'error':{'message':'Invalid API path','type':'invalid_request_error'}},status_code=400)
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
                timeout=httpx.Timeout(connect=5,read=None,write=30,pool=5), follow_redirects=False, trust_env=False,
            )
        if proxy_path.strip('/')=='usage/token':
            if request.method!='GET':
                return Response(status_code=405,headers={'Allow':'GET'})
            try:
                upstream = await client_holder['client'].get(config['base_url'][:-3]+'/api/usage/token/',
                    headers={'Authorization':f'Bearer {raw_key}'},timeout=15)
                return Response(upstream.content,status_code=upstream.status_code,
                    media_type=upstream.headers.get('content-type','application/json'),
                    headers={'Cache-Control':'no-store, private'})
            except httpx.HTTPError:
                return JSONResponse({'error':{'message':'Upstream API unavailable','type':'upstream_error'}},status_code=502)
        target = config["base_url"].rstrip("/") + "/" + proxy_path.lstrip("/")
        headers = {"Authorization": f"Bearer {raw_key}", "Accept": request.headers.get("accept", "*/*"), "Accept-Encoding": "identity"}
        if request.headers.get("content-type"):
            headers["Content-Type"] = request.headers["content-type"]
        for name in ('openai-beta','openai-project','openai-organization','idempotency-key','x-client-request-id'):
            if name in request.headers:
                headers[name] = request.headers[name]
        started = time.perf_counter()
        try:
            upstream_request = client_holder["client"].build_request(request.method, target, params=list(request.query_params.multi_items()), headers=headers, content=body)
            upstream = await client_holder["client"].send(upstream_request, stream=True)
            response_type = upstream.headers.get("content-type", "")
            response_headers = {name: value for name, value in upstream.headers.items() if name.lower() in {"content-type", "cache-control",'retry-after','x-request-id'}}
            if "text/event-stream" in response_type:
                response_headers.update({'X-Accel-Buffering':'no','Cache-Control':'no-cache'})
                collector = _StreamUsage()

                async def stream_body():
                    stream_error = ''
                    try:
                        async for chunk in upstream.aiter_bytes():
                            collector.feed(chunk)
                            yield chunk
                    except httpx.HTTPError as error:
                        stream_error = type(error).__name__
                        raise
                    except asyncio.CancelledError:
                        stream_error = 'stream_cancelled'
                        raise
                    finally:
                        with anyio.CancelScope(shield=True):
                            await upstream.aclose()
                            collector.feed(b'\n')
                            prompt, completion, total = collector.usage
                            store.record_usage(key_id=key["id"], user_id=key["user_id"], model=model,
                                               prompt_tokens=prompt, completion_tokens=completion, total_tokens=total,
                                               status_code=upstream.status_code, request_ms=int((time.perf_counter() - started) * 1000),
                                               error=stream_error or ("" if upstream.status_code < 400 else "upstream_error"))
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

    @router.get("/api/finance/groups")
    def finance_groups(request: Request):
        return {"items": request.app.state.site_accounts.user_groups(current_user(request)["id"])}

    @router.post('/api/finance/invoices/analyze')
    async def analyze_finance_invoice(request: Request, invoice: Optional[UploadFile] = File(None), app_id: Optional[str] = Form(None)):
        from .finance_invoices import confirmed_fields
        current_user(request)
        existing = store.finance_application(app_id) if app_id else None
        if app_id:
            if not existing:
                raise HTTPException(404,'未找到报销申请')
            public_application(request,existing)
        saved = []
        try:
            if invoice:
                saved = await save_finance_uploads([('invoice',invoice)])
                file = saved[0]
            else:
                file = next((file for file in (existing or {}).get('files',[]) if file['kind'] == 'invoice'),None)
                if not file:
                    raise HTTPException(400,'请选择发票文件')
            recognition = await recognize_invoice(file)
            try:
                fields = confirmed_fields(recognition['fields'])
            except ValueError:
                fields = None
            return {'recognition':recognition,'duplicate':store.invoice_is_duplicate(fields,file['sha256'],exclude=app_id or '')}
        finally:
            for file in saved:
                (store.files / file['stored_name']).unlink(missing_ok=True)

    @router.post('/api/finance/reports/export')
    async def export_finance_report(request: Request):
        from .finance_reports import filter_rows, report_rows, write_report
        user = current_user(request)
        payload = await gateway_payload(request)
        report, format, scope = payload.get('report'), payload.get('format'), payload.get('scope', 'mine')
        if (set(payload) - {'report','format','scope','filters'} or report not in {'applications','history','groups'}
                or format not in {'csv','xlsx'} or scope not in {'mine','all'}):
            raise HTTPException(400, '报表请求无效')
        if scope == 'all' or report != 'applications':
            require_admin(request)
        if report == 'history':
            rows = [public_event(request, event) for event in store.finance_history() if event['action'] in {'approved','rejected'}]
        else:
            rows = [hydrate_application(request, item) for item in store.list_finance_applications(user['id'] if scope == 'mine' else None)]
        try:
            selected = filter_rows(rows, payload.get('filters', {}), history=report == 'history')
            title, headers, rows = report_rows(selected, report)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        directory = store.root / 'finance_exports'
        directory.mkdir(mode=0o700, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix='report-', suffix='.'+format, dir=directory)
        os.close(descriptor)
        path = Path(filename)
        try:
            await anyio.to_thread.run_sync(lambda: write_report(path, format, title, headers, rows))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        media_type = 'text/csv' if format == 'csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        async def report_body():
            try:
                async with await anyio.open_file(path,'rb') as output:
                    while chunk := await output.read(65536):
                        yield chunk
            finally:
                path.unlink(missing_ok=True)
        return StreamingResponse(report_body(),media_type=media_type,
            headers={'Cache-Control':'no-store, private','X-Export-Count':str(len(rows)),
                     'Content-Disposition':_content_disposition(title+'.'+format),'Content-Length':str(path.stat().st_size)},
            background=BackgroundTask(path.unlink, missing_ok=True))

    @router.post("/api/finance/applications", status_code=201)
    async def create_finance(request: Request, project: str = Form(...), amount: str = Form(...), invoice: UploadFile = File(...), payment_record: Optional[UploadFile] = File(None), expense_group_id: Optional[str] = Form(None), invoice_fields: Optional[str] = Form(None)):
        account, groups = user_snapshot(request)
        group_id, group_name = expense_group(request, expense_group_id)
        try:
            project = store._text(project, "报销项目", MAX_FINANCE_PROJECT, required=True)
            amount_cents = _parse_amount(amount)
            uploads = [("invoice", invoice)] + ([('payment', payment_record)] if payment_record else [])
            saved = await save_finance_uploads(uploads)
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        try:
            fields, recognition = await prepare_invoice(saved,invoice_fields)
            item = store.create_finance_application(user_id=account["id"], username=account["username"],
                                                    user_note=account.get("note", ""), groups=groups,
                                                    project=project, amount_cents=amount_cents, files=saved,
                                                    expense_group_id=group_id, expense_group_name=group_name, invoice_fields=fields, recognition=recognition)
        except Exception:
            for entry in saved:
                (store.files / entry["stored_name"]).unlink(missing_ok=True)
            raise
        return {"item": public_application(request, item)}

    @router.put("/api/finance/applications/{app_id}")
    async def update_finance(app_id: str, request: Request, project: str = Form(...), amount: str = Form(...), invoice: Optional[UploadFile] = File(None), payment_record: Optional[UploadFile] = File(None), expense_group_id: Optional[str] = Form(None), version: Optional[int] = Form(None), invoice_fields: Optional[str] = Form(None)):
        user = current_user(request)
        try:
            project = store._text(project, "报销项目", MAX_FINANCE_PROJECT, required=True)
            amount_cents = _parse_amount(amount)
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        existing = store.finance_application(app_id)
        if not existing or existing["user_id"] != user["id"]:
            raise HTTPException(404, "未找到报销申请")
        if existing["status"] not in {"pending", "rejected"}:
            raise HTTPException(409, "只有待审核或已驳回申请可以修改")
        group = expense_group(request, expense_group_id, existing)
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
            fields, recognition = await prepare_invoice(saved,invoice_fields,existing)
            updated = store.update_finance_application(app_id, user["id"], project, amount_cents, {item["kind"]: item for item in saved},
                                                       expense_group=group, expected_version=version if version is not None else existing["version"],invoice_fields=fields,recognition=recognition)
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
    def cancel_finance(app_id: str, request: Request, version: Optional[int] = None):
        user = current_user(request)
        item = store.cancel_finance_application(app_id, user["id"], expected_version=version)
        if not item:
            raise HTTPException(409, "只有本人提交的待审核申请可以撤销")
        return {"item": public_application(request, item)}

    @router.get("/api/admin/finance/applications")
    def admin_finance(request: Request):
        require_admin(request)
        return {"items": [public_application(request, item) for item in store.list_finance_applications()]}

    def public_event(request, event):
        return {key: value for key, value in event.items() if key != "snapshot"} | {"snapshot": public_application(request, event["snapshot"])}

    @router.get("/api/finance/applications/{app_id}/history")
    def application_history(app_id: str, request: Request):
        get_allowed_application(request, app_id)
        return {"items": [public_event(request, event) for event in store.finance_history(app_id)]}

    @router.get('/api/finance/applications/{app_id}/invoice')
    def finance_invoice_detail(app_id: str, request: Request):
        item = store.finance_application(app_id)
        if not item:
            raise HTTPException(404,'未找到报销申请')
        public = public_application(request,item)
        return {'item':public,'checks':store.invoice_verifications(app_id)}

    @router.post('/api/admin/finance/applications/{app_id}/invoice/verify')
    async def verify_finance_invoice(app_id: str, request: Request):
        from .finance_invoices import confirmed_fields
        actor = require_admin(request)
        payload = await gateway_payload(request)
        try:
            fields = confirmed_fields(payload['fields']) if 'fields' in payload else None
        except ValueError as error:
            raise HTTPException(400,str(error)) from None
        item = store.verify_finance_invoice(app_id,actor,payload.get('status'),payload.get('note'),
            version=payload.get('version'),revision=payload.get('revision'),fields=fields)
        if not item:
            raise HTTPException(409,'申请或查验记录已变化，请刷新后重试')
        return {'item':public_application(request,item)}

    @router.get("/api/admin/finance/history")
    def approval_history(request: Request):
        require_admin(request)
        return {"items": [public_event(request, event) for event in store.finance_history() if event["action"] in {"approved", "rejected"}]}

    @router.get("/api/admin/finance/announcements")
    def admin_announcements(request: Request):
        require_admin(request)
        return {"items": store.announcements(include_disabled=True)}

    @router.post("/api/admin/finance/applications/{app_id}/review")
    async def review_finance(app_id: str, request: Request):
        reviewer = require_admin(request)
        payload = await request.json()
        try:
            item = store.review_finance_application(app_id, payload.get("status"), reviewer["id"], reviewer["username"], payload.get("note", ""), expected_version=payload.get("version"))
        except TestFeatureError as error:
            raise HTTPException(error.status_code, str(error)) from error
        if not item:
            raise HTTPException(409, "申请状态或版本已变化，请刷新后重试")
        return {"item": public_application(request, item)}

    @router.post('/api/admin/finance/applications/{app_id}/re-review')
    async def re_review_finance(app_id: str, request: Request):
        reviewer = require_admin(request)
        payload = await gateway_payload(request)
        item = store.re_review_finance_application(app_id,payload.get('status'),reviewer['id'],reviewer['username'],
                payload.get('note'),expected_version=payload.get('version'),event_id=payload.get('event_id'))
        if not item:
            raise HTTPException(409,'申请或审批记录已变化，请刷新后再审核')
        return {'item':public_application(request,item)}

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

    def get_allowed_application(request: Request, app_id: str, event_id: Optional[str] = None):
        item = store.finance_application(app_id)
        if not item:
            raise HTTPException(404, "未找到报销申请")
        public_application(request, item)
        if event_id:
            event = next((event for event in store.finance_history(app_id) if event["id"] == event_id), None)
            if not event:
                raise HTTPException(404, "未找到历史版本")
            item = event["snapshot"]
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

    def zip_applications(items: list[dict[str, Any]], *, manifest=None) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            used = set()
            if manifest is not None:
                archive.writestr("审批清单.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for item in items:
                folder = application_folder(item)
                for file_item in item["files"]:
                    path = store.files / file_item["stored_name"]
                    if not path.is_file():
                        raise HTTPException(410, "附件已丢失，下载已取消，请联系管理员")
                    filename = application_filename(item, file_item)
                    archive_name = f"{folder}/{filename}"
                    if archive_name in used:
                        archive_name = f"{folder}/{Path(filename).stem}_{item['id'][:8]}{Path(filename).suffix}"
                    used.add(archive_name)
                    archive.writestr(archive_name, path.read_bytes())
        return output.getvalue()

    @router.get("/api/finance/applications/{app_id}/download")
    def download_finance_file(app_id: str, request: Request, kind: str, event_id: Optional[str] = None):
        item = get_allowed_application(request, app_id, event_id)
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
        event_ids = payload.get("event_ids") if isinstance(payload, dict) else None
        if event_ids is not None:
            if not isinstance(event_ids, list) or not event_ids or len(event_ids) > 500 or any(not isinstance(value, str) for value in event_ids):
                raise HTTPException(400, "请选择审批通过的记录，最多 500 条")
            events = {event["id"]: event for event in store.finance_history()}
            selected = [events.get(value) for value in dict.fromkeys(event_ids)]
            if any(event is None or event["action"] != "approved" or not event['is_current_review'] for event in selected):
                raise HTTPException(400, "仅可批量下载当前有效的审批通过记录")
            items = [event["snapshot"] for event in selected]
            manifest = [{"record_id": event["id"], "application_id": event["application_id"], "version": event["version"],
                         "reviewed_at": event["created_at"], "reviewer": event["actor_name"], "note": event["note"],
                         "expense_group": event["snapshot"]["expense_group_name"], "project": event["snapshot"]["project"],
                         "amount": event["snapshot"]["amount"]} for event in selected]
            data = zip_applications(items, manifest=manifest)
            return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={"Content-Disposition": _content_disposition("审批通过附件.zip")})
        ids = payload.get("application_ids") if isinstance(payload, dict) else None
        if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(value, str) for value in ids):
            raise HTTPException(400, "请选择报销申请")
        items = [store.finance_application(value) for value in ids]
        if any(item is None for item in items):
            raise HTTPException(404, "报销申请不存在")
        if any(item["status"] != "approved" for item in items):
            raise HTTPException(400, "仅可批量下载审批通过的申请")
        items = [hydrate_application(request, item) for item in items]
        data = zip_applications(items)
        filename = datetime.now().strftime("%Y%m%d") + "报销申请.zip"
        return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers={"Content-Disposition": _content_disposition(filename)})

    app.include_router(router)

    @app.on_event("shutdown")
    async def close_test_feature_client():
        await gateway.close()
        client = client_holder["client"]
        if client is not None:
            await client.aclose()

    return store
