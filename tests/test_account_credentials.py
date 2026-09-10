import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from account_credentials import AccountCredentialStore
from account_passwords import AccountPasswordStore


class AccountCredentialTests(unittest.TestCase):
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
