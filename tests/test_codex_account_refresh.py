import base64
from contextlib import ExitStack, contextmanager
from account_credentials import AccountCredentialStore
from fastapi.testclient import TestClient
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class CodexAccountRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.previous_directory = os.getcwd()
        temporary_path = Path(cls.temporary_directory.name)
        (temporary_path / "static").mkdir()
        os.chdir(temporary_path)
        try:
            with patch.dict(
                os.environ,
                {
                    "ACCESS_PASSWORD": "test-access-password",
                    "MAIL_RELAY_SECRET": "test-mail-relay-secret-32-characters",
                },
            ):
                spec = importlib.util.spec_from_file_location(
                    "mailapi_code_receive_for_tests",
                    ROOT / "code-receive.py",
                )
                cls.module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(cls.module)
        finally:
            os.chdir(cls.previous_directory)

    @classmethod
    def tearDownClass(cls):
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
            client = TestClient(module.app, headers={"x-access-token": module.ACCESS_PASSWORD})
            yield client, store
            client.close()

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
            client.headers.pop('x-access-token')
            self.assertEqual(client.get('/api/accounts').status_code, 401)
            self.assertEqual(client.post('/api/accounts/email:only@example.com/totp-secret').status_code, 401)

    def test_current_totp_endpoint_returns_code_without_secret(self):
        with self.account_client() as (client, store):
            path = '/api/accounts/email:only@example.com/totp-code'
            self.assertEqual(client.post(path).status_code, 404)
            store.set('only@example.com', 'secret', 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ')
            with patch.object(self.module.time, 'time', return_value=1111111109):
                response = client.post(path)
            self.assertEqual(response.json(), {'code': '081804', 'expires_at': 1111111110})
            self.assertIn('no-store', response.headers['cache-control'])
            store.set('only@example.com', 'secret', 'invalid-legacy-secret')
            self.assertEqual(client.post(path).status_code, 400)
            self.assertNotIn('invalid-legacy-secret', client.post(path).text)
            self.assertEqual(client.put('/api/admin/accounts/email:only@example.com/totp-secret', json={'secret':'123456'}).status_code, 400)
            self.assertEqual(store.get('only@example.com', 'secret'), 'invalid-legacy-secret')
            client.headers.pop('x-access-token')
            self.assertEqual(client.post(path).status_code, 401)

    def test_removing_one_integration_preserves_shared_credentials(self):
        with self.account_client() as (client, store):
            store.set("user@example.com", "password", "shared-password")
            self.assertEqual(client.delete('/api/admin/mail/accounts/account-1').status_code, 200)
            self.assertEqual(client.post('/api/accounts/email:user@example.com/gpt-password').json()['password'], 'shared-password')
            self.assertEqual(client.delete('/api/codex/accounts/codex-1').status_code, 200)
            self.assertIsNone(store.get('user@example.com', 'password'))

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

    def test_manual_endpoint_forces_token_and_subscription_refresh(self):
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_plan_type": "free",
            "token": {"access_token": "old-access-token"},
        }
        calls = []

        def fake_refresh(value, force_subscription=False, force_token_refresh=False):
            calls.append((force_subscription, force_token_refresh))
            return value

        with patch.object(self.module, "CODEX_ACCOUNTS", {"account-id": record}), patch.object(
            self.module,
            "refresh_codex_account_record",
            side_effect=fake_refresh,
        ), patch.object(self.module, "save_codex_accounts"):
            response = self.module.refresh_codex_account("account-id")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [(True, True)])

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

    def test_forced_token_refresh_updates_plan_when_subscription_is_blocked(self):
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_account_id": "chatgpt-account-id",
            "workspace_id": "workspace-id",
            "chatgpt_plan_type": "free",
            "token": {
                "access_token": "old-access-token",
                "refresh_token": "refresh-token",
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
            "fetch_codex_subscription",
            side_effect=RuntimeError(" status 403 Forbidden"),
        ), patch.object(
            self.module,
            "fetch_codex_usage",
            return_value=usage,
        ):
            updated = self.module.refresh_codex_account_record(
                record,
                force_subscription=True,
                force_token_refresh=True,
            )

        token_refresh.assert_called_once()
        self.assertEqual(updated["chatgpt_plan_type"], "pro")
        self.assertIn("403", updated["subscription_error"])


if __name__ == "__main__":
    unittest.main()
