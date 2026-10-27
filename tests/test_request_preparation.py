"""Preparação de pedidos e perguntas com conector somente de leitura simulado."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
import io
import json
from types import MappingProxyType
import unittest
from unittest.mock import Mock, patch

import httplib2
from googleapiclient.errors import HttpError

from app import google_calendar
from app import prepare_request as command
from app.services.agenda import AgendaEvent, CalendarListing
from app.services.event_selection import EventLookup
from app.services.interviews import CalendarOperationError
from app.services.request_preparation import RequestPreparationService


EVENT_ID = "AbC123_20261023T190000Z"


def agenda_event(event_id=EVENT_ID, title="Entrevista técnica — Empresa Exemplo (teste)", **changes):
    event = AgendaEvent(
        event_id, title, "2026-10-23T16:00:00-03:00", "2026-10-23T16:30:00-03:00",
        False, "America/Recife", "confirmed", "https://example.invalid/event",
    )
    return replace(event, **changes)


def create_request(**changes):
    return {
        "action": "create", "title": "Entrevista fictícia",
        "start": "2026-10-23T16:00:00-03:00", "end": "2026-10-23T16:30:00-03:00",
    } | changes


def targeted_request(action="delete", **changes):
    result = {"action": action, "event_id": EVENT_ID}
    if action == "reschedule":
        result.update(start="2026-10-24T16:00:00-03:00", end="2026-10-24T16:30:00-03:00")
    elif action == "edit":
        result["location"] = "Sala fictícia"
    return result | changes


class RequestPreparationTests(unittest.TestCase):
    def setUp(self):
        self.event = agenda_event()
        self.gateway = Mock(name="read_only_gateway_without_io")
        self.gateway.get_event.return_value = EventLookup("Genius Testes", self.event)
        self.gateway.list_events.return_value = CalendarListing("Genius Testes", (self.event,))
        for method in ("create_event", "reschedule_event", "update_event_details", "delete_event",
                       "insert", "update", "patch", "delete"):
            getattr(self.gateway, method).side_effect = AssertionError("Preparar um pedido não pode gravar.")
        self.service = RequestPreparationService(self.gateway)
        guard = patch.object(google_calendar, "get_credentials", side_effect=AssertionError("Sem credenciais."))
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self._assert_no_mutations)

    def _assert_no_mutations(self):
        for method in ("create_event", "reschedule_event", "update_event_details", "delete_event",
                       "insert", "update", "patch", "delete"):
            getattr(self.gateway, method).assert_not_called()

    def assert_no_reads(self):
        self.gateway.get_event.assert_not_called()
        self.gateway.list_events.assert_not_called()

    def assert_question(self, result, status, reason):
        self.assertEqual(result.status, status)
        self.assertEqual(result.reason, reason)
        self.assertIsInstance(result.question, str)
        self.assertTrue(result.question.strip())
        self.assertIsNone(result.parameters)
        self.assertIsNone(result.event)
        self.assertIsInstance(result.missing_fields, tuple)
        self.assertIsInstance(result.invalid_fields, tuple)
        self.assertIsInstance(result.candidates, tuple)

    def test_missing_action_requests_clarification_without_calendar(self):
        for request in ({}, {"action": None}, {"action": "  "}):
            with self.subTest(request=request):
                result = self.service.prepare(request)
                self.assert_question(result, "needs_clarification", "missing_information")
                self.assertEqual(result.missing_fields, ("action",))
                self.assert_no_reads()

    def test_invalid_structure_or_action_cannot_become_an_executable_request(self):
        for request in (None, [], "create", {123: "value"}):
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    self.service.prepare(request)
                self.assert_no_reads()
        for action in (True, 123, "unknown", "CREATE"):
            with self.subTest(action=action):
                result = self.service.prepare({"action": action})
                self.assert_question(result, "needs_correction", "invalid_information")
                self.assertEqual(result.invalid_fields, ("action",))
                self.assert_no_reads()

    def test_creation_and_rescheduling_missing_data_is_collected_before_selection(self):
        cases = (
            ({"action": "create"}, {"title", "start", "end"}),
            ({"action": "create", "title": "  ", "start": None}, {"title", "start", "end"}),
            ({"action": "reschedule", "event_id": EVENT_ID}, {"start", "end"}),
            ({"action": "reschedule", "search_title": "entrevista"}, {"start", "end"}),
            ({"action": "delete"}, {"search_title"}),
            ({"action": "edit", "event_id": EVENT_ID, "title": None}, {"changes"}),
            ({"action": "list"}, {"date"}),
        )
        for request, missing in cases:
            with self.subTest(request=request):
                result = self.service.prepare(request)
                self.assert_question(result, "needs_clarification", "missing_information")
                self.assertEqual(set(result.missing_fields), missing)
                self.assertEqual(result.invalid_fields, ())
                self.assert_no_reads()

    def test_unknown_fields_and_write_or_confirmation_options_are_corrections_before_reads(self):
        for extra in ({"execute": True}, {"confirmation_token": "0" * 64}, {"operation_id": "request"},
                      {"attendees": []}, {"date": "2026-10-23"}):
            with self.subTest(extra=extra):
                result = self.service.prepare(create_request(**extra))
                self.assert_question(result, "needs_correction", "invalid_information")
                self.assertTrue(set(extra).issubset(result.invalid_fields))
                self.assert_no_reads()
        result = self.service.prepare(targeted_request(execute=True))
        self.assert_question(result, "needs_correction", "invalid_information")
        self.assertIn("execute", result.invalid_fields)
        self.assert_no_reads()

    def test_invalid_fields_and_missing_fields_are_reported_together_without_io(self):
        result = self.service.prepare({"action": "reschedule", "event_id": EVENT_ID, "start": 123})

        self.assert_question(result, "needs_correction", "invalid_information")
        self.assertEqual(result.invalid_fields, ("start",))
        self.assertEqual(result.missing_fields, ("end",))
        self.assert_no_reads()

    def test_invalid_create_and_reschedule_times_share_correction_behavior_before_reads(self):
        cases = (
            {"start": "2026-10-23T16:00:00"},
            {"start": "2026-10-23"},
            {"start": "2026-02-30T16:00:00-03:00"},
            {"start": datetime(2026, 10, 23, 16)},
            {"end": 123},
            {"end": "2026-10-23T15:00:00-03:00"},
            {"start": "2026-10-23T16:00:00Z", "end": "2026-10-23T16:30:00Z"},
            {"time_zone": 123}, {"time_zone": "Invalid/NoSuchZone"},
            {"start": "2026-03-08T02:15:00-05:00", "end": "2026-03-08T03:30:00-04:00",
             "time_zone": "America/New_York"},
            {"start": "0001-01-01T00:00:00+14:00", "end": "0001-01-01T01:00:00+14:00",
             "time_zone": "Pacific/Kiritimati"},
        )
        for changes in cases:
            for request in (create_request(**changes), targeted_request("reschedule", **changes)):
                with self.subTest(request=request):
                    result = self.service.prepare(request)
                    self.assert_question(result, "needs_correction", "invalid_information")
                    self.assertTrue(result.invalid_fields)
                    self.assert_no_reads()

    def test_ready_create_has_only_prepared_parameters_and_preserves_input(self):
        request = MappingProxyType(create_request(title="  Entrevista fictícia  ", description=None))
        previous = deepcopy(dict(request))

        result = RequestPreparationService().prepare(request)

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.action, "create")
        self.assertIsNone(result.reason)
        self.assertIsNone(result.question)
        self.assertIsNone(result.event)
        self.assertEqual(result.parameters, {
            "title": "Entrevista fictícia", "description": "", "time_zone": "America/Recife",
            "start": "2026-10-23T16:00:00-03:00", "end": "2026-10-23T16:30:00-03:00",
        })
        self.assertEqual(dict(request), previous)
        self.assert_no_reads()

    def test_configured_default_zone_explicit_override_and_both_dst_folds_are_accepted(self):
        service = RequestPreparationService(default_time_zone="America/New_York")
        for offset in ("-04:00", "-05:00"):
            with self.subTest(offset=offset):
                result = service.prepare(create_request(
                    start=f"2026-11-01T01:00:00{offset}", end=f"2026-11-01T01:30:00{offset}",
                ))
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.parameters["time_zone"], "America/New_York")
                self.assertTrue(result.parameters["start"].endswith(offset))
        result = service.prepare(create_request(start="2026-10-23T16:00:00Z", end="2026-10-23T16:30:00Z", time_zone="UTC"))
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.parameters["start"], "2026-10-23T16:00:00+00:00")
        with self.assertRaises(ValueError):
            RequestPreparationService(default_time_zone=123)
        self.assert_no_reads()

    def test_list_preparation_defaults_day_and_does_not_query_calendar(self):
        for period in (None, "week", "month"):
            with self.subTest(period=period):
                request = {"action": "list", "date": "2026-10-23"}
                if period is not None:
                    request["period"] = period
                result = RequestPreparationService().prepare(request)
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.parameters, {
                    "date": "2026-10-23", "period": period or "day", "time_zone": "America/Recife",
                })
                self.assertIsNone(result.event)
                self.assert_no_reads()

    def test_invalid_date_period_or_typed_text_is_a_correction_before_selection(self):
        cases = (
            ({"action": "list", "date": "2026-02-30"}, "date"),
            ({"action": "list", "date": "20261023"}, "date"),
            ({"action": "list", "date": date(2026, 10, 23)}, "date"),
            ({"action": "list", "date": "2026-10-23", "period": "year"}, "period"),
            ({"action": "list", "date": "9999-12-31", "period": "month"}, "date"),
            ({"action": "delete", "search_title": 123, "date": "2026-10-23"}, "search_title"),
            ({"action": "delete", "search_title": "\u0301", "date": "2026-10-23"}, "search_title"),
            (create_request(title=123), "title"),
            (create_request(description=[]), "description"),
        )
        for request, field in cases:
            with self.subTest(request=request):
                result = self.service.prepare(request)
                self.assert_question(result, "needs_correction", "invalid_information")
                self.assertIn(field, result.invalid_fields)
                self.assert_no_reads()

    def test_exact_id_cannot_be_combined_with_title_date_or_period(self):
        for extra in ({"search_title": "entrevista"}, {"date": "2026-10-23"}, {"period": "week"}):
            with self.subTest(extra=extra):
                result = self.service.prepare(targeted_request(**extra))
                self.assert_question(result, "needs_correction", "invalid_information")
                self.assertIn("event_id", result.invalid_fields)
                self.assertTrue(set(extra).issubset(result.invalid_fields))
                self.assert_no_reads()

    def test_targeted_ready_operations_preserve_exact_occurrence_id_without_writes(self):
        for action in ("reschedule", "edit", "delete"):
            with self.subTest(action=action):
                self.gateway.reset_mock()
                request = targeted_request(action)
                result = self.service.prepare(request)
                self.assertEqual(result.status, "ready")
                self.assertEqual(result.action, action)
                self.assertIs(result.event, self.event)
                self.assertEqual(result.parameters["event_id"], EVENT_ID)
                self.assertIsNone(result.question)
                self.assertEqual(result.candidates, ())
                self.gateway.get_event.assert_called_once_with(EVENT_ID, time_zone="America/Recife")
                self.gateway.list_events.assert_not_called()
                self.assertNotIn("execute", result.parameters)
                self.assertNotIn("confirmation_token", result.parameters)
        result = self.service.prepare(targeted_request())
        self.assertEqual(result.parameters, {"event_id": EVENT_ID, "time_zone": "America/Recife"})

    def test_unique_title_search_revalidates_id_and_uses_requested_period(self):
        request = {"action": "delete", "search_title": "  ENTREVÍSTA   TÉCNICA  ", "date": "2026-10-23", "period": "week"}

        result = self.service.prepare(request)

        self.assertEqual(result.status, "ready")
        self.assertIs(result.event, self.event)
        self.assertEqual(result.parameters["event_id"], EVENT_ID)
        self.gateway.get_event.assert_called_once_with(EVENT_ID, time_zone="America/Recife")
        period = self.gateway.list_events.call_args.args[0]
        self.assertEqual(period.start.isoformat(), "2026-10-19T00:00:00-03:00")
        self.assertEqual(period.end.isoformat(), "2026-10-26T00:00:00-03:00")

    def test_exact_and_substring_candidates_require_selection_without_ready_parameters(self):
        exact = agenda_event("ExactID", "Entrevista")
        another = agenda_event("OtherID", "Entrevista técnica")
        self.gateway.list_events.return_value = CalendarListing("Genius Testes", (exact, another))

        result = self.service.prepare({"action": "delete", "search_title": "entrevista", "date": "2026-10-23"})

        self.assert_question(result, "needs_clarification", "event_selection")
        self.assertEqual(result.candidates, (exact, another))
        self.gateway.get_event.assert_not_called()

    def test_no_match_or_missing_id_returns_question_without_fallback_or_parameters(self):
        self.gateway.list_events.return_value = CalendarListing("Genius Testes", ())
        result = self.service.prepare({"action": "delete", "search_title": "entrevista", "date": "2026-10-23"})
        self.assert_question(result, "needs_clarification", "event_not_found")
        self.gateway.get_event.assert_not_called()
        self.gateway.reset_mock()
        self.gateway.get_event.return_value = EventLookup("Genius Testes", None)
        result = self.service.prepare(targeted_request("reschedule"))
        self.assert_question(result, "needs_clarification", "event_not_found")
        self.gateway.get_event.assert_called_once_with(EVENT_ID, time_zone="America/Recife")
        self.gateway.list_events.assert_not_called()

    def test_event_changed_after_search_requires_clarification_and_current_candidate(self):
        current = replace(self.event, end="2026-10-23T17:00:00-03:00")
        self.gateway.get_event.return_value = EventLookup("Genius Testes", current)

        result = self.service.prepare({"action": "delete", "search_title": "entrevista", "date": "2026-10-23"})

        self.assert_question(result, "needs_clarification", "event_selection")
        self.assertEqual(result.candidates, (current,))
        self.gateway.list_events.assert_called_once()
        self.gateway.get_event.assert_called_once()

    def test_edit_preserves_none_omission_empty_clears_and_whitespace_in_content(self):
        request = targeted_request("edit", title=None, description="", location="  Sala fictícia  ")
        original = deepcopy(request)

        result = self.service.prepare(request)

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.parameters, {
            "event_id": EVENT_ID, "time_zone": "America/Recife", "description": "", "location": "  Sala fictícia  ",
        })
        self.assertNotIn("title", result.parameters)
        self.assertEqual(request, original)
        result.parameters["description"] = "Não deve modificar o pedido original."
        self.assertEqual(request["description"], "")
        self.gateway.reset_mock()
        result = self.service.prepare(targeted_request("edit", title="  Novo título  ", description=None, location=None))
        self.assertEqual(result.parameters, {"event_id": EVENT_ID, "time_zone": "America/Recife", "title": "Novo título"})

    def test_empty_or_wrongly_typed_edit_title_and_content_are_corrected_before_reads(self):
        for changes in ({"title": ""}, {"title": "  "}, {"description": []}, {"location": 123}):
            with self.subTest(changes=changes):
                result = self.service.prepare(targeted_request("edit", **changes))
                self.assert_question(result, "needs_correction", "invalid_information")
                self.assertTrue(set(changes).issubset(result.invalid_fields))
                self.assert_no_reads()

    def test_calendar_failure_propagates_instead_of_becoming_a_user_question(self):
        for failure in (CalendarOperationError("Falha de acesso simulada."), TimeoutError("Falha de rede simulada.")):
            with self.subTest(failure=type(failure).__name__):
                self.gateway.get_event.side_effect = failure
                with self.assertRaises(type(failure)):
                    self.service.prepare(targeted_request())
        self.gateway.get_event.side_effect = None
        self.gateway.list_events.side_effect = CalendarOperationError("Falha de listagem simulada.")
        with self.assertRaises(CalendarOperationError):
            self.service.prepare({"action": "delete", "search_title": "entrevista", "date": "2026-10-23"})

    def run_cli(self, payload, *, extra_arguments=(), raw=False):
        text = payload if raw else json.dumps(payload, ensure_ascii=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command.Path, "read_text", return_value=text) as read:
            with patch.object(command, "calendar_client") as client:
                with patch.object(command, "GoogleCalendarGateway", return_value=self.gateway) as factory:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        status = command.main(["--request-file", "fake-request.json", *extra_arguments])
        return status, stdout.getvalue(), stderr.getvalue(), client, factory, read

    def test_cli_missing_and_correction_results_never_open_credentials(self):
        for payload, expected in (({"action": "create"}, "needs_clarification"),
                                  (targeted_request("reschedule", start="invalid"), "needs_correction"),
                                  (targeted_request(execute=True), "needs_correction")):
            with self.subTest(payload=payload):
                status, stdout, stderr, client, factory, _ = self.run_cli(payload)
                self.assertEqual(status, 0)
                self.assertEqual(json.loads(stdout)["status"], expected)
                self.assertEqual(stderr, "")
                client.assert_not_called()
                factory.assert_not_called()
                self.assert_no_reads()

    def test_cli_ready_create_and_list_are_local_preparation_without_opening_google(self):
        for payload in (create_request(), {"action": "list", "date": "2026-10-23", "period": "month"}):
            with self.subTest(payload=payload):
                status, stdout, stderr, client, factory, read = self.run_cli(payload)
                self.assertEqual(status, 0)
                self.assertEqual(json.loads(stdout)["status"], "ready")
                self.assertEqual(stderr, "")
                client.assert_not_called()
                factory.assert_not_called()
                read.assert_called_once_with(encoding="utf-8-sig")
                self.assert_no_reads()

    def test_cli_complete_target_opens_one_client_for_list_and_id_revalidation(self):
        payload = {"action": "delete", "search_title": "entrevista", "date": "2026-10-23"}

        status, stdout, stderr, client, factory, _ = self.run_cli(payload)

        self.assertEqual(status, 0)
        response = json.loads(stdout)
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["parameters"]["event_id"], EVENT_ID)
        self.assertEqual(response["event"]["event_id"], EVENT_ID)
        self.assertEqual(stderr, "")
        client.assert_called_once_with()
        client.return_value.__enter__.assert_called_once()
        client.return_value.__exit__.assert_called_once()
        factory.assert_called_once_with(client.return_value.__enter__.return_value)
        self.gateway.list_events.assert_called_once()
        self.gateway.get_event.assert_called_once_with(EVENT_ID, time_zone="America/Recife")

    def test_cli_not_found_and_ambiguity_keep_questions_instead_of_successful_actions(self):
        for events, expected_reason in (((), "event_not_found"),
                                        ((self.event, agenda_event("OtherID")), "event_selection")):
            with self.subTest(events=events):
                self.gateway.reset_mock()
                self.gateway.list_events.return_value = CalendarListing("Genius Testes", events)
                status, stdout, stderr, _, _, _ = self.run_cli({
                    "action": "delete", "search_title": "entrevista", "date": "2026-10-23",
                })
                self.assertEqual(status, 0)
                response = json.loads(stdout)
                self.assertEqual(response["status"], "needs_clarification")
                self.assertEqual(response["reason"], expected_reason)
                self.assertIsNone(response["parameters"])
                self.assertTrue(response["question"])
                self.assertEqual(stderr, "")
                self.gateway.get_event.assert_not_called()

    def test_cli_invalid_json_duplicate_fields_or_wrong_structure_has_no_success_output(self):
        for content in ("{", "[]", '{"action":"create","action":"delete"}'):
            with self.subTest(content=content):
                status, stdout, stderr, client, factory, _ = self.run_cli(content, raw=True)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.strip())
                client.assert_not_called()
                factory.assert_not_called()

    def test_cli_google_errors_return_nonzero_without_ready_json(self):
        failures = (
            CalendarOperationError("Falha simulada."), TimeoutError("Rede indisponível."),
            HttpError(httplib2.Response({"status": "403", "reason": "Fake denied"}), b"{}"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.gateway.get_event.side_effect = failure
                status, stdout, stderr, client, _, _ = self.run_cli(targeted_request())
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.strip())
                client.return_value.__exit__.assert_called_once()

    def test_cli_argument_and_file_errors_do_not_open_calendar(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as error:
                    command.main([])
            self.assertEqual(error.exception.code, 2)
            client.assert_not_called()
        with patch.object(command.Path, "read_text", side_effect=OSError("Arquivo indisponível.")):
            with patch.object(command, "calendar_client") as client:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = command.main(["--request-file", "missing.json"])
                self.assertEqual(status, 1)
                client.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertTrue(stderr.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
