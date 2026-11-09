"""Permission-boundary regression tests through the test-version HTTP API."""
import importlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from mailapi_shared.coding_plan_quota import CodingPlanQuotaCache
from test_app.accounts import SESSION_COOKIE
from test_app.site_accounts import SiteAccountStore
from test_app.services.account_credentials import AccountCredentialStore
from test_app.services.resource_accounts import ResourceStore


class SiteAccountHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application_directory = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"TEST_DATA_DIR": cls.application_directory.name}):
            cls.module = importlib.import_module("test_app.main")
        seed_path = Path(cls.application_directory.name) / "seed.json"
        seed = SiteAccountStore(seed_path)
        seed.ensure_admin("admin-password")
        admin_token, admin = seed.login("admin", "admin-password")
        cls.identities = {"admin": admin}
        cls.tokens = {"admin": admin_token}
        for name, role in (("manager", "manager"), ("alice", "user"), ("bob", "user")):
            user = seed.create_user(admin["id"], name, name + "-password", role)
            cls.identities[name] = user
            cls.tokens[name] = seed.login(name, name + "-password")[0]
        cls.seed_data = seed_path.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        for handler in list(cls.module.ERROR_LOGGER.handlers):
            handler.close()
            cls.module.ERROR_LOGGER.removeHandler(handler)
        cls.application_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        site_path = root / "site_accounts.json"
        site_path.write_text(self.seed_data, encoding="utf-8")
        self.store = SiteAccountStore(site_path)
        self.module.app.state.site_accounts = self.store
        self.credentials = AccountCredentialStore(root / "credentials.json")
        for email in ("owned@example.com", "other@example.com"):
            self.credentials.set(email, "password", email + "-gpt-password")
            self.credentials.set(email, "secret", "JBSWY3DPEHPK3PXP")
        self.plans = ResourceStore(root / "plans.json", "api_key", "API key")
        self.relays = ResourceStore(root / "relays.json", "password", "密码")
        self.plan = self.plans.create({"name": "Team API", "api_key": "owned-api-key-secret"})
        self.other_plan = self.plans.create({"name": "Private API", "api_key": "other-api-key-secret"})
        self.relay = self.relays.create({"name": "Team relay", "url": "https://relay.example.com", "account": "relay-login", "password": "owned-relay-password"})
        self.other_relay = self.relays.create({"name": "Private relay", "url": "https://private.example.com", "account": "other-login", "password": "other-relay-password"})
        patches = {
            "GMAIL_ACCOUNTS_FILE": str(root / "mailboxes.json"),
            "MAIL_FILTERS_FILE": str(root / "mail_filters.json"),
            "MAIL_FILTERS": [*self.module.default_mail_filters(), {"id": "siliconflow", "name": "硅基流动验证码"}],
            "CREDENTIAL_STORE": self.credentials, "CODING_PLAN_STORE": self.plans,
            "RELAY_ACCOUNT_STORE": self.relays,
            "ACCOUNTS": [
                {"name": "account-1", "email": "Owned@Example.com", "provider": "gmail", "enabled": False, "resource_id": "a" * 32, "listed": False},
                {"name": "account-2", "email": "other@example.com", "provider": "outlook", "enabled": False, "resource_id": "b" * 32, "listed": False},
            ],
            "CODEX_ACCOUNTS": {
                "codex-1": {"id": "codex-1", "email": "owned@example.com"},
                "codex-2": {"id": "codex-2", "email": "other@example.com"},
            },
            "STATE": {"account-1": {"last_code": "111111"}, "account-2": {"last_code": "222222"}},
            "CODE_HISTORY": {
                "account-1": [{"code": "111111", "rule_id": "openai", "email": "owned@example.com"}],
                "account-2": [{"code": "222222", "rule_id": "openai", "email": "other@example.com"}],
            },
        }
        for name, value in patches.items():
            self.enterContext(patch.object(self.module, name, value))
        self.clients = {}
        for name, token in self.tokens.items():
            client = TestClient(self.module.app, cookies={SESSION_COOKIE: token}, follow_redirects=False)
            self.addCleanup(client.close)
            self.clients[name] = client
        self.admin = self.clients["admin"]
        self.manager = self.clients["manager"]
        self.alice = self.clients["alice"]
        self.bob = self.clients["bob"]
        self.keys = ["gpt:email:owned@example.com", "coding-plan:" + self.plan["id"], "relay-account:" + self.relay["id"]]
        self.group = self.create_group("Team A", [self.identities["alice"]["id"]], self.keys)

    def create_group(self, name, members, keys):
        response = self.admin.post("/api/admin/groups", json={"name": name, "member_ids": members, "resource_keys": keys})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["item"]

    def emails(self, client, suffix=""):
        response = client.get("/api/accounts" + suffix)
        self.assertEqual(response.status_code, 200, response.text)
        return [item["email"] for item in response.json()["items"]]

    def test_gpt_pool_supports_username_only_accounts_and_stable_group_grants(self):
        created = self.admin.post("/api/admin/gpt/accounts", json={"login": "plain-account"})
        self.assertEqual(created.status_code, 201, created.text)
        account = created.json()["item"]
        self.assertFalse(account["has_gpt_password"])
        self.assertFalse(account["has_totp_secret"])
        self.assertEqual(account["mailbox_ids"], [])
        self.admin.put("/api/admin/groups/" + self.group["id"], json={"resource_keys": ["gpt:" + account["key"]]})
        self.assertEqual(self.emails(self.alice), ["plain-account"])
        path = "/api/admin/gpt/accounts/" + account["key"]
        self.assertEqual(self.admin.put(path, json={"login": "renamed-account"}).status_code, 200)
        self.assertEqual(self.emails(self.alice), ["renamed-account"])
        self.assertEqual(self.alice.put(path, json={"mailbox_ids": []}).status_code, 403)
        secret = "/api/accounts/" + account["key"] + "/gpt-password"
        self.assertEqual(self.alice.post(secret).status_code, 404)
        response = self.admin.put("/api/admin/accounts/" + account["key"] + "/gpt-password", json={"password": "private-gpt-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.alice.post(secret).json()["password"], "private-gpt-password")
        self.assertEqual(self.bob.post(secret).status_code, 404)

    def test_mailbox_choices_and_exclusive_binding_follow_gpt_resource_permissions(self):
        key = self.admin.post("/api/admin/gpt/accounts", json={"login": "other"}).json()["item"]["key"]
        path = "/api/admin/gpt/accounts/" + key
        choices = self.admin.get(path + "/mailboxes").json()["items"]
        recommended = next(item for item in choices if item["name"] == "account-2")
        self.assertTrue(recommended["recommended"])
        self.assertTrue(recommended["assigned_to"])
        self.assertEqual(self.admin.put(path, json={"mailbox_ids": ["account-2"]}).status_code, 400)
        for old in ("owned@example.com", "other@example.com"):
            self.assertEqual(self.admin.put("/api/admin/gpt/accounts/email:" + old, json={"mailbox_ids": []}).status_code, 200)
        self.assertEqual(self.admin.put(path, json={"mailbox_ids": ["account-1", "account-2"]}).status_code, 200)
        self.admin.put("/api/admin/groups/" + self.group["id"], json={"resource_keys": ["gpt:" + key]})
        self.assertEqual(self.emails(self.alice), ["other"])
        self.assertEqual(len(self.alice.get("/api/codes").json()["items"]), 2)
        self.assertEqual(self.alice.get("/api/history/account-2").status_code, 200)
        self.assertEqual(self.bob.get("/api/history/account-2").status_code, 404)
        self.assertEqual(self.alice.post("/api/accounts/account-1/gpt-password").status_code, 404)
        self.admin.put(path, json={"mailbox_ids": ["account-1"]})
        self.assertEqual(self.alice.get("/api/history/account-2").status_code, 404)
        self.assertEqual(len(self.alice.get("/api/codes").json()["items"]), 1)

    def test_new_mailbox_does_not_create_gpt_account_and_deleting_gpt_keeps_mailbox(self):
        self.admin.get("/api/accounts")
        self.module.ACCOUNTS.append({"name": "account-3", "email": "new@example.com", "provider": "gmail", "enabled": False})
        self.assertNotIn("new@example.com", self.emails(self.admin))
        self.assertEqual(len(self.admin.get("/api/admin/mail/accounts").json()["items"]), 3)
        self.assertEqual(self.admin.delete("/api/admin/accounts/email:owned@example.com").status_code, 200)
        self.assertTrue(any(item["name"] == "account-1" for item in self.admin.get("/api/admin/mail/accounts").json()["items"]))
        self.assertEqual(self.alice.get("/api/history/account-1").status_code, 404)
        recreated = self.admin.post("/api/admin/gpt/accounts", json={"login": "owned@example.com"}).json()["item"]
        self.assertNotEqual(recreated["key"], "email:owned@example.com")
        self.assertFalse(self.store.resource_allowed(self.identities["alice"]["id"], "gpt:" + recreated["key"]))

    def test_group_member_update_preserves_omitted_resource_assignments(self):
        self.assertEqual(self.admin.delete("/api/admin/coding-plans/" + self.plan["id"]).status_code, 200)
        path = "/api/admin/groups/" + self.group["id"]
        member_ids = [self.identities["bob"]["id"]]
        response = self.manager.put(path, json={"name": "Agent-1", "member_ids": member_ids})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["item"]["member_ids"], member_ids)
        self.assertEqual(response.json()["item"]["resource_keys"], self.keys)
        self.assertEqual(self.emails(self.alice), [])
        self.assertEqual(self.emails(self.bob), ["owned@example.com"])
        self.assertEqual(self.manager.put(path, json={"resource_keys": self.keys}).status_code, 400)

    def test_bulk_resource_assignment_preserves_members_other_resources_and_order(self):
        second = self.create_group("Team B", [self.identities["bob"]["id"]], ["gpt:email:other@example.com"])
        self.assertEqual(self.admin.delete("/api/admin/coding-plans/" + self.plan["id"]).status_code, 200)
        before = self.store.list_groups(self.identities["admin"]["id"])
        response = self.manager.put("/api/admin/resource-groups", json={"resource_key": self.keys[0], "group_ids": [second["id"], second["id"]]})
        self.assertEqual(response.status_code, 200, response.text)
        after = response.json()["items"]
        self.assertEqual([item["id"] for item in after], [item["id"] for item in before])
        for original, updated in zip(before, after):
            self.assertEqual(updated["name"], original["name"])
            self.assertEqual(updated["member_ids"], original["member_ids"])
            self.assertEqual([key for key in updated["resource_keys"] if key != self.keys[0]], [key for key in original["resource_keys"] if key != self.keys[0]])
        self.assertNotIn(self.keys[0], after[0]["resource_keys"])
        self.assertEqual(after[1]["resource_keys"].count(self.keys[0]), 1)
        self.assertEqual(self.emails(self.alice), [])
        self.assertEqual(self.emails(self.bob), ["owned@example.com", "other@example.com"])
        reopened = SiteAccountStore(self.store.path)
        self.assertEqual(reopened.list_groups(self.identities["admin"]["id"]), after)

    def test_bulk_resource_assignment_select_all_and_clear_only_selected_resource(self):
        self.create_group("Team B", [self.identities["bob"]["id"]], [])
        path = "/api/admin/resource-groups"
        before = self.store.list_groups(self.identities["admin"]["id"])
        for key in self.keys:
            response = self.admin.put(path, json={"resource_key": key, "group_ids": [item["id"] for item in before]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(all(key in item["resource_keys"] for item in response.json()["items"]))
            response = self.manager.put(path, json={"resource_key": key, "group_ids": []})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(all(key not in item["resource_keys"] for item in response.json()["items"]))

    def test_bulk_resource_assignment_keeps_mailbox_listing_requirement(self):
        cfg = self.module.ACCOUNTS[0]
        key = self.module.mailbox_resource_key(cfg)
        response = self.manager.put("/api/admin/resource-groups", json={"resource_key": key, "group_ids": [self.group["id"]]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.alice.get("/api/mailboxes").json()["items"], [])
        self.assertFalse(cfg["listed"])
        self.admin.put("/api/admin/mail/accounts/account-1/listing", json={"listed": True})
        self.assertEqual(len(self.alice.get("/api/mailboxes").json()["items"]), 1)
        self.manager.put("/api/admin/resource-groups", json={"resource_key": key, "group_ids": []})
        self.assertEqual(self.alice.get("/api/mailboxes").json()["items"], [])

    def test_bulk_resource_assignment_rejects_invalid_requests_without_partial_writes(self):
        before = self.store.path.read_bytes()
        for payload in (
            {}, {"resource_key": self.keys[0]}, {"group_ids": []},
            {"resource_key": "gpt:missing", "group_ids": []},
            {"resource_key": {}, "group_ids": []},
            {"resource_key": self.keys[0], "group_ids": "all"},
            {"resource_key": self.keys[0], "group_ids": [self.group["id"], "missing"]},
            {"resource_key": self.keys[0], "group_ids": [{}]},
            {"resource_key": self.keys[0], "group_ids": [], "member_ids": []},
        ):
            response = self.admin.put("/api/admin/resource-groups", json=payload)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.store.path.read_bytes(), before)
        response = self.alice.put("/api/admin/resource-groups", json={"resource_key": self.keys[0], "group_ids": []})
        self.assertEqual(response.status_code, 403)
        with self.assertRaisesRegex(ValueError, "需要管理员权限"):
            self.store.assign_resource_groups(self.identities["alice"]["id"], self.keys[0], [], set(self.keys))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_login_uses_new_credentials_and_http_only_cookie(self):
        client = TestClient(self.module.app, follow_redirects=False)
        self.addCleanup(client.close)
        self.assertEqual(client.get("/login").status_code, 200)
        response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("set-cookie", response.headers)
        response = client.post("/login", data={"username": "ADMIN", "password": "admin-password"})
        self.assertEqual(response.status_code, 303, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        self.assertNotIn("admin-password", response.headers["set-cookie"])
        self.assertEqual(client.get("/api/auth/me").json()["user"]["role"], "admin")
        https = TestClient(self.module.app, base_url="https://testserver")
        self.addCleanup(https.close)
        response = https.post("/api/auth/login", json={"username": "admin", "password": "admin-password"})
        self.assertIn("Secure", response.headers["set-cookie"])

    def test_unauthed_requests_and_old_shared_tokens_cannot_bypass_login(self):
        client = TestClient(self.module.app, follow_redirects=False)
        self.addCleanup(client.close)
        for path in ("/api/accounts", "/api/codes", "/api/auth/me", "/api/coding-plans", "/api/admin/users", "/api/codex/accounts"):
            self.assertEqual(client.get(path).status_code, 401, path)
            self.assertEqual(client.get(path + "?access_token=admin-password", headers={"x-access-token": "admin-password", "authorization": "Bearer admin-password"}).status_code, 401)
        self.assertEqual(client.get("/").headers["location"], "/login")
        self.assertEqual(client.get("/static/access.html").headers["location"], "/login")

    def test_both_admin_roles_create_users_but_only_admin_deletes(self):
        for role in ("user", "manager"):
            response = self.manager.post("/api/admin/users", json={"username": "new-" + role, "password": "new-user-password", "role": role})
            self.assertEqual(response.status_code, 201, response.text)
            target_id = response.json()["item"]["id"]
            self.assertNotIn("password", response.text)
            self.assertEqual(self.manager.delete("/api/admin/users/" + target_id).status_code, 403)
            self.assertEqual(self.admin.delete("/api/admin/users/" + target_id).status_code, 200)
        self.assertEqual(self.admin.delete("/api/admin/users/" + self.identities["admin"]["id"]).status_code, 403)

    def test_admin_can_batch_create_users_with_one_initial_password(self):
        response = self.admin.post(
            "/api/admin/users/batch",
            json={"usernames": ["Batch-Alice", "batch-bob"], "password": "batch-password", "role": "user"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        items = response.json()["items"]
        self.assertEqual([item["username"] for item in items], ["batch-alice", "batch-bob"])
        self.assertTrue(all(item["role"] == "user" for item in items))
        self.assertNotIn("batch-password", response.text)
        self.assertTrue(all(self.store.login(item["username"], "batch-password") for item in items))
        hashes = [user["password_hash"] for user in self.store._data["users"].values() if user["username"].startswith("batch-")]
        self.assertEqual(len(hashes), len(set(hashes)))

        before = {item["username"] for item in self.admin.get("/api/admin/users").json()["items"]}
        duplicate = self.admin.post(
            "/api/admin/users/batch",
            json={"usernames": ["atomic-new", "alice"], "password": "batch-password", "role": "user"},
        )
        self.assertEqual(duplicate.status_code, 409)
        after = {item["username"] for item in self.admin.get("/api/admin/users").json()["items"]}
        self.assertEqual(after, before)

        manager_response = self.manager.post(
            "/api/admin/users/batch",
            json={"usernames": ["manager-batch"], "password": "batch-password", "role": "user"},
        )
        self.assertEqual(manager_response.status_code, 403)

    def test_user_cannot_reach_management_or_mutation_endpoints(self):
        for path in ("/api/admin/users", "/api/admin/groups", "/api/admin/resources", "/api/admin/mail/accounts", "/api/codex/accounts"):
            self.assertEqual(self.alice.get(path).status_code, 403, path)
        for method, path in (("post", "/api/admin/users"), ("post", "/api/admin/groups"), ("delete", "/api/history/account-1"), ("delete", "/api/codex/accounts/codex-1")):
            self.assertEqual(getattr(self.alice, method)(path).status_code, 403, path)
        self.assertEqual(self.alice.get("/admin/access").headers["location"], "/")
        self.assertEqual(self.alice.get("/static/admin.html").headers["location"], "/")

    def test_subscription_dates_are_admin_managed_and_use_calendar_months(self):
        key = "email:owned@example.com"
        path = "/api/admin/gpt/accounts/" + key
        self.assertEqual(self.alice.put(path, json={"subscription_started_on": "2026-01-31"}).status_code, 403)
        response = self.admin.put(path, json={"subscription_started_on": "2026-01-31"})
        self.assertEqual(response.status_code, 200, response.text)
        visible = self.alice.get("/api/accounts").json()["items"][0]
        self.assertEqual(visible["subscription_started_on"], "2026-01-31")
        self.assertEqual(visible["subscription_ends_on"], "2026-02-28")
        self.assertEqual(self.admin.put(path, json={"subscription_started_on": "bad"}).status_code, 400)
        self.assertEqual(self.admin.put(path, json={"subscription_ends_on": "2027-01-01"}).status_code, 400)
        self.assertEqual(self.admin.put(path, json={"subscription_started_on": ""}).status_code, 200)
        self.assertEqual(self.alice.get("/api/accounts").json()["items"][0]["subscription_started_on"], "")

    def test_resource_lists_and_secret_aliases_are_group_scoped(self):
        self.assertEqual(self.emails(self.alice), ["owned@example.com"])
        self.assertEqual(self.emails(self.bob), [])
        for path, visible_id in (("/api/coding-plans", self.plan["id"]), ("/api/relay-accounts", self.relay["id"])):
            response = self.alice.get(path)
            self.assertEqual([item["id"] for item in response.json()["items"]], [visible_id])
            self.assertNotIn("secret", response.text)
        for prefix in ("/api/accounts/", "/api/mail/accounts/", "/api/gmail/accounts/"):
            for field in ("gpt-password", "totp-secret"):
                self.assertEqual(self.alice.post(prefix + "account-1/" + field).status_code, 200)
                self.assertEqual(self.alice.post(prefix + "account-2/" + field).status_code, 404)
        self.assertEqual(self.alice.post("/api/accounts/email:owned@example.com/totp-code").status_code, 200)
        self.assertEqual(self.alice.post("/api/accounts/email:other@example.com/totp-code").status_code, 404)
        for path in (f"/api/coding-plans/{self.other_plan['id']}/api-key", f"/api/relay-accounts/{self.other_relay['id']}/password"):
            self.assertEqual(self.alice.get(path).status_code, 404)
            self.assertEqual(self.alice.post(path).status_code, 404)
        self.assertEqual(self.alice.post(f"/api/coding-plans/{self.plan['id']}/api-key").json()["api_key"], "owned-api-key-secret")
        self.assertEqual(self.alice.post(f"/api/relay-accounts/{self.relay['id']}/password").json()["password"], "owned-relay-password")

    def test_coding_plan_quota_uses_existing_group_grants_and_cached_results(self):
        plan_id = self.plan["id"]
        path = f"/api/coding-plans/{plan_id}/quota"
        admin_path = f"/api/admin/coding-plans/{plan_id}"
        windows = [{"label": "5 小时", "remaining_percent": 72, "reset_at": None}]
        with (
            patch.object(self.module, "CODING_PLAN_QUOTAS", CodingPlanQuotaCache()),
            patch.object(self.module, "EXTERNAL_WATCHERS", True),
            patch("mailapi_shared.coding_plan_quota.fetch_coding_plan_quota", return_value=windows) as fetch,
        ):
            self.assertEqual(self.alice.post(path).status_code, 409)
            self.assertEqual(self.admin.put(admin_path, json={"provider": "kimi"}).status_code, 200)
            self.assertEqual(self.bob.post(path).status_code, 404)
            suffix = "?preview=1&user_id=" + self.identities["bob"]["id"]
            self.assertEqual(self.admin.post(path + suffix).status_code, 404)
            item = self.alice.get("/api/coding-plans").json()["items"][0]
            self.assertIsNone(item["quota"])
            self.assertTrue(item["quota_refresh_due"])
            fetch.assert_not_called()
            with patch.object(self.module, "EXTERNAL_WATCHERS", False):
                self.assertFalse(self.alice.get("/api/coding-plans").json()["items"][0]["quota_refresh_due"])
                self.assertEqual(self.alice.post(path).status_code, 409)
            fetch.assert_not_called()

            response = self.alice.post(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("owned-api-key-secret", response.text)
            self.assertEqual(response.json()["quota"]["windows"], windows)
            item = self.alice.get("/api/coding-plans").json()["items"][0]
            self.assertEqual(item["quota"]["windows"], windows)
            self.assertFalse(item["quota_refresh_due"])
            fetch.assert_called_once_with("kimi", "owned-api-key-secret")
            self.assertEqual(self.admin.put(admin_path, json={"api_key": "replacement-key"}).status_code, 200)
            self.assertIsNone(self.alice.get("/api/coding-plans").json()["items"][0]["quota"])

    def test_codes_history_and_query_parameters_cannot_cross_groups(self):
        response = self.alice.get("/api/codes")
        self.assertEqual([item["name"] for item in response.json()["items"]], ["account-1"])
        self.assertNotIn("222222", response.text)
        self.assertEqual(self.alice.get("/api/history/account-1").json()["items"], self.module.CODE_HISTORY["account-1"])
        self.assertEqual(self.alice.get("/api/history/account-2").status_code, 404)
        self.assertEqual(self.emails(self.bob, "?preview=1&user_id=" + self.identities["alice"]["id"]), [])
        self.assertEqual(self.bob.post("/api/accounts/account-1/gpt-password?user_id=" + self.identities["alice"]["id"]).status_code, 404)

    def test_unlisting_also_hides_assigned_credentials_and_history(self):
        self.credentials.set("owned@example.com", "listed", False)
        self.assertEqual(self.emails(self.alice), [])
        self.assertEqual(self.alice.get("/api/codes").json()["items"], [])
        for method, path in (("post", "/api/accounts/account-1/gpt-password"), ("post", "/api/accounts/account-1/totp-code"), ("post", "/api/accounts/account-1/totp-secret"), ("get", "/api/history/account-1")):
            self.assertEqual(getattr(self.alice, method)(path).status_code, 404)
        for client in (self.admin, self.manager):
            self.assertEqual(len(self.emails(client)), 2)
            self.assertEqual(client.get("/admin").status_code, 200)
            self.assertEqual(client.get("/admin/access").status_code, 200)

    def test_membership_and_resource_removal_take_effect_immediately(self):
        path = "/api/admin/groups/" + self.group["id"]
        self.assertEqual(self.manager.put(path, json={"name": "新的组名", "resource_keys": []}).status_code, 200)
        self.assertEqual(self.emails(self.alice), [])
        self.assertEqual(self.alice.post("/api/accounts/account-1/gpt-password").status_code, 404)
        self.assertEqual(self.alice.get("/api/auth/me").json()["groups"][0]["name"], "新的组名")
        self.assertEqual(self.manager.put(path, json={"resource_keys": self.keys}).status_code, 200)
        self.assertEqual(self.emails(self.alice), ["owned@example.com"])
        self.assertEqual(self.manager.put(path, json={"member_ids": []}).status_code, 200)
        self.assertEqual(self.emails(self.alice), [])

    def test_multiple_groups_share_resources_and_group_deletion_revokes_grants(self):
        user_id = self.identities["alice"]["id"]
        second = self.create_group("Team B", [user_id, self.identities["bob"]["id"]], [self.keys[0], "gpt:email:other@example.com"])
        self.assertEqual(set(self.emails(self.alice)), {"owned@example.com", "other@example.com"})
        self.assertEqual(self.manager.delete("/api/admin/groups/" + self.group["id"]).status_code, 200)
        self.assertEqual(set(self.emails(self.alice)), {"owned@example.com", "other@example.com"})
        self.assertEqual(self.manager.delete("/api/admin/groups/" + second["id"]).status_code, 200)
        self.assertEqual(self.emails(self.alice), [])

    def test_group_validation_rejects_unknown_members_resources_and_invalid_json(self):
        for payload in ({"name": ""}, {"name": "Team A"}, {"name": "Bad", "member_ids": ["missing"]}, {"name": "Bad", "resource_keys": ["gpt:email:missing@example.com"]}, {"name": "Bad", "member_ids": "alice"}, {"name": "Bad", "resource_keys": [{}]}, {"name": "Bad", "id": "injected"}):
            self.assertIn(self.admin.post("/api/admin/groups", json=payload).status_code, (400, 409), payload)
        for content in ("[]", "null", "not-json"):
            self.assertEqual(self.admin.post("/api/admin/groups", content=content).status_code, 400)
        self.assertEqual(self.admin.post("/api/admin/groups", content="x" * 65537).status_code, 413)

    def test_reserved_admin_and_invalid_roles_are_rejected(self):
        for payload in ({"username": "Admin", "role": "user"}, {"username": "new-user", "role": "admin"}, {"username": "new-user", "role": {}}, {"username": "ab", "role": "user"}):
            self.assertEqual(self.admin.post("/api/admin/users", json={**payload, "password": "new-user-password"}).status_code, 400)
        self.assertEqual(self.admin.put("/api/admin/users/" + self.identities["alice"]["id"], json={"role": []}).status_code, 400)

    def test_deletion_removes_sessions_and_membership_without_inheriting_grants(self):
        target = self.identities["alice"]["id"]
        self.assertEqual(self.admin.delete("/api/admin/users/" + target).status_code, 200)
        self.assertEqual(self.alice.get("/api/auth/me").status_code, 401)
        self.assertNotIn(target, self.admin.get("/api/admin/groups").json()["items"][0]["member_ids"])
        replacement = self.admin.post("/api/admin/users", json={"username": "alice", "password": "replacement-password"}).json()["item"]
        self.assertNotEqual(replacement["id"], target)
        self.assertFalse(self.store.resource_allowed(replacement["id"], self.keys[0]))

    def test_only_admin_changes_roles_and_role_change_revokes_sessions(self):
        target = self.identities["alice"]["id"]
        self.assertEqual(self.manager.put("/api/admin/users/" + target, json={"role": "manager"}).status_code, 403)
        self.assertEqual(self.admin.put("/api/admin/users/" + target, json={"role": "manager"}).status_code, 200)
        self.assertEqual(self.alice.get("/api/admin/users").status_code, 401)
        self.alice.cookies.clear()
        self.assertEqual(self.alice.post("/api/auth/login", json={"username": "alice", "password": "alice-password"}).status_code, 200)
        self.assertEqual(self.alice.get("/api/admin/users").status_code, 200)
        self.assertEqual(self.admin.put("/api/admin/users/" + target, json={"role": "user"}).status_code, 200)
        self.assertEqual(self.alice.get("/api/admin/users").status_code, 401)

    def test_password_reset_revokes_old_sessions_and_protects_administrators(self):
        for name in ("admin", "manager"):
            self.assertEqual(self.manager.put("/api/admin/users/" + self.identities[name]["id"], json={"password": "replacement-password"}).status_code, 403)
        self.assertEqual(self.manager.put("/api/admin/users/" + self.identities["alice"]["id"], json={"password": "replacement-password"}).status_code, 200)
        self.assertEqual(self.alice.get("/api/auth/me").status_code, 401)
        response = self.alice.post("/api/auth/login", json={"username": "alice", "password": "replacement-password"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.alice.get("/api/auth/me").status_code, 200)

    def test_self_password_change_requires_current_password_and_revokes_sessions(self):
        self.assertEqual(self.alice.post("/api/auth/password", json={"current_password": "wrong", "new_password": "replacement-password"}).status_code, 403)
        self.assertEqual(self.alice.get("/api/auth/me").status_code, 200)
        self.assertEqual(self.alice.post("/api/auth/password", json={"current_password": "alice-password", "new_password": "replacement-password"}).status_code, 200)
        self.assertEqual(self.alice.get("/api/auth/me").status_code, 401)

    def test_cross_site_mutations_are_rejected_and_logout_revokes_token(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "https://testserver"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.alice.post("/api/auth/logout", headers=headers).status_code, 403)
        self.assertEqual(self.alice.get("/api/auth/me").status_code, 200)
        self.assertEqual(self.alice.post("/api/auth/logout", headers={"Origin": "http://testserver"}).status_code, 200)
        self.assertIsNone(self.store.session_user(self.tokens["alice"]))

    def test_admin_preview_uses_exact_user_grants_for_lists_and_secrets(self):
        suffix = "?preview=1&user_id=" + self.identities["alice"]["id"]
        self.assertEqual(self.emails(self.admin, suffix), ["owned@example.com"])
        self.assertEqual(self.admin.post("/api/accounts/account-2/gpt-password" + suffix).status_code, 404)
        self.assertEqual(self.admin.get(f"/api/coding-plans/{self.other_plan['id']}/api-key" + suffix).status_code, 404)
        page = self.admin.get("/preview?user_id=" + self.identities["alice"]["id"])
        self.assertIn(self.identities["alice"]["id"], page.text)
        self.assertIn("window.__PREVIEW_USER_ID__", page.text)
        self.assertEqual(self.admin.get("/preview?user_id=missing").status_code, 404)

    def test_persistence_password_hashes_and_session_expiry(self):
        reopened = SiteAccountStore(self.store.path)
        reopened.ensure_admin("different-bootstrap-password")
        self.assertIsNotNone(reopened.login("admin", "admin-password"))
        self.assertTrue(reopened.resource_allowed(self.identities["alice"]["id"], self.keys[0]))
        self.assertEqual(reopened.session_user(self.tokens["alice"])["username"], "alice")
        stored = self.store.path.read_text()
        for secret in ("admin-password", "alice-password", self.tokens["alice"]):
            self.assertNotIn(secret, stored)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)
        with patch("test_app.site_accounts.time.time", return_value=time.time() + 86401):
            self.assertIsNone(reopened.session_user(self.tokens["alice"]))

    def test_resource_catalog_and_identity_responses_do_not_expose_credentials(self):
        catalog = self.manager.get("/api/admin/resources")
        self.assertEqual(len(catalog.json()["items"]), 8)
        for secret in ("password", "secret", "JBSWY", "api_key"):
            self.assertNotIn(secret, catalog.text)
        self.assertEqual(self.alice.get("/api/auth/me").json()["groups"], [{"id": self.group["id"], "name": "Team A"}])
        response = self.alice.get("/api/accounts")
        self.assertEqual(response.headers["cache-control"], "no-store, private")

    def test_mailbox_listing_persists_without_enabling_watchers_and_requires_a_group(self):
        self.assertEqual(self.bob.get("/api/mailboxes").json()["items"], [])
        catalog = self.admin.get("/api/admin/resources").json()["items"]
        mailbox = next(item for item in catalog if item["kind"] == "mailbox" and item["name"] == "Owned@Example.com")
        self.assertFalse(mailbox["listed"])
        path = "/api/admin/mail/accounts/account-1/listing"
        self.assertEqual(self.bob.put(path, json={"listed": True}).status_code, 403)
        self.assertEqual(self.admin.put(path, json={"listed": "true"}).status_code, 400)
        with patch.object(self.module, "start_mail_watcher") as watcher:
            self.assertEqual(self.manager.put(path, json={"listed": True}).status_code, 200)
            watcher.assert_not_called()
        self.assertFalse(self.module.ACCOUNTS[0]["enabled"])
        reloaded = self.module.load_accounts()[0]
        self.assertTrue(reloaded["listed"])
        self.assertEqual(self.module.mailbox_resource_key(reloaded), mailbox["key"])
        self.assertEqual(self.bob.get("/api/mailboxes").json()["items"], [])
        self.create_group("Mailbox readers", [self.identities["bob"]["id"]], [mailbox["key"]])
        items = self.bob.get("/api/mailboxes").json()["items"]
        self.assertEqual([item["name"] for item in items], ["account-1"])
        self.assertNotIn("has_gpt_password", items[0])
        self.assertNotIn("token_file", items[0])
        self.assertEqual(self.bob.post("/api/accounts/account-1/gpt-password").status_code, 404)

    def test_pending_gmail_reauthorization_does_not_restore_an_older_listing_state(self):
        cfg = self.module.ACCOUNTS[0]
        cfg["note"] = "新的接码说明"
        raw = {**cfg, "note": "旧说明", "listed": True, "token_file": str(Path(self.directory.name) / "token.json")}
        service = Mock()
        service.users.return_value.getProfile.return_value.execute.return_value = {"emailAddress": cfg["email"]}
        credentials = Mock()
        credentials.to_json.return_value = "{}"
        with patch.object(self.module, "build", return_value=service), patch.object(self.module, "start_mail_watcher"), patch.object(self.module, "stop_mail_watcher"):
            updated = self.module.complete_managed_gmail_auth({"cfg": raw, "managed_action": "reauth"}, credentials)
        self.assertFalse(updated["listed"])
        self.assertEqual(updated["resource_id"], cfg["resource_id"])
        self.assertEqual(updated["note"], "新的接码说明")

    def test_pending_outlook_reauthorization_preserves_latest_mailbox_note(self):
        cfg = self.module.ACCOUNTS[1]
        cfg["note"] = "新的 Outlook 接码说明"
        raw = {**cfg, "note": "旧说明", "token_file": str(Path(self.directory.name) / "outlook-token.json")}
        authorization = Mock(spec=self.module.OutlookAuthorization)
        authorization.complete.return_value = "isolated-access-token"
        authorization.serialized_cache.return_value = "{}"
        with patch.object(self.module, "fetch_outlook_profile", return_value={"email": cfg["email"]}), patch.object(self.module, "start_mail_watcher"), patch.object(self.module, "stop_mail_watcher"):
            updated = self.module.complete_managed_outlook_auth({"cfg": raw, "managed_action": "reauth", "authorization": authorization, "flow": {}}, {})
        self.assertEqual(updated["note"], "新的 Outlook 接码说明")

    def test_mailbox_note_persists_and_follows_independent_resource_permissions(self):
        cfg = self.module.ACCOUNTS[0]
        before = dict(cfg)
        note = "接收硅基流动登录码\nKimi 验证码"
        path = "/api/admin/mail/accounts/account-1/note"
        with patch.object(self.module, "start_mail_watcher") as start, patch.object(self.module, "stop_mail_watcher") as stop:
            response = self.manager.put(path, json={"note": " " + note + " "})
            self.assertEqual(response.status_code, 200, response.text)
            start.assert_not_called()
            stop.assert_not_called()
        self.assertEqual({key: value for key, value in cfg.items() if key != "note"}, before)
        self.assertEqual(self.module.load_accounts()[0]["note"], note)
        self.assertEqual(self.admin.get("/api/admin/mail/accounts").json()["items"][0]["note"], note)
        self.assertNotIn('"note"', self.alice.get("/api/accounts").text)
        self.assertNotIn('"note"', self.alice.get("/api/codes").text)
        key = self.module.mailbox_resource_key(cfg)
        self.create_group("Mailbox notes", [self.identities["bob"]["id"]], [key])
        self.assertEqual(self.bob.get("/api/mailboxes").json()["items"], [])
        self.admin.put("/api/admin/mail/accounts/account-1/listing", json={"listed": True})
        self.assertEqual(self.bob.get("/api/mailboxes").json()["items"][0]["note"], note)
        self.assertEqual(self.alice.get("/api/mailboxes").json()["items"], [])
        preview = self.admin.get("/api/mailboxes", params={"preview": "1", "user_id": self.identities["bob"]["id"]})
        self.assertEqual(preview.json()["items"][0]["note"], note)
        self.assertEqual(self.manager.put(path, json={"note": ""}).status_code, 200)
        self.assertEqual(self.module.load_accounts()[0]["note"], "")

    def test_mailbox_note_rejects_invalid_or_unauthorized_updates(self):
        path = "/api/admin/mail/accounts/account-1/note"
        before = dict(self.module.ACCOUNTS[0])
        for payload in ({}, {"note": None}, {"note": 123}, {"note": "x" * 501}, {"note": "说明", "listed": True}):
            self.assertEqual(self.admin.put(path, json=payload).status_code, 400)
        self.assertEqual(self.alice.put(path, json={"note": "说明"}).status_code, 403)
        self.assertEqual(self.admin.put("/api/admin/mail/accounts/missing/note", json={"note": "说明"}).status_code, 404)
        self.assertEqual(self.module.ACCOUNTS[0], before)

    def test_passwordless_relay_can_be_created_assigned_and_edited(self):
        payload = {"name": "硅基流动", "url": "https://relay.example.com", "account": "user@example.com"}
        response = self.manager.post("/api/admin/relay-accounts", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["item"]
        self.assertFalse(item["has_password"])
        path = "/api/admin/relay-accounts/" + item["id"]
        self.assertEqual(self.manager.put(path, json={"note": "邮箱验证码登录"}).status_code, 200)
        self.assertEqual(ResourceStore(self.relays.path, "password", "密码").get_secret(item["id"]), "")
        self.admin.put("/api/admin/groups/" + self.group["id"], json={"resource_keys": ["relay-account:" + item["id"]]})
        self.assertFalse(self.alice.get("/api/relay-accounts").json()["items"][0]["has_password"])
        self.assertEqual(self.alice.post("/api/relay-accounts/" + item["id"] + "/password").status_code, 404)
        response = self.manager.put(path, json={"password": "relay-test-password"})
        self.assertTrue(response.json()["item"]["has_password"])
        self.manager.put(path, json={"password": "", "note": "新备注"})
        self.assertEqual(self.alice.post("/api/relay-accounts/" + item["id"] + "/password").json()["password"], "relay-test-password")
        self.assertEqual(self.bob.post("/api/relay-accounts/" + item["id"] + "/password").status_code, 404)

    def test_relay_mailbox_access_follows_the_relay_grant_without_granting_standalone_access(self):
        cfg = self.module.ACCOUNTS[1]
        key = self.module.mailbox_resource_key(cfg)
        relay_id = self.relay["id"]
        self.module.CODE_HISTORY[cfg["name"]][0:0] = [
            {"email": cfg["email"], "code": "999999", "rule_name": "硅基流动验证码"},
            {"email": cfg["email"], "code": "321321", "rule_id": "siliconflow"},
        ]
        self.module.STATE[cfg["name"]].update(email=cfg["email"], last_code="222222", rule_id="openai")
        response = self.manager.put(f"/api/admin/relay-accounts/{relay_id}", json={
            "mailbox_key": key, "mail_rule_id": "siliconflow",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.manager.get("/api/relay-accounts").json()["items"][0]["mailbox_key"], key)
        listed = self.alice.get("/api/relay-accounts").json()["items"][0]
        self.assertTrue(listed["has_mailbox"])
        self.assertNotIn("mailbox_key", listed)
        self.assertNotIn("mailbox_email", listed)
        self.assertNotIn("mail_rule_id", listed)

        path = f"/api/relay-accounts/{relay_id}/mailbox"
        response = self.alice.get(path, params={
            "mailbox_key": self.module.mailbox_resource_key(self.module.ACCOUNTS[0]), "rule_id": "openai",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["item"]["email"], cfg["email"])
        self.assertFalse(response.json()["item"]["listed"])
        self.assertEqual(response.json()["item"]["last_code"], "321321")
        self.assertEqual([item["code"] for item in response.json()["history"]], ["321321"])
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(self.alice.get("/api/mailboxes").json()["items"], [])
        self.assertEqual(self.alice.get(f"/api/history/{cfg['name']}?scope=mailbox").status_code, 404)
        self.assertEqual(self.alice.get(f"/api/history/{cfg['name']}").status_code, 404)
        self.assertEqual(self.bob.get(path).status_code, 404)
        preview = {"preview": "1", "user_id": self.identities["bob"]["id"]}
        self.assertEqual(self.admin.get(path, params=preview).status_code, 404)
        self.manager.put(f"/api/admin/mail/accounts/{cfg['name']}/listing", json={"listed": True})
        self.create_group("Standalone mailbox", [self.identities["bob"]["id"]], [key])
        self.assertEqual(self.bob.get("/api/mailboxes").json()["items"][0]["last_code"], "222222")
        full = self.bob.get(f"/api/history/{cfg['name']}?scope=mailbox").json()["items"]
        self.assertEqual([item["code"] for item in full], ["999999", "321321", "222222"])
        self.manager.put(f"/api/admin/groups/{self.group['id']}", json={"resource_keys": []})
        self.assertEqual(self.alice.get(path).status_code, 404)

    def test_relay_mailbox_binding_survives_reload_and_does_not_follow_a_reused_mailbox_name(self):
        cfg = self.module.ACCOUNTS[1]
        key = self.module.mailbox_resource_key(cfg)
        path = f"/api/admin/relay-accounts/{self.relay['id']}"
        self.assertEqual(self.manager.put(path, json={"mailbox_key": "mailbox:unknown"}).status_code, 400)
        self.assertEqual(self.manager.put(path, json={"mailbox_key": {}}).status_code, 400)
        self.assertEqual(self.alice.put(path, json={"mailbox_key": key}).status_code, 403)
        self.assertEqual(self.manager.put(path, json={"mailbox_key": key}).status_code, 200)
        read_path = f"/api/relay-accounts/{self.relay['id']}/mailbox"
        self.assertEqual(self.alice.get(read_path).status_code, 404)
        self.assertFalse(self.alice.get("/api/relay-accounts").json()["items"][0]["has_mailbox"])
        self.assertEqual(self.manager.put(path, json={"mail_rule_id": "unknown"}).status_code, 400)
        self.assertEqual(self.manager.put(path, json={"mail_rule_id": "siliconflow"}).status_code, 200)
        empty = self.alice.get(read_path).json()
        self.assertIsNone(empty["item"]["last_code"])
        self.assertEqual(empty["history"], [])
        self.assertEqual(self.manager.put(path, json={"note": "验证码登录"}).status_code, 200)
        reopened = ResourceStore(self.relays.path, "password", "密码")
        self.assertEqual(reopened.get_public(self.relay["id"])["mailbox_key"], key)
        self.assertEqual(reopened.get_public(self.relay["id"])["mail_rule_id"], "siliconflow")
        self.assertEqual(self.manager.delete("/api/admin/mail-filters/siliconflow").status_code, 200)
        self.assertEqual(self.alice.get(read_path).status_code, 404)
        self.assertEqual(self.manager.put(path, json={"mail_rule_id": "openai"}).status_code, 200)
        self.assertEqual(self.alice.get(read_path).status_code, 200)
        self.module.ACCOUNTS[1] = {**cfg, "resource_id": "c" * 32, "email": "replacement@example.com"}
        self.assertEqual(self.alice.get(read_path).status_code, 404)
        self.assertFalse(self.alice.get("/api/relay-accounts").json()["items"][0]["has_mailbox"])
        response = self.manager.put(path, json={"mailbox_key": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item"]["mailbox_key"], "")

    def test_gpt_results_are_separated_from_full_mailbox_history_and_union_grants(self):
        cfg = self.module.ACCOUNTS[0]
        address = cfg["email"]
        self.module.CODE_HISTORY["account-1"] = [
            {"type": "link", "value": "https://claude.ai/login#private", "rule_id": "claude", "rule_name": "Claude", "email": address, "subject": "Claude login"},
            {"type": "code", "value": "111111", "code": "111111", "rule_id": "openai", "email": address, "subject": "OpenAI code"},
            {"code": "333333", "email": address, "subject": "Legacy unknown"},
            {"code": "999999", "rule_id": "openai", "email": "previous-owner@example.com"},
        ]
        self.module.STATE["account-1"].update(email=address, result_type="link", result_value="https://claude.ai/login#private", rule_id="claude", subject="Claude login")
        gpt = self.alice.get("/api/accounts")
        self.assertEqual(gpt.json()["items"][0]["mail"][0]["last_code"], "111111")
        self.assertNotIn("claude.ai", gpt.text)
        self.assertNotIn("Legacy unknown", gpt.text)
        self.assertEqual(len(self.alice.get("/api/history/account-1").json()["items"]), 1)
        self.assertEqual(self.alice.get("/api/history/account-1?scope=mailbox").status_code, 404)
        self.assertNotIn("claude.ai", self.alice.get("/api/codes").text)
        self.assertFalse(self.module.is_gpt_mail_result({"rule_id": "custom", "rule_name": "OpenAI 验证码"}))
        self.admin.put("/api/admin/mail/accounts/account-1/listing", json={"listed": True})
        self.create_group("Mailbox readers", [self.identities["bob"]["id"]], [self.module.mailbox_resource_key(cfg)])
        self.assertIn("claude.ai", self.bob.get("/api/mailboxes").text)
        full = self.bob.get("/api/history/account-1?scope=mailbox")
        self.assertEqual(len(full.json()["items"]), 3)
        self.assertNotIn("999999", full.text)
        self.assertEqual(self.bob.get("/api/history/account-1?scope=invalid").status_code, 400)
        self.assertEqual(self.bob.get("/api/mailboxes?preview=1&user_id=" + self.identities["alice"]["id"]).json()["items"][0]["name"], "account-1")
        self.assertEqual(self.admin.get("/api/mailboxes?preview=1&user_id=" + self.identities["alice"]["id"]).json()["items"], [])
        self.admin.put("/api/admin/mail/accounts/account-1/listing", json={"listed": False})
        self.assertEqual(self.bob.get("/api/mailboxes").json()["items"], [])
        self.assertEqual(self.bob.get("/api/history/account-1?scope=mailbox").status_code, 404)
        self.assertEqual(self.bob.get("/api/history/account-1").status_code, 404)
        self.assertEqual(self.alice.get("/api/history/account-1").status_code, 200)

    def test_reused_mailbox_names_do_not_inherit_old_grants_or_history(self):
        old = self.module.mailbox_resource_key(self.module.ACCOUNTS[0])
        self.create_group("Mailbox readers", [self.identities["bob"]["id"]], [old])
        self.assertEqual(self.admin.delete("/api/admin/mail/accounts/account-1").status_code, 200)
        replacement = {"name": "account-1", "email": "replacement@example.com", "provider": "gmail", "enabled": False}
        self.module.ACCOUNTS.append(replacement)
        self.module.save_mail_accounts()
        self.module.CODE_HISTORY["account-1"] = [{"code": "111111", "rule_id": "openai", "email": "owned@example.com"}]
        self.assertNotEqual(old, self.module.mailbox_resource_key(replacement))
        self.admin.put("/api/admin/mail/accounts/account-1/listing", json={"listed": True})
        self.assertEqual(self.bob.get("/api/mailboxes").json()["items"], [])
        self.assertEqual(self.bob.get("/api/history/account-1?scope=mailbox").status_code, 404)
        self.assertEqual(self.admin.get("/api/history/account-1?scope=mailbox").json()["items"], [])

    def test_relay_url_is_required_and_shared_through_group_scoped_lists(self):
        payload = {"name": "新中转站", "account": "user", "password": "relay-password"}
        self.assertEqual(self.manager.post("/api/admin/relay-accounts", json=payload).status_code, 400)
        self.assertEqual(self.manager.post("/api/admin/relay-accounts", json={**payload, "url": "javascript:alert(1)"}).status_code, 400)
        response = self.manager.post("/api/admin/relay-accounts", json={**payload, "url": "https://new.example.com/panel"})
        self.assertEqual(response.status_code, 201, response.text)
        item = response.json()["item"]
        self.assertEqual(item["url"], "https://new.example.com/panel")
        self.assertEqual(self.manager.put("/api/admin/relay-accounts/" + item["id"], json={"note": "新备注"}).status_code, 200)
        self.admin.put("/api/admin/groups/" + self.group["id"], json={"resource_keys": ["relay-account:" + item["id"]]})
        visible = self.alice.get("/api/relay-accounts").json()["items"]
        self.assertEqual([(value["name"], value["url"]) for value in visible], [("新中转站", "https://new.example.com/panel")])
        self.assertNotIn("relay-password", self.alice.get("/api/relay-accounts").text)


if __name__ == "__main__":
    unittest.main()
