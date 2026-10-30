"""The standalone demo serves fictional assets, never the live app or private files."""

from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest

from scripts.preview_panel import PreviewHandler


class QuietPreview(PreviewHandler):
    def log_message(self, _format, *args):
        pass


class StandaloneDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / 'web'
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietPreview, directory=str(root)))
        cls.worker = Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=3)

    def request(self, path, *, method='GET', headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            request_headers = {'Host': f'127.0.0.1:{self.server.server_port}'}
            request_headers.update(headers or {})
            connection.request(method, path, headers=request_headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def test_public_demo_assets_and_security_headers(self):
        for path in ['/', '/index.html', '/app.js', '/demo.js', '/demo.css', '/styles.css',
                     '/assets/mark.svg', '/assets/interview-background.png']:
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertIn("connect-src 'none'", headers['Content-Security-Policy'])
                self.assertEqual(headers['X-Frame-Options'], 'DENY')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Referrer-Policy'], 'no-referrer')

    def test_live_files_traversal_directory_listing_and_api_are_unavailable(self):
        for path in ['/live.html', '/live.js', '/live.css', '/README.md', '/.env', '/assets/',
                     '/assets/../live.js', '/%6cive.js', '/assets/%2e%2e/live.js',
                     '/%2e%2e/.env', '/../.env', '/painel/api/events', '/api/v1/events',
                     '/unlisted.css', 'http://example.invalid/app.js', 'http://[invalid']:
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertIn("connect-src 'none'", headers['Content-Security-Policy'])
                self.assertNotIn(b'OPENAI_API_KEY=', body)

    def test_foreign_hosts_origins_and_forwarded_requests_are_rejected(self):
        own_host = f'127.0.0.1:{self.server.server_port}'
        for headers in [{'Host': 'example.invalid'}, {'Origin': 'https://example.invalid'},
                        {'X-Forwarded-For': '127.0.0.1'}, {'Forwarded': 'for=127.0.0.1'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Host': own_host, 'Origin': 'http://localhost:1'}]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/', headers=headers)[0], 403)
        for host in [own_host, f'localhost:{self.server.server_port}']:
            self.assertEqual(self.request('/', headers={'Host': host, 'Origin': 'http://' + host,
                                                       'Sec-Fetch-Site': 'same-origin'})[0], 200)

    def test_head_and_cookie_do_not_create_authentication_state(self):
        status, headers, body = self.request('/app.js', method='HEAD',
            headers={'Cookie': 'genius_panel_session=' + 'a' * 43})
        self.assertEqual(status, 200)
        self.assertFalse(body)
        self.assertNotIn('Set-Cookie', headers)
        self.assertIn("connect-src 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/painel/api/session')[0], 404)


if __name__ == '__main__':
    unittest.main()
