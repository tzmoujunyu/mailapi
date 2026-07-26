import unittest

from google_oauth_callback import (
    GoogleCallbackLinkError,
    authorization_response_for_redirect,
    parse_google_callback_link,
)


class GoogleOAuthCallbackTests(unittest.TestCase):
    def test_parses_full_localhost_callback_link(self):
        callback = parse_google_callback_link(
            "http://localhost:8000/?state=session-state&code=one%2Ftwo&scope=gmail"
        )
        self.assertEqual(callback.state, "session-state")
        self.assertEqual(
            authorization_response_for_redirect(
                callback,
                "http://localhost:8000/",
            ),
            "http://localhost:8000/?state=session-state&code=one%2Ftwo&scope=gmail",
        )

    def test_rebuilds_response_with_configured_redirect_uri(self):
        callback = parse_google_callback_link(
            "http://localhost:9999/?state=session-state&code=authorization-code"
        )
        self.assertEqual(
            authorization_response_for_redirect(
                callback,
                "https://public.example.com/oauth/callback",
            ),
            "https://public.example.com/oauth/callback"
            "?state=session-state&code=authorization-code",
        )

    def test_accepts_google_access_denied_response(self):
        callback = parse_google_callback_link(
            "http://localhost:8000/?state=session-state&error=access_denied"
        )
        self.assertEqual(callback.state, "session-state")

    def test_rejects_link_without_state(self):
        with self.assertRaises(GoogleCallbackLinkError):
            parse_google_callback_link(
                "http://localhost:8000/?code=authorization-code"
            )

    def test_rejects_non_http_link(self):
        with self.assertRaises(GoogleCallbackLinkError):
            parse_google_callback_link(
                "javascript:alert(1)?state=session-state&code=authorization-code"
            )


if __name__ == "__main__":
    unittest.main()
