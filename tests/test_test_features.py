import json
from concurrent.futures import ThreadPoolExecutor
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

    def test_old_revoked_keys_migrate_as_permanent_but_new_paused_keys_remain_reversible(self):
        old=self.store.create_api_key('a','Old')
        self.store.revoke_api_key(old['id'],'a')
        with self.store._connect() as db:
            db.execute('ALTER TABLE api2api_keys DROP COLUMN revoked')
        migrated=TestFeatureStore(self.data,self.keyring)
        self.assertTrue(migrated.gateway_key(old['id'])['revoked'])
        self.assertFalse(migrated.set_api_key_enabled(old['id'],'a',True))
        current=migrated.create_api_key('a','Current')
        self.assertTrue(migrated.set_api_key_enabled(current['id'],'a',False))
        reopened=TestFeatureStore(self.data,self.keyring)
        self.assertFalse(reopened.gateway_key(current['id'])['revoked'])
        self.assertTrue(reopened.set_api_key_enabled(current['id'],'a',True))
        self.assertIsNotNone(reopened.authenticate_api_key(current['api_key']))

    def test_api_audit_persists_and_can_page_beyond_one_hundred_events(self):
        actor={'id':'alice','username':'Alice'}
        for index in range(125):
            event=self.store.begin_api_audit(actor,'created',{'name':'Development'})
            self.store.finish_api_audit(event,'succeeded',{'id':str(index),'key_prefix':'sk-short'})
        reopened=TestFeatureStore(self.data,self.keyring)
        first=reopened.api_audit_page(page_size=100)
        second=reopened.api_audit_page(page=2,page_size=100)
        self.assertEqual(first['total'],125)
        self.assertEqual(len(first['items']),100)
        self.assertEqual(len(second['items']),25)
        self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
        self.assertEqual(reopened.api_audit_page(query='%')['total'],0)

    def test_ip_policies_migrate_persist_normalize_and_fail_closed(self):
        item=self.store.create_api_key('alice','Restricted')
        with self.store._connect() as db:
            db.execute('ALTER TABLE api2api_keys DROP COLUMN caller_ip_allowlist')
        reopened=TestFeatureStore(self.data,self.keyring)
        self.assertEqual(reopened.gateway_key(item['id'])['caller_ip_allowlist'],'')
        normalized=reopened.normalize_api_key_ips('192.0.2.42\n192.0.2.42/32\n198.51.100.17/24\n2001:db8::1/32\n::ffff:203.0.113.0/120')
        self.assertEqual(normalized,'192.0.2.42\n198.51.100.0/24\n2001:db8::/32\n203.0.113.0/24')
        self.assertTrue(reopened.set_api_key_ips(item['id'],'alice',normalized,expected=''))
        self.assertFalse(reopened.set_api_key_ips(item['id'],'bob','',expected=normalized))
        self.assertFalse(reopened.set_api_key_ips(item['id'],'alice','',expected=''))
        saved=TestFeatureStore(self.data,self.keyring).authenticate_api_key(item['api_key'])
        for host in ('192.0.2.42','::ffff:192.0.2.42','198.51.100.254','2001:db8:1::1','203.0.113.5'):
            self.assertTrue(reopened.api_key_ip_allowed(saved,host),host)
        for host in ('192.0.2.43','198.51.101.1','2001:db9::1','not-an-ip','fe80::1%en0',None):
            self.assertFalse(reopened.api_key_ip_allowed(saved,host),host)
        self.assertFalse(reopened.api_key_ip_allowed({'caller_ip_allowlist':'broken'},'192.0.2.42'))
        self.assertTrue(reopened.set_api_key_ips(item['id'],'alice','',expected=normalized))
        self.assertTrue(reopened.api_key_ip_allowed(reopened.gateway_key(item['id']),None))
        for value in ('example.com','192.0.2.*','192.0.2.999','192.0.2.1,192.0.2.2','::1%en0','192.0.2.0/33','::/129','x'*8193,'\n'.join(['192.0.2.1']*101),[],None):
            with self.assertRaises(TestFeatureError,msg=str(value)[:40]):
                reopened.normalize_api_key_ips(value)

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

    def test_legacy_backfill_is_idempotent_and_does_not_guess_multi_group_attribution(self):
        item = self.store.create_finance_application(user_id="a", username="alice", user_note="", groups=["A", "B"], project="订阅", amount_cents=100, files=[])
        self.store.review_finance_application(item["id"], "approved", "admin", "admin", "通过")
        with self.store._connect() as db:
            db.execute("DELETE FROM finance_events")
            db.execute("DELETE FROM finance_versions")
        self.store = TestFeatureStore(self.data, self.keyring)
        self.assertEqual(self.store.finance_application(item["id"])["expense_group_name"], "待确认")
        self.assertEqual([row["action"] for row in self.store.finance_history(item["id"])], ["approved", "legacy"])
        self.store = TestFeatureStore(self.data, self.keyring)
        self.assertEqual(len(self.store.finance_history(item["id"])), 2)

    def test_re_review_schema_migrates_existing_events_without_rewriting_snapshots(self):
        item = self.store.create_finance_application(user_id='alice',username='alice',user_note='',groups=[],project='订阅',amount_cents=100,files=[])
        self.store.review_finance_application(item['id'],'approved','admin','admin','原记录',expected_version=1)
        with self.store._connect() as db:
            before = db.execute('SELECT snapshot_json FROM finance_versions').fetchall()
            db.execute('ALTER TABLE finance_events DROP COLUMN supersedes_event_id')
        migrated = TestFeatureStore(self.data,self.keyring)
        with migrated._connect() as db:
            self.assertEqual(db.execute('SELECT snapshot_json FROM finance_versions').fetchall(),before)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        events = migrated.finance_history(item['id'])
        self.assertEqual([event['action'] for event in events],['approved','submitted'])
        self.assertTrue(events[0]['is_current_review'])
        self.assertIsNone(events[0]['supersedes_event_id'])

    def test_concurrent_review_only_records_one_result(self):
        item = self.store.create_finance_application(user_id="a", username="alice", user_note="", groups=[], project="订阅", amount_cents=100, files=[])
        def review(status):
            return self.store.review_finance_application(item["id"], status, status, status, "意见", expected_version=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, ["approved", "rejected"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.store.finance_history(item["id"])), 2)

    def test_concurrent_re_review_preserves_history_after_reopen(self):
        item = self.store.create_finance_application(user_id='alice',username='alice',user_note='',groups=[],project='订阅',amount_cents=100,files=[])
        self.store.review_finance_application(item['id'],'approved','admin','admin','原审批',expected_version=1)
        original = self.store.finance_history(item['id'])[0]
        def re_review(pair):
            store,actor = pair
            return store.re_review_finance_application(item['id'],'rejected',actor,actor,'改审原因',expected_version=1,event_id=original['id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(re_review,[(self.store,'admin-a'),(TestFeatureStore(self.data,self.keyring),'admin-b')]))
        self.assertEqual(sum(result is not None for result in results),1)
        events = self.store.finance_history(item['id'])
        self.assertEqual(len(events),3)
        self.assertEqual(events[0]['supersedes_event_id'],original['id'])
        self.assertEqual(events[1]['snapshot'],original['snapshot'])
        self.assertEqual(sum(event['is_current_review'] for event in events),1)
        reopened = TestFeatureStore(self.data,self.keyring)
        self.assertEqual(reopened.finance_history(item['id']),events)


if __name__ == "__main__":
    unittest.main()
