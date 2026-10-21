"""Semanas e meses civis, serviços injetados e CLI com Google falso."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
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
    PeriodAgenda,
    month_period,
    week_period,
)
from app.services.interviews import CalendarOperationError


class PeriodBoundaryTests(unittest.TestCase):
    def test_week_runs_from_monday_to_exclusive_next_monday(self):
        for reference in (date(2026, 10, 19), date(2026, 10, 22), date(2026, 10, 25)):
            with self.subTest(reference=reference):
                period = week_period(reference, "America/Recife")
                self.assertEqual(period.start.isoformat(), "2026-10-19T00:00:00-03:00")
                self.assertEqual(period.end.isoformat(), "2026-10-26T00:00:00-03:00")
        following = week_period(date(2026, 10, 26), "America/Recife")
        self.assertEqual(following.start.date(), date(2026, 10, 26))
        self.assertEqual(following.end.date(), date(2026, 11, 2))

    def test_week_boundaries_cross_calendar_years(self):
        cases = (
            (date(2026, 1, 1), "2025-12-29", "2026-01-05"),
            (date(2026, 12, 31), "2026-12-28", "2027-01-04"),
            (date(2027, 1, 3), "2026-12-28", "2027-01-04"),
        )
        for reference, expected_start, expected_end in cases:
            with self.subTest(reference=reference):
                period = week_period(reference, "America/Recife")
                self.assertEqual(period.start.date().isoformat(), expected_start)
                self.assertEqual(period.end.date().isoformat(), expected_end)

    def test_month_uses_actual_february_december_and_last_day_boundaries(self):
        cases = (
            (date(2026, 2, 28), "2026-02-01", "2026-03-01", 28),
            (date(2024, 2, 29), "2024-02-01", "2024-03-01", 29),
            (date(2026, 12, 31), "2026-12-01", "2027-01-01", 31),
            (date(2026, 10, 31), "2026-10-01", "2026-11-01", 31),
            (date(2026, 4, 30), "2026-04-01", "2026-05-01", 30),
        )
        for reference, expected_start, expected_end, days in cases:
            with self.subTest(reference=reference):
                period = month_period(reference, "America/Recife")
                self.assertEqual(period.start.date().isoformat(), expected_start)
                self.assertEqual(period.end.date().isoformat(), expected_end)
                self.assertEqual((period.end.date() - period.start.date()).days, days)
                self.assertEqual(period.start.hour, 0)
                self.assertEqual(period.end.hour, 0)

    def test_dst_weeks_have_167_or_169_elapsed_hours(self):
        cases = (
            (date(2026, 3, 8), "2026-03-02T00:00:00-05:00", "2026-03-09T00:00:00-04:00", 167),
            (date(2026, 11, 1), "2026-10-26T00:00:00-04:00", "2026-11-02T00:00:00-05:00", 169),
        )
        for reference, start, end, hours in cases:
            with self.subTest(reference=reference):
                period = week_period(reference, "America/New_York")
                self.assertEqual(period.start.isoformat(), start)
                self.assertEqual(period.end.isoformat(), end)
                elapsed = period.end.astimezone(timezone.utc) - period.start.astimezone(timezone.utc)
                self.assertEqual(elapsed.total_seconds(), hours * 3600)

    def test_dst_months_preserve_civil_boundaries_and_variable_elapsed_hours(self):
        cases = (
            (date(2026, 3, 22), "2026-03-01T00:00:00-05:00", "2026-04-01T00:00:00-04:00", 743),
            (date(2026, 11, 22), "2026-11-01T00:00:00-04:00", "2026-12-01T00:00:00-05:00", 721),
        )
        for reference, start, end, hours in cases:
            with self.subTest(reference=reference):
                period = month_period(reference, "America/New_York")
                self.assertEqual(period.start.isoformat(), start)
                self.assertEqual(period.end.isoformat(), end)
                elapsed = period.end.astimezone(timezone.utc) - period.start.astimezone(timezone.utc)
                self.assertEqual(elapsed.total_seconds(), hours * 3600)

    def test_invalid_reference_zone_and_overflow_never_call_gateway(self):
        cases = (
            ("2026-10-22", "America/Recife"),
            (datetime(2026, 10, 22), "America/Recife"),
            (date.max, "America/Recife"),
            (date(2026, 10, 22), "Invalid/NoSuchZone"),
            (date(2026, 10, 22), ""),
        )
        for method_name in ("list_week", "list_month"):
            for reference, zone in cases:
                with self.subTest(method=method_name, reference=reference, zone=zone):
                    gateway = Mock()
                    service = AgendaService(gateway)
                    with self.assertRaises(ValueError):
                        getattr(service, method_name)(reference, time_zone=zone)
                    gateway.list_events.assert_not_called()
        gateway = Mock()
        with self.assertRaises(ValueError):
            AgendaService(gateway).list_month(date(9999, 12, 1))
        gateway.list_events.assert_not_called()

    def test_injected_service_preserves_events_and_explicit_period_contract(self):
        events = (
            AgendaEvent(
                "overnight", "Entrevista extensa", "2026-10-18T23:30:00-03:00",
                "2026-10-19T00:30:00-03:00", False, "America/Recife", "confirmed",
            ),
            AgendaEvent(
                "all-day", "Preparação", "2026-10-21", "2026-10-25",
                True, "America/Recife", "tentative",
            ),
        )
        reference = date(2026, 10, 22)
        cases = (
            ("list_week", "week", "2026-10-19", "2026-10-26"),
            ("list_month", "month", "2026-10-01", "2026-11-01"),
        )
        for method, kind, start, end in cases:
            with self.subTest(period=kind):
                gateway = Mock()
                gateway.list_events.return_value = CalendarListing("Genius Testes", events)
                result = getattr(AgendaService(gateway), method)(reference)
                self.assertIsInstance(result, PeriodAgenda)
                self.assertEqual(set(asdict(result)), {
                    "period", "reference_date", "start_date", "end_date_exclusive",
                    "time_zone", "calendar_name", "events",
                })
                self.assertEqual(result.period, kind)
                self.assertEqual(result.reference_date, "2026-10-22")
                self.assertEqual(result.start_date, start)
                self.assertEqual(result.end_date_exclusive, end)
                self.assertEqual(result.time_zone, "America/Recife")
                self.assertEqual(result.calendar_name, "Genius Testes")
                self.assertEqual(result.events, events)
                gateway.list_events.assert_called_once()
                sent_period = gateway.list_events.call_args.args[0]
                self.assertEqual(sent_period.start.date().isoformat(), start)
                self.assertEqual(sent_period.end.date().isoformat(), end)

    def test_service_propagates_failure_instead_of_returning_empty_events(self):
        for method in ("list_week", "list_month"):
            with self.subTest(method=method):
                gateway = Mock()
                gateway.list_events.side_effect = CalendarOperationError("Consulta incompleta.")
                with self.assertRaisesRegex(CalendarOperationError, "Consulta incompleta"):
                    getattr(AgendaService(gateway), method)(date(2026, 10, 22))


class PeriodCliTests(unittest.TestCase):
    def setUp(self):
        settings = SimpleNamespace(
            google_calendar_name="Genius Testes", google_calendar_id=""
        )
        database = Mock(name="database_without_io")
        database.get_metadata.return_value = None
        for target, value in (("settings", settings), ("database", database)):
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
            "items": [{
                "id": "genius-test@example.invalid", "summary": "Genius Testes",
                "timeZone": "America/Recife", "accessRole": "owner", "primary": False,
            }]
        }
        self.events = self.client.events.return_value
        self.request = self.events.list.return_value
        self.request.execute.return_value = {"items": [{
            "id": "approved-interview", "summary": "Entrevista de teste", "status": "confirmed",
            "start": {"dateTime": "2026-10-22T15:00:00-03:00"},
            "end": {"dateTime": "2026-10-22T15:30:00-03:00"},
        }]}
        self.addCleanup(self._assert_read_only)

    def _assert_read_only(self):
        for method in ("insert", "update", "patch", "delete"):
            getattr(self.events, method).assert_not_called()

    def _run_success(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            client.return_value.__enter__.return_value = self.client
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = command.main(args)
            client.assert_called_once_with()
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        return json.loads(stdout.getvalue())

    def test_day_default_and_explicit_day_keep_the_existing_json_contract(self):
        responses = []
        for extra_args in ([], ["--period", "day"]):
            with self.subTest(extra_args=extra_args):
                response = self._run_success(["--date", "2026-10-22", *extra_args])
                self.assertEqual(set(response), {
                    "status", "date", "time_zone", "calendar_name", "events", "count"
                })
                self.assertEqual(response["status"], "ok")
                self.assertEqual(response["date"], "2026-10-22")
                self.assertEqual(response["count"], 1)
                sent = self.events.list.call_args.kwargs
                self.assertEqual(sent["timeMin"], "2026-10-22T00:00:00-03:00")
                self.assertEqual(sent["timeMax"], "2026-10-23T00:00:00-03:00")
                responses.append(response)
        self.assertEqual(responses[0], responses[1])

    def test_week_cli_forwards_civil_week_bounds_to_real_gateway_with_fake_google(self):
        response = self._run_success(["--date", "2026-10-22", "--period", "week"])

        sent = self.events.list.call_args.kwargs
        self.assertEqual(sent["timeMin"], "2026-10-19T00:00:00-03:00")
        self.assertEqual(sent["timeMax"], "2026-10-26T00:00:00-03:00")
        self.assertEqual(sent["timeZone"], "America/Recife")
        self.assertEqual(response["period"], "week")
        self.assertEqual(response["reference_date"], "2026-10-22")
        self.assertEqual(response["start_date"], "2026-10-19")
        self.assertEqual(response["end_date_exclusive"], "2026-10-26")
        self.assertNotIn("date", response)
        self.assertEqual(response["count"], 1)
        self.assertEqual(response["events"][0]["event_id"], "approved-interview")

    def test_month_cli_forwards_first_to_exclusive_next_month_to_fake_google(self):
        response = self._run_success(["--date", "2026-10-22", "--period", "month"])

        sent = self.events.list.call_args.kwargs
        self.assertEqual(sent["timeMin"], "2026-10-01T00:00:00-03:00")
        self.assertEqual(sent["timeMax"], "2026-11-01T00:00:00-03:00")
        self.assertEqual(response["period"], "month")
        self.assertEqual(response["reference_date"], "2026-10-22")
        self.assertEqual(response["start_date"], "2026-10-01")
        self.assertEqual(response["end_date_exclusive"], "2026-11-01")
        self.assertEqual(response["count"], 1)

    def test_timezone_override_preserves_dst_offsets_in_request_and_metadata(self):
        self.request.execute.return_value = {"items": []}
        cases = (
            ("week", "2026-03-08", "2026-03-02T00:00:00-05:00", "2026-03-09T00:00:00-04:00"),
            ("month", "2026-11-22", "2026-11-01T00:00:00-04:00", "2026-12-01T00:00:00-05:00"),
        )
        for kind, reference, start, end in cases:
            with self.subTest(period=kind):
                response = self._run_success([
                    "--date", reference, "--period", kind, "--timezone", "America/New_York"
                ])
                sent = self.events.list.call_args.kwargs
                self.assertEqual(sent["timeMin"], start)
                self.assertEqual(sent["timeMax"], end)
                self.assertEqual(sent["timeZone"], "America/New_York")
                self.assertEqual(response["time_zone"], "America/New_York")
                self.assertEqual(response["reference_date"], reference)
                self.assertEqual(response["count"], 0)

    def test_invalid_period_date_zone_and_overflow_fail_before_client(self):
        cases = (
            (["--date", "2026-10-22", "--period", "year"], 2),
            (["--date", "2026-02-30", "--period", "week"], 2),
            (["--date", "2026-10-22", "--period", "week", "--timezone", "Invalid/NoSuchZone"], 1),
            (["--date", "2026-10-22", "--period", "month", "--timezone", "Invalid/NoSuchZone"], 1),
            (["--date", "9999-12-31", "--period", "week"], 1),
            (["--date", "9999-12-01", "--period", "month"], 1),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(command, "calendar_client") as client:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        if expected == 2:
                            with self.assertRaises(SystemExit) as error:
                                command.main(args)
                            result = error.exception.code
                        else:
                            result = command.main(args)
                    client.assert_not_called()
                self.assertEqual(result, expected)
                self.assertEqual(stdout.getvalue(), "")
                self.assertTrue(stderr.getvalue().strip())

    def test_week_and_month_query_errors_never_print_successful_empty_agenda(self):
        failures = (
            HttpError(
                httplib2.Response({"status": "503", "reason": "Fake error"}),
                b'{"error":{"message":"Fake Google failure"}}',
            ),
            TimeoutError("Simulated network failure"),
        )
        for kind in ("week", "month"):
            for failure in failures:
                with self.subTest(period=kind, failure=type(failure).__name__):
                    self.request.execute.side_effect = failure
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with patch.object(command, "calendar_client") as client:
                        client.return_value.__enter__.return_value = self.client
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            result = command.main(["--date", "2026-10-22", "--period", kind])
                    self.assertEqual(result, 1)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertTrue(stderr.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
