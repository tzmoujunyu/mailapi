import base64
import html
import json
import os
import secrets
import string
import re
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

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

CHECK_INTERVAL_SECONDS = 3
MAX_POLL_RESULTS = 100
MAX_HISTORY_RECORDS = 30
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
DATA_DIR = "runtime"
HISTORY_DIR = os.path.join(DATA_DIR, "history")
HISTORY_ID_DIR = os.path.join(DATA_DIR, "history_id")
GMAIL_AUTH_FALLBACK = os.getenv("GMAIL_AUTH_FALLBACK", "auto").strip().lower()
GMAIL_OAUTH_REDIRECT_BASE = os.getenv("GMAIL_OAUTH_REDIRECT_BASE", "http://localhost:8000").rstrip("/")
AUTH_REMINDER_EVERY = int(os.getenv("AUTH_REMINDER_EVERY", "10"))
AUTH_LOCK = threading.Lock()
HISTORY_LOCK = threading.Lock()
AUTH_SESSION_LOCK = threading.Lock()
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    if path in {"/login", "/login.html"} or path.startswith("/oauth2callback/"):
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
        session["error"] = str(e)
        if hasattr(event, "set"):
            event.set()
        return HTMLResponse(f"Google 授权失败：{html.escape(str(e))}", status_code=400)


@app.get("/oauth2callback/{account_name}", response_class=HTMLResponse)
def oauth2_callback(account_name: str, request: FastAPIRequest):
    return complete_oauth_callback(request)


def get_gmail_service(cfg: AccountConfig):
    creds = None
    token_file = cfg["token_file"]
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        else:
            if not os.path.exists(cfg["credential_file"]):
                raise FileNotFoundError(
                    f"{cfg['name']} 的 credential 文件未找到: {cfg['credential_file']}"
                )
            with AUTH_LOCK:
                if os.path.exists(token_file):
                    creds = Credentials.from_authorized_user_file(token_file, SCOPES)
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(GoogleRequest())
                if not creds or not creds.valid:
                    print(f"[{cfg['name']}] 开始浏览器授权：{cfg['email']}（token: {token_file}）")
                    if should_use_console_auth():
                        flow = InstalledAppFlow.from_client_secrets_file(cfg["credential_file"], SCOPES)
                        creds = request_gmail_credentials(cfg, flow)
                    else:
                        session = create_auth_session(cfg)
                        creds = wait_for_auth_session(cfg, session)
                        with AUTH_SESSION_LOCK:
                            AUTH_SESSIONS.pop(cfg["name"], None)
                            AUTH_SESSIONS.pop(str(session.get("state", "")), None)
        with open(token_file, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


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


@app.get("/api/history/{account_name}")
def get_history(account_name: str):
    with HISTORY_LOCK:
        records = CODE_HISTORY.get(account_name, [])
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
