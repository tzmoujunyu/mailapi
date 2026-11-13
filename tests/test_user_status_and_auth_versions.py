import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mailapi_shared.auth_artifact_versions import AuthArtifactVersionStore
from mailapi_shared.account_credentials import AccountCredentialStore
from mailapi_shared import site_accounts
from mailapi_shared.site_accounts import SiteAccountStore


class UserStatusTests(unittest.TestCase):
    def test_new_password_policy_checks_bounds_identity_and_padded_range(self):
        password = " 例🧪! test "
        digest = hashlib.sha1(password.encode()).hexdigest().upper()
        with patch.object(site_accounts, "urlopen") as lookup:
            response = lookup.return_value.__enter__.return_value
            response.read.return_value = f"{digest[5:]}:7\r\n".encode()
            for invalid in ("x" * 7, "x" * 129, "contains-ALICE", "has-mailname-prefix", "line\nbreak"):
                with self.assertRaises(site_accounts.SiteAccountError):
                    site_accounts.validate_new_password(invalid, "alice", "mailname@example.test")
            lookup.assert_not_called()
            with self.assertRaisesRegex(site_accounts.SiteAccountError, "泄露"):
                site_accounts.validate_new_password(password, "alice")
            request = lookup.call_args.args[0]
            self.assertEqual(request.full_url, f"https://api.pwnedpasswords.com/range/{digest[:5]}")
            self.assertEqual(request.get_header("Add-padding"), "true")
            response.read.return_value = f"{digest[5:]}:0\r\n".encode()
            site_accounts.validate_new_password(password, "alice")
            lookup.side_effect = TimeoutError
            with self.assertLogs("mailapi_shared.site_accounts", level="WARNING"):
                site_accounts.validate_new_password("🧪" * 128, "alice")
        encoded = site_accounts.password_hash(password)
        self.assertTrue(encoded.startswith("$argon2id$v=19$m=19456,t=2,p=1$"))
        self.assertTrue(site_accounts.password_matches(password, encoded))
        self.assertFalse(site_accounts.password_matches(password.strip(), encoded))
        with patch.object(site_accounts, "PASSWORD_HASHER") as hasher:
            self.assertFalse(site_accounts.password_matches("x" * 129, encoded))
            with self.assertRaises(site_accounts.SiteAccountError):
                site_accounts.password_hash("x" * 129)
            self.assertFalse(hasher.mock_calls)

    @patch.object(site_accounts, "password_is_pwned", return_value=False)
    def test_legacy_login_upgrades_hash_without_clearing_reminder(self, lookup):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site_accounts.json"
            store = SiteAccountStore(path)
            store.ensure_admin("fixture-entry-8642")
            _, admin = store.login("admin", "fixture-entry-8642")
            user = store.create_user(admin["id"], "alice", "shared-password")
            data = json.loads(path.read_text())
            salt = "test-salt"
            digest = hashlib.pbkdf2_hmac("sha256", b"shared-password", salt.encode(), 600_000).hex()
            data["users"][user["id"]]["password_hash"] = f"pbkdf2_sha256$600000${salt}${digest}"
            data["users"][user["id"]]["must_change_password"] = False
            path.write_text(json.dumps(data))
            store = SiteAccountStore(path)
            result = store.scan_initial_password(admin["id"], "shared-password", usernames={"alice"})
            self.assertEqual(result["matched_count"], 1)
            lookup.reset_mock()
            _, logged_in = store.login("alice", "shared-password")
            self.assertTrue(logged_in["must_change_password"])
            updated = json.loads(path.read_text())["users"][user["id"]]
            self.assertTrue(updated["password_hash"].startswith("$argon2id$"))
            self.assertIsNone(updated["password_changed_at"])
            self.assertEqual(updated["password_version"], 1)
            lookup.assert_not_called()

    @patch.object(site_accounts, "password_is_pwned", return_value=False)
    def test_initial_password_scan_and_password_change_status(self, _lookup):
        with tempfile.TemporaryDirectory() as directory:
            store = SiteAccountStore(Path(directory) / "site_accounts.json")
            store.ensure_admin("fixture-entry-8642")
            _, admin = store.login("admin", "fixture-entry-8642")
            user = store.create_user(admin["id"], "alice", "shared-password")

            created = store.list_users(admin["id"])[1]
            self.assertTrue(created["must_change_password"])
            self.assertEqual(created["password_source"], "initial")
            result = store.scan_initial_password(admin["id"], "shared-password")
            self.assertEqual(result["matched_count"], 1)
            self.assertEqual(result["matched"][0]["username"], "alice")
            self.assertNotIn("password_hash", result["matched"][0])

            store.login("alice", "shared-password")
            self.assertIsNotNone(store.list_users(admin["id"])[1]["last_login_at"])
            store.change_password(user["id"], "shared-password", "new-password")
            updated = next(item for item in store.list_users(admin["id"]) if item["id"] == user["id"])
            self.assertFalse(updated["must_change_password"])
            self.assertEqual(updated["password_source"], "user")
            self.assertEqual(updated["password_version"], 2)
            self.assertIsNotNone(updated["password_changed_at"])


class AuthArtifactVersionTests(unittest.TestCase):
    def test_replacement_archives_old_file_and_records_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AuthArtifactVersionStore(root / "versions.json", root / "archive")
            target = root / "gmail.json"
            store.write_json(target, {"refresh_token": "first"}, provider="gmail", resource_id="account-1", source="oauth")
            store.write_json(target, {"refresh_token": "second"}, provider="gmail", resource_id="account-1", source="reauth")

            entries = store.list_public(provider="gmail", resource_id="account-1")
            self.assertEqual([item["version"] for item in entries], [1, 2, 3])
            self.assertTrue(entries[-1]["active"])
            self.assertFalse(entries[-2]["active"])
            self.assertNotIn("refresh_token", json.dumps(entries))
            self.assertTrue((root / "archive/gmail/account-1/v2-before.json").is_file())
            self.assertEqual(json.loads(target.read_text())["refresh_token"], "second")


class SecurityAuditTests(unittest.TestCase):
    def test_external_password_backfill_is_correlated_with_auth_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AccountCredentialStore(Path(directory) / "account_credentials.json")
            actor = {"source": "admin", "name": "admin", "id": "admin-id"}
            account = store.save_account(None, {"login": "user@example.com"}, set(), actor=actor)
            store.record_security_event(account["key"], "external_auth_invalidated", "外部 Codex 授权失效，需要重新授权")
            store.set("user@example.com", "password", "new-password", actor=actor, change_reason="external_backfill")
            history = store.history(account["key"])["items"]
            backfill = next(item for item in history if item.get("event_type") == "external_password_backfill")
            invalidated = next(item for item in history if item.get("event_type") == "external_auth_invalidated")
            self.assertEqual(backfill["correlation_id"], invalidated["correlation_id"])
            self.assertEqual(backfill["related_event_id"], next(
                item["id"] for item in history if "外部密码回填" in " ".join(item["descriptions"])
            ))
            self.assertTrue(any("外部密码回填" in " ".join(item["descriptions"]) for item in history))


if __name__ == "__main__":
    unittest.main()
