"""Argumentos compactos só no Gemini; o plano interno conserva o contrato completo."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.agent_query_contract import QUERY_TOOLS
from app.dialogue_contract import DIALOGUE_TOOLS
from app.gemini_llm import GeminiModel
from app.llm import MAX_DIALOGUE_ARGUMENT_BYTES, ModelError, OpenAIResponsesModel


KEY = "PRIVATE_COMPACT_FIXTURE_KEY"
MODEL = "gemini-3.5-flash-lite"
CORE = {"mode": "start", "action": "delete", "use_selected_event": False}
PREPARE_SCHEMA = DIALOGUE_TOOLS[0]["parameters"]


def canonical(arguments):
    return {**{field: None for field in PREPARE_SCHEMA["required"]}, **arguments}


def response_payload(name, arguments):
    return {"candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
        {"functionCall": {"name": name, "args": arguments}},
    ]}}], "modelVersion": MODEL}


class Response:
    def __init__(self, payload=None, *, raw=None):
        self.status_code = 200
        self.raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") if raw is None else raw
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, *, chunk_size):
        yield self.raw


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def mount(self, *_args):
        pass

    def post(self, url, **options):
        self.calls.append((url, options))
        return self.response


class GeminiCompactDialogueTests(unittest.TestCase):
    def invoke(self, arguments=None, *, name="prepare_request", payload=None, raw=None, query=False):
        if payload is None:
            payload = response_payload(name, CORE if arguments is None else arguments)
        self.response = Response(payload, raw=raw)
        self.session = Session(self.response)
        model = GeminiModel(KEY, MODEL, session_factory=lambda: self.session)
        method = model.plan_query if query else model.plan_dialogue
        return method(instructions="Prepare somente os dados informados.", message="Pedido fictício")

    def assert_invalid(self, arguments=None, **options):
        with self.assertRaises(ModelError) as caught:
            self.invoke(arguments, **options)
        self.assertEqual((caught.exception.code, caught.exception.http_status), ("llm_invalid_response", 502))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.closed and self.response.closed)

    def test_outgoing_schema_keeps_only_three_core_fields_required(self):
        snapshot = deepcopy(DIALOGUE_TOOLS)
        self.invoke()

        declarations = self.session.calls[0][1]["json"]["tools"][0]["functionDeclarations"]
        schema = declarations[0]["parametersJsonSchema"]
        self.assertEqual(schema["required"], ["mode", "action", "use_selected_event"])
        self.assertEqual(schema["properties"], PREPARE_SCHEMA["properties"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(DIALOGUE_TOOLS, snapshot)
        self.assertEqual(set(PREPARE_SCHEMA["required"]), set(PREPARE_SCHEMA["properties"]))
        for actual, original in zip(declarations[1:], DIALOGUE_TOOLS[1:]):
            self.assertEqual(actual["parametersJsonSchema"], original["parameters"])
        schema["required"].clear()
        self.assertEqual(DIALOGUE_TOOLS, snapshot)

    def test_minimal_plan_restores_only_nullable_missing_fields(self):
        supplied = dict(CORE)

        result = self.invoke(supplied)

        self.assertEqual(result.arguments, canonical(supplied))
        self.assertEqual(supplied, CORE)
        self.assertEqual(set(result.arguments), set(PREPARE_SCHEMA["required"]))
        self.assertEqual(result.arguments["action"], "delete")
        self.assertIs(result.arguments["use_selected_event"], False)

    def test_all_mutation_and_list_arguments_preserve_provided_values(self):
        cases = (
            {**CORE, "search_title": "Entrevista técnica"},
            {**CORE, "action": "edit", "search_title": "Entrevista técnica", "location": "Sala 2", "description": ""},
            {**CORE, "action": "reschedule", "search_title": "Entrevista técnica",
             "start": "2030-06-17T15:00:00-03:00", "end": "2030-06-17T15:30:00-03:00"},
            {**CORE, "action": "create", "title": "Entrevista", "start": "2030-06-17T15:00:00-03:00"},
            {**CORE, "action": "list", "date": "2030-06-17", "period": "week"},
            {**CORE, "mode": "continue", "action": None, "end": "2030-06-17T15:30:00-03:00"},
            {**CORE, "action": "edit", "use_selected_event": True, "title": "Novo título"},
        )
        for supplied in cases:
            with self.subTest(action=supplied["action"], mode=supplied["mode"]):
                self.assertEqual(self.invoke(supplied).arguments, canonical(supplied))

    def test_full_canonical_and_explicit_null_plans_remain_accepted(self):
        supplied = canonical({**CORE, "search_title": "Entrevista", "description": "", "location": None})
        self.assertEqual(self.invoke(supplied).arguments, supplied)
        partial = {**CORE, "time_zone": None, "search_title": "Entrevista", "description": "", "location": ""}
        self.assertEqual(self.invoke(partial).arguments, canonical(partial))

    def test_missing_core_fields_are_rejected_even_if_action_accepts_null(self):
        for missing in ("mode", "action", "use_selected_event"):
            with self.subTest(missing=missing):
                supplied = {key: value for key, value in CORE.items() if key != missing}
                self.assert_invalid(supplied)
        self.assertEqual(self.invoke({**CORE, "action": None}).arguments["action"], None)

    def test_absent_nonnullable_future_fields_are_never_synthesized(self):
        changed = deepcopy(DIALOGUE_TOOLS)
        changed[0]["parameters"]["properties"]["location"] = {"type": "string"}
        with patch("app.gemini_llm.DIALOGUE_TOOLS", changed):
            self.assert_invalid(CORE)
            outgoing = self.session.calls[0][1]["json"]["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]
            self.assertEqual(outgoing["required"], ["mode", "action", "location", "use_selected_event"])

    def test_extra_fields_and_invalid_values_are_not_normalized_away(self):
        cases = (
            {**CORE, "execute": True}, {**CORE, "confirmed": True},
            {**CORE, "mode": None}, {**CORE, "mode": "continue_all"},
            {**CORE, "action": "erase"}, {**CORE, "use_selected_event": None},
            {**CORE, "use_selected_event": "false"}, {**CORE, "use_selected_event": 0},
            {**CORE, "description": {}}, {**CORE, "search_title": 123},
            {**CORE, "time_zone": "America/Recife\n"}, {**CORE, "ambiguous_field": "calendar"},
        )
        for supplied in cases:
            with self.subTest(keys=list(supplied)):
                self.assert_invalid(supplied)

    def test_other_dialogue_plans_keep_exact_required_arguments(self):
        for name, arguments in (("select_candidate", {}), ("ask_dialogue", {"reason": "ambiguous_information"}),
                                ("cancel_request", {"event_id": "invented"})):
            with self.subTest(name=name):
                self.assert_invalid(arguments, name=name)
        self.assertEqual(self.invoke({}, name="cancel_request").arguments, {})

    def test_query_tools_are_not_compacted_or_filled(self):
        self.assert_invalid({"date": "2030-06-17", "period": "day"}, name="list_events", query=True)
        declarations = self.session.calls[0][1]["json"]["tools"][0]["functionDeclarations"]
        for actual, original in zip(declarations, QUERY_TOOLS):
            self.assertEqual(actual["parametersJsonSchema"], original["parameters"])

    def test_canonical_argument_byte_limit_still_applies_after_expansion(self):
        supplied = {**CORE, "description": ""}
        base = len(json.dumps(canonical(supplied), ensure_ascii=False, allow_nan=False).encode("utf-8"))
        exact = {**supplied, "description": "x" * (MAX_DIALOGUE_ARGUMENT_BYTES - base)}
        self.assertEqual(self.invoke(exact).arguments, canonical(exact))
        too_big = {**exact, "description": exact["description"] + "x"}
        self.assertLess(len(json.dumps(too_big).encode()), MAX_DIALOGUE_ARGUMENT_BYTES)
        self.assert_invalid(too_big)

    def test_malformed_duplicate_and_non_json_responses_still_fail_closed(self):
        raw = json.dumps(response_payload("prepare_request", CORE))
        cases = (
            b"not-json", b"\xff", b"[]", b"null",
            raw.replace('"mode": "start"', '"mode":"start","mode":"continue"').encode(),
            raw.replace('"action": "delete"', '"action": NaN').encode(),
        )
        for value in cases:
            with self.subTest(raw_kind=type(value).__name__):
                self.assert_invalid(raw=value)

    def test_missing_argument_objects_multiple_calls_and_truncation_are_rejected(self):
        original = response_payload("prepare_request", CORE)
        bad = []
        missing = deepcopy(original)
        del missing["candidates"][0]["content"]["parts"][0]["functionCall"]["args"]
        bad.append(missing)
        multiple = deepcopy(original)
        multiple["candidates"][0]["content"]["parts"].append(deepcopy(multiple["candidates"][0]["content"]["parts"][0]))
        bad.append(multiple)
        for reason in ("MAX_TOKENS", "SAFETY", "CONTINUATION", "MALFORMED_FUNCTION_CALL"):
            unfinished = deepcopy(original)
            unfinished["candidates"][0]["finishReason"] = reason
            bad.append(unfinished)
        for value in bad:
            with self.subTest(candidate_keys=list(value["candidates"][0])):
                self.assert_invalid(payload=value)

    def test_openai_keeps_original_strict_schema_and_rejects_compact_arguments(self):
        compact = {**CORE, "search_title": "Entrevista"}
        payload = {"status": "completed", "model": "fixture-openai-model", "output": [{
            "type": "function_call", "status": "completed", "name": "prepare_request", "call_id": "fixture-call",
            "arguments": json.dumps(compact),
        }]}
        response = Response(payload)
        session = Session(response)
        model = OpenAIResponsesModel(KEY, "fixture-openai-model", session_factory=lambda: session)

        with self.assertRaises(ModelError) as caught:
            model.plan_dialogue(instructions="Instrução fictícia", message="Pedido fictício")

        self.assertEqual(caught.exception.code, "llm_invalid_response")
        outgoing = session.calls[0][1]["json"]["tools"]
        self.assertEqual(outgoing, DIALOGUE_TOOLS)
        self.assertEqual(set(outgoing[0]["parameters"]["required"]), set(PREPARE_SCHEMA["properties"]))
        self.assertTrue(response.closed and session.closed)

    def test_compact_example_reduces_serialized_arguments_without_changing_values(self):
        compact = {**CORE, "search_title": "Entrevista técnica"}
        full = canonical(compact)
        encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        self.assertEqual(self.invoke(compact).arguments, full)
        self.assertLess(len(encode(compact)), len(encode(full)))
        self.assertEqual(len(compact), 4)
        self.assertEqual(len(full), len(PREPARE_SCHEMA["required"]))


if __name__ == "__main__":
    unittest.main()
