"""Contrato do LLM e transporte REST com fixtures, sem rede ou credenciais reais."""

from dataclasses import FrozenInstanceError
import json
import unittest
from unittest.mock import patch

import requests

from app.llm import (
    MAX_REPLY_CHARACTERS, MAX_RESPONSE_BYTES, RESPONSES_URL,
    ModelError, ModelReply, OpenAIResponsesModel,
)


KEY = "PRIVATE_FIXTURE_KEY_DO_NOT_LOG"
INPUT = "PRIVATE_FIXTURE_INPUT_DO_NOT_LOG"
INSTRUCTIONS = "PRIVATE_FIXTURE_INSTRUCTIONS_DO_NOT_LOG"
OUTPUT = "PRIVATE_FIXTURE_OUTPUT_DO_NOT_LOG"
RESPONSE_ID = "resp_PRIVATE_FIXTURE_ID_DO_NOT_LOG"


def reply_payload():
    return {
        "id": RESPONSE_ID, "status": "completed", "model": "example-model-2026-10-01",
        "error": None, "incomplete_details": None,
        "output": [{"type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": OUTPUT, "annotations": []}]}],
        "usage": {"input_tokens": 32, "output_tokens": 12},
    }


class FakeResponse:
    def __init__(self, *, payload=None, raw=None, chunks=None, status=200, error=None):
        self.status_code = status
        if raw is None:
            raw = json.dumps(reply_payload() if payload is None else payload).encode("utf-8")
        self.chunks = [raw] if chunks is None else chunks
        self.error = error
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, *, chunk_size):
        if self.error:
            raise self.error
        yield from self.chunks


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = FakeResponse() if response is None else response
        self.error = error
        self.calls = []
        self.adapters = []
        self.closed = False
        self.trust_env = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def mount(self, prefix, adapter):
        self.adapters.append((prefix, adapter))

    def post(self, url, **options):
        self.calls.append((url, options))
        if self.error:
            raise self.error
        return self.response


class ModelTransportTests(unittest.TestCase):
    def model(self, *, response=None, error=None, **options):
        self.session = FakeSession(response=response, error=error)
        return OpenAIResponsesModel(
            KEY, "example-model", session_factory=lambda: self.session, **options,
        )

    def generate(self, model):
        return model.generate(instructions=INSTRUCTIONS, message=INPUT)

    def assert_safe_error(self, action, code, status):
        with self.assertRaises(ModelError) as result:
            action()
        self.assertEqual(result.exception.code, code)
        self.assertEqual(result.exception.http_status, status)
        self.assertIsNone(result.exception.__cause__)
        for private in (KEY, INPUT, INSTRUCTIONS, OUTPUT, RESPONSE_ID):
            self.assertNotIn(private, str(result.exception))
            self.assertNotIn(private, repr(result.exception))
        return result.exception

    def test_constructor_does_not_create_session_or_call_network(self):
        with patch("app.llm.requests.Session") as factory:
            model = OpenAIResponsesModel(KEY, "example-model")
            factory.assert_not_called()
            self.assertNotIn(KEY, repr(model))

    def test_fixed_post_with_store_false_tls_and_no_retry_redirect_proxy(self):
        result = self.generate(self.model(timeout_seconds=27.5, max_output_tokens=900))
        self.assertEqual(result, ModelReply(OUTPUT, "example-model-2026-10-01", 32, 12))
        self.assertEqual(len(self.session.calls), 1)
        url, options = self.session.calls[0]
        self.assertEqual(url, RESPONSES_URL)
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(options, {
            "headers": {"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
                        "Accept": "application/json"},
            "json": {"model": "example-model", "instructions": INSTRUCTIONS,
                     "input": [{"role": "user", "content": INPUT}],
                     "max_output_tokens": 900, "store": False},
            "timeout": (5, 27.5), "allow_redirects": False, "stream": True, "verify": True,
        })
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.adapters[0][0], "https://")
        self.assertEqual(self.session.adapters[0][1].max_retries.total, 0)
        self.assertTrue(self.session.closed)
        self.assertTrue(self.session.response.closed)
        self.assertNotIn(OUTPUT, repr(result))
        self.assertNotIn(RESPONSE_ID, repr(result))
        with self.assertRaises(FrozenInstanceError):
            result.text = "changed"

    def test_each_generation_gets_a_fresh_session(self):
        sessions = []
        def factory():
            session = FakeSession()
            sessions.append(session)
            return session
        model = OpenAIResponsesModel(KEY, "example-model", session_factory=factory)
        self.generate(model)
        self.generate(model)
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(session.closed and len(session.calls) == 1 for session in sessions))

    def test_reasoning_and_multiple_text_messages_preserve_order(self):
        payload = reply_payload()
        payload["output"].insert(0, {"type": "reasoning", "summary": []})
        payload["output"][1]["content"].append({"type": "output_text", "text": "Segunda parte."})
        payload["output"].append({"type": "message", "role": "assistant", "status": "completed",
                                  "content": [{"type": "output_text", "text": "Terceira parte."}]})
        result = self.generate(self.model(response=FakeResponse(payload=payload)))
        self.assertEqual(result.text, OUTPUT + "\nSegunda parte.\nTerceira parte.")

    def test_usage_missing_or_null_is_optional(self):
        for usage in (None, {}):
            with self.subTest(usage=usage):
                payload = reply_payload()
                payload["usage"] = usage
                result = self.generate(self.model(response=FakeResponse(payload=payload)))
                self.assertIsNone(result.input_tokens)
                self.assertIsNone(result.output_tokens)
        payload = reply_payload()
        del payload["usage"]
        result = self.generate(self.model(response=FakeResponse(payload=payload)))
        self.assertIsNone(result.input_tokens)

    def test_valid_unicode_and_newlines_are_supported_across_utf8_chunks(self):
        payload = reply_payload()
        payload["output"][0]["content"][0]["text"] = "Olá!\nSua agenda está pronta. 📅"
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        chunks = [raw[index:index + 1] for index in range(len(raw))]
        result = self.generate(self.model(response=FakeResponse(chunks=chunks)))
        self.assertEqual(result.text, "Olá!\nSua agenda está pronta. 📅")

    def test_credentials_and_rate_limit_use_only_safe_public_errors(self):
        for status, code in ((401, "llm_credentials_rejected"),
                             (403, "llm_credentials_rejected"), (429, "llm_rate_limited")):
            with self.subTest(status=status):
                response = FakeResponse(status=status, raw=KEY.encode())
                self.assert_safe_error(lambda: self.generate(self.model(response=response)), code, 503)
                self.assertEqual(len(self.session.calls), 1)
                self.assertTrue(response.closed)
                self.assertTrue(self.session.closed)

    def test_other_http_failures_do_not_parse_echo_follow_or_retry(self):
        for status in (201, 204, 301, 302, 307, 308, 400, 404, 408, 500, 502, 503):
            with self.subTest(status=status):
                response = FakeResponse(status=status, raw=KEY.encode())
                self.assert_safe_error(lambda: self.generate(self.model(response=response)),
                                       "llm_unavailable", 502)
                self.assertEqual(len(self.session.calls), 1)
                self.assertTrue(response.closed)

    def test_connection_and_stream_timeouts_do_not_leak_exceptions(self):
        for exception in (requests.ConnectTimeout(KEY), requests.ReadTimeout(INPUT), requests.Timeout(OUTPUT)):
            for in_stream in (False, True):
                with self.subTest(type=type(exception).__name__, in_stream=in_stream):
                    model = self.model(response=FakeResponse(error=exception)) if in_stream else self.model(error=exception)
                    error = self.assert_safe_error(lambda: self.generate(model), "llm_timeout", 504)
                    self.assertTrue(error.__suppress_context__)
                    self.assertEqual(len(self.session.calls), 1)
                    self.assertTrue(self.session.closed)

    def test_other_transport_errors_are_safe_and_single_attempt(self):
        for exception in (requests.ConnectionError(KEY), requests.exceptions.SSLError(KEY),
                          requests.TooManyRedirects(INPUT), RuntimeError(OUTPUT)):
            for in_stream in (False, True):
                with self.subTest(type=type(exception).__name__, in_stream=in_stream):
                    model = self.model(response=FakeResponse(error=exception)) if in_stream else self.model(error=exception)
                    error = self.assert_safe_error(lambda: self.generate(model), "llm_unavailable", 502)
                    self.assertTrue(error.__suppress_context__)
                    self.assertEqual(len(self.session.calls), 1)
                    self.assertTrue(self.session.closed)

    def test_oversized_body_stops_before_consuming_remaining_chunks(self):
        consumed = []
        def chunks():
            consumed.append(1)
            yield b"x" * MAX_RESPONSE_BYTES
            consumed.append(2)
            yield b"x"
            consumed.append(3)
            raise AssertionError("Must not consume unbounded provider stream.")
        response = FakeResponse(chunks=chunks())
        self.assert_safe_error(lambda: self.generate(self.model(response=response)), "llm_invalid_response", 502)
        self.assertEqual(consumed, [1, 2])
        self.assertTrue(response.closed)

    def test_exact_byte_limit_is_accepted_with_valid_json(self):
        raw = json.dumps(reply_payload()).encode()
        raw += b" " * (MAX_RESPONSE_BYTES - len(raw))
        self.assertEqual(len(raw), MAX_RESPONSE_BYTES)
        result = self.generate(self.model(response=FakeResponse(raw=raw)))
        self.assertEqual(result.text, OUTPUT)

    def test_invalid_json_encoding_duplicates_and_constants_are_rejected(self):
        valid = json.dumps(reply_payload()).encode()
        bad_bodies = [b"", b"not-json", b"[]", b"null", b'"string"', b"\xff", b"\xef\xbb\xbf" + valid,
                      b'{"status":"completed","status":"completed"}',
                      valid.replace(b'"annotations": []', b'"annotations": [], "annotations": []'),
                      valid.replace(b'"input_tokens": 32', b'"input_tokens": NaN'),
                      valid.replace(b'"input_tokens": 32', b'"input_tokens": Infinity'),
                      valid + b"{}"]
        for index, raw in enumerate(bad_bodies):
            with self.subTest(index=index):
                self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(raw=raw))),
                                       "llm_invalid_response", 502)

    def test_incomplete_failed_refusal_and_tool_outputs_are_rejected(self):
        invalid_outputs = [
            [], None, {}, [None], [{"type": "reasoning", "summary": []}],
            [{"type": "function_call", "name": "delete_event", "arguments": INPUT}],
            [{"type": "web_search_call", "status": "completed"}],
        ]
        for index, output in enumerate(invalid_outputs):
            with self.subTest(index=index):
                payload = reply_payload()
                payload["output"] = output
                self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                                       "llm_invalid_response", 502)
        for field, values in {
            "status": [None, "incomplete", "failed", "queued", "in_progress"],
            "error": [{}, {"message": KEY}], "incomplete_details": [{}, {"reason": "max_output_tokens"}],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = reply_payload()
                    payload[field] = value
                    self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                                           "llm_invalid_response", 502)

    def test_mixed_text_and_tools_is_rejected(self):
        payload = reply_payload()
        payload["output"].append({"type": "function_call", "name": "delete_event", "arguments": INPUT})
        self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                               "llm_invalid_response", 502)

    def test_message_role_state_content_and_refusal_are_checked(self):
        changes = [("role", "user"), ("role", None), ("status", "incomplete"), ("status", None),
                   ("type", "function_call"), ("content", []), ("content", None),
                   ("content", [{"type": "refusal", "refusal": OUTPUT}]),
                   ("content", [{"type": "output_text", "text": OUTPUT},
                                {"type": "refusal", "refusal": OUTPUT}])]
        for index, (field, value) in enumerate(changes):
            with self.subTest(index=index):
                payload = reply_payload()
                payload["output"][0][field] = value
                self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                                       "llm_invalid_response", 502)

    def test_bad_text_or_control_characters_are_rejected(self):
        for index, text in enumerate((None, 42, "", " \n\t", "bad\x00text", "bad\x7ftext", "bad\ud800text")):
            with self.subTest(index=index):
                payload = reply_payload()
                payload["output"][0]["content"][0]["text"] = text
                self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                                       "llm_invalid_response", 502)

    def test_text_limit_counts_all_parts_and_separators(self):
        payload = reply_payload()
        payload["output"][0]["content"][0]["text"] = "x" * MAX_REPLY_CHARACTERS
        self.assertEqual(len(self.generate(self.model(response=FakeResponse(payload=payload))).text), MAX_REPLY_CHARACTERS)
        payload["output"][0]["content"].append({"type": "output_text", "text": "x"})
        self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                               "llm_invalid_response", 502)

    def test_reported_model_is_validated_without_exposing_unsafe_id(self):
        for index, model in enumerate((None, "", "https://example.invalid", "model/alias", "model name", "mádel",
                                       "x" * 101, "model\n", KEY + "\n")):
            with self.subTest(index=index):
                payload = reply_payload()
                payload["model"] = model
                self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                                       "llm_invalid_response", 502)

    def test_usage_rejects_wrong_shapes_negative_float_and_bool(self):
        for usage in ([], "unsafe", True):
            with self.subTest(usage=usage):
                payload = reply_payload()
                payload["usage"] = usage
                self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                                       "llm_invalid_response", 502)
        for field in ("input_tokens", "output_tokens"):
            for value in (-1, 1.5, True, "32", {}):
                with self.subTest(field=field, value=value):
                    payload = reply_payload()
                    payload["usage"][field] = value
                    self.assert_safe_error(lambda: self.generate(self.model(response=FakeResponse(payload=payload))),
                                           "llm_invalid_response", 502)
        payload = reply_payload()
        payload["usage"] = {"input_tokens": 0, "output_tokens": 0}
        self.assertEqual(self.generate(self.model(response=FakeResponse(payload=payload))).input_tokens, 0)

    def test_invalid_keys_and_model_names_fail_before_session(self):
        for field, values in {
            "api_key": [None, "", KEY + "\n", " " + KEY, KEY + " ", "á", "\x00", "x" * 513],
            "model": [None, "", "https://example.invalid", "x/y", "x y", "mádel", "x" * 101, "x\n"],
        }.items():
            for index, value in enumerate(values):
                with self.subTest(field=field, index=index):
                    options = {"api_key": KEY, "model": "example-model"}
                    options[field] = value
                    with patch("app.llm.requests.Session") as factory:
                        self.assert_safe_error(lambda: OpenAIResponsesModel(**options), "llm_unavailable", 502)
                        factory.assert_not_called()

    def test_keys_do_not_require_vendor_prefix(self):
        for key in (KEY, "a", "sk-example.1_-", "!visible_ascii~"):
            with self.subTest(length=len(key)):
                OpenAIResponsesModel(key, "example-model")

    def test_configuration_validates_timeouts_and_token_budget(self):
        for field, values in {
            "timeout_seconds": [None, 0, -1, True, "30", float("nan"), float("inf"), float("-inf")],
            "max_output_tokens": [None, 0, -1, True, "600", 1.5],
            "session_factory": [42, "factory"],
        }.items():
            for index, value in enumerate(values):
                with self.subTest(field=field, index=index):
                    self.assert_safe_error(lambda: OpenAIResponsesModel(KEY, "example-model", **{field: value}),
                                           "llm_unavailable", 502)

    def test_invalid_prompts_are_blocked_before_network(self):
        for field in ("instructions", "message"):
            for index, value in enumerate((None, "", "  \n", "bad\x00text", "bad\ud800text")):
                with self.subTest(field=field, index=index):
                    options = {"instructions": INSTRUCTIONS, "message": INPUT}
                    options[field] = value
                    model = self.model()
                    self.assert_safe_error(lambda: model.generate(**options), "llm_unavailable", 502)
                    self.assertEqual(self.session.calls, [])

    def test_unknown_error_code_cannot_introduce_provider_text_or_status(self):
        for code in (KEY, None, [], 42):
            error = ModelError(code=code, http_status=KEY)
            self.assertEqual(error.code, "llm_unavailable")
            self.assertEqual(error.http_status, 502)
            self.assertNotIn(KEY, str(error))
            self.assertNotIn(KEY, repr(error))


if __name__ == "__main__":
    unittest.main()
