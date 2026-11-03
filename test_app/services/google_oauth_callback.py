from __future__ import annotations

import urllib.parse
from dataclasses import dataclass


MAX_CALLBACK_URL_LENGTH = 8192


class GoogleCallbackLinkError(ValueError):
    pass


@dataclass(frozen=True)
class GoogleCallbackLink:
    state: str
    query: str


def parse_google_callback_link(value: str) -> GoogleCallbackLink:
    callback_url = (value or "").strip()
    if not callback_url:
        raise GoogleCallbackLinkError("请粘贴 Google 授权返回链接")
    if len(callback_url) > MAX_CALLBACK_URL_LENGTH:
        raise GoogleCallbackLinkError("Google 授权返回链接过长")

    parsed = urllib.parse.urlsplit(callback_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise GoogleCallbackLinkError("请粘贴浏览器地址栏中的完整 http/https 返回链接")
    if not parsed.query:
        raise GoogleCallbackLinkError("返回链接缺少授权参数")

    values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    states = values.get("state", [])
    if len(states) != 1 or not states[0]:
        raise GoogleCallbackLinkError("返回链接中的 state 无效")
    if not values.get("code") and not values.get("error"):
        raise GoogleCallbackLinkError("返回链接缺少 code 或 error 参数")
    return GoogleCallbackLink(state=states[0], query=parsed.query)


def authorization_response_for_redirect(callback: GoogleCallbackLink, redirect_uri: str) -> str:
    parsed_redirect = urllib.parse.urlsplit((redirect_uri or "").strip())
    if parsed_redirect.scheme.lower() not in {"http", "https"} or not parsed_redirect.netloc:
        raise GoogleCallbackLinkError("Google OAuth 回调地址配置无效")
    return urllib.parse.urlunsplit(
        (
            parsed_redirect.scheme,
            parsed_redirect.netloc,
            parsed_redirect.path,
            callback.query,
            "",
        )
    )
