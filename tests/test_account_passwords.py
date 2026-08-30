import tempfile
import unittest
from pathlib import Path

from account_passwords import AccountPasswordError, AccountPasswordStore


class AccountPasswordStoreTests(unittest.TestCase):
    def test_stores_password_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gpt_passwords.json"
            store = AccountPasswordStore(path)
            store.set("account-4", "user@outlook.com", "secret value")

            self.assertEqual(
                AccountPasswordStore(path).get("account-4", "user@outlook.com"),
                "secret value",
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_email_guard_prevents_reused_account_name_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountPasswordStore(Path(directory) / "passwords.json")
            store.set("account-4", "old@example.com", "old-secret")
            self.assertIsNone(store.get("account-4", "new@example.com"))

    def test_deletes_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountPasswordStore(Path(directory) / "passwords.json")
            store.set("account-2", "user@example.com", "secret")
            self.assertTrue(store.delete("account-2"))
            self.assertIsNone(store.get("account-2", "user@example.com"))

    def test_imports_legacy_environment_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountPasswordStore(Path(directory) / "passwords.json")
            store.set("account-2", "two@example.com", "stored")
            imported = store.import_legacy_environment(
                [
                    {"name": "account-2", "email": "two@example.com"},
                    {"name": "account-3", "email": "three@example.com"},
                ],
                {"ACCOUNT_PASSWORD_2": "legacy-two", "ACCOUNT_PASSWORD_3": "legacy-three"},
            )
            self.assertEqual(imported, 1)
            self.assertEqual(store.get("account-2", "two@example.com"), "stored")
            self.assertEqual(store.get("account-3", "three@example.com"), "legacy-three")

    def test_rejects_empty_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountPasswordStore(Path(directory) / "passwords.json")
            with self.assertRaises(AccountPasswordError):
                store.set("account-1", "one@example.com", "")

    def test_does_not_import_for_pending_account(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountPasswordStore(Path(directory) / "passwords.json")
            imported = store.import_legacy_environment(
                [{"name": "account-4", "email": "等待授权"}],
                {"ACCOUNT_PASSWORD_4": "legacy-four"},
            )
            self.assertEqual(imported, 0)


if __name__ == "__main__":
    unittest.main()
