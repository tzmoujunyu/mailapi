from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi.testclient import TestClient
import requests

from mailapi_shared.accounts import SESSION_COOKIE
from mailapi_shared.site_accounts import SiteAccountStore


class MailOAuthFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        source = Path(__file__).resolve().parents[1] / 'mailapi_shared' / 'application.py'
        spec = importlib.util.spec_from_file_location('mailapi_shared.mail_oauth_test', source)
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {
            'MAILAPI_MODE': 'test', 'TEST_DATA_DIR': cls.directory.name,
            'TEST_EXTERNAL_WATCHERS': '0', 'TEST_GMAIL_ACCOUNTS': '[]',
        }):
            spec.loader.exec_module(cls.module)
        cls.real_stop = cls.module.stop_mail_watcher

    @classmethod
    def tearDownClass(cls):
        for handler in list(cls.module.ERROR_LOGGER.handlers):
            handler.close()
            cls.module.ERROR_LOGGER.removeHandler(handler)
        cls.directory.cleanup()

    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        module = self.module
        self.account_file = self.root / 'mail-accounts.json'
        self.account_file.write_text('{"items": []}')
        client_config = {'installed': {
            'client_id': 'synthetic-client', 'client_secret': 'synthetic-secret',
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': ['http://localhost'],
        }}
        (self.root / 'credentials.json').write_text(json.dumps(client_config))
        for name, value in {
            'BASE_DIR': self.root, 'DATA_DIR': str(self.root),
            'ACCOUNTS': [], 'AUTH_SESSIONS': {}, 'OUTLOOK_AUTH_SESSIONS': {},
            'STATE': {}, 'CODE_HISTORY': {}, 'MAIL_WATCHERS': {},
            'GMAIL_ACCOUNTS_FILE': str(self.account_file),
            'GMAIL_TOKEN_DIR': str(self.root / 'gmail_tokens'),
            'OUTLOOK_TOKEN_DIR': str(self.root / 'outlook_tokens'),
            'GMAIL_OAUTH_REDIRECT_BASE': 'https://mailapi.example.test',
        }.items():
            stack.enter_context(patch.object(module, name, value))
        for name in ('stop_mail_watcher', 'start_mail_watcher', 'log_exception'):
            stack.enter_context(patch.object(module, name))
        stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('unexpected external request')))
        site = SiteAccountStore(self.root / 'site.json', require_admin_mfa=False)
        site.ensure_admin('isolated-admin-password')
        self.admin_token, _ = site.login('admin', 'isolated-admin-password')
        stack.enter_context(patch.object(module.app.state, 'site_accounts', site))
        self.client = TestClient(module.app, base_url='https://mailapi.example.test')
        stack.callback(self.client.close)
        self.login()
        self.gmail_email = self.outlook_email = 'added@example.test'
        service = Mock()
        service.users().getProfile().execute.side_effect = lambda: {'emailAddress': self.gmail_email}
        stack.enter_context(patch.object(module, 'build', return_value=service))
        stack.enter_context(patch.object(module, 'load_outlook_settings'))
        authorization_type = module.OutlookAuthorization

        def outlook_authorization(cls, settings, token_file):
            authorization = object.__new__(authorization_type)
            authorization.settings = settings
            authorization.app = Mock()
            authorization.app.initiate_auth_code_flow.side_effect = lambda **kw: {
                'state': kw['state'], 'auth_uri': 'https://login.microsoftonline.com/authorize?' + urlencode({'state': kw['state']}),
            }
            authorization.app.acquire_token_by_auth_code_flow.return_value = {'access_token': 'synthetic-outlook-token'}
            authorization.cache = Mock()
            authorization.cache.serialize.return_value = '{"AccessToken": {"synthetic": {"secret": "new-outlook-token"}}}'
            return authorization

        stack.enter_context(patch.object(authorization_type, '__new__', side_effect=outlook_authorization))
        stack.enter_context(patch.object(authorization_type, '__init__', return_value=None))
        stack.enter_context(patch.object(module, 'fetch_outlook_profile', side_effect=lambda token: {'email': self.outlook_email}))

    def login(self):
        self.client.cookies.set(SESSION_COOKIE, self.admin_token)

    def begin(self, provider, account_name=None):
        self.login()
        response = self.client.post(f'/api/admin/{provider}/auth/start', params={'account_name': account_name} if account_name else {}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        state = parse_qs(urlsplit(response.headers['location']).query)['state'][0]
        sessions = self.module.AUTH_SESSIONS if provider == 'gmail' else self.module.OUTLOOK_AUTH_SESSIONS
        session = sessions[state]
        if provider == 'gmail':
            token = requests.Response()
            token.status_code = 200
            token.request = requests.Request('POST', 'https://oauth2.googleapis.com/token').prepare()
            token._content = json.dumps({
                'access_token': 'new-gmail-token', 'refresh_token': 'new-gmail-refresh',
                'token_type': 'Bearer', 'expires_in': 3600,
            }).encode()
            session['flow'].oauth2session.request = Mock(return_value=token)
        return state, session

    def complete(self, provider, state, email, entry='default'):
        self.gmail_email = self.outlook_email = email
        data = {'state': state, 'code': 'synthetic-authorization-code'}
        if provider == 'gmail':
            url = '/?' + urlencode(data)
            if entry == 'manual':
                self.login()
                return self.client.post('/api/admin/gmail/auth/complete', json={'callback_url': 'http://localhost:8000' + url})
            self.client.cookies.clear()
            if entry == 'legacy':
                url = '/oauth2callback/legacy?' + urlencode(data)
            return self.client.get(url, headers={'Sec-Fetch-Site': 'cross-site'}, follow_redirects=False)
        self.client.cookies.clear()
        headers = {'Origin': 'https://login.microsoftonline.com', 'Sec-Fetch-Site': 'cross-site'}
        if entry == 'get':
            return self.client.get('/api/admin/outlook/auth/callback', params=data, headers=headers)
        return self.client.post('/api/admin/outlook/auth/callback', data=data, headers=headers)

    def existing(self, provider):
        name = 'existing-' + provider
        token_file = self.root / (provider + '_tokens') / (name + '.json')
        token_file.parent.mkdir(exist_ok=True)
        token_file.write_text('{"old": "retained-until-success"}')
        cfg = {'name': name, 'provider': provider, 'email': provider + '@example.test',
               'token_file': str(token_file), 'credential_file': str(self.root / 'credentials.json'),
               'enabled': True, 'resource_id': ('a' if provider == 'gmail' else 'b') * 32,
               'listed': True, 'note': '保留接码说明'}
        self.module.ACCOUNTS.append(cfg)
        self.module.save_mail_accounts()
        return dict(cfg)

    def test_add_callbacks_and_manual_submission_persist_and_reject_replay(self):
        for provider, entry in [('gmail', 'default'), ('gmail', 'legacy'), ('gmail', 'manual'), ('outlook', 'default'), ('outlook', 'get')]:
            with self.subTest(provider=provider, entry=entry):
                state, session = self.begin(provider)
                email = f'{provider}-{entry}@example.test'
                response = self.complete(provider, state, email, entry)
                self.assertEqual(response.status_code, 200, response.text)
                cfg = session['cfg']
                self.assertTrue(Path(cfg['token_file']).is_file())
                self.assertIn(email, [item['email'] for item in self.module.load_accounts()])
                self.assertEqual(self.complete(provider, state, email, entry).status_code, 404)
                self.login()
                self.assertIn(email, [item['email'] for item in self.client.get('/api/admin/mail/accounts').json()['items']])

    def test_reauthorization_accepts_email_case_and_preserves_resource_metadata(self):
        for provider in ('gmail', 'outlook'):
            with self.subTest(provider=provider):
                cfg = self.existing(provider)
                state, _ = self.begin(provider, cfg['name'])
                response = self.complete(provider, state, cfg['email'].upper())
                self.assertEqual(response.status_code, 200, response.text)
                updated = self.module.find_mail_account(cfg['name'])
                for field in ('name', 'resource_id', 'listed', 'note', 'token_file'):
                    self.assertEqual(updated[field], cfg[field])
                self.assertNotIn('retained-until-success', Path(cfg['token_file']).read_text())
                self.assertEqual(len([a for a in self.module.load_accounts() if a['name'] == cfg['name']]), 1)

    def test_wrong_account_reauthorization_does_not_change_mailbox_or_token(self):
        for provider in ('gmail', 'outlook'):
            with self.subTest(provider=provider):
                cfg = self.existing(provider)
                original = Path(cfg['token_file']).read_bytes(), self.account_file.read_bytes()
                state, _ = self.begin(provider, cfg['name'])
                response = self.complete(provider, state, 'another@example.test')
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('与原账号不一致', response.text)
                self.assertEqual((Path(cfg['token_file']).read_bytes(), self.account_file.read_bytes()), original)
                self.assertEqual(self.module.find_mail_account(cfg['name']), cfg)

    def test_adding_existing_mailbox_updates_its_token_without_duplicate_or_relisting(self):
        for provider in ('gmail', 'outlook'):
            with self.subTest(provider=provider):
                cfg = self.existing(provider)
                state, session = self.begin(provider)
                response = self.complete(provider, state, cfg['email'].upper())
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(Path(session['cfg']['token_file']).exists())
                updated = self.module.find_mail_account(cfg['name'])
                for field in ('name', 'resource_id', 'listed', 'note', 'token_file'):
                    self.assertEqual(updated[field], cfg[field])
                self.assertEqual(len([a for a in self.module.load_accounts() if a['provider'] == provider]), 1)

    def test_pending_additions_have_independent_sessions_and_token_paths(self):
        pending = [(provider, *self.begin(provider)) for provider in ('gmail', 'gmail', 'outlook', 'outlook')]
        names = [session['cfg']['name'] for _, _, session in pending]
        self.assertEqual(len(set(names)), len(names))
        for index, (provider, state, session) in enumerate(reversed(pending)):
            self.assertEqual(self.complete(provider, state, f'mail-{index}@example.test').status_code, 200)
        self.assertEqual(len(self.module.load_accounts()), 4)
        self.assertEqual(len({item['email'] for item in self.module.load_accounts()}), 4)
        self.assertTrue(all(Path(session['cfg']['token_file']).is_file() for _, _, session in pending))

    def test_disabling_mailbox_cancels_pending_reauthorization(self):
        for provider in ('gmail', 'outlook'):
            with self.subTest(provider=provider):
                cfg = self.existing(provider)
                original_token = Path(cfg['token_file']).read_bytes()
                state, _ = self.begin(provider, cfg['name'])
                with patch.object(self.module, 'stop_mail_watcher', side_effect=type(self).real_stop):
                    disabled = self.client.post(f'/api/admin/mail/accounts/{cfg["name"]}/disable')
                self.assertEqual(disabled.status_code, 200)
                self.assertEqual(self.complete(provider, state, cfg['email']).status_code, 404)
                self.assertFalse(self.module.find_mail_account(cfg['name'])['enabled'])
                self.assertEqual(Path(cfg['token_file']).read_bytes(), original_token)

    def test_google_denial_reaches_callback_without_login_or_saving(self):
        state, session = self.begin('gmail')
        self.client.cookies.clear()
        response = self.client.get('/', params={'state': state, 'error': 'access_denied'}, headers={'Sec-Fetch-Site': 'cross-site'}, follow_redirects=False)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('Google 授权失败', response.text)
        self.assertEqual(self.module.load_accounts(), [])
        session['flow'].oauth2session.request.assert_not_called()

    def test_google_manual_submission_requires_admin_and_callbacks_require_valid_state(self):
        state, session = self.begin('gmail')
        callback = 'http://localhost:8000/?' + urlencode({'state': state, 'code': 'synthetic-code'})
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/admin/gmail/auth/complete', json={'callback_url': callback}).status_code, 401)
        self.assertEqual(self.client.get('/?state=unknown&code=synthetic-code').status_code, 404)
        self.assertEqual(self.client.get('/oauth2callback/legacy?code=synthetic-code').status_code, 400)
        self.assertEqual(self.client.get('/?' + urlencode([('state', state), ('state', state), ('code', 'synthetic-code')])).status_code, 400)
        self.assertEqual(self.module.load_accounts(), [])
        session['flow'].oauth2session.request.assert_not_called()
        self.assertEqual(self.complete('gmail', state, 'added@example.test', 'manual').status_code, 200)

    def test_token_save_failure_does_not_report_success_or_add_mailbox(self):
        for provider in ('gmail', 'outlook'):
            with self.subTest(provider=provider):
                state, _ = self.begin(provider)
                with patch.object(self.module, 'write_json_atomic', side_effect=PermissionError('synthetic write failure')):
                    response = self.complete(provider, state, 'added@example.test')
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.module.load_accounts(), [])
                self.assertEqual(self.module.ACCOUNTS, [])

    def test_concurrent_saves_cannot_overwrite_a_newer_mailbox_list(self):
        module = self.module
        module.ACCOUNTS.append({'name': 'first', 'email': 'first@example.test'})
        writing = threading.Event()
        release = threading.Event()
        second_done = threading.Event()
        write_json = module.write_json_atomic

        def delayed_write(path, value):
            if len(value['items']) == 1:
                writing.set()
                if not release.wait(5):
                    raise TimeoutError('first writer was not released')
            write_json(path, value)

        def add_second():
            with module.MAIL_ACCOUNTS_LOCK:
                module.ACCOUNTS.append({'name': 'second', 'email': 'second@example.test'})
            module.save_mail_accounts()
            second_done.set()

        with patch.object(module, 'write_json_atomic', side_effect=delayed_write), ThreadPoolExecutor(2) as pool:
            first = pool.submit(module.save_mail_accounts)
            try:
                self.assertTrue(writing.wait(2))
                second = pool.submit(add_second)
                second_done.wait(0.2)
            finally:
                release.set()
            first.result(timeout=3)
            second.result(timeout=3)
        self.assertEqual([item['name'] for item in module.load_accounts()], ['first', 'second'])
