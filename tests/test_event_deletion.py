"""Exclusão confirmada por versão usando apenas clientes e persistência falsos."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import httplib2
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from app import delete_event as command
from app import google_calendar
from app.services.agenda import AgendaEvent
from app.services.event_deletion import (
    DeletionResult, EventDeletionService, deletion_confirmation, deletion_question,
)
from app.services.interviews import CalendarOperationError


TITLE = "Entrevista técnica — Empresa Exemplo (teste)"
CALENDAR_ID = "genius-test@example.invalid"
EVENT_ID = "TargetID"
ETAG = '"version 1"'


def raw_event(event_id=EVENT_ID, **changes):
    event = {
        "id": event_id, "etag": ETAG, "summary": TITLE,
        "status": "confirmed", "eventType": "default",
        "start": {"dateTime": "2026-10-23T16:00:00-03:00"},
        "end": {"dateTime": "2026-10-23T16:30:00-03:00"},
        "htmlLink": "https://example.invalid/event",
    }
    event.update(changes)
    return event


def agenda_event():
    return AgendaEvent(
        EVENT_ID, TITLE, "2026-10-23T16:00:00-03:00", "2026-10-23T16:30:00-03:00",
        False, "America/Recife", "confirmed", "https://example.invalid/event",
    )


def token(calendar_id=CALENDAR_ID, event_id=EVENT_ID, etag=ETAG):
    return deletion_confirmation(calendar_id, event_id, etag)


def google_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake Google error"}),
        b'{"error":{"message":"Fake Google error"}}',
    )


class EventDeletionServiceTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock(name="injected_gateway_without_io")
        self.service = EventDeletionService(self.gateway)

    def test_service_preserves_result_and_forwards_preview_or_explicit_confirmation(self):
        expected = DeletionResult("needs_confirmation", "Genius Testes", agenda_event(), token(), "Confirma?")
        self.gateway.delete_event.return_value = expected

        self.assertIs(self.service.delete(EVENT_ID), expected)
        self.assertIs(self.service.delete(EVENT_ID, time_zone="UTC", execute=True,
                                          confirmation_token=token()), expected)
        self.assertEqual(self.gateway.delete_event.call_args_list, [
            call(EVENT_ID, time_zone="America/Recife", execute=False, confirmation_token=None),
            call(EVENT_ID, time_zone="UTC", execute=True, confirmation_token=token()),
        ])

    def test_invalid_identity_zone_and_confirmation_flags_fail_before_gateway(self):
        cases = (
            {"event_id": ""}, {"event_id": "with space"}, {"event_id": "hidden\u200bcharacter"},
            {"time_zone": "Invalid/NoSuchZone"},
            {"confirmation_token": token()},
            {"execute": True},
            {"execute": True, "confirmation_token": ""},
            {"execute": True, "confirmation_token": "0" * 63},
            {"execute": True, "confirmation_token": "0" * 65},
            {"execute": True, "confirmation_token": "g" * 64},
            {"execute": True, "confirmation_token": "A" * 64},
            {"execute": True, "confirmation_token": 123},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.service.delete(**({"event_id": EVENT_ID} | arguments))
                self.gateway.delete_event.assert_not_called()

    def test_confirmation_is_stable_and_binds_calendar_id_event_id_and_version(self):
        original = token()
        self.assertRegex(original, r"^[0-9a-f]{64}$")
        self.assertEqual(original, token())
        alternatives = (
            token(calendar_id="another-calendar@example.invalid"),
            token(event_id="AnotherID"), token(etag='"version 2"'),
            deletion_confirmation("a:b", "c", "d"),
            deletion_confirmation("a", "b:c", "d"),
        )
        self.assertNotIn(original, alternatives)
        self.assertEqual(len(set(alternatives)), len(alternatives))

    def test_question_shows_timed_dates_and_all_day_inclusive_display(self):
        question = deletion_question(agenda_event())
        self.assertIn(TITLE, question)
        self.assertIn("23/10/2026", question)
        self.assertIn("16h00", question)
        self.assertIn("16h30", question)
        overnight = replace(agenda_event(), end="2026-10-24T00:30:00-03:00")
        self.assertIn("24/10/2026 às 00h30", deletion_question(overnight))
        for end, expected_last in (("2026-10-24", "23/10/2026"), ("2026-10-26", "25/10/2026")):
            with self.subTest(end=end):
                event = replace(agenda_event(), start="2026-10-23", end=end, all_day=True)
                question = deletion_question(event)
                self.assertIn(expected_last, question)
                self.assertNotIn("24/10/2026" if end == "2026-10-24" else "26/10/2026", question)


class EventDeletionGoogleTests(unittest.TestCase):
    def setUp(self):
        self.calendar = {
            "id": CALENDAR_ID, "summary": "Genius Testes", "timeZone": "America/Recife",
            "accessRole": "owner", "primary": False,
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
        self.client.calendarList.return_value.list.return_value.execute.return_value = {"items": [self.calendar]}
        self.events = self.client.events.return_value
        for method in ("insert", "update", "patch"):
            getattr(self.events, method).side_effect = AssertionError("Excluir não pode chamar outra mutação.")
        self.get_request = self.events.get.return_value
        self.get_request.execute.return_value = raw_event()
        self.delete_request = self.events.delete.return_value
        self.delete_request.headers = {}
        self.delete_request.execute.return_value = None
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        self.addCleanup(self._assert_no_other_mutations_or_fallback)

    def _assert_no_other_mutations_or_fallback(self):
        for method in ("insert", "update", "patch", "list"):
            getattr(self.events, method).assert_not_called()

    def execute(self, **arguments):
        return self.gateway.delete_event(
            EVENT_ID, execute=True, confirmation_token=token(), **arguments,
        )

    def test_preview_shows_event_question_and_version_token_without_delete(self):
        result = self.gateway.delete_event(EVENT_ID)

        self.assertEqual(result.status, "needs_confirmation")
        self.assertEqual(result.calendar_name, "Genius Testes")
        self.assertEqual(result.event, agenda_event())
        self.assertEqual(result.confirmation_token, token())
        self.assertIn(TITLE, result.question)
        self.assertIn("23/10/2026", result.question)
        self.events.get.assert_called_once_with(
            calendarId=CALENDAR_ID, eventId=EVENT_ID, timeZone="America/Recife",
            fields="id,etag,summary,status,start,end,htmlLink,recurrence,recurringEventId,eventType",
        )
        self.get_request.execute.assert_called_once_with(num_retries=2)
        self.events.delete.assert_not_called()

    def test_empty_delete_response_and_postread_404_410_or_matching_tombstone_confirm(self):
        for empty_response in (None, ""):
            for after in (google_error(404), google_error(410), {"id": EVENT_ID, "status": "cancelled"}):
                with self.subTest(empty_response=empty_response, after=after):
                    self.client.reset_mock()
                    self.get_request.execute.side_effect = [raw_event(), after]
                    self.delete_request.execute.return_value = empty_response
                    result = self.execute()
                    self.assertEqual(result.status, "deleted")
                    self.assertEqual(result.event, agenda_event())
                    self.assertIsNone(result.confirmation_token)
                    self.assertIsNone(result.question)
                    self.events.delete.assert_called_once_with(
                        calendarId=CALENDAR_ID, eventId=EVENT_ID, sendUpdates="none",
                    )
                    self.assertEqual(self.delete_request.headers, {"If-Match": ETAG})
                    self.delete_request.execute.assert_called_once_with(num_retries=0)
                    self.assertEqual(self.get_request.execute.call_count, 2)
                    for arguments in self.events.get.call_args_list:
                        self.assertEqual(arguments.kwargs["calendarId"], CALENDAR_ID)
                        self.assertEqual(arguments.kwargs["eventId"], EVENT_ID)

    def test_delete_occurs_between_revalidation_and_absence_check(self):
        self.get_request.execute.side_effect = [raw_event(), google_error(404)]
        operations = Mock()
        operations.attach_mock(self.get_request.execute, "get")
        operations.attach_mock(self.delete_request.execute, "delete")

        self.assertEqual(self.execute().status, "deleted")

        self.assertEqual(operations.mock_calls, [
            call.get(num_retries=2), call.delete(num_retries=0), call.get(num_retries=2),
        ])

    def test_initial_absence_is_already_absent_in_preview_and_execute_without_delete(self):
        for execute in (False, True):
            for response in (google_error(404), google_error(410), {"id": EVENT_ID, "status": "cancelled"}):
                with self.subTest(execute=execute, response=response):
                    self.client.reset_mock()
                    self.get_request.execute.side_effect = response if isinstance(response, Exception) else None
                    self.get_request.execute.return_value = response
                    result = self.gateway.delete_event(
                        EVENT_ID, execute=execute, confirmation_token=token() if execute else None,
                    )
                    self.assertEqual(result.status, "already_absent")
                    self.assertIsNone(result.event)
                    self.assertIsNone(result.confirmation_token)
                    self.assertIsNone(result.question)
                    self.events.delete.assert_not_called()
                    self.get_request.execute.assert_called_once_with(num_retries=2)

    def test_other_calendar_event_or_stale_version_token_is_rejected_before_delete(self):
        for confirmation in (token(calendar_id="other@example.invalid"), token(event_id="OtherID"), token(etag='"old"')):
            with self.subTest(confirmation=confirmation):
                self.client.reset_mock()
                with self.assertRaisesRegex(CalendarOperationError, "nova prévia"):
                    self.gateway.delete_event(EVENT_ID, execute=True, confirmation_token=confirmation)
                self.events.delete.assert_not_called()
                self.get_request.execute.assert_called_once_with(num_retries=2)
        self.client.reset_mock()
        self.get_request.execute.return_value = raw_event(etag='"version 2"')
        with self.assertRaisesRegex(CalendarOperationError, "nova prévia"):
            self.execute()
        self.events.delete.assert_not_called()

    def test_412_does_not_refresh_etag_or_retry_delete(self):
        self.delete_request.execute.side_effect = google_error(412)

        with self.assertRaisesRegex(CalendarOperationError, "nova prévia"):
            self.execute()

        self.events.delete.assert_called_once()
        self.delete_request.execute.assert_called_once_with(num_retries=0)
        self.get_request.execute.assert_called_once_with(num_retries=2)
        self.assertEqual(self.delete_request.headers["If-Match"], ETAG)

    def test_delete_404_or_410_race_requires_absence_check_and_returns_already_absent(self):
        for status in (404, 410):
            for after in (google_error(404), {"id": EVENT_ID, "status": "cancelled"}):
                with self.subTest(status=status, after=after):
                    self.client.reset_mock()
                    self.get_request.execute.side_effect = [raw_event(), after]
                    self.delete_request.execute.side_effect = google_error(status)
                    result = self.execute()
                    self.assertEqual(result.status, "already_absent")
                    self.assertIsNone(result.event)
                    self.assertEqual(self.get_request.execute.call_count, 2)
                    self.delete_request.execute.assert_called_once_with(num_retries=0)
        for after in (raw_event(), TimeoutError("fake race verification timeout")):
            with self.subTest(unconfirmed_after=after):
                self.client.reset_mock()
                self.get_request.execute.side_effect = [raw_event(), after]
                self.delete_request.execute.side_effect = google_error(404)
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.execute()
                self.delete_request.execute.assert_called_once_with(num_retries=0)

    def test_write_timeout_transport_and_server_error_are_uncertain_without_retry(self):
        for failure in (TimeoutError("fake delete timeout"), TransportError("fake transport"),
                        google_error(500), google_error(503)):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.delete_request.execute.side_effect = failure
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.execute()
                self.events.delete.assert_called_once()
                self.delete_request.execute.assert_called_once_with(num_retries=0)
                self.get_request.execute.assert_called_once_with(num_retries=2)

    def test_active_malformed_or_failed_postread_never_confirms_deleted(self):
        cases = (
            raw_event(), None, [], {}, {"id": "WrongID", "status": "cancelled"},
            raw_event(status="unknown"), google_error(403), google_error(503),
            TimeoutError("fake postdelete timeout"),
        )
        for after in cases:
            with self.subTest(after=after):
                self.client.reset_mock()
                self.get_request.execute.side_effect = [raw_event(), after]
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.execute()
                self.delete_request.execute.assert_called_once_with(num_retries=0)
                self.assertEqual(self.get_request.execute.call_count, 2)

    def test_malformed_snapshot_bad_id_status_etag_series_and_special_event_rejected(self):
        cases = (
            None, [], {}, raw_event("WrongID"), {"id": "WrongID", "status": "cancelled"},
            raw_event(status="unknown"), raw_event(status=""),
            raw_event(etag=None), raw_event(etag=""), raw_event(etag="  "),
            raw_event(etag='"version"\r\nInjected: value'),
            raw_event(recurrence=["RRULE:FREQ=WEEKLY"]), raw_event(eventType="outOfOffice"),
            raw_event(summary=["invalid", "title"]),
            raw_event(start={"dateTime": "2026-10-23T16:00:00"}),
        )
        for response in cases:
            with self.subTest(response=response):
                self.client.reset_mock()
                self.get_request.execute.return_value = response
                with self.assertRaises(CalendarOperationError):
                    self.execute()
                self.events.delete.assert_not_called()

    def test_initial_network_and_permission_errors_are_not_treated_as_absence(self):
        for failure in (google_error(403), google_error(503), TimeoutError("fake initial timeout")):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.get_request.execute.side_effect = failure
                with self.assertRaisesRegex(CalendarOperationError, "nenhuma exclusão foi feita"):
                    self.gateway.delete_event(EVENT_ID)
                self.events.delete.assert_not_called()

    def test_all_day_preview_and_delete_preserve_exclusive_dates_and_calendar_zone(self):
        self.calendar["timeZone"] = "America/New_York"
        item = raw_event(start={"date": "2026-10-23"}, end={"date": "2026-10-26"})
        self.get_request.execute.return_value = item

        preview = self.gateway.delete_event(EVENT_ID, time_zone="UTC")

        self.assertEqual(preview.status, "needs_confirmation")
        self.assertTrue(preview.event.all_day)
        self.assertEqual(preview.event.start, "2026-10-23")
        self.assertEqual(preview.event.end, "2026-10-26")
        self.assertEqual(preview.event.time_zone, "America/New_York")
        self.assertIn("25/10/2026", preview.question)
        self.assertNotIn("26/10/2026", preview.question)
        self.events.delete.assert_not_called()
        self.get_request.execute.side_effect = [deepcopy(item), google_error(410)]
        result = self.execute(time_zone="UTC")
        self.assertEqual(result.status, "deleted")
        self.assertEqual(result.event, preview.event)

    def test_specific_recurring_occurrence_and_tentative_status_are_supported(self):
        event_id = "AbC123_20261023T190000Z"
        item = raw_event(event_id, recurringEventId="AbC123", status="tentative")
        self.get_request.execute.side_effect = [item, {"id": event_id, "status": "cancelled"}]

        result = self.gateway.delete_event(event_id, execute=True, confirmation_token=token(event_id=event_id))

        self.assertEqual(result.status, "deleted")
        self.assertEqual(result.event.event_id, event_id)
        self.assertEqual(result.event.status, "tentative")
        self.assertEqual(self.events.delete.call_args.kwargs["eventId"], event_id)
        for arguments in self.events.get.call_args_list:
            self.assertEqual(arguments.kwargs["eventId"], event_id)

    def test_unsafe_or_missing_pinned_calendar_fails_before_event_io(self):
        cases = (
            (dict(self.calendar, primary=True), "", None),
            (dict(self.calendar, accessRole="writer"), "", None),
            (self.calendar, "missing-configured@example.invalid", None),
            (self.calendar, "", "missing-saved@example.invalid"),
        )
        for calendar, configured, saved in cases:
            with self.subTest(calendar=calendar, configured=configured, saved=saved):
                self.client.reset_mock()
                self.settings.google_calendar_id = configured
                self.database.get_metadata.return_value = saved
                self.client.calendarList.return_value.list.return_value.execute.return_value = {"items": [calendar]}
                with self.assertRaises(google_calendar.CalendarSetupError):
                    self.execute()
                self.client.events.assert_not_called()

    def test_direct_gateway_rejects_invalid_confirmation_before_calendar_io(self):
        for arguments in ({"execute": True}, {"confirmation_token": token()},
                          {"execute": True, "confirmation_token": "bad"}, {"time_zone": "Invalid/NoSuchZone"}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.gateway.delete_event(EVENT_ID, **arguments)
                self.client.calendarList.assert_not_called()
                self.client.events.assert_not_called()
                self.database.initialize.assert_not_called()

    def _run_cli(self, arguments=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            client.return_value.__enter__.return_value = self.client
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = command.main(["--event-id", EVENT_ID, *arguments])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_default_preview_returns_question_token_and_no_delete(self):
        status, stdout, stderr = self._run_cli()

        self.assertEqual(status, 0)
        response = json.loads(stdout)
        self.assertEqual(response["status"], "needs_confirmation")
        self.assertEqual(response["event"]["event_id"], EVENT_ID)
        self.assertEqual(response["confirmation_token"], token())
        self.assertTrue(response["question"])
        self.assertEqual(stderr, "")
        self.events.delete.assert_not_called()

    def test_cli_execute_and_preexisting_absence_report_distinct_success_statuses(self):
        self.get_request.execute.side_effect = [raw_event(), google_error(404)]
        status, stdout, stderr = self._run_cli(["--execute", "--confirm", token()])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout)["status"], "deleted")
        self.assertEqual(stderr, "")
        self.delete_request.execute.assert_called_once_with(num_retries=0)
        self.client.reset_mock()
        self.get_request.execute.side_effect = google_error(410)
        status, stdout, stderr = self._run_cli(["--execute", "--confirm", token()])
        self.assertEqual(status, 0)
        response = json.loads(stdout)
        self.assertEqual(response["status"], "already_absent")
        self.assertIsNone(response["event"])
        self.assertEqual(stderr, "")
        self.events.delete.assert_not_called()

    def test_cli_stale_confirmation_or_write_and_verification_errors_never_print_success(self):
        status, stdout, stderr = self._run_cli(["--execute", "--confirm", token(etag='"old"')])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("nova prévia", stderr)
        self.events.delete.assert_not_called()
        for failure in (google_error(412), TimeoutError("fake timeout"), google_error(503)):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.delete_request.execute.side_effect = failure
                status, stdout, stderr = self._run_cli(["--execute", "--confirm", token()])
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.strip())
                self.delete_request.execute.assert_called_once_with(num_retries=0)
        self.client.reset_mock()
        self.delete_request.execute.side_effect = None
        self.get_request.execute.side_effect = [raw_event(), TimeoutError("fake postdelete timeout")]
        status, stdout, stderr = self._run_cli(["--execute", "--confirm", token()])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("pode ter sido aplicada", stderr)
        self.delete_request.execute.assert_called_once_with(num_retries=0)

    def test_cli_invalid_arguments_fail_before_opening_client(self):
        cases = (
            ([], 2), (["--event-id", EVENT_ID, "--unknown"], 2),
            (["--event-id", "with space"], 1),
            (["--event-id", EVENT_ID, "--timezone", "Invalid/NoSuchZone"], 1),
            (["--event-id", EVENT_ID, "--execute"], 1),
            (["--event-id", EVENT_ID, "--confirm", token()], 1),
            (["--event-id", EVENT_ID, "--execute", "--confirm", "bad"], 1),
            (["--event-id", EVENT_ID, "--execute", "--confirm", "A" * 64], 1),
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
