import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import pyotp
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mailapi_shared.accounts import create_account_router
from mailapi_shared.site_accounts import SiteAccountError, SiteAccountStore
from mailapi_shared.web_security import BodyLimitMiddleware, ProxyIngress, SecurityHeadersMiddleware


class WebSecurityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("mailapi_shared.site_accounts.password_is_pwned", return_value=False))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "login.html").write_text("<!-- LOGIN_ERROR -->", encoding="utf-8")
        self.store = SiteAccountStore(root / "site_accounts.json", require_admin_mfa=True)
        self.totp = pyotp.TOTP("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
        self.password = "fixture-entry-8642"
        self.app = FastAPI()
        self.app.state.site_accounts = self.store
        self.login_failure = Mock()
        self.app.include_router(create_account_router(root, lambda: [], on_admin_failure=self.login_failure))
        self.app.add_middleware(BodyLimitMiddleware)
        self.app.add_middleware(SecurityHeadersMiddleware)

    def test_admin_otp_is_required_single_use_and_rotation_revokes_session(self):
        self.store.ensure_admin("legacy-test-password")
        with self.assertRaises(SiteAccountError):
            self.store.check_admin_setup()
        self.assertIsNone(self.store.login("admin", "legacy-test-password"))
        self.store.provision_admin("admin", self.password, self.totp.secret)
        self.store.check_admin_setup()
        with TestClient(self.app, base_url="https://testserver") as client:
            payload = {"username": "admin", "password": self.password}
            self.assertEqual(client.post("/api/auth/login", json=payload).status_code, 401)
            with patch("mailapi_shared.site_accounts.time.time", return_value=1111111109):
                payload["totp_code"] = self.totp.at(1111111109)
                response = client.post("/api/auth/login", json=payload)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Secure", response.headers["set-cookie"])
                self.assertIn("HttpOnly", response.headers["set-cookie"])
                self.assertNotIn("totp_secret", response.text)
                token = client.cookies["mailapi_session"]
                self.assertEqual(client.post("/api/auth/login", json=payload).status_code, 401)
                self.assertEqual(self.store.session_user(token)["username"], "admin")
                self.store.provision_admin("admin", "rotated-test-password", pyotp.random_base32())
                self.assertIsNone(self.store.session_user(token))
                self.assertIsNone(self.store.login("admin", self.password, payload["totp_code"]))

    def test_pre_upgrade_admin_session_and_unenrolled_manager_are_denied(self):
        legacy = SiteAccountStore(self.store.path)
        legacy.ensure_admin(self.password)
        token, user = legacy.login("admin", self.password)
        legacy.create_user(user["id"], "manager", "fixture-entry-1673", "manager")
        secure = SiteAccountStore(self.store.path, require_admin_mfa=True)
        self.assertIsNone(secure.session_user(token))
        self.assertIsNone(secure.login("manager", "fixture-entry-1673", self.totp.now()))
        secure.provision_admin("manager", "fixture-entry-1876", self.totp.secret)
        _, manager = secure.login("manager", "fixture-entry-1876", self.totp.now())
        self.assertTrue(manager["must_change_password"])

    def test_admin_login_failure_reports_only_reason_and_keeps_the_public_response(self):
        self.store.provision_admin("admin", self.password, self.totp.secret)
        now = 1111111109
        valid_code = self.totp.at(now)
        valid_window = {self.totp.at(now + offset) for offset in (-30, 0, 30)}
        wrong_code = next(f"{i:06d}" for i in range(4) if f"{i:06d}" not in valid_window)
        good = {"username": "admin", "password": self.password, "totp_code": valid_code}
        with TestClient(self.app) as client, patch("mailapi_shared.site_accounts.time.time", return_value=now):
            self.assertEqual(client.post("/api/auth/login", json={**good, "username": "unknown"}).status_code, 401)
            for payload in ({**good, "password": "incorrect"}, {**good, "totp_code": ""},
                            {**good, "totp_code": "abc"}, {**good, "totp_code": wrong_code}):
                response = client.post("/login?mode=admin", data=payload, follow_redirects=False)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.text, "用户名、密码或动态验证码错误")
            self.assertEqual(client.post("/api/auth/login", json=good).status_code, 200)
            self.assertEqual(client.post("/api/auth/login", json=good).status_code, 401)
        self.assertEqual(self.login_failure.call_args_list, [call("password_mismatch"), call("totp_missing"),
                         call("totp_bad_format"), call("totp_mismatch"), call("totp_replayed")])

    def test_account_and_ip_throttling_share_the_browser_and_json_limits(self):
        with patch.object(self.store, "login", return_value=None) as check:
            first = self.enterContext(TestClient(self.app, client=("192.0.2.1", 1)))
            second = self.enterContext(TestClient(self.app, client=("192.0.2.2", 1)))
            for _ in range(8):
                self.assertEqual(first.post("/api/auth/login", json={"username": "admin"}).status_code, 401)
            response = second.post("/login", data={"username": "ADMIN"})
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response.headers["retry-after"]), 0)
            for number in range(22):
                self.assertEqual(first.post("/api/auth/login", json={"username": str(number)}).status_code, 401)
            self.assertEqual(first.post("/api/auth/login", json={"username": "elsewhere"}).status_code, 429)
            self.assertEqual(check.call_count, 30)

    def test_body_limit_rejects_content_length_and_streamed_bodies_before_login(self):
        with TestClient(self.app) as client, patch.object(self.store, "login") as login:
            response = client.post("/login", content=b"", headers={"Content-Length": "8193"})
            self.assertEqual(response.status_code, 413)
            response = client.post("/api/auth/login", content=iter((b"a" * 8192, b"b")))
            self.assertEqual(response.status_code, 413)
            self.assertIn("no-store", response.headers["cache-control"])
            login.assert_not_called()

    def test_only_trusted_socket_peer_can_supply_public_proxy_headers(self):
        @self.app.get("/peer")
        def peer(request: Request):
            return {"scheme": request.url.scheme, "client": request.client.host}

        ingress = ProxyIngress(self.app, "https://mail.example", ["10.0.0.10"])
        forwarded = {"Host": "mail.example", "X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.42"}
        with TestClient(ingress, client=("10.0.0.11", 1)) as direct:
            self.assertEqual(direct.get("/peer", headers=forwarded).status_code, 403)
        with TestClient(ingress, client=("10.0.0.10", 1)) as proxy:
            response = proxy.get("/peer", headers=forwarded)
            self.assertEqual(response.json(), {"scheme": "https", "client": "192.0.2.42"})
            self.assertIn("max-age=", response.headers["strict-transport-security"])
            self.assertEqual(response.headers["x-frame-options"], "DENY")
            self.assertEqual(proxy.get("/peer", headers={**forwarded, "Host": "wrong.example"}).status_code, 403)
            self.assertEqual(proxy.get("/peer", headers={**forwarded, "X-Forwarded-Proto": "http"}).status_code, 403)
        with TestClient(ingress, client=("127.0.0.1", 1)) as local:
            response = local.get("/peer", headers=forwarded)
            self.assertEqual(response.json(), {"scheme": "http", "client": "127.0.0.1"})
            self.assertNotIn("strict-transport-security", response.headers)
