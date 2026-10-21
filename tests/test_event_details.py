"""Edição parcial com clientes falsos, sem credenciais, banco real ou rede."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import itertools
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import httplib2
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from app import edit_event as command
from app import google_calendar
from app.services.event_details import (
    DetailsChange, DetailsResult, EventDetails, EventDetailsService, validate_details_change,
)
from app.services.interviews import CalendarOperationError


TITLE = "Entrevista de estágio — Empresa Exemplo (teste)"
NEW_TITLE = "Entrevista técnica — Empresa Exemplo (teste)"
NEW_DESCRIPTION = "Entrevista fictícia atualizada. Preparar apresentação do projeto Genius Agenda AI."
NEW_LOCATION = "Online — sala fictícia de teste"


def raw_event(event_id="TargetID", **changes):
    item = {
        "id": event_id, "etag": '"version 1"', "summary": TITLE,
        "description": "Descrição anterior fictícia.", "location": "Local anterior fictício.",
        "status": "confirmed", "eventType": "default",
        "start": {"dateTime": "2026-10-23T16:00:00-03:00"},
        "end": {"dateTime": "2026-10-23T16:30:00-03:00"},
        "htmlLink": "https://example.invalid/event",
    }
    item.update(changes)
    return item


def event_details():
    return EventDetails(
        "TargetID", TITLE, "2026-10-23T16:00:00-03:00", "2026-10-23T16:30:00-03:00",
        False, "America/Recife", "confirmed", "https://example.invalid/event",
        "Descrição anterior fictícia.", "Local anterior fictício.",
    )


def google_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake Google error"}),
        b'{"error":{"message":"Fake Google error"}}',
    )


class EventDetailsServiceTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock(name="injected_gateway_without_io")
        self.service = EventDetailsService(self.gateway)

    def test_service_uses_injected_gateway_and_preserves_result_and_flags(self):
        change = DetailsChange(description="Nova descrição fictícia.")
        expected = DetailsResult(
            "preview", "Genius Testes", event_details(), None,
            {"description": "Nova descrição fictícia."},
        )
        self.gateway.update_event_details.return_value = expected

        self.assertIs(self.service.update("TargetID", change), expected)
        self.assertIs(self.service.update("TargetID", change, time_zone="UTC", execute=True), expected)
        self.assertEqual(self.gateway.update_event_details.call_args_list, [
            call("TargetID", change, time_zone="America/Recife", execute=False),
            call("TargetID", change, time_zone="UTC", execute=True),
        ])

    def test_empty_invalid_fields_identity_and_zone_fail_before_gateway(self):
        cases = (
            ("TargetID", DetailsChange(), "America/Recife"),
            ("TargetID", DetailsChange(title=""), "America/Recife"),
            ("TargetID", DetailsChange(title=" \t\n"), "America/Recife"),
            ("TargetID", DetailsChange(title=123), "America/Recife"),
            ("TargetID", DetailsChange(description=[]), "America/Recife"),
            ("TargetID", DetailsChange(location=False), "America/Recife"),
            ("", DetailsChange(title=NEW_TITLE), "America/Recife"),
            ("with space", DetailsChange(title=NEW_TITLE), "America/Recife"),
            ("hidden\u200bcharacter", DetailsChange(title=NEW_TITLE), "America/Recife"),
            ("TargetID", DetailsChange(title=NEW_TITLE), "Invalid/NoSuchZone"),
        )
        for event_id, change, zone in cases:
            with self.subTest(event_id=event_id, change=change, zone=zone):
                with self.assertRaises(ValueError):
                    self.service.update(event_id, change, time_zone=zone, execute=True)
                self.gateway.update_event_details.assert_not_called()

    def test_validation_trims_only_title_and_distinguishes_omission_from_clear(self):
        change = DetailsChange(title=f"  {NEW_TITLE}\t", description="", location="  Sala  ")

        self.assertEqual(validate_details_change("TargetID", change), {
            "title": NEW_TITLE, "description": "", "location": "  Sala  ",
        })
        self.assertEqual(validate_details_change("TargetID", DetailsChange(description="")), {
            "description": "",
        })
        self.assertEqual(validate_details_change("TargetID", DetailsChange(location="")), {
            "location": "",
        })
        self.assertEqual(change.title, f"  {NEW_TITLE}\t")


class EventDetailsGoogleTests(unittest.TestCase):
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
            "items": [self.calendar],
        }
        self.events = self.client.events.return_value
        for method in ("insert", "update", "delete"):
            getattr(self.events, method).side_effect = AssertionError(
                "A edição parcial não pode chamar outro método de escrita."
            )
        self.get_request = self.events.get.return_value
        self.get_request.execute.return_value = raw_event()
        self.patch_request = self.events.patch.return_value
        self.patch_request.headers = {}
        self.patch_request.execute.return_value = {"id": "TargetID"}
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        self.addCleanup(self._assert_no_other_mutations_or_fallback)

    def _assert_no_other_mutations_or_fallback(self):
        for method in ("insert", "update", "delete", "list"):
            getattr(self.events, method).assert_not_called()

    def test_preview_reads_details_and_normalized_request_without_patch(self):
        result = self.gateway.update_event_details("TargetID", DetailsChange(title=f" {NEW_TITLE} "))

        self.assertEqual(result.status, "preview")
        self.assertEqual(result.calendar_name, "Genius Testes")
        self.assertEqual(result.before, event_details())
        self.assertIsNone(result.after)
        self.assertEqual(result.requested, {"title": NEW_TITLE})
        self.events.get.assert_called_once_with(
            calendarId=self.calendar["id"], eventId="TargetID", timeZone="America/Recife",
            fields="id,etag,summary,description,location,status,start,end,htmlLink,recurrence,recurringEventId,eventType",
        )
        self.get_request.execute.assert_called_once_with(num_retries=2)
        self.events.patch.assert_not_called()

    def test_patch_contains_only_each_provided_field_and_every_combination(self):
        values = {"title": NEW_TITLE, "description": NEW_DESCRIPTION, "location": NEW_LOCATION}
        for size in range(1, 4):
            for fields in itertools.combinations(values, size):
                with self.subTest(fields=fields):
                    self.client.reset_mock()
                    requested = {field: values[field] for field in fields}
                    body = {"summary" if field == "title" else field: value
                            for field, value in requested.items()}
                    self.get_request.execute.side_effect = [raw_event(), raw_event(etag='"v2"', **body)]
                    result = self.gateway.update_event_details(
                        "TargetID", DetailsChange(**requested), execute=True,
                    )
                    self.assertEqual(result.status, "updated")
                    self.assertEqual(result.requested, requested)
                    self.events.patch.assert_called_once_with(
                        calendarId=self.calendar["id"], eventId="TargetID", body=body,
                        sendUpdates="none", fields="id",
                    )
                    self.assertEqual(self.patch_request.headers, {"If-Match": '"version 1"'})
                    self.patch_request.execute.assert_called_once_with(num_retries=0)
                    self.assertEqual(result.after.start, result.before.start)
                    self.assertEqual(result.after.end, result.before.end)
                    for field in values:
                        self.assertEqual(getattr(result.after, field), requested.get(field, getattr(result.before, field)))

    def test_etag_is_intact_and_success_waits_for_second_get_on_same_id(self):
        self.get_request.execute.side_effect = [raw_event(), raw_event(summary=NEW_TITLE, etag='"v2"')]
        operations = Mock()
        operations.attach_mock(self.get_request.execute, "get")
        operations.attach_mock(self.patch_request.execute, "patch")

        result = self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)

        self.assertEqual(result.status, "updated")
        self.assertEqual(self.patch_request.headers["If-Match"], '"version 1"')
        self.assertEqual(operations.mock_calls, [
            call.get(num_retries=2), call.patch(num_retries=0), call.get(num_retries=2),
        ])
        self.assertEqual(self.events.get.call_count, 2)
        for arguments in self.events.get.call_args_list:
            self.assertEqual(arguments.kwargs["calendarId"], self.calendar["id"])
            self.assertEqual(arguments.kwargs["eventId"], "TargetID")

    def test_empty_description_and_location_clear_without_touching_title_or_dates(self):
        after = raw_event(etag='"v2"')
        del after["description"]
        del after["location"]
        self.get_request.execute.side_effect = [raw_event(), after]

        result = self.gateway.update_event_details(
            "TargetID", DetailsChange(description="", location=""), execute=True,
        )

        self.assertEqual(result.status, "updated")
        self.assertEqual(self.events.patch.call_args.kwargs["body"], {"description": "", "location": ""})
        self.assertEqual(result.after.description, "")
        self.assertEqual(result.after.location, "")
        self.assertEqual(result.after.title, TITLE)
        self.assertEqual(result.after.start, result.before.start)
        self.assertEqual(result.after.end, result.before.end)

    def test_equal_values_including_absent_empty_fields_are_unchanged_without_patch(self):
        absent = raw_event()
        del absent["description"]
        del absent["location"]
        cases = (
            (raw_event(), DetailsChange(title=f" {TITLE} ")),
            (absent, DetailsChange(description="", location="")),
            (raw_event(description="", location=""), DetailsChange(description="", location="")),
        )
        for response, change in cases:
            with self.subTest(response=response, change=change):
                self.client.reset_mock()
                self.get_request.execute.return_value = deepcopy(response)
                result = self.gateway.update_event_details("TargetID", change, execute=True)
                self.assertEqual(result.status, "unchanged")
                self.assertIs(result.after, result.before)
                self.events.patch.assert_not_called()
                self.get_request.execute.assert_called_once_with(num_retries=2)

    def test_missing_raw_summary_does_not_use_display_fallback_for_noop(self):
        before = raw_event()
        del before["summary"]
        self.get_request.execute.side_effect = [before, raw_event(summary="(sem título)", etag='"v2"')]

        result = self.gateway.update_event_details("TargetID", DetailsChange(title="(sem título)"), execute=True)

        self.assertEqual(result.status, "updated")
        self.assertEqual(result.before.title, "")
        self.assertEqual(result.after.title, "(sem título)")
        self.assertEqual(self.events.patch.call_args.kwargs["body"], {"summary": "(sem título)"})

    def test_postwrite_verification_checks_requested_preserved_fields_and_schedule(self):
        cases = (
            raw_event(summary=TITLE),
            raw_event(summary=NEW_TITLE, description="Outra descrição inesperada."),
            raw_event(summary=NEW_TITLE, location="Outro local inesperado."),
            raw_event(summary=NEW_TITLE, start={"dateTime": "2026-10-23T16:05:00-03:00"}),
            raw_event(summary=NEW_TITLE, end={"dateTime": "2026-10-23T17:00:00-03:00"}),
            raw_event(summary=NEW_TITLE, start={"date": "2026-10-23"}, end={"date": "2026-10-24"}),
            raw_event("WrongID", summary=NEW_TITLE),
            raw_event(summary=NEW_TITLE, status="cancelled"),
        )
        for after in cases:
            with self.subTest(after=after):
                self.client.reset_mock()
                self.get_request.execute.side_effect = [raw_event(), deepcopy(after)]
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)
                self.patch_request.execute.assert_called_once_with(num_retries=0)
                self.assertEqual(self.get_request.execute.call_count, 2)

    def test_same_timed_instants_with_different_google_offsets_still_confirm(self):
        self.get_request.execute.side_effect = [raw_event(), raw_event(
            summary=NEW_TITLE, start={"dateTime": "2026-10-23T19:00:00Z"},
            end={"dateTime": "2026-10-23T19:30:00+00:00"},
        )]

        result = self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)

        self.assertEqual(result.status, "updated")
        self.assertEqual(result.after.start, result.before.start)
        self.assertEqual(result.after.end, result.before.end)

    def test_412_never_refreshes_etag_or_repeats_patch(self):
        self.patch_request.execute.side_effect = google_error(412)

        with self.assertRaisesRegex(CalendarOperationError, "mudou durante"):
            self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)

        self.patch_request.execute.assert_called_once_with(num_retries=0)
        self.get_request.execute.assert_called_once_with(num_retries=2)
        self.assertEqual(self.patch_request.headers["If-Match"], '"version 1"')

    def test_write_timeout_transport_or_5xx_is_uncertain_without_retry(self):
        for failure in (TimeoutError("fake write timeout"), TransportError("fake transport"),
                        google_error(500), google_error(503)):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.patch_request.execute.side_effect = failure
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)
                self.events.patch.assert_called_once()
                self.patch_request.execute.assert_called_once_with(num_retries=0)
                self.get_request.execute.assert_called_once_with(num_retries=2)

    def test_bad_patch_response_or_failed_postwrite_get_never_confirms(self):
        cases = (
            (None, raw_event(summary=NEW_TITLE), 1),
            ({"id": "WrongID"}, raw_event(summary=NEW_TITLE), 1),
            ({"id": "TargetID"}, TimeoutError("fake postwrite timeout"), 2),
            ({"id": "TargetID"}, google_error(404), 2),
            ({"id": "TargetID"}, google_error(503), 2),
        )
        for response, after, get_count in cases:
            with self.subTest(response=response, after=after):
                self.client.reset_mock()
                self.patch_request.execute.return_value = response
                self.get_request.execute.side_effect = [raw_event(), after]
                with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
                    self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)
                self.patch_request.execute.assert_called_once_with(num_retries=0)
                self.assertEqual(self.get_request.execute.call_count, get_count)

    def test_unavailable_event_or_failed_initial_get_never_starts_patch(self):
        for failure in (google_error(404), google_error(410), google_error(403),
                        google_error(503), TimeoutError("fake initial timeout")):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.get_request.execute.side_effect = failure
                with self.assertRaisesRegex(CalendarOperationError, "nenhuma edição foi feita"):
                    self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)
                self.events.patch.assert_not_called()

    def test_malformed_snapshot_identity_status_etag_series_special_event_fail_before_patch(self):
        cases = (
            None, [], {}, raw_event("WrongID"), raw_event(status="cancelled"), raw_event(status="unknown"),
            raw_event(etag=None), raw_event(etag=""), raw_event(etag="  "),
            raw_event(etag='"version"\r\nInjected: value'),
            raw_event(description=None), raw_event(location=[]), raw_event(summary=123),
            raw_event(recurrence=["RRULE:FREQ=WEEKLY"]), raw_event(eventType="outOfOffice"),
            raw_event(start={"dateTime": "2026-10-23T16:00:00"}),
            raw_event(end={"dateTime": "2026-10-23T15:00:00-03:00"}),
        )
        for response in cases:
            with self.subTest(response=response):
                self.client.reset_mock()
                self.get_request.execute.return_value = response
                with self.assertRaises(CalendarOperationError):
                    self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)
                self.events.patch.assert_not_called()

    def test_all_day_dates_and_exclusive_end_are_preserved_in_calendar_timezone(self):
        self.calendar["timeZone"] = "America/New_York"
        before = raw_event(start={"date": "2026-10-23"}, end={"date": "2026-10-25"})
        after = deepcopy(before)
        after.update(summary=NEW_TITLE, etag='"v2"')
        self.get_request.execute.side_effect = [before, after]

        result = self.gateway.update_event_details(
            "TargetID", DetailsChange(title=NEW_TITLE), time_zone="UTC", execute=True,
        )

        self.assertEqual(result.status, "updated")
        self.assertTrue(result.after.all_day)
        self.assertEqual(result.after.start, "2026-10-23")
        self.assertEqual(result.after.end, "2026-10-25")
        self.assertEqual(result.after.time_zone, "America/New_York")
        self.assertEqual(self.events.patch.call_args.kwargs["body"], {"summary": NEW_TITLE})
        self.client.reset_mock()
        self.get_request.execute.side_effect = [before, dict(after, end={"date": "2026-10-24"})]
        with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
            self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), time_zone="UTC", execute=True)
        self.patch_request.execute.assert_called_once_with(num_retries=0)

    def test_specific_recurring_occurrence_uses_exact_id_and_keeps_tentative_status(self):
        event_id = "AbC123_20261023T190000Z"
        self.get_request.execute.side_effect = [
            raw_event(event_id, recurringEventId="AbC123", status="tentative"),
            raw_event(event_id, recurringEventId="AbC123", status="tentative", summary=NEW_TITLE),
        ]
        self.patch_request.execute.return_value = {"id": event_id}

        result = self.gateway.update_event_details(event_id, DetailsChange(title=NEW_TITLE), execute=True)

        self.assertEqual(result.status, "updated")
        self.assertEqual(result.before.status, "tentative")
        self.assertEqual(result.after.status, "tentative")
        self.assertEqual(result.after.event_id, event_id)
        self.assertEqual(self.events.patch.call_args.kwargs["eventId"], event_id)
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
                    self.gateway.update_event_details("TargetID", DetailsChange(title=NEW_TITLE), execute=True)
                self.client.events.assert_not_called()

    def test_direct_gateway_rejects_invalid_change_before_calendar_or_database_io(self):
        for change in (DetailsChange(), DetailsChange(title=" \t"), DetailsChange(description=123)):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    self.gateway.update_event_details("TargetID", change, execute=True)
                self.client.calendarList.assert_not_called()
                self.client.events.assert_not_called()
                self.database.initialize.assert_not_called()

    def _run_cli(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            client.return_value.__enter__.return_value = self.client
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = command.main(["--event-id", "TargetID", *arguments])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_default_preview_including_clear_flags_does_not_write(self):
        status, stdout, stderr = self._run_cli(["--clear-description", "--clear-location"])

        self.assertEqual(status, 0)
        response = json.loads(stdout)
        self.assertEqual(response["status"], "preview")
        self.assertEqual(response["requested"], {"description": "", "location": ""})
        self.assertEqual(response["before"]["title"], TITLE)
        self.assertIsNone(response["after"])
        self.assertEqual(stderr, "")
        self.events.patch.assert_not_called()

    def test_cli_execute_and_unchanged_report_correct_statuses(self):
        self.get_request.execute.side_effect = [raw_event(), raw_event(summary=NEW_TITLE)]
        status, stdout, stderr = self._run_cli(["--title", f" {NEW_TITLE} ", "--execute"])

        self.assertEqual(status, 0)
        response = json.loads(stdout)
        self.assertEqual(response["status"], "updated")
        self.assertEqual(response["after"]["title"], NEW_TITLE)
        self.assertEqual(response["after"]["start"], "2026-10-23T16:00:00-03:00")
        self.assertEqual(stderr, "")
        self.patch_request.execute.assert_called_once_with(num_retries=0)
        self.client.reset_mock()
        self.get_request.execute.side_effect = None
        self.get_request.execute.return_value = raw_event()
        status, stdout, stderr = self._run_cli(["--title", TITLE, "--execute"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout)["status"], "unchanged")
        self.assertEqual(stderr, "")
        self.events.patch.assert_not_called()

    def test_cli_operational_errors_never_print_success_or_repeat_write(self):
        for failure in (google_error(412), TimeoutError("fake timeout"), google_error(503)):
            with self.subTest(failure=type(failure).__name__, text=str(failure)):
                self.client.reset_mock()
                self.patch_request.execute.side_effect = failure
                status, stdout, stderr = self._run_cli(["--title", NEW_TITLE, "--execute"])
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.strip())
                self.patch_request.execute.assert_called_once_with(num_retries=0)
        self.client.reset_mock()
        self.patch_request.execute.side_effect = None
        self.get_request.execute.side_effect = [raw_event(), TimeoutError("fake postwrite timeout")]
        status, stdout, stderr = self._run_cli(["--title", NEW_TITLE, "--execute"])
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("pode ter sido aplicada", stderr)
        self.patch_request.execute.assert_called_once_with(num_retries=0)

    def test_cli_invalid_and_conflicting_arguments_fail_before_opening_client(self):
        cases = (
            ([], 2),
            (["--event-id", "TargetID"], 1),
            (["--event-id", "TargetID", "--title", ""], 1),
            (["--event-id", "TargetID", "--title", "  "], 1),
            (["--event-id", "with space", "--title", NEW_TITLE], 1),
            (["--event-id", "TargetID", "--title", NEW_TITLE, "--timezone", "Invalid/NoSuchZone"], 1),
            (["--event-id", "TargetID", "--description", "value", "--clear-description"], 2),
            (["--event-id", "TargetID", "--location", "value", "--clear-location"], 2),
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
