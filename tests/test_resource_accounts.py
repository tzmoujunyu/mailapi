import json
import tempfile
import unittest
from pathlib import Path

from resource_accounts import ResourceStore, ResourceStoreError


class ResourceStoreTests(unittest.TestCase):
    def test_coding_plan_masks_secret_and_preserves_it_when_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResourceStore(Path(directory) / "coding-plans.json", "api_key", "API key")
            item = store.create({"name": "Kimi", "note": "团队计划", "api_key": "sk-test-123456"})

            self.assertNotEqual(item["api_key"], "sk-test-123456")
            self.assertEqual(store.get_secret(item["id"]), "sk-test-123456")
            updated = store.update(item["id"], {"name": "Kimi Pro", "note": "新备注"})

            self.assertEqual(updated["name"], "Kimi Pro")
            self.assertEqual(store.get_secret(item["id"]), "sk-test-123456")

    def test_legacy_coding_plan_can_enable_quota_without_reentering_its_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coding-plans.json"
            path.write_text(json.dumps({"items": [{"id": "old", "name": "Plan", "api_key": "old-key"}]}))
            store = ResourceStore(path, "api_key", "API key")
            self.assertEqual(store.list_public()[0]["provider"], "")
            self.assertEqual(store.update("old", {"provider": "kimi"})["provider"], "kimi")
            reopened = ResourceStore(path, "api_key", "API key")
            self.assertEqual(reopened.get_coding_plan_auth("old"), ("kimi", "old-key"))
            with self.assertRaises(ResourceStoreError):
                reopened.update("old", {"provider": "zhipu"})
            with self.assertRaises(ResourceStoreError):
                reopened.update("old", {"provider": "https://other.example"})

    def test_relay_account_requires_account_and_masks_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResourceStore(Path(directory) / "relay-accounts.json", "password", "密码")
            with self.assertRaises(ResourceStoreError):
                store.create({"name": "中转站", "url": "https://relay.example.com", "password": "secret"})

            item = store.create({"name": "中转站", "url": "https://relay.example.com", "account": "user", "password": "secret", "note": "备用"})
            self.assertEqual(item["account"], "user")
            self.assertNotEqual(item["password"], "secret")
            self.assertEqual(store.get_secret(item["id"]), "secret")

    def test_relay_account_can_omit_password_and_survives_reload_and_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay-accounts.json"
            store = ResourceStore(path, "password", "密码")
            item = store.create({"name": "硅基流动", "url": "https://relay.example.com", "account": "user@example.com"})
            self.assertFalse(item["has_password"])
            self.assertEqual(item["password_masked"], "未设置")
            reopened = ResourceStore(path, "password", "密码")
            self.assertEqual(reopened.list_public(), [item])
            self.assertFalse(reopened.update(item["id"], {"note": "邮箱验证码登录"})["has_password"])
            self.assertTrue(reopened.update(item["id"], {"password": "secret"})["has_password"])
            self.assertTrue(reopened.update(item["id"], {"password": ""})["has_password"])
            self.assertTrue(reopened.update(item["id"], {"password": "  "})["has_password"])
            self.assertEqual(reopened.get_secret(item["id"]), "secret")
            for value in (None, 123, {}):
                with self.assertRaises(ResourceStoreError):
                    reopened.create({"name": "中转站", "url": "https://relay.example.com", "account": "user", "password": value})
            plan = ResourceStore(Path(directory) / "plans.json", "api_key", "API key")
            with self.assertRaises(ResourceStoreError):
                plan.create({"name": "Coding Plan"})


if __name__ == "__main__":
    unittest.main()
