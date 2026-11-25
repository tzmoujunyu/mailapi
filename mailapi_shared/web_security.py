"""Limits and ingress checks for the web application."""
import asyncio
import ipaddress
import math
import threading
import time
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


LOGIN_WINDOW = 300
LOGIN_ATTEMPTS_PER_ACCOUNT = 8
LOGIN_ATTEMPTS_PER_IP = 30
MAX_LOGIN_BUCKETS = 10_000
SMALL_BODY_LIMIT = 8192
BODY_LIMIT = 1024 * 1024
FINANCE_BODY_LIMIT = 22 * 1024 * 1024


class LoginThrottle:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets = {}

    def check(self, address, username):
        name = username.strip().lower()[:64] if isinstance(username, str) else ""
        limits = (("ip", address, LOGIN_ATTEMPTS_PER_IP), ("user", name, LOGIN_ATTEMPTS_PER_ACCOUNT))
        now = time.monotonic()
        with self._lock:
            pending = {}
            for kind, value, limit in limits:
                key = (kind, value)
                expires, count = self._buckets.get(key, (now, 0))
                if expires <= now:
                    expires, count = now + LOGIN_WINDOW, 0
                if count >= limit:
                    return max(1, math.ceil(expires - now))
                pending[key] = (expires, count + 1)
            if len(self._buckets) + len(pending) > MAX_LOGIN_BUCKETS:
                self._buckets = {key: bucket for key, bucket in self._buckets.items() if bucket[0] > now}
                if len(self._buckets.keys() | pending.keys()) > MAX_LOGIN_BUCKETS:
                    return LOGIN_WINDOW
            self._buckets.update(pending)
        return 0


def same_origin(origin, scheme, host):
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme == scheme and parsed.netloc.lower() == host.lower()
                and not (parsed.path or parsed.query or parsed.fragment))
    except ValueError:
        return False


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        limit = SMALL_BODY_LIMIT if scope["path"] in {"/login", "/api/auth/login"} else BODY_LIMIT
        if scope["path"] == "/api/admin/codex-tip-images":
            limit = 9 * 1024 * 1024
        elif (scope["path"].startswith(("/api/finance/applications", "/api/finance/drafts/")) or scope["path"] == "/api/admin/finance/applications") and scope["method"] in {"POST", "PUT"}:
            # Two 10 MB files plus multipart headers and form fields.
            limit = FINANCE_BODY_LIMIT
        length = Headers(scope=scope).get("content-length")
        if length is not None:
            try:
                size = int(length)
                if size < 0:
                    raise ValueError
            except ValueError:
                await JSONResponse({"detail": "请求长度无效"}, 400)(scope, receive, send)
                return
            if size > limit:
                await JSONResponse({"detail": "请求内容过长"}, 413)(scope, receive, send)
                return
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        await JSONResponse({"detail": "请求内容过长"}, 413)(scope, receive, send)
                        return
                    body.extend(chunk)
                    if not message.get("more_body"):
                        break
        except TimeoutError:
            await JSONResponse({"detail": "请求读取超时"}, 408)(scope, receive, send)
            return

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                data = bytes(body)
                body.clear()
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
                headers["Pragma"] = "no-cache"
                headers["X-Content-Type-Options"] = "nosniff"
                path = scope["path"]
                callback = path.endswith("/callback") or path.startswith("/oauth2callback/") or (
                    path == "/" and scope.get("query_string")
                )
                headers["Referrer-Policy"] = "no-referrer" if callback else "same-origin"
                headers["X-Frame-Options"] = "DENY"
                headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
                if scope["scheme"] == "https":
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ProxyIngress:
    """Check the socket peer before accepting any forwarded headers."""

    def __init__(self, app, public_url, trusted_proxies):
        parsed = urlsplit(public_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("APP_BASE_URL 必须是 HTTPS 网站根地址")
        self.public_host = parsed.netloc.lower()
        self.trusted_proxies = {str(ipaddress.ip_address(value)) for value in trusted_proxies}
        if not self.trusted_proxies:
            raise ValueError("必须配置可信反代 IP")
        self.app = app
        self.forwarded_app = ProxyHeadersMiddleware(app, trusted_hosts=list(self.trusted_proxies))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        peer = client[0] if client else ""
        if peer in self.trusted_proxies:
            headers = Headers(scope=scope)
            if headers.get("host", "").lower() != self.public_host or headers.get("x-forwarded-proto") != "https":
                await JSONResponse({"detail": "反向代理必须转发站点域名和 HTTPS 协议"}, 403)(scope, receive, send)
                return
            await self.forwarded_app(scope, receive, send)
            return
        if peer in {"127.0.0.1", "::1"}:
            await self.app(scope, receive, send)
            return
        await JSONResponse({"detail": "源站仅接受受信任的反向代理连接"}, 403)(scope, receive, send)
