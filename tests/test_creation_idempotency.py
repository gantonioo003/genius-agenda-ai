"""Repetições de criação com SQLite real e Google inteiramente em memória."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
import unicodedata
import unittest
from unittest.mock import patch

import httplib2
from googleapiclient.errors import HttpError

from app import google_calendar
from app.database import Database
from app.services.conflicts import CalendarConflictError
from app.services.creation_identity import creation_content_hash
from app.services.interviews import CalendarOperationError, InterviewDraft


def google_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake provider error"}),
        b'{"error":{"message":"Fake provider error"}}',
    )


class FakeRequest:
    def __init__(self, execute):
        self._execute = execute

    def execute(self, **kwargs):
        return self._execute(**kwargs)


class MemoryEvents:
    """Cada requisição captura seus argumentos, inclusive em duas threads."""

    def __init__(self):
        self.saved = {}
        self.get_calls = []
        self.list_calls = []
        self.insert_calls = []
        self.get_failures = []
        self.error_after_save = None
        self.before_insert = None
        self.lock = Lock()

    def get(self, **kwargs):
        arguments = deepcopy(kwargs)

        def execute(**options):
            with self.lock:
                self.get_calls.append((arguments, options))
                if self.get_failures:
                    failure = self.get_failures.pop(0)
                    if failure is not None:
                        raise failure
                event = self.saved.get(arguments["eventId"])
                if event is None:
                    raise google_error(404)
                return deepcopy(event)

        return FakeRequest(execute)

    def list(self, **kwargs):
        arguments = deepcopy(kwargs)

        def execute(**options):
            with self.lock:
                self.list_calls.append((arguments, options))
                return {"items": deepcopy(list(self.saved.values()))}

        return FakeRequest(execute)

    def insert(self, **kwargs):
        arguments = deepcopy(kwargs)

        def execute(**options):
            if self.before_insert is not None:
                self.before_insert(arguments)
            with self.lock:
                self.insert_calls.append((arguments, options))
                event = deepcopy(arguments["body"])
                if event["id"] in self.saved:
                    raise google_error(409)
                event.update(
                    status="confirmed",
                    htmlLink="https://calendar.google.com/calendar/event?eid=fake",
                )
                self.saved[event["id"]] = event
                if self.error_after_save is not None:
                    failure, self.error_after_save = self.error_after_save, None
                    raise failure
                return deepcopy(event)

        return FakeRequest(execute)


class MemoryCalendar:
    def __init__(self, events):
        self.memory = events
        self.events_calls = 0
        self.lock = Lock()

    def events(self):
        with self.lock:
            self.events_calls += 1
        return self.memory


class CreationIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Database(Path(self.temporary.name) / "test.sqlite3")
        self.db.initialize()
        self.calendar = {
            "id": "test-calendar@example.invalid",
            "summary": "Genius Testes",
            "timeZone": "America/Recife",
            "accessRole": "owner",
            "primary": False,
        }
        self.draft = InterviewDraft(
            "Entrevista técnica — Empresa fictícia",
            datetime.fromisoformat("2026-10-23T15:00:00-03:00"),
            datetime.fromisoformat("2026-10-23T15:30:00-03:00"),
            "America/Recife",
            "Preparação fictícia.\n Levar currículo. ",
        )
        self.events = MemoryEvents()
        self.service = MemoryCalendar(self.events)
        self.gateway = google_calendar.GoogleCalendarGateway(self.service)
        for name, value in (
            ("database", self.db),
            ("resolve_calendar", lambda _service: dict(self.calendar)),
        ):
            patcher = patch.object(google_calendar, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(
            google_calendar, "get_credentials",
            side_effect=AssertionError("Este teste não acessa credenciais."),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def create(self, draft=None, operation="operation-one", gateway=None):
        return (gateway or self.gateway).create_event(
            draft or self.draft, operation_id=operation
        )

    def content_hash(self, draft=None):
        draft = draft or self.draft
        return creation_content_hash(
            draft.title, draft.description, draft.start, draft.end
        )

    def record(self, draft=None):
        return self.db.get_creation(self.calendar["id"], self.content_hash(draft))

    def seed_legacy(self, operation="legacy-operation", *, tagged=True):
        """Formato público da versão anterior, sem registro SQLite novo."""
        body = {
            "id": "genius" + sha256(operation.encode("utf-8")).hexdigest(),
            "summary": self.draft.title.strip(),
            "description": self.draft.description,
            "start": {"dateTime": self.draft.start.isoformat(), "timeZone": self.draft.time_zone},
            "end": {"dateTime": self.draft.end.isoformat(), "timeZone": self.draft.time_zone},
            "visibility": "private",
            "reminders": {"useDefault": False},
        }
        fingerprint = sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if tagged:
            body["extendedProperties"] = {"private": {"geniusRequest": fingerprint}}
        body.update(status="confirmed", htmlLink="https://example.invalid/legacy-event")
        self.events.saved[body["id"]] = deepcopy(body)
        return body

    def test_same_operation_reuses_confirmed_event_without_second_insert(self):
        first = self.create()
        second = self.create()
        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(second.event_id, first.event_id)
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(self.record().state, "confirmed")

    def test_identical_content_with_new_operation_reuses_original_id_and_fingerprint(self):
        first = self.create()
        original = deepcopy(self.events.saved[first.event_id])
        second = self.create(operation="operation-two")
        self.assertTrue(second.reused)
        self.assertEqual(second.event_id, first.event_id)
        self.assertNotEqual(second.event_id, "genius" + sha256(b"operation-two").hexdigest())
        self.assertEqual(self.events.saved[first.event_id], original)
        self.assertEqual(self.record().fingerprint, original["extendedProperties"]["private"]["geniusRequest"])
        self.assertEqual(len(self.events.insert_calls), 1)

    def test_new_gateway_and_database_instance_reuse_after_reopening(self):
        first = self.create()
        reopened = Database(self.db.path)
        reopened.initialize()
        with patch.object(google_calendar, "database", reopened):
            second = self.create(
                operation="operation-after-restart",
                gateway=google_calendar.GoogleCalendarGateway(MemoryCalendar(self.events)),
            )
        self.assertTrue(second.reused)
        self.assertEqual(second.event_id, first.event_id)
        self.assertEqual(len(self.events.insert_calls), 1)

    def test_same_operation_with_changed_content_stops_before_events_resource(self):
        self.create()
        before = self.service.events_calls
        with self.assertRaises(CalendarOperationError):
            self.create(replace(self.draft, title="Outra entrevista"))
        self.assertEqual(self.service.events_calls, before)
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(len(self.events.saved), 1)

    def test_nfc_title_trim_and_utc_alias_reuse_without_overwriting_event(self):
        first = self.create()
        original = deepcopy(self.events.saved[first.event_id])
        equivalent = replace(
            self.draft,
            title="  " + unicodedata.normalize("NFD", self.draft.title) + "  ",
            description=unicodedata.normalize("NFD", self.draft.description),
            start=self.draft.start.astimezone(timezone.utc),
            end=self.draft.end.astimezone(timezone.utc),
            time_zone="UTC",
        )
        second = self.create(equivalent, operation="equivalent-operation")
        self.assertTrue(second.reused)
        self.assertEqual(second.event_id, first.event_id)
        self.assertEqual(self.events.saved[first.event_id], original)
        self.assertEqual(len(self.events.insert_calls), 1)

    def test_new_operation_with_different_title_is_not_fuzzy_deduplicated(self):
        first = self.create()
        for index, title in enumerate((self.draft.title.lower(), "Entrevista técnica — Outra empresa")):
            changed = replace(self.draft, title=title)
            with self.subTest(title=title):
                with self.assertRaises(CalendarConflictError):
                    self.create(changed, operation=f"different-title-{index}")
                self.assertNotEqual(self.record(changed).event_id, first.event_id)
                self.assertEqual(self.record(changed).state, "pending")
        self.assertEqual(len(self.events.insert_calls), 1)

    def test_description_change_including_spaces_is_not_deduplicated(self):
        first = self.create()
        for index, description in enumerate((self.draft.description.strip(), "Outra preparação")):
            changed = replace(self.draft, description=description)
            with self.subTest(description=description):
                with self.assertRaises(CalendarConflictError):
                    self.create(changed, operation=f"different-description-{index}")
                self.assertNotEqual(self.record(changed).event_id, first.event_id)
        self.assertEqual(len(self.events.insert_calls), 1)

    def test_adjacent_different_schedule_creates_a_new_event(self):
        first = self.create()
        later = replace(self.draft, start=self.draft.end, end=self.draft.end + timedelta(minutes=30))
        second = self.create(later, operation="new-time")
        self.assertFalse(second.reused)
        self.assertNotEqual(second.event_id, first.event_id)
        self.assertEqual(len(self.events.insert_calls), 2)

    def test_timeout_after_provider_saved_recovers_with_different_operation(self):
        self.events.error_after_save = TimeoutError("Fake lost response")
        # A gravação foi aceita, mas a consulta imediata também falhou. O ID
        # pendente precisa sobreviver para a próxima execução consultar primeiro.
        self.events.get_failures.extend([None, TimeoutError("Fake verification timeout")])
        with self.assertRaises(CalendarOperationError):
            self.create()
        pending = self.record()
        self.assertEqual(pending.state, "pending")
        self.assertIn(pending.event_id, self.events.saved)
        recovered = self.create(operation="retry-with-another-operation")
        self.assertTrue(recovered.reused)
        self.assertFalse(recovered.recovered)
        self.assertEqual(recovered.event_id, pending.event_id)
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(len(self.events.get_calls), 3)
        self.assertEqual(self.record().state, "confirmed")

    def test_failed_get_before_post_preserves_original_id_for_retry(self):
        self.events.get_failures.append(google_error(403))
        with self.assertRaises(CalendarOperationError):
            self.create()
        pending = self.record()
        self.assertEqual(pending.state, "pending")
        self.assertFalse(self.events.insert_calls)
        recovered = self.create(operation="retry-after-get-failure")
        self.assertFalse(recovered.reused)
        self.assertEqual(recovered.event_id, pending.event_id)
        self.assertEqual(len(self.events.insert_calls), 1)

    def test_reservation_is_durable_and_readable_from_another_connection_before_post(self):
        checked = []

        def inspect_before_post(arguments):
            # Uma conexão nova vê somente dados já confirmados no SQLite.
            reopened = Database(self.db.path)
            record = reopened.get_creation(self.calendar["id"], self.content_hash())
            self.assertIsNotNone(record)
            self.assertEqual(record.event_id, arguments["body"]["id"])
            self.assertEqual(record.state, "pending")
            checked.append(record.event_id)

        self.events.before_insert = inspect_before_post
        result = self.create()
        self.assertEqual(checked, [result.event_id])
        self.assertEqual(self.record().state, "confirmed")

    def test_confirmed_missing_event_is_not_recreated(self):
        first = self.create()
        del self.events.saved[first.event_id]
        with self.assertRaises(CalendarOperationError):
            self.create(operation="same-content-after-removal")
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(self.record().state, "confirmed")
        self.assertFalse(self.events.saved)

    def test_confirmed_gone_response_is_not_recreated(self):
        self.create()
        self.events.get_failures.append(google_error(410))
        with self.assertRaises(CalendarOperationError):
            self.create(operation="same-content-after-gone")
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(self.record().state, "confirmed")

    def test_confirmed_cancelled_event_is_not_recreated(self):
        first = self.create()
        self.events.saved[first.event_id] = {"id": first.event_id, "status": "cancelled"}
        with self.assertRaises(CalendarOperationError):
            self.create(operation="same-content-after-cancellation")
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(self.events.saved[first.event_id]["status"], "cancelled")

    def test_confirmed_changed_event_is_not_overwritten_or_recreated(self):
        first = self.create()
        self.events.saved[first.event_id]["summary"] = "Alterado manualmente"
        changed = deepcopy(self.events.saved[first.event_id])
        with self.assertRaises(CalendarOperationError):
            self.create(operation="same-content-after-manual-edit")
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(self.events.saved[first.event_id], changed)
        self.assertEqual(self.record().state, "confirmed")

    def test_legacy_event_with_original_id_and_fingerprint_is_adopted(self):
        legacy = self.seed_legacy()
        self.assertIsNone(self.record())
        result = self.create(operation="legacy-operation")
        self.assertTrue(result.reused)
        self.assertEqual(result.event_id, legacy["id"])
        self.assertFalse(self.events.insert_calls)
        self.assertEqual(self.record().state, "confirmed")
        self.assertEqual(self.events.saved[legacy["id"]], legacy)

    def test_foreign_untagged_event_with_same_id_is_not_adopted(self):
        foreign = self.seed_legacy(tagged=False)
        with self.assertRaises(CalendarOperationError):
            self.create(operation="legacy-operation")
        self.assertFalse(self.events.insert_calls)
        self.assertEqual(self.record().state, "pending")
        self.assertEqual(self.events.saved[foreign["id"]], foreign)

    def test_two_concurrent_operations_with_identical_content_insert_once(self):
        barrier = Barrier(2)

        def worker(operation):
            barrier.wait(timeout=5)
            return self.create(
                operation=operation,
                gateway=google_calendar.GoogleCalendarGateway(MemoryCalendar(self.events)),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, operation) for operation in ("concurrent-a", "concurrent-b")]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results[0].event_id, results[1].event_id)
        self.assertEqual(sorted(result.reused for result in results), [False, True])
        self.assertEqual(len(self.events.insert_calls), 1)
        self.assertEqual(len(self.events.saved), 1)
        self.assertEqual(self.record().state, "confirmed")

    def test_malformed_existing_fingerprint_is_not_success_or_new_insert(self):
        first = self.create()
        self.events.saved[first.event_id]["extendedProperties"] = None
        with self.assertRaises(CalendarOperationError):
            self.create(operation="malformed-existing-event")
        self.assertEqual(len(self.events.insert_calls), 1)


class CreationContentHashTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime.fromisoformat("2026-10-23T15:00:00-03:00")
        self.end = self.start + timedelta(minutes=30)

    def digest(self, title="Entrevista técnica", description=" Uma descrição. ", start=None, end=None):
        return creation_content_hash(title, description, start or self.start, end or self.end)

    def test_equivalent_unicode_title_trim_and_instants_have_same_hash(self):
        expected = self.digest()
        actual = self.digest(
            title="  " + unicodedata.normalize("NFD", "Entrevista técnica") + "  ",
            description=unicodedata.normalize("NFD", " Uma descrição. "),
            start=self.start.astimezone(timezone.utc),
            end=self.end.astimezone(timezone.utc),
        )
        self.assertEqual(actual, expected)
        self.assertRegex(actual, r"^[0-9a-f]{64}$")

    def test_case_accents_description_spaces_and_schedule_are_significant(self):
        expected = self.digest()
        changed = (
            self.digest(title="entrevista técnica"),
            self.digest(title="Entrevista tecnica"),
            self.digest(description="Uma descrição."),
            self.digest(description=" Outra descrição. "),
            self.digest(start=self.start + timedelta(seconds=1)),
            self.digest(end=self.end + timedelta(seconds=1)),
        )
        for digest in changed:
            with self.subTest(digest=digest):
                self.assertNotEqual(digest, expected)


if __name__ == "__main__":
    unittest.main()
