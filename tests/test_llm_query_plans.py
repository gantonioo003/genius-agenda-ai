"""Interpretação de consultas com fixtures REST; sem credencial, agenda ou rede."""

from dataclasses import FrozenInstanceError
import json
import unittest
from unittest.mock import patch

import requests

from app.agent_query_contract import QUERY_TOOLS, QueryPlan
from app.llm import MAX_QUERY_ARGUMENT_BYTES, MAX_RESPONSE_BYTES, ModelError, OpenAIResponsesModel


KEY = "PRIVATE_FIXTURE_QUERY_KEY"
MESSAGE = "PRIVATE_FIXTURE_QUERY_MESSAGE"
INSTRUCTIONS = "PRIVATE_FIXTURE_QUERY_INSTRUCTIONS"
EVENT_ID = "PRIVATE_FIXTURE_EVENT_ID"
CALL_ID = "call_PRIVATE_FIXTURE_CALL_ID"


def query_payload(name="list_events", arguments=None):
    if arguments is None:
        arguments = {"date": "2026-10-24", "period": "day", "time_zone": "America/Recife"}
    return {
        "id": "resp_PRIVATE_FIXTURE", "status": "completed", "model": "example-model-2026-10-01",
        "error": None, "incomplete_details": None,
        "output": [{"type": "function_call", "status": "completed", "call_id": CALL_ID,
                    "name": name, "arguments": json.dumps(arguments)}],
        "usage": {"input_tokens": 40, "output_tokens": 20},
    }


class FakeResponse:
    def __init__(self, payload=None, *, raw=None, status=200, chunks=None, error=None):
        self.status_code = status
        self.raw = json.dumps(query_payload() if payload is None else payload).encode() if raw is None else raw
        self.chunks = [self.raw] if chunks is None else chunks
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
    def __init__(self, response, error=None):
        self.response = response
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
        if self.error:
            raise self.error
        return self.response


class QueryPlanTests(unittest.TestCase):
    def model(self, payload=None, *, response=None, error=None):
        self.session = FakeSession(FakeResponse(payload) if response is None else response, error=error)
        return OpenAIResponsesModel(KEY, "example-model", session_factory=lambda: self.session)

    def plan(self, model):
        return model.plan_query(instructions=INSTRUCTIONS, message=MESSAGE)

    def assert_invalid(self, payload=None, *, raw=None):
        response = FakeResponse(payload, raw=raw)
        model = self.model(response=response)
        with self.assertRaises(ModelError) as raised:
            self.plan(model)
        self.assertEqual(raised.exception.code, "llm_invalid_response")
        self.assertEqual(raised.exception.http_status, 502)
        self.assertIsNone(raised.exception.__cause__)
        for value in (KEY, MESSAGE, INSTRUCTIONS, EVENT_ID, CALL_ID):
            self.assertNotIn(value, str(raised.exception))
            self.assertNotIn(value, repr(raised.exception))
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.closed)
        self.assertTrue(response.closed)

    def test_contract_is_frozen_and_hides_private_arguments(self):
        plan = QueryPlan("get_event", {"event_id": EVENT_ID, "time_zone": "America/Recife"}, "example-model")
        self.assertNotIn(EVENT_ID, repr(plan))
        self.assertIsNone(plan.input_tokens)
        with self.assertRaises(FrozenInstanceError):
            plan.name = "delete_event"

    def test_schemas_are_strict_read_only_and_require_every_property(self):
        self.assertEqual([tool["name"] for tool in QUERY_TOOLS], ["list_events", "get_event", "ask_query", "get_context_event"])
        for tool in QUERY_TOOLS:
            self.assertEqual(tool["type"], "function")
            self.assertIs(tool["strict"], True)
            parameters = tool["parameters"]
            self.assertEqual(parameters["type"], "object")
            self.assertIs(parameters["additionalProperties"], False)
            self.assertEqual(set(parameters["required"]), set(parameters["properties"]))
            self.assertTrue(all(item["type"] == "string" for item in parameters["properties"].values()))
        self.assertEqual(QUERY_TOOLS[0]["parameters"]["properties"]["period"]["enum"], ["day", "week", "month"])
        self.assertEqual(QUERY_TOOLS[2]["parameters"]["properties"]["reason"]["enum"],
                         ["missing_information", "unsupported_request"])

    def test_single_request_uses_required_tools_no_remote_state_or_event_data(self):
        result = self.plan(self.model())
        self.assertEqual(result, QueryPlan("list_events", {"date": "2026-10-24", "period": "day",
                                                         "time_zone": "America/Recife"},
                                           "example-model-2026-10-01", 40, 20))
        self.assertEqual(len(self.session.calls), 1)
        url, options = self.session.calls[0]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(options["json"], {
            "model": "example-model", "instructions": INSTRUCTIONS,
            "input": [{"role": "user", "content": MESSAGE}],
            "max_output_tokens": 600, "store": False, "tools": QUERY_TOOLS,
            "tool_choice": "required", "parallel_tool_calls": False,
        })
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.adapters[0][1].max_retries.total, 0)
        self.assertEqual(options["timeout"], (5, 30.0))
        self.assertFalse(options["allow_redirects"])
        self.assertTrue(options["verify"])
        self.assertTrue(options["stream"])
        self.assertNotIn("previous_response_id", options["json"])
        self.assertNotIn("conversation", options["json"])

    def test_all_allowed_tools_and_periods_parse(self):
        cases = [("list_events", {"date": "2026-10-24", "period": period, "time_zone": "UTC"})
                 for period in ("day", "week", "month")]
        cases += [("get_event", {"event_id": EVENT_ID, "time_zone": "America/Recife"}),
                  ("ask_query", {"reason": "missing_information"}),
                  ("ask_query", {"reason": "unsupported_request"}),
                  ("get_context_event", {"time_zone": "UTC"})]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                plan = self.plan(self.model(query_payload(name, arguments)))
                self.assertEqual(plan.name, name)
                self.assertEqual(plan.arguments, arguments)

    def test_reasoning_is_ignored_and_function_status_may_be_absent(self):
        payload = query_payload()
        del payload["output"][0]["status"]
        payload["output"].insert(0, {"type": "reasoning", "summary": []})
        payload["output"].append({"type": "reasoning", "summary": []})
        self.assertEqual(self.plan(self.model(payload)).name, "list_events")

    def test_one_function_only_no_messages_unknown_tools_or_mutations(self):
        valid = query_payload()["output"][0]
        outputs = [[], None, {}, [None], [{"type": "reasoning"}],
                   [valid, valid], [valid, {"type": "message", "content": []}],
                   [{"type": "message", "role": "assistant", "status": "completed", "content": []}],
                   [{"type": "refusal", "refusal": MESSAGE}], [{"type": "web_search_call"}]]
        for index, output in enumerate(outputs):
            with self.subTest(index=index):
                payload = query_payload()
                payload["output"] = output
                self.assert_invalid(payload)
        for name in ("create_event", "delete_event", "reschedule_event", "unknown", None, True, []):
            with self.subTest(name=name):
                payload = query_payload()
                payload["output"][0]["name"] = name
                self.assert_invalid(payload)

    def test_call_status_and_identifier_are_checked(self):
        for field, values in {"status": [None, "in_progress", "incomplete", "failed", True],
                              "call_id": [None, "", "  ", True, [], "bad\x00", "bad\ud800"]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = query_payload()
                    payload["output"][0][field] = value
                    self.assert_invalid(payload)
        payload = query_payload()
        del payload["output"][0]["call_id"]
        self.assert_invalid(payload)

    def test_incomplete_error_and_bad_outer_shapes_are_rejected(self):
        for field, values in {"status": [None, "incomplete", "failed", "queued", "in_progress"],
                              "error": [{}, {"message": MESSAGE}],
                              "incomplete_details": [{}, {"reason": "max_output_tokens"}]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = query_payload()
                    payload[field] = value
                    self.assert_invalid(payload)
        for raw in (b"null", b"[]", b'"text"', b"{}", b"", b"not-json", b"\xff"):
            with self.subTest(raw=raw):
                self.assert_invalid(raw=raw)

    def test_arguments_must_be_an_object_with_exact_keys_and_strings(self):
        cases = [None, [], True, 12, "text", {}, {"date": "2026-10-24", "period": "day"},
                 {"date": "2026-10-24", "period": "day", "time_zone": "UTC", "calendar_id": "primary"}]
        for key in ("date", "period", "time_zone"):
            for value in (None, True, 0, [], {}, "", "  ", "bad\x00", "bad\ud800"):
                arguments = {"date": "2026-10-24", "period": "day", "time_zone": "UTC"}
                arguments[key] = value
                cases.append(arguments)
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assert_invalid(query_payload(arguments=arguments) if arguments is not None else
                                    query_payload(arguments={"unexpected": None}))
        for name, arguments in [("list_events", {"date": "2026-10-24", "period": "year", "time_zone": "UTC"}),
                                ("get_event", {"event_id": EVENT_ID}),
                                ("get_event", {"event_id": EVENT_ID, "time_zone": "UTC", "confirm": True}),
                                ("ask_query", {"reason": "other"}), ("ask_query", {"question": MESSAGE})]:
            with self.subTest(name=name, arguments=arguments):
                self.assert_invalid(query_payload(name, arguments))

    def test_raw_arguments_reject_duplicates_constants_invalid_encoding_and_trailing_data(self):
        cases = [None, {}, [], True, "", "null", "[]", "true", '"text"',
                 '{"reason":"missing_information","reason":"missing_information"}',
                 '{"reason":NaN}', '{"reason":Infinity}', '{"reason":-Infinity}',
                 '{"reason":"missing_information"}{}', '\ufeff{"reason":"missing_information"}',
                 '{"reason":"\ud800"}']
        for arguments in cases:
            with self.subTest(arguments=arguments):
                payload = query_payload("ask_query", {"reason": "missing_information"})
                payload["output"][0]["arguments"] = arguments
                self.assert_invalid(payload)
        raw = json.dumps(query_payload()).encode()
        self.assert_invalid(raw=raw.replace(b'"status": "completed"', b'"status":"completed","status":"completed"', 1))
        self.assert_invalid(raw=b"\xef\xbb\xbf" + raw)
        self.assert_invalid(raw=raw + b"{}")

    def test_argument_limit_is_utf8_bytes_and_inclusive(self):
        payload = query_payload("get_event", {"event_id": EVENT_ID, "time_zone": "UTC"})
        arguments = payload["output"][0]["arguments"]
        payload["output"][0]["arguments"] = arguments + " " * (MAX_QUERY_ARGUMENT_BYTES - len(arguments.encode()))
        self.assertEqual(self.plan(self.model(payload)).arguments["event_id"], EVENT_ID)
        payload["output"][0]["arguments"] += " "
        self.assert_invalid(payload)
        payload["output"][0]["arguments"] = json.dumps({"event_id": "á" * 4100, "time_zone": "UTC"}, ensure_ascii=False)
        self.assert_invalid(payload)

    def test_reported_model_and_usage_follow_text_reply_rules(self):
        for model in (None, "", "model/unsafe", "https://example.invalid", "model name", "mádel", "x" * 101, True):
            with self.subTest(model=model):
                payload = query_payload()
                payload["model"] = model
                self.assert_invalid(payload)
        for usage in ([], True, "unsafe", {"input_tokens": -1}, {"input_tokens": True},
                      {"input_tokens": 1.5}, {"output_tokens": "20"}, {"output_tokens": {} }):
            with self.subTest(usage=usage):
                payload = query_payload()
                payload["usage"] = usage
                self.assert_invalid(payload)
        for usage in (None, {}, {"input_tokens": 0, "output_tokens": 0}):
            payload = query_payload()
            payload["usage"] = usage
            result = self.plan(self.model(payload))
            self.assertEqual(result.input_tokens, 0 if usage else None)

    def test_transport_errors_are_safe_single_attempt_and_do_not_parse_provider_messages(self):
        for status, code in ((401, "llm_credentials_rejected"), (403, "llm_credentials_rejected"),
                             (429, "llm_rate_limited"), (302, "llm_unavailable"), (500, "llm_unavailable")):
            with self.subTest(status=status):
                model = self.model(response=FakeResponse(raw=MESSAGE.encode(), status=status))
                with self.assertRaises(ModelError) as raised:
                    self.plan(model)
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn(MESSAGE, str(raised.exception))
                self.assertEqual(len(self.session.calls), 1)
        for exception, code in ((requests.Timeout(KEY), "llm_timeout"),
                                (requests.ConnectionError(MESSAGE), "llm_unavailable")):
            with self.subTest(code=code):
                model = self.model(error=exception)
                with self.assertRaises(ModelError) as raised:
                    self.plan(model)
                self.assertEqual(raised.exception.code, code)
                self.assertTrue(raised.exception.__suppress_context__)
                self.assertNotIn(KEY, str(raised.exception))
                self.assertNotIn(MESSAGE, str(raised.exception))
                self.assertEqual(len(self.session.calls), 1)

    def test_body_limit_and_stream_timeout_apply_to_query_plans(self):
        self.assert_invalid(raw=b"x" * (MAX_RESPONSE_BYTES + 1))
        model = self.model(response=FakeResponse(error=requests.ReadTimeout(MESSAGE)))
        with self.assertRaises(ModelError) as raised:
            self.plan(model)
        self.assertEqual(raised.exception.code, "llm_timeout")
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.closed)

    def test_bad_prompts_fail_before_session(self):
        for field in ("instructions", "message"):
            for value in (None, "", "  ", "bad\x00", "bad\ud800"):
                with self.subTest(field=field, value=value):
                    options = {"instructions": INSTRUCTIONS, "message": MESSAGE, field: value}
                    model = self.model()
                    with self.assertRaises(ModelError):
                        model.plan_query(**options)
                    self.assertEqual(self.session.calls, [])

    def test_plan_does_not_execute_a_calendar_tool_and_generate_contract_is_preserved(self):
        with patch("requests.Session", side_effect=AssertionError("No network permitted")):
            result = self.plan(self.model(query_payload("get_event", {"event_id": EVENT_ID, "time_zone": "UTC"})))
        self.assertEqual(result.arguments["event_id"], EVENT_ID)
        payload = query_payload()
        payload["output"] = [{"type": "message", "role": "assistant", "status": "completed",
                              "content": [{"type": "output_text", "text": "Resposta de teste."}]}]
        model = self.model(payload)
        self.assertEqual(model.generate(instructions=INSTRUCTIONS, message=MESSAGE).text, "Resposta de teste.")
        self.assertNotIn("tools", self.session.calls[0][1]["json"])
        self.assertNotIn("tool_choice", self.session.calls[0][1]["json"])
        self.assert_invalid(payload)


if __name__ == "__main__":
    unittest.main()
