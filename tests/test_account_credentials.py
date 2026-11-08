import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from account_credentials import AccountCredentialStore
from account_passwords import AccountPasswordStore
from mailapi_shared.account_passwords import AccountPasswordError


class AccountCredentialTests(unittest.TestCase):
    def test_edits_and_renames_preserve_account_order_across_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            store = AccountCredentialStore(path)
            accounts = [store.save_account(None, {"login": login}, {}) for login in ("first", "middle", "last")]
            keys = [account["key"] for account in accounts]
            for key, payload in (
                (keys[1], {"subscription_started_on": "2026-01-31"}),
                (keys[0], {"login": "renamed", "password": "kept-password"}),
                (keys[1], {"listed": False}),
            ):
                store.save_account(key, payload, {})
                self.assertEqual([account["key"] for account in store.list_accounts()], keys)
                store = AccountCredentialStore(path)
                self.assertEqual([account["key"] for account in store.list_accounts()], keys)
            added = store.save_account(None, {"login": "new"}, {})
            self.assertEqual([account["key"] for account in AccountCredentialStore(path).list_accounts()], keys + [added["key"]])
            self.assertEqual(store.get("renamed", "password"), "kept-password")

    def test_monthly_subscription_clamps_month_end_and_survives_edits_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / "credentials.json")
            for index, (start, end) in enumerate((
                ("2026-01-31", "2026-02-28"), ("2024-01-31", "2024-02-29"),
                ("2026-03-31", "2026-04-30"), ("2026-12-15", "2027-01-15"),
            )):
                account = store.save_account(None, {"login": f"monthly-{index}", "subscription_started_on": start}, {})
                self.assertEqual(account["subscription_ends_on"], end)
                store.save_account(account["key"], {"password": "kept-password"}, {})
                reopened = AccountCredentialStore(store.path).account_by_key(account["key"])
                self.assertEqual(reopened["subscription_started_on"], start)
                self.assertEqual(reopened["subscription_ends_on"], end)
            original = store.path.read_bytes()
            for value in (None, 12, [], "2026-02-30", "20260101", "2026-W01-1", "9999-12-01"):
                with self.assertRaises(AccountPasswordError):
                    store.save_account(account["key"], {"subscription_started_on": value}, {})
                self.assertEqual(store.path.read_bytes(), original)
            cleared = store.save_account(account["key"], {"subscription_started_on": ""}, {})
            self.assertEqual(cleared["subscription_ends_on"], "")
            self.assertEqual(store.get(cleared["login"], "password"), "kept-password")

    def test_pool_migration_preserves_keys_credentials_and_explicit_mailbox_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / "credentials.json")
            store.set("old@example.com", "password", "existing-password")
            store.set("old@example.com", "listed", False)
            mail = [{"name": "mail-1", "email": "old@example.com"}]
            store.migrate_pool(mail, [{"email": "old@example.com"}])
            account = store.list_accounts()[0]
            self.assertEqual(account["key"], "email:old@example.com")
            self.assertFalse(account["listed"])
            self.assertEqual(store.linked_mailbox_ids(account["login"], mail), ["mail-1"])
            self.assertNotIn("existing-password", str(account))
            store.delete("old@example.com")
            restored = AccountCredentialStore(store.path)
            restored.migrate_pool(mail + [{"name": "mail-2", "email": "new@example.com"}], [])
            self.assertEqual(restored.list_accounts(), [])

    def test_username_only_account_and_rename_keep_stable_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / "credentials.json")
            store.migrate_pool([], [])
            account = store.save_account(None, {"login": "Team-Only"}, {})
            self.assertFalse(account["has_gpt_password"])
            self.assertFalse(account["has_totp_secret"])
            store.set("Team-Only", "password", "new-password")
            updated = store.save_account(account["key"], {"login": "Renamed"}, {})
            self.assertEqual(updated["key"], account["key"])
            self.assertEqual(store.get("Renamed", "password"), "new-password")
            self.assertEqual(AccountCredentialStore(store.path).account_by_key(account["key"]), updated)

    def test_mailbox_has_one_owner_and_reused_names_do_not_rebind_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / "credentials.json")
            store.migrate_pool([], [])
            accounts = [store.save_account(None, {"login": name}, {}) for name in ("first", "second")]
            mailboxes = {"mail-1": "receiver@example.com"}
            def bind(account):
                try:
                    store.save_account(account["key"], {"mailbox_ids": ["mail-1"]}, mailboxes)
                    return True
                except AccountPasswordError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(sorted(executor.map(bind, accounts)), [False, True])
            self.assertEqual(sum(bool(account["mailbox_ids"]) for account in store.list_accounts()), 1)
            self.assertTrue(all(store.linked_mailbox_ids(account["login"], [
                {"name": "mail-1", "email": "different@example.com"},
            ]) == [] for account in accounts))
            store.detach_mailbox("mail-1")
            self.assertTrue(all(not account["mailbox_ids"] for account in store.list_accounts()))

    def test_codex_owner_survives_rename_and_reuse_of_old_login(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / "credentials.json")
            store.migrate_pool([], [{"email": "codex@example.com"}])
            original = store.account_for_login("codex@example.com")
            renamed = store.save_account(original["key"], {"login": "renamed"}, {})
            replacement = store.save_account(None, {"login": "codex@example.com"}, {})
            store.ensure_codex_account("codex@example.com")
            self.assertEqual(store.get(renamed["login"], "codex_email"), "codex@example.com")
            self.assertIsNone(store.get(replacement["login"], "codex_email"))

    def test_listing_survives_restart_and_credential_changes_in_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / 'credentials.json')
            store.set('user@example.com', 'password', 'password-value')
            original_files = set(Path(directory).iterdir())
            store.set(' User@Example.com ', 'listed', False)
            store.set('user@example.com', 'secret', 'secret-value')
            store.delete('user@example.com', 'password')
            restored = AccountCredentialStore(store.path)
            self.assertIs(restored.get('USER@example.com', 'listed'), False)
            self.assertEqual(restored.get('user@example.com', 'secret'), 'secret-value')
            self.assertEqual(set(Path(directory).iterdir()), original_files)
            restored.set('user@example.com', 'listed', True)
            self.assertIs(AccountCredentialStore(store.path).get('user@example.com', 'listed'), True)

    def test_shared_identity_and_independent_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / 'credentials.json')
            store.set(' User@Example.com ', 'password', 'password-value')
            store.set('user@example.com', 'secret', 'secret-value')
            store.delete('USER@example.com', 'password')
            restored = AccountCredentialStore(store.path)
            self.assertIsNone(restored.get('user@example.com', 'password'))
            self.assertEqual(restored.get('USER@example.com', 'secret'), 'secret-value')
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_migration_does_not_restore_deleted_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passwords = AccountPasswordStore(root / 'old-passwords.json')
            totp = AccountPasswordStore(root / 'old-totp.json')
            passwords.set('account-1', 'user@example.com', 'legacy-password')
            totp.set('account-1', 'user@example.com', 'legacy-secret')
            accounts = [{'name': 'account-1', 'email': 'USER@example.com'}]
            store = AccountCredentialStore(root / 'credentials.json')
            store.migrate(accounts, passwords, totp, {})
            self.assertEqual(store.get('user@example.com', 'password'), 'legacy-password')
            self.assertEqual(store.get('user@example.com', 'secret'), 'legacy-secret')
            store.delete('user@example.com')
            store = AccountCredentialStore(store.path)
            store.migrate(accounts, passwords, totp, {'ACCOUNT_PASSWORD_1': 'env-password'})
            self.assertIsNone(store.get('user@example.com', 'password'))
            self.assertIsNone(store.get('user@example.com', 'secret'))
            self.assertEqual(passwords.get('account-1', 'user@example.com'), 'legacy-password')

    def test_migration_keeps_existing_values_and_checks_account_email(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = AccountPasswordStore(root / 'old.json')
            old.set('account-1', 'old@example.com', 'old-value')
            store = AccountCredentialStore(root / 'credentials.json')
            store.set('new@example.com', 'password', 'new-value')
            store.migrate([{'name': 'account-1', 'email': 'new@example.com'}], old, old, {})
            self.assertEqual(store.get('new@example.com', 'password'), 'new-value')
            self.assertIsNone(store.get('new@example.com', 'secret'))

    def test_failed_write_keeps_memory_and_disk_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / 'credentials.json')
            store.set('user@example.com', 'password', 'original')
            with patch('account_credentials.os.replace', side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    store.set('user@example.com', 'password', 'replacement')
            self.assertEqual(store.get('user@example.com', 'password'), 'original')
            self.assertEqual(AccountCredentialStore(store.path).get('user@example.com', 'password'), 'original')


if __name__ == '__main__':
    unittest.main()
