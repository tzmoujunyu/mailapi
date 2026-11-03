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

    def test_relay_account_requires_account_and_masks_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResourceStore(Path(directory) / "relay-accounts.json", "password", "密码")
            with self.assertRaises(ResourceStoreError):
                store.create({"name": "中转站", "password": "secret"})

            item = store.create({"name": "中转站", "account": "user", "password": "secret", "note": "备用"})
            self.assertEqual(item["account"], "user")
            self.assertNotEqual(item["password"], "secret")
            self.assertEqual(store.get_secret(item["id"]), "secret")


if __name__ == "__main__":
    unittest.main()
