import json
import os
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
