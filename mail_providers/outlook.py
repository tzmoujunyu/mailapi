from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mail_messages import IncomingMail


OUTLOOK_SCOPES = ("User.Read", "Mail.ReadWrite")
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class OutlookConfigurationError(RuntimeError):
    pass


class OutlookAuthorizationRequired(RuntimeError):
    pass


class OutlookAPIError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(f"Microsoft Graph HTTP {status}: {detail}")


@dataclass(frozen=True)
class OutlookSettings:
    client_id: str
    client_secret: str
    authority: str
    redirect_uri: str


def load_outlook_settings(default_redirect_uri: str = "") -> OutlookSettings:
    client_id = os.getenv("OUTLOOK_CLIENT_ID", "").strip()
    client_secret = os.getenv("OUTLOOK_CLIENT_SECRET", "").strip()
    tenant = os.getenv("OUTLOOK_TENANT", "common").strip() or "common"
    authority = (
        os.getenv("OUTLOOK_AUTHORITY", "").strip()
        or f"https://login.microsoftonline.com/{tenant}"
    ).rstrip("/")
    redirect_uri = os.getenv("OUTLOOK_OAUTH_REDIRECT_URI", "").strip() or default_redirect_uri

    if not client_id:
        raise OutlookConfigurationError("缺少 OUTLOOK_CLIENT_ID")
    if not client_secret:
        raise OutlookConfigurationError("缺少 OUTLOOK_CLIENT_SECRET")
    if not redirect_uri:
        raise OutlookConfigurationError("缺少 OUTLOOK_OAUTH_REDIRECT_URI")

    parsed = urllib.parse.urlsplit(redirect_uri)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise OutlookConfigurationError("OUTLOOK_OAUTH_REDIRECT_URI 必须是完整的 http/https 地址")
    if parsed.scheme.lower() != "https" and (parsed.hostname or "").lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise OutlookConfigurationError("Outlook 的非本地 OAuth 回调必须使用 HTTPS")

    return OutlookSettings(
        client_id=client_id,
        client_secret=client_secret,
        authority=authority,
        redirect_uri=redirect_uri,
    )


def _msal_types() -> tuple:
    try:
        from msal import ConfidentialClientApplication, SerializableTokenCache
    except ImportError as error:
        raise OutlookConfigurationError(
            "缺少 msal，请在 gmail conda 环境安装 requirements.txt"
        ) from error
    return ConfidentialClientApplication, SerializableTokenCache


class OutlookAuthorization:
    def __init__(self, settings: OutlookSettings, token_file: str) -> None:
        ConfidentialClientApplication, SerializableTokenCache = _msal_types()
        self.settings = settings
        self.token_file = token_file
        self.cache = SerializableTokenCache()
        token_path = Path(token_file)
        if token_path.is_file():
            self.cache.deserialize(token_path.read_text(encoding="utf-8"))
        self.app = ConfidentialClientApplication(
            settings.client_id,
            authority=settings.authority,
            client_credential=settings.client_secret,
            token_cache=self.cache,
        )

    def start(self, state: str) -> Dict[str, Any]:
        flow = self.app.initiate_auth_code_flow(
            scopes=list(OUTLOOK_SCOPES),
            redirect_uri=self.settings.redirect_uri,
            state=state,
            prompt="select_account",
            response_mode="form_post",
        )
        if "auth_uri" not in flow:
            detail = flow.get("error_description") or flow.get("error") or "无法创建授权链接"
            raise OutlookConfigurationError(str(detail))
        return flow

    def complete(self, flow: Dict[str, Any], response: Dict[str, str]) -> str:
        try:
            result = self.app.acquire_token_by_auth_code_flow(flow, response)
        except ValueError as error:
            raise OutlookAuthorizationRequired("Outlook 授权 state 校验失败") from error
        access_token = str(result.get("access_token") or "").strip()
        if not access_token:
            detail = (
                result.get("error_description")
                or result.get("error")
                or "Microsoft 未返回 access token"
            )
            raise OutlookAuthorizationRequired(str(detail))
        return access_token

    def acquire_silent(self, expected_email: str = "") -> str:
        accounts = self.app.get_accounts(username=expected_email or None)
        if not accounts and expected_email:
            accounts = self.app.get_accounts()
        if not accounts:
            raise OutlookAuthorizationRequired("Outlook token 不存在或已失效，请重新授权")
        result = self.app.acquire_token_silent(list(OUTLOOK_SCOPES), account=accounts[0])
        access_token = str((result or {}).get("access_token") or "").strip()
        if not access_token:
            detail = (
                (result or {}).get("error_description")
                or (result or {}).get("error")
                or "Outlook token 不存在或已失效，请重新授权"
            )
            raise OutlookAuthorizationRequired(str(detail))
        return access_token

    def serialized_cache(self) -> Dict[str, Any]:
        value = json.loads(self.cache.serialize())
        if not isinstance(value, dict):
            raise OutlookAuthorizationRequired("Outlook token cache 格式无效")
        return value


def graph_request(
    access_token: str,
    path: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    url = path if path.startswith("https://") else f"{GRAPH_BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Prefer": 'outlook.body-content-type="text"',
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
            detail = payload.get("error", {}).get("message") or error.reason
        except Exception:
            detail = raw.decode("utf-8", errors="replace") or str(error.reason)
        raise OutlookAPIError(error.code, str(detail)) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Microsoft Graph 连接失败：{error.reason}") from error
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def fetch_outlook_profile(access_token: str, timeout: int = 30) -> Dict[str, str]:
    query = urllib.parse.urlencode({"$select": "id,mail,userPrincipalName,displayName"})
    value = graph_request(access_token, f"/me?{query}", timeout=timeout)
    email = str(value.get("mail") or value.get("userPrincipalName") or "").strip()
    if not email:
        raise OutlookAuthorizationRequired("Microsoft Graph 未返回 Outlook 邮箱地址")
    return {
        "id": str(value.get("id") or ""),
        "email": email,
        "display_name": str(value.get("displayName") or ""),
    }


def outlook_incoming_mail(message: Dict[str, Any]) -> IncomingMail:
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    address = (
        sender.get("emailAddress")
        if isinstance(sender.get("emailAddress"), dict)
        else {}
    )
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    return IncomingMail(
        message_id=str(message.get("id") or ""),
        sender=str(address.get("address") or ""),
        subject=str(message.get("subject") or ""),
        body=str(body.get("content") or ""),
    )


class OutlookMailbox:
    def __init__(self, settings: OutlookSettings, token_file: str, email: str) -> None:
        self.authorization = OutlookAuthorization(settings, token_file)
        self.email = email
        self.access_token = ""

    def connect(self) -> Dict[str, str]:
        self.access_token = self.authorization.acquire_silent(self.email)
        return fetch_outlook_profile(self.access_token)

    def save_refreshed_cache(self, writer: Any) -> None:
        if self.authorization.cache.has_state_changed:
            writer(self.authorization.serialized_cache())

    def get_unread_messages(self, limit: int = 30) -> List[Dict[str, Any]]:
        self.access_token = self.authorization.acquire_silent(self.email)
        query = urllib.parse.urlencode(
            {
                "$filter": "isRead eq false",
                "$top": str(max(1, min(limit, 100))),
                "$select": "id,subject,body,from,receivedDateTime,isRead",
            }
        )
        value = graph_request(
            self.access_token,
            f"/me/mailFolders/inbox/messages?{query}",
        )
        messages = value.get("value")
        if not isinstance(messages, list):
            return []
        return [item for item in messages if isinstance(item, dict)]

    def mark_as_read(self, message_id: str) -> None:
        encoded_id = urllib.parse.quote(message_id, safe="")
        graph_request(
            self.access_token,
            f"/me/messages/{encoded_id}",
            method="PATCH",
            body={"isRead": True},
        )
