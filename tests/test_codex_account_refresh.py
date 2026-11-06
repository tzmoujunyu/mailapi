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

import pyotp

from mailapi_shared.accounts import SESSION_COOKIE
from mailapi_shared.site_accounts import SiteAccountStore


ROOT = Path(__file__).resolve().parents[1]
ADMIN_PASSWORD = "test-admin-password"
USER_PASSWORD = "test-user-password"
ADMIN_TOTP = pyotp.TOTP("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")


class CodexAccountRefreshTests(unittest.TestCase):
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
            self.assertIn('用户账号主页预览', response.text)
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
            module.STATE['account-1'] = {'last_code': '123456'}
            module.CODE_HISTORY['account-1'] = [{'code': '654321'}]
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
                self.assertEqual(client.get('/api/history/account-1').json()['items'], [{'code': '654321'}])
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
