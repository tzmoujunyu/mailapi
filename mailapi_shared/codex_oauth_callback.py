from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Dict, Optional


MAX_CALLBACK_URL_LENGTH = 8192


class CodexCallbackLinkError(ValueError):
    pass


@dataclass(frozen=True)
class CodexCallbackLink:
    state: str
    params: Dict[str, str]


def parse_codex_callback_link(value: str) -> CodexCallbackLink:
    callback_url = (value or "").strip()
    if not callback_url:
        raise CodexCallbackLinkError("请粘贴 Codex 授权返回链接")
    if len(callback_url) > MAX_CALLBACK_URL_LENGTH:
        raise CodexCallbackLinkError("Codex 授权返回链接过长")

    parsed = urllib.parse.urlsplit(callback_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise CodexCallbackLinkError("请粘贴浏览器地址栏中的完整 http/https 返回链接")
    if not parsed.query:
        raise CodexCallbackLinkError("返回链接缺少授权参数")

    values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for key in ("state", "code", "error", "error_description"):
        if len(values.get(key, [])) > 1:
            raise CodexCallbackLinkError(f"返回链接中的 {key} 参数重复")

    states = values.get("state", [])
    if len(states) != 1 or not states[0]:
        raise CodexCallbackLinkError("返回链接中的 state 无效")
    if not values.get("code") and not values.get("error"):
        raise CodexCallbackLinkError("返回链接缺少 code 或 error 参数")

    params = {
        key: items[0]
        for key, items in values.items()
        if items and key in {"state", "code", "error", "error_description"}
    }
    return CodexCallbackLink(state=states[0], params=params)


def validate_codex_account_email(expected_email: Optional[str], actual_email: Optional[str]) -> None:
    expected = (expected_email or "").strip().lower()
    if not expected:
        return

    actual = (actual_email or "").strip().lower()
    if not actual:
        raise CodexCallbackLinkError(
            f"无法从授权 token 识别邮箱，未覆盖目标账号 {expected_email}"
        )
    if actual != expected:
        raise CodexCallbackLinkError(
            f"授权账号 {actual_email} 与目标账号 {expected_email} 不一致，未覆盖原授权"
        )
