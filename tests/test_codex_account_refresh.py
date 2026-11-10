import base64
from contextlib import ExitStack, contextmanager
from account_credentials import AccountCredentialStore
from fastapi.testclient import TestClient
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import jwt
import pyotp
from cryptography.hazmat.primitives.asymmetric import rsa
from types import SimpleNamespace

from mailapi_shared.accounts import SESSION_COOKIE
from mailapi_shared.site_accounts import SiteAccountStore


ROOT = Path(__file__).resolve().parents[1]
ADMIN_PASSWORD = "test-admin-password"
USER_PASSWORD = "test-user-password"
ADMIN_TOTP = pyotp.TOTP("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")


class CodexAccountRefreshTests(unittest.TestCase):
    @staticmethod
    def subscription_token(checked, account_id="account-id", workspace_id="workspace-id"):
        def iso(value):
            from datetime import datetime, timezone
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        claims = {"sub": "user-id", "email": "user@example.com", "exp": checked - 1,
                  "workspace_id": workspace_id, "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id, "chatgpt_plan_type": "pro",
            "chatgpt_subscription_active_start": iso(checked - 86400),
            "chatgpt_subscription_active_until": iso(checked + 29 * 86400),
            "chatgpt_subscription_last_checked": iso(checked),
        }}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return "header." + encoded + ".signature"

    def test_subscription_dates_are_local_and_bound_to_the_exact_account_and_workspace(self):
        checked = int(time.time()) - 3600
        record = {"chatgpt_account_id": "account-id", "workspace_id": "workspace-id",
                  "token": {"access_token": "access-secret", "id_token": self.subscription_token(checked)}}
        with patch.object(self.module, "http_json", side_effect=AssertionError("Unexpected network")) as network:
            result = self.module.sanitize_codex_account(record)
            network.assert_not_called()
        self.assertTrue(result["subscription"]["available"])
        self.assertEqual(result["subscription"]["checked_at"], checked)
        self.assertNotIn("access-secret", json.dumps(result))
        snapshot = self.module.id_token_subscription(record)
        snapshot["checked_at"] = int(time.time()) + 86400
        self.assertEqual(self.module.sanitize_codex_account({**record, "subscription": snapshot})["subscription"]["checked_at"], checked)
        snapshot["checked_at"] = None
        file_only_dates = {**record, "subscription": snapshot, "token": {"id_token": ""}}
        self.assertTrue(self.module.sanitize_codex_account(file_only_dates)["subscription"]["available"])
        self.assertIsNone(self.module.sanitize_codex_account(file_only_dates)["subscription"]["checked_at"])
        for field in ("chatgpt_account_id", "workspace_id"):
            with self.subTest(field=field):
                result = self.module.sanitize_codex_account({**record, field: "another-id"})
                self.assertEqual(result["subscription"]["unavailable_reason"], "account_mismatch")
                self.assertIsNone(result["subscription"]["expires_at"])

    def test_auth_file_dates_are_imported_without_replacing_running_tokens(self):
        checked = int(time.time()) - 3600
        payload = {"access_token": "runtime-access", "id_token": self.subscription_token(checked - 86400),
                   "refresh_token": "runtime-refresh", "email": "user@example.com"}
        record = self.module.build_codex_account_record(payload)
        tokens = dict(record["token"])
        file_payload = {"tokens": {"access_token": "file-access", "refresh_token": "file-refresh",
                                   "id_token": self.subscription_token(checked)},
                        "chatgpt_account_id": "account-id", "workspace_id": "workspace-id", "email": "user@example.com"}
        with self.account_client(), tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text(json.dumps(file_payload))
            original = path.read_bytes()
            with patch.object(self.module, "CODEX_AUTH_DIR", directory), patch.object(self.module, "CODEX_ACCOUNTS", {record["id"]: record}), \
                    patch.object(self.module, "http_json", side_effect=AssertionError("Unexpected network")) as network:
                self.module.import_codex_auth_file(refresh_snapshot=False, overwrite_existing_tokens=False)
                updated = self.module.CODEX_ACCOUNTS[record["id"]]
                self.assertEqual(updated["token"], tokens)
                self.assertEqual(updated["subscription"]["checked_at"], checked)
                self.assertEqual(self.module.sanitize_codex_account(updated)["subscription"]["checked_at"], checked)
                missing_cached_id = {**updated, "token": {**tokens, "id_token": ""}}
                self.assertEqual(self.module.sanitize_codex_account(missing_cached_id)["subscription"]["checked_at"], checked)
                self.assertEqual(path.read_bytes(), original)
                network.assert_not_called()

    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.previous_directory = os.getcwd()
        temporary_path = Path(cls.temporary_directory.name)
        (temporary_path / "static").mkdir()
        seed = SiteAccountStore(temporary_path / "site-seed.json", require_admin_mfa=True)
        seed.provision_admin("admin", ADMIN_PASSWORD, ADMIN_TOTP.secret)
        earlier = time.time() - 60
        with patch("mailapi_shared.site_accounts.time.time", return_value=earlier):
            cls.admin_token, admin = seed.login("admin", ADMIN_PASSWORD, ADMIN_TOTP.at(int(earlier)))
        cls.user_id = seed.create_user(admin["id"], "reader", USER_PASSWORD)["id"]
        cls.user_token, _ = seed.login("reader", USER_PASSWORD)
        resources = ["gpt:email:user@example.com", "gpt:email:only@example.com"]
        seed.save_group(admin["id"], None, {
            "name": "Readers", "member_ids": [cls.user_id], "resource_keys": resources,
        }, set(resources))
        cls.site_seed = seed.path.read_bytes()
        os.chdir(temporary_path)
        try:
            with patch.dict(
                os.environ,
                {
                    "ACCESS_PASSWORD": "test-access-password",
                    "ADMIN_ACCESS_PASSWORD": "test-admin-password",
                    "MAIL_RELAY_SECRET": "test-mail-relay-secret-32-characters",
                },
            ):
                spec = importlib.util.spec_from_file_location(
                    "mailapi_code_receive_for_tests",
                    ROOT / "code-receive.py",
                )
                cls.module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                sys.modules[spec.name] = cls.module
                spec.loader.exec_module(cls.module)
        finally:
            os.chdir(cls.previous_directory)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("mailapi_code_receive_for_tests", None)
        for handler in list(cls.module.ERROR_LOGGER.handlers):
            handler.close()
            cls.module.ERROR_LOGGER.removeHandler(handler)
        cls.temporary_directory.cleanup()

    @contextmanager
    def account_client(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            module = self.module
            store = AccountCredentialStore(Path(directory) / "credentials.json")
            stack.enter_context(patch.object(module, "CREDENTIAL_STORE", store))
            stack.enter_context(patch.object(module, "ACCOUNTS", [
                {"name": "account-1", "email": "User@Example.com", "provider": "gmail", "enabled": True}
            ]))
            stack.enter_context(patch.object(module, "CODEX_ACCOUNTS", {
                "codex-1": {"id": "codex-1", "email": "user@example.com"},
                "codex-2": {"id": "codex-2", "email": "only@example.com"},
            }))
            stack.enter_context(patch.object(module, "STATE", {}))
            stack.enter_context(patch.object(module, "CODE_HISTORY", {}))
            for name in ("save_mail_accounts", "save_codex_accounts", "stop_mail_watcher", "delete_codex_record_auth_files"):
                stack.enter_context(patch.object(module, name))
            site_path = Path(directory) / "site_accounts.json"
            site_path.write_bytes(self.site_seed)
            stack.enter_context(patch.object(module.app.state, "site_accounts", SiteAccountStore(site_path, require_admin_mfa=True)))
            client = TestClient(module.app, cookies={SESSION_COOKIE: self.admin_token})
            yield client, store
            client.close()

    def use_session(self, client, token):
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, token)

    def test_member_edits_shared_gpt_and_admin_can_search_and_review(self):
        with self.account_client() as (client, store):
            key = "email:user@example.com"
            admin_path = f"/api/admin/gpt/accounts/{key}"
            changes_path = admin_path + "/changes"
            edit_path = f"/api/accounts/gpt/{key}/info"
            store_account = client.put(admin_path, json={"password": "before-password"}).json()["item"]
            revision = store_account["revision"]
            self.assertTrue(revision)
            self.assertEqual(client.put(edit_path + "?preview=1", json={"revision": revision, "login": "blocked"}).status_code, 404)
            unassigned = client.post("/api/admin/gpt/accounts", json={"login": "other@example.com"}).json()["item"]
            self.use_session(client, self.user_token)
            self.assertEqual(client.put(f"/api/accounts/gpt/{unassigned['key']}/info", json={"revision": unassigned["revision"], "login": "blocked"}).status_code, 404)
            self.assertEqual(client.put(edit_path, json={"revision": revision, "mailbox_ids": []}).status_code, 400)
            self.assertEqual(client.put(edit_path, json={"revision": revision, "password": "blocked"}, headers={"Origin": "https://wrong.example"}).status_code, 403)
            edited = client.put(edit_path, json={"revision": revision, "login": "Renamed@example.com",
                                "subscription_expires_on": "2027-01-05", "password": "after-password",
                                "secret": "JBSWY3DPEHPK3PXP"})
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual(edited.json()["item"]["mailbox_ids"], ["account-1"])
            self.assertEqual(client.put(edit_path, json={"revision": revision, "login": "stale"}).status_code, 409)
            self.assertEqual(client.get(changes_path).status_code, 403)
            self.assertEqual(store.get("renamed@example.com", "password"), "after-password")
            self.use_session(client, self.admin_token)
            results = client.get(changes_path, params={"q": "READER 2027-01-05", "source": "user"}).json()
            self.assertEqual(results["total"], 1)
            original = results["items"][0]
            self.assertIn("Renamed@example.com", str(original))
            self.assertFalse(original["reviewed"])
            review_url = f"{changes_path}/{original['id']}/review"
            for _ in range(2):
                self.assertEqual(client.post(review_url).status_code, 200)
            history = client.get(changes_path).json()
            self.assertEqual([item["source"] for item in history["items"]], ["review", "user", "admin"])
            self.assertTrue(history["items"][1]["reviewed"])
            self.assertNotIn("before-password", json.dumps(history))
            self.assertNotIn("after-password", json.dumps(history))
            self.assertNotIn("JBSWY3DPEHPK3PXP", json.dumps(history))
            self.assertEqual(AccountCredentialStore(store.path).history(key), history)
            self.assertEqual(client.put(admin_path, json={"listed": False}).status_code, 200)
            self.use_session(client, self.user_token)
            self.assertEqual(client.put(edit_path, json={"revision": edited.json()["item"]["revision"], "login": "blocked"}).status_code, 404)

    def test_admin_restores_only_selected_gpt_fields_and_rejects_stale_events(self):
        with self.account_client() as (client, store):
            key = "email:user@example.com"
            path = f"/api/admin/gpt/accounts/{key}"
            history = path + "/changes"
            store.set("user@example.com", "password", "original-secret-value")
            self.module.ensure_gpt_pool()
            store.save_account(key, {"subscription_started_on": "2026-01-31"}, {})
            initial = client.put(path, json={"login": "new@example.com", "password": "wrong-password",
                                             "secret": "JBSWY3DPEHPK3PXP", "subscription_expires_on": "2027-01-05"})
            event = client.get(history).json()["items"][0]
            self.assertEqual({item["field"] for item in event["restorable_fields"]}, {"login", "password", "secret", "subscription"})
            self.assertNotIn("original-secret-value", store.path.read_text())
            self.assertNotIn("original-secret-value", json.dumps(event))
            self.assertNotIn("wrong-password", json.dumps(event))
            self.assertNotIn("JBSWY3DPEHPK3PXP", json.dumps(event))
            self.assertEqual(store.restore_key_path.stat().st_mode & 0o777, 0o600)

            restore = f"{history}/{event['id']}/restore"
            self.use_session(client, self.user_token)
            self.assertEqual(client.post(restore, json={"field": "password"}).status_code, 403)
            self.use_session(client, self.admin_token)
            self.assertEqual(client.post(restore + "?preview=1", json={"field": "password"}).status_code, 403)
            self.assertEqual(client.post(restore, json={"field": "mailbox_ids"}).status_code, 400)
            self.assertEqual(client.post(restore, json={"field": "password"}).status_code, 200)
            self.assertEqual(client.post(restore, json={"field": "password"}).status_code, 409)
            self.assertEqual(store.get("new@example.com", "password"), "original-secret-value")
            self.assertEqual(store.account_by_key(key)["subscription_expires_on"], "2027-01-05")
            self.assertEqual(client.put(path, json={"revision": initial.json()["item"]["revision"], "password": "stale"}).status_code, 409)

            for field in ("secret", "subscription", "login"):
                self.assertEqual(client.post(restore, json={"field": field}).status_code, 200)
            reopened = AccountCredentialStore(store.path)
            account = reopened.account_by_key(key)
            self.assertEqual(account["login"], "user@example.com")
            self.assertEqual(account["subscription_ends_on"], "2026-02-28")
            self.assertEqual(account["mailbox_ids"], ["account-1"])
            self.assertFalse(account["has_totp_secret"])
            results = client.get(history, params={"source": "restore"}).json()
            self.assertEqual(results["total"], 4)
            self.assertEqual({item["restore_of"] for item in results["items"]}, {event["id"]})
            self.assertEqual(reopened.history(key, source="restore"), results)

    def test_member_codex_import_requires_signed_matching_tokens_and_tracks_expiry(self):
        module = self.module
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        def auth_file(email="user@example.com", workspace="workspace-id", day=0):
            checked = int(time.time()) + day * 86400 - 3600
            token = self.subscription_token(checked, workspace_id=workspace)
            claims = module.decode_jwt_claims(token)
            claims.update(iss=module.CODEX_ISSUER, aud=[module.CODEX_CLIENT_ID], exp=int(time.time()) + 3600, email=email)
            identity = jwt.encode(claims, private_key, algorithm="RS256")
            access = jwt.encode({"sub": claims["sub"], "iss": module.CODEX_ISSUER, "aud": "https://api.openai.com/v1",
                                 "exp": claims["exp"], "https://api.openai.com/auth": {"chatgpt_account_id": "account-id"}},
                                private_key, algorithm="RS256")
            return {"tokens": {"access_token": access, "id_token": identity, "refresh_token": "hidden-refresh"}}

        with self.account_client() as (client, store), patch.object(module, "save_codex_record_auth_file") as save, \
                patch.object(module.CODEX_SIGNING_KEYS, "get_signing_key_from_jwt",
                             return_value=SimpleNamespace(key=private_key.public_key())):
            module.CODEX_ACCOUNTS.pop("codex-1")
            key = "email:user@example.com"
            path = f"/api/accounts/gpt/{key}/codex-auth"
            self.use_session(client, self.user_token)

            def upload(data, revision=""):
                return client.put(path, json={"revision": revision, "auth_json": json.dumps(data)})

            self.assertEqual(upload(auth_file("wrong@example.com")).status_code, 409)
            invalid = auth_file()
            parts = invalid["tokens"]["access_token"].split(".")
            invalid["tokens"]["access_token"] = ".".join([*parts[:2], "ZmFrZQ"])
            self.assertEqual(upload(invalid).status_code, 400)
            save.assert_not_called()
            self.assertEqual(upload(auth_file()).status_code, 200)
            revision = store.account_by_key(key)["revision"]
            self.assertEqual(upload(auth_file(workspace="other-workspace"), revision).status_code, 409)
            self.assertEqual(upload(auth_file(day=1), revision).status_code, 200)
            self.assertEqual(save.call_count, 2)
            events = store.history(key)["items"]
            self.assertEqual([item["source"] for item in events], ["system", "user", "system", "user"])
            self.assertIn("替换了Codex 授权文件", str(events[1]))
            self.assertNotIn("hidden-refresh", str(events))
            with module.CODEX_ACCOUNTS_LOCK:
                record = module.CODEX_ACCOUNTS["account-id::ws=workspace-id"]
            module.record_codex_changes(record, record)
            self.assertEqual(store.history(key)["total"], 4)
            # Background rotation preserves the explicit authorization version.
            rotated = {**record, "token": {**record["token"], "refresh_token": "background-rotated"}}
            module.CODEX_ACCOUNTS[record["id"]] = rotated
            self.use_session(client, self.admin_token)
            change = events[1]
            field = change["restorable_fields"][0]["field"]
            url = f"/api/admin/gpt/accounts/{key}/changes/{change['id']}/restore"
            with patch.object(store, "record_codex_change", side_effect=OSError("simulated storage failure")):
                with self.assertRaises(OSError):
                    module.restore_codex_authorization(key, change["id"], field,
                                                      {"source": "admin", "id": "admin-fixture", "name": "admin"})
            self.assertIs(module.CODEX_ACCOUNTS[record["id"]], rotated)
            self.assertEqual(store.history(key)["total"], 4)
            self.assertEqual(client.post(url, json={"field": field}).status_code, 200)
            restored = module.CODEX_ACCOUNTS[record["id"]]
            self.assertEqual(restored["token"]["refresh_token"], "hidden-refresh")
            self.assertNotEqual(restored["token"]["id_token"], record["token"]["id_token"])
            self.assertEqual(client.post(url, json={"field": field}).status_code, 409)
            restored_events = store.history(key, source="restore")["items"]
            self.assertEqual(len(restored_events), 1)
            self.assertNotIn("background-rotated", json.dumps(restored_events))

    def test_codex_restore_updates_auth_file_and_account_cache(self):
        module = self.module
        save_accounts = module.save_codex_accounts
        delete_files = module.delete_codex_record_auth_files
        with self.account_client() as (client, store), tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "accounts.json"
            auth_dir = root / "auth"
            with patch.object(module, "CODEX_AUTH_DIR", str(auth_dir)), patch.object(module, "CODEX_ACCOUNTS_FILE", str(cache)), \
                    patch.object(module, "save_codex_accounts", save_accounts), patch.object(module, "delete_codex_record_auth_files", delete_files):
                module.ensure_gpt_pool()
                original = {**module.CODEX_ACCOUNTS["codex-1"], "token": {"access_token": "original-access-fixture"}}
                modified = {**original, "token": {"access_token": "new-access-fixture"}, "auth_revision": "replacement"}
                module.CODEX_ACCOUNTS["codex-1"] = modified
                module.save_codex_record_auth_file(modified)
                module.save_codex_accounts()
                module.record_codex_changes(original, modified, actor={"source": "user", "name": "member", "id": "fixture"})
                key = "email:user@example.com"
                event = store.history(key)["items"][0]
                url = f"/api/admin/gpt/accounts/{key}/changes/{event['id']}/restore"
                self.assertEqual(client.post(url, json={"field": "codex:codex-1"}).status_code, 200)
                [auth_file] = auth_dir.glob("*.json")
                self.assertEqual(json.loads(auth_file.read_text())["tokens"]["access_token"], "original-access-fixture")
                module.load_codex_accounts()
                self.assertEqual(module.CODEX_ACCOUNTS["codex-1"]["token"]["access_token"], "original-access-fixture")
                self.assertNotIn("new-access-fixture", store.path.read_text())

                self.assertEqual(client.delete("/api/codex/accounts/codex-1").status_code, 200)
                self.assertFalse(auth_file.exists())
                removed = store.history(key)["items"][0]
                url = f"/api/admin/gpt/accounts/{key}/changes/{removed['id']}/restore"
                self.assertEqual(client.post(url, json={"field": "codex:codex-1"}).status_code, 200)
                self.assertTrue(auth_file.exists())

    def test_unified_accounts_share_credentials_without_exposing_secrets(self):
        with self.account_client() as (client, store):
            store.set("user@example.com", "password", "private-value")
            response = client.get("/api/accounts")
            self.assertEqual(response.status_code, 200)
            groups = response.json()["items"]
            self.assertEqual(len(groups), 2)
            group = next(item for item in groups if item["email"] == "user@example.com")
            self.assertEqual(len(group["mail"]), 1)
            self.assertEqual(len(group["codex"]), 1)
            self.assertTrue(group["has_gpt_password"])
            self.assertNotIn("private-value", response.text)
            self.assertEqual(group["mail"][0]["email"], "User@Example.com")

    def test_group_catalog_exposes_only_subscription_expiry_fields(self):
        with self.account_client() as (client, store):
            module = self.module
            checked = int(time.time()) - 3600
            token = self.subscription_token(checked)
            record = module.build_codex_account_record({"access_token": "synthetic-secret-token", "id_token": token})
            module.CODEX_ACCOUNTS["codex-1"] = record
            module.ensure_gpt_pool()
            store.save_account("email:user@example.com", {"subscription_expires_on": "2027-02-15"}, {})
            store.save_account("email:only@example.com", {"subscription_started_on": "2026-01-31"}, {})
            response = client.get("/api/admin/resources")
            by_key = {item["key"]: item for item in response.json()["items"]}
            first = by_key["gpt:email:user@example.com"]
            self.assertEqual(first["subscription_expires_at"], checked + 29 * 86400)
            self.assertEqual(first["subscription_expires_on"], "2027-02-15")
            second = by_key["gpt:email:only@example.com"]
            self.assertIsNone(second["subscription_expires_at"])
            self.assertEqual(second["subscription_expires_on"], "2026-02-28")
            self.assertNotIn(token, response.text)
            self.assertNotIn("synthetic-secret-token", response.text)

    def test_relay_accounts_require_url_and_keep_it_when_updating_other_fields(self):
        from resource_accounts import ResourceStore

        with self.account_client() as (client, _), tempfile.TemporaryDirectory() as directory:
            store = ResourceStore(Path(directory) / "relays.json", "password", "密码")
            with patch.object(self.module, "RELAY_ACCOUNT_STORE", store):
                payload = {"name": "中转站", "account": "user", "password": "relay-password"}
                self.assertEqual(client.post("/api/admin/relay-accounts", json=payload).status_code, 400)
                self.assertEqual(client.post("/api/admin/relay-accounts", json={**payload, "url": "javascript:alert(1)"}).status_code, 400)
                response = client.post("/api/admin/relay-accounts", json={**payload, "url": "https://relay.example.com/panel"})
                self.assertEqual(response.status_code, 201, response.text)
                item = response.json()["item"]
                self.assertEqual(item["url"], "https://relay.example.com/panel")
                updated = client.put("/api/admin/relay-accounts/" + item["id"], json={"note": "备用"})
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["item"]["url"], item["url"])
                self.assertEqual(store.get_secret(item["id"]), "relay-password")
                response = client.get("/api/relay-accounts")
                self.assertEqual(response.json()["items"][0]["url"], item["url"])
                self.assertNotIn("relay-password", response.text)

    def test_multiple_workspaces_are_preserved_in_one_email_group(self):
        with self.account_client() as (client, store):
            self.module.CODEX_ACCOUNTS['another-workspace'] = {
                'id': 'another-workspace', 'email': ' User@Example.com ', 'workspace_id': 'workspace-2'
            }
            groups = client.get('/api/accounts').json()['items']
            group = next(item for item in groups if item['email'] == 'user@example.com')
            self.assertEqual(len(group['codex']), 2)
            self.assertEqual(len(group['mail']), 1)
            self.assertEqual(len(groups), 2)

    def test_codex_only_login_credentials_crud_and_authentication(self):
        with self.account_client() as (client, store):
            for path, field, value in (("gpt-password", "password", "test-password"), ("totp-secret", "secret", "JBSWY3DPEHPK3PXP")):
                endpoint = f"/api/admin/accounts/email:only@example.com/{path}"
                self.assertEqual(client.put(endpoint, json={field: value}).status_code, 200)
                copied = client.post(f"/api/accounts/email:only@example.com/{path}")
                self.assertEqual(copied.json()[field], value)
                self.assertIn("no-store", copied.headers["cache-control"])
                self.assertEqual(client.delete(endpoint).status_code, 200)
                self.assertEqual(client.post(f"/api/accounts/email:only@example.com/{path}").status_code, 404)
            self.assertEqual(client.put('/api/admin/accounts/email:only@example.com/gpt-password', json={"password": ""}).status_code, 400)
            self.assertEqual(client.post('/api/accounts/email:missing@example.com/gpt-password').status_code, 404)
            client.cookies.clear()
            self.assertEqual(client.get('/api/accounts').status_code, 401)
            self.assertEqual(client.post('/api/accounts/email:only@example.com/totp-secret').status_code, 401)

    def test_admin_role_gates_console_and_homepage_link(self):
        with self.account_client() as (client, _):
            self.use_session(client, self.user_token)
            self.assertEqual(client.get("/api/accounts").status_code, 200)
            self.assertEqual(client.get("/api/admin/mail/accounts").status_code, 403)
            self.assertEqual(client.get("/api/codex/accounts").status_code, 403)
            homepage = client.get("/")
            self.assertEqual(homepage.status_code, 200)
            self.assertNotIn("/admin", homepage.text)
            console = client.get("/admin", follow_redirects=False)
            self.assertEqual(console.status_code, 303)
            self.assertEqual(console.headers["location"], "/")

            self.use_session(client, self.admin_token)
            self.assertEqual(client.get("/api/admin/mail/accounts").status_code, 200)
            self.assertEqual(client.get("/api/codex/accounts").status_code, 200)
            self.assertIn('href="/admin"', client.get("/").text)

    def test_admin_preview_renders_user_view_and_can_return_to_console(self):
        with self.account_client() as (client, store):
            store.set('user@example.com', 'listed', False)
            response = client.get('/preview?user_id=' + self.user_id)
            self.assertEqual(response.status_code, 200)
            self.assertIn('用户账户主页预览', response.text)
            self.assertIn('href="/admin"', response.text)
            self.assertNotIn('href="/admin">账号控制台', response.text)
            admin_items = client.get('/api/accounts').json()['items']
            suffix = '?preview=1&user_id=' + self.user_id
            preview_items = client.get('/api/accounts' + suffix).json()['items']
            self.assertEqual(len(admin_items), 2)
            self.assertEqual([item['email'] for item in preview_items], ['only@example.com'])
            self.assertEqual(client.post('/api/accounts/email:user@example.com/gpt-password' + suffix).status_code, 404)
            self.assertEqual(client.get('/api/history/account-1' + suffix).status_code, 404)
            self.use_session(client, self.user_token)
            self.assertEqual(client.get('/preview', follow_redirects=False).status_code, 303)
            self.assertEqual(client.get('/preview', follow_redirects=False).headers['location'], '/')

    def test_login_form_accepts_both_roles(self):
        with self.account_client() as (client, _):
            client.cookies.clear()
            user_login = client.post(
                "/login",
                data={"username": "reader", "password": USER_PASSWORD},
                follow_redirects=False,
            )
            self.assertEqual(user_login.status_code, 303)
            self.assertEqual(client.get("/api/admin/mail/accounts", follow_redirects=False).status_code, 403)

            client.cookies.clear()
            admin_login = client.post(
                "/login",
                data={"username": "admin", "password": ADMIN_PASSWORD, "totp_code": ADMIN_TOTP.now()},
                follow_redirects=False,
            )
            self.assertEqual(admin_login.status_code, 303)
            self.assertEqual(client.get("/api/admin/mail/accounts").status_code, 200)

    def test_production_rejects_all_legacy_access_methods(self):
        with self.account_client() as (client, _):
            client.cookies.clear()
            client.cookies.set("chatgpt_code_access", "test-admin-password")
            for name in ("test-access-password", "test-admin-password"):
                response = client.get("/api/admin/users", params={"access_token": name}, headers={
                    "x-access-token": name, "Authorization": "Bearer " + name,
                })
                self.assertEqual(response.status_code, 401)
            self.assertEqual(client.post("/login", data={"access_token": "test-admin-password"}).status_code, 401)
            self.assertEqual(client.post("/api/auth/login", json={"access_token": "test-admin-password"}).status_code, 400)

    def test_unlisting_blocks_user_reads_and_relisting_restores_them(self):
        with self.account_client() as (client, store):
            module = self.module
            store.set('user@example.com', 'password', 'private-password')
            store.set('user@example.com', 'secret', 'JBSWY3DPEHPK3PXP')
            module.STATE['account-1'] = {'last_code': '123456', 'rule_id': 'openai', 'email': 'user@example.com'}
            module.CODE_HISTORY['account-1'] = [{'code': '654321', 'rule_id': 'openai', 'email': 'user@example.com'}]
            for cfg in module.ACCOUNTS:
                module.mailbox_resource_key(cfg)
            accounts_before = json.loads(json.dumps(module.ACCOUNTS))
            codex_before = json.loads(json.dumps(module.CODEX_ACCOUNTS))
            original_files = set(store.path.parent.iterdir())
            endpoint = '/api/admin/accounts/email:user@example.com/listing'
            response = client.put(endpoint, json={'listed': False})
            self.assertEqual(response.status_code, 200)
            self.assertIs(response.json()['listed'], False)
            store = AccountCredentialStore(store.path)
            self.assertIs(store.get('USER@example.com', 'listed'), False)
            with patch.object(module, 'CREDENTIAL_STORE', store):
                admin_groups = client.get('/api/accounts').json()['items']
                hidden = next(item for item in admin_groups if item['email'] == 'user@example.com')
                self.assertIs(hidden['listed'], False)
                self.assertEqual(len(hidden['codex']), 1)
                self.assertEqual(len(hidden['mail']), 1)
                reads = [('GET', '/api/history/account-1')]
                for prefix in ('/api/accounts', '/api/mail/accounts', '/api/gmail/accounts'):
                    for name in ('email:user@example.com', 'account-1'):
                        for field in ('gpt-password', 'totp-secret'):
                            reads.append(('POST', f'{prefix}/{name}/{field}'))
                reads.append(('POST', '/api/accounts/email:user@example.com/totp-code'))
                for method, path in reads:
                    self.assertEqual(client.request(method, path).status_code, 200, path)
                self.use_session(client, self.user_token)
                self.assertEqual([item['email'] for item in client.get('/api/accounts').json()['items']], ['only@example.com'])
                self.assertEqual(client.get('/api/codes').json()['items'], [])
                for method, path in reads:
                    self.assertEqual(client.request(method, path).status_code, 404, path)
                self.assertEqual(client.put(endpoint, json={'listed': True}).status_code, 403)
                self.use_session(client, self.admin_token)
                self.assertEqual(client.put(endpoint, json={'listed': True}).status_code, 200)
                self.use_session(client, self.user_token)
                self.assertEqual(len(client.get('/api/accounts').json()['items']), 2)
                self.assertEqual(client.get('/api/codes').json()['items'][0]['last_code'], '123456')
                self.assertEqual(client.get('/api/history/account-1').json()['items'], module.CODE_HISTORY['account-1'])
                self.assertEqual(client.post('/api/accounts/email:user@example.com/gpt-password').json()['password'], 'private-password')
            self.assertEqual(module.ACCOUNTS, accounts_before)
            self.assertEqual(module.CODEX_ACCOUNTS, codex_before)
            self.assertEqual(set(store.path.parent.iterdir()), original_files)
            module.stop_mail_watcher.assert_not_called()
            module.delete_codex_record_auth_files.assert_not_called()
            module.save_mail_accounts.assert_not_called()
            module.save_codex_accounts.assert_not_called()

    def test_listing_applies_to_codex_only_accounts_and_validates_input(self):
        with self.account_client() as (client, store):
            endpoint = '/api/admin/accounts/email:only@example.com/listing'
            for payload in ({}, {'listed': 'false'}, {'listed': 0}, [], None):
                with self.subTest(payload=payload):
                    self.assertEqual(client.put(endpoint, json=payload).status_code, 400)
            self.assertEqual(client.put('/api/admin/accounts/email:missing@example.com/listing', json={'listed': False}).status_code, 404)
            self.assertEqual(client.put(endpoint, json={'listed': False}).status_code, 200)
            self.use_session(client, self.user_token)
            self.assertEqual([item['email'] for item in client.get('/api/accounts').json()['items']], ['user@example.com'])
            client.cookies.clear()
            self.assertEqual(client.put(endpoint, json={'listed': True}).status_code, 401)

    def test_current_totp_endpoint_returns_code_without_secret(self):
        with self.account_client() as (client, store):
            path = '/api/accounts/email:only@example.com/totp-code'
            self.assertEqual(client.post(path).status_code, 404)
            store.set('only@example.com', 'secret', 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ')
            with patch.object(self.module.time, 'time', return_value=1111111109):
                response = client.post(path)
            self.assertEqual(response.json(), {'code': '081804', 'expires_at': 1111111110, 'server_time': 1111111109})
            self.assertIn('no-store', response.headers['cache-control'])
            store.set('only@example.com', 'secret', 'invalid-legacy-secret')
            self.assertEqual(client.post(path).status_code, 400)
            self.assertNotIn('invalid-legacy-secret', client.post(path).text)
            self.assertEqual(client.put('/api/admin/accounts/email:only@example.com/totp-secret', json={'secret':'123456'}).status_code, 400)
            self.assertEqual(store.get('only@example.com', 'secret'), 'invalid-legacy-secret')
            client.cookies.clear()
            self.assertEqual(client.post(path).status_code, 401)

    def test_removing_one_integration_preserves_shared_credentials(self):
        with self.account_client() as (client, store):
            store.set("user@example.com", "password", "shared-password")
            self.assertEqual(client.delete('/api/admin/mail/accounts/account-1').status_code, 200)
            self.assertEqual(client.post('/api/accounts/email:user@example.com/gpt-password').json()['password'], 'shared-password')
            self.assertEqual(client.delete('/api/codex/accounts/codex-1').status_code, 200)
            self.assertEqual(store.get('user@example.com', 'password'), 'shared-password')
            self.assertEqual(client.post('/api/accounts/email:user@example.com/gpt-password').status_code, 200)

    def test_deleting_group_removes_all_associations_only_for_that_email(self):
        with self.account_client() as (client, store):
            store.set('user@example.com', 'secret', 'shared-secret')
            self.assertEqual(client.delete('/api/admin/accounts/email:user@example.com').status_code, 200)
            self.assertEqual([item['email'] for item in client.get('/api/accounts').json()['items']], ['only@example.com'])
            self.assertIsNone(store.get('user@example.com', 'secret'))
            self.assertEqual(client.delete('/api/admin/accounts/email:user@example.com').status_code, 404)

    def test_refresh_does_not_restore_a_deleted_account(self):
        for manual in (False, True):
            with self.subTest(manual=manual), self.account_client() as (client, store):
                def refresh_then_delete(record, **kwargs):
                    self.module.CODEX_ACCOUNTS.pop(record["id"], None)
                    return record
                with patch.object(self.module, "refresh_codex_account_record", side_effect=refresh_then_delete):
                    if manual:
                        self.assertEqual(client.post('/api/codex/accounts/codex-1/refresh').status_code, 404)
                    else:
                        self.module.refresh_all_codex_accounts_once()
                self.assertNotIn('codex-1', self.module.CODEX_ACCOUNTS)

    def test_authorization_url_can_be_fetched_without_browser_redirect(self):
        with self.account_client() as (client, store), patch.object(self.module, 'ensure_codex_login_server'), patch.object(self.module, 'CODEX_AUTH_SESSIONS', {}):
            for path in ('/api/codex/auth/start', '/api/admin/gmail/auth/start', '/api/admin/outlook/auth/start'):
                self.assertEqual(client.get(path).status_code, 405)
                self.assertEqual(client.post(path, headers={'Origin': 'https://evil.example'}).status_code, 403)
            self.assertEqual(self.module.CODEX_AUTH_SESSIONS, {})
            response = client.post('/api/codex/auth/start?format=json&reason=admin-import')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {'auth_url'})
            self.assertIn('no-store', response.headers['cache-control'])
            query = self.module.urllib.parse.parse_qs(self.module.urllib.parse.urlparse(response.json()['auth_url']).query)
            session = self.module.CODEX_AUTH_SESSIONS[query['state'][0]]
            self.assertTrue(session['code_verifier'])
            self.assertEqual(query['code_challenge_method'], ['S256'])
            self.assertEqual(client.post('/api/codex/auth/start', follow_redirects=False).status_code, 303)
            response = client.post('/api/codex/auth/start?format=json&account_id=codex-1')
            query = self.module.urllib.parse.parse_qs(self.module.urllib.parse.urlparse(response.json()['auth_url']).query)
            self.assertEqual(self.module.CODEX_AUTH_SESSIONS[query['state'][0]]['target_email'], 'user@example.com')
            self.assertEqual(client.post('/api/codex/auth/start?format=json&account_id=missing').status_code, 404)
            client.cookies.clear()
            self.assertEqual(client.post('/api/codex/auth/start?format=json').status_code, 401)

    def test_outlook_missing_configuration_renders_utf8_setup_page(self):
        with self.account_client() as (client, store), patch.dict(os.environ, {
            'OUTLOOK_CLIENT_ID': '', 'OUTLOOK_CLIENT_SECRET': '',
        }), patch.object(self.module, 'OutlookAuthorization') as authorization:
            response = client.post('/api/admin/outlook/auth/start', follow_redirects=False)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.headers['content-type'], 'text/html; charset=utf-8')
            self.assertIn('缺少 OUTLOOK_CLIENT_ID', response.content.decode('utf-8'))
            self.assertIn('Outlook 配置未完成', response.text)
            self.assertIn('OUTLOOK_CLIENT_SECRET=', response.text)
            self.assertIn('Mail.ReadWrite', response.text)
            self.assertIn('返回账号控制台', response.text)
            self.assertIn('no-store', response.headers['cache-control'])
            authorization.assert_not_called()

    def test_outlook_configured_start_still_redirects_and_preserves_session(self):
        with self.account_client() as (client, store), patch.object(self.module, 'load_outlook_settings'), \
                patch.object(self.module, 'OutlookAuthorization') as authorization, \
                patch.object(self.module, 'OUTLOOK_AUTH_SESSIONS', {}):
            url = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?test=1'
            authorization.return_value.start.return_value = {'auth_uri': url}
            response = client.post('/api/admin/outlook/auth/start', follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['location'], url)
            state = authorization.return_value.start.call_args.args[0]
            self.assertEqual(self.module.OUTLOOK_AUTH_SESSIONS[state]['managed_action'], 'add')

    def test_outlook_start_errors_render_escaped_html(self):
        with self.account_client() as (client, store):
            response = client.post('/api/admin/outlook/auth/start', params={'account_name': '<script>alert(1)</script>'})
            self.assertEqual(response.status_code, 404)
            self.assertIn('&lt;script&gt;', response.text)
            self.assertNotIn('<script>alert(1)</script>', response.text)
            with patch.object(self.module, 'load_outlook_settings', side_effect=RuntimeError('connection failed')):
                response = client.post('/api/admin/outlook/auth/start')
                self.assertEqual(response.status_code, 400)
                self.assertIn('text/html', response.headers['content-type'])
                self.assertIn('connection failed', response.text)

    def test_manual_endpoint_forces_token_refresh_without_a_subscription_request(self):
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_plan_type": "free",
            "token": {"access_token": "old-access-token"},
        }
        calls = []

        def fake_refresh(value, force_token_refresh=False):
            calls.append(force_token_refresh)
            return value

        with patch.object(self.module, "CODEX_ACCOUNTS", {"account-id": record}), patch.object(
            self.module,
            "refresh_codex_account_record",
            side_effect=fake_refresh,
        ), patch.object(self.module, "save_codex_accounts"):
            response = self.module.refresh_codex_account("account-id")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [True])

    def test_token_refresh_reloads_plan_from_new_access_token(self):
        claims = {
            "sub": "user-id",
            "email": "user@example.com",
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
        }
        encoded_claims = base64.urlsafe_b64encode(
            json.dumps(claims).encode("utf-8")
        ).decode("ascii").rstrip("=")
        new_access_token = f"header.{encoded_claims}.signature"
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_plan_type": "free",
            "token": {
                "access_token": "old-access-token",
                "refresh_token": "old-refresh-token",
            },
        }

        with patch.object(
            self.module,
            "http_json",
            return_value={
                "access_token": new_access_token,
                "refresh_token": "new-refresh-token",
            },
        ), patch.object(self.module, "save_codex_record_auth_file"):
            refreshed = self.module.refresh_codex_access_token(record)

        self.assertTrue(refreshed)
        self.assertEqual(record["chatgpt_plan_type"], "pro")
        self.assertEqual(record["token"]["refresh_token"], "new-refresh-token")

    def test_token_and_usage_refresh_preserve_local_subscription_verification_time(self):
        checked = int(time.time()) - 3600
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_account_id": "chatgpt-account-id",
            "workspace_id": "workspace-id",
            "chatgpt_plan_type": "free",
            "subscription_error": "old status 403 Forbidden",
            "token": {
                "access_token": "old-access-token",
                "refresh_token": "refresh-token",
                "id_token": self.subscription_token(checked, account_id="chatgpt-account-id"),
            },
        }

        def fake_token_refresh(value):
            value["token"]["access_token"] = "new-access-token"
            value["chatgpt_plan_type"] = "pro"
            return True

        usage = {
            "used_percent": 1.0,
            "window_minutes": 10080,
            "resets_at": 1234567890,
            "credits": None,
            "captured_at": 1234567890,
        }
        with patch.object(
            self.module,
            "refresh_codex_access_token",
            side_effect=fake_token_refresh,
        ) as token_refresh, patch.object(
            self.module,
            "fetch_codex_usage",
            return_value=usage,
        ), patch.object(self.module, "fetch_codex_reset_credits", return_value={"available": 0}), \
                patch.object(self.module, "http_json", side_effect=AssertionError("Unexpected network")) as network:
            updated = self.module.refresh_codex_account_record(
                record,
                force_token_refresh=True,
            )

        token_refresh.assert_called_once()
        self.assertEqual(updated["chatgpt_plan_type"], "pro")
        self.assertIsNone(updated["subscription_error"])
        self.assertEqual(updated["subscription"]["checked_at"], checked)
        network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
