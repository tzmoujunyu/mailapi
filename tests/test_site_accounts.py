"""Permission-boundary regression tests through the test-version HTTP API."""
import importlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

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
            "CREDENTIAL_STORE": self.credentials, "CODING_PLAN_STORE": self.plans,
            "RELAY_ACCOUNT_STORE": self.relays,
            "ACCOUNTS": [
                {"name": "account-1", "email": "Owned@Example.com", "provider": "gmail", "enabled": False},
                {"name": "account-2", "email": "other@example.com", "provider": "outlook", "enabled": False},
            ],
            "CODEX_ACCOUNTS": {
                "codex-1": {"id": "codex-1", "email": "owned@example.com"},
                "codex-2": {"id": "codex-2", "email": "other@example.com"},
            },
            "STATE": {"account-1": {"last_code": "111111"}, "account-2": {"last_code": "222222"}},
            "CODE_HISTORY": {"account-1": [{"code": "111111"}], "account-2": [{"code": "222222"}]},
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

    def test_codes_history_and_query_parameters_cannot_cross_groups(self):
        response = self.alice.get("/api/codes")
        self.assertEqual([item["name"] for item in response.json()["items"]], ["account-1"])
        self.assertNotIn("222222", response.text)
        self.assertEqual(self.alice.get("/api/history/account-1").json()["items"], [{"code": "111111"}])
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

    def test_only_admin_changes_roles_and_manager_session_demotes_immediately(self):
        target = self.identities["alice"]["id"]
        self.assertEqual(self.manager.put("/api/admin/users/" + target, json={"role": "manager"}).status_code, 403)
        self.assertEqual(self.admin.put("/api/admin/users/" + target, json={"role": "manager"}).status_code, 200)
        self.assertEqual(self.alice.get("/api/admin/users").status_code, 200)
        self.assertEqual(self.admin.put("/api/admin/users/" + target, json={"role": "user"}).status_code, 200)
        self.assertEqual(self.alice.get("/api/admin/users").status_code, 403)

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
        self.assertEqual(self.alice.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.alice.post("/api/auth/logout", headers={"sec-fetch-site": "cross-site"}).status_code, 403)
        self.assertEqual(self.alice.get("/api/auth/me").status_code, 200)
        self.assertEqual(self.alice.post("/api/auth/logout").status_code, 200)
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
        self.assertEqual(len(catalog.json()["items"]), 6)
        for secret in ("password", "secret", "JBSWY", "api_key"):
            self.assertNotIn(secret, catalog.text)
        self.assertEqual(self.alice.get("/api/auth/me").json()["groups"], [{"id": self.group["id"], "name": "Team A"}])
        response = self.alice.get("/api/accounts")
        self.assertEqual(response.headers["cache-control"], "no-store, private")

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
