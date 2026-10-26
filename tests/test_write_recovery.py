"""Reconciliação de escritas com um provedor em memória e SQLite descartável.

Cada cenário comprova o número de mutações, o ID consultado e o estado final;
nenhum teste acessa credenciais ou serviços externos.
"""

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httplib2
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from app import google_calendar
from app.database import Database
from app.services.creation_identity import creation_content_hash
from app.services.event_deletion import deletion_confirmation
from app.services.event_details import DetailsChange
from app.services.interviews import CalendarOperationError, InterviewDraft
from app.services.rescheduling import ScheduleChange


CALENDAR = {
    "id": "recovery-calendar@example.invalid", "summary": "Genius Testes",
    "timeZone": "America/Recife", "accessRole": "owner", "primary": False,
}
EVENT_ID = "RecoveryTargetID"
ETAG = '"version-before"'
NORMAL_RESPONSE = object()


def provider_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Simulated refusal"}),
        b'{"error":{"message":"PRIVATE_PROVIDER_DIAGNOSTIC"}}',
    )


def raw_event(**changes):
    event = {
        "id": EVENT_ID, "etag": ETAG, "summary": "Entrevista fictícia",
        "description": "Descrição original", "location": "Sala original",
        "status": "confirmed", "eventType": "default", "transparency": "opaque",
        "start": {"dateTime": "2026-10-23T15:00:00-03:00"},
        "end": {"dateTime": "2026-10-23T15:30:00-03:00"},
        "htmlLink": "https://example.invalid/event",
    }
    event.update(changes)
    return event


class MemoryRequest:
    def __init__(self, execute):
        self._execute = execute
        self.headers = {}

    def execute(self, **options):
        return self._execute(self.headers, options)


class RecoveryEvents:
    def __init__(self):
        self.saved = {}
        self.calls = []
        self.apply_write = True
        self.write_error = None
        self.write_response = NORMAL_RESPONSE
        self.after_apply = None
        self.before_insert = None
        self.recovery_failure = None
        self.initial_failure = None
        self.wrote = False

    def get(self, **arguments):
        def execute(headers, options):
            self.calls.append(("get", deepcopy(arguments), deepcopy(options), dict(headers)))
            failure = self.recovery_failure if self.wrote else self.initial_failure
            if failure is not None:
                raise failure
            item = self.saved.get(arguments["eventId"])
            if item is None:
                raise provider_error(404)
            return deepcopy(item)
        return MemoryRequest(execute)

    def list(self, **arguments):
        def execute(headers, options):
            self.calls.append(("list", deepcopy(arguments), deepcopy(options), dict(headers)))
            return {"items": deepcopy(list(self.saved.values()))}
        return MemoryRequest(execute)

    def _write(self, method, arguments):
        def execute(headers, options):
            self.calls.append((method, deepcopy(arguments), deepcopy(options), dict(headers)))
            if method == "insert" and self.before_insert is not None:
                self.before_insert(arguments)
            self.wrote = True
            event_id = arguments.get("eventId") or arguments["body"]["id"]
            if self.apply_write:
                if method == "insert":
                    item = deepcopy(arguments["body"])
                    item.update(status="confirmed", etag='"version-created"',
                                htmlLink="https://example.invalid/created")
                    self.saved[event_id] = item
                elif method == "patch":
                    self.saved[event_id].update(deepcopy(arguments["body"]))
                    self.saved[event_id]["etag"] = '"version-after"'
                elif method == "delete":
                    self.saved.pop(event_id, None)
                if self.after_apply is not None:
                    self.after_apply(self.saved, event_id)
            if self.write_error is not None:
                raise self.write_error
            if self.write_response is not NORMAL_RESPONSE:
                return deepcopy(self.write_response)
            if method == "insert":
                return deepcopy(self.saved[event_id])
            if method == "patch":
                return {"id": event_id}
            return None
        return MemoryRequest(execute)

    def insert(self, **arguments):
        return self._write("insert", arguments)

    def patch(self, **arguments):
        return self._write("patch", arguments)

    def delete(self, **arguments):
        return self._write("delete", arguments)


class RecoveryCalendar:
    def __init__(self, events):
        self.memory = events

    def events(self):
        return self.memory


class WriteRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Database(Path(temporary.name) / "recovery.sqlite3")
        self.db.initialize()
        self.events = RecoveryEvents()
        self.client = RecoveryCalendar(self.events)
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        for target, value in (
            ("database", self.db),
            ("resolve_calendar", lambda _client: dict(CALENDAR)),
        ):
            patcher = patch.object(google_calendar, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        guard = patch.object(google_calendar, "get_credentials",
                             side_effect=AssertionError("Nenhuma credencial deve ser acessada."))
        guard.start()
        self.addCleanup(guard.stop)
        self.draft = InterviewDraft(
            "Criação fictícia", datetime.fromisoformat("2026-10-23T16:00:00-03:00"),
            datetime.fromisoformat("2026-10-23T16:30:00-03:00"), "America/Recife",
            "Descrição fictícia",
        )
        self.change = ScheduleChange(self.draft.start, self.draft.end, "America/Recife")

    def seed(self):
        self.events.saved[EVENT_ID] = raw_event()

    def create(self, operation="recovery-operation"):
        return self.gateway.create_event(self.draft, operation_id=operation)

    def reschedule(self):
        return self.gateway.reschedule_event(EVENT_ID, self.change, execute=True)

    def details(self):
        return self.gateway.update_event_details(
            EVENT_ID, DetailsChange(title="Novo título"), execute=True,
        )

    def delete(self):
        return self.gateway.delete_event(
            EVENT_ID, execute=True,
            confirmation_token=deletion_confirmation(CALENDAR["id"], EVENT_ID, ETAG),
        )

    def record(self):
        request_hash = creation_content_hash(
            self.draft.title, self.draft.description, self.draft.start, self.draft.end,
        )
        return self.db.get_creation(CALENDAR["id"], request_hash)

    def assert_one_write(self, method):
        mutations = [call for call in self.events.calls if call[0] in ("insert", "patch", "delete")]
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0][0], method)
        self.assertEqual(mutations[0][2], {"num_retries": 0})
        if method != "insert":
            self.assertEqual(mutations[0][3].get("If-Match"), ETAG)
        for operation, arguments, _, _ in self.events.calls:
            self.assertEqual(arguments["calendarId"], CALENDAR["id"])
            if operation == "get":
                expected_id = self.record().event_id if method == "insert" else EVENT_ID
                self.assertEqual(arguments["eventId"], expected_id)

    def assert_safe_failure(self, invoke, method):
        with self.assertRaises(CalendarOperationError) as raised:
            invoke()
        self.assertNotIn("PRIVATE_PROVIDER_DIAGNOSTIC", str(raised.exception))
        self.assert_one_write(method)

    def test_create_timeout_is_reconciled_by_id_and_confirms_durable_record(self):
        self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")

        def reserved_before_post(arguments):
            # A nova conexão enxerga pending mesmo enquanto a trava externa está ativa.
            reopened = Database(self.db.path)
            key = creation_content_hash(self.draft.title, self.draft.description,
                                        self.draft.start, self.draft.end)
            pending = reopened.get_creation(CALENDAR["id"], key)
            self.assertEqual(pending.state, "pending")
            self.assertEqual(pending.event_id, arguments["body"]["id"])

        self.events.before_insert = reserved_before_post
        result = self.create()
        self.assertTrue(result.recovered)
        self.assertEqual(self.record().state, "confirmed")
        self.assertEqual(result.event_id, self.record().event_id)
        self.assert_one_write("insert")

    def test_create_server_error_after_save_is_recovered_without_second_insert(self):
        self.events.write_error = provider_error(500)
        result = self.create()
        self.assertTrue(result.recovered)
        self.assertEqual(self.record().state, "confirmed")
        self.assert_one_write("insert")

    def test_create_response_with_correct_id_but_missing_details_is_reconciled(self):
        event_id = "genius" + sha256(b"recovery-operation").hexdigest()
        self.events.write_response = {"id": event_id, "status": "confirmed"}
        result = self.create()
        self.assertTrue(result.recovered)
        self.assertEqual(result.event_id, event_id)
        self.assertEqual(self.record().state, "confirmed")
        self.assert_one_write("insert")

    def test_create_missing_event_after_timeout_stays_pending_and_is_not_retried(self):
        self.events.apply_write = False
        self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")
        self.assert_safe_failure(self.create, "insert")
        self.assertEqual(self.record().state, "pending")
        self.assertFalse(self.events.saved)

    def test_create_recovery_wrong_fingerprint_does_not_confirm_pending_record(self):
        self.events.write_error = provider_error(503)
        self.events.after_apply = lambda saved, event_id: saved[event_id].update(
            extendedProperties={"private": {"geniusRequest": "a" * 64}},
        )
        self.assert_safe_failure(self.create, "insert")
        self.assertEqual(self.record().state, "pending")

    def test_create_recovery_changed_content_is_not_success(self):
        self.events.write_error = TransportError("PRIVATE_PROVIDER_DIAGNOSTIC")
        self.events.after_apply = lambda saved, event_id: saved[event_id].update(summary="Outro título")
        self.assert_safe_failure(self.create, "insert")
        self.assertEqual(self.record().state, "pending")

    def test_create_recovery_read_failure_never_confirms_or_repeats_post(self):
        self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")
        self.events.recovery_failure = provider_error(403)
        self.assert_safe_failure(self.create, "insert")
        self.assertEqual(self.record().state, "pending")

    def test_local_confirmation_failure_after_recovery_preserves_pending_and_provider_id(self):
        self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")
        with patch.object(self.db, "confirm_creation", side_effect=sqlite3.OperationalError("private database detail")):
            self.assert_safe_failure(self.create, "insert")
        record = self.record()
        self.assertEqual(record.state, "pending")
        self.assertIn(record.event_id, self.events.saved)
        # Reabrir não perde a identidade reservada antes do POST.
        reopened = Database(self.db.path)
        key = creation_content_hash(self.draft.title, self.draft.description,
                                    self.draft.start, self.draft.end)
        self.assertEqual(reopened.get_creation(CALENDAR["id"], key).event_id, record.event_id)

    def test_prewrite_failure_never_sends_create(self):
        for failure in (provider_error(401), provider_error(403), TransportError("private")):
            with self.subTest(failure=type(failure).__name__):
                self.events.calls.clear()
                self.events.initial_failure = failure
                with self.assertRaises(CalendarOperationError):
                    self.create()
                self.assertFalse(any(call[0] == "insert" for call in self.events.calls))
                self.assertEqual(self.record().state, "pending")

    def test_null_prewrite_get_response_is_not_confirmed_absence_and_never_inserts(self):
        with patch.object(self.events, "get") as fake_get:
            fake_get.return_value.execute.return_value = None
            with self.assertRaises(CalendarOperationError):
                self.create()
        self.assertFalse(any(call[0] == "insert" for call in self.events.calls))
        self.assertEqual(self.record().state, "pending")

    def test_prewrite_get_decode_error_is_safe_and_never_inserts(self):
        with patch.object(self.events, "get") as fake_get:
            fake_get.return_value.execute.side_effect = ValueError("PRIVATE_PROVIDER_DIAGNOSTIC")
            with self.assertRaises(CalendarOperationError) as raised:
                self.create()
        self.assertNotIn("PRIVATE_PROVIDER_DIAGNOSTIC", str(raised.exception))
        self.assertFalse(any(call[0] == "insert" for call in self.events.calls))
        self.assertEqual(self.record().state, "pending")

    def test_confirmed_creation_reuse_does_not_claim_new_response_recovery(self):
        first = self.create()
        second = self.create(operation="another-delivery-id")
        self.assertFalse(first.recovered)
        self.assertFalse(second.recovered)
        self.assertTrue(second.reused)
        self.assertEqual(second.event_id, first.event_id)
        self.assert_one_write("insert")

    def test_normal_patch_and_delete_results_default_to_not_recovered(self):
        for invoke, status in ((self.reschedule, "rescheduled"), (self.details, "updated"),
                               (self.delete, "deleted")):
            with self.subTest(status=status):
                self.events.calls.clear()
                self.events.wrote = False
                self.seed()
                result = invoke()
                self.assertEqual(result.status, status)
                self.assertIs(asdict(result)["recovered"], False)
                self.assert_one_write("delete" if status == "deleted" else "patch")

    def test_reschedule_timeout_recovers_only_verified_requested_schedule(self):
        self.seed()
        self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")
        result = self.reschedule()
        self.assertEqual(result.status, "rescheduled")
        self.assertTrue(result.recovered)
        self.assertEqual(result.after.start, self.draft.start.isoformat())
        self.assertEqual(result.after.end, self.draft.end.isoformat())
        self.assert_one_write("patch")

    def test_details_transport_failure_recovers_with_unrequested_fields_preserved(self):
        self.seed()
        self.events.write_error = TransportError("PRIVATE_PROVIDER_DIAGNOSTIC")
        result = self.details()
        self.assertEqual(result.status, "updated")
        self.assertTrue(result.recovered)
        self.assertEqual(result.after.title, "Novo título")
        self.assertEqual(result.after.description, "Descrição original")
        self.assertEqual(result.after.location, "Sala original")
        self.assertEqual(result.after.start, result.before.start)
        self.assert_one_write("patch")

    def test_delete_timeout_recovers_when_exact_id_is_missing(self):
        self.seed()
        self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")
        result = self.delete()
        self.assertEqual(result.status, "deleted")
        self.assertTrue(result.recovered)
        self.assertNotIn(EVENT_ID, self.events.saved)
        self.assert_one_write("delete")

    def test_delete_server_error_recovers_cancelled_tombstone_for_exact_id(self):
        self.seed()
        self.events.write_error = provider_error(503)
        self.events.after_apply = lambda saved, event_id: saved.update(
            {event_id: {"id": event_id, "status": "cancelled"}},
        )
        result = self.delete()
        self.assertTrue(result.recovered)
        self.assertEqual(result.status, "deleted")
        self.assert_one_write("delete")

    def test_unchanged_provider_state_after_uncertain_write_is_never_success(self):
        self.events.apply_write = False
        self.events.write_error = provider_error(500)
        for invoke, method in ((self.reschedule, "patch"), (self.details, "patch"), (self.delete, "delete")):
            with self.subTest(operation=invoke.__name__):
                self.events.calls.clear()
                self.events.wrote = False
                self.seed()
                self.assert_safe_failure(invoke, method)
                self.assertEqual(self.events.saved[EVENT_ID], raw_event())

    def test_failed_recovery_read_stops_without_repeating_patch_or_delete(self):
        self.events.write_error = TransportError("PRIVATE_PROVIDER_DIAGNOSTIC")
        for invoke, method in ((self.reschedule, "patch"), (self.details, "patch"), (self.delete, "delete")):
            for failure in (TimeoutError("private recovery error"), provider_error(401), provider_error(403)):
                with self.subTest(operation=invoke.__name__, failure=type(failure).__name__):
                    self.events.calls.clear()
                    self.events.wrote = False
                    self.events.recovery_failure = failure
                    self.seed()
                    self.assert_safe_failure(invoke, method)

    def test_412_never_recovers_even_when_a_different_actor_reaches_target(self):
        self.events.write_error = provider_error(412)
        for invoke, method in ((self.reschedule, "patch"), (self.details, "patch"), (self.delete, "delete")):
            with self.subTest(operation=invoke.__name__):
                self.events.calls.clear()
                self.events.wrote = False
                self.seed()
                self.assert_safe_failure(invoke, method)
                self.assertEqual(len([call for call in self.events.calls if call[0] == "get"]), 1)

    def test_write_401_and_403_do_not_recover_as_success(self):
        for status in (401, 403):
            self.events.write_error = provider_error(status)
            for invoke, method in ((self.create, "insert"), (self.reschedule, "patch"),
                                   (self.details, "patch"), (self.delete, "delete")):
                with self.subTest(status=status, operation=invoke.__name__):
                    self.events.calls.clear()
                    self.events.wrote = False
                    self.events.saved.clear()
                    if method != "insert":
                        self.seed()
                    self.assert_safe_failure(invoke, method)
                    self.assertEqual(len([call for call in self.events.calls if call[0] == "get"]), 1)

    def test_408_429_and_5xx_reconcile_once_by_id(self):
        for status in (408, 429, 500, 502, 503, 504):
            self.events.write_error = provider_error(status)
            for invoke, method in ((self.reschedule, "patch"), (self.details, "patch"), (self.delete, "delete")):
                with self.subTest(status=status, operation=invoke.__name__):
                    self.events.calls.clear()
                    self.events.wrote = False
                    self.seed()
                    self.assertTrue(invoke().recovered)
                    self.assert_one_write(method)

    def test_malformed_patch_response_recovers_only_after_valid_snapshot(self):
        for response in (None, [], {}, {"id": "WrongID"}):
            self.events.write_response = response
            for invoke in (self.reschedule, self.details):
                with self.subTest(response=response, operation=invoke.__name__):
                    self.events.calls.clear()
                    self.events.wrote = False
                    self.seed()
                    self.assertTrue(invoke().recovered)
                    self.assert_one_write("patch")

    def test_response_decode_failure_after_write_is_reconciled_by_id(self):
        self.events.write_error = ValueError("PRIVATE_PROVIDER_DIAGNOSTIC")
        for invoke, method in ((self.create, "insert"), (self.reschedule, "patch"),
                               (self.details, "patch"), (self.delete, "delete")):
            with self.subTest(operation=invoke.__name__):
                self.events.calls.clear()
                self.events.wrote = False
                self.events.saved.clear()
                if method != "insert":
                    self.seed()
                self.assertTrue(invoke().recovered)
                self.assert_one_write(method)

    def test_schedule_recovery_rejects_changed_transparency(self):
        self.seed()
        self.events.write_error = provider_error(503)
        self.events.after_apply = lambda saved, event_id: saved[event_id].update(transparency="transparent")
        self.assert_safe_failure(self.reschedule, "patch")

    def test_details_recovery_rejects_unrequested_field_changed(self):
        for modification in (
            {"description": "Mudança concorrente"}, {"location": "Outra sala"},
            {"start": {"dateTime": "2026-10-23T14:00:00-03:00"}},
        ):
            with self.subTest(modification=modification):
                self.events.calls.clear()
                self.events.wrote = False
                self.seed()
                self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")
                self.events.after_apply = lambda saved, event_id: saved[event_id].update(modification)
                self.assert_safe_failure(self.details, "patch")

    def test_wrong_cancelled_identity_after_delete_is_not_confirmed_absence(self):
        self.seed()
        self.events.write_error = TimeoutError("PRIVATE_PROVIDER_DIAGNOSTIC")
        self.events.after_apply = lambda saved, event_id: saved.update(
            {event_id: {"id": "WrongID", "status": "cancelled"}},
        )
        self.assert_safe_failure(self.delete, "delete")

    def test_corrupt_postwrite_snapshot_never_becomes_success_or_second_mutation(self):
        for snapshot in (None, [], {}, {"id": "WrongID", "status": "confirmed"},
                         {"id": EVENT_ID, "status": "unknown"}):
            for invoke, method in ((self.reschedule, "patch"), (self.details, "patch"), (self.delete, "delete")):
                with self.subTest(snapshot=snapshot, operation=invoke.__name__):
                    self.events.calls.clear()
                    self.events.wrote = False
                    self.seed()
                    self.events.write_error = provider_error(500)
                    self.events.after_apply = lambda saved, event_id: saved.update({event_id: snapshot})
                    # None representa ausência no fake; para esse caso use um item explicitamente inválido.
                    if snapshot is None:
                        self.events.after_apply = lambda saved, event_id: saved.update({event_id: False})
                    self.assert_safe_failure(invoke, method)


if __name__ == "__main__":
    unittest.main()
