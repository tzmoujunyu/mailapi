import json
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from concurrent.futures import ThreadPoolExecutor

from mailapi_shared.accounts import create_account_router
from mailapi_shared.web_security import BodyLimitMiddleware, SecurityHeadersMiddleware

from mailapi_shared.login_protection import (
    ChallengeUnavailable, ProtectedLoginThrottle, TEST_SECRET_KEY, TEST_SITE_KEY, TurnstileVerifier,
)


class TurnstileVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = TurnstileVerifier("public-site-key", "private-secret", "mail.example")
        self.opener = self.enterContext(patch("mailapi_shared.login_protection.build_opener")).return_value
        self.response = self.opener.open.return_value.__enter__.return_value
        self.result = {"success": True, "hostname": "mail.example", "action": "login"}
        self.response.read.side_effect = lambda limit: json.dumps(self.result).encode()[:limit]

    def test_validates_at_fixed_https_destination_and_only_sends_required_fields(self):
        self.assertTrue(self.verifier.verify("challenge-token", "192.0.2.1"))
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://challenges.cloudflare.com/turnstile/v0/siteverify")
        self.assertEqual(parse_qs(request.data.decode()), {
            "secret": ["private-secret"], "response": ["challenge-token"], "remoteip": ["192.0.2.1"],
        })
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 5})
        self.response.read.assert_called_once_with(8193)

    def test_replay_is_rejected_locally_even_if_provider_would_accept_it(self):
        self.assertTrue(self.verifier.verify("single-use", "192.0.2.1"))
        self.assertFalse(self.verifier.verify("single-use", "192.0.2.2"))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_simultaneous_redemptions_are_reserved_before_the_provider_response(self):
        started, release = threading.Event(), threading.Event()

        def read(limit):
            started.set()
            self.assertTrue(release.wait(2))
            return json.dumps(self.result).encode()

        self.response.read.side_effect = read
        with ThreadPoolExecutor(max_workers=2) as workers:
            pending = workers.submit(self.verifier.verify, "concurrent-token", "192.0.2.1")
            try:
                self.assertTrue(started.wait(2))
                self.assertFalse(self.verifier.verify("concurrent-token", "192.0.2.2"))
            finally:
                release.set()
            self.assertTrue(pending.result(timeout=2))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_invalid_payload_hostname_action_and_failure_responses_are_denied(self):
        for value in (None, 1, "", "x" * 2049, "\ud800"):
            self.assertFalse(self.verifier.verify(value, "192.0.2.1"))
        self.opener.open.assert_not_called()
        for number, result in enumerate((
            {**self.result, "hostname": "attacker.example"}, {**self.result, "hostname": None},
            {**self.result, "action": "signup"}, {**self.result, "success": 1},
            {"success": False, "error-codes": ["timeout-or-duplicate"]},
            {"success": False, "error-codes": ["invalid-input-response"]},
        )):
            self.result = result
            self.assertFalse(self.verifier.verify(f"token-{number}", "192.0.2.1"))

    def test_provider_errors_and_oversized_or_invalid_responses_are_unavailable(self):
        for number, body in enumerate((b"x" * 8193, b"not json", b"[]", b"null")):
            self.response.read.side_effect = None
            self.response.read.return_value = body
            with self.assertRaises(ChallengeUnavailable):
                self.verifier.verify(f"bad-response-{number}", "192.0.2.1")
        self.opener.open.side_effect = URLError("sensitive upstream detail")
        with self.assertRaises(ChallengeUnavailable) as error:
            self.verifier.verify("network-failure", "192.0.2.1")
        self.assertEqual(str(error.exception), "")

    def test_config_failure_and_resource_limits_do_not_reach_password_validation(self):
        self.result = {"success": False, "error-codes": ["invalid-input-secret"]}
        with self.assertRaises(ChallengeUnavailable):
            self.verifier.verify("config-failure", "192.0.2.1")
        self.verifier._slots = MagicMock()
        self.verifier._slots.acquire.return_value = False
        with self.assertRaises(ChallengeUnavailable):
            self.verifier.verify("busy", "192.0.2.1")
        self.verifier._slots.release.assert_not_called()

    def test_demo_keys_require_explicit_test_mode_and_never_relax_real_key_validation(self):
        with self.assertRaises(ValueError):
            TurnstileVerifier(TEST_SITE_KEY, TEST_SECRET_KEY, "mail.example")
        with self.assertRaises(ValueError):
            TurnstileVerifier(TEST_SITE_KEY, "private-secret", "localhost", test_mode=True)
        demo = TurnstileVerifier(TEST_SITE_KEY, TEST_SECRET_KEY, "localhost", test_mode=True)
        self.result = {"success": True, "hostname": "example.com"}
        self.assertTrue(demo.verify("XXXX.DUMMY.TOKEN.XXXX", "127.0.0.1"))
        self.assertTrue(demo.verify("XXXX.DUMMY.TOKEN.XXXX", "127.0.0.1"))
        self.assertFalse(self.verifier.verify("real-token", "192.0.2.1"))
        for key in ("", None):
            with self.assertRaises(ValueError):
                TurnstileVerifier(key, "private-secret", "mail.example")


class ProtectedLoginThrottleTests(unittest.TestCase):
    def setUp(self):
        self.clock = self.enterContext(patch("mailapi_shared.login_protection.time.monotonic", return_value=100))
        self.throttle = ProtectedLoginThrottle()

    def fail(self, username="alice", address="192.0.2.1"):
        attempt = self.throttle.begin(address, username)
        self.throttle.finish(attempt, False)
        return attempt

    def test_three_failures_require_challenge_and_eight_lock_across_addresses(self):
        for _ in range(3):
            self.assertFalse(self.fail().challenge_required)
        self.assertTrue(self.throttle.challenge_required("192.0.2.2", " ALICE "))
        for _ in range(5):
            self.assertTrue(self.fail(address="192.0.2.2").challenge_required)
        self.assertEqual(self.throttle.begin("192.0.2.3", "alice").retry_after, 300)
        self.clock.return_value = 401
        self.assertFalse(self.throttle.begin("192.0.2.3", "alice").challenge_required)

    def test_shared_ip_challenges_but_does_not_block_other_users(self):
        for number in range(30):
            self.fail(username=f"user-{number}")
        attempt = self.throttle.begin("192.0.2.1", "legitimate-user")
        self.assertEqual(attempt.retry_after, 0)
        self.assertTrue(attempt.challenge_required)
        self.throttle.finish(attempt, True)
        self.assertFalse(self.throttle.challenge_required("192.0.2.2", "legitimate-user"))
        self.assertTrue(self.throttle.challenge_required("192.0.2.1", "another-user"))

    def test_success_resets_account_failures_and_challenge_failures_do_not_lock_it(self):
        for _ in range(3):
            self.fail()
        for _ in range(20):
            attempt = self.throttle.begin("192.0.2.1", "alice")
            self.assertEqual(attempt.retry_after, 0)
            self.throttle.finish(attempt, None)
        attempt = self.throttle.begin("192.0.2.1", "alice")
        self.throttle.finish(attempt, True)
        self.assertFalse(self.throttle.challenge_required("192.0.2.1", "alice"))
        for _ in range(40):
            attempt = self.throttle.begin("192.0.2.1", "alice")
            self.assertEqual(attempt.retry_after, 0)
            self.throttle.finish(attempt, True)

    def test_concurrent_attempts_cannot_exceed_account_password_budget(self):
        with ThreadPoolExecutor(max_workers=12) as workers:
            attempts = list(workers.map(lambda unused: self.throttle.begin("192.0.2.1", "alice"), range(20)))
        admitted = [item for item in attempts if not item.retry_after]
        self.assertEqual(len(admitted), 8)
        for item in admitted:
            self.throttle.finish(item, False)
        self.assertEqual(self.throttle.begin("192.0.2.2", "alice").retry_after, 300)

    def test_bucket_exhaustion_is_bounded_and_expires(self):
        with patch("mailapi_shared.login_protection.MAX_LOGIN_BUCKETS", 4):
            self.fail("first", "192.0.2.1")
            self.fail("second", "192.0.2.2")
            self.assertEqual(self.throttle.begin("192.0.2.3", "third").retry_after, 300)
            self.assertEqual(len(self.throttle._buckets), 4)
            self.clock.return_value = 401
            self.assertEqual(self.throttle.begin("192.0.2.3", "third").retry_after, 0)


class ProtectedLoginHttpTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict("os.environ", {"MAILAPI_MODE": "test"}))
        self.store = MagicMock()
        self.store.login.return_value = None
        self.verifier = MagicMock(site_key="public-site-key", testing=False)
        self.verifier.verify.return_value = True
        self.app = FastAPI()
        self.app.state.site_accounts = self.store

        @self.app.middleware("http")
        async def state(request: Request, call_next):
            request.state.site_user = None
            return await call_next(request)

        static = Path(__file__).resolve().parents[1] / "mailapi_shared/static"
        self.app.include_router(create_account_router(static, lambda: [], login_verifier=self.verifier))
        self.app.add_middleware(BodyLimitMiddleware)
        self.app.add_middleware(SecurityHeadersMiddleware)
        self.client = self.enterContext(TestClient(self.app, client=("192.0.2.1", 1), base_url="https://testserver"))

    def post(self, username="alice", token=None, browser=False, **kwargs):
        payload = {"username": username, "password": "private-password", "totp_code": "123456"}
        payload["cf-turnstile-response" if browser else "turnstile_token"] = token
        return self.client.post("/login?mode=admin" if browser else "/api/auth/login",
                                **({"data": payload} if browser else {"json": payload}), follow_redirects=False, **kwargs)

    def fail_three(self):
        for _ in range(3):
            response = self.post()
            self.assertEqual(response.status_code, 401)
        return response

    def test_normal_login_does_not_contact_provider_and_preserves_cookie_and_landing(self):
        self.store.login.return_value = ("session-token", {"id": "alice-id", "username": "alice"})
        for _ in range(35):
            response = self.post()
            self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect"], "/test-features#view-overview")
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("no-store", response.headers["cache-control"])
        self.verifier.verify.assert_not_called()

    def test_both_routes_require_server_verification_before_credentials_and_reset_after_success(self):
        response = self.fail_three()
        self.assertEqual(response.json()["challenge"], {"site_key": "public-site-key", "testing": False})
        self.store.login.reset_mock()
        for browser in (False, True):
            response = self.post(browser=browser)
            self.assertEqual(response.status_code, 403)
            if browser:
                self.assertIn('data-required="1"', response.text)
                self.assertIn('name="totp_code"', response.text)
                self.assertNotIn("private-password", response.text)
            else:
                self.assertEqual(response.json()["code"], "challenge_required")
        self.store.login.assert_not_called()
        self.store.login.return_value = ("session-token", {"id": "alice-id"})
        response = self.post(token="valid-challenge", browser=True,
                             headers={"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.8"})
        self.assertEqual(response.status_code, 303)
        self.verifier.verify.assert_called_once_with("valid-challenge", "192.0.2.1")
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.verifier.verify.call_count, 1)

    def test_invalid_and_unavailable_verification_fail_closed_without_consuming_password_budget(self):
        self.fail_three()
        self.store.login.reset_mock()
        self.verifier.verify.return_value = False
        for _ in range(10):
            self.assertEqual(self.post(token="invalid").status_code, 403)
        self.verifier.verify.side_effect = ChallengeUnavailable
        for browser in (True, False):
            response = self.post(token="unavailable", browser=browser)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers["retry-after"], "30")
            self.assertIn("安全验证暂时不可用", response.text)
        self.store.login.assert_not_called()
        self.assertEqual(self.post(username="unaffected").status_code, 401)

    def test_valid_challenge_does_not_bypass_account_lock(self):
        self.fail_three()
        for _ in range(5):
            self.assertEqual(self.post(token="valid-challenge").status_code, 401)
        self.assertEqual(self.post(token="valid-challenge").status_code, 429)
        self.assertEqual(self.store.login.call_count, 8)
        self.assertEqual(self.verifier.verify.call_count, 5)

    def test_shared_ip_does_not_deny_a_valid_user_after_other_accounts_fail(self):
        for number in range(30):
            self.assertEqual(self.post(username=f"user-{number}").status_code, 401)
        self.assertEqual(self.post(username="another-user").status_code, 403)
        self.store.login.return_value = ("session-token", {"id": "another-id"})
        self.assertEqual(self.post(username="another-user", token="valid-challenge").status_code, 200)

    def test_initial_page_has_no_third_party_script_or_secret_and_large_tokens_are_rejected(self):
        response = self.client.get("/login")
        self.assertIn('/static/login-protection.js', response.text)
        self.assertNotIn('src="https://challenges.cloudflare.com', response.text)
        self.assertIn('data-required="0"', response.text)
        self.assertNotIn("private-secret", response.text)
        self.fail_three()
        self.store.login.reset_mock()
        response = self.client.post("/api/auth/login", json={"username": "alice", "turnstile_token": "x" * 8200})
        self.assertEqual(response.status_code, 413)
        self.store.login.assert_not_called()
