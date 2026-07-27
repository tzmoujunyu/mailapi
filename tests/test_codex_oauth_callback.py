import unittest

from codex_oauth_callback import (
    CodexCallbackLinkError,
    parse_codex_callback_link,
    validate_codex_account_email,
)


class CodexOAuthCallbackTests(unittest.TestCase):
    def test_parses_full_localhost_callback_link(self):
        callback = parse_codex_callback_link(
            "http://localhost:1455/auth/callback"
            "?code=authorization-code&state=session-state"
        )
        self.assertEqual(callback.state, "session-state")
        self.assertEqual(callback.params["code"], "authorization-code")

    def test_accepts_authorization_error(self):
        callback = parse_codex_callback_link(
            "http://localhost:1455/auth/callback"
            "?error=access_denied&error_description=Denied&state=session-state"
        )
        self.assertEqual(callback.params["error"], "access_denied")

    def test_rejects_link_without_state(self):
        with self.assertRaises(CodexCallbackLinkError):
            parse_codex_callback_link(
                "http://localhost:1455/auth/callback?code=authorization-code"
            )

    def test_rejects_non_http_link(self):
        with self.assertRaises(CodexCallbackLinkError):
            parse_codex_callback_link(
                "javascript:alert(1)?state=session-state&code=authorization-code"
            )

    def test_accepts_matching_email_case_insensitively(self):
        validate_codex_account_email("Janymil722@gmail.com", "janymil722@gmail.com")

    def test_rejects_different_account_email(self):
        with self.assertRaisesRegex(CodexCallbackLinkError, "未覆盖原授权"):
            validate_codex_account_email(
                "janymil722@gmail.com",
                "another@example.com",
            )

    def test_rejects_missing_email_for_targeted_reauthorization(self):
        with self.assertRaisesRegex(CodexCallbackLinkError, "无法从授权 token 识别邮箱"):
            validate_codex_account_email("janymil722@gmail.com", None)


if __name__ == "__main__":
    unittest.main()
