import tempfile
import unittest
from pathlib import Path

from account_passwords import (
    AccountPasswordError,
    AccountPasswordStore,
    normalize_totp_secret,
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
        self.assertEqual(normalize_totp_secret("  ABCD EFGH  "), "ABCD EFGH")

    def test_rejects_missing_or_blank_secret(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(AccountPasswordError):
                    normalize_totp_secret(value)


if __name__ == "__main__":
    unittest.main()
