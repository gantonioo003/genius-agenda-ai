"""Consulta diária com cliente falso, sem rede, credenciais ou banco real."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httplib2
from googleapiclient.errors import HttpError

from app import google_calendar
from app import list_events as command
from app.services.agenda import (
    AgendaEvent,
    AgendaService,
    CalendarListing,
    day_period,
)
from app.services.interviews import CalendarOperationError


def google_error(status: int) -> HttpError:
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake Google error"}),
        b'{"error":{"message":"Fake Google error"}}',
    )


def timed_event(event_id, start, end, **changes):
    event = {
        "id": event_id,
        "summary": "Evento " + event_id,
        "status": "confirmed",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "htmlLink": "https://example.invalid/event/" + event_id,
    }
    event.update(changes)
    return event


def all_day_event(event_id, start, end):
    return {
        "id": event_id,
        "summary": "Dia inteiro " + event_id,
        "status": "confirmed",
        "start": {"date": start},
        "end": {"date": end},
    }


class DailyQueryTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 10, 22)
        self.period = day_period(self.day, "America/Recife")
        self.calendar = {
            "id": "genius-test@example.invalid",
            "summary": "Genius Testes",
            "timeZone": "America/Recife",
            "accessRole": "owner",
            "primary": False,
        }
        self.settings = SimpleNamespace(
            google_calendar_name="Genius Testes", google_calendar_id=""
        )
        self.database = Mock(name="database_without_io")
        self.database.get_metadata.return_value = None
        for target, value in (("settings", self.settings), ("database", self.database)):
            patcher = patch.object(google_calendar, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        credentials = patch.object(
            google_calendar,
            "get_credentials",
            side_effect=AssertionError("O teste não deve acessar credenciais."),
        )
        credentials.start()
        self.addCleanup(credentials.stop)
        self.client = Mock(name="fake_google_client")
        self.client.calendarList.return_value.list.return_value.execute.return_value = {
            "items": [self.calendar]
        }
        self.events = self.client.events.return_value
        self.request = self.events.list.return_value
        self.request.execute.return_value = {"items": []}
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        self.addCleanup(self._assert_read_only)

    def _assert_read_only(self):
        for method in ("insert", "update", "patch", "delete"):
            getattr(self.events, method).assert_not_called()

    def test_recife_day_has_local_midnight_bounds_with_offsets(self):
        self.assertEqual(self.period.start.isoformat(), "2026-10-22T00:00:00-03:00")
        self.assertEqual(self.period.end.isoformat(), "2026-10-23T00:00:00-03:00")
        self.assertEqual(self.period.time_zone, "America/Recife")
        self.assertEqual(self.period.start.astimezone(timezone.utc).hour, 3)

    def test_dst_days_use_local_dates_and_have_23_or_25_hours(self):
        cases = (
            (date(2026, 3, 8), "2026-03-08T00:00:00-05:00", "2026-03-09T00:00:00-04:00", 23),
            (date(2026, 11, 1), "2026-11-01T00:00:00-04:00", "2026-11-02T00:00:00-05:00", 25),
        )
        for day, start, end, hours in cases:
            with self.subTest(day=day):
                period = day_period(day, "America/New_York")
                self.assertEqual(period.start.isoformat(), start)
                self.assertEqual(period.end.isoformat(), end)
                duration = period.end.astimezone(timezone.utc) - period.start.astimezone(timezone.utc)
                self.assertEqual(duration.total_seconds(), hours * 3600)

    def test_service_uses_injected_gateway_and_preserves_listing(self):
        event = AgendaEvent(
            "fake-id", "Entrevista", "2026-10-22T15:00:00-03:00",
            "2026-10-22T15:30:00-03:00", False, "America/Recife", "confirmed",
        )
        gateway = Mock()
        gateway.list_events.return_value = CalendarListing("Genius Testes", (event,))

        result = AgendaService(gateway).list_day(self.day)

        self.assertEqual(result.date, "2026-10-22")
        self.assertEqual(result.time_zone, "America/Recife")
        self.assertEqual(result.calendar_name, "Genius Testes")
        self.assertEqual(result.events, (event,))
        gateway.list_events.assert_called_once_with(self.period)
        self.client.calendarList.assert_not_called()
        self.database.initialize.assert_not_called()

    def test_invalid_date_zone_and_period_are_rejected_before_io(self):
        cases = (
            ("2026-10-22", "America/Recife"),
            (datetime(2026, 10, 22), "America/Recife"),
            (date.max, "America/Recife"),
            (self.day, "Invalid/NoSuchZone"),
            (self.day, ""),
            (self.day, " America/Recife"),
            (date(2011, 12, 30), "Pacific/Apia"),
        )
        for day, zone in cases:
            with self.subTest(day=day, zone=zone):
                gateway = Mock()
                with self.assertRaises(ValueError):
                    AgendaService(gateway).list_day(day, time_zone=zone)
                gateway.list_events.assert_not_called()
        periods = (
            replace(self.period, start=self.period.start.replace(tzinfo=None)),
            replace(self.period, end=self.period.end.replace(tzinfo=None)),
            replace(self.period, end=self.period.start),
            replace(self.period, time_zone="Invalid/NoSuchZone"),
        )
        for period in periods:
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    self.gateway.list_events(period)
                self.client.calendarList.assert_not_called()
                self.client.events.assert_not_called()

    def test_exclusive_boundaries_keep_events_crossing_midnight(self):
        self.request.execute.return_value = {"items": [
            timed_event("ends-at-start", "2026-10-21T23:00:00-03:00", "2026-10-22T00:00:00-03:00"),
            timed_event("starts-at-end", "2026-10-23T00:00:00-03:00", "2026-10-23T01:00:00-03:00"),
            timed_event("cross-in", "2026-10-21T23:30:00-03:00", "2026-10-22T00:30:00-03:00"),
            timed_event("inside", "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00"),
            timed_event("cross-out", "2026-10-22T23:30:00-03:00", "2026-10-23T00:30:00-03:00"),
            timed_event("spans", "2026-10-21T10:00:00-03:00", "2026-10-23T10:00:00-03:00"),
        ]}

        result = self.gateway.list_events(self.period)

        self.assertEqual([event.event_id for event in result.events], [
            "spans", "cross-in", "inside", "cross-out"
        ])
        self.assertEqual(result.events[1].start, "2026-10-21T23:30:00-03:00")
        self.assertEqual(result.events[-1].end, "2026-10-23T00:30:00-03:00")

    def test_all_day_events_keep_date_semantics_and_exclusive_end(self):
        self.request.execute.return_value = {"items": [
            all_day_event("previous", "2026-10-21", "2026-10-22"),
            all_day_event("current", "2026-10-22", "2026-10-23"),
            all_day_event("next", "2026-10-23", "2026-10-24"),
            all_day_event("spans", "2026-10-21", "2026-10-24"),
        ]}

        result = self.gateway.list_events(self.period)

        self.assertEqual([event.event_id for event in result.events], ["spans", "current"])
        event = result.events[1]
        self.assertTrue(event.all_day)
        self.assertEqual(event.start, "2026-10-22")
        self.assertEqual(event.end, "2026-10-23")
        self.assertEqual(event.time_zone, "America/Recife")

    def test_all_day_filter_uses_calendar_zone_when_query_zone_differs(self):
        self.calendar["timeZone"] = "America/New_York"
        self.request.execute.return_value = {"items": [
            all_day_event("overlaps-from-previous", "2026-10-21", "2026-10-22"),
            all_day_event("current", "2026-10-22", "2026-10-23"),
            all_day_event("next", "2026-10-23", "2026-10-24"),
        ]}

        result = self.gateway.list_events(day_period(self.day, "UTC"))

        self.assertEqual([event.event_id for event in result.events], [
            "overlaps-from-previous", "current"
        ])
        previous = result.events[0]
        self.assertEqual(previous.start, "2026-10-21")
        self.assertEqual(previous.end, "2026-10-22")
        self.assertTrue(previous.all_day)
        self.assertEqual(previous.time_zone, "America/New_York")

    def test_cancelled_tombstone_is_ignored_and_tentative_event_is_kept(self):
        provisional = timed_event(
            "provisional", "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00",
            status="tentative", summary=None,
            attendees=[{"self": True, "responseStatus": "needsAction"}],
        )
        self.request.execute.return_value = {"items": [
            {"id": "cancelled-without-dates", "status": "cancelled"}, provisional
        ]}

        result = self.gateway.list_events(self.period)

        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].event_id, "provisional")
        self.assertEqual(result.events[0].status, "tentative")
        self.assertEqual(result.events[0].title, "(sem título)")

    def test_pagination_continues_after_empty_page_with_correct_query(self):
        event = timed_event("interview", "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00")
        self.request.execute.side_effect = [
            {"items": [], "nextPageToken": "second"},
            {"items": [event], "nextPageToken": "third"},
            {"items": []},
        ]

        result = self.gateway.list_events(self.period)

        self.assertEqual([event.event_id for event in result.events], ["interview"])
        calls = self.events.list.call_args_list
        self.assertEqual([call.kwargs["pageToken"] for call in calls], [None, "second", "third"])
        for call in calls:
            values = call.kwargs
            self.assertEqual(values["calendarId"], self.calendar["id"])
            self.assertEqual(values["timeMin"], "2026-10-22T00:00:00-03:00")
            self.assertEqual(values["timeMax"], "2026-10-23T00:00:00-03:00")
            self.assertEqual(values["timeZone"], "America/Recife")
            self.assertTrue(values["singleEvents"])
            self.assertFalse(values["showDeleted"])
            self.assertEqual(values["orderBy"], "startTime")
        self.assertEqual(self.request.execute.call_count, 3)

    def test_second_page_error_never_returns_a_partial_success(self):
        event = timed_event("interview", "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00")
        for failure in (google_error(503), TimeoutError("Simulated network failure")):
            with self.subTest(failure=type(failure).__name__):
                self.request.execute.side_effect = [
                    {"items": [event], "nextPageToken": "second"}, failure
                ]
                with self.assertRaises(CalendarOperationError):
                    self.gateway.list_events(self.period)

    def test_real_selection_rejects_primary_nonowner_and_missing_pinned_id(self):
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
                    self.gateway.list_events(self.period)
                self.client.events.assert_not_called()

    def test_timed_results_are_normalized_sorted_and_deduplicated(self):
        early = timed_event("early", "2026-10-22T12:00:00Z", "2026-10-22T12:30:00Z")
        late = timed_event("late", "2026-10-22T20:00:00+02:00", "2026-10-22T20:30:00+02:00")
        duplicate = timed_event(
            "duplicate", "2026-10-22T14:00:00Z", "2026-10-22T14:30:00Z", summary="Versão atual"
        )
        revised = dict(duplicate)  # Mesma versão repetida entre páginas.
        self.request.execute.side_effect = [
            {"items": [late, duplicate], "nextPageToken": "second"},
            {"items": [early, revised]},
        ]

        result = self.gateway.list_events(self.period)

        self.assertEqual([event.event_id for event in result.events], ["early", "duplicate", "late"])
        self.assertEqual(result.events[0].start, "2026-10-22T09:00:00-03:00")
        self.assertEqual(result.events[1].start, "2026-10-22T11:00:00-03:00")
        self.assertEqual(result.events[1].title, "Versão atual")
        self.assertEqual(result.events[2].start, "2026-10-22T15:00:00-03:00")
        self.assertTrue(all(not event.all_day for event in result.events))
        self.assertTrue(all(event.time_zone == "America/Recife" for event in result.events))

    def test_malformed_event_data_is_an_error_instead_of_an_empty_agenda(self):
        valid = timed_event("interview", "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00")
        invalid_events = (
            dict(deepcopy(valid), id=""),
            dict(deepcopy(valid), start={"dateTime": "2026-10-22T15:00:00"}),
            dict(deepcopy(valid), end={"dateTime": "2026-10-22T14:30:00-03:00"}),
            dict(deepcopy(valid), start={"dateTime": "not-a-timestamp"}),
            dict(deepcopy(valid), start={"date": "2026-10-22"}),
            all_day_event("invalid-dates", "2026-10-22", "2026-10-22"),
        )
        for event in invalid_events:
            with self.subTest(event=event):
                self.request.execute.return_value = {"items": [event]}
                with self.assertRaises(CalendarOperationError):
                    self.gateway.list_events(self.period)

    def test_repeated_page_token_stops_with_error_not_partial_listing(self):
        self.request.execute.side_effect = [
            {"items": [], "nextPageToken": "same-token"},
            {"items": [], "nextPageToken": "same-token"},
        ]

        with self.assertRaisesRegex(CalendarOperationError, "repetiu uma página"):
            self.gateway.list_events(self.period)

        self.assertEqual(self.request.execute.call_count, 2)

    def test_cli_invalid_date_and_zone_never_open_google_client(self):
        bad_dates = ("2026-02-30", "20261022", "2026-W43-4", "not-a-date")
        for value in bad_dates:
            with self.subTest(date=value):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(command, "calendar_client") as client:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        with self.assertRaises(SystemExit) as error:
                            command.main(["--date", value])
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("AAAA-MM-DD", stderr.getvalue())
                client.assert_not_called()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = command.main(["--date", "2026-10-22", "--timezone", "Invalid/NoSuchZone"])
        self.assertEqual(result, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Fuso horário inválido", stderr.getvalue())
        client.assert_not_called()

    def test_cli_distinguishes_successfully_empty_agenda_from_query_failure(self):
        for failure in (None, google_error(403), TimeoutError("Simulated timeout")):
            with self.subTest(failure=None if failure is None else type(failure).__name__):
                self.request.execute.side_effect = failure
                self.request.execute.return_value = {"items": []}
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(command, "calendar_client") as client:
                    client.return_value.__enter__.return_value = self.client
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = command.main(["--date", "2026-10-22", "--timezone", "America/Recife"])
                if failure is None:
                    self.assertEqual(result, 0)
                    response = json.loads(stdout.getvalue())
                    self.assertEqual(response["status"], "ok")
                    self.assertEqual(response["count"], 0)
                    self.assertEqual(response["events"], [])
                    self.assertEqual(response["date"], "2026-10-22")
                    self.assertEqual(response["time_zone"], "America/Recife")
                    self.assertEqual(response["calendar_name"], "Genius Testes")
                    self.assertEqual(stderr.getvalue(), "")
                else:
                    self.assertEqual(result, 1)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertTrue(stderr.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
