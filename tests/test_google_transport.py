"""Contrato do transporte e repetição explícita do SDK, sem acesso à rede."""

import unittest
from unittest.mock import Mock, patch

import httplib2
from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.http import HttpRequest
import requests

from app.google_transport import SingleAttemptHttp, TRANSPORT_ERROR


class SingleAttemptHttpTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.patch_session = patch(
            "app.google_transport.requests.Session", return_value=self.session
        )
        self.patch_session.start()
        self.addCleanup(self.patch_session.stop)
        self.http = SingleAttemptHttp()
        self.addCleanup(self.http.close)

    def response(self, *, status=200, reason="OK", headers=None, content=b"{}"):
        response = Mock(spec=requests.Response)
        response.status_code = status
        response.reason = reason
        response.headers = headers or {"Content-Type": "application/json"}
        response.content = content
        self.session.request.return_value = response
        return response

    def test_mounts_zero_retry_adapters_for_both_schemes(self):
        mounted = dict(call.args for call in self.session.mount.call_args_list)
        self.assertEqual(set(mounted), {"http://", "https://"})
        for adapter in mounted.values():
            self.assertEqual(adapter.max_retries.total, 0)
            self.assertEqual(adapter.max_retries.read, False)

    def test_each_method_sends_body_headers_and_timeout_once(self):
        for method in ("GET", "POST", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.session.request.reset_mock()
                response = self.response()
                headers = {"Authorization": "Bearer private", "If-Match": '"v7"'}
                body = b'{"summary":"private title"}'
                self.http.request(
                    "https://example.invalid/calendar", method,
                    body=body, headers=headers,
                )
                self.session.request.assert_called_once_with(
                    method=method, url="https://example.invalid/calendar",
                    data=body, headers=headers, timeout=20,
                    allow_redirects=False, verify=True,
                )
                self.assertEqual(headers["If-Match"], '"v7"')
                response.close.assert_called_once_with()

    def test_timeout_can_be_changed_through_authorized_http(self):
        auth_http = AuthorizedHttp(AnonymousCredentials(), http=self.http)
        auth_http.timeout = 7
        self.response()
        auth_http.request("https://example.invalid/calendar")
        self.assertEqual(self.session.request.call_args.kwargs["timeout"], 7)
        self.assertEqual(auth_http.timeout, 7)
        self.assertFalse(auth_http.follow_redirects)
        self.assertIs(auth_http.connections, self.http.connections)

    def test_constructor_timeout_is_used(self):
        http = SingleAttemptHttp(timeout=3)
        self.addCleanup(http.close)
        self.response()
        http.request("https://example.invalid/calendar")
        self.assertEqual(self.session.request.call_args.kwargs["timeout"], 3)

    def test_returns_httplib_response_status_reason_headers_and_bytes(self):
        self.response(
            status=201, reason="Created", headers={"ETag": '"v8"'},
            content=b'{"id":"event"}',
        )
        response, content = self.http.request("https://example.invalid/calendar")
        self.assertIsInstance(response, httplib2.Response)
        self.assertEqual(response.status, 201)
        self.assertEqual(response["status"], "201")
        self.assertEqual(response.reason, "Created")
        self.assertEqual(response["etag"], '"v8"')
        self.assertEqual(content, b'{"id":"event"}')

    def test_redirect_is_returned_without_forwarding_credentials_or_body(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.session.request.reset_mock()
                self.response(status=status, headers={
                    "Location": "https://other.invalid/target",
                })
                # Mesmo que alguém altere a propriedade compatível, a política
                # de não reenviar permanece no argumento da chamada real.
                self.http.follow_redirects = True
                response, _ = self.http.request(
                    "https://example.invalid/calendar", "POST",
                    body=b"private", headers={"Authorization": "Bearer private"},
                    redirections=10,
                )
                self.assertEqual(response.status, status)
                self.session.request.assert_called_once()
                self.assertFalse(self.session.request.call_args.kwargs["allow_redirects"])
                self.assertEqual(
                    self.session.request.call_args.kwargs["url"],
                    "https://example.invalid/calendar",
                )

    def test_decompressed_response_has_consistent_headers(self):
        self.response(headers={
            "Content-Encoding": "gzip", "Content-Length": "999",
            "Content-Type": "application/json",
        }, content=b'{"decoded":true}')
        response, content = self.http.request("https://example.invalid/calendar")
        self.assertNotIn("content-encoding", response)
        self.assertEqual(response["content-length"], str(len(content)))
        self.assertEqual(response["content-type"], "application/json")

    def test_request_failures_have_one_attempt_and_safe_message(self):
        for failure in (
            requests.Timeout("secret token https://private.invalid"),
            requests.ConnectionError("Authorization: Bearer secret"),
            requests.exceptions.ChunkedEncodingError("private body"),
            requests.exceptions.SSLError("private certificate details"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.session.request.reset_mock()
                self.session.request.side_effect = failure
                with self.assertRaises(ConnectionError) as raised:
                    self.http.request("https://private.invalid/secret", "POST")
                self.assertEqual(str(raised.exception), TRANSPORT_ERROR)
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)
                self.session.request.assert_called_once()

    def test_reading_broken_response_closes_it_and_hides_details(self):
        response = self.response()
        type(response).content = property(lambda _: (_ for _ in ()).throw(
            requests.exceptions.ChunkedEncodingError("private body")
        ))
        with self.assertRaisesRegex(ConnectionError, TRANSPORT_ERROR):
            self.http.request("https://example.invalid/calendar", "PATCH")
        response.close.assert_called_once_with()
        self.session.request.assert_called_once()

    def test_close_failure_also_uses_safe_transport_message(self):
        response = self.response()
        response.close.side_effect = requests.ConnectionError("private cleanup detail")
        with self.assertRaises(ConnectionError) as raised:
            self.http.request("https://example.invalid/calendar")
        self.assertEqual(str(raised.exception), TRANSPORT_ERROR)

    def test_close_closes_session(self):
        self.http.close()
        self.session.close.assert_called_once_with()

    def test_accepts_authorized_http_compatibility_arguments(self):
        self.response()
        self.http.request(
            "http://localhost:8000/test", "GET", redirections=0,
            connection_type=None,
        )
        self.session.request.assert_called_once()

    def test_unsupported_connection_options_fail_before_request(self):
        with self.assertRaisesRegex(TypeError, "não suportadas"):
            self.http.request("https://example.invalid", connection_type=object)
        with self.assertRaisesRegex(TypeError, "não suportadas"):
            self.http.request("https://example.invalid", unknown="private")
        self.session.request.assert_not_called()

    def test_sdk_write_execute_zero_does_not_replay_transport_failure(self):
        for method in ("POST", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.session.request.reset_mock()
                self.session.request.side_effect = requests.ConnectionError("private")
                request = HttpRequest(
                    self.http, lambda response, content: content,
                    "https://example.invalid/calendar", method=method, body=b"{}",
                )
                request._sleep = Mock()
                with self.assertRaises(ConnectionError):
                    request.execute(num_retries=0)
                self.session.request.assert_called_once()
                request._sleep.assert_not_called()

    def test_sdk_read_can_explicitly_retry_two_times(self):
        self.session.request.side_effect = requests.Timeout("private")
        request = HttpRequest(
            self.http, lambda response, content: content,
            "https://example.invalid/calendar", method="GET",
        )
        request._sleep = Mock()
        request._rand = lambda: 0
        with self.assertRaises(ConnectionError):
            request.execute(num_retries=2)
        self.assertEqual(self.session.request.call_count, 3)
        self.assertEqual(request._sleep.call_count, 2)

    def test_sdk_can_decode_successful_json_response(self):
        self.response(content=b'{"id":"event"}')
        request = HttpRequest(
            AuthorizedHttp(AnonymousCredentials(), http=self.http),
            lambda response, content: content.decode("utf-8"),
            "https://example.invalid/calendar", method="GET",
        )
        self.assertEqual(request.execute(num_retries=2), '{"id":"event"}')
        self.session.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
