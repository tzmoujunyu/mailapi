from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mailapi_shared.test_features import TestFeatureStore
from mailapi_shared.view_preferences import install_view_preferences


class ViewPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.client = self.make_client()

    def make_client(self):
        app = FastAPI()

        @app.middleware('http')
        async def identity(request: Request, call_next):
            user = request.headers.get('x-user')
            request.state.site_user = {'id': user, 'role': 'user'} if user else None
            return await call_next(request)

        install_view_preferences(app, TestFeatureStore(Path(self.directory), None))
        return self.enterContext(TestClient(app))

    def save(self, settings, scope='resources', user='alice', client=None):
        return (client or self.client).put('/api/view-preferences/' + scope,
                                         headers={'x-user': user}, json={'settings': settings})

    def read(self, scope='resources', user='alice', client=None):
        return (client or self.client).get('/api/view-preferences/' + scope, headers={'x-user': user})

    def test_account_scope_and_restart_persistence(self):
        self.assertEqual(self.client.get('/api/view-preferences/resources').status_code, 401)
        self.assertEqual(self.client.put('/api/view-preferences/resources', json={'settings': {}}).status_code, 401)
        self.assertEqual(self.save({'gptStatus': 'available', 'gptSort': 'quota'}).status_code, 200)
        self.assertEqual(self.read(user='bob').json()['settings'], {})
        restarted = self.make_client()
        response = self.read(client=restarted)
        self.assertEqual(response.json()['settings'], {'gptStatus': 'available', 'gptSort': 'quota'})
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(self.read('api-logs', client=restarted).json()['settings'], {})
        self.save({'codingSort': 'quota'}, client=restarted)
        self.assertEqual(self.read().json()['settings']['gptStatus'], 'available')
        self.assertEqual(self.read().json()['settings']['codingSort'], 'quota')

    def test_unknown_fields_search_text_and_invalid_types_are_rejected(self):
        for settings in ({'q': 'private'}, {'search': 'private'}, {'unknown': True},
                         {'gptStatus': 'invented'}, {'gptSort': ['quota']}, None):
            self.assertEqual(self.save(settings).status_code, 400)
        for payload in ({'settings': {}, 'user_id': 'bob'}, [], 'text'):
            response = self.client.put('/api/view-preferences/resources', headers={'x-user': 'alice'}, json=payload)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.read('missing').status_code, 404)
        self.assertEqual(self.save({'gptSort': 'quota'}, scope='missing').status_code, 404)
        self.assertEqual(self.read().json()['settings'], {})

    def test_api_columns_dates_page_size_and_reset_roundtrip(self):
        settings = {'size': 50, 'type': '2', 'columns': ['time', 'model', 'tokens'], 'range': 'custom',
                    'start': '2026-11-01T00:00:00', 'end': '2026-11-24T23:59:59', 'sensitive': False}
        self.assertEqual(self.save(settings, 'api-logs').status_code, 200)
        for invalid in ({'size': True}, {'size': 1000}, {'columns': ['model']}, {'columns': ['time', 'time']},
                        {'columns': ['time', {}]}, {'sensitive': 'false'}, {'start': '20261124'},
                        {'end': '2026-11-24T23:59:59Z'}, {'model_name': 'private model query'}):
            self.assertEqual(self.save(invalid, 'api-logs').status_code, 400)
        self.assertEqual(self.read('api-logs').json()['settings'], settings)
        self.save({'range': 'today', 'start': '', 'end': '', 'type': '0'}, 'api-logs')
        restored = self.read('api-logs', client=self.make_client()).json()['settings']
        self.assertEqual(restored['start'], '')
        self.assertEqual(restored['range'], 'today')
        self.assertEqual(restored['columns'], settings['columns'])

    def test_finance_scope_and_concurrent_partial_updates_do_not_overwrite_other_fields(self):
        for invalid in ({'from': '2026-02-30'}, {'min': '-1'}, {'max': '2.123'}, {'user': '\n'}, {'q': 'private'}):
            self.assertEqual(self.save(invalid, 'finance-review').status_code, 400)
        self.assertEqual(self.save({'period': 'week', 'status': 'pending'}, 'finance-review').status_code, 200)
        self.assertEqual(self.read('finance-mine').json()['settings'], {})
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.save, partial) for partial in ({'gptSort': 'expiry'}, {'codingStatus': 'low-quota'})]
            self.assertTrue(all(f.result().status_code == 200 for f in futures))
        self.assertEqual(self.read().json()['settings'], {'gptSort': 'expiry', 'codingStatus': 'low-quota'})


if __name__ == '__main__':
    unittest.main()
