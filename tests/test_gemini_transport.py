"""Keep-alive Gemini com HTTP falso; sem rede, ambiente privado ou chamadas ao modelo."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier, Event, get_ident
import unittest
from unittest.mock import patch

import requests

from app.gemini_llm import GENERATE_CONTENT_BASE_URL, GeminiModel
from app.gemini_transport import close_gemini_transport, gemini_session
from app.llm import MAX_RESPONSE_BYTES, ModelError


MODEL = "gemini-3.5-flash-lite"
KEY = "PRIVATE_FIXTURE_KEY_ONE"
OTHER_KEY = "PRIVATE_FIXTURE_KEY_TWO"


def payload(parts=None):
    return {"candidates": [{"finishReason": "STOP", "content": {
        "role": "model", "parts": [{"text": "Resposta fictícia"}] if parts is None else parts,
    }}], "modelVersion": MODEL}


class FakeResponse:
    def __init__(self, *, status=200, raw=None, chunks=None, error=None, entered=None, release=None):
        self.status_code = status
        self.closed = False
        self.error = error
        self.entered = entered
        self.release = release
        body = json.dumps(payload()).encode("utf-8") if raw is None else raw
        self.chunks = [body] if chunks is None else chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, *, chunk_size):
        if self.entered is not None:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("O teste não liberou a resposta simulada.")
        if self.error is not None:
            raise self.error
        yield from self.chunks


class FakeSession:
    def __init__(self):
        self.owner = get_ident()
        self.trust_env = True
        self.headers = {"Accept": "*/*"}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.adapters = []
        self.calls = []
        self.responses = []
        self.next_responses = []
        self.error = None
        self.closed = False
        self.close_calls = 0
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *_args):
        self.close()

    def mount(self, prefix, adapter):
        self.adapters.append((prefix, adapter))

    def post(self, url, **options):
        if self.owner != get_ident() or self.closed:
            raise AssertionError("Sessão compartilhada entre threads ou já fechada.")
        self.calls.append((url, deepcopy(options), dict(self.cookies)))
        if self.error is not None:
            raise self.error
        self.cookies.set("remote_cookie", "must_not_persist")
        response = self.next_responses.pop(0) if self.next_responses else FakeResponse()
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True
        self.close_calls += 1


class GeminiTransportTests(unittest.TestCase):
    def setUp(self):
        close_gemini_transport()
        self.sessions = []
        self.patcher = patch("app.gemini_transport.requests.Session", side_effect=self.make_session)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(close_gemini_transport)

    def make_session(self):
        session = FakeSession()
        self.sessions.append(session)
        return session

    def invoke(self, *, key=KEY, message="Pedido fictício", **options):
        # Cada invocação usa uma instância nova, como as factories da aplicação.
        return GeminiModel(key, MODEL, **options).generate(instructions="Instrução fictícia", message=message)

    def test_construction_does_not_create_a_session(self):
        GeminiModel(KEY, MODEL)
        self.assertEqual(self.sessions, [])

    def test_same_thread_reuses_session_across_short_lived_model_instances(self):
        first = self.invoke(message="Primeiro pedido")
        second = self.invoke(message="Outro pedido")

        self.assertEqual((first.text, second.text), ("Resposta fictícia", "Resposta fictícia"))
        self.assertEqual(len(self.sessions), 1)
        session = self.sessions[0]
        self.assertEqual(len(session.calls), 2)
        self.assertFalse(session.closed)
        self.assertEqual(session.entered, 0)
        self.assertTrue(all(response.closed for response in session.responses))

    def test_default_session_is_configured_once_and_requests_keep_safety_flags(self):
        for _ in range(3):
            self.invoke(timeout_seconds=17, max_output_tokens=900)

        session = self.sessions[0]
        self.assertFalse(session.trust_env)
        self.assertEqual(len(session.adapters), 1)
        prefix, adapter = session.adapters[0]
        self.assertEqual(prefix, "https://")
        self.assertEqual(adapter.max_retries.total, 0)
        for url, options, _cookies in session.calls:
            self.assertEqual(url, GENERATE_CONTENT_BASE_URL + MODEL + ":generateContent")
            self.assertEqual(options["timeout"], (5, 17))
            self.assertIs(options["allow_redirects"], False)
            self.assertIs(options["verify"], True)
            self.assertIs(options["stream"], True)
            self.assertEqual(options["json"]["generationConfig"], {"candidateCount": 1, "maxOutputTokens": 900})

    def test_keys_are_request_scoped_and_remote_history_is_not_reused(self):
        self.invoke(key=KEY, message="Mensagem um")
        self.invoke(key=OTHER_KEY, message="Mensagem dois")

        session = self.sessions[0]
        self.assertEqual([options["headers"]["x-goog-api-key"] for _, options, _ in session.calls], [KEY, OTHER_KEY])
        self.assertNotIn("x-goog-api-key", session.headers)
        for index, (_url, options, cookies) in enumerate(session.calls):
            self.assertEqual(options["json"]["contents"], [{"role": "user", "parts": [{
                "text": "Mensagem um" if index == 0 else "Mensagem dois",
            }]}])
            self.assertEqual(cookies, {})
            self.assertNotIn(KEY, json.dumps(options["json"]))
            self.assertNotIn(OTHER_KEY, json.dumps(options["json"]))
        self.assertEqual(dict(session.cookies), {})

    def test_threads_have_independent_reusable_sessions(self):
        barrier = Barrier(2)
        def worker():
            self.invoke()
            barrier.wait(timeout=5)
            self.invoke()
            return get_ident()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            owners = [future.result(timeout=10) for future in futures]

        self.assertEqual(len(set(owners)), 2)
        self.assertEqual(len(self.sessions), 2)
        self.assertEqual({session.owner for session in self.sessions}, set(owners))
        self.assertTrue(all(len(session.calls) == 2 and not session.closed for session in self.sessions))
        close_gemini_transport()
        self.assertTrue(all(session.closed for session in self.sessions))

    def test_close_is_idempotent_and_same_thread_restarts_with_fresh_session(self):
        self.invoke()
        previous = self.sessions[0]

        close_gemini_transport()
        close_gemini_transport()

        self.assertTrue(previous.closed)
        self.assertEqual(previous.close_calls, 1)
        self.invoke()
        self.assertEqual(len(self.sessions), 2)
        self.assertFalse(self.sessions[1].closed)
        self.assertEqual(len(previous.calls), 1)

    def test_close_invalidates_slots_in_surviving_worker_threads(self):
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(self.invoke).result(timeout=5)
            previous = self.sessions[0]

            close_gemini_transport()
            executor.submit(self.invoke).result(timeout=5)

        self.assertTrue(previous.closed)
        self.assertEqual(len(self.sessions), 2)
        self.assertEqual(self.sessions[0].owner, self.sessions[1].owner)
        self.assertFalse(self.sessions[1].closed)

    def test_shutdown_does_not_close_a_response_while_it_is_in_use(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        with ThreadPoolExecutor(max_workers=1) as executor:
            def blocked_request():
                with gemini_session() as session:
                    session.next_responses = [FakeResponse(entered=entered, release=release)]
                return self.invoke()
            future = executor.submit(blocked_request)
            try:
                self.assertTrue(entered.wait(timeout=5))
                previous = self.sessions[0]
                close_gemini_transport()
                self.assertFalse(previous.closed)
                self.assertFalse(previous.responses[0].closed)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).text, "Resposta fictícia")
            self.assertTrue(previous.closed)
            self.assertTrue(previous.responses[0].closed)
            executor.submit(self.invoke).result(timeout=5)
        self.assertEqual(len(self.sessions), 2)

    def test_nested_leases_do_not_share_an_active_session(self):
        with gemini_session() as first:
            with gemini_session() as second:
                self.assertIsNot(first, second)
                self.assertFalse(first.closed or second.closed)
        self.assertEqual(len(self.sessions), 2)
        self.assertTrue(first.closed)
        self.assertFalse(second.closed)
        close_gemini_transport()
        self.assertTrue(first.closed and second.closed)

    def test_injected_factory_preserves_per_request_context_and_stays_outside_pool(self):
        injected = []
        def factory():
            session = FakeSession()
            injected.append(session)
            return session
        model = GeminiModel(KEY, MODEL, session_factory=factory)

        for message in ("Um pedido", "Outro pedido"):
            model.generate(instructions="Instrução fictícia", message=message)

        self.assertEqual(self.sessions, [])
        self.assertEqual(len(injected), 2)
        for session in injected:
            self.assertEqual(session.entered, 1)
            self.assertTrue(session.closed and session.responses[0].closed)
            self.assertFalse(session.trust_env)
            self.assertEqual(len(session.adapters), 1)
            self.assertEqual(session.adapters[0][1].max_retries.total, 0)
        close_gemini_transport()
        self.assertTrue(all(session.close_calls == 1 for session in injected))

    def test_function_planning_still_calls_gemini_on_every_turn(self):
        plan = payload([{"functionCall": {"name": "ask_query", "args": {"reason": "missing_information"}}}])
        with gemini_session() as session:
            session.next_responses = [FakeResponse(raw=json.dumps(plan).encode()) for _ in range(2)]

        for _ in range(2):
            result = GeminiModel(KEY, MODEL).plan_query(instructions="Planeje a leitura", message="Pedido fictício")
            self.assertEqual(result.name, "ask_query")

        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all(response.closed for response in session.responses))
        self.assertTrue(all(options["json"]["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
                            for _url, options, _cookies in session.calls))

    def test_http_errors_and_redirects_close_responses_without_retry(self):
        for status, code in ((401, "llm_credentials_rejected"), (429, "llm_rate_limited"),
                             (301, "llm_unavailable"), (503, "llm_unavailable")):
            with self.subTest(status=status):
                close_gemini_transport()
                with gemini_session() as session:
                    session.next_responses = [FakeResponse(status=status)]

                with self.assertRaises(ModelError) as caught:
                    self.invoke()

                self.assertEqual(caught.exception.code, code)
                self.assertEqual(len(session.calls), 1)
                self.assertTrue(session.responses[0].closed)
                self.assertFalse(session.closed)
                self.assertEqual(dict(session.cookies), {})
                self.assertNotIn(KEY, str(caught.exception))

    def test_transport_timeout_keeps_safe_error_and_does_not_repeat_request(self):
        with gemini_session() as session:
            session.error = requests.Timeout(KEY)

        with self.assertRaises(ModelError) as caught:
            self.invoke()

        self.assertEqual((caught.exception.code, caught.exception.http_status), ("llm_timeout", 504))
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn(KEY, str(caught.exception))
        session.error = None
        self.assertEqual(self.invoke().text, "Resposta fictícia")
        self.assertEqual(len(self.sessions), 1)

    def test_stream_size_and_chunk_limits_apply_to_pooled_responses(self):
        for chunks in ([b"x" * MAX_RESPONSE_BYTES, b"x"], ["invalid-chunk"]):
            with self.subTest(chunk_kind=type(chunks[0]).__name__):
                with gemini_session() as session:
                    session.next_responses = [FakeResponse(chunks=chunks)]
                before = len(session.calls)

                with self.assertRaises(ModelError) as caught:
                    self.invoke()

                self.assertEqual(caught.exception.code, "llm_invalid_response")
                self.assertEqual(len(session.calls), before + 1)
                self.assertTrue(session.responses[-1].closed)
                self.assertFalse(session.closed)
                self.assertEqual(dict(session.cookies), {})

    def test_malformed_json_never_becomes_a_valid_reply_or_extra_model_request(self):
        with gemini_session() as session:
            session.next_responses = [FakeResponse(raw=b"not-json")]

        with self.assertRaises(ModelError) as caught:
            self.invoke()

        self.assertEqual(caught.exception.code, "llm_invalid_response")
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.responses[0].closed)
        self.assertFalse(session.closed)

    def test_cookie_cleanup_failure_discards_session_without_hiding_valid_reply(self):
        with gemini_session() as session:
            pass
        with patch.object(session.cookies, "clear", side_effect=RuntimeError("cookie cleanup")):
            self.assertEqual(self.invoke().text, "Resposta fictícia")
        self.assertTrue(session.closed)
        self.invoke()
        self.assertEqual(len(self.sessions), 2)

    def test_configuration_failure_closes_session_and_never_posts(self):
        previous_factory = self.make_session
        def broken_factory():
            session = previous_factory()
            def broken_mount(*_args):
                raise RuntimeError(KEY)
            session.mount = broken_mount
            return session
        with patch("app.gemini_transport.requests.Session", side_effect=broken_factory):
            with self.assertRaises(ModelError) as caught:
                self.invoke()
        self.assertEqual(caught.exception.code, "llm_unavailable")
        self.assertEqual(len(self.sessions), 1)
        self.assertTrue(self.sessions[0].closed)
        self.assertEqual(self.sessions[0].calls, [])
        self.assertNotIn(KEY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
