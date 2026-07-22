from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Collection, Optional


VERIFICATION_TERMS = (
    r"temporary|login|sign[ -]?in|verification|security|otp|one[ -]?time|"
    r"临时|登录|验证(?:码|代码)|动态码"
)
STRONG_SUBJECT_PATTERN = re.compile(
    rf"(?is)^(?=.*(?:chatgpt|openai))(?=.*(?:{VERIFICATION_TERMS})).*$"
)
STRONG_BODY_PATTERN = re.compile(
    rf"(?is)(?=.*(?:chatgpt|openai))(?=.*(?:{VERIFICATION_TERMS})).*"
)
CONTEXT_CODE_PATTERNS = [
    re.compile(rf"(?is)(?:{VERIFICATION_TERMS}|code)[^\d]{{0,80}}?(\d{{4,8}})"),
    re.compile(rf"(?is)(?<!\d)(\d{{4,8}})(?!\d).{{0,80}}?(?:{VERIFICATION_TERMS}|code)"),
]
BARE_CODE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{6})(?!\d)"),
    re.compile(r"(?<!\d)(\d{4,5})(?!\d)"),
    re.compile(r"(?<!\d)(\d{7,8})(?!\d)"),
]


@dataclass(frozen=True)
class IncomingMail:
    message_id: str
    sender: str
    subject: str
    body: str
    received_at: int = 0


def html_to_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"(?is)<(?:style|script)\b[^>]*>.*?</(?:style|script)>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_verification_code(
    message: IncomingMail,
    allowed_senders: Collection[str],
) -> Optional[str]:
    sender = message.sender.strip().lower()
    allowed = {item.strip().lower() for item in allowed_senders if item.strip()}
    body = html_to_text(message.body)
    strong_subject = bool(STRONG_SUBJECT_PATTERN.search(message.subject))
    strong_body = bool(STRONG_BODY_PATTERN.search(body))
    if sender not in allowed or not (strong_subject or strong_body):
        return None

    candidate_text = "\n".join([message.subject, body])
    for pattern in CONTEXT_CODE_PATTERNS:
        match = pattern.search(candidate_text)
        if match:
            return match.group(1)

    if strong_subject:
        for pattern in BARE_CODE_PATTERNS:
            match = pattern.search(candidate_text)
            if match:
                return match.group(1)
    return None
