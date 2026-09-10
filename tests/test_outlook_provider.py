import __future__
import json
import ast
import os
import threading
import unittest
import urllib.error
import urllib.parse
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from mail_providers.outlook import (
    OutlookAPIError,
    OutlookConfigurationError,
    OutlookMailbox,
    graph_request,
    load_outlook_settings,
    outlook_incoming_mail,
)
from mail_messages import extract_verification_code


class OutlookProviderTests(unittest.TestCase):
    def test_reads_latest_unread_from_inbox_and_junk_in_time_order(self):
        mailbox = object.__new__(OutlookMailbox)
        mailbox.email = "test@example.com"
        mailbox.authorization = Mock()
        mailbox.authorization.acquire_silent.return_value = "access-token"
        with patch("mail_providers.outlook.graph_request", side_effect=[
            {"value": [{"id": "older", "receivedDateTime": "2026-09-09T12:00:00Z"}]},
            {"value": [{"id": "newer", "receivedDateTime": "2026-09-10T12:00:00Z"}]},
        ]) as request:
            messages = mailbox.get_unread_messages(30)
        self.assertEqual([item["id"] for item in messages], ["newer", "older"])
        for call, folder in zip(request.call_args_list, ("inbox", "junkemail")):
            url = urllib.parse.urlsplit(call.args[1])
            self.assertEqual(url.path, f"/me/mailFolders/{folder}/messages")
            query = urllib.parse.parse_qs(url.query)
            self.assertEqual(query["$orderby"], ["receivedDateTime desc"])
            self.assertEqual(query["$top"], ["30"])
            self.assertEqual(query["$filter"], [
                "receivedDateTime ge 1970-01-01T00:00:00Z and isRead eq false"
            ])

    def test_loads_web_oauth_settings(self):
        with patch.dict(
            os.environ,
            {
                "OUTLOOK_CLIENT_ID": "client-id",
                "OUTLOOK_CLIENT_SECRET": "client-secret",
                "OUTLOOK_TENANT": "common",
                "OUTLOOK_OAUTH_REDIRECT_URI": "https://mail.example.com/api/admin/outlook/auth/callback",
                "OUTLOOK_AUTHORITY": "",
            },
            clear=False,
        ):
            settings = load_outlook_settings()
        self.assertEqual(settings.client_id, "client-id")
        self.assertEqual(settings.authority, "https://login.microsoftonline.com/common")

    def test_rejects_remote_http_callback(self):
        with patch.dict(
            os.environ,
            {
                "OUTLOOK_CLIENT_ID": "client-id",
                "OUTLOOK_CLIENT_SECRET": "client-secret",
                "OUTLOOK_OAUTH_REDIRECT_URI": "http://mail.example.com/callback",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(OutlookConfigurationError, "必须使用 HTTPS"):
                load_outlook_settings()

    def test_converts_graph_message(self):
        incoming = outlook_incoming_mail(
            {
                "id": "message-id",
                "subject": "Your temporary ChatGPT login code",
                "from": {"emailAddress": {"address": "noreply@tm.openai.com"}},
                "body": {"contentType": "text", "content": "Your login code is 123456."},
            }
        )
        self.assertEqual(incoming.sender, "noreply@tm.openai.com")
        self.assertEqual(incoming.body, "Your login code is 123456.")
        self.assertEqual(
            extract_verification_code(incoming, {"noreply@tm.openai.com"}),
            "123456",
        )

    def test_graph_error_does_not_expose_access_token(self):
        body = json.dumps({"error": {"message": "Access denied"}}).encode()
        error = urllib.error.HTTPError(
            "https://graph.microsoft.com/v1.0/me",
            401,
            "Unauthorized",
            {},
            BytesIO(body),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(OutlookAPIError) as raised:
                graph_request("secret-access-token", "/me")
        self.assertEqual(raised.exception.status, 401)
        self.assertNotIn("secret-access-token", str(raised.exception))

    def test_marks_matching_message_as_read_through_graph(self):
        mailbox = object.__new__(OutlookMailbox)
        mailbox.access_token = "access-token"
        with patch("mail_providers.outlook.graph_request") as request:
            mailbox.mark_as_read("message/id")
        request.assert_called_once_with(
            "access-token",
            "/me/messages/message%2Fid",
            method="PATCH",
            body={"isRead": True},
        )


class OutlookWatcherTests(unittest.TestCase):
    def setUp(self):
        # Load just the watcher functions, without starting the app or reading runtime secrets.
        source = Path(__file__).resolve().parents[1] / "code-receive.py"
        tree = ast.parse(source.read_text())
        names = {"process_outlook_message", "save_outlook_cache", "scan_outlook_unread_once",
                 "watch_outlook_account", "append_code_record"}
        functions = ast.Module(body=[node for node in tree.body
                                   if isinstance(node, ast.FunctionDef) and node.name in names],
                               type_ignores=[])
        from mail_providers.outlook import OutlookAuthorizationRequired
        self.env = dict(
            threading=threading, OutlookAPIError=OutlookAPIError,
            OutlookAuthorizationRequired=OutlookAuthorizationRequired,
            outlook_incoming_mail=outlook_incoming_mail,
            extract_code_from_mail=extract_verification_code,
            OPENAI_CODE_SENDERS={"noreply@tm.openai.com"},
            OUTLOOK_CATCHUP_LIMIT=30, OUTLOOK_POLL_INTERVAL_SECONDS=3,
            APP_BASE_URL="https://mail.example.com", MAIL_WATCHERS={},
            MAIL_WATCHERS_LOCK=threading.Lock(),
            save_state=Mock(), log_exception=Mock(), write_json_atomic=Mock(),
            load_outlook_settings=Mock(), OutlookMailbox=Mock(), store_code_result=Mock(return_value=True),
        )
        exec(compile(functions, str(source), "exec", flags=__future__.annotations.compiler_flag), self.env)
        self.mailbox = Mock()
        self.cfg = {"name": "test", "email": "test@example.com", "token_file": "unused"}
        self.message = {"id": "message-id", "subject": "Your temporary ChatGPT login code",
                        "from": {"emailAddress": {"address": "noreply@tm.openai.com"}},
                        "body": {"content": "Your login code is 123456."}, "isRead": False}
        self.mailbox.get_unread_messages.return_value = [self.message]

    def test_persists_code_before_marking_read(self):
        events = []
        self.env["store_code_result"].side_effect = lambda *a, **k: events.append("save") or True
        self.mailbox.mark_as_read.side_effect = lambda *a: events.append("read")
        self.assertTrue(self.env["scan_outlook_unread_once"](self.mailbox, self.cfg))
        self.assertEqual(events, ["save", "read"])

    def test_storage_failure_leaves_message_unread_for_retry(self):
        self.env["store_code_result"].side_effect = OSError("disk full")
        self.assertFalse(self.env["scan_outlook_unread_once"](self.mailbox, self.cfg))
        self.mailbox.mark_as_read.assert_not_called()

    def test_failed_history_write_does_not_suppress_retry_as_duplicate(self):
        old = {"message_id": "old"}
        self.env.update(CODE_HISTORY={"test": [old]}, HISTORY_LOCK=threading.Lock(),
                        MAX_HISTORY_RECORDS=10, save_code_history=Mock(side_effect=OSError("disk full")))
        append = self.env["append_code_record"]
        record = {"message_id": "new"}
        with self.assertRaises(OSError):
            append("test", record)
        self.assertEqual(self.env["CODE_HISTORY"]["test"], [old])
        self.env["save_code_history"].side_effect = None
        self.assertTrue(append("test", record))
        self.assertEqual(self.env["CODE_HISTORY"]["test"], [record, old])
        self.assertFalse(append("test", record))

    def test_mark_read_failure_keeps_saved_code_and_duplicate_can_be_acknowledged(self):
        self.mailbox.mark_as_read.side_effect = OutlookAPIError(503, "unavailable")
        with self.assertRaises(OutlookAPIError):
            self.env["scan_outlook_unread_once"](self.mailbox, self.cfg)
        self.env["store_code_result"].assert_called_once()
        self.env["store_code_result"].return_value = False
        self.mailbox.mark_as_read.side_effect = None
        self.assertFalse(self.env["scan_outlook_unread_once"](self.mailbox, self.cfg))
        self.assertEqual(self.mailbox.mark_as_read.call_count, 2)

    def test_startup_network_and_scan_failures_retry(self):
        self.env["OutlookMailbox"].return_value = self.mailbox
        self.mailbox.connect.side_effect = [RuntimeError("network unavailable"),
                                            {"email": self.cfg["email"]}]
        scan = Mock(side_effect=[OutlookAPIError(503, "unavailable"), False])
        self.env["scan_outlook_unread_once"] = scan
        stop = Mock()
        stop.is_set.side_effect = [False, False, False, True]
        self.env["watch_outlook_account"](self.cfg, stop)
        self.assertEqual(self.mailbox.connect.call_count, 2)
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(stop.wait.call_count, 3)


if __name__ == "__main__":
    unittest.main()
