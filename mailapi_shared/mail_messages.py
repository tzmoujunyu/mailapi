from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Collection, Optional
from urllib.parse import urlparse


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

CONDITION_FIELDS = {"subject", "body"}
CONDITION_OPERATORS = {
    "contains",
    "not_contains",
    "regex",
    "not_regex",
    "equals",
    "starts_with",
    "ends_with",
}
MAX_MAIL_CONDITIONS = 20
MAX_CONDITION_VALUE_LENGTH = 500
CONDITION_VALUE_MODES = {"all", "any"}


def _condition_bool(value: object, *, default: bool = False) -> bool:
    """Normalize a JSON boolean without treating the string ``"false"`` as true."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError("条件的 case_sensitive 必须是布尔值")


def normalize_mail_conditions(value: object) -> list[dict]:
    """Validate and normalize advanced subject/body conditions.

    Conditions are deliberately flat.  ``condition_logic`` on the containing
    rule decides whether all or any conditions must match, while the negative
    operators provide exclusion without introducing an ambiguous expression
    language.  The legacy subject/body fields remain supported separately.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("高级条件必须是数组")
    if len(value) > MAX_MAIL_CONDITIONS:
        raise ValueError(f"高级条件最多 {MAX_MAIL_CONDITIONS} 条")

    normalized: list[dict] = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 条高级条件格式无效")
        field = str(raw.get("field") or "").strip().lower()
        if field not in CONDITION_FIELDS:
            raise ValueError(f"第 {index} 条高级条件字段无效")
        operator = str(raw.get("operator") or "contains").strip().lower()
        if operator not in CONDITION_OPERATORS:
            raise ValueError(f"第 {index} 条高级条件运算符无效")
        condition_value = str(raw.get("value") or "").strip()
        if not condition_value:
            raise ValueError(f"第 {index} 条高级条件内容不能为空")
        if len(condition_value) > MAX_CONDITION_VALUE_LENGTH:
            raise ValueError(
                f"第 {index} 条高级条件内容最多 {MAX_CONDITION_VALUE_LENGTH} 个字符"
            )
        case_sensitive = _condition_bool(raw.get("case_sensitive"), default=False)
        value_mode = str(raw.get("value_mode") or "all").strip().lower()
        if value_mode not in CONDITION_VALUE_MODES:
            raise ValueError(f"第 {index} 条高级条件关键词关系无效")
        if operator in {"regex", "not_regex"}:
            try:
                re.compile(
                    condition_value,
                    0 if case_sensitive else re.IGNORECASE,
                )
            except re.error as error:
                raise ValueError(f"第 {index} 条高级条件正则无效：{error}") from error
        normalized.append(
            {
                "field": field,
                "operator": operator,
                "value": condition_value,
                "case_sensitive": case_sensitive,
                "value_mode": value_mode,
            }
        )
    return normalized


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


def _matches_condition(value: str, condition: dict) -> bool:
    """Evaluate one normalized text condition against a message field."""
    operator = condition["operator"]
    expected = condition["value"]
    case_sensitive = bool(condition.get("case_sensitive"))
    candidate = value or ""
    if not case_sensitive:
        candidate = candidate.lower()
        expected = expected.lower()

    if operator in {"contains", "not_contains"}:
        terms = [item.strip() for item in expected.split(",") if item.strip()]
        if not terms:
            return True
        matches = [term in candidate for term in terms]
        if operator == "contains":
            return any(matches) if condition.get("value_mode") == "any" else all(matches)
        return not any(matches)
    if operator == "equals":
        return candidate == expected
    if operator == "starts_with":
        return candidate.startswith(expected)
    if operator == "ends_with":
        return candidate.endswith(expected)

    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        matched = bool(re.search(condition["value"], value or "", flags))
    except re.error:
        # API normalization rejects invalid expressions.  Keep this helper
        # safe for callers that construct an in-memory rule directly.
        matched = False
    return not matched if operator == "not_regex" else matched


def _matches_advanced_conditions(message: "IncomingMail", rule: dict) -> Optional[bool]:
    """Return advanced-condition result, or ``None`` when none were supplied."""
    raw_conditions = rule.get("conditions")
    if not raw_conditions:
        return None
    try:
        conditions = normalize_mail_conditions(raw_conditions)
    except ValueError:
        return False
    values = {
        "subject": message.subject or "",
        "body": html_to_text(message.body),
    }
    results = [
        _matches_condition(values[item["field"]], item)
        for item in conditions
    ]
    if not results:
        return None
    logic = str(rule.get("condition_logic") or "all").lower()
    return any(results) if logic == "any" else all(results)


def matches_mail_rule(message: "IncomingMail", rule: dict) -> bool:
    """Apply a user-facing sender/subject/body rule to a normalized message."""
    sender = message.sender.strip().lower()
    sender_mode = str(rule.get("sender_mode") or "exact").lower()
    senders = {item.lower() for item in _rule_values(rule, "senders")}
    sender_pattern = str(rule.get("sender_pattern") or "").strip()
    if sender_mode == "regex" and sender_pattern:
        try:
            sender_matches = bool(re.fullmatch(sender_pattern, sender, re.IGNORECASE))
        except re.error:
            sender_matches = False
    elif sender_mode == "domain" and senders:
        sender_matches = sender.rpartition("@")[2] in senders
    else:
        sender_matches = sender in senders
    if not sender_matches:
        return False

    advanced_match = _matches_advanced_conditions(message, rule)
    if advanced_match is not None:
        return advanced_match

    conditions = []
    subject = str(rule.get("subject") or "").strip()
    body = str(rule.get("body") or "").strip()
    mode = "any" if str(rule.get("match_mode") or "all").lower() == "any" else "all"
    if subject:
        conditions.append(_matches_text(message.subject, subject, rule.get("text_mode", "all")))
    if body:
        conditions.append(_matches_text(html_to_text(message.body), body, rule.get("text_mode", "all")))
    return (any(conditions) if mode == "any" else all(conditions)) if conditions else bool(senders or sender_pattern)


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


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href: Optional[str] = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.text)))
            self.href = None


def extract_link_for_rule(message: "IncomingMail", rule: dict) -> Optional[str]:
    allowed_hosts = {item.lower().lstrip(".") for item in _rule_values(rule, "allowed_hosts")}
    if not allowed_hosts:
        return None
    anchor_pattern = str(rule.get("link_text_pattern") or r"(?i)sign\s*in|login|continue|verify").strip()
    try:
        matcher = re.compile(anchor_pattern, re.IGNORECASE)
    except re.error:
        return None
    parser = _LinkParser()
    parser.feed(message.body)
    if not parser.links:
        parser.links = [
            (match.group(1), match.group(1))
            for match in re.finditer(r"(?i)(https://[^\s<>\"']+)", message.body)
        ]
    candidates = set()
    for href, text in parser.links:
        href = href.strip()
        try:
            parsed = urlparse(href)
            if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
                    or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or re.search(r"[\x00-\x20\x7f\\]", href)):
                continue
        except ValueError:
            continue
        if matcher.search(html_to_text(text)) or (text == href and matcher.search(href)):
            candidates.add(href)
    # Never choose an arbitrary destination when multiple login buttons disagree.
    return next(iter(candidates)) if len(candidates) == 1 else None


def extract_mail_result(message: "IncomingMail", rule: dict) -> Optional[dict]:
    if not matches_mail_rule(message, rule):
        return None
    extraction_type = str(rule.get("extract_type") or "code").lower()
    if extraction_type in {"link", "auto"}:
        link = extract_link_for_rule(message, rule)
        if link:
            return {"type": "link", "value": link}
        if extraction_type == "link":
            return None
    code = extract_code_for_rule(message, rule)
    return {"type": "code", "value": code} if code else None


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
