import json
from base64 import b64encode
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mailapi_shared.credential_crypto import CredentialKeyring
from mailapi_shared.test_features import MAX_FINANCE_PROJECT, TestFeatureError, TestFeatureStore


class TestFeatureStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.key_file = self.directory / "test-key.json"
        self.key_file.write_text(json.dumps({
            "namespace": "test-features",
            "active": "v1",
            "keys": {"v1": b64encode(AESGCM.generate_key(bit_length=256)).decode()},
        }))
        self.key_file.chmod(0o600)
        self.data = self.directory / "data"
        self.data.mkdir(mode=0o700)
        self.keyring = CredentialKeyring(self.key_file, data_dirs=(self.data,))
        self.store = TestFeatureStore(self.data, self.keyring)

    def test_api2api_key_and_usage_are_scoped_and_upstream_key_is_encrypted(self):
        config = self.store.configure_api2api({
            "base_url": "https://newapi.example.test/v1",
            "upstream_key": "upstream-secret",
            "enabled": True,
            "model_allowlist": ["glm-4.5"],
        })
        self.assertTrue(config["configured"])
        self.assertNotIn(b"upstream-secret", (self.data / "test_features.sqlite3").read_bytes())
        created = self.store.create_api_key("user-a", "开发环境")
        self.assertEqual(self.store.authenticate_api_key(created["api_key"])["user_id"], "user-a")
        self.store.record_usage(key_id=created["id"], user_id="user-a", model="glm-4.5",
                                prompt_tokens=3, completion_tokens=5, total_tokens=8,
                                status_code=200, request_ms=12)
        summary = self.store.usage_summary("user-a")
        self.assertEqual(summary["total_tokens"], 8)
        self.assertEqual(self.store.usage_summary("user-b")["requests"], 0)

    def test_finance_metadata_and_announcements_persist(self):
        stored = self.data / "finance_files" / "invoice.pdf"
        stored.write_bytes(b"invoice")
        item = self.store.create_finance_application(
            user_id="user-a", username="alice", user_note="Agent",
            groups=["Agent-1"], project="模型订阅", amount_cents=1250,
            files=[{"id": "file-1", "kind": "invoice", "stored_name": "invoice.pdf",
                    "original_name": "invoice.pdf", "content_type": "application/pdf",
                    "size": 7, "sha256": "digest"}],
        )
        self.assertEqual(item["status"], "pending")
        reviewed = self.store.review_finance_application("missing", "approved", "admin", "admin", "")
        self.assertIsNone(reviewed)
        reviewed = self.store.review_finance_application(item["id"], "approved", "admin", "admin", "通过")
        self.assertEqual(reviewed["status"], "approved")
        announcement = self.store.save_announcement(announcement_id=None, title="公告", body="内容", pinned=True, actor="admin")
        self.assertTrue(self.store.announcements()[0]["pinned"])
        self.assertEqual(announcement["title"], "公告")

    def test_finance_edit_cancel_and_announcement_update(self):
        stored = self.data / "finance_files" / "invoice.pdf"
        stored.write_bytes(b"invoice")
        item = self.store.create_finance_application(
            user_id="user-a", username="alice", user_note="Agent",
            groups=[], project="模型订阅", amount_cents=1250,
            files=[{"id": "file-1", "kind": "invoice", "stored_name": "invoice.pdf",
                    "original_name": "long invoice name.pdf", "content_type": "application/pdf",
                    "size": 7, "sha256": "digest"}],
        )
        updated = self.store.update_finance_application(item["id"], "user-a", "模型订阅续费", 1680, {})
        self.assertEqual(updated["project"], "模型订阅续费")
        self.assertEqual(updated["amount"], "16.80")
        cancelled = self.store.cancel_finance_application(item["id"], "user-a")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(self.store.update_finance_application(item["id"], "user-a", "再次修改", 2000, {}))
        self.assertIsNone(self.store.cancel_finance_application(item["id"], "user-a"))
        with self.assertRaises(TestFeatureError):
            self.store._text("x" * (MAX_FINANCE_PROJECT + 1), "报销项目", MAX_FINANCE_PROJECT, required=True)

        announcement = self.store.save_announcement(announcement_id=None, title="旧公告", body="旧内容", pinned=True, actor="admin")
        revised = self.store.save_announcement(announcement_id=announcement["id"], title="新公告", body="新内容", pinned=False, actor="admin")
        self.assertEqual(revised["title"], "新公告")
        self.assertFalse(revised["pinned"])


if __name__ == "__main__":
    unittest.main()
