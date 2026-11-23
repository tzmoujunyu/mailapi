from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mailapi_shared.test_features import install_test_features
from tests import test_newapi_gateway as gateway_tests


class RelayResponsivenessTests(unittest.TestCase):
    setUp = gateway_tests.GatewayHttpTests.setUp
    create = gateway_tests.GatewayHttpTests.create

    def test_wal_is_reused_between_requests_and_flushed_when_the_application_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = FastAPI()
            store = install_test_features(app, Path(directory), directory, None)
            with TestClient(app):
                key = store.create_api_key('alice', 'WAL fixture')
                wal = Path(str(store.path)+'-wal')
                self.assertTrue(wal.is_file(), 'The application should retain its WAL between requests')
                inode = wal.stat().st_ino
                for _ in range(5):
                    store.record_usage(key_id=key['id'], user_id='alice', model='fixture', prompt_tokens=2,
                                       completion_tokens=3, total_tokens=5, status_code=200, request_ms=1)
                    self.assertEqual(store.authenticate_api_key(key['api_key'])['user_id'], 'alice')
                    self.assertEqual(wal.stat().st_ino, inode)
            self.assertFalse(wal.exists(), 'The last WAL connection should be closed at shutdown')
            self.assertEqual(store.usage_summary()['total_tokens'], 25)

    def test_slow_authentication_and_usage_writes_do_not_block_unrelated_requests(self):
        key = self.create()

        @self.app.get('/fixture-heartbeat')
        async def heartbeat():
            return {'ok': True}

        async def upstream(request):
            if b'"stream":true' in request.content:
                return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                    content=b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}\n\ndata: [DONE]\n\n')
            return httpx.Response(200, json={'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}})

        relay = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        for method, stream in [('authenticate_api_key', False), ('record_usage', False), ('record_usage', True)]:
            with self.subTest(method=method, stream=stream):
                entered, release = threading.Event(), threading.Event()
                original = getattr(self.store, method)

                def slow_database(*args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('The blocked fixture was not released')
                    return original(*args, **kwargs)

                with patch.object(self.store, method, slow_database), \
                        patch('mailapi_shared.test_features.httpx.AsyncClient', return_value=relay), \
                        ThreadPoolExecutor(max_workers=2) as executor:
                    pending = executor.submit(self.client.post, '/api2api/v1/chat/completions',
                        json={'model': 'gpt-4o-mini', 'stream': stream},
                        headers={'Authorization': 'Bearer '+key['api_key']})
                    try:
                        self.assertTrue(entered.wait(2), 'Relay did not reach the fixture database operation')
                        health = executor.submit(self.client.get, '/fixture-heartbeat')
                        try:
                            response = health.result(timeout=1)
                        except TimeoutError:
                            self.fail('A synchronous relay database operation blocked the website event loop')
                        self.assertEqual(response.status_code, 200)
                        self.assertFalse(pending.done(), 'The fixture database should still be blocked')
                    finally:
                        release.set()
                        self.assertEqual(pending.result(timeout=3).status_code, 200)
        self.assertEqual(self.store.usage_summary()['requests'], 3)
        self.assertEqual(self.store.usage_summary()['total_tokens'], 15)


if __name__ == '__main__':
    unittest.main()
