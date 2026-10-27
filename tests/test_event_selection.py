"""Identificação segura de eventos usando somente clientes falsos."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import date
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httplib2
from googleapiclient.errors import HttpError

from app import google_calendar
from app import select_event as command
from app.services.agenda import AgendaEvent, CalendarListing, day_period
from app.services.event_selection import (
    EventLookup,
    EventSelectionService,
)
from app.services.interviews import CalendarOperationError


def agenda_event(event_id="TargetID", title="Entrevista de estágio", **changes):
    event = AgendaEvent(
        event_id, title, "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00",
        False, "America/Recife", "confirmed", "https://example.invalid/event",
    )
    return replace(event, **changes)


def raw_event(event_id="TargetID", title="Entrevista de estágio", **changes):
    event = {
        "id": event_id, "summary": title, "status": "confirmed",
        "start": {"dateTime": "2026-10-22T18:00:00Z"},
        "end": {"dateTime": "2026-10-22T18:30:00Z"},
        "htmlLink": "https://example.invalid/event",
    }
    event.update(changes)
    return event


def google_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake Google error"}),
        b'{"error":{"message":"Fake Google error"}}',
    )


class EventSelectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.period = day_period(date(2026, 10, 22), "America/Recife")
        self.event = agenda_event()
        self.gateway = Mock(name="injected_gateway_without_io")
        self.gateway.list_events.return_value = CalendarListing("Genius Testes", (self.event,))
        self.gateway.get_event.return_value = EventLookup("Genius Testes", self.event)
        self.service = EventSelectionService(self.gateway)

    def test_unique_normalized_title_is_revalidated_by_get_before_selected(self):
        result = self.service.select(title="  ENTREVÍSTA   DE ESTÁGIO  ", period=self.period)

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.event, self.event)
        self.assertEqual(result.candidates, ())
        self.assertIsNone(result.question)
        self.gateway.list_events.assert_called_once_with(self.period)
        self.gateway.get_event.assert_called_once_with("TargetID", time_zone="America/Recife")

    def test_exact_title_and_substring_match_still_require_clarification(self):
        exact = agenda_event("ExactID", "Entrevista")
        another = agenda_event("AnotherID", "Entrevista técnica")
        self.gateway.list_events.return_value = CalendarListing("Genius Testes", (exact, another))

        result = self.service.select(title="entrevista", period=self.period)

        self.assertEqual(result.status, "needs_clarification")
        self.assertIsNone(result.event)
        self.assertEqual([event.event_id for event in result.candidates], ["ExactID", "AnotherID"])
        self.assertTrue(result.question)
        self.gateway.get_event.assert_not_called()

    def test_empty_or_unmatched_listing_returns_not_found_and_a_question(self):
        for events in ((), (agenda_event(title="Consulta diferente"),),
                       (agenda_event(status="cancelled"),)):
            with self.subTest(events=events):
                self.gateway.list_events.return_value = CalendarListing("Genius Testes", events)
                result = self.service.select(title="entrevista", period=self.period)
                self.assertEqual(result.status, "not_found")
                self.assertIsNone(result.event)
                self.assertEqual(result.candidates, ())
                self.assertTrue(result.question)
                self.gateway.get_event.assert_not_called()

    def test_explicit_and_recurring_instance_ids_are_preserved_without_listing(self):
        for event_id in ("AbC123", "AbC123_20261022T180000Z"):
            with self.subTest(event_id=event_id):
                self.gateway.reset_mock()
                event = agenda_event(event_id)
                self.gateway.get_event.return_value = EventLookup("Genius Testes", event)
                result = self.service.select(event_id=event_id)
                self.assertEqual(result.status, "selected")
                self.assertEqual(result.event.event_id, event_id)
                self.gateway.get_event.assert_called_once_with(event_id, time_zone="America/Recife")
                self.gateway.list_events.assert_not_called()

    def test_missing_or_cancelled_id_is_not_found_without_title_fallback(self):
        for event in (None, agenda_event(status="cancelled")):
            with self.subTest(event=event):
                self.gateway.get_event.return_value = EventLookup("Genius Testes", event)
                result = self.service.select(event_id="TargetID")
                self.assertEqual(result.status, "not_found")
                self.assertIsNone(result.event)
                self.assertTrue(result.question)
                self.gateway.list_events.assert_not_called()

    def test_event_changed_between_list_and_get_requires_new_clarification(self):
        changes = (
            {"title": "Outro compromisso"},
            {"start": "2026-10-23T15:00:00-03:00", "end": "2026-10-23T15:30:00-03:00"},
            {"end": "2026-10-22T16:00:00-03:00"},
            {"status": "tentative"},
            {"start": "2026-10-22", "end": "2026-10-23", "all_day": True},
        )
        for change in changes:
            with self.subTest(change=change):
                current = replace(self.event, **change)
                self.gateway.get_event.return_value = EventLookup("Genius Testes", current)
                result = self.service.select(title="entrevista", period=self.period)
                self.assertEqual(result.status, "needs_clarification")
                self.assertIsNone(result.event)
                self.assertEqual(result.candidates, (current,))
                self.assertTrue(result.question)
        self.gateway.get_event.return_value = EventLookup("Genius Testes", None)
        result = self.service.select(title="entrevista", period=self.period)
        self.assertEqual(result.status, "not_found")
        self.assertTrue(result.question)

    def test_mismatched_returned_id_or_unknown_status_is_an_operational_error(self):
        for event in (agenda_event("WrongID"), agenda_event(status="unknown"), agenda_event(status="")):
            with self.subTest(event=event):
                self.gateway.get_event.return_value = EventLookup("Genius Testes", event)
                with self.assertRaises(CalendarOperationError):
                    self.service.select(event_id="TargetID")
                self.gateway.list_events.assert_not_called()

    def test_invalid_selection_is_rejected_before_any_gateway_call(self):
        cases = (
            {},
            {"title": "", "period": self.period},
            {"title": "  ", "period": self.period},
            {"title": "\u0301", "period": self.period},
            {"title": "entrevista", "period": self.period, "time_zone": "UTC"},
            {"event_id": "TargetID", "title": "entrevista"},
            {"event_id": "TargetID", "period": self.period},
            {"event_id": ""},
            {"event_id": "with space"},
            {"event_id": "hidden\u200bcharacter"},
            {"event_id": "x" * 1025},
            {"event_id": "TargetID", "time_zone": "Invalid/NoSuchZone"},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.service.select(**arguments)
                self.gateway.list_events.assert_not_called()
                self.gateway.get_event.assert_not_called()


class EventSelectionGoogleTests(unittest.TestCase):
    def setUp(self):
        self.calendar = {
            "id": "genius-test@example.invalid", "summary": "Genius Testes",
            "timeZone": "America/Recife", "accessRole": "owner", "primary": False,
        }
        self.settings = SimpleNamespace(google_calendar_name="Genius Testes", google_calendar_id="")
        self.database = Mock(name="database_without_io")
        self.database.get_metadata.return_value = None
        for target, value in (("settings", self.settings), ("database", self.database)):
            patcher = patch.object(google_calendar, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        guard = patch.object(
            google_calendar, "get_credentials",
            side_effect=AssertionError("O teste não deve acessar credenciais."),
        )
        guard.start()
        self.addCleanup(guard.stop)
        self.client = Mock(name="fake_google_client")
        self.client.calendarList.return_value.list.return_value.execute.return_value = {
            "items": [self.calendar]
        }
        self.events = self.client.events.return_value
        for method in ("insert", "update", "patch", "delete"):
            getattr(self.events, method).side_effect = AssertionError(
                "Identificar um evento não pode modificar o Google Calendar."
            )
        self.get_request = self.events.get.return_value
        self.get_request.execute.return_value = raw_event()
        self.events.list.return_value.execute.return_value = {"items": [raw_event()]}
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        self.addCleanup(self._assert_read_only)

    def _assert_read_only(self):
        for method in ("insert", "update", "patch", "delete"):
            getattr(self.events, method).assert_not_called()

    def test_get_uses_safe_calendar_minimal_fields_read_retries_and_exact_instance_id(self):
        event_id = "AbC123_20261022T180000Z"
        self.get_request.execute.return_value = raw_event(event_id)

        lookup = self.gateway.get_event(event_id, time_zone="America/Recife")

        self.assertEqual(lookup.calendar_name, "Genius Testes")
        self.assertEqual(lookup.event.event_id, event_id)
        self.assertEqual(lookup.event.start, "2026-10-22T15:00:00-03:00")
        self.assertEqual(lookup.event.end, "2026-10-22T15:30:00-03:00")
        self.events.get.assert_called_once_with(
            calendarId="genius-test@example.invalid", eventId=event_id,
            timeZone="America/Recife", fields="id,summary,status,start,end,htmlLink",
        )
        self.get_request.execute.assert_called_once_with(num_retries=2)
        self.events.list.assert_not_called()

    def test_get_rejects_unsafe_calendar_and_missing_configured_or_saved_id(self):
        cases = (
            (dict(self.calendar, primary=True), "", None),
            (dict(self.calendar, accessRole="writer"), "", None),
            (self.calendar, "missing-configured@example.invalid", None),
            (self.calendar, "", "missing-saved@example.invalid"),
        )
        for calendar, configured_id, saved_id in cases:
            with self.subTest(calendar=calendar, configured_id=configured_id, saved_id=saved_id):
                self.client.reset_mock()
                self.settings.google_calendar_id = configured_id
                self.database.get_metadata.return_value = saved_id
                self.client.calendarList.return_value.list.return_value.execute.return_value = {
                    "items": [calendar]
                }
                with self.assertRaises(google_calendar.CalendarSetupError):
                    self.gateway.get_event("TargetID")
                self.client.events.assert_not_called()

    def test_404_410_and_cancelled_tombstone_are_not_found_without_fallback(self):
        for response in (google_error(404), google_error(410), {"id": "TargetID", "status": "cancelled"}):
            with self.subTest(response=response):
                self.client.reset_mock()
                self.get_request.execute.side_effect = response if isinstance(response, Exception) else None
                self.get_request.execute.return_value = response
                result = EventSelectionService(self.gateway).select(event_id="TargetID")
                self.assertEqual(result.status, "not_found")
                self.assertIsNone(result.event)
                self.assertTrue(result.question)
                self.events.get.assert_called_once()
                self.events.list.assert_not_called()

    def test_get_permission_and_network_errors_are_not_treated_as_missing(self):
        for failure in (google_error(403), TimeoutError("Simulated timeout")):
            with self.subTest(failure=type(failure).__name__):
                self.get_request.execute.side_effect = failure
                with self.assertRaises(CalendarOperationError):
                    EventSelectionService(self.gateway).select(event_id="TargetID")
                self.events.list.assert_not_called()

    def test_malformed_identity_status_and_dates_produce_controlled_error(self):
        cases = (
            None,
            [],
            {},
            raw_event("DifferentID"),
            raw_event(status="unknown"),
            raw_event(status=""),
            raw_event(summary=["invalid", "title"]),
            raw_event(start={"dateTime": "2026-10-22T15:00:00"}),
            raw_event(end={"dateTime": "2026-10-22T14:00:00-03:00"}),
        )
        for response in cases:
            with self.subTest(response=response):
                self.get_request.execute.return_value = response
                with self.assertRaises(CalendarOperationError):
                    self.gateway.get_event("TargetID")
                self.events.list.assert_not_called()

    def test_get_all_day_keeps_exclusive_dates_and_calendar_timezone(self):
        self.calendar["timeZone"] = "America/New_York"
        self.get_request.execute.return_value = raw_event(
            start={"date": "2026-10-22"}, end={"date": "2026-10-24"}, status="tentative",
        )

        lookup = self.gateway.get_event("TargetID", time_zone="UTC")

        self.assertTrue(lookup.event.all_day)
        self.assertEqual(lookup.event.start, "2026-10-22")
        self.assertEqual(lookup.event.end, "2026-10-24")
        self.assertEqual(lookup.event.time_zone, "America/New_York")
        self.assertEqual(lookup.event.status, "tentative")

    def test_cli_selected_id_reports_event_without_listing_or_mutating(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            client.return_value.__enter__.return_value = self.client
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = command.main(["--event-id", "TargetID"])

        self.assertEqual(result, 0)
        response = json.loads(stdout.getvalue())
        self.assertEqual(response["status"], "selected")
        self.assertEqual(response["calendar_name"], "Genius Testes")
        self.assertEqual(response["event"]["event_id"], "TargetID")
        self.assertIsNone(response["question"])
        self.assertEqual(stderr.getvalue(), "")
        self.events.list.assert_not_called()

    def test_cli_title_distinguishes_selected_ambiguous_and_not_found_results(self):
        cases = (
            ([raw_event()], "selected", 0),
            ([raw_event(), raw_event("SecondID", "Entrevista técnica")], "needs_clarification", 2),
            ([], "not_found", 0),
        )
        for events, status, candidate_count in cases:
            with self.subTest(status=status):
                self.client.reset_mock()
                self.events.list.return_value.execute.return_value = {"items": deepcopy(events)}
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(command, "calendar_client") as client:
                    client.return_value.__enter__.return_value = self.client
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = command.main([
                            "--title", "entrevista", "--date", "2026-10-22", "--period", "week"
                        ])
                self.assertEqual(result, 0)
                response = json.loads(stdout.getvalue())
                self.assertEqual(response["status"], status)
                self.assertEqual(len(response["candidates"]), candidate_count)
                self.assertEqual(stderr.getvalue(), "")
                query = self.events.list.call_args.kwargs
                self.assertEqual(query["timeMin"], "2026-10-19T00:00:00-03:00")
                self.assertEqual(query["timeMax"], "2026-10-26T00:00:00-03:00")
                if status == "selected":
                    self.assertEqual(response["event"]["event_id"], "TargetID")
                    self.events.get.assert_called_once()
                else:
                    self.assertIsNone(response["event"])
                    self.assertTrue(response["question"])
                    self.events.get.assert_not_called()

    def test_cli_operational_error_has_nonzero_exit_without_selection_json(self):
        for failure in (google_error(403), TimeoutError("Simulated network failure")):
            with self.subTest(failure=type(failure).__name__):
                self.get_request.execute.side_effect = failure
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(command, "calendar_client") as client:
                    client.return_value.__enter__.return_value = self.client
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = command.main(["--event-id", "TargetID"])
                self.assertEqual(result, 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertTrue(stderr.getvalue().strip())
                self.events.list.assert_not_called()

    def test_cli_invalid_inputs_are_rejected_before_opening_client(self):
        cases = (
            ([], 2),
            (["--event-id", "TargetID", "--title", "entrevista"], 2),
            (["--title", "entrevista"], 1),
            (["--title", "", "--date", "2026-10-22"], 1),
            (["--title", "entrevista", "--date", "2026-02-30"], 2),
            (["--event-id", "TargetID", "--date", "2026-10-22"], 1),
            (["--event-id", "TargetID", "--period", "week"], 1),
            (["--event-id", "TargetID", "--timezone", "Invalid/NoSuchZone"], 1),
            (["--event-id", "with space"], 1),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(command, "calendar_client") as client:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        if expected == 2:
                            with self.assertRaises(SystemExit) as error:
                                command.main(arguments)
                            result = error.exception.code
                        else:
                            result = command.main(arguments)
                    client.assert_not_called()
                self.assertEqual(result, expected)
                self.assertEqual(stdout.getvalue(), "")
                self.assertTrue(stderr.getvalue().strip())
                self.client.events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
