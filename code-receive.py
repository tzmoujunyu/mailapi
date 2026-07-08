import base64
import hashlib
import html
import http.server
import json
import os
import secrets
import string
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from fastapi import FastAPI, Form, Request as FastAPIRequest
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from typing import TypedDict


def load_dotenv_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except Exception as e:  # noqa: BLE001
        print(f".env 加载失败：{e}")


load_dotenv_file()

CHECK_INTERVAL_SECONDS = 3
MAX_POLL_RESULTS = 100
MAX_HISTORY_RECORDS = 30
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
DATA_DIR = "runtime"
HISTORY_DIR = os.path.join(DATA_DIR, "history")
HISTORY_ID_DIR = os.path.join(DATA_DIR, "history_id")
CODEX_AUTH_FILE = os.path.join(DATA_DIR, "codex_auth.json")
CODEX_AUTH_DIR = os.path.join(DATA_DIR, "codex_auth")
CODEX_ACCOUNTS_FILE = os.path.join(DATA_DIR, "codex_accounts.json")
GMAIL_AUTH_FALLBACK = os.getenv("GMAIL_AUTH_FALLBACK", "auto").strip().lower()
GMAIL_OAUTH_REDIRECT_BASE = os.getenv("GMAIL_OAUTH_REDIRECT_BASE", "http://localhost:8000").rstrip("/")
APP_BASE_URL = os.getenv("APP_BASE_URL", GMAIL_OAUTH_REDIRECT_BASE).rstrip("/")
CODEX_ISSUER = os.getenv("CODEX_ISSUER", "https://auth.openai.com").rstrip("/")
CODEX_OAUTH_REDIRECT_BASE = os.getenv("CODEX_OAUTH_REDIRECT_BASE", "http://localhost:1455").rstrip("/")
CODEX_LOGIN_ADDR = os.getenv(
    "CODEX_LOGIN_ADDR",
    urllib.parse.urlparse(CODEX_OAUTH_REDIRECT_BASE).netloc or "localhost:1455",
).strip()
CODEX_USAGE_BASE_URL = os.getenv("CODEX_USAGE_BASE_URL", "https://chatgpt.com").rstrip("/")
CODEX_CLIENT_ID = os.getenv("CODEX_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann").strip()
CODEX_OAUTH_TOKEN_URL = os.getenv("CODEX_OAUTH_TOKEN_URL", f"{CODEX_ISSUER}/oauth/token").strip()
CODEX_REFRESH_TOKEN_URL = os.getenv("CODEX_REFRESH_TOKEN_URL", CODEX_OAUTH_TOKEN_URL).strip()
CODEX_HTTP_TIMEOUT_SECONDS = int(os.getenv("CODEX_HTTP_TIMEOUT_SECONDS", "30"))
CODEX_REFRESH_INTERVAL_SECONDS = int(os.getenv("CODEX_REFRESH_INTERVAL_SECONDS", "60"))
CODEX_SUBSCRIPTION_REFRESH_INTERVAL_SECONDS = int(
    os.getenv("CODEX_SUBSCRIPTION_REFRESH_INTERVAL_SECONDS", "3600")
)
AUTH_REMINDER_EVERY = int(os.getenv("AUTH_REMINDER_EVERY", "10"))
CODEX_EXHAUSTED_USED_PERCENT = 99.9
AUTH_LOCK = threading.Lock()
HISTORY_LOCK = threading.Lock()
AUTH_SESSION_LOCK = threading.Lock()
CODEX_ACCOUNTS_LOCK = threading.Lock()
CODEX_AUTH_SESSION_LOCK = threading.Lock()
CODEX_LOGIN_SERVER_LOCK = threading.Lock()
CODEX_LOGIN_SERVER: Optional[http.server.ThreadingHTTPServer] = None
ACCESS_PASSWORD = ""
ACCESS_PASSWORD_FROM_ENV = False
ACCESS_COOKIE_NAME = "chatgpt_code_access"
SEARCH_QUERY = (
    'is:unread from:(noreply@tm.openai.com OR noreply@tm1.openai.com) '
    '(subject:(chatgpt OR ChatGPT OR 验证码 OR "verification code" OR "security code" OR "two-factor" OR verification) OR '
    '\"ChatGPT\" OR "OpenAI")'
)

CODE_PATTERNS = [
    re.compile(r"(?i)(?:验证(?:码|代码)|verification code|verification|otp|one[- ]?time|code)[^\d]{0,30}?(\d{4,8})"),
    re.compile(r"(?<!\d)(\d{6})(?!\d)"),
    re.compile(r"(?<!\d)(\d{4,5})(?!\d)"),
    re.compile(r"(?<!\d)(\d{7,8})(?!\d)"),
]


class AccountConfig(TypedDict):
    name: str
    email: str
    token_file: str
    credential_file: str


app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


def generate_access_password(length: int = 20) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def init_access_password() -> None:
    global ACCESS_PASSWORD, ACCESS_PASSWORD_FROM_ENV
    configured = os.getenv("ACCESS_PASSWORD", "").strip()
    if configured:
        ACCESS_PASSWORD = configured
        ACCESS_PASSWORD_FROM_ENV = True
    else:
        ACCESS_PASSWORD = generate_access_password()
        ACCESS_PASSWORD_FROM_ENV = False


def should_use_console_auth() -> bool:
    return GMAIL_AUTH_FALLBACK == "console"


AUTH_SESSIONS: Dict[str, Dict[str, object]] = {}


def oauth_callback_url(account_name: str) -> str:
    return f"{GMAIL_OAUTH_REDIRECT_BASE}/"


def create_auth_session(cfg: AccountConfig) -> Dict[str, object]:
    flow = InstalledAppFlow.from_client_secrets_file(cfg["credential_file"], SCOPES)
    flow.redirect_uri = oauth_callback_url(cfg["name"])
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session = {
        "account_name": cfg["name"],
        "flow": flow,
        "event": threading.Event(),
        "auth_url": auth_url,
        "state": state,
        "credentials": None,
        "error": None,
    }
    with AUTH_SESSION_LOCK:
        AUTH_SESSIONS[cfg["name"]] = session
        AUTH_SESSIONS[state] = session
    return session


def wait_for_auth_session(cfg: AccountConfig, session: Dict[str, object]) -> Credentials:
    auth_url = str(session["auth_url"])
    event = session["event"]
    if not hasattr(event, "wait"):
        raise RuntimeError("授权会话初始化失败")

    print(f"[{cfg['name']}] 缺少授权 token，请打开以下链接完成 Google 授权：\n{auth_url}")
    print(f"[{cfg['name']}] 授权回调将使用现有 Web 端口：{GMAIL_OAUTH_REDIRECT_BASE}/")
    wait_count = 0
    while not event.wait(CHECK_INTERVAL_SECONDS):
        wait_count += 1
        if AUTH_REMINDER_EVERY > 0 and wait_count % AUTH_REMINDER_EVERY == 0:
            print(f"[{cfg['name']}] 仍在等待 Google 授权，请打开以下链接：\n{auth_url}")
        save_state(cfg["name"], {"status": "等待 Google 授权，请查看终端授权链接"})

    error = session.get("error")
    if error:
        raise RuntimeError(f"Google 授权失败: {error}")

    creds = session.get("credentials")
    if not isinstance(creds, Credentials):
        raise RuntimeError("Google 授权完成但未收到 credentials")
    return creds


def request_gmail_credentials(cfg: AccountConfig, flow: InstalledAppFlow) -> Credentials:
    print(f"[{cfg['name']}] 已设置强制控制台授权，打开链接后将 code 粘贴到终端。")
    return flow.run_console()


def request_gmail_browser_auth(cfg: AccountConfig) -> Credentials:
    print(f"[{cfg['name']}] 开始浏览器授权：{cfg['email']}（token: {cfg['token_file']}）")
    if should_use_console_auth():
        flow = InstalledAppFlow.from_client_secrets_file(cfg["credential_file"], SCOPES)
        return request_gmail_credentials(cfg, flow)

    session = create_auth_session(cfg)
    try:
        return wait_for_auth_session(cfg, session)
    finally:
        with AUTH_SESSION_LOCK:
            AUTH_SESSIONS.pop(cfg["name"], None)
            AUTH_SESSIONS.pop(str(session.get("state", "")), None)


def is_google_cert_error(detail: str) -> bool:
    return "CERTIFICATE_VERIFY_FAILED" in detail or "self-signed certificate" in detail


def format_google_auth_error(error: Exception) -> str:
    detail = str(error)
    if is_google_cert_error(detail):
        return (
            "HTTPS 证书校验失败。通常是代理、抓包工具或网络网关使用了 Python 不信任的"
            "自签根证书。请先把该根证书加入系统/Python 信任链，或设置 REQUESTS_CA_BUNDLE/"
            f"SSL_CERT_FILE 指向 CA 证书文件（可写入 .env），然后重启程序重新授权。原始错误: {detail}"
        )
    return detail


def should_reauth_after_google_refresh_error(error: Exception) -> bool:
    detail = str(error).lower()
    return any(
        marker in detail
        for marker in (
            "invalid_grant",
            "refresh token",
            "token has been expired",
            "revoked",
            "unauthorized",
            "certificate_verify_failed",
            "self-signed certificate",
        )
    )


init_access_password()


DEFAULT_ACCOUNTS: List[AccountConfig] = [
    {
        "name": "account-1",
        "email": "santeekyan0162@gmail.com",
        "token_file": os.path.join(DATA_DIR, "token_account1.json"),
        "credential_file": "credentials.json",
    },
    {
        "name": "account-2",
        "email": "janymil722@gmail.com",
        "token_file": os.path.join(DATA_DIR, "token_account2.json"),
        "credential_file": "credentials.json",
    },
]

os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(HISTORY_ID_DIR, exist_ok=True)
os.makedirs(CODEX_AUTH_DIR, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> int:
    return int(time.time())


def base64_url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def generate_codex_pkce() -> Dict[str, str]:
    verifier = base64_url_no_pad(secrets.token_bytes(64))
    challenge = base64_url_no_pad(hashlib.sha256(verifier.encode("utf-8")).digest())
    return {"code_verifier": verifier, "code_challenge": challenge}


def generate_codex_state() -> str:
    return base64_url_no_pad(secrets.token_bytes(32))


def codex_oauth_callback_url() -> str:
    return f"{CODEX_OAUTH_REDIRECT_BASE}/auth/callback"


def build_codex_authorize_url(code_challenge: str, state: str) -> str:
    query = {
        "response_type": "code",
        "client_id": CODEX_CLIENT_ID,
        "redirect_uri": codex_oauth_callback_url(),
        "scope": "openid profile email offline_access api.connectors.read api.connectors.invoke",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": "codex_cli_rs",
    }
    return f"{CODEX_ISSUER}/oauth/authorize?{urllib.parse.urlencode(query)}"


def create_codex_auth_session(reason: str) -> Dict[str, Any]:
    pkce = generate_codex_pkce()
    state = generate_codex_state()
    session = {
        "state": state,
        "code_verifier": pkce["code_verifier"],
        "auth_url": build_codex_authorize_url(pkce["code_challenge"], state),
        "reason": reason,
        "created_at": now_iso(),
    }
    with CODEX_AUTH_SESSION_LOCK:
        CODEX_AUTH_SESSIONS[state] = session
    return session


def exchange_codex_code_for_tokens(code: str, code_verifier: str) -> Dict[str, str]:
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": codex_oauth_callback_url(),
            "client_id": CODEX_CLIENT_ID,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    value = http_json(
        CODEX_OAUTH_TOKEN_URL,
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data=data,
    )
    access_token = str(value.get("access_token") or "").strip()
    id_token = str(value.get("id_token") or "").strip()
    refresh_token = str(value.get("refresh_token") or "").strip()
    if not access_token:
        raise RuntimeError("token 响应缺少 access_token")
    return {
        "access_token": access_token,
        "id_token": id_token,
        "refresh_token": refresh_token,
    }


def codex_auth_payload_from_tokens(tokens: Dict[str, str]) -> Dict[str, Any]:
    payload = {
        "tokens": {
            "access_token": tokens.get("access_token", ""),
            "id_token": tokens.get("id_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
        },
        "source": "oauth",
        "updated_at": now_iso(),
    }
    record = build_codex_account_record(extract_codex_token_payload(payload))
    payload.update(
        {
            "account_id": record.get("id"),
            "email": record.get("email"),
            "subject": record.get("subject"),
            "chatgpt_account_id": record.get("chatgpt_account_id"),
            "workspace_id": record.get("workspace_id"),
            "chatgpt_plan_type": record.get("chatgpt_plan_type"),
            "access_token_expires_at": record.get("access_token_expires_at"),
        }
    )
    return payload


def codex_auth_identity(payload: Dict[str, Any]) -> str:
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    access_token = str(tokens.get("access_token") or payload.get("access_token") or "").strip()
    identity = (
        str(payload.get("account_id") or "").strip()
        or str(payload.get("chatgpt_account_id") or "").strip()
        or str(payload.get("workspace_id") or "").strip()
        or str(payload.get("email") or "").strip()
        or str(payload.get("subject") or "").strip()
    )
    if identity:
        return identity
    if access_token:
        return f"token-{hashlib.sha256(access_token.encode('utf-8')).hexdigest()[:12]}"
    return "codex_auth"


def safe_codex_auth_filename(payload: Dict[str, Any]) -> str:
    identity = codex_auth_identity(payload)
    name = re.sub(r"[^A-Za-z0-9_.=-]+", "_", identity).strip("._")
    return f"{(name or 'codex_auth')[:160]}.json"


def save_codex_auth_file(payload: Dict[str, Any]) -> None:
    with open(CODEX_AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    path = os.path.join(CODEX_AUTH_DIR, safe_codex_auth_filename(payload))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_codex_auth_file() -> Optional[Dict[str, Any]]:
    if not os.path.exists(CODEX_AUTH_FILE):
        return None
    try:
        with open(CODEX_AUTH_FILE, "r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except Exception as e:  # noqa: BLE001
        print(f"Codex auth 文件加载失败：{e}")
        return None


def load_codex_auth_files() -> List[Dict[str, Any]]:
    entries = []
    paths = []
    if os.path.exists(CODEX_AUTH_FILE):
        paths.append(CODEX_AUTH_FILE)
    if os.path.isdir(CODEX_AUTH_DIR):
        for filename in sorted(os.listdir(CODEX_AUTH_DIR)):
            if filename.endswith(".json"):
                paths.append(os.path.join(CODEX_AUTH_DIR, filename))

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = json.load(f)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, dict):
                    entries.append((os.path.getmtime(path), path, item))
        except Exception as e:  # noqa: BLE001
            print(f"Codex auth 文件加载失败：{path}: {e}")

    deduped: Dict[str, Dict[str, Any]] = {}
    for _, path, payload in sorted(entries, key=lambda item: item[0]):
        key = codex_auth_identity(payload) or path
        deduped[key] = payload
    return list(deduped.values())


def import_codex_auth_file(refresh_snapshot: bool = False) -> List[Dict[str, Any]]:
    imported: List[Dict[str, Any]] = []
    for payload in load_codex_auth_files():
        imported.extend(import_codex_auth_json(json.dumps(payload), refresh_snapshot=refresh_snapshot))
    return imported


def complete_codex_oauth_callback(code: str, state: str) -> List[Dict[str, Any]]:
    with CODEX_AUTH_SESSION_LOCK:
        session = CODEX_AUTH_SESSIONS.pop(state, None)
    if not session:
        raise RuntimeError("Codex 授权会话不存在或已过期，请重新点击导入/刷新")

    tokens = exchange_codex_code_for_tokens(code, str(session["code_verifier"]))
    payload = codex_auth_payload_from_tokens(tokens)
    save_codex_auth_file(payload)
    return import_codex_auth_json(json.dumps(payload), refresh_snapshot=True)


def decode_jwt_claims(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        value = json.loads(decoded.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def normalize_scoped_identity_value(value: Optional[str], marker: str) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None

    scoped = raw.rsplit("::", 1)[-1]
    for segment in scoped.split("|"):
        segment = segment.strip()
        if segment.startswith(marker):
            found = segment[len(marker) :].strip()
            if found:
                return found

    if "::" in raw or "|" in raw or "=" in raw or raw.startswith("import-sub-"):
        return None
    return raw


def normalize_chatgpt_account_id(value: Optional[str]) -> Optional[str]:
    return normalize_scoped_identity_value(value, "cgpt=")


def normalize_workspace_id(value: Optional[str]) -> Optional[str]:
    return normalize_scoped_identity_value(value, "ws=")


def extract_auth_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    auth = claims.get("https://api.openai.com/auth")
    return auth if isinstance(auth, dict) else {}


def extract_chatgpt_account_id_from_token(token: str) -> Optional[str]:
    claims = decode_jwt_claims(token)
    direct = normalize_chatgpt_account_id(str(claims.get("chatgpt_account_id", "") or ""))
    if direct:
        return direct
    auth = extract_auth_claims(claims)
    return normalize_chatgpt_account_id(str(auth.get("chatgpt_account_id", "") or ""))


def extract_workspace_id_from_token(token: str) -> Optional[str]:
    claims = decode_jwt_claims(token)
    for key in ("workspace_id", "chatgpt_account_id", "organization_id", "org_id"):
        found = normalize_workspace_id(str(claims.get(key, "") or ""))
        if found:
            return found

    auth = extract_auth_claims(claims)
    orgs = auth.get("organizations")
    if isinstance(orgs, list):
        default_org = next(
            (item for item in orgs if isinstance(item, dict) and item.get("is_default")),
            None,
        )
        for item in (default_org, orgs[0] if orgs else None):
            if isinstance(item, dict):
                found = normalize_workspace_id(str(item.get("id", "") or ""))
                if found:
                    return found

    for key in ("workspace_id", "chatgpt_account_id", "organization_id", "org_id"):
        found = normalize_workspace_id(str(auth.get(key, "") or ""))
        if found:
            return found
    return None


def extract_plan_type_from_claims(claims: Dict[str, Any]) -> Optional[str]:
    auth = extract_auth_claims(claims)
    raw = str(auth.get("chatgpt_plan_type", "") or "").strip().lower()
    return raw or None


def extract_token_exp(token: str) -> Optional[int]:
    exp = decode_jwt_claims(token).get("exp")
    return exp if isinstance(exp, int) else None


def optional_string_any(sources: List[tuple]) -> Optional[str]:
    for source, key in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(key)
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return None


def required_string_any(sources: List[tuple], label: str) -> str:
    value = optional_string_any(sources)
    if not value:
        raise ValueError(f"缺少 {label}")
    return value


def parse_codex_auth_items(raw_text: str) -> List[Dict[str, Any]]:
    value = json.loads(raw_text)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        raise ValueError("Codex 授权内容必须是 JSON 对象或数组")

    for key in ("items", "accounts"):
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        if isinstance(items, dict):
            nested = [item for item in items.values() if isinstance(item, dict)]
            if nested:
                return nested
    return [value]


def extract_codex_token_payload(item: Dict[str, Any]) -> Dict[str, str]:
    tokens = item.get("tokens")
    if not isinstance(tokens, dict):
        tokens = item
    user = item.get("user") if isinstance(item.get("user"), dict) else {}

    access_token = required_string_any(
        [
            (tokens, "access_token"),
            (tokens, "accessToken"),
            (item, "access_token"),
            (item, "accessToken"),
        ],
        "access_token/accessToken",
    )
    return {
        "access_token": access_token,
        "id_token": optional_string_any(
            [
                (tokens, "id_token"),
                (tokens, "idToken"),
                (item, "id_token"),
                (item, "idToken"),
            ]
        )
        or "",
        "refresh_token": optional_string_any(
            [
                (tokens, "refresh_token"),
                (tokens, "refreshToken"),
                (item, "refresh_token"),
                (item, "refreshToken"),
            ]
        )
        or "",
        "account_id_hint": optional_string_any(
            [
                (tokens, "account_id"),
                (tokens, "accountId"),
                (item, "account_id"),
                (item, "accountId"),
            ]
        )
        or "",
        "chatgpt_account_id_hint": optional_string_any(
            [
                (tokens, "chatgpt_account_id"),
                (tokens, "chatgptAccountId"),
                (item, "chatgpt_account_id"),
                (item, "chatgptAccountId"),
            ]
        )
        or "",
        "workspace_id_hint": optional_string_any(
            [
                (tokens, "workspace_id"),
                (tokens, "workspaceId"),
                (item, "workspace_id"),
                (item, "workspaceId"),
            ]
        )
        or "",
        "email": optional_string_any([(item, "email"), (user, "email")]) or "",
    }


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def build_codex_account_record(
    payload: Dict[str, str], existing: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    access_token = payload["access_token"]
    id_token = payload.get("id_token") or access_token
    access_claims = decode_jwt_claims(access_token)
    id_claims = decode_jwt_claims(id_token)
    claims = id_claims or access_claims

    subject = str(access_claims.get("sub") or claims.get("sub") or "").strip()
    email = (
        payload.get("email")
        or str(claims.get("email") or "").strip()
        or str(access_claims.get("email") or "").strip()
    )
    chatgpt_account_id = (
        normalize_chatgpt_account_id(payload.get("chatgpt_account_id_hint"))
        or extract_chatgpt_account_id_from_token(id_token)
        or extract_chatgpt_account_id_from_token(access_token)
    )
    workspace_id = (
        normalize_workspace_id(payload.get("workspace_id_hint"))
        or extract_workspace_id_from_token(id_token)
        or extract_workspace_id_from_token(access_token)
        or chatgpt_account_id
    )
    plan_type = extract_plan_type_from_claims(access_claims) or extract_plan_type_from_claims(claims)

    if chatgpt_account_id and workspace_id and chatgpt_account_id != workspace_id:
        account_id = f"{chatgpt_account_id}::ws={workspace_id}"
    else:
        account_id = (
            chatgpt_account_id
            or workspace_id
            or payload.get("account_id_hint")
            or subject
            or f"codex-{token_fingerprint(access_token)}"
        )

    now = now_iso()
    previous = existing or {}
    record = {
        "id": previous.get("id") or account_id,
        "label": email or chatgpt_account_id or workspace_id or account_id,
        "email": email,
        "subject": subject,
        "chatgpt_account_id": chatgpt_account_id,
        "workspace_id": workspace_id,
        "chatgpt_plan_type": plan_type,
        "account_id_hint": payload.get("account_id_hint") or "",
        "token": {
            "access_token": access_token,
            "id_token": payload.get("id_token") or "",
            "refresh_token": payload.get("refresh_token") or "",
        },
        "access_token_expires_at": extract_token_exp(access_token),
        "subscription": previous.get("subscription"),
        "subscription_last_refresh_at": previous.get("subscription_last_refresh_at"),
        "usage": previous.get("usage"),
        "status": "已导入",
        "error": previous.get("error"),
        "subscription_error": previous.get("subscription_error"),
        "usage_error": None,
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
        "last_refresh_at": previous.get("last_refresh_at"),
    }
    return record


def normalize_codex_base_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        base = "https://chatgpt.com"
    is_chatgpt_host = base.startswith("https://chatgpt.com") or base.startswith(
        "https://chat.openai.com"
    )
    if is_chatgpt_host and "/backend-api" not in base:
        base += "/backend-api"
    return base


def codex_usage_endpoint() -> str:
    base = normalize_codex_base_url(CODEX_USAGE_BASE_URL)
    if "/backend-api" in base:
        return f"{base}/wham/usage"
    return f"{base}/api/codex/usage"


def codex_accounts_check_endpoint() -> str:
    return f"{normalize_codex_base_url(CODEX_USAGE_BASE_URL)}/accounts/check/v4-2023-04-27"


def summarize_response_body(body: str) -> str:
    text = body.strip()
    if not text:
        return ""
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            for key in ("message", "detail", "error", "code"):
                found = value.get(key)
                if isinstance(found, str) and found.strip():
                    return found.strip()[:240]
                if isinstance(found, dict):
                    nested = found.get("message") or found.get("code")
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()[:240]
    except Exception:
        pass
    return " ".join(text.split())[:240]


def response_error_details(headers: Any) -> str:
    details = []
    content_type = headers.get("Content-Type", "")
    if "html" in str(content_type).lower():
        details.append("kind=html")
    for name, label in (
        ("x-request-id", "request_id"),
        ("x-oai-request-id", "request_id"),
        ("cf-ray", "cf_ray"),
        ("x-openai-authorization-error", "auth_error"),
    ):
        value = headers.get(name)
        if value:
            details.append(f"{label}={value}")
    return f" [{', '.join(details)}]" if details else ""


def http_json(url: str, headers: Dict[str, str], data: Optional[bytes] = None) -> Dict[str, Any]:
    method = "POST" if data is not None else "GET"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=CODEX_HTTP_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8", errors="replace")
            if "text/html" in content_type.lower():
                raise RuntimeError(f"上游返回 HTML: {summarize_response_body(body)}")
            if not body.strip():
                return {}
            value = json.loads(body)
            return value if isinstance(value, dict) else {"value": value}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        suffix = summarize_response_body(body)
        detail = f" status {e.code} {e.reason}{response_error_details(e.headers)}"
        if suffix:
            detail += f": {suffix}"
        raise RuntimeError(detail) from e
    except urllib.error.URLError as e:
        raise RuntimeError(str(e.reason)) from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON 解析失败: {e}") from e


def parse_subscription_timestamp(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def normalize_account_plan_value(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    aliases = {"education": "edu"}
    return aliases.get(normalized, normalized)


def build_subscription_snapshot(entry: Dict[str, Any]) -> Dict[str, Any]:
    account = entry.get("account") if isinstance(entry.get("account"), dict) else {}
    entitlement = entry.get("entitlement") if isinstance(entry.get("entitlement"), dict) else {}

    account_plan_type = normalize_account_plan_value(account.get("plan_type")) or normalize_account_plan_value(
        entitlement.get("subscription_plan")
    )
    plan_type = normalize_account_plan_value(entitlement.get("subscription_plan")) or account_plan_type
    expires_at = parse_subscription_timestamp(entitlement.get("expires_at"))
    renews_at = (
        parse_subscription_timestamp(entitlement.get("renews_at"))
        or parse_subscription_timestamp(entitlement.get("next_renewal_at"))
        or parse_subscription_timestamp(entitlement.get("next_credit_grant_update"))
        or parse_subscription_timestamp(entitlement.get("renewal_date"))
    )
    if not renews_at and entitlement.get("will_renew") and expires_at:
        renews_at = expires_at

    has_subscription = entitlement.get("has_active_subscription")
    if has_subscription is None:
        has_subscription = (
            account.get("has_subscription")
            if account.get("has_subscription") is not None
            else account.get("has_active_subscription")
        )
    if has_subscription is None:
        has_subscription = account.get("is_paid_subscription_active")
    if has_subscription is None:
        has_subscription = bool(
            (account_plan_type and account_plan_type != "free")
            or (plan_type and plan_type != "free")
            or expires_at
            or renews_at
        )

    return {
        "has_subscription": bool(has_subscription),
        "account_plan_type": account_plan_type,
        "plan_type": plan_type,
        "expires_at": expires_at,
        "renews_at": renews_at,
    }


def fetch_codex_subscription(access_token: str, account_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not account_id:
        return None
    value = http_json(
        codex_accounts_check_endpoint(),
        {
            "Authorization": f"Bearer {access_token}",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "Accept": "application/json",
        },
    )
    accounts = value.get("accounts") if isinstance(value.get("accounts"), dict) else {}
    if not accounts:
        return {
            "has_subscription": False,
            "account_plan_type": None,
            "plan_type": None,
            "expires_at": None,
            "renews_at": None,
        }

    matched = accounts.get(account_id)
    if isinstance(matched, dict):
        return build_subscription_snapshot(matched)

    snapshots = []
    default_snapshot = None
    paid_snapshot = None
    for entry in accounts.values():
        if not isinstance(entry, dict):
            continue
        snapshot = build_subscription_snapshot(entry)
        snapshots.append(snapshot)
        account = entry.get("account") if isinstance(entry.get("account"), dict) else {}
        if default_snapshot is None and account.get("is_default"):
            default_snapshot = snapshot
        if paid_snapshot is None and snapshot.get("account_plan_type") not in (None, "free"):
            paid_snapshot = snapshot
    return default_snapshot or paid_snapshot or (snapshots[0] if snapshots else None)


def parse_iso_timestamp(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def should_refresh_codex_subscription(record: Dict[str, Any]) -> bool:
    refreshed_at = parse_iso_timestamp(record.get("subscription_last_refresh_at"))
    if refreshed_at is None:
        return True
    return now_ts() - refreshed_at >= max(60, CODEX_SUBSCRIPTION_REFRESH_INTERVAL_SECONDS)


def get_nested_number(value: Dict[str, Any], path: List[str]) -> Optional[float]:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, (int, float)):
        return float(current)
    return None


def get_nested_int(value: Dict[str, Any], path: List[str]) -> Optional[int]:
    found = get_nested_number(value, path)
    return int(found) if found is not None else None


def parse_usage_snapshot(value: Dict[str, Any]) -> Dict[str, Any]:
    window_seconds = get_nested_int(value, ["rate_limit", "primary_window", "limit_window_seconds"])
    secondary_window_seconds = get_nested_int(
        value, ["rate_limit", "secondary_window", "limit_window_seconds"]
    )
    return {
        "used_percent": get_nested_number(value, ["rate_limit", "primary_window", "used_percent"]),
        "window_minutes": (window_seconds + 59) // 60 if window_seconds is not None else None,
        "resets_at": get_nested_int(value, ["rate_limit", "primary_window", "reset_at"]),
        "secondary_used_percent": get_nested_number(
            value, ["rate_limit", "secondary_window", "used_percent"]
        ),
        "secondary_window_minutes": (
            (secondary_window_seconds + 59) // 60
            if secondary_window_seconds is not None
            else None
        ),
        "secondary_resets_at": get_nested_int(
            value, ["rate_limit", "secondary_window", "reset_at"]
        ),
        "credits": value.get("credits"),
        "captured_at": now_ts(),
    }


def classify_usage_status(snapshot: Optional[Dict[str, Any]]) -> str:
    if not snapshot:
        return "unknown"
    primary_present = snapshot.get("used_percent") is not None and snapshot.get("window_minutes") is not None
    if not primary_present:
        return "unavailable"
    if float(snapshot.get("used_percent") or 0) >= CODEX_EXHAUSTED_USED_PERCENT:
        return "unavailable"
    secondary_present = snapshot.get("secondary_used_percent") is not None or snapshot.get(
        "secondary_window_minutes"
    ) is not None
    secondary_complete = snapshot.get("secondary_used_percent") is not None and snapshot.get(
        "secondary_window_minutes"
    ) is not None
    if not secondary_present:
        return "primary_window_available_only"
    if not secondary_complete:
        return "unavailable"
    if float(snapshot.get("secondary_used_percent") or 0) >= CODEX_EXHAUSTED_USED_PERCENT:
        return "unavailable"
    credits = snapshot.get("credits") if isinstance(snapshot.get("credits"), dict) else {}
    if credits.get("overage_limit_reached") is True:
        return "unavailable"
    return "available"


def codex_error_status_reason(error: str) -> str:
    normalized = error.lower()
    if "订阅信息" in normalized:
        if "401" in normalized:
            return "subscription_http_401"
        if "403" in normalized:
            return "subscription_http_403"
        return "subscription_refresh_failed"
    if "401" in normalized:
        return "usage_http_401"
    if "403" in normalized:
        return "usage_http_403"
    if "timeout" in normalized or "timed out" in normalized:
        return "usage_refresh_timeout"
    if "name or service" in normalized or "dns" in normalized:
        return "usage_refresh_dns"
    if "connection" in normalized or "连接" in normalized:
        return "usage_refresh_connection"
    if "refresh token" in normalized or "invalid_grant" in normalized:
        return "refresh_token_invalid"
    return "usage_refresh_failed"


def codex_usage_status_reason(
    snapshot: Optional[Dict[str, Any]], error: Optional[str] = None
) -> str:
    if error:
        return codex_error_status_reason(error)
    if not snapshot:
        return "usage_unknown"

    primary_present = snapshot.get("used_percent") is not None and snapshot.get("window_minutes") is not None
    if not primary_present:
        return "usage_missing_primary"
    if float(snapshot.get("used_percent") or 0) >= CODEX_EXHAUSTED_USED_PERCENT:
        return "usage_limit_exhausted"

    secondary_present = snapshot.get("secondary_used_percent") is not None or snapshot.get(
        "secondary_window_minutes"
    ) is not None
    secondary_complete = snapshot.get("secondary_used_percent") is not None and snapshot.get(
        "secondary_window_minutes"
    ) is not None
    if secondary_present and not secondary_complete:
        return "usage_missing_secondary"
    if (
        secondary_complete
        and float(snapshot.get("secondary_used_percent") or 0) >= CODEX_EXHAUSTED_USED_PERCENT
    ):
        return "usage_limit_exhausted"

    credits = snapshot.get("credits") if isinstance(snapshot.get("credits"), dict) else {}
    if credits.get("overage_limit_reached") is True:
        return "usage_limit_exhausted"
    if not secondary_present:
        return "secondary_window_missing"
    return "usage_ok"


def fetch_codex_usage(access_token: str, workspace_id: Optional[str]) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "codex_cli_rs/0.0.0",
        "originator": "codex_cli_rs",
    }
    if workspace_id:
        headers["ChatGPT-Account-ID"] = workspace_id
    return parse_usage_snapshot(http_json(codex_usage_endpoint(), headers))


def should_refresh_codex_token(error: str) -> bool:
    detail = error.lower()
    if "unsupported_country_region_territory" in detail:
        return False
    return " status 401 " in detail or " status 403 " in detail or "token" in detail


def refresh_codex_access_token(record: Dict[str, Any]) -> bool:
    token = record.get("token") if isinstance(record.get("token"), dict) else {}
    refresh_token = str(token.get("refresh_token") or "").strip()
    if not refresh_token:
        return False

    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": CODEX_CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": "openid profile email",
        }
    ).encode("utf-8")
    value = http_json(
        CODEX_REFRESH_TOKEN_URL,
        {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data=data,
    )
    access_token = str(value.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("刷新 token 响应缺少 access_token")

    token["access_token"] = access_token
    if isinstance(value.get("refresh_token"), str) and value["refresh_token"].strip():
        token["refresh_token"] = value["refresh_token"].strip()
    if isinstance(value.get("id_token"), str) and value["id_token"].strip():
        token["id_token"] = value["id_token"].strip()
    record["token"] = token

    refreshed_payload = {
        "access_token": token["access_token"],
        "id_token": token.get("id_token") or "",
        "refresh_token": token.get("refresh_token") or "",
        "account_id_hint": str(record.get("account_id_hint") or ""),
        "chatgpt_account_id_hint": str(record.get("chatgpt_account_id") or ""),
        "workspace_id_hint": str(record.get("workspace_id") or ""),
        "email": str(record.get("email") or ""),
    }
    refreshed_record = build_codex_account_record(refreshed_payload, record)
    record.update(refreshed_record)
    record["status"] = "token 已刷新"
    return True


def refresh_codex_account_record(
    record: Dict[str, Any], force_subscription: bool = False
) -> Dict[str, Any]:
    updated = dict(record)
    updated["token"] = dict(record.get("token") or {})
    if not str(updated["token"].get("access_token") or "").strip():
        raise ValueError("账号缺少 access_token")

    errors: List[str] = []
    subscription_errors: List[str] = []
    usage_errors: List[str] = []
    subscription_refresh_attempted = False
    refreshed_once = False
    for attempt in range(2):
        errors = []
        subscription_errors = []
        usage_errors = []
        access_token = str(updated["token"].get("access_token") or "").strip()
        chatgpt_account_id = str(updated.get("chatgpt_account_id") or "").strip() or None
        workspace_id = str(updated.get("workspace_id") or "").strip() or chatgpt_account_id

        refresh_subscription = force_subscription or should_refresh_codex_subscription(updated)
        if refresh_subscription:
            subscription_refresh_attempted = True
            try:
                subscription = fetch_codex_subscription(access_token, chatgpt_account_id)
                if subscription is not None:
                    updated["subscription"] = subscription
            except Exception as e:  # noqa: BLE001
                message = f"订阅信息: {e}"
                subscription_errors.append(message)
                errors.append(message)

        try:
            usage = fetch_codex_usage(access_token, workspace_id)
            updated["usage"] = usage
            updated["usage_status"] = classify_usage_status(usage)
            updated["status_reason"] = codex_usage_status_reason(usage)
        except Exception as e:  # noqa: BLE001
            message = f"额度信息: {e}"
            usage_errors.append(message)
            errors.append(message)

        if (
            errors
            and attempt == 0
            and not refreshed_once
            and any(should_refresh_codex_token(error) for error in errors)
        ):
            try:
                refreshed_once = refresh_codex_access_token(updated)
                if refreshed_once:
                    continue
            except Exception as e:  # noqa: BLE001
                errors.append(f"刷新 token: {e}")
        break

    updated["last_refresh_at"] = now_iso()
    updated["updated_at"] = now_iso()
    updated["access_token_expires_at"] = extract_token_exp(str(updated["token"].get("access_token") or ""))
    if subscription_refresh_attempted:
        updated["subscription_last_refresh_at"] = now_iso()
        updated["subscription_error"] = "; ".join(subscription_errors) if subscription_errors else None
    else:
        updated["subscription_last_refresh_at"] = record.get("subscription_last_refresh_at")
        updated["subscription_error"] = record.get("subscription_error")
    updated["usage_error"] = "; ".join(usage_errors) if usage_errors else None
    updated["error"] = updated["usage_error"]
    if usage_errors:
        updated["status_reason"] = codex_usage_status_reason(
            updated.get("usage") if isinstance(updated.get("usage"), dict) else None,
            updated["usage_error"],
        )
    else:
        updated["status_reason"] = codex_usage_status_reason(
            updated.get("usage") if isinstance(updated.get("usage"), dict) else None
        )
    if not usage_errors:
        updated["status"] = "已刷新"
    elif updated.get("subscription") or updated.get("usage"):
        updated["status"] = "部分刷新失败"
    else:
        updated["status"] = "刷新失败"
    return updated


def load_codex_accounts() -> None:
    if not os.path.exists(CODEX_ACCOUNTS_FILE):
        return
    try:
        with open(CODEX_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return
        with CODEX_ACCOUNTS_LOCK:
            CODEX_ACCOUNTS.clear()
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    CODEX_ACCOUNTS[str(item["id"])] = item
    except Exception as e:  # noqa: BLE001
        print(f"Codex 账号信息加载失败：{e}")


def save_codex_accounts() -> None:
    with CODEX_ACCOUNTS_LOCK:
        items = list(CODEX_ACCOUNTS.values())
    with open(CODEX_ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)


def sanitize_codex_account(record: Dict[str, Any]) -> Dict[str, Any]:
    token = record.get("token") if isinstance(record.get("token"), dict) else {}
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else None
    usage_status = record.get("usage_status") or classify_usage_status(usage)
    error = record.get("error")
    subscription_error = record.get("subscription_error")
    usage_error = record.get("usage_error")
    if error and str(error).startswith("订阅信息") and not usage_error:
        subscription_error = subscription_error or error
        error = None
    status_reason = (
        record.get("status_reason")
        or codex_usage_status_reason(usage, str(error) if error else None)
    )
    if not error and status_reason in {"subscription_http_401", "subscription_http_403", "subscription_refresh_failed"}:
        status_reason = codex_usage_status_reason(usage)
    return {
        "id": record.get("id"),
        "label": record.get("label"),
        "email": record.get("email"),
        "subject": record.get("subject"),
        "chatgpt_account_id": record.get("chatgpt_account_id"),
        "workspace_id": record.get("workspace_id"),
        "chatgpt_plan_type": record.get("chatgpt_plan_type"),
        "access_token_expires_at": record.get("access_token_expires_at"),
        "has_refresh_token": bool(str(token.get("refresh_token") or "").strip()),
        "subscription": record.get("subscription"),
        "subscription_last_refresh_at": record.get("subscription_last_refresh_at"),
        "usage": usage,
        "usage_status": usage_status,
        "status_reason": status_reason,
        "status": record.get("status"),
        "error": error,
        "subscription_error": subscription_error,
        "usage_error": usage_error,
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "last_refresh_at": record.get("last_refresh_at"),
    }


def import_codex_auth_json(raw_text: str, refresh_snapshot: bool = True) -> List[Dict[str, Any]]:
    imported: List[Dict[str, Any]] = []
    for item in parse_codex_auth_items(raw_text):
        payload = extract_codex_token_payload(item)
        candidate = build_codex_account_record(payload)
        with CODEX_ACCOUNTS_LOCK:
            existing = CODEX_ACCOUNTS.get(str(candidate.get("id") or ""))
        record = build_codex_account_record(payload, existing)
        if refresh_snapshot:
            try:
                record = refresh_codex_account_record(record, force_subscription=True)
            except Exception as e:  # noqa: BLE001
                record["status"] = "已导入，刷新失败"
                record["error"] = str(e)
                record["status_reason"] = codex_usage_status_reason(
                    record.get("usage") if isinstance(record.get("usage"), dict) else None,
                    str(e),
                )
                record["updated_at"] = now_iso()
        with CODEX_ACCOUNTS_LOCK:
            CODEX_ACCOUNTS[str(record["id"])] = record
        imported.append(sanitize_codex_account(record))
    save_codex_accounts()
    return imported


def refresh_all_codex_accounts_once() -> None:
    with CODEX_ACCOUNTS_LOCK:
        records = list(CODEX_ACCOUNTS.values())

    if not records:
        return

    changed = False
    for record in records:
        account_id = str(record.get("id") or "")
        if not account_id:
            continue
        try:
            updated = refresh_codex_account_record(record)
        except Exception as e:  # noqa: BLE001
            updated = dict(record)
            updated["status"] = "刷新失败"
            updated["error"] = str(e)
            updated["status_reason"] = codex_usage_status_reason(
                updated.get("usage") if isinstance(updated.get("usage"), dict) else None,
                str(e),
            )
            updated["updated_at"] = now_iso()

        with CODEX_ACCOUNTS_LOCK:
            CODEX_ACCOUNTS[account_id] = updated
        changed = True

    if changed:
        save_codex_accounts()


def codex_refresh_loop() -> None:
    while True:
        try:
            refresh_all_codex_accounts_once()
        except Exception as e:  # noqa: BLE001
            print(f"Codex 账号额度刷新失败：{e}")
        time.sleep(max(10, CODEX_REFRESH_INTERVAL_SECONDS))


def load_accounts() -> List[AccountConfig]:
    raw = os.getenv("GMAIL_ACCOUNTS")
    if not raw:
        return DEFAULT_ACCOUNTS

    cfgs = json.loads(raw)
    normalized = []
    for item in cfgs:
        normalized.append(
            {
                "name": item.get("name", "account"),
                "email": item.get("email", "未配置"),
                "token_file": item.get("token_file", "token.json"),
                "credential_file": item.get("credential_file", "credentials.json"),
            }
        )
    return normalized[:2]


ACCOUNTS = load_accounts()
STATE_LOCK = threading.Lock()
STATE: Dict[str, Dict[str, Optional[str]]] = {}
CODE_HISTORY: Dict[str, List[Dict[str, str]]] = {}
CODEX_ACCOUNTS: Dict[str, Dict[str, Any]] = {}
CODEX_AUTH_SESSIONS: Dict[str, Dict[str, Any]] = {}


for cfg in ACCOUNTS:
    STATE[cfg["name"]] = {
        "name": cfg["name"],
        "email": cfg["email"],
        "last_code": None,
        "message_id": None,
        "subject": None,
        "updated_at": None,
        "status": "待启动",
    }
    CODE_HISTORY[cfg["name"]] = []


def history_file_path(name: str) -> str:
    return os.path.join(HISTORY_DIR, f"{name}.codes.history.json")


def load_code_history(name: str) -> List[Dict[str, str]]:
    path = history_file_path(name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def save_code_history(name: str, records: List[Dict[str, str]]) -> None:
    path = history_file_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def clear_code_history(name: str) -> None:
    CODE_HISTORY.pop(name, None)
    path = history_file_path(name)
    if os.path.exists(path):
        os.remove(path)


def append_code_record(name: str, record: Dict[str, str]) -> None:
    with HISTORY_LOCK:
        records = CODE_HISTORY.get(name, [])
        if any(item.get("message_id") == record.get("message_id") for item in records):
            return
        records.insert(0, record)
        CODE_HISTORY[name] = records[:MAX_HISTORY_RECORDS]
        save_code_history(name, CODE_HISTORY[name])


def load_histories():
    for cfg in ACCOUNTS:
        CODE_HISTORY[cfg["name"]] = load_code_history(cfg["name"])


def request_access_ok(request: FastAPIRequest) -> bool:
    if not ACCESS_PASSWORD:
        return True

    cookie_token = request.cookies.get(ACCESS_COOKIE_NAME, "")
    header_token = request.headers.get("x-access-token", "")
    query_token = request.query_params.get("access_token", "")
    bearer = request.headers.get("authorization", "")
    bearer_token = ""
    if bearer.startswith("Bearer "):
        bearer_token = bearer.split(" ", 1)[1]

    token = header_token or query_token or bearer_token
    if cookie_token == ACCESS_PASSWORD or token == ACCESS_PASSWORD:
        return True
    return False


@app.middleware("http")
async def access_guard(request: FastAPIRequest, call_next):
    if request_access_ok(request):
        return await call_next(request)

    path = request.url.path
    if path in {"/login", "/login.html", "/auth/callback"} or path.startswith("/oauth2callback/"):
        return await call_next(request)
    if path == "/" and request.query_params.get("code") and request.query_params.get("state"):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(content={"detail": "未授权访问，请先登录"}, status_code=401)

    return RedirectResponse(url="/login")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    if not ACCESS_PASSWORD:
        return RedirectResponse(url="/")

    return """
    <!doctype html>
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8" />
      <title>访问口令</title>
      <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f3f4f6; display: grid; place-items: center; min-height: 100vh; }
        .card { width: 360px; background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 12px 25px rgba(0,0,0,0.08); }
        h2 { margin: 0 0 16px; }
        input { width: 100%; padding: 10px 12px; margin-top: 8px; border: 1px solid #d1d5db; border-radius: 8px; }
        button { margin-top: 12px; width: 100%; padding: 10px 12px; border: 0; border-radius: 8px; background: #0ea5e9; color: #fff; font-weight: 700; cursor: pointer; }
      </style>
    </head>
    <body>
      <form class="card" method="post" action="/login">
        <h2>输入访问口令</h2>
        <div>请输入分享口令后进入页面。</div>
        <input type="password" name="access_token" placeholder="口令" required />
        <button type="submit">进入</button>
      </form>
    </body>
    </html>
    """


@app.post("/login")
def login_submit(access_token: str = Form(...)):
    if not ACCESS_PASSWORD:
        return RedirectResponse(url="/", status_code=302)

    if access_token == ACCESS_PASSWORD:
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(ACCESS_COOKIE_NAME, access_token, httponly=True, max_age=86400, samesite="Lax")
        return response

    return HTMLResponse("口令错误", status_code=401)


def complete_oauth_callback(request: FastAPIRequest) -> HTMLResponse:
    state = request.query_params.get("state", "")
    with AUTH_SESSION_LOCK:
        session = AUTH_SESSIONS.get(state)

    if not session:
        return HTMLResponse("授权会话不存在或已完成，请回到终端重新发起授权。", status_code=404)

    event = session["event"]
    try:
        flow = session["flow"]
        if not isinstance(flow, InstalledAppFlow):
            raise RuntimeError("授权会话数据异常")
        flow.fetch_token(authorization_response=str(request.url))
        session["credentials"] = flow.credentials
        session["error"] = None
        if hasattr(event, "set"):
            event.set()
        return HTMLResponse("Google 授权完成，token 已返回服务器。可以关闭此页面并回到终端。")
    except Exception as e:  # noqa: BLE001
        session["error"] = format_google_auth_error(e)
        if hasattr(event, "set"):
            event.set()
        return HTMLResponse(f"Google 授权失败：{html.escape(session['error'])}", status_code=400)


@app.get("/oauth2callback/{account_name}", response_class=HTMLResponse)
def oauth2_callback(account_name: str, request: FastAPIRequest):
    return complete_oauth_callback(request)


def get_gmail_service(cfg: AccountConfig):
    creds = None
    token_file = cfg["token_file"]
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        with AUTH_LOCK:
            if os.path.exists(token_file):
                creds = Credentials.from_authorized_user_file(token_file, SCOPES)
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(GoogleRequest())
                    except Exception as e:  # noqa: BLE001
                        if not should_reauth_after_google_refresh_error(e):
                            raise RuntimeError(format_google_auth_error(e)) from e
                        backup_path = backup_token_file(token_file)
                        suffix = f"，旧 token 已备份到 {backup_path}" if backup_path else ""
                        print(f"[{cfg['name']}] token 刷新失败，将重新授权{suffix}：{format_google_auth_error(e)}")
                        creds = None
            if not creds or not creds.valid:
                if not os.path.exists(cfg["credential_file"]):
                    raise FileNotFoundError(
                        f"{cfg['name']} 的 credential 文件未找到: {cfg['credential_file']}"
                    )
                creds = request_gmail_browser_auth(cfg)
        with open(token_file, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def backup_token_file(token_file: str) -> Optional[str]:
    if not os.path.exists(token_file):
        return None
    backup_path = f"{token_file}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    os.replace(token_file, backup_path)
    return backup_path


def decode_body(data: Optional[str]) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def collect_text_from_payload(payload: Dict) -> List[str]:
    if not payload:
        return []

    texts: List[str] = []
    for part in payload.get("parts", []):
        texts.extend(collect_text_from_payload(part))

    mime = (payload.get("mimeType") or "").lower()
    body_data = payload.get("body", {}).get("data", "")
    if body_data and (mime.startswith("text/plain") or mime.startswith("text/html")):
        raw = decode_body(body_data)
        if mime.startswith("text/html"):
            raw = html.unescape(raw)
            raw = re.sub(r"<[^>]+>", " ", raw)
        texts.append(raw)

    return texts


def extract_verification_code(message: Dict) -> Optional[str]:
    headers = message.get("payload", {}).get("headers", [])
    snippet = message.get("snippet", "")
    subject = ""
    for h in headers:
        if h.get("name", "").lower() == "subject":
            subject = h.get("value", "")
            break

    payload_texts = collect_text_from_payload(message.get("payload", {}))
    candidate_text = "\n".join([snippet, subject] + payload_texts)

    for pattern in CODE_PATTERNS:
        m = pattern.search(candidate_text)
        if m:
            return m.group(1)

    return None


def parse_subject(message: Dict) -> str:
    for header in message.get("payload", {}).get("headers", []):
        if header.get("name") == "Subject":
            return header.get("value", "")
    return "（无主题）"


def mark_as_read(service, message_id: str):
    service.users().messages().batchModify(
        userId="me",
        body={"ids": [message_id], "removeLabelIds": ["UNREAD"]},
    ).execute()


def read_history_id(cfg: AccountConfig) -> int:
    path = os.path.join(HISTORY_ID_DIR, f"{cfg['name']}.history")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)
    return 0


def write_history_id(cfg: AccountConfig, history_id: int):
    path = os.path.join(HISTORY_ID_DIR, f"{cfg['name']}.history")
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(history_id))


def save_state(name: str, patch: Dict[str, Optional[str]]):
    with STATE_LOCK:
        STATE[name].update(patch)


def process_message(service, cfg: AccountConfig, message_id: str) -> Optional[Dict[str, str]]:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()

    labels = msg.get("labelIds", [])
    if "UNREAD" not in labels:
        return None

    code = extract_verification_code(msg)
    if not code:
        return None

    mark_as_read(service, msg["id"])
    return {
        "code": code,
        "message_id": msg["id"],
        "subject": parse_subject(msg),
    }


def scan_unread_once(service, cfg: AccountConfig):
    response = service.users().messages().list(
        userId="me",
        q=SEARCH_QUERY,
        maxResults=20,
    ).execute()
    latest = None
    for item in response.get("messages", []):
        result = process_message(service, cfg, item["id"])
        if result:
            append_code_record(
                cfg["name"],
                {
                    "code": result["code"],
                    "message_id": result["message_id"],
                    "subject": result["subject"],
                    "created_at": now_iso(),
                    "email": cfg["email"],
                },
            )
            latest = result
    if latest:
        save_state(
            cfg["name"],
            {
                "last_code": latest["code"],
                "message_id": latest["message_id"],
                "subject": latest["subject"],
                "updated_at": now_iso(),
                "status": "已完成（补扫）",
            },
        )


def watch_account(cfg: AccountConfig):
    name = cfg["name"]
    save_state(name, {"status": "启动中"})

    try:
        service = get_gmail_service(cfg)
        profile = service.users().getProfile(userId="me").execute()
        history_id = read_history_id(cfg) or int(profile["historyId"])

        # 首次启动，先补扫一次未读验证码邮件，防止历史遗漏
        scan_unread_once(service, cfg)

        while True:
            try:
                response = service.users().history().list(
                    userId="me",
                    startHistoryId=history_id,
                    maxResults=MAX_POLL_RESULTS,
                ).execute()

                got = False
                for item in response.get("history", []):
                    for added in item.get("messagesAdded", []):
                        message = added.get("message", {})
                        msg_id = message.get("id")
                        if not msg_id:
                            continue
                        result = process_message(service, cfg, msg_id)
                        if result:
                            got = True
                            append_code_record(
                                cfg["name"],
                                {
                                    "code": result["code"],
                                    "message_id": result["message_id"],
                                    "subject": result["subject"],
                                    "created_at": now_iso(),
                                    "email": cfg["email"],
                                },
                            )
                            save_state(
                                name,
                                {
                                    "last_code": result["code"],
                                    "message_id": result["message_id"],
                                    "subject": result["subject"],
                                    "updated_at": now_iso(),
                                    "status": "监听中",
                                },
                            )
                if response.get("historyId"):
                    history_id = int(response["historyId"])
                    write_history_id(cfg, history_id)

                if not got:
                    save_state(name, {"status": "监听中"})

            except HttpError as e:
                status = getattr(e, "status", None) or getattr(e.resp, "status", None)
                if status in (404, 410):
                    save_state(name, {"status": "历史游标过期，补扫"})
                    scan_unread_once(service, cfg)
                    profile = service.users().getProfile(userId="me").execute()
                    history_id = int(profile["historyId"])
                    write_history_id(cfg, history_id)
                else:
                    save_state(name, {"status": f"API 错误: {status}"})
            except Exception as e:  # noqa: BLE001
                save_state(name, {"status": f"运行错误: {e}"})

            time.sleep(CHECK_INTERVAL_SECONDS)
    except Exception as e:  # noqa: BLE001
        save_state(name, {"status": f"启动失败: {e}"})


@app.on_event("startup")
def start_watchers():
    source = "环境变量 ACCESS_PASSWORD" if ACCESS_PASSWORD_FROM_ENV else "随机生成（重启后可能变化）"
    print(f"访问口令：{ACCESS_PASSWORD}（来源：{source}）")
    load_histories()
    load_codex_accounts()
    import_codex_auth_file(refresh_snapshot=False)
    threading.Thread(target=codex_refresh_loop, daemon=True).start()
    for cfg in ACCOUNTS:
        threading.Thread(target=watch_account, args=(cfg,), daemon=True).start()


@app.get("/")
def index(request: FastAPIRequest):
    if request.query_params.get("code") and request.query_params.get("state"):
        return complete_oauth_callback(request)
    return FileResponse("static/index.html")


@app.get("/api/codes")
def get_codes():
    with STATE_LOCK:
        data = list(STATE.values())
    return JSONResponse(content={"items": data, "updated_at": now_iso()})


def parse_codex_login_addr() -> tuple:
    raw = CODEX_LOGIN_ADDR or urllib.parse.urlparse(CODEX_OAUTH_REDIRECT_BASE).netloc
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        raw = parsed.netloc
    host, sep, port_text = raw.rpartition(":")
    if not sep:
        return raw or "localhost", 1455
    return host or "localhost", int(port_text)


def codex_callback_result(params: Dict[str, str]) -> tuple:
    error_code = params.get("error", "").strip()
    error_description = params.get("error_description", "").strip()
    if error_code:
        detail = error_description or error_code
        return False, f"Codex 授权失败：{detail}", 400

    code = params.get("code", "").strip()
    state = params.get("state", "").strip()
    if not code or not state:
        return False, "Codex 授权回调缺少 code 或 state", 400

    try:
        imported = complete_codex_oauth_callback(code, state)
    except Exception as e:  # noqa: BLE001
        return False, f"Codex 授权导入失败：{e}", 500

    label = imported[0].get("label") if imported else "Codex 账号"
    return True, f"{label} 已导入，auth 文件已保存到 runtime/codex_auth/ 并同步最新文件", 200


class CodexLoginHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


class CodexLoginCallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_error(404)
            return

        params = dict(urllib.parse.parse_qsl(parsed.query))
        ok, message, status_code = codex_callback_result(params)
        body = codex_callback_html(ok, message, redirect_url=APP_BASE_URL if ok else None)
        data = body.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def ensure_codex_login_server() -> None:
    global CODEX_LOGIN_SERVER
    with CODEX_LOGIN_SERVER_LOCK:
        if CODEX_LOGIN_SERVER is not None:
            return
        host, port = parse_codex_login_addr()
        try:
            server = CodexLoginHTTPServer((host, port), CodexLoginCallbackHandler)
        except OSError as e:
            raise RuntimeError(
                f"Codex 登录回调端口 {host}:{port} 无法监听：{e}。"
                "请确认没有其他程序占用，或设置 CODEX_LOGIN_ADDR/CODEX_OAUTH_REDIRECT_BASE。"
            ) from e
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        CODEX_LOGIN_SERVER = server
        print(f"Codex 授权回调监听中：http://{host}:{port}/auth/callback")


@app.get("/api/codex/auth/start")
def start_codex_auth(request: FastAPIRequest):
    reason = request.query_params.get("reason", "import").strip() or "import"
    try:
        ensure_codex_login_server()
    except Exception as e:  # noqa: BLE001
        return JSONResponse(content={"detail": str(e)}, status_code=500)
    session = create_codex_auth_session(reason)
    return RedirectResponse(url=str(session["auth_url"]), status_code=302)


@app.get("/auth/callback", response_class=HTMLResponse)
def codex_auth_callback(request: FastAPIRequest):
    ok, message, status_code = codex_callback_result(dict(request.query_params))
    return codex_callback_page(ok, message, status_code=status_code)


def codex_callback_html(ok: bool, message: str, redirect_url: Optional[str] = "/") -> str:
    title = "Codex 授权完成" if ok else "Codex 授权失败"
    color = "#166534" if ok else "#b91c1c"
    escaped_message = html.escape(message)
    redirect_script = (
        f"<script>setTimeout(() => {{ window.location.href = {json.dumps(redirect_url)}; }}, 1200);</script>"
        if ok and redirect_url
        else ""
    )
    return f"""
        <!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="UTF-8" />
          <meta name="viewport" content="width=device-width,initial-scale=1" />
          <title>{title}</title>
          <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f7fa; color: #1f2937; margin: 0; padding: 32px; }}
            .card {{ max-width: 560px; margin: 60px auto; background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 8px 20px rgba(0, 0, 0, 0.08); }}
            h1 {{ color: {color}; margin: 0 0 12px; font-size: 22px; }}
            p {{ line-height: 1.6; }}
            a {{ color: #0ea5e9; }}
          </style>
        </head>
        <body>
          <div class="card">
            <h1>{title}</h1>
            <p>{escaped_message}</p>
            <p><a href="/">返回验证码页面</a></p>
          </div>
          {redirect_script}
        </body>
        </html>
        """


def codex_callback_page(ok: bool, message: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(codex_callback_html(ok, message), status_code=status_code)


@app.get("/api/codex/accounts")
def get_codex_accounts():
    with CODEX_ACCOUNTS_LOCK:
        items = [sanitize_codex_account(item) for item in CODEX_ACCOUNTS.values()]
    items.sort(key=lambda item: str(item.get("label") or item.get("id") or ""))
    return JSONResponse(content={"items": items, "updated_at": now_iso()})


@app.post("/api/codex/accounts")
async def import_codex_accounts(request: FastAPIRequest):
    body = (await request.body()).decode("utf-8", errors="replace").strip()
    if not body:
        return JSONResponse(content={"detail": "请求内容为空"}, status_code=400)

    raw_text = body
    try:
        payload = json.loads(body)
        if isinstance(payload, dict):
            raw_value = (
                payload.get("auth_json")
                or payload.get("session_json")
                or payload.get("content")
                or payload.get("data")
            )
            if isinstance(raw_value, str) and raw_value.strip():
                raw_text = raw_value.strip()
            else:
                raw_text = json.dumps(payload)
        elif isinstance(payload, str):
            raw_text = payload.strip()
    except Exception:
        pass

    try:
        imported = import_codex_auth_json(raw_text)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(content={"detail": str(e)}, status_code=400)

    return JSONResponse(content={"items": imported, "updated_at": now_iso()})


@app.post("/api/codex/accounts/{account_id}/refresh")
def refresh_codex_account(account_id: str):
    with CODEX_ACCOUNTS_LOCK:
        record = CODEX_ACCOUNTS.get(account_id)
    if not record:
        return JSONResponse(
            content={"detail": f"未找到 Codex 账号：{account_id}"},
            status_code=404,
        )

    try:
        updated = refresh_codex_account_record(record)
    except Exception as e:  # noqa: BLE001
        updated = dict(record)
        updated["status"] = "刷新失败"
        updated["error"] = str(e)
        updated["updated_at"] = now_iso()

    with CODEX_ACCOUNTS_LOCK:
        CODEX_ACCOUNTS[account_id] = updated
    save_codex_accounts()
    return JSONResponse(content={"item": sanitize_codex_account(updated), "updated_at": now_iso()})


@app.delete("/api/codex/accounts/{account_id}")
def delete_codex_account(account_id: str):
    with CODEX_ACCOUNTS_LOCK:
        existed = CODEX_ACCOUNTS.pop(account_id, None)
    if not existed:
        return JSONResponse(
            content={"detail": f"未找到 Codex 账号：{account_id}"},
            status_code=404,
        )
    save_codex_accounts()
    return JSONResponse(content={"ok": True, "account": account_id})


@app.get("/api/history/{account_name}")
def get_history(account_name: str):
    with HISTORY_LOCK:
        records = CODE_HISTORY.get(account_name, [])
        if not records:
            records = load_code_history(account_name)
            CODE_HISTORY[account_name] = records
    return JSONResponse(
        content={
            "account": account_name,
            "items": records,
        }
    )


@app.delete("/api/history/{account_name}")
def clear_history(account_name: str):
    if account_name not in CODE_HISTORY:
        return JSONResponse(
            content={"detail": f"未找到账号：{account_name}"},
            status_code=404,
        )

    clear_code_history(account_name)
    save_state(
        account_name,
        {
            "last_code": None,
            "message_id": None,
            "subject": "（历史已清空）",
            "updated_at": now_iso(),
            "status": "历史已清空",
        },
    )
    return JSONResponse(content={"ok": True, "account": account_name})


if __name__ == "__main__":
    import uvicorn

    print("启动 Gmail 双邮箱验证码监听 Web 服务（http://127.0.0.1:8000）")
    uvicorn.run(app, host="0.0.0.0", port=8000)
