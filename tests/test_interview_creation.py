"""Criação de entrevistas com clientes falsos: sem credenciais ou rede."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httplib2
from googleapiclient.errors import HttpError

from app import create_test_interview as command
from app import google_calendar
from app.database import Database
from app.services.interviews import (
    CalendarOperationError,
    CreatedInterview,
    InterviewService,
)


def google_error(status: int) -> HttpError:
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake Google error"}),
        b'{"error":{"message":"Fake Google error"}}',
    )


class InterviewCreationTests(unittest.TestCase):
    def setUp(self):
        self.draft = command.test_interview()
        self.operation_id = command.OPERATION_ID
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
        self.creation_temp = TemporaryDirectory()
        self.addCleanup(self.creation_temp.cleanup)
        self.creation_ledger_number = 0
        self._use_empty_creation_ledger()
        for target, value in (
            ("settings", self.settings),
            ("database", self.database),
            ("calendar_write_lock", nullcontext),
        ):
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
        self.events.list.return_value.execute.return_value = {"items": []}
        self.events.get.return_value.execute.side_effect = google_error(404)
        self.events.insert.return_value.execute.side_effect = self._insert_response
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)

    def _insert_response(self, **_kwargs):
        body = deepcopy(self.events.insert.call_args.kwargs["body"])
        body.update(
            status="confirmed",
            htmlLink="https://calendar.google.com/calendar/event?eid=fake-test",
        )
        self.saved_event = body
        return deepcopy(body)

    def _use_empty_creation_ledger(self):
        self.creation_ledger_number += 1
        self.creation_database = Database(
            Path(self.creation_temp.name) / f"creation-{self.creation_ledger_number}.db"
        )
        self.creation_database.initialize()
        for name in ("reserve_creation", "get_creation", "confirm_creation"):
            getattr(self.database, name).side_effect = getattr(self.creation_database, name)

    def _seed_existing_event(self, *, confirmed_locally=True):
        self.gateway.create_event(self.draft, operation_id=self.operation_id)
        event = deepcopy(self.saved_event)
        if not confirmed_locally:
            # Simula evento legado/corrida externa, sem apagar um registro confirmado.
            self._use_empty_creation_ledger()
        self.client.reset_mock()
        self.events.get.return_value.execute.side_effect = None
        self.events.get.return_value.execute.return_value = event
        return event

    def test_service_uses_injected_gateway_without_google(self):
        gateway = Mock()
        expected = CreatedInterview("fake-id", "https://example.invalid/event", "Testes")
        gateway.create_event.return_value = expected

        result = InterviewService(gateway).create(
            self.draft, operation_id=self.operation_id
        )

        self.assertIs(result, expected)
        gateway.create_event.assert_called_once_with(
            self.draft, operation_id=self.operation_id
        )
        self.client.assert_not_called()
        self.database.initialize.assert_not_called()

    def test_invalid_input_is_rejected_before_gateway_or_google_io(self):
        invalid_cases = (
            (replace(self.draft, title="  "), self.operation_id),
            (replace(self.draft, time_zone="  "), self.operation_id),
            (replace(self.draft, start=self.draft.start.replace(tzinfo=None)), self.operation_id),
            (replace(self.draft, end=self.draft.end.replace(tzinfo=None)), self.operation_id),
            (replace(self.draft, end=self.draft.start), self.operation_id),
            (replace(self.draft, end=datetime.fromisoformat("2026-10-22T14:59:00-03:00")), self.operation_id),
            (replace(self.draft, end=datetime.fromisoformat("2026-10-22T18:00:00+00:00")), self.operation_id),
            (self.draft, "  "),
        )
        for draft, operation_id in invalid_cases:
            with self.subTest(draft=draft, operation_id=operation_id):
                gateway = Mock()
                with self.assertRaises(ValueError):
                    InterviewService(gateway).create(draft, operation_id=operation_id)
                gateway.create_event.assert_not_called()
                with self.assertRaises(ValueError):
                    self.gateway.create_event(draft, operation_id=operation_id)
                self.client.calendarList.assert_not_called()
                self.client.events.assert_not_called()

    def test_missing_event_is_created_with_approved_body_and_no_guests(self):
        result = self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.assertFalse(result.reused)
        self.assertEqual(result.calendar_name, "Genius Testes")
        arguments = self.events.insert.call_args.kwargs
        body = arguments["body"]
        self.assertEqual(arguments["calendarId"], self.calendar["id"])
        self.assertEqual(arguments["sendUpdates"], "none")
        self.assertEqual(body["summary"], "Entrevista de estágio — Empresa Exemplo (teste)")
        self.assertEqual(body["description"], self.draft.description)
        self.assertEqual(body["start"], {
            "dateTime": "2026-10-22T15:00:00-03:00", "timeZone": "America/Recife"
        })
        self.assertEqual(body["end"], {
            "dateTime": "2026-10-22T15:30:00-03:00", "timeZone": "America/Recife"
        })
        self.assertNotIn("attendees", body)
        self.assertEqual(body["visibility"], "private")
        self.assertEqual(body["reminders"], {"useDefault": False})
        self.assertRegex(body["id"], r"^[0-9a-v]{5,1024}$")
        self.assertRegex(body["extendedProperties"]["private"]["geniusRequest"], r"^[0-9a-f]{64}$")
        self.assertEqual(result.event_id, body["id"])
        self.assertEqual(self.events.get.call_args.kwargs["eventId"], body["id"])
        self.events.insert.return_value.execute.assert_called_once_with(num_retries=0)

    def test_real_calendar_selection_blocks_unsafe_or_missing_target_before_insert(self):
        cases = (
            (dict(self.calendar, primary=True), ""),
            (dict(self.calendar, accessRole="writer"), ""),
            (self.calendar, "missing-pinned-id@example.invalid"),
        )
        for calendar, pinned_id in cases:
            with self.subTest(calendar=calendar, pinned_id=pinned_id):
                self.client.reset_mock()
                self.settings.google_calendar_id = pinned_id
                self.client.calendarList.return_value.list.return_value.execute.return_value = {
                    "items": [calendar]
                }
                with self.assertRaises(google_calendar.CalendarSetupError):
                    self.gateway.create_event(self.draft, operation_id=self.operation_id)
                self.client.events.assert_not_called()
                self.events.insert.assert_not_called()

    def test_saved_calendar_id_cannot_fall_back_to_matching_name(self):
        self.database.get_metadata.return_value = "missing-saved-id@example.invalid"

        with self.assertRaises(google_calendar.CalendarSetupError):
            self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.client.events.assert_not_called()
        self.database.set_metadata.assert_not_called()

    def test_calendar_discovery_follows_pagination_before_creating(self):
        self.client.calendarList.return_value.list.return_value.execute.side_effect = [
            {"items": [], "nextPageToken": "second-page"},
            {"items": [self.calendar]},
        ]

        self.gateway.create_event(self.draft, operation_id=self.operation_id)

        calls = self.client.calendarList.return_value.list.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0].kwargs["pageToken"])
        self.assertEqual(calls[1].kwargs["pageToken"], "second-page")
        self.events.insert.assert_called_once()

    def test_reexecution_reuses_confirmed_event_without_another_insert(self):
        event = self._seed_existing_event()

        result = self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.assertTrue(result.reused)
        self.assertEqual(result.event_id, event["id"])
        self.events.insert.assert_not_called()

    def test_existing_event_requires_matching_fingerprint_and_content(self):
        original = self._seed_existing_event()
        changes = (
            {"extendedProperties": {"private": {"geniusRequest": "foreign-request"}}},
            {"summary": "Outro compromisso"},
            {"description": "Descrição alterada"},
            {"start": {"dateTime": "2026-10-22T15:05:00-03:00"}},
            {"end": {"dateTime": "2026-10-22T16:00:00-03:00"}},
            {"id": "another-id"},
            {"status": "cancelled"},
            {"htmlLink": ""},
        )
        for change in changes:
            with self.subTest(change=change):
                self.events.get.return_value.execute.return_value = dict(deepcopy(original), **change)
                with self.assertRaises(CalendarOperationError):
                    self.gateway.create_event(self.draft, operation_id=self.operation_id)
                self.events.insert.assert_not_called()

    def test_same_operation_id_with_changed_request_is_not_confirmed_as_success(self):
        self._seed_existing_event()

        with self.assertRaises(CalendarOperationError):
            self.gateway.create_event(
                replace(self.draft, title="Entrevista diferente"),
                operation_id=self.operation_id,
            )

        self.events.insert.assert_not_called()

    def test_insert_conflict_reads_and_reuses_the_matching_event(self):
        event = self._seed_existing_event(confirmed_locally=False)
        self.events.get.return_value.execute.side_effect = [google_error(404), event]
        self.events.insert.return_value.execute.side_effect = google_error(409)

        result = self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.assertTrue(result.reused)
        self.assertEqual(result.event_id, event["id"])
        self.assertEqual(self.events.get.return_value.execute.call_count, 2)
        self.events.insert.return_value.execute.assert_called_once_with(num_retries=0)

    def test_insert_conflict_does_not_confirm_unrelated_or_unavailable_event(self):
        original = self._seed_existing_event(confirmed_locally=False)
        invalid_events = (
            dict(deepcopy(original), summary="Evento alterado"),
            dict(deepcopy(original), extendedProperties={"private": {"geniusRequest": "other"}}),
            google_error(404),
        )
        for event in invalid_events:
            with self.subTest(event=event):
                self.client.reset_mock()
                self.events.get.return_value.execute.side_effect = [google_error(404), event]
                self.events.insert.return_value.execute.side_effect = google_error(409)
                with self.assertRaises(CalendarOperationError):
                    self.gateway.create_event(self.draft, operation_id=self.operation_id)
                self.events.insert.return_value.execute.assert_called_once_with(num_retries=0)

    def test_timeout_does_not_repeat_post_and_next_execution_checks_saved_result(self):
        def save_then_timeout(**kwargs):
            self._insert_response(**kwargs)
            raise TimeoutError("Simulated response lost after provider saved the event")

        def read_provider_state(**_kwargs):
            event = getattr(self, "saved_event", None)
            if event is None:
                raise google_error(404)
            return deepcopy(event)

        self.events.get.return_value.execute.side_effect = read_provider_state
        self.events.insert.return_value.execute.side_effect = save_then_timeout
        first = self.gateway.create_event(self.draft, operation_id=self.operation_id)
        self.assertTrue(first.recovered)
        self.assertFalse(first.reused)

        self.events.insert.return_value.execute.assert_called_once_with(num_retries=0)
        event = deepcopy(self.saved_event)
        result = self.gateway.create_event(self.draft, operation_id=self.operation_id)
        self.assertTrue(result.reused)
        self.assertFalse(result.recovered)
        self.assertEqual(result.event_id, event["id"])
        self.events.insert.assert_called_once()
        self.assertEqual(self.events.get.return_value.execute.call_count, 3)

    def test_access_denied_while_checking_event_never_triggers_insert(self):
        self.events.get.return_value.execute.side_effect = google_error(403)

        with self.assertRaisesRegex(CalendarOperationError, "HTTP 403"):
            self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.events.insert.assert_not_called()

    def test_default_command_only_previews_without_opening_calendar_client(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = command.main([])

        self.assertEqual(exit_code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["start"], "2026-10-22T15:00:00-03:00")
        self.assertEqual(result["end"], "2026-10-22T15:30:00-03:00")
        self.assertEqual(result["time_zone"], "America/Recife")
        self.assertEqual(result["attendees"], [])
        self.assertEqual(stderr.getvalue(), "")
        client.assert_not_called()

    def test_execute_command_reports_failure_without_printing_success(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            client.return_value.__enter__.return_value = self.client
            with patch.object(command, "InterviewService") as service:
                service.return_value.create.side_effect = CalendarOperationError(
                    "Resultado da criação ainda não confirmado."
                )
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = command.main(["--execute"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("não confirmado", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
