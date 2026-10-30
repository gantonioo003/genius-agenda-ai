"""Panel session/Origin/CSRF behavior through ASGI, without providers or sockets."""

from contextlib import closing
import asyncio
from http.cookies import SimpleCookie
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import APIRouter, Depends, FastAPI

from app.panel_access import PanelAccessStore, PanelStoreError
from app.panel_security import (
    COOKIE_NAME, PanelAccessError, panel_access_error_handler,
    register_panel_auth_routes, require_panel_session,
)


async def invoke(app, path, *, method="GET", headers=(), body=b"", query=b"", client="127.0.0.1", scheme="http"):
    messages = []
    delivered = False
    done = asyncio.Event()
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "scheme": scheme, "method": method, "root_path": "",
             "path": path, "raw_path": path.encode("ascii"), "query_string": query,
             "headers": list(headers), "server": ("127.0.0.1", 8000), "client": (client, 30000)}
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            done.set()
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], {key.decode().lower(): value.decode() for key, value in start["headers"]}, json.loads(raw)


class PanelSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1000.0
        self.store = PanelAccessStore(Path(self.directory.name) / "access.sqlite3", clock=lambda: self.now)
        self.app = FastAPI()
        self.app.state.settings = SimpleNamespace(api_port=8000, panel_enabled=True, api_auth_token="PRIVATE_SERVICE_KEY_SHOULD_NOT_APPEAR")
        self.app.state.panel_access_store_factory = lambda: self.store
        self.app.add_exception_handler(PanelAccessError, panel_access_error_handler)
        router = APIRouter(prefix="/painel/api")
        register_panel_auth_routes(router)
        @router.get("/private", dependencies=[Depends(require_panel_session)])
        async def private():
            return {"read": True}
        @router.post("/private", dependencies=[Depends(require_panel_session)])
        async def operation():
            return {"operation": True}
        self.app.include_router(router)

    def request(self, path="/painel/api/session", *, method="GET", sid=None, csrf=None, headers=None, body=None, **kwargs):
        values = [(b"host", b"127.0.0.1:8000")]
        if method == "POST":
            values += [(b"origin", b"http://127.0.0.1:8000"), (b"content-type", b"application/json")]
        if sid is not None:
            values.append((b"cookie", f"{COOKIE_NAME}={sid}".encode("ascii")))
        if csrf is not None:
            values.append((b"x-panel-csrf", csrf.encode("utf-8")))
        if headers is not None:
            replaced = {key.lower() for key, _value in headers}
            values = [item for item in values if item[0].lower() not in replaced]
            values.extend(headers)
        result = asyncio.run(invoke(self.app, path, method=method, headers=values, body=body if body is not None else b"{}" if method == "POST" else b"", **kwargs))
        self.assertNotIn("PRIVATE_SERVICE_KEY_SHOULD_NOT_APPEAR", json.dumps(result))
        if path != "/painel/api/private" or result[0] >= 400:
            self.assertEqual(result[1]["cache-control"], "no-store")
            self.assertEqual(result[1]["vary"], "Cookie")
        return result

    def begin(self):
        response = self.request("/painel/api/auth/start", method="POST")
        self.assertEqual(response[0], 200)
        cookie = SimpleCookie()
        cookie.load(response[1]["set-cookie"])
        return cookie[COOKIE_NAME].value, response

    def activate(self):
        sid, response = self.begin()
        self.assertTrue(self.store.authorize(response[2]["pairing_code"]))
        active = self.request(sid=sid)
        self.assertTrue(active[2]["authenticated"])
        return sid, active[2]["csrf_token"]

    def test_without_cookie_session_returns_no_private_data(self):
        result = self.request()
        self.assertEqual(result[2], {"authenticated": False, "pending": False})
        self.assertNotIn("set-cookie", result[1])

    def test_pending_request_and_cookie_are_bound_to_exact_browser(self):
        sid, response = self.begin()
        self.assertFalse(response[2]["authenticated"])
        self.assertTrue(response[2]["pending"])
        cookie = response[1]["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Path=/painel", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Max-Age=300", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertNotIn(sid, json.dumps(response[2]))
        self.assertEqual(self.request("/painel/api/private", sid=sid)[0], 401)

    def test_pairing_code_is_not_an_http_credential(self):
        _sid, response = self.begin()
        code = response[2]["pairing_code"]
        result = self.request("/painel/api/private", headers=[(b"authorization", ("Bearer " + code).encode())])
        self.assertEqual(result[0], 401)

    def test_cli_authorization_unblocks_only_cookie_owner(self):
        sid, csrf = self.activate()
        self.assertEqual(self.request("/painel/api/private", sid=sid)[0], 200)
        self.assertEqual(self.request("/painel/api/private")[0], 401)
        self.assertNotEqual(sid, csrf)

    def test_start_with_valid_cookie_keeps_original_pending_code(self):
        sid, first = self.begin()
        response = self.request("/painel/api/auth/start", method="POST", sid=sid)
        self.assertEqual(response[2], first[2])

    def test_https_local_request_marks_cookie_secure(self):
        response = self.request("/painel/api/auth/start", method="POST", scheme="https", headers=[(b"origin", b"https://127.0.0.1:8000")])
        self.assertEqual(response[0], 200)
        self.assertIn("Secure", response[1]["set-cookie"])

    def test_missing_and_wrong_csrf_reject_mutation(self):
        sid, csrf = self.activate()
        for token in [None, "", "X" * 43, "é" * 43]:
            with self.subTest(token=token):
                self.assertEqual(self.request("/painel/api/private", method="POST", sid=sid, csrf=token)[0], 403)
        self.assertEqual(self.request("/painel/api/private", method="POST", sid=sid, csrf=csrf)[0], 200)

    def test_duplicate_csrf_is_rejected(self):
        sid, csrf = self.activate()
        response = self.request("/painel/api/private", method="POST", sid=sid,
                                headers=[(b"x-panel-csrf", csrf.encode()), (b"x-panel-csrf", csrf.encode())])
        self.assertEqual(response[0], 403)

    def test_logout_requires_csrf_and_revokes_cookie(self):
        sid, csrf = self.activate()
        self.assertEqual(self.request("/painel/api/auth/logout", method="POST", sid=sid)[0], 403)
        result = self.request("/painel/api/auth/logout", method="POST", sid=sid, csrf=csrf)
        self.assertEqual(result[0], 200)
        self.assertIn("Max-Age=0", result[1]["set-cookie"])
        self.assertIn("Path=/painel", result[1]["set-cookie"])
        self.assertEqual(self.request("/painel/api/private", sid=sid)[0], 401)

    def test_expired_cookie_is_cleared_and_requires_new_pairing(self):
        sid, _csrf = self.activate()
        self.now += 1800
        response = self.request(sid=sid)
        self.assertFalse(response[2]["authenticated"])
        self.assertIn("Max-Age=0", response[1]["set-cookie"])
        self.assertEqual(self.request("/painel/api/private", sid=sid)[0], 401)

    def test_wrong_hosts_ports_clients_and_forwarding_are_denied(self):
        for host in [b"evil.test:8000", b"localhost.evil:8000", b"localhost:8015", b"localhost", b"127.0.0.1:8000@evil.test"]:
            with self.subTest(host=host):
                self.assertEqual(self.request(headers=[(b"host", host)])[0], 403)
        for client in ["192.0.2.2", "testclient", "localhost"]:
            with self.subTest(client=client):
                self.assertEqual(self.request(client=client)[0], 403)
        for header in [b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto"]:
            self.assertEqual(self.request(headers=[(header, b"127.0.0.1")])[0], 403)

    def test_ipv6_loopback_and_localhost_are_valid(self):
        self.assertEqual(self.request(headers=[(b"host", b"localhost:8000")])[0], 200)
        self.assertEqual(self.request(headers=[(b"host", b"[::1]:8000")], client="::1")[0], 200)

    def test_cross_origin_and_cross_site_are_rejected(self):
        for origin in [b"null", b"http://evil.test", b"http://localhost:8000", b"http://127.0.0.1:8015"]:
            with self.subTest(origin=origin):
                self.assertEqual(self.request(headers=[(b"origin", origin)])[0], 403)
        for site in [b"cross-site", b"same-site", b"", b"invalid"]:
            self.assertEqual(self.request(headers=[(b"sec-fetch-site", site)])[0], 403)
        for site in [b"same-origin", b"none"]:
            self.assertEqual(self.request(headers=[(b"sec-fetch-site", site)])[0], 200)

    def test_write_requires_an_origin_even_when_cookie_is_valid(self):
        # An empty value replaces the default Origin and must be rejected.
        sid, csrf = self.activate()
        self.assertEqual(self.request("/painel/api/private", method="POST", sid=sid, csrf=csrf,
                                      headers=[(b"origin", b"")])[0], 403)

    def test_duplicate_security_headers_are_rejected(self):
        for header, value in [(b"host", b"127.0.0.1:8000"), (b"origin", b"http://127.0.0.1:8000"), (b"sec-fetch-site", b"same-origin")]:
            with self.subTest(header=header):
                self.assertEqual(self.request(headers=[(header, value), (header, value)])[0], 403)

    def test_duplicate_or_malformed_session_cookies_do_not_authenticate(self):
        sid, _csrf = self.activate()
        for values in [[(b"cookie", f"{COOKIE_NAME}={sid}; {COOKIE_NAME}={sid}".encode())],
                       [(b"cookie", f"{COOKIE_NAME}={sid}".encode()), (b"cookie", f"{COOKIE_NAME}={sid}".encode())],
                       [(b"cookie", f"{COOKIE_NAME}=wrong".encode())]]:
            self.assertEqual(self.request("/painel/api/private", headers=values)[0], 401)

    def test_start_body_is_strict_bounded_empty_json(self):
        for body in [b"", b"null", b"[]", b'{"x":1}', b"{" + b" " * 1024 + b"}", b"{}{}", b"\xff"]:
            with self.subTest(body=body[:16]):
                self.assertEqual(self.request("/painel/api/auth/start", method="POST", body=body)[0], 422)
        self.assertEqual(self.request("/painel/api/auth/start", method="POST", headers=[(b"content-type", b"text/plain")])[0], 422)
        self.assertEqual(self.request("/painel/api/auth/start", method="POST", query=b"extra=1")[0], 422)
        self.assertEqual(self.request(query=b"extra=1")[0], 422)

    def test_rate_limit_has_retry_after_and_reused_cookie_does_not_count(self):
        sid, _first = self.begin()
        for _ in range(20):
            self.assertEqual(self.request("/painel/api/auth/start", method="POST", sid=sid)[0], 200)
        for _ in range(9):
            self.begin()
        result = self.request("/painel/api/auth/start", method="POST")
        self.assertEqual(result[0], 429)
        self.assertEqual(result[1]["retry-after"], "60")

    def test_disabled_panel_is_not_accessible(self):
        self.app.state.settings.panel_enabled = False
        self.assertEqual(self.request()[0], 404)
        self.assertEqual(self.request("/painel/api/auth/start", method="POST")[0], 404)

    def test_unavailable_store_fails_closed_without_leaking_details(self):
        sid, _csrf = self.activate()
        def unavailable():
            raise RuntimeError("SECRET_PATH_PRIVATE")
        self.app.state.panel_access_store_factory = unavailable
        result = self.request(sid=sid)
        self.assertEqual(result[0], 503)
        self.assertNotIn("SECRET_PATH_PRIVATE", json.dumps(result))

    def test_corrupt_store_fails_closed(self):
        sid, _csrf = self.activate()
        import sqlite3
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            connection.execute("UPDATE panel_sessions SET csrf_token='invalid'")
        self.assertEqual(self.request(sid=sid)[0], 503)


if __name__ == "__main__":
    unittest.main()
