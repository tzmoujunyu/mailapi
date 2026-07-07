import os
import re
import time
import base64
import html
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


CHECK_INTERVAL_SECONDS = 8
HISTORY_FILE = "gmail_history_id.txt"
MAX_POLL_RESULTS = 100

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
SEARCH_QUERY = 'is:unread (verification OR code OR "verification code" OR 验证码 OR OTP OR one-time)'

# 更精确匹配：先尝试关键词上下文再回退到纯 6 位/4-8 位数字
CODE_PATTERNS = [
    re.compile(r'(?i)(?:验证(?:码|代码|码子)|verification code|verification|otp|one-time code)[^\d]{0,30}?(\d{4,8})'),
    re.compile(r'(?<!\d)(\d{6})(?!\d)'),
    re.compile(r'(?<!\d)(\d{4,5})(?!\d)'),
    re.compile(r'(?<!\d)(\d{7,8})(?!\d)'),
]


def get_gmail_service():
    """获取并初始化 Gmail API 服务（支持持久化 token）"""
    creds = None

    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Access Token 已过期，自动刷新中...")
            creds.refresh(Request())
        else:
            print("首次运行，启动浏览器进行 Google OAuth 授权...")
            if not os.path.exists('credentials.json'):
                raise FileNotFoundError("错误：未在当前目录下找到 'credentials.json' 文件，请先下载并放置该文件。")

            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def _decode_message_data(data):
    if not data:
        return ''
    try:
        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    except Exception:
        return ''


def _collect_message_text(payload):
    texts = []
    parts = payload.get('parts') if payload else None
    if parts:
        for part in parts:
            texts.extend(_collect_message_text(part))
            continue

    mime = (payload.get('mimeType') or '').lower()
    body_data = payload.get('body', {}).get('data')
    if body_data and (mime.startswith('text/plain') or mime.startswith('text/html')):
        raw = _decode_message_data(body_data)
        if mime.startswith('text/html'):
            raw = html.unescape(raw)
            raw = re.sub(r'<[^>]+>', ' ', raw)
        texts.append(raw)
    return texts


def extract_code(message):
    candidate_fields = [
        message.get('snippet', '') or '',
        ' '.join(_collect_message_text(message.get('payload', {}))),
        message.get('payload', {}).get('headers', [{}])[0].get('value', ''),
    ]
    text = '\n'.join(candidate_fields).strip()

    for pattern in CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)

    return None


def mark_as_read(service, message_id):
    service.users().messages().batchModify(
        userId='me',
        body={
            'ids': [message_id],
            'removeLabelIds': ['UNREAD'],
        },
    ).execute()


def process_message(service, message_id):
    message = service.users().messages().get(userId='me', id=message_id, format='full').execute()
    if 'UNREAD' not in message.get('labelIds', []):
        return None

    code = extract_code(message)
    if not code:
        return None

    mark_as_read(service, message_id)
    return code


def get_initial_history_id(service):
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)

    profile = service.users().getProfile(userId='me').execute()
    return int(profile['historyId'])


def save_history_id(history_id):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        f.write(str(history_id))


def sync_missed_unread(service):
    """在 historyId 失效或首次启动时做一次兜底扫描。"""
    results = service.users().messages().list(
        userId='me',
        q=SEARCH_QUERY,
        maxResults=20,
    ).execute()
    messages = results.get('messages', [])

    for msg in messages:
        code = process_message(service, msg['id'])
        if code:
            print(f"🔥 【从历史补扫提取验证码】: {code}")


def fetch_verification_code():
    service = get_gmail_service()
    history_id = get_initial_history_id(service)

    sync_missed_unread(service)

    print(f"📡 Gmail 监听已启动，当前 historyId: {history_id}")
    while True:
        try:
            response = service.users().history().list(
                userId='me',
                startHistoryId=history_id,
                maxResults=MAX_POLL_RESULTS,
            ).execute()

            for hist in response.get('history', []):
                for item in hist.get('messagesAdded', []):
                    msg = item.get('message', {})
                    msg_id = msg.get('id')
                    if not msg_id:
                        continue

                    code = process_message(service, msg_id)
                    if code:
                        print(f"🔥 【收到验证码】: {code}")

            if response.get('historyId'):
                history_id = int(response['historyId'])
                save_history_id(history_id)

        except HttpError as e:
            if e.resp.status in (404, 410):
                # historyId 过期，先回退为一次增量补扫
                sync_missed_unread(service)
                profile = service.users().getProfile(userId='me').execute()
                history_id = int(profile['historyId'])
                save_history_id(history_id)
            else:
                print(f"API 错误: {e}")

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    print("=================== Gmail 验证码监听程序已启动 ===================")
    fetch_verification_code()
