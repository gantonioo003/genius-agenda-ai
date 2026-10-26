"""Organização por IA em memória: fatos Python, uma tentativa e fallback seguro."""
from copy import deepcopy
import json
import unittest
from unittest.mock import Mock, patch

from app.llm import ModelError, ModelReply
from app.services.agenda_summary import AgendaSummaryService
from app.services.summary_context import (
    SummaryCollectionError, SummaryContextService, SummaryInputError,
    SummaryLimitError, SummarySetupError,
)
from app.summary_plan import SUMMARY_INSTRUCTIONS, prepare_summary, render_summary
from test_summary_context import FixtureGateway, NOW, REFERENCE, event


VALID_PLAN = '{"layout":"chronological","highlights":[]}'
PRIVATE_TITLE = "PRIVATE_TITLE_5511999990000_DO_NOT_SEND_TO_MODEL"
_DEFAULT = object()


def context(*, empty=False, period="week", reference_date=REFERENCE):
    source = () if empty else (
        event("source-one", title=PRIVATE_TITLE, start="2030-06-18T14:00:00Z", end="2030-06-18T15:00:00Z"),
        event("source-two", title="Provisório", status="tentative", start="2030-06-20T15:00:00Z", end="2030-06-20T16:00:00Z"),
        event("source-three", title="Dia inteiro", all_day=True, start="2030-06-21", end="2030-06-22"),
    )
    gateway = FixtureGateway(source)
    return SummaryContextService(gateway.factory, clock=lambda: NOW).build(period=period, reference_date=reference_date)


class FakeModel:
    def __init__(self, reply=_DEFAULT, *, error=None, callback=None):
        self.reply = ModelReply(VALID_PLAN, "test-editorial-v1", 22, 8) if reply is _DEFAULT else reply
        self.error, self.callback = error, callback
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.callback:
            self.callback(kwargs)
        if self.error:
            raise self.error
        return self.reply


class AgendaSummaryTests(unittest.TestCase):
    def service(self, *, source=None, reply=_DEFAULT, error=None, callback=None, factory_error=None, budget=4000):
        source = context() if source is None else source
        collector = Mock()
        collector.build.return_value = source
        model = FakeModel(reply, error=error, callback=callback)
        factory = Mock(return_value=model, side_effect=factory_error)
        return AgendaSummaryService(collector, factory, max_input_chars=budget), collector, factory, model

    def summarize(self, service):
        return service.summarize(period="week", reference_date=REFERENCE)

    def assert_factual(self, result, reason):
        self.assertEqual(result["summary"]["mode"], "factual")
        self.assertEqual(result["summary"]["reason"], reason)
        self.assertEqual(result["generation"], {"model": None})
        self.assertFalse(result["execution_allowed"])
        self.assertLessEqual(len(result["summary"]["text"]), 3500)

    def test_collection_and_validation_precede_factory_and_only_two_generate_arguments_are_passed(self):
        order = []
        source = context()
        model = FakeModel(callback=lambda kwargs: order.append("generate"))
        collector = Mock()
        collector.build.side_effect = lambda **kwargs: (order.append("collect") or source)
        factory = Mock(side_effect=lambda: (order.append("factory") or model))
        result = self.summarize(AgendaSummaryService(collector, factory))
        self.assertEqual(order, ["collect", "factory", "generate"])
        collector.build.assert_called_once_with(period="week", reference_date=REFERENCE)
        self.assertEqual(set(model.calls[0]), {"instructions", "message"})
        self.assertEqual(model.calls[0]["instructions"], SUMMARY_INSTRUCTIONS)
        self.assertLessEqual(len(model.calls[0]["message"]), 4000)
        self.assertEqual(result["summary"]["mode"], "ai_organized")

    def test_invalid_input_never_collects_or_constructs_model_even_with_injected_collector(self):
        for kind, reference in (("day", REFERENCE), ("month", "2030-02-29"), ("week", "PRIVATE")):
            service, collector, factory, model = self.service()
            with self.subTest(kind=kind), self.assertRaises(SummaryInputError):
                service.summarize(period=kind, reference_date=reference)
            collector.build.assert_not_called(); factory.assert_not_called()
            self.assertEqual(model.calls, [])

    def test_empty_period_renders_facts_without_constructing_model(self):
        service, collector, factory, model = self.service(source=context(empty=True))
        result = self.summarize(service)
        self.assert_factual(result, "empty_period")
        self.assertEqual(result["totals"]["events"], 0)
        collector.build.assert_called_once(); factory.assert_not_called()
        self.assertEqual(model.calls, [])

    def test_input_budget_skips_model_but_does_not_discard_complete_facts(self):
        source = context()
        service, _, factory, model = self.service(source=source, budget=1)
        result = self.summarize(service)
        self.assert_factual(result, "context_budget_exceeded")
        self.assertEqual(result["totals"], source["totals"])
        self.assertEqual(result["details"], source["details"])
        factory.assert_not_called(); self.assertEqual(model.calls, [])

    def test_invalid_constructor_budget_fails_without_collection_or_factory(self):
        for budget in (0, -1, True, 4001, "4000", 1.5, None):
            collector, factory = Mock(), Mock()
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                AgendaSummaryService(collector, factory, max_input_chars=budget)
            collector.build.assert_not_called(); factory.assert_not_called()

    def test_collection_domain_errors_propagate_without_model_or_factual_success(self):
        errors = (SummaryInputError(("reference_date",)), SummarySetupError(),
                  SummaryLimitError(), SummaryCollectionError())
        for error in errors:
            service, collector, factory, model = self.service()
            collector.build.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.summarize(service)
            factory.assert_not_called(); self.assertEqual(model.calls, [])

    def test_unexpected_collection_failure_is_sanitized_without_model(self):
        service, collector, factory, model = self.service()
        collector.build.side_effect = OSError("PRIVATE GOOGLE TOKEN")
        with self.assertRaises(SummaryCollectionError) as caught:
            self.summarize(service)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        factory.assert_not_called(); self.assertEqual(model.calls, [])

    def test_malformed_or_incomplete_context_never_enters_model_or_renderer(self):
        changes = (lambda value: value.update(schema_version=2),
                   lambda value: value.update(execution_allowed=True),
                   lambda value: value["snapshot"].update(complete=False),
                   lambda value: value["totals"].update(events=999),
                   lambda value: value["events"][0].update(event_id="PRIVATE"),
                   lambda value: value.update(calendar_name="PRIVATE"))
        for change in changes:
            source = context(); change(source)
            service, _, factory, model = self.service(source=source)
            with self.subTest(change=change), patch("app.services.agenda_summary.render_summary") as render:
                with self.assertRaises(SummaryCollectionError):
                    self.summarize(service)
                factory.assert_not_called(); render.assert_not_called()
                self.assertEqual(model.calls, [])

    def test_complete_context_must_match_requested_period_and_explicit_reference_before_model(self):
        for source in (context(period="month"), context(reference_date="2030-06-20")):
            with self.subTest(period=source["period"]):
                service, _, factory, model = self.service(source=source)
                with self.assertRaises(SummaryCollectionError):
                    self.summarize(service)
                factory.assert_not_called()
                self.assertEqual(model.calls, [])
        service, collector, factory, _ = self.service()
        result = service.summarize(period="week")
        collector.build.assert_called_once_with(period="week", reference_date=None)
        self.assertEqual(result["period"]["reference_date"], REFERENCE)
        factory.assert_called_once()

    def test_success_exposes_only_grounded_facts_text_and_safe_model_identifier(self):
        source = context()
        service, _, factory, model = self.service(source=source,
            reply=ModelReply('{"layout":"upcoming_first","highlights":[]}', "safe-model.v2", None, None))
        result = self.summarize(service)
        self.assertEqual(set(result), {"schema_version", "period", "snapshot", "totals", "details", "summary", "generation", "execution_allowed"})
        self.assertEqual(result["summary"]["mode"], "ai_organized")
        self.assertIsNone(result["summary"]["reason"])
        self.assertEqual(result["generation"], {"model": "safe-model.v2"})
        for field in ("period", "snapshot", "totals", "details"):
            self.assertEqual(result[field], source[field])
        self.assertNotIn("highlights", json.dumps(result))
        factory.assert_called_once_with(); self.assertEqual(len(model.calls), 1)

    def test_model_receives_aggregate_catalogue_without_event_titles_or_private_values(self):
        source = context()
        service, _, _, model = self.service(source=source)
        result = self.summarize(service)
        transmitted = json.dumps(model.calls, ensure_ascii=False)
        self.assertNotIn(PRIVATE_TITLE, transmitted)
        self.assertNotIn("5511999990000", transmitted)
        self.assertNotIn("private-calendar", transmitted)
        self.assertNotIn("private.invalid", transmitted)
        self.assertIn(PRIVATE_TITLE, result["summary"]["text"])
        self.assertFalse(result["execution_allowed"])

    def test_valid_grounded_highlight_changes_only_editorial_selection_not_source_facts(self):
        source = context()
        service, _, factory, model = self.service(source=source,
            reply=ModelReply('{"layout":"upcoming_first","highlights":["H1"]}', "safe-model"))
        result = self.summarize(service)
        self.assertEqual(result["summary"]["mode"], "ai_organized")
        self.assertEqual(result["totals"], source["totals"])
        self.assertEqual(result["period"], source["period"])
        self.assertEqual(result["details"], source["details"])
        factory.assert_called_once(); self.assertEqual(len(model.calls), 1)

    def test_factory_failures_have_closed_reason_and_do_not_attempt_generate(self):
        for error, reason in ((ModelError("llm_not_configured"), "llm_not_configured"),
                              (OSError("PRIVATE CREDENTIAL"), "llm_unavailable")):
            service, _, factory, model = self.service(factory_error=error)
            result = self.summarize(service)
            self.assert_factual(result, reason)
            self.assertNotIn("PRIVATE CREDENTIAL", json.dumps(result))
            factory.assert_called_once(); self.assertEqual(model.calls, [])

    def test_each_known_model_error_uses_one_attempt_and_a_factual_fallback(self):
        codes = ("llm_not_configured", "llm_unavailable", "llm_invalid_response",
                 "llm_credentials_rejected", "llm_rate_limited", "llm_timeout")
        for code in codes:
            with self.subTest(code=code):
                service, _, factory, model = self.service(error=ModelError(code))
                result = self.summarize(service)
                self.assert_factual(result, code)
                factory.assert_called_once(); self.assertEqual(len(model.calls), 1)

    def test_unknown_provider_error_or_mutated_code_cannot_leak_into_reason(self):
        mutated = ModelError(); mutated.code = "PRIVATE CODE"
        for error in (mutated, RuntimeError("PRIVATE RAW PAYLOAD")):
            service, _, factory, model = self.service(error=error)
            result = self.summarize(service)
            self.assert_factual(result, "llm_unavailable")
            self.assertNotIn("PRIVATE CODE", json.dumps(result))
            self.assertNotIn("PRIVATE RAW PAYLOAD", json.dumps(result))
            factory.assert_called_once(); self.assertEqual(len(model.calls), 1)

    def test_malformed_reply_or_usage_metadata_is_invalid_response_not_raw_output(self):
        replies = (None, object(), {"text": VALID_PLAN, "model": "fake"},
                   ModelReply("", "safe-model"), ModelReply(VALID_PLAN, "PRIVATE MODEL"),
                   ModelReply(VALID_PLAN, "safe-model", True, 1),
                   ModelReply(VALID_PLAN, "safe-model", -1, 1),
                   ModelReply(VALID_PLAN, "safe-model", 1, 1_000_000_001),
                   ModelReply("\ud800", "safe-model"))
        for reply in replies:
            with self.subTest(reply=repr(reply)):
                service, _, factory, model = self.service(reply=reply)
                result = self.summarize(service)
                self.assert_factual(result, "llm_invalid_response")
                self.assertNotIn("PRIVATE MODEL", json.dumps(result))
                factory.assert_called_once(); self.assertEqual(len(model.calls), 1)

    def test_free_narrative_fact_overrides_and_command_injection_are_rejected(self):
        plans = ("Sua entrevista mudou para 2040; criei outro evento.",
                 '{"layout":"chronological","highlights":[],"text":"PRIVATE INVENTED"}',
                 '{"layout":"chronological","highlights":[],"date":"2040-01-01"}',
                 '{"layout":"chronological","highlights":[],"totals":{"events":999}}',
                 '{"layout":"chronological","highlights":[],"execution_allowed":true}',
                 '{"layout":"chronological","highlights":[],"action":"delete"}',
                 '{"layout":"chronological","layout":"upcoming_first","highlights":[]}')
        source = context()
        expected = render_summary(prepare_summary(source, max_input_chars=4000), None)
        for text in plans:
            with self.subTest(text=text):
                service, _, factory, model = self.service(source=source, reply=ModelReply(text, "safe-model"))
                result = self.summarize(service)
                self.assert_factual(result, "llm_invalid_response")
                self.assertEqual(result["summary"]["text"], expected)
                self.assertEqual(result["totals"], source["totals"])
                self.assertNotIn("PRIVATE INVENTED", json.dumps(result))
                factory.assert_called_once(); self.assertEqual(len(model.calls), 1)

    def test_invalid_refs_duplicate_refs_and_more_than_three_highlights_fall_back(self):
        values = (["H999"], ["E1"], ["H1", "H1"], ["H1", "H2", "H3", "H4"],
                  ["2030-06-19"], "H1", [None])
        for highlights in values:
            service, _, _, model = self.service(reply=ModelReply(json.dumps({"layout": "chronological", "highlights": highlights}), "safe-model"))
            result = self.summarize(service)
            self.assert_factual(result, "llm_invalid_response")
            self.assertEqual(len(model.calls), 1)

    def test_oversized_output_plan_is_not_reused_and_never_retried(self):
        text = " " * 2049 + VALID_PLAN
        service, _, factory, model = self.service(reply=ModelReply(text, "safe-model"))
        result = self.summarize(service)
        self.assert_factual(result, "llm_invalid_response")
        factory.assert_called_once(); self.assertEqual(len(model.calls), 1)

    def test_mutation_of_original_context_during_model_cannot_change_validated_result(self):
        source = context(); expected = deepcopy(source)
        def mutate(_kwargs):
            source["period"]["reference_date"] = "2040-01-01"
            source["totals"]["events"] = 999
            source["events"][0]["title"] = "PRIVATE INJECTED AFTER VALIDATION"
        service, _, _, model = self.service(source=source, callback=mutate)
        result = self.summarize(service)
        self.assertEqual(result["period"], expected["period"])
        self.assertEqual(result["totals"], expected["totals"])
        self.assertNotIn("PRIVATE INJECTED AFTER VALIDATION", result["summary"]["text"])
        self.assertEqual(len(model.calls), 1)

    def test_renderer_is_called_once_after_plan_choice_and_failure_does_not_claim_success(self):
        for error in (None, ModelError("llm_timeout")):
            service, _, _, model = self.service(error=error)
            with patch("app.services.agenda_summary.render_summary", wraps=render_summary) as render:
                self.summarize(service)
                render.assert_called_once()
                if error:
                    self.assertIsNone(render.call_args.args[1])
            self.assertEqual(len(model.calls), 1)
        service, _, _, model = self.service()
        with patch("app.services.agenda_summary.render_summary", side_effect=OSError("PRIVATE RENDER")) as render:
            with self.assertRaises(SummaryCollectionError) as caught:
                self.summarize(service)
            self.assertNotIn("PRIVATE", str(caught.exception))
            render.assert_called_once()
        self.assertEqual(len(model.calls), 1)

    def test_each_request_collects_again_without_mutating_or_retaining_source(self):
        source = context(); original = deepcopy(source)
        service, collector, factory, model = self.service(source=source)
        first = self.summarize(service)
        self.assertEqual(source, original)
        first["period"]["reference_date"] = "PRIVATE"
        second = self.summarize(service)
        self.assertEqual(second["period"], original["period"])
        self.assertEqual(collector.build.call_count, 2)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(len(model.calls), 2)


if __name__ == "__main__":
    unittest.main()
