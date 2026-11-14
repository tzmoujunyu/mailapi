from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mailapi_shared.account_credentials import AccountCredentialStore
from mailapi_shared.resource_accounts import ResourceStore
from mailapi_shared.resource_database import DATABASE_NAME, GPT, ResourceDatabase, migrate_resources


class ResourceDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.admin = {"source": "admin", "name": "admin", "id": "test-admin"}
        self.member = {"source": "user", "name": "member", "id": "test-user"}
        self.gpt_path = self.root / GPT
        self.plan_path = self.root / "coding_plans.json"
        self.gpt = AccountCredentialStore(self.gpt_path)
        self.gpt.migrate_pool([], [])
        self.account = self.gpt.save_account(None, {"login": "member@example.test", "password": "original-fixture"}, {}, actor=self.admin)
        self.gpt.save_account(self.account["key"], {"password": "replacement-fixture"}, {}, actor=self.member)
        self.plan = ResourceStore(self.plan_path, "api_key", "API Key").create({
            "name": "Test Plan", "api_key": "plan-secret-fixture", "provider": "kimi",
        })
        (self.root / "codex_accounts.json").write_text(json.dumps({"items": [
            {"id": "codex-test", "email": "member@example.test", "token": {"refresh_token": "oauth-secret-fixture"}},
        ]}))

    def test_migration_preserves_secrets_history_and_resource_ids(self):
        source = self.gpt_path.read_bytes()
        info = migrate_resources(self.root)
        self.assertEqual(info["history_events"], 2)
        database = ResourceDatabase(self.root)
        store = AccountCredentialStore(self.gpt_path, database=database)
        key = self.account["key"]
        item = store.history(key)["items"][0]
        self.assertEqual(item["source"], "user")
        self.assertTrue(store.review_change(key, item["id"], self.admin))
        store.restore_account_field(key, item["id"], "password", self.admin)
        reopened = AccountCredentialStore(self.gpt_path, database=database)
        self.assertEqual(reopened.get(self.account["login"], "password"), "original-fixture")
        self.assertEqual(reopened.history(key)["items"][0]["restore_of"], item["id"])
        self.assertIn("review", [event["source"] for event in reopened.history(key)["items"]])
        plan = ResourceStore(self.plan_path, "api_key", "API Key", database=database)
        result = plan.update(self.plan["id"], {"name": "Renamed"})
        self.assertEqual(result["id"], self.plan["id"])
        self.assertEqual(plan.get_coding_plan_auth(self.plan["id"]), ("kimi", "plan-secret-fixture"))
        self.assertEqual(database.load("codex_accounts.json")["items"][0]["token"]["refresh_token"], "oauth-secret-fixture")
        self.assertEqual(self.gpt_path.read_bytes(), source)
        db_file = self.root / DATABASE_NAME
        for secret in (b"original-fixture", b"replacement-fixture", b"plan-secret-fixture", b"oauth-secret-fixture", b'"sealed"'):
            self.assertNotIn(secret, db_file.read_bytes())
        self.assertEqual(db_file.stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in database.secrets.iterdir()))
        export = self.root / "export"
        export.mkdir()
        database.export_json(export)
        self.assertEqual(json.loads((export / GPT).read_text()), database.load(GPT))

    def test_failed_database_write_preserves_committed_secret_pair(self):
        migrate_resources(self.root)
        database = ResourceDatabase(self.root)
        store = ResourceStore(self.plan_path, "api_key", "API Key", database=database)
        with closing(database._connect()) as db, db:
            db.executescript("""CREATE TRIGGER prevent_mutation BEFORE UPDATE ON records
                                BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END;""")
        with self.assertRaises(sqlite3.IntegrityError):
            store.update(self.plan["id"], {"name": "Other", "api_key": "uncommitted-fixture"})
        self.assertEqual(store.get_public(self.plan["id"])["name"], "Test Plan")
        self.assertEqual(database.load("coding_plans.json")["items"][0]["api_key"], "plan-secret-fixture")
        database.prune_private_files()
        self.assertFalse(any("uncommitted-fixture" in path.read_text() for path in database.secrets.iterdir()))

    def test_bad_import_does_not_publish_or_change_source(self):
        path = self.root / "relay_accounts.json"
        source = '{"items":[{"id":"duplicate"},{"id":"duplicate"}]}'
        path.write_text(source)
        with self.assertRaises(ValueError):
            migrate_resources(self.root)
        self.assertFalse((self.root / DATABASE_NAME).exists())
        self.assertEqual(path.read_text(), source)
