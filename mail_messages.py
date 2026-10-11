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


def _rule_values(rule: dict, key: str) -> list[str]:
    value = rule.get(key, [])
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _matches_text(value: str, condition: str, mode: str) -> bool:
    if not condition:
        return True
    terms = [item.strip().lower() for item in condition.split(",") if item.strip()]
    if not terms:
        return True
    text = value.lower()
    matches = [term in text for term in terms]
    return all(matches) if mode == "all" else any(matches)


def matches_mail_rule(message: "IncomingMail", rule: dict) -> bool:
    """Apply a user-facing sender/subject/body rule to a normalized message."""
    sender = message.sender.strip().lower()
    senders = {item.lower() for item in _rule_values(rule, "senders")}
    if senders and sender not in senders:
        return False
    conditions = []
    subject = str(rule.get("subject") or "").strip()
    body = str(rule.get("body") or "").strip()
    mode = "any" if str(rule.get("match_mode") or "all").lower() == "any" else "all"
    if subject:
        conditions.append(_matches_text(message.subject, subject, rule.get("text_mode", "all")))
    if body:
        conditions.append(_matches_text(html_to_text(message.body), body, rule.get("text_mode", "all")))
    return (any(conditions) if mode == "any" else all(conditions)) if conditions else bool(senders)


def extract_code_for_rule(message: "IncomingMail", rule: dict) -> Optional[str]:
    if not matches_mail_rule(message, rule):
        return None
    candidate_text = "\n".join([message.subject, html_to_text(message.body)])
    expression = str(rule.get("code_pattern") or r"(?<!\d)(\d{4,8})(?!\d)").strip()
    try:
        match = re.search(expression, candidate_text, re.IGNORECASE | re.DOTALL)
    except re.error:
        return None
    if not match:
        return None
    return match.group(1) if match.groups() else match.group(0)


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
