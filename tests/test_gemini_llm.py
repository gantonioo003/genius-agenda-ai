"""Contrato Gemini com HTTP simulado; nenhum token, rede ou serviço real."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import requests

from app.agent_query_contract import QUERY_TOOLS, QueryPlan
from app.dialogue_contract import DIALOGUE_TOOLS, DialoguePlan
from app.gemini_llm import GENERATE_CONTENT_BASE_URL, GeminiModel
from app.llm import (
    MAX_DIALOGUE_ARGUMENT_BYTES, MAX_QUERY_ARGUMENT_BYTES, MAX_REPLY_CHARACTERS,
    MAX_RESPONSE_BYTES, ModelError, ModelReply,
)


KEY = "PRIVATE_GEMINI_FIXTURE_KEY"
MESSAGE = "PRIVATE_GEMINI_FIXTURE_MESSAGE"
INSTRUCTIONS = "PRIVATE_GEMINI_FIXTURE_INSTRUCTIONS"
OUTPUT = "PRIVATE_GEMINI_FIXTURE_OUTPUT"
MODEL = "gemini-3.8-flash"


def envelope(parts=None):
    return {
        "candidates": [{"index": 0, "finishReason": "STOP", "content": {
            "role": "model", "parts": [{"text": OUTPUT}] if parts is None else parts,
        }}],
        "modelVersion": MODEL,
        "usageMetadata": {"promptTokenCount": 23, "candidatesTokenCount": 11,
                          "thoughtsTokenCount": 6, "totalTokenCount": 40},
    }


def function_payload(name="list_events", arguments=None):
    if arguments is None:
        arguments = {"date": "2030-06-17", "period": "day", "time_zone": "America/Recife"}
    return envelope([{"functionCall": {"name": name, "args": arguments},
                      "thoughtSignature": "fixture-signature"}])


def draft_arguments(**values):
    return {
        "mode": "start", "action": "create", "time_zone": "America/Recife",
        "title": "Entrevista", "start": "2030-06-17T15:00:00-03:00",
        "end": "2030-06-17T15:30:00-03:00", "description": None, "location": None,
        "event_id": None, "search_title": None, "date": None, "period": None,
        "ambiguous_field": None, "use_selected_event": False, **values,
    }


class Response:
    def __init__(self, payload=None, *, raw=None, chunks=None, status=200, error=None):
        self.status_code = status
        self.closed = False
        self.error = error
        if raw is None:
            raw = json.dumps(envelope() if payload is None else payload).encode("utf-8")
        self.chunks = [raw] if chunks is None else chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, *, chunk_size):
        if self.error is not None:
            raise self.error
        yield from self.chunks


class Session:
    def __init__(self, response=None, *, error=None):
        self.response = Response() if response is None else response
        self.error = error
        self.calls = []
        self.adapters = []
        self.trust_env = True
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def mount(self, prefix, adapter):
        self.adapters.append((prefix, adapter))

    def post(self, url, **options):
        self.calls.append((url, options))
        if self.error is not None:
            raise self.error
        return self.response


class GeminiModelTests(unittest.TestCase):
    def model(self, response=None, *, error=None, **options):
        self.session = Session(response, error=error)
        return GeminiModel(KEY, MODEL, session_factory=lambda: self.session, **options)

    def invoke(self, model, method="generate"):
        return getattr(model, method)(instructions=INSTRUCTIONS, message=MESSAGE)

    def assert_error(self, action, code="llm_invalid_response", status=502):
        with self.assertRaises(ModelError) as caught:
            action()
        error = caught.exception
        self.assertEqual((error.code, error.http_status), (code, status))
        self.assertIsNone(error.__cause__)
        for private in (KEY, MESSAGE, INSTRUCTIONS, OUTPUT):
            self.assertNotIn(private, str(error))
            self.assertNotIn(private, repr(error))
        return error

    def test_constructor_has_no_network_or_session_side_effect(self):
        with patch("app.gemini_llm.requests.Session", side_effect=AssertionError("network")):
            GeminiModel(KEY, MODEL)
        calls = []
        model = GeminiModel(KEY, MODEL, session_factory=lambda: calls.append(True))
        self.assertEqual(calls, [])
        self.assertNotIn(KEY, repr(model))

    def test_constructor_rejects_invalid_endpoint_and_transport_configuration(self):
        cases = [
            {"api_key": None}, {"api_key": ""}, {"api_key": "line\nbreak"},
            {"api_key": " x"}, {"api_key": "x" * 513}, {"model": ""},
            {"model": "../gemini"}, {"model": "models/" + MODEL},
            {"model": MODEL + "?key=" + KEY}, {"model": "https://example.invalid"},
            {"model": MODEL + "\r\n"}, {"model": None},
            {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": -1},
            {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
            {"timeout_seconds": "30"}, {"max_output_tokens": True},
            {"max_output_tokens": 0}, {"max_output_tokens": -1},
            {"max_output_tokens": 127}, {"max_output_tokens": 4097},
            {"max_output_tokens": 1.5}, {"session_factory": 1},
        ]
        for values in cases:
            with self.subTest(values=values):
                options = {"api_key": KEY, "model": MODEL, **values}
                self.assert_error(lambda: GeminiModel(**options), "llm_unavailable")

    def test_output_budget_accepts_both_configured_boundaries(self):
        for limit in (128, 4096):
            model = self.model(max_output_tokens=limit)
            self.invoke(model)
            self.assertEqual(self.session.calls[0][1]["json"]["generationConfig"]["maxOutputTokens"], limit)

    def test_text_uses_fixed_https_header_key_and_separate_system_instruction(self):
        model = self.model(timeout_seconds=17, max_output_tokens=900)
        reply = self.invoke(model)
        self.assertIsInstance(reply, ModelReply)
        self.assertEqual((reply.text, reply.model, reply.input_tokens, reply.output_tokens),
                         (OUTPUT, MODEL, 23, 11))
        self.assertNotIn(OUTPUT, repr(reply))
        self.assertEqual(len(self.session.calls), 1)
        url, options = self.session.calls[0]
        self.assertEqual(url, GENERATE_CONTENT_BASE_URL + MODEL + ":generateContent")
        self.assertNotIn(KEY, url)
        self.assertEqual(options["headers"]["x-goog-api-key"], KEY)
        self.assertEqual(options["timeout"], (5, 17))
        self.assertEqual((options["allow_redirects"], options["stream"], options["verify"]),
                         (False, True, True))
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.adapters[0][0], "https://")
        self.assertEqual(self.session.adapters[0][1].max_retries.total, 0)
        self.assertEqual(options["json"], {
            "systemInstruction": {"parts": [{"text": INSTRUCTIONS}]},
            "contents": [{"role": "user", "parts": [{"text": MESSAGE}]}],
            "generationConfig": {"candidateCount": 1, "maxOutputTokens": 900,
                                 "thinkingConfig": {"thinkingLevel": "LOW"}},
        })
        self.assertNotIn(KEY, json.dumps(options["json"]))
        self.assertTrue(self.session.closed and self.session.response.closed)

    def test_each_turn_opens_fresh_session_without_remote_history(self):
        sessions = []
        def factory():
            session = Session()
            sessions.append(session)
            return session
        model = GeminiModel(KEY, MODEL, session_factory=factory)
        self.invoke(model)
        self.invoke(model)
        self.assertEqual(len(sessions), 2)
        for session in sessions:
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(len(session.calls[0][1]["json"]["contents"]), 1)
            self.assertTrue(session.closed)

    def test_other_models_do_not_receive_unsupported_thinking_level(self):
        session = Session()
        model = GeminiModel(KEY, "gemini-2.5-flash", session_factory=lambda: session)
        self.invoke(model)
        self.assertNotIn("thinkingConfig", session.calls[0][1]["json"]["generationConfig"])

    def test_plans_preserve_server_schemas_and_only_offer_allowlisted_tools(self):
        for method, tools, payload, result_type in (
            ("plan_query", QUERY_TOOLS, function_payload(), QueryPlan),
            ("plan_dialogue", DIALOGUE_TOOLS,
             function_payload("prepare_request", draft_arguments()), DialoguePlan),
        ):
            with self.subTest(method=method):
                snapshot = deepcopy(tools)
                model = self.model(Response(payload))
                plan = self.invoke(model, method)
                self.assertIsInstance(plan, result_type)
                self.assertEqual(plan.arguments, payload["candidates"][0]["content"]["parts"][0]["functionCall"]["args"])
                self.assertEqual((plan.input_tokens, plan.output_tokens), (23, 11))
                body = self.session.calls[0][1]["json"]
                declarations = body["tools"][0]["functionDeclarations"]
                self.assertEqual(declarations, [
                    {"name": tool["name"], "description": tool["description"],
                     "parametersJsonSchema": tool["parameters"]} for tool in tools
                ])
                self.assertEqual(body["toolConfig"], {"functionCallingConfig": {
                    "mode": "ANY", "allowedFunctionNames": [tool["name"] for tool in tools],
                }})
                self.assertTrue(all(set(tool) == {"name", "description", "parametersJsonSchema"}
                                    for tool in declarations))
                self.assertEqual(tools, snapshot)
                self.assertNotIn("Entrevista", repr(plan))

    def test_all_query_tools_and_periods_remain_compatible(self):
        examples = [("list_events", {"date": "2030-06-17", "period": period,
                                     "time_zone": "America/Recife"}) for period in ("day", "week", "month")]
        examples += [("get_event", {"event_id": "event-123", "time_zone": "America/Recife"}),
                     ("get_context_event", {"time_zone": "America/Recife"}),
                     ("ask_query", {"reason": "missing_information"})]
        for name, arguments in examples:
            with self.subTest(name=name, arguments=arguments):
                result = self.invoke(self.model(Response(function_payload(name, arguments))), "plan_query")
                self.assertEqual((result.name, result.arguments), (name, arguments))

    def test_dialogue_nullable_fields_empty_metadata_and_cancellation(self):
        cases = [("prepare_request", draft_arguments(action=None, title="", description="a\nb", location="")),
                 ("select_candidate", {"choice": 1}), ("select_candidate", {"choice": 20}),
                 ("ask_dialogue", {"reason": "ambiguous_information", "field": "date"}),
                 ("cancel_request", {})]
        for name, arguments in cases:
            with self.subTest(name=name):
                result = self.invoke(self.model(Response(function_payload(name, arguments))), "plan_dialogue")
                self.assertEqual((result.name, result.arguments), (name, arguments))

    def test_unknown_tools_missing_extra_or_invalid_query_arguments_are_rejected(self):
        cases = [("delete_event", {"event_id": "123"}), ("list_events", {}),
                 ("list_events", {"date": "2030-06-17", "period": "year", "time_zone": "America/Recife"}),
                 ("get_event", {"event_id": None, "time_zone": "America/Recife"}),
                 ("get_event", {"event_id": " ", "time_zone": "America/Recife"}),
                 ("get_event", {"event_id": "id", "time_zone": "America/Recife", "confirmed": True}),
                 ("get_event", {"event_id": "id\x00", "time_zone": "America/Recife"}),
                 ("get_context_event", {"time_zone": True}), ("ask_query", {"reason": "made_up"})]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                self.assert_error(lambda: self.invoke(self.model(Response(function_payload(name, arguments))), "plan_query"))

    def test_dialogue_reuses_strict_local_types_and_enums(self):
        cases = [("select_candidate", {"choice": value}) for value in (0, 21, True, 1.0, "1", None)]
        cases += [("prepare_request", draft_arguments(**values)) for values in (
            {"action": "erase"}, {"mode": "continue_all"}, {"use_selected_event": "yes"},
            {"time_zone": "America/Recife\n"}, {"title": " ", "start": None},
            {"ambiguous_field": "calendar"}, {"description": {"text": "x"}},
        )]
        cases += [("cancel_request", {"event_id": "123"}),
                  ("ask_dialogue", {"reason": "sure", "field": "event"})]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                self.assert_error(lambda: self.invoke(self.model(Response(function_payload(name, arguments))), "plan_dialogue"))

    def test_incomplete_prepare_request_is_not_filled_by_adapter(self):
        arguments = draft_arguments()
        del arguments["location"]
        self.assert_error(lambda: self.invoke(self.model(Response(function_payload("prepare_request", arguments))), "plan_dialogue"))

    def test_multiple_calls_and_text_beside_call_fail_closed(self):
        part = function_payload()["candidates"][0]["content"]["parts"][0]
        for parts in ([part, deepcopy(part)], [part, {"text": OUTPUT}], [{"text": OUTPUT}],
                      [{"functionCall": {"name": "list_events", "args": "{}"}}],
                      [{"functionCall": {"name": "list_events", "args": {}, "extra": True}}],
                      [{"functionCall": {"name": "cancel_request"}}]):
            with self.subTest(parts=parts):
                self.assert_error(lambda: self.invoke(self.model(Response(envelope(parts))), "plan_query"))

    def test_text_rejects_tools_and_multimodal_parts(self):
        for parts in ([{"functionCall": {"name": "cancel_request", "args": {}}}],
                      [{"inlineData": {"mimeType": "image/png", "data": "AAAA"}}],
                      [{"text": OUTPUT, "functionCall": {"name": "cancel_request", "args": {}}}],
                      [{"text": ""}], [{"text": " "}], [{"text": "a\x00b"}]):
            with self.subTest(parts=parts):
                self.assert_error(lambda: self.invoke(self.model(Response(envelope(parts)))))

    def test_thought_summary_is_excluded_and_signature_is_not_executed(self):
        for method, visible in (("generate", {"text": OUTPUT}),
                                ("plan_query", {"functionCall": {"name": "ask_query", "args": {"reason": "missing_information"}}})):
            parts = [{"thought": True, "text": "PRIVATE_THOUGHT"},
                     {"thought": True, "thoughtSignature": "opaque-fixture"}, visible]
            result = self.invoke(self.model(Response(envelope(parts))), method)
            self.assertNotIn("PRIVATE_THOUGHT", repr(result))
            if method == "generate":
                self.assertEqual(result.text, OUTPUT)
        for parts in ([{"thought": True, "text": "only thought"}],
                      [{"thought": True, "functionCall": {"name": "ask_query", "args": {}}}, {"text": OUTPUT}],
                      [{"thought": "false", "text": OUTPUT}],
                      [{"thoughtSignature": None, "text": OUTPUT}]):
            self.assert_error(lambda: self.invoke(self.model(Response(envelope(parts)))))

    def test_reply_fragments_are_joined_and_optional_metadata_can_be_absent(self):
        payload = envelope([{"text": "Olá "}, {"text": "mundo"}])
        del payload["modelVersion"]
        del payload["usageMetadata"]
        reply = self.invoke(self.model(Response(payload)))
        self.assertEqual((reply.text, reply.model, reply.input_tokens, reply.output_tokens),
                         ("Olá mundo", MODEL, None, None))

    def test_truncated_safety_and_unfinished_responses_are_not_accepted(self):
        for reason in (None, "", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "CONTINUATION",
                       "MALFORMED_FUNCTION_CALL", "MALFORMED_RESPONSE", "UNEXPECTED_TOOL_CALL"):
            with self.subTest(reason=reason):
                payload = envelope()
                payload["candidates"][0]["finishReason"] = reason
                self.assert_error(lambda: self.invoke(self.model(Response(payload))))

    def test_candidate_envelope_and_safety_metadata_are_validated(self):
        mutations = [lambda p: p.update(candidates=[]),
                     lambda p: p["candidates"].append(deepcopy(p["candidates"][0])),
                     lambda p: p.update(candidates=None), lambda p: p.update(error={"message": KEY}),
                     lambda p: p.update(promptFeedback={"blockReason": "SAFETY"}),
                     lambda p: p.update(promptFeedback=None),
                     lambda p: p["candidates"][0].update(continuationToken="token"),
                     lambda p: p["candidates"][0].update(index=True),
                     lambda p: p["candidates"][0].update(index=1),
                     lambda p: p["candidates"][0].update(safetyRatings=[{"blocked": True}]),
                     lambda p: p["candidates"][0].update(safetyRatings=[{"blocked": "false"}]),
                     lambda p: p["candidates"][0].update(safetyRatings={}),
                     lambda p: p["candidates"][0]["content"].update(role="user"),
                     lambda p: p["candidates"][0]["content"].update(parts=[]),
                     lambda p: p["candidates"][0]["content"].update(parts=[None]),
                     lambda p: p.update(modelVersion="../secret"),
                     lambda p: p.update(usageMetadata=[])]
        for change in mutations:
            payload = envelope()
            change(payload)
            self.assert_error(lambda: self.invoke(self.model(Response(payload))))

    def test_token_metadata_is_strict_integer_and_never_boolean(self):
        for field in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount"):
            for value in (True, -1, 1.1, "1"):
                with self.subTest(field=field, value=value):
                    payload = envelope()
                    payload["usageMetadata"][field] = value
                    self.assert_error(lambda: self.invoke(self.model(Response(payload))))

    def test_argument_and_reply_limits_apply_in_bytes_and_characters(self):
        query = function_payload("get_event", {"event_id": "é" * MAX_QUERY_ARGUMENT_BYTES,
                                                "time_zone": "America/Recife"})
        dialogue = function_payload("prepare_request", draft_arguments(description="é" * MAX_DIALOGUE_ARGUMENT_BYTES))
        for method, payload in (("plan_query", query), ("plan_dialogue", dialogue),
                                ("generate", envelope([{"text": "x" * (MAX_REPLY_CHARACTERS + 1)}]))):
            self.assert_error(lambda: self.invoke(self.model(Response(payload)), method))
        valid = self.invoke(self.model(Response(envelope([{"text": "é" * MAX_REPLY_CHARACTERS}]))))
        self.assertEqual(len(valid.text), MAX_REPLY_CHARACTERS)

    def test_json_duplicates_non_json_and_invalid_utf8_are_rejected(self):
        valid = json.dumps(function_payload())
        raw_cases = [b"not json", b"\xff", b"[]", b"null",
                     valid.replace('"name": "list_events"', '"name":"list_events","name":"get_event"').encode(),
                     valid.replace('"period": "day"', '"period":"day","period":"week"').encode(),
                     valid.replace('"promptTokenCount": 23', '"promptTokenCount": NaN').encode(),
                     valid.replace('"date": "2030-06-17"', '"date": "\\ud800"').encode()]
        for raw in raw_cases:
            self.assert_error(lambda: self.invoke(self.model(Response(raw=raw)), "plan_query"))

    def test_stream_limit_and_invalid_chunks_are_rejected_and_resources_close(self):
        for chunks in ([b"x" * MAX_RESPONSE_BYTES, b"x"], ["not bytes"], [None]):
            response = Response(chunks=chunks)
            self.assert_error(lambda: self.invoke(self.model(response)))
            self.assertTrue(self.session.closed and response.closed)

    def test_http_error_mapping_is_sanitized_and_never_retried_or_redirected(self):
        for status, code, public_status in ((401, "llm_credentials_rejected", 503),
                                           (403, "llm_credentials_rejected", 503),
                                           (429, "llm_rate_limited", 503),
                                           (301, "llm_unavailable", 502),
                                           (400, "llm_unavailable", 502),
                                           (404, "llm_unavailable", 502),
                                           (503, "llm_unavailable", 502)):
            with self.subTest(status=status):
                response = Response(status=status, error=AssertionError("error body must not be read"))
                self.assert_error(lambda: self.invoke(self.model(response)), code, public_status)
                self.assertEqual(len(self.session.calls), 1)
                self.assertFalse(self.session.calls[0][1]["allow_redirects"])
                self.assertTrue(self.session.closed and response.closed)

    def test_transport_exceptions_do_not_leak_credentials_or_repeat(self):
        for exception, code, status in ((requests.Timeout(KEY), "llm_timeout", 504),
                                        (requests.ConnectionError(KEY), "llm_unavailable", 502),
                                        (requests.exceptions.SSLError(KEY), "llm_unavailable", 502),
                                        (RuntimeError(KEY), "llm_unavailable", 502)):
            self.assert_error(lambda: self.invoke(self.model(error=exception)), code, status)
            self.assertEqual(len(self.session.calls), 1)
            self.assertTrue(self.session.closed)
        self.assert_error(lambda: self.invoke(self.model(Response(error=requests.ReadTimeout(KEY)))),
                          "llm_timeout", 504)

    def test_invalid_input_never_opens_session(self):
        for method in ("generate", "plan_query", "plan_dialogue"):
            for field in ("instructions", "message"):
                for value in (None, "", " ", "a\x00b", "\ud800"):
                    with self.subTest(method=method, field=field, value=repr(value)):
                        model = self.model()
                        args = {"instructions": INSTRUCTIONS, "message": MESSAGE, field: value}
                        self.assert_error(lambda: getattr(model, method)(**args), "llm_unavailable")
                        self.assertEqual(self.session.calls, [])


if __name__ == "__main__":
    unittest.main()
