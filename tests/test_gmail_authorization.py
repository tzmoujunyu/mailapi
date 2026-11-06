from datetime import datetime, timedelta, timezone
import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from mailapi_shared.google_oauth_callback import parse_google_callback_link


class GmailAuthorizationTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = Path(__file__).resolve().parents[1] / "mailapi_shared" / "application.py"
        spec = importlib.util.spec_from_file_location("mailapi_shared.gmail_authorization_test", source)
        self.app = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {
            "MAILAPI_MODE": "test",
            "TEST_DATA_DIR": str(directory),
            "TEST_EXTERNAL_WATCHERS": "1",
            "TEST_GMAIL_ACCOUNTS": "[]",
            "GMAIL_AUTH_FALLBACK": "auto",
        }):
            spec.loader.exec_module(self.app)
        client_file = directory / "client.json"
        client_file.touch()
        self.cfg = {
            "name": "account-1",
            "email": "gmail@example.com",
            "provider": "gmail",
            "enabled": True,
            "token_file": self.app.gmail_token_path("account-1"),
            "credential_file": str(client_file),
        }
        self.app.ACCOUNTS = [self.cfg]
        self.waiting = threading.Event()
        self.listening = threading.Event()
        save_state = self.app.save_state

        def observe_state(name, values):
            save_state(name, values)
            status = values.get("status", "")
            if status.startswith("等待 Google 授权"):
                self.waiting.set()
            if status == "监听中":
                self.listening.set()

        self.app.CHECK_INTERVAL_SECONDS = 0.01
        self.enterContext(patch.object(self.app, "save_state", side_effect=observe_state))
        self.enterContext(patch.object(self.app, "print", create=True))
        self.enterContext(patch.object(self.app, "log_exception"))
        self.flow = self.enterContext(patch.object(InstalledAppFlow, "from_client_secrets_file"))

    def start_waiter(self):
        flow = Mock(spec=InstalledAppFlow)
        flow.authorization_url.return_value = ("https://example.com/oauth", "automatic-state")
        self.flow.return_value = flow
        name = self.cfg["name"]
        self.assertTrue(self.app.start_mail_watcher(self.cfg))
        watcher = self.app.MAIL_WATCHERS[name]
        self.addCleanup(self.app.stop_mail_watcher, name)
        self.assertTrue(self.waiting.wait(timeout=2))
        session = self.app.AUTH_SESSIONS[name]
        self.addCleanup(session["event"].set)
        return watcher["thread"]

    def test_stopping_watcher_ends_automatic_auth_wait(self):
        old_thread = self.start_waiter()
        self.app.stop_mail_watcher(self.cfg["name"], cancel_auth=False)
        self.assertFalse(old_thread.is_alive())
        self.assertEqual(self.app.AUTH_SESSIONS, {})

    def test_web_reauthorization_retires_old_waiter_and_starts_new_watcher(self):
        old_thread = self.start_waiter()
        flow = Mock(spec=InstalledAppFlow)
        flow.authorization_url.return_value = ("https://example.com/oauth", "managed-state")
        flow.credentials = Credentials(
            "test-token",
            refresh_token="test-refresh-token",
            client_id="test-client",
            client_secret="test-secret",
            expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
        )
        self.flow.return_value = flow
        name = self.cfg["name"]
        self.assertEqual(self.app.start_gmail_admin_auth(name).status_code, 302)
        old_thread.join(timeout=1)
        self.assertFalse(old_thread.is_alive())
        session = self.app.AUTH_SESSIONS[name]
        self.assertIs(self.app.AUTH_SESSIONS["managed-state"], session)

        service = Mock()
        service.users().getProfile().execute.return_value = {"emailAddress": self.cfg["email"], "historyId": "1"}
        service.users().history().list().execute.return_value = {"historyId": "1"}
        callback = parse_google_callback_link("http://localhost:8001/?state=managed-state&code=test-code")
        with (
            patch.object(self.app, "build", return_value=service),
            patch.object(self.app, "fetch_google_oauth_token"),
            patch.object(self.app, "scan_unread_once"),
            patch.object(Credentials, "refresh", side_effect=AssertionError("unexpected token refresh")),
        ):
            self.app.finish_google_auth_session(session, callback)
            self.assertTrue(self.listening.wait(timeout=2))
        self.assertEqual(self.app.AUTH_SESSIONS, {})
        self.assertEqual(self.app.STATE[name]["status"], "监听中")


if __name__ == "__main__":
    unittest.main()
