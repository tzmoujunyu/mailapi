import tempfile
import unittest
from pathlib import Path

from account_passwords import (
    AccountPasswordError,
    AccountPasswordStore,
    normalize_totp_secret,
    generate_totp_code,
)


class TotpCredentialTests(unittest.TestCase):
    def test_stores_totp_secret_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "totp_secrets.json"
            store = AccountPasswordStore(path)
            store.set("account-1", "user@example.com", "ABCD EFGH")

            self.assertEqual(
                AccountPasswordStore(path).get("account-1", "user@example.com"),
                "ABCD EFGH",
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_normalizes_surrounding_whitespace(self):
        self.assertEqual(normalize_totp_secret("  abcd efgh  "), "ABCDEFGH")

    def test_standard_totp_vectors_preserve_leading_zeroes(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        for timestamp, expected in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471")):
            with self.subTest(timestamp=timestamp):
                result = generate_totp_code(secret, timestamp)
                self.assertEqual(result["code"], expected)
                self.assertEqual(result["server_time"], timestamp)
                self.assertEqual(result["expires_at"], (timestamp // 30 + 1) * 30)

    def test_period_boundary_changes_the_current_code(self):
        secret = "JBSWY3DPEHPK3PXP"
        before = generate_totp_code(secret, 59.9)
        after = generate_totp_code(secret, 60)
        self.assertNotEqual(before["code"], after["code"])
        self.assertEqual(before["expires_at"], 60)
        self.assertEqual(after["expires_at"], 90)

    def test_invalid_tokens_are_rejected_without_echoing_secret(self):
        for value in ("123456", "invalid-token!", "A", "otpauth://totp/test?secret=ABC"):
            with self.subTest(value=value):
                with self.assertRaises(AccountPasswordError) as error:
                    normalize_totp_secret(value)
                if len(value) > 1:
                    self.assertNotIn(value, str(error.exception))

    def test_rejects_missing_or_blank_secret(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(AccountPasswordError):
                    normalize_totp_secret(value)


if __name__ == "__main__":
    unittest.main()
