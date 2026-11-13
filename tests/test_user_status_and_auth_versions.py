import json
from pathlib import Path
import tempfile
import unittest

from mailapi_shared.auth_artifact_versions import AuthArtifactVersionStore
from mailapi_shared.site_accounts import SiteAccountStore


class UserStatusTests(unittest.TestCase):
    def test_initial_password_scan_and_password_change_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SiteAccountStore(Path(directory) / "site_accounts.json")
            store.ensure_admin("admin-password")
            _, admin = store.login("admin", "admin-password")
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


if __name__ == "__main__":
    unittest.main()
