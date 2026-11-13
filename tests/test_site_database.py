import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pyotp

from mailapi_shared.site_accounts import SiteAccountError, SiteAccountStore
from mailapi_shared.site_database import DATABASE_NAME, LEGACY_NAME, migrate_site_accounts, open_site_accounts


class SiteDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch("mailapi_shared.site_accounts.password_is_pwned", return_value=False))
        self.password = "fixture-entry-8642"
        self.totp = pyotp.TOTP("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
        self.source = self.root / LEGACY_NAME
        self.json = SiteAccountStore(self.source)
        self.json.provision_admin("admin", self.password, self.totp.secret)
        self.admin_token, admin = self.json.login("admin", self.password, self.totp.now())
        self.admin_id = admin["id"]
        self.alice = self.json.create_user(self.admin_id, "alice", self.password)
        self.bob = self.json.create_user(self.admin_id, "bob", self.password)
        self.group = self.json.save_group(self.admin_id, None, {
            "name": "Team", "member_ids": [self.alice["id"]],
            "resource_keys": ["gpt:test", "mail:test"],
        }, {"gpt:test", "mail:test"})

    def test_migration_preserves_state_and_writes_only_database(self):
        raw = json.loads(self.source.read_text())
        salt = "fixture-salt"
        digest = hashlib.pbkdf2_hmac("sha256", self.password.encode(), salt.encode(), 600000).hex()
        raw["users"][self.alice["id"]]["password_hash"] = f"pbkdf2_sha256$600000${salt}${digest}"
        self.source.write_text(json.dumps(raw))
        before = self.source.read_bytes()
        counts = migrate_site_accounts(self.root)
        self.assertEqual(counts, {"users": 3, "groups": 1, "sessions": 1, "memberships": 1, "grants": 2})
        store = open_site_accounts(self.root, backend="sqlite", require_admin_mfa=True)
        self.assertEqual(store._data, raw)
        self.assertEqual(store.session_user(self.admin_token)["role"], "admin")
        self.assertTrue(store.resource_allowed(self.alice["id"], "gpt:test"))
        self.assertFalse(store.resource_allowed(self.bob["id"], "gpt:test"))
        alice_token, alice = store.login("alice", self.password)
        self.assertTrue(alice["must_change_password"])
        store.change_password(alice["id"], self.password, "fixture-new-entry")
        self.assertIsNone(store.session_user(alice_token))
        self.assertIsNone(store.login("admin", self.password))
        store.set_theme(self.bob["id"], "dark")
        store.delete_user(self.admin_id, self.bob["id"])
        store = open_site_accounts(self.root, backend="sqlite")
        _, alice = store.login("alice", "fixture-new-entry")
        self.assertFalse(alice["must_change_password"])
        self.assertTrue(store._data["users"][alice["id"]]["password_hash"].startswith("$argon2id$"))
        self.assertEqual(store.list_groups(self.admin_id), [self.group])
        self.assertIsNone(store.get_user(self.bob["id"]))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual((self.root / DATABASE_NAME).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(SiteAccountError):
            migrate_site_accounts(self.root)

    def test_failed_migration_leaves_no_database_or_temporary_file(self):
        data = json.loads(self.source.read_text())
        data["groups"][self.group["id"]]["member_ids"].append("missing-user")
        self.source.write_text(json.dumps(data))
        with self.assertRaises(sqlite3.IntegrityError):
            migrate_site_accounts(self.root)
        self.assertEqual(list(self.root.iterdir()), [self.source])
        with self.assertRaises(SiteAccountError):
            open_site_accounts(self.root, backend="sqlite")

    def test_failed_transaction_does_not_update_cache_or_database(self):
        migrate_site_accounts(self.root)
        store = open_site_accounts(self.root, backend="sqlite")
        before = store._data
        with self.assertRaises(sqlite3.IntegrityError):
            with store._edit() as data:
                data["users"][self.alice["id"]]["theme"] = "dark"
                data["groups"][self.group["id"]]["member_ids"].append("missing-user")
        self.assertEqual(store._data, before)
        self.assertEqual(open_site_accounts(self.root, backend="sqlite")._data, before)
