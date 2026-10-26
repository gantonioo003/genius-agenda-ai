"""Planos de esclarecimento via fixtures REST; sem credenciais ou serviços reais."""

from dataclasses import FrozenInstanceError
import json
import unittest

import requests

from app.dialogue_contract import DIALOGUE_TOOLS, DialoguePlan
from app.llm import MAX_DIALOGUE_ARGUMENT_BYTES, MAX_RESPONSE_BYTES, ModelError, OpenAIResponsesModel


KEY = "PRIVATE_DIALOGUE_FIXTURE_KEY"
MESSAGE = "PRIVATE_DIALOGUE_FIXTURE_MESSAGE"
INSTRUCTIONS = "PRIVATE_DIALOGUE_FIXTURE_INSTRUCTIONS"
EVENT_ID = "PRIVATE_DIALOGUE_FIXTURE_EVENT_ID"
CALL_ID = "call_PRIVATE_DIALOGUE_FIXTURE_ID"


def draft(**updates):
    result = {name: None for name in (
        "action", "time_zone", "title", "start", "end", "description", "location",
        "event_id", "search_title", "date", "period", "ambiguous_field",
    )}
    result.update(mode="start", use_selected_event=False)
    result.update(updates)
    return result


def dialogue_payload(name="prepare_request", arguments=None):
    return {
        "status": "completed", "model": "example-model-2026-10-01", "error": None,
        "incomplete_details": None,
        "output": [{"type": "function_call", "status": "completed", "call_id": CALL_ID,
                    "name": name, "arguments": json.dumps(draft() if arguments is None else arguments)}],
        "usage": {"input_tokens": 42, "output_tokens": 23},
    }


class FakeResponse:
    def __init__(self, payload=None, *, raw=None, status=200, chunks=None, error=None):
        self.status_code = status
        self.raw = json.dumps(dialogue_payload() if payload is None else payload).encode() if raw is None else raw
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


class DialoguePlanTests(unittest.TestCase):
    def model(self, payload=None, *, response=None, error=None):
        self.session = FakeSession(FakeResponse(payload) if response is None else response, error=error)
        return OpenAIResponsesModel(KEY, "example-model", session_factory=lambda: self.session)

    def plan(self, model):
        return model.plan_dialogue(instructions=INSTRUCTIONS, message=MESSAGE)

    def assert_invalid(self, payload=None, *, raw=None):
        response = FakeResponse(payload, raw=raw)
        with self.assertRaises(ModelError) as raised:
            self.plan(self.model(response=response))
        self.assertEqual(raised.exception.code, "llm_invalid_response")
        self.assertEqual(raised.exception.http_status, 502)
        self.assertIsNone(raised.exception.__cause__)
        for private in (KEY, MESSAGE, INSTRUCTIONS, EVENT_ID, CALL_ID):
            self.assertNotIn(private, str(raised.exception))
            self.assertNotIn(private, repr(raised.exception))
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.closed)
        self.assertTrue(response.closed)

    def test_contract_is_frozen_and_arguments_are_private(self):
        plan = DialoguePlan("prepare_request", draft(event_id=EVENT_ID), "example-model")
        self.assertNotIn(EVENT_ID, repr(plan))
        self.assertIsNone(plan.input_tokens)
        with self.assertRaises(FrozenInstanceError):
            plan.name = "execute"

    def test_schemas_are_strict_exact_and_nullable(self):
        self.assertEqual([tool["name"] for tool in DIALOGUE_TOOLS],
                         ["prepare_request", "select_candidate", "ask_dialogue", "cancel_request"])
        for tool in DIALOGUE_TOOLS:
            self.assertIs(tool["strict"], True)
            self.assertEqual(tool["type"], "function")
            specification = tool["parameters"]
            self.assertEqual(specification["type"], "object")
            self.assertIs(specification["additionalProperties"], False)
            self.assertEqual(set(specification["required"]), set(specification["properties"]))
        properties = DIALOGUE_TOOLS[0]["parameters"]["properties"]
        self.assertEqual(set(properties), set(draft()))
        self.assertEqual(properties["action"]["enum"], ["create", "list", "reschedule", "edit", "delete", None])
        self.assertEqual(properties["ambiguous_field"]["enum"], [
            None, "action", "title", "start", "end", "time_zone", "date", "period", "event", "changes", "request",
        ])
        for field in set(draft()) - {"mode", "use_selected_event"}:
            self.assertEqual(properties[field]["type"], ["string", "null"])
        self.assertEqual(DIALOGUE_TOOLS[3]["parameters"]["properties"], {})

    def test_single_request_has_no_remote_state_no_retries_and_fixed_transport(self):
        result = self.plan(self.model())
        self.assertEqual(result, DialoguePlan("prepare_request", draft(), "example-model-2026-10-01", 42, 23))
        self.assertEqual(len(self.session.calls), 1)
        url, options = self.session.calls[0]
        self.assertEqual(url, "https://api.openai.com/v1/responses")
        self.assertEqual(options["json"], {
            "model": "example-model", "instructions": INSTRUCTIONS,
            "input": [{"role": "user", "content": MESSAGE}], "max_output_tokens": 600,
            "store": False, "tools": DIALOGUE_TOOLS, "tool_choice": "required", "parallel_tool_calls": False,
        })
        self.assertFalse(self.session.trust_env)
        self.assertEqual(self.session.adapters[0][1].max_retries.total, 0)
        self.assertEqual(options["timeout"], (5, 30.0))
        self.assertFalse(options["allow_redirects"])
        self.assertTrue(options["verify"])
        self.assertTrue(options["stream"])
        self.assertEqual(options["headers"]["Authorization"], "Bearer " + KEY)

    def test_all_actions_modes_and_functions_parse(self):
        cases = [("prepare_request", draft(mode=mode, action=action))
                 for mode in ("start", "continue")
                 for action in ("create", "list", "reschedule", "edit", "delete", None)]
        cases += [("select_candidate", {"choice": choice}) for choice in (1, 20)]
        cases += [("prepare_request", draft(title="Entrevista", ambiguous_field=field))
                  for field in (None, "action", "title", "start", "end", "time_zone", "date", "period", "event", "changes", "request")]
        cases += [("ask_dialogue", {"reason": reason, "field": field})
                  for reason in ("ambiguous_information", "multiple_requests", "unclear_answer", "unsupported_request")
                  for field in ("action", "title", "start", "end", "time_zone", "date", "period", "event", "changes", "request")]
        cases += [("cancel_request", {})]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                plan = self.plan(self.model(dialogue_payload(name, arguments)))
                self.assertEqual(plan.name, name)
                self.assertEqual(plan.arguments, arguments)

    def test_null_is_retained_and_empty_draft_fields_parse_for_downstream_validation(self):
        arguments = draft(action="create", title="", description="", location="", use_selected_event=True)
        plan = self.plan(self.model(dialogue_payload(arguments=arguments)))
        self.assertEqual(plan.arguments, arguments)
        self.assertIsNone(plan.arguments["start"])

    def test_multiline_draft_text_is_allowed_but_other_control_characters_are_not(self):
        for field in ("title", "description", "location"):
            arguments = draft(**{field: "Entrevista\tno\nprimeiro\randar"})
            self.assertEqual(self.plan(self.model(dialogue_payload(arguments=arguments))).arguments, arguments)
        for field in draft():
            if field in {"mode", "action", "use_selected_event"}:
                continue
            for value in ("bad\x00", "bad\x1f", "bad\x7f", "bad\x85", "bad\ud800"):
                with self.subTest(field=field, value=value):
                    self.assert_invalid(dialogue_payload(arguments=draft(**{field: value})))
        for field in ("start", "event_id", "search_title", "time_zone", "date", "period"):
            for value in ("bad\tvalue", "bad\nvalue", "bad\rvalue"):
                self.assert_invalid(dialogue_payload(arguments=draft(**{field: value})))

    def test_required_keys_and_unknown_fields_are_rejected(self):
        for field in draft():
            arguments = draft()
            del arguments[field]
            with self.subTest(missing=field):
                self.assert_invalid(dialogue_payload(arguments=arguments))
        for name, arguments in (("prepare_request", {**draft(), "confirmed": True}),
                                ("select_candidate", {}), ("select_candidate", {"choice": 1, "event_id": EVENT_ID}),
                                ("ask_dialogue", {"reason": "unclear_answer"}),
                                ("cancel_request", {"confirmed": True})):
            self.assert_invalid(dialogue_payload(name, arguments))

    def test_types_are_strict_for_nullable_string_fields(self):
        for field in set(draft()) - {"mode", "use_selected_event"}:
            for value in (True, False, 0, 1.5, [], {}):
                with self.subTest(field=field, value=value):
                    self.assert_invalid(dialogue_payload(arguments=draft(**{field: value})))
        for field in set(draft()) - {"title", "description", "location", "use_selected_event"}:
            for value in ("", " ", "\t\n"):
                self.assert_invalid(dialogue_payload(arguments=draft(**{field: value})))

    def test_boolean_mode_action_and_enum_values_are_strict(self):
        for value in (None, 0, 1, "true", [], {}):
            self.assert_invalid(dialogue_payload(arguments=draft(use_selected_event=value)))
        for value in (None, "", "resume", True):
            self.assert_invalid(dialogue_payload(arguments=draft(mode=value)))
        for value in ("execute", "get", "CREATE"):
            self.assert_invalid(dialogue_payload(arguments=draft(action=value)))
        for value in ("other", "event_id", "", True):
            self.assert_invalid(dialogue_payload(arguments=draft(ambiguous_field=value)))
        for arguments in ({"reason": "other", "field": "event"},
                          {"reason": "unclear_answer", "field": "event_id"},
                          {"reason": None, "field": "event"}):
            self.assert_invalid(dialogue_payload("ask_dialogue", arguments))

    def test_ambiguous_field_preserves_known_draft_values_without_inventing_missing_data(self):
        arguments = draft(action="create", title="Entrevista", location="Escritório", ambiguous_field="start")
        result = self.plan(self.model(dialogue_payload(arguments=arguments)))
        self.assertEqual(result.arguments, arguments)
        self.assertIsNone(result.arguments["start"])
        self.assertIsNone(result.arguments["end"])
        self.assertEqual(result.arguments["title"], "Entrevista")
        self.assertEqual(result.arguments["ambiguous_field"], "start")

    def test_candidate_position_is_a_strict_bounded_integer(self):
        for choice in (None, True, False, 0, -1, 21, 1.0, "1", [], {}):
            with self.subTest(choice=choice):
                self.assert_invalid(dialogue_payload("select_candidate", {"choice": choice}))

    def test_reasoning_may_surround_only_one_complete_function_call(self):
        payload = dialogue_payload()
        del payload["output"][0]["status"]
        payload["output"].insert(0, {"type": "reasoning", "summary": []})
        payload["output"].append({"type": "reasoning", "summary": []})
        self.assertEqual(self.plan(self.model(payload)).name, "prepare_request")
        for output in ([], None, {}, [None], [{"type": "reasoning"}],
                       [dialogue_payload()["output"][0]] * 2,
                       [{"type": "message", "role": "assistant", "status": "completed", "content": []}],
                       [dialogue_payload()["output"][0], {"type": "message"}],
                       [{"type": "refusal"}], [{"type": "web_search_call"}]):
            payload = dialogue_payload()
            payload["output"] = output
            self.assert_invalid(payload)

    def test_unknown_tool_call_id_and_function_status_are_rejected(self):
        for field, values in {"name": [None, "execute", "create_event", "list_events", True, []],
                              "call_id": [None, "", " ", "bad\x00", "bad\ud800"],
                              "status": [None, "failed", "in_progress", "incomplete", True]}.items():
            for value in values:
                payload = dialogue_payload()
                payload["output"][0][field] = value
                self.assert_invalid(payload)

    def test_raw_arguments_reject_invalid_json_duplicates_constants_and_trailing_data(self):
        for arguments in (None, {}, [], True, "", "null", "[]", "true", '"text"',
                          '{"choice":1,"choice":2}', '{"choice":NaN}', '{"choice":Infinity}',
                          '{"choice":-Infinity}', '{"choice":1}{}', '\ufeff{"choice":1}', '{"choice":"\ud800"}'):
            payload = dialogue_payload("select_candidate", {"choice": 1})
            payload["output"][0]["arguments"] = arguments
            self.assert_invalid(payload)

    def test_outer_shape_json_and_encoding_are_strict(self):
        raw = json.dumps(dialogue_payload()).encode()
        for invalid in (b"null", b"[]", b'"text"', b"{}", b"", b"not-json", b"\xff", raw + b"{}",
                        b"\xef\xbb\xbf" + raw,
                        raw.replace(b'"status": "completed"', b'"status":"completed","status":"completed"', 1)):
            self.assert_invalid(raw=invalid)

    def test_failed_incomplete_and_error_results_are_rejected(self):
        for field, values in {"status": [None, "incomplete", "failed", "queued", "in_progress"],
                              "error": [{}, {"message": MESSAGE}],
                              "incomplete_details": [{}, {"reason": "max_output_tokens"}]}.items():
            for value in values:
                payload = dialogue_payload()
                payload[field] = value
                self.assert_invalid(payload)

    def test_argument_limit_counts_utf8_bytes_and_includes_boundary(self):
        payload = dialogue_payload("cancel_request", {})
        payload["output"][0]["arguments"] = "{}" + " " * (MAX_DIALOGUE_ARGUMENT_BYTES - 2)
        self.assertEqual(self.plan(self.model(payload)).arguments, {})
        payload["output"][0]["arguments"] += " "
        self.assert_invalid(payload)
        payload = dialogue_payload(arguments=draft(description="á" * 12300))
        payload["output"][0]["arguments"] = json.dumps(draft(description="á" * 12300), ensure_ascii=False)
        self.assert_invalid(payload)

    def test_model_name_and_usage_are_validated_without_provider_details(self):
        for model in (None, "", "model/unsafe", "model name", "mádel", "x" * 101, True):
            payload = dialogue_payload()
            payload["model"] = model
            self.assert_invalid(payload)
        for usage in ([], True, "unsafe", {"input_tokens": -1}, {"input_tokens": True},
                      {"input_tokens": 1.5}, {"output_tokens": "20"}, {"output_tokens": {}}):
            payload = dialogue_payload()
            payload["usage"] = usage
            self.assert_invalid(payload)
        for usage in (None, {}, {"input_tokens": 0, "output_tokens": 0}):
            payload = dialogue_payload()
            payload["usage"] = usage
            result = self.plan(self.model(payload))
            self.assertEqual(result.input_tokens, 0 if usage else None)

    def test_http_errors_are_safe_and_never_retried(self):
        for status, code in ((401, "llm_credentials_rejected"), (403, "llm_credentials_rejected"),
                             (429, "llm_rate_limited"), (302, "llm_unavailable"), (500, "llm_unavailable")):
            with self.assertRaises(ModelError) as raised:
                self.plan(self.model(response=FakeResponse(raw=MESSAGE.encode(), status=status)))
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn(MESSAGE, str(raised.exception))
            self.assertEqual(len(self.session.calls), 1)

    def test_timeout_network_and_stream_failures_are_safe_single_attempt(self):
        for exception, code in ((requests.Timeout(KEY), "llm_timeout"),
                                (requests.ConnectionError(MESSAGE), "llm_unavailable")):
            with self.assertRaises(ModelError) as raised:
                self.plan(self.model(error=exception))
            self.assertEqual(raised.exception.code, code)
            self.assertTrue(raised.exception.__suppress_context__)
            self.assertNotIn(KEY, str(raised.exception))
            self.assertNotIn(MESSAGE, str(raised.exception))
            self.assertEqual(len(self.session.calls), 1)
        self.assert_invalid(raw=b"x" * (MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(ModelError) as raised:
            self.plan(self.model(response=FakeResponse(error=requests.ReadTimeout(MESSAGE))))
        self.assertEqual(raised.exception.code, "llm_timeout")
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.closed)

    def test_invalid_prompts_fail_before_network(self):
        for field in ("instructions", "message"):
            for value in (None, "", " ", "bad\x00", "bad\ud800"):
                model = self.model()
                with self.assertRaises(ModelError):
                    model.plan_dialogue(**{"instructions": INSTRUCTIONS, "message": MESSAGE, field: value})
                self.assertEqual(self.session.calls, [])


if __name__ == "__main__":
    unittest.main()
