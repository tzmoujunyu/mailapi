"""Server-side verification for the on-demand login challenge."""
import hashlib
from http.client import HTTPException
import json
import math
import threading
import time
from typing import NamedTuple
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .web_security import LOGIN_WINDOW, LOGIN_ATTEMPTS_PER_ACCOUNT, LOGIN_ATTEMPTS_PER_IP, MAX_LOGIN_BUCKETS


TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TEST_SITE_KEY = "1x00000000000000000000AA"
TEST_SECRET_KEY = "1x0000000000000000000000000000000AA"


class ChallengeUnavailable(Exception):
    pass


class LoginAttempt(NamedTuple):
    buckets: tuple
    retry_after: int
    challenge_required: bool


class ProtectedLoginThrottle:
    """Count failed credentials, reserving in-flight attempts under one lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buckets = {}

    @staticmethod
    def _keys(address, username):
        name = username.strip().lower()[:64] if isinstance(username, str) else ""
        return (("user", name), ("ip", address))

    def begin(self, address, username):
        now = time.monotonic()
        keys = self._keys(address, username)
        with self._lock:
            if len(self._buckets) + 2 > MAX_LOGIN_BUCKETS:
                self._buckets = {key: bucket for key, bucket in self._buckets.items() if bucket[0] > now}
                if len(self._buckets.keys() | set(keys)) > MAX_LOGIN_BUCKETS:
                    return LoginAttempt((), LOGIN_WINDOW, False)
            buckets = []
            for key in keys:
                bucket = self._buckets.get(key)
                if bucket is None or bucket[0] <= now:
                    bucket = [now + LOGIN_WINDOW, 0, 0]  # expiry, failures, in-flight
                    self._buckets[key] = bucket
                buckets.append(bucket)
            account, ip = buckets
            if account[1] + account[2] >= LOGIN_ATTEMPTS_PER_ACCOUNT:
                return LoginAttempt((), max(1, math.ceil(account[0] - now)), False)
            required = account[1] >= 3 or ip[1] >= LOGIN_ATTEMPTS_PER_IP
            for bucket in buckets:
                bucket[2] += 1
            return LoginAttempt(tuple(buckets), 0, required)

    def finish(self, attempt, authenticated):
        if not attempt.buckets:
            return
        with self._lock:
            account, ip = attempt.buckets
            for bucket in attempt.buckets:
                bucket[2] -= 1
            if authenticated is True:
                account[1] = 0
            elif authenticated is False:
                account[1] = min(LOGIN_ATTEMPTS_PER_ACCOUNT, account[1] + 1)
                ip[1] = min(LOGIN_ATTEMPTS_PER_IP, ip[1] + 1)

    def challenge_required(self, address, username):
        now = time.monotonic()
        with self._lock:
            return any(
                bucket[0] > now and bucket[1] >= limit
                for key, limit in zip(self._keys(address, username), (3, LOGIN_ATTEMPTS_PER_IP))
                if (bucket := self._buckets.get(key)) is not None
            )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class TurnstileVerifier:
    def __init__(self, site_key, secret_key, hostname, *, test_mode=False):
        if not site_key or not secret_key or not hostname:
            raise ValueError("启用登录验证必须同时配置网站密钥、服务端密钥和网站地址")
        dummy = site_key.startswith(("1x000000", "2x000000", "3x000000")) or secret_key.startswith(("1x000000", "2x000000", "3x000000"))
        self.testing = (site_key, secret_key) == (TEST_SITE_KEY, TEST_SECRET_KEY)
        if dummy and (not test_mode or not self.testing):
            raise ValueError("演示验证密钥只能成对用于隔离测试版")
        self.site_key = site_key
        self._secret_key = secret_key
        self._hostname = hostname.lower()
        self._lock = threading.Lock()
        self._used = {}
        self._slots = threading.BoundedSemaphore(4)

    def verify(self, token, address):
        if not isinstance(token, str) or not token or len(token) > 2048 or not token.isascii():
            return False
        if not self._slots.acquire(blocking=False):
            raise ChallengeUnavailable from None
        try:
            # Reserve tokens before the network request: simultaneous redemptions
            # can otherwise pass during the provider's propagation delay.
            digest = hashlib.sha256(token.encode()).digest()
            now = time.monotonic()
            with self._lock:
                self._used = {key: expiry for key, expiry in self._used.items() if expiry > now}
                if digest in self._used and not (self.testing and token == "XXXX.DUMMY.TOKEN.XXXX"):
                    return False
                if len(self._used) >= 10_000:
                    raise ChallengeUnavailable
                self._used[digest] = now + 300
            payload = urlencode({"secret": self._secret_key, "response": token, "remoteip": address}).encode()
            request = Request(TURNSTILE_URL, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                # A fixed HTTPS destination, no redirects or environment proxy,
                # bounded response and timeout. Never log a token or the secret.
                with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=5) as response:
                    raw = response.read(8193)
                if len(raw) > 8192:
                    raise ValueError
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError
            except (OSError, URLError, HTTPException, ValueError, RecursionError):
                raise ChallengeUnavailable from None
            if result.get("success") is not True:
                errors = result.get("error-codes")
                if isinstance(errors, list) and any(code in ("internal-error", "missing-input-secret", "invalid-input-secret") for code in errors):
                    raise ChallengeUnavailable
                return False
            if self.testing:
                return result.get("hostname") == "example.com"
            hostname = result.get("hostname")
            return isinstance(hostname, str) and hostname.lower() == self._hostname and result.get("action") == "login"
        finally:
            self._slots.release()
