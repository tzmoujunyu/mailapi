from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mail_messages import IncomingMail, extract_verification_code  # noqa: E402
from mail_providers.proton import ProtonMailbox, load_proton_credentials  # noqa: E402
from mail_relay import RelayMessage, encode_message, sign_request  # noqa: E402


DEFAULT_SENDERS = "noreply@tm.openai.com,noreply@tm1.openai.com"


class RelayDeliveryError(RuntimeError):
    pass


def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def setting(values: Dict[str, str], name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or values.get(name, default).strip()


def resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def proton_incoming_mail(message: object) -> IncomingMail:
    sender = getattr(getattr(message, "sender", None), "address", "") or ""
    return IncomingMail(
        message_id=str(getattr(message, "id", "") or ""),
        sender=str(sender),
        subject=str(getattr(message, "subject", "") or ""),
        body=str(getattr(message, "body", "") or ""),
        received_at=int(getattr(message, "time", 0) or 0),
    )


def deliver_message(
    server_url: str,
    secret: str,
    message: RelayMessage,
    timeout: int,
) -> None:
    body = encode_message(message)
    timestamp = int(time.time())
    nonce = secrets.token_urlsafe(24)
    request = urllib.request.Request(
        server_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Mail-Relay-Timestamp": str(timestamp),
            "X-Mail-Relay-Nonce": nonce,
            "X-Mail-Relay-Signature": sign_request(secret, timestamp, nonce, body),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status not in {200, 201}:
                raise RelayDeliveryError(f"服务器返回 HTTP {response.status}")
    except urllib.error.HTTPError as error:
        detail = error.read(1000).decode("utf-8", errors="replace")
        try:
            detail = str(json.loads(detail).get("detail") or detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        raise RelayDeliveryError(f"服务器返回 HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RelayDeliveryError(f"无法连接中继服务器: {error.reason}") from error


def process_message(
    mailbox: ProtonMailbox,
    message: object,
    *,
    account_name: str,
    server_url: str,
    relay_secret: str,
    allowed_senders: set[str],
    timeout: int,
) -> bool:
    if getattr(message, "unread", True) is False:
        return False
    full_message = mailbox.read_message(message)
    incoming = proton_incoming_mail(full_message)
    code = extract_verification_code(incoming, allowed_senders)
    if not code:
        return False
    deliver_message(
        server_url,
        relay_secret,
        RelayMessage(
            account_name=account_name,
            code=code,
            message_id=incoming.message_id,
            subject=incoming.subject or "（无主题）",
        ),
        timeout,
    )
    mailbox.mark_as_read(incoming.message_id)
    print(
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"已转发验证码邮件 {incoming.message_id[:16]}"
    )
    return True


def scan_unread(
    mailbox: ProtonMailbox,
    limit: int,
    process_options: Dict[str, object],
) -> None:
    for message in reversed(mailbox.get_recent_messages(limit=max(1, limit))):
        process_message(mailbox, message, **process_options)


def run_relay(args: argparse.Namespace) -> int:
    config_file = Path(args.config).expanduser().resolve()
    values = read_env_file(config_file)
    server_url = args.server or setting(values, "MAIL_RELAY_URL")
    relay_secret = setting(values, "MAIL_RELAY_SECRET")
    relay_secret_file = setting(values, "MAIL_RELAY_SECRET_FILE")
    if not relay_secret and relay_secret_file:
        secret_path = Path(relay_secret_file).expanduser()
        if not secret_path.is_absolute():
            secret_path = config_file.parent / secret_path
        relay_secret = secret_path.read_text(encoding="utf-8").strip()
    account_name = args.account or setting(values, "MAIL_RELAY_ACCOUNT", "account-4")
    session_value = args.session or setting(
        values,
        "PROTON_RELAY_SESSION_FILE",
        "runtime/proton_sessions/account-4.pickle",
    )
    parsed_server = urllib.parse.urlparse(server_url)
    is_local_http = (
        parsed_server.scheme == "http"
        and (parsed_server.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
    )
    if parsed_server.scheme != "https" and not is_local_http:
        raise RuntimeError("MAIL_RELAY_URL 必须使用 HTTPS；仅 localhost 可以使用 HTTP")
    if len(relay_secret) < 32:
        raise RuntimeError("MAIL_RELAY_SECRET 未配置或长度不足 32 个字符")

    senders = {
        item.strip().lower()
        for item in setting(values, "OPENAI_CODE_SENDERS", DEFAULT_SENDERS).split(",")
        if item.strip()
    }
    poll_interval = max(1, int(setting(values, "PROTON_POLL_INTERVAL_SECONDS", "3")))
    keepalive = max(10, int(setting(values, "PROTON_KEEPALIVE_SECONDS", "300")))
    catchup_limit = max(1, int(setting(values, "PROTON_CATCHUP_LIMIT", "30")))
    http_timeout = max(3, int(setting(values, "MAIL_RELAY_TIMEOUT_SECONDS", "15")))
    mailbox = ProtonMailbox(str(resolve_project_path(session_value)))

    if args.login:
        credentials = load_proton_credentials(str(config_file))
        mailbox.login_fresh(credentials, manual_captcha=args.manual_captcha)
        print("Proton 登录成功，会话已保存。")
    else:
        mailbox.connect()

    process_options: Dict[str, object] = {
        "account_name": account_name,
        "server_url": server_url,
        "relay_secret": relay_secret,
        "allowed_senders": senders,
        "timeout": http_timeout,
    }
    print(f"Proton 本地中继已启动：{account_name} -> {server_url}")

    while True:
        try:
            scan_unread(mailbox, catchup_limit, process_options)
            message = mailbox.wait_for_new_message(
                interval=poll_interval,
                timeout=keepalive,
            )
            if message is not None:
                process_message(mailbox, message, **process_options)
        except RelayDeliveryError as error:
            print(f"中继发送失败，10 秒后重试：{error}", file=sys.stderr)
            time.sleep(10)
        except KeyboardInterrupt:
            print("Proton 本地中继已停止。")
            return 0
        except Exception as error:  # noqa: BLE001
            print(f"Proton 连接异常，5 秒后重连：{error}", file=sys.stderr)
            time.sleep(5)
            mailbox.connect()


def main() -> int:
    parser = argparse.ArgumentParser(description="在本地接收 Proton 验证码并安全转发到服务器")
    parser.add_argument("--config", default=".env", help="本地 Proton/中继配置文件")
    parser.add_argument("--server", help="服务器中继接口 URL")
    parser.add_argument("--account", help="服务器上的邮箱账号编号")
    parser.add_argument("--session", help="本地 Proton 会话文件")
    parser.add_argument("--login", action="store_true", help="先使用账号密码重新登录 Proton")
    parser.add_argument("--manual-captcha", action="store_true", help="登录时手动处理 CAPTCHA")
    args = parser.parse_args()
    try:
        return run_relay(args)
    except Exception as error:  # noqa: BLE001
        print(f"Proton 本地中继启动失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
