"""Reagendamento seguro com clientes falsos, sem credenciais nem rede."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import httplib2
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from app import google_calendar
from app import reschedule_event as command
from app.services.agenda import AgendaEvent
from app.services.interviews import CalendarOperationError
from app.services.rescheduling import RescheduleResult, RescheduleService, ScheduleChange


def timestamp(value):
    return datetime.fromisoformat(value)


def requested_change():
    return ScheduleChange(
        timestamp("2026-10-23T16:00:00-03:00"),
        timestamp("2026-10-23T16:30:00-03:00"),
        "America/Recife",
    )


def raw_event(event_id="TargetID", *, moved=False, **changes):
    event = {
        "id": event_id, "etag": '"version 1"',
        "summary": "Entrevista de estágio — Empresa Exemplo (teste)",
        "status": "confirmed", "eventType": "default",
        "start": {"dateTime": "2026-10-22T15:00:00-03:00"},
        "end": {"dateTime": "2026-10-22T15:30:00-03:00"},
        "htmlLink": "https://example.invalid/event",
    }
    if moved:
        event.update(
            etag='"version 2"',
            start={"dateTime": "2026-10-23T16:00:00-03:00"},
            end={"dateTime": "2026-10-23T16:30:00-03:00"},
        )
    event.update(changes)
    return event


def agenda_event():
    return AgendaEvent(
        "TargetID", "Entrevista de estágio — Empresa Exemplo (teste)",
        "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00",
        False, "America/Recife", "confirmed", "https://example.invalid/event",
    )


def google_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake Google error"}),
        b'{"error":{"message":"Fake Google error"}}',
    )


class RescheduleServiceTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock(name="injected_gateway_without_io")
        self.service = RescheduleService(self.gateway)
        self.change = requested_change()

    def test_injected_service_preserves_result_and_explicit_execution_flag(self):
        expected = RescheduleResult(
            "preview", "Genius Testes", agenda_event(), None,
            self.change.start.isoformat(), self.change.end.isoformat(), "America/Recife",
        )
        self.gateway.reschedule_event.return_value = expected

        self.assertIs(self.service.reschedule("TargetID", self.change), expected)
        self.assertIs(self.service.reschedule("TargetID", self.change, execute=True), expected)
        self.assertEqual(self.gateway.reschedule_event.call_args_list, [
            call("TargetID", self.change, execute=False),
            call("TargetID", self.change, execute=True),
        ])

    def test_invalid_identity_times_offsets_and_zone_fail_before_gateway(self):
        invalid = (
            ("", self.change),
            ("with space", self.change),
            ("hidden\u200bcharacter", self.change),
            ("TargetID", replace(self.change, start=self.change.start.replace(tzinfo=None))),
            ("TargetID", replace(self.change, end=self.change.end.replace(tzinfo=None))),
            ("TargetID", replace(self.change, start="2026-10-23T16:00:00-03:00")),
            ("TargetID", replace(self.change, end=self.change.start)),
            ("TargetID", replace(self.change, end=timestamp("2026-10-23T15:59:59-03:00"))),
            ("TargetID", replace(self.change, time_zone="Invalid/NoSuchZone")),
            ("TargetID", replace(self.change, time_zone=" America/Recife")),
            ("TargetID", replace(self.change, start=timestamp("2026-10-23T16:00:00+00:00"))),
            ("TargetID", ScheduleChange(
                timestamp("2026-03-08T02:30:00-05:00"),
                timestamp("2026-03-08T03:30:00-04:00"), "America/New_York",
            )),
        )
        for event_id, change in invalid:
            with self.subTest(event_id=event_id, change=change):
                with self.assertRaises(ValueError):
                    self.service.reschedule(event_id, change, execute=True)
                self.gateway.reschedule_event.assert_not_called()

    def test_datetime_overflow_becomes_validation_error_before_gateway(self):
        cases = (
            ScheduleChange(timestamp("0001-01-01T00:00:00+14:00"),
                           timestamp("0001-01-01T01:00:00+14:00"), "Pacific/Kiritimati"),
            ScheduleChange(timestamp("9999-12-31T22:00:00-12:00"),
                           timestamp("9999-12-31T23:00:00-12:00"), "Etc/GMT+12"),
        )
        for change in cases:
            with self.subTest(change=change):
                with self.assertRaisesRegex(ValueError, "intervalo"):
                    self.service.reschedule("TargetID", change)
                self.gateway.reschedule_event.assert_not_called()

    def test_both_dst_folds_and_cross_fold_interval_are_valid_instants(self):
        cases = (
            ("2026-11-01T01:00:00-04:00", "2026-11-01T01:30:00-04:00"),
            ("2026-11-01T01:00:00-05:00", "2026-11-01T01:30:00-05:00"),
            ("2026-11-01T01:45:00-04:00", "2026-11-01T01:15:00-05:00"),
        )
        for start, end in cases:
            with self.subTest(start=start, end=end):
                self.gateway.reset_mock()
                change = ScheduleChange(timestamp(start), timestamp(end), "America/New_York")
                self.service.reschedule("TargetID", change)
                self.gateway.reschedule_event.assert_called_once_with("TargetID", change, execute=False)


class RescheduleGoogleTests(unittest.TestCase):
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
        for method in ("insert", "update", "delete"):
            getattr(self.events, method).side_effect = AssertionError(
                "Reagendar não deve chamar outro método de escrita."
            )
        self.get_request = self.events.get.return_value
        self.get_request.execute.return_value = raw_event()
        self.patch_request = self.events.patch.return_value
        self.patch_request.headers = {"Existing-Header": "preserved"}
        self.patch_request.execute.return_value = {"id": "TargetID"}
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        self.change = requested_change()
        self.addCleanup(self._assert_no_other_mutations_or_title_fallback)

    def _assert_no_other_mutations_or_title_fallback(self):
        for method in ("insert", "update", "delete", "list"):
            getattr(self.events, method).assert_not_called()

    def test_preview_reads_current_event_and_reports_request_without_patch(self):
        result = self.gateway.reschedule_event("TargetID", self.change)

        self.assertEqual(result.status, "preview")
        self.assertEqual(result.calendar_name, "Genius Testes")
        self.assertEqual(result.before, agenda_event())
        self.assertIsNone(result.after)
        self.assertEqual(result.requested_start, "2026-10-23T16:00:00-03:00")
        self.assertEqual(result.requested_end, "2026-10-23T16:30:00-03:00")
        self.events.get.assert_called_once_with(
            calendarId=self.calendar["id"], eventId="TargetID", timeZone="America/Recife",
            fields="id,etag,summary,status,start,end,htmlLink,recurrence,recurringEventId,eventType",
        )
        self.get_request.execute.assert_called_once_with(num_retries=2)
        self.events.patch.assert_not_called()

    def test_execute_patches_only_start_end_with_etag_and_confirms_exact_id(self):
        self.get_request.execute.side_effect = [raw_event(), raw_event(moved=True)]
        operations = Mock()
        operations.attach_mock(self.get_request.execute, "get")
        operations.attach_mock(self.patch_request.execute, "patch")

        result = self.gateway.reschedule_event("TargetID", self.change, execute=True)

        self.assertEqual(result.status, "rescheduled")
        self.assertEqual(result.before, agenda_event())
        self.assertEqual(result.after.event_id, "TargetID")
        self.assertEqual(result.after.start, self.change.start.isoformat())
        self.assertEqual(result.after.end, self.change.end.isoformat())
        self.assertEqual(result.after.title, result.before.title)
        self.events.patch.assert_called_once_with(
            calendarId=self.calendar["id"], eventId="TargetID",
            body={
                "start": {"dateTime": "2026-10-23T16:00:00-03:00", "timeZone": "America/Recife"},
                "end": {"dateTime": "2026-10-23T16:30:00-03:00", "timeZone": "America/Recife"},
            },
            sendUpdates="none", fields="id",
        )
        self.assertEqual(self.patch_request.headers, {
            "Existing-Header": "preserved", "If-Match": '"version 1"',
        })
        self.assertEqual(operations.mock_calls, [
            call.get(num_retries=2), call.patch(num_retries=0), call.get(num_retries=2),
        ])
        self.assertEqual(self.events.get.call_count, 2)
        for arguments in self.events.get.call_args_list:
            self.assertEqual(arguments.kwargs["calendarId"], self.calendar["id"])
            self.assertEqual(arguments.kwargs["eventId"], "TargetID")

    def test_same_instants_are_unchanged_even_when_google_returns_utc(self):
        self.get_request.execute.return_value = raw_event(
            moved=True, start={"dateTime": "2026-10-23T19:00:00Z"},
            end={"dateTime": "2026-10-23T19:30:00+00:00"},
        )

        result = self.gateway.reschedule_event("TargetID", self.change, execute=True)

        self.assertEqual(result.status, "unchanged")
        self.assertIs(result.after, result.before)
        self.assertEqual(result.after.start, self.change.start.isoformat())
        self.events.patch.assert_not_called()
        self.get_request.execute.assert_called_once_with(num_retries=2)

    def test_precondition_failure_does_not_refresh_etag_or_repeat_patch(self):
        self.patch_request.execute.side_effect = google_error(412)

        with self.assertRaisesRegex(CalendarOperationError, "mudou durante"):
            self.gateway.reschedule_event("TargetID", self.change, execute=True)

        self.events.patch.assert_called_once()
        self.patch_request.execute.assert_called_once_with(num_retries=0)
        self.get_request.execute.assert_called_once_with(num_retries=2)
        self.assertEqual(self.patch_request.headers["If-Match"], '"version 1"')

    def test_write_timeout_transport_and_server_errors_are_uncertain_without_retry(self):
        failures = (TimeoutError("fake timeout"), TransportError("fake transport"),
                    google_error(500), google_error(503))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.patch_request.execute.side_effect = failure
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.gateway.reschedule_event("TargetID", self.change, execute=True)
                self.events.patch.assert_called_once()
                self.patch_request.execute.assert_called_once_with(num_retries=0)
                self.get_request.execute.assert_called_once_with(num_retries=2)

    def test_failed_postwrite_read_never_reports_success_or_repeats_patch(self):
        failures = (google_error(404), google_error(503), TimeoutError("fake postwrite timeout"))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.get_request.execute.side_effect = [raw_event(), failure]
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.gateway.reschedule_event("TargetID", self.change, execute=True)
                self.events.patch.assert_called_once()
                self.patch_request.execute.assert_called_once_with(num_retries=0)
                self.assertEqual(self.get_request.execute.call_count, 2)

    def test_postwrite_mismatch_and_bad_patch_response_remain_unconfirmed(self):
        cases = (
            ({"id": "TargetID"}, raw_event()),
            ({"id": "TargetID"}, raw_event("DifferentID", moved=True)),
            ({"id": "TargetID"}, raw_event(moved=True, status="cancelled")),
            (None, raw_event(moved=True)),
            ({"id": "DifferentID"}, raw_event(moved=True)),
        )
        for patch_response, after in cases:
            with self.subTest(patch_response=patch_response, after=after):
                self.client.reset_mock()
                self.get_request.execute.side_effect = [raw_event(), deepcopy(after)]
                self.patch_request.execute.return_value = patch_response
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.gateway.reschedule_event("TargetID", self.change, execute=True)
                self.events.patch.assert_called_once()
                self.patch_request.execute.assert_called_once_with(num_retries=0)
                self.assertEqual(self.get_request.execute.call_count,
                                 2 if patch_response == {"id": "TargetID"} else 1)

    def test_unavailable_or_unreadable_initial_event_never_starts_patch(self):
        for failure in (google_error(404), google_error(410), google_error(403),
                        google_error(503), TimeoutError("fake initial timeout")):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.get_request.execute.side_effect = failure
                with self.assertRaisesRegex(CalendarOperationError, "nenhum reagendamento foi feito"):
                    self.gateway.reschedule_event("TargetID", self.change, execute=True)
                self.events.patch.assert_not_called()

    def test_invalid_snapshot_day_event_series_and_special_event_rejected_before_patch(self):
        cases = (
            None, [], {}, raw_event("WrongID"),
            raw_event(status="cancelled"), raw_event(status="unknown"),
            raw_event(etag=None), raw_event(etag=""), raw_event(etag="  "),
            raw_event(etag='"version"\r\nInjected: value'),
            raw_event(start={"date": "2026-10-22"}, end={"date": "2026-10-23"}),
            raw_event(recurrence=["RRULE:FREQ=WEEKLY"]),
            raw_event(eventType="outOfOffice"), raw_event(eventType="birthday"),
            raw_event(start={"dateTime": "2026-10-22T15:00:00"}),
            raw_event(end={"dateTime": "2026-10-22T14:00:00-03:00"}),
        )
        for response in cases:
            with self.subTest(response=response):
                self.client.reset_mock()
                self.get_request.execute.return_value = response
                with self.assertRaises(CalendarOperationError):
                    self.gateway.reschedule_event("TargetID", self.change, execute=True)
                self.events.patch.assert_not_called()

    def test_specific_recurring_occurrence_and_tentative_status_are_preserved(self):
        event_id = "AbC123_20261022T180000Z"
        before = raw_event(event_id, status="tentative", recurringEventId="AbC123")
        after = raw_event(event_id, moved=True, status="tentative", recurringEventId="AbC123")
        self.get_request.execute.side_effect = [before, after]
        self.patch_request.execute.return_value = {"id": event_id}

        result = self.gateway.reschedule_event(event_id, self.change, execute=True)

        self.assertEqual(result.status, "rescheduled")
        self.assertEqual(result.before.status, "tentative")
        self.assertEqual(result.after.status, "tentative")
        self.assertEqual(result.after.event_id, event_id)
        self.assertEqual(self.events.patch.call_args.kwargs["eventId"], event_id)
        for arguments in self.events.get.call_args_list:
            self.assertEqual(arguments.kwargs["eventId"], event_id)

    def test_primary_nonowner_and_missing_pinned_calendars_fail_before_event_io(self):
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
                    self.gateway.reschedule_event("TargetID", self.change, execute=True)
                self.client.events.assert_not_called()

    def test_direct_gateway_validates_before_calendar_resolution(self):
        for change in (
            replace(self.change, start=self.change.start.replace(tzinfo=None)),
            replace(self.change, time_zone="UTC"),
            replace(self.change, end=self.change.start),
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.gateway.reschedule_event("TargetID", change, execute=True)
                self.client.calendarList.assert_not_called()
                self.client.events.assert_not_called()
                self.database.initialize.assert_not_called()

    def _run_cli(self, extra_arguments=()):
        arguments = [
            "--event-id", "TargetID", "--start", "2026-10-23T16:00:00-03:00",
            "--end", "2026-10-23T16:30:00-03:00", *extra_arguments,
        ]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            client.return_value.__enter__.return_value = self.client
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = command.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_cli_defaults_to_preview_without_calendar_writes(self):
        status, stdout, stderr = self._run_cli()

        self.assertEqual(status, 0)
        response = json.loads(stdout)
        self.assertEqual(response["status"], "preview")
        self.assertEqual(response["before"]["event_id"], "TargetID")
        self.assertIsNone(response["after"])
        self.assertEqual(response["requested_start"], "2026-10-23T16:00:00-03:00")
        self.assertEqual(stderr, "")
        self.events.patch.assert_not_called()

    def test_cli_execute_prints_confirmed_result_only_after_followup_get(self):
        self.get_request.execute.side_effect = [raw_event(), raw_event(moved=True)]

        status, stdout, stderr = self._run_cli(["--execute"])

        self.assertEqual(status, 0)
        response = json.loads(stdout)
        self.assertEqual(response["status"], "rescheduled")
        self.assertEqual(response["after"]["event_id"], "TargetID")
        self.assertEqual(response["after"]["start"], "2026-10-23T16:00:00-03:00")
        self.assertEqual(stderr, "")
        self.patch_request.execute.assert_called_once_with(num_retries=0)
        self.assertEqual(self.get_request.execute.call_count, 2)

    def test_cli_errors_never_print_success_json_or_repeat_write(self):
        for failure in (google_error(412), TimeoutError("fake write timeout"), google_error(503)):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.patch_request.execute.side_effect = failure
                status, stdout, stderr = self._run_cli(["--execute"])
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.strip())
                self.patch_request.execute.assert_called_once_with(num_retries=0)
        self.client.reset_mock()
        self.patch_request.execute.side_effect = None
        self.get_request.execute.side_effect = [raw_event(), TimeoutError("fake postwrite timeout")]
        status, stdout, stderr = self._run_cli(["--execute"])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("pode ter sido aplicada", stderr)
        self.patch_request.execute.assert_called_once_with(num_retries=0)

    def test_cli_invalid_arguments_fail_before_opening_client(self):
        valid = [
            "--event-id", "TargetID", "--start", "2026-10-23T16:00:00-03:00",
            "--end", "2026-10-23T16:30:00-03:00",
        ]
        cases = (
            ([], 2),
            ([*valid[:3], "2026-10-23", *valid[4:]], 2),
            ([*valid[:3], "2026-10-23T16:00:00", *valid[4:]], 2),
            ([*valid[:3], "2026-02-30T16:00:00-03:00", *valid[4:]], 2),
            (["--event-id", "with space", *valid[2:]], 1),
            ([*valid, "--timezone", "Invalid/NoSuchZone"], 1),
            ([*valid, "--timezone", "UTC"], 1),
            ([*valid[:5], "2026-10-23T16:00:00-03:00"], 1),
            (["--event-id", "TargetID", "--start", "0001-01-01T00:00:00+14:00",
              "--end", "0001-01-01T01:00:00+14:00", "--timezone", "Pacific/Kiritimati"], 1),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(command, "calendar_client") as client:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        if expected == 2:
                            with self.assertRaises(SystemExit) as error:
                                command.main(arguments)
                            status = error.exception.code
                        else:
                            status = command.main(arguments)
                    client.assert_not_called()
                self.assertEqual(status, expected)
                self.assertEqual(stdout.getvalue(), "")
                self.assertTrue(stderr.getvalue().strip())
                self.client.events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
