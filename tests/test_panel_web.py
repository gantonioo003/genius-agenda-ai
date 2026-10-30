"""The live shell exposes only listed assets and keeps service auth separate."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.main import create_app
from app.panel_access import PanelAccessStore
from app.settings import Settings


SECRET = 'OnlyTestsServiceKey_0123456789AbCdEfGh'


async def raw_request(app, path, *, headers=(), method='GET', body=b'', client='127.0.0.1'):
    sent = []
    delivered = False
    complete = asyncio.Event()
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'http_version': '1.1', 'scheme': 'http', 'method': method,
             'root_path': '', 'path': path, 'raw_path': path.encode('ascii'),
             'query_string': b'', 'headers': list(headers),
             'server': ('127.0.0.1', 8000), 'client': (client, 12345)}

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await complete.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        sent.append(message)
        if message['type'] == 'http.response.body' and not message.get('more_body'):
            complete.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    start = next(message for message in sent if message['type'] == 'http.response.start')
    data = b''.join(message.get('body', b'') for message in sent if message['type'] == 'http.response.body')
    response_headers = {key.decode().lower(): value.decode('latin-1') for key, value in start['headers']}
    return start['status'], response_headers, data


class PanelWebTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = Settings(_env_file=None, app_env='test', api_auth_token=SECRET,
                               panel_database_path=Path(self.temporary.name) / 'panel.sqlite3')
        self.app = create_app(config=self.config)

    def request(self, path, *, headers=None, **kwargs):
        return asyncio.run(raw_request(self.app, path,
            headers=[(b'host', b'127.0.0.1:8000')] if headers is None else headers, **kwargs))

    def test_canonical_redirects_are_relative_and_do_not_include_credentials(self):
        for path, destination in [('/painel', '/painel/'), ('/painel/demo', '/painel/demo/')]:
            with self.subTest(path=path):
                code, headers, body = self.request(path)
                self.assertEqual(code, 303)
                self.assertEqual(headers['location'], destination)
                self.assertEqual(headers['cache-control'], 'no-store')
                self.assertNotIn(SECRET.encode(), body)

    def test_shells_and_assets_have_separate_content_policies(self):
        for path, content in [('/painel/', b'live.js'), ('/painel/demo/', b'app.js')]:
            with self.subTest(path=path):
                code, headers, body = self.request(path)
                self.assertEqual(code, 200)
                self.assertIn(content, body)
                self.assertIn("frame-ancestors 'none'", headers['content-security-policy'])
                self.assertIn("connect-src 'none'" if path.startswith('/painel/demo/') else "connect-src 'self'",
                              headers['content-security-policy'])
                self.assertEqual(headers['cache-control'], 'no-store')
                self.assertEqual(headers['x-content-type-options'], 'nosniff')
                self.assertEqual(headers['x-frame-options'], 'DENY')
                self.assertEqual(headers['referrer-policy'], 'no-referrer')
                self.assertIn('Cookie', headers['vary'])
                self.assertNotIn(SECRET.encode(), body)
        self.assertIn(b"connect-src 'none'", self.request('/painel/demo/')[2])

    def test_only_whitelisted_assets_are_served(self):
        valid = ['/painel/live.js', '/painel/live.css', '/painel/styles.css', '/painel/assets/mark.svg',
                 '/painel/demo/app.js', '/painel/demo/demo.js', '/painel/demo/demo.css', '/painel/demo/styles.css']
        for path in valid:
            with self.subTest(path=path):
                code, headers, body = self.request(path)
                self.assertEqual(code, 200)
                self.assertTrue(body)
                self.assertEqual(headers['cache-control'], 'no-store')
                self.assertNotIn(SECRET.encode(), body)
        for path in ['/painel/.env', '/painel/README.md', '/painel/../.env', '/painel/assets/',
                     '/painel/demo/../../.env', '/painel/demo/index.html', '/painel/app.js',
                     '/painel/demo/live.js', '/painel/demo/live.css', '/painel/demo/live.html',
                     '/painel/demo/api/events', '/painel/demo/README.md', '/painel/demo/assets/']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_local_boundary_also_applies_to_public_shell_and_assets(self):
        for path in ['/painel/', '/painel/live.js', '/painel/demo/']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, client='203.0.113.4')[0], 403)
                self.assertEqual(self.request(path, headers=[(b'host', b'example.invalid:8000')])[0], 403)
                self.assertEqual(self.request(path, headers=[(b'host', b'127.0.0.1:8000'), (b'x-forwarded-for', b'127.0.0.1')])[0], 403)
                self.assertEqual(self.request(path, headers=[(b'host', b'127.0.0.1:8000'), (b'sec-fetch-site', b'cross-site')])[0], 403)

    def test_disabling_panel_does_not_disable_legacy_api(self):
        self.app = create_app(config=self.config.model_copy(update={'panel_enabled': False}))
        self.assertEqual(self.request('/painel/')[0], 404)
        self.assertEqual(self.request('/health')[0], 200)
        self.assertEqual(self.request('/api/v1/status')[0], 401)
        headers = [(b'host', b'127.0.0.1:8000'), (b'authorization', ('Bearer '+SECRET).encode())]
        self.assertEqual(self.request('/api/v1/status', headers=headers)[0], 200)

    def test_browser_cookie_is_never_a_service_bearer_credential(self):
        headers = [(b'host', b'127.0.0.1:8000'), (b'cookie', b'genius_panel_session=opaque')]
        self.assertEqual(self.request('/api/v1/status', headers=headers)[0], 401)
        self.assertEqual(self.request('/painel/api/events', headers=headers)[0], 401)

    def test_demo_reads_neither_private_sessions_nor_provider_factories(self):
        calls = []

        def forbidden():
            calls.append('private_factory')
            raise AssertionError('The fictional demo must not open private providers or stores.')

        self.app = create_app(config=self.config, calendar_gateway_factory=forbidden,
                              panel_calendar_gateway_factory=forbidden,
                              panel_access_store_factory=forbidden,
                              reminder_store_factory=forbidden,
                              summary_model_factory=forbidden)
        for cookie in [None, 'genius_panel_session=' + 'a' * 43, 'genius_panel_session=malformed']:
            for path in ['/painel/demo/', '/painel/demo/app.js', '/painel/demo/demo.js',
                         '/painel/demo/styles.css', '/painel/demo/demo.css']:
                with self.subTest(cookie=cookie is not None, path=path):
                    headers = [(b'host', b'127.0.0.1:8000')]
                    if cookie:
                        headers.append((b'cookie', cookie.encode()))
                    status, response_headers, _ = self.request(path, headers=headers)
                    self.assertEqual(status, 200)
                    self.assertNotIn('set-cookie', response_headers)
                    self.assertIn("connect-src 'none'", response_headers['content-security-policy'])
        self.assertEqual(calls, [])
        self.assertFalse(self.config.panel_database_path.exists())

    def test_exploring_demo_does_not_touch_or_revoke_existing_real_session(self):
        access = PanelAccessStore(self.config.panel_database_path)
        sid, pending = access.begin()
        self.assertTrue(access.authorize(pending.pairing_code))
        ledger_before = sha256(self.config.panel_database_path.read_bytes()).hexdigest()
        self.app = create_app(config=self.config, panel_access_store_factory=lambda: access)
        headers = [(b'host', b'127.0.0.1:8000'), (b'cookie', ('genius_panel_session=' + sid).encode())]
        for path in ['/painel/demo/', '/painel/demo/app.js', '/painel/demo/demo.js',
                     '/painel/demo/demo.css', '/painel/demo/styles.css', '/painel/demo/live.js']:
            status, response_headers, _ = self.request(path, headers=headers)
            self.assertIn(status, (200, 404))
            self.assertNotIn('set-cookie', response_headers)
        self.assertEqual(sha256(self.config.panel_database_path.read_bytes()).hexdigest(), ledger_before)
        status, _, body = self.request('/painel/api/session', headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['authenticated'])
        self.assertEqual(self.request('/api/v1/status', headers=headers)[0], 401)


if __name__ == '__main__':
    unittest.main()
