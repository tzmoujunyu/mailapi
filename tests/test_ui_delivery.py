"""UI transfer optimizations must not weaken private response or streaming rules."""
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from mailapi_shared.web_security import SecurityHeadersMiddleware, UICompressionMiddleware


class UIDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = ('const text = "界面组件";\n' * 300).encode()
        (self.root / 'ui.js').write_bytes(self.source)
        (self.root / 'private.html').write_text('private page')
        app = FastAPI()
        app.mount('/static', StaticFiles(directory=self.root))

        @app.get('/api/finance/drafts')
        def drafts():
            return JSONResponse({'private': 'draft' * 1000}, headers={'ETag': '"private"'})

        @app.get('/api2api/v1/stream')
        def stream():
            return StreamingResponse(iter([b'data: ' + b'x' * 2000 + b'\n\n', b'data: [DONE]\n\n']), media_type='text/event-stream')

        app.add_middleware(UICompressionMiddleware)
        app.add_middleware(SecurityHeadersMiddleware)
        self.client = self.enterContext(TestClient(app))

    def test_scripts_are_compressed_and_revalidated_without_resending_the_body(self):
        first = self.client.get('/static/ui.js', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(first.content, self.source)
        self.assertEqual(first.headers['content-encoding'], 'gzip')
        self.assertLess(int(first.headers['content-length']), len(self.source) // 4)
        self.assertEqual(first.headers['cache-control'], 'private, no-cache')
        self.assertIn('Accept-Encoding', first.headers['vary'])
        self.assertTrue(first.headers['etag'].startswith('W/'))
        same = self.client.get('/static/ui.js', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(same.status_code, 304)
        self.assertEqual(same.content, b'')
        (self.root / 'ui.js').write_bytes(self.source + b'// changed')
        changed = self.client.get('/static/ui.js', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed.headers['etag'], first.headers['etag'])
        plain = self.client.get('/static/ui.js', headers={'Accept-Encoding': 'identity'})
        self.assertNotIn('content-encoding', plain.headers)
        self.assertEqual(plain.content, changed.content)

    def test_private_data_html_errors_and_model_streams_remain_uncached_and_uncompressed(self):
        for path in ('/api/finance/drafts', '/static/private.html', '/static/missing.js', '/api2api/v1/stream'):
            with self.subTest(path=path):
                response = self.client.get(path, headers={'Accept-Encoding': 'gzip'})
                self.assertEqual(response.headers['cache-control'], 'no-store, private')
                self.assertNotIn('content-encoding', response.headers)
        stream = self.client.get('/api2api/v1/stream')
        self.assertEqual(stream.content, b'data: ' + b'x' * 2000 + b'\n\ndata: [DONE]\n\n')
