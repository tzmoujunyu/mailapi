from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


SIGNATURE_PREFIX = "v1="
DEFAULT_MAX_CLOCK_SKEW_SECONDS = 300
ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{4,12}$")
NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class RelayValidationError(ValueError):
    pass


class RelayAuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class RelayMessage:
    account_name: str
    code: str
    message_id: str
    subject: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "account_name": self.account_name,
            "code": self.code,
            "message_id": self.message_id,
            "subject": self.subject,
        }


def encode_message(message: RelayMessage) -> bytes:
    return json.dumps(
        message.as_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sign_request(secret: str, timestamp: int, nonce: str, body: bytes) -> str:
    if not secret:
        raise RelayAuthenticationError("邮件中继密钥未配置")
    signed = f"{timestamp}.{nonce}.".encode("ascii") + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_request(
    secret: str,
    timestamp_text: str,
    nonce: str,
    signature: str,
    body: bytes,
    *,
    now: Optional[int] = None,
    max_clock_skew_seconds: int = DEFAULT_MAX_CLOCK_SKEW_SECONDS,
) -> int:
    if not NONCE_PATTERN.fullmatch(nonce or ""):
        raise RelayAuthenticationError("邮件中继 nonce 无效")
    try:
        timestamp = int(timestamp_text)
    except (TypeError, ValueError) as error:
        raise RelayAuthenticationError("邮件中继时间戳无效") from error
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > max_clock_skew_seconds:
        raise RelayAuthenticationError("邮件中继请求已过期，请校准两端系统时间")
    expected = sign_request(secret, timestamp, nonce, body)
    if not hmac.compare_digest(expected, signature or ""):
        raise RelayAuthenticationError("邮件中继签名无效")
    return timestamp


def parse_message(value: Any) -> RelayMessage:
    if not isinstance(value, Mapping):
        raise RelayValidationError("邮件中继请求必须是 JSON 对象")

    account_name = str(value.get("account_name") or "").strip()
    code = str(value.get("code") or "").strip()
    message_id = str(value.get("message_id") or "").strip()
    subject = str(value.get("subject") or "").strip()

    if not ACCOUNT_PATTERN.fullmatch(account_name):
        raise RelayValidationError("邮件中继账号编号无效")
    if not CODE_PATTERN.fullmatch(code):
        raise RelayValidationError("邮件中继验证码格式无效")
    if not message_id or len(message_id) > 256:
        raise RelayValidationError("邮件中继 message_id 无效")
    if len(subject) > 500:
        raise RelayValidationError("邮件中继主题过长")
    return RelayMessage(
        account_name=account_name,
        code=code,
        message_id=message_id,
        subject=subject or "（无主题）",
    )


class ReplayGuard:
    def __init__(self, retention_seconds: int = DEFAULT_MAX_CLOCK_SKEW_SECONDS * 2) -> None:
        self.retention_seconds = max(1, retention_seconds)
        self._seen: Dict[str, int] = {}

    def consume(self, nonce: str, timestamp: int, *, now: Optional[int] = None) -> bool:
        current = int(time.time()) if now is None else int(now)
        cutoff = current - self.retention_seconds
        self._seen = {key: value for key, value in self._seen.items() if value >= cutoff}
        if nonce in self._seen:
            return False
        self._seen[nonce] = timestamp
        return True
