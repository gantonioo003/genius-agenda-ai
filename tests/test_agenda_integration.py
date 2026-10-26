"""Percursos completos com SDK/transporte reais e HTTP em memória.

Somente a sessão HTTP simula o Google. Os serviços, a descoberta da agenda,
o adaptador, os ETags e o SQLite executam seu código de produção.
"""

from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app import google_calendar
from app.database import Database
from app.google_transport import SingleAttemptHttp
from app.services.agenda import AgendaService, CalendarPeriod, day_period
from app.services.conflicts import CalendarConflictError
from app.services.creation_identity import creation_content_hash
from app.services.event_deletion import EventDeletionService
from app.services.event_details import DetailsChange, EventDetailsService
from app.services.event_selection import EventSelectionService
from app.services.interviews import CalendarOperationError, InterviewDraft, InterviewService
from app.services.request_preparation import RequestPreparationService
from app.services.rescheduling import RescheduleService, ScheduleChange
from calendar_http_fake import CalendarHttpFake


ZONE = "America/Recife"
DAY = date(2030, 6, 17)


def instant(value):
    return datetime.fromisoformat(value)


def draft(title="Entrevista técnica", *, start="10:00", end="10:30", day=DAY):
    return InterviewDraft(
        title,
        instant(f"{day.isoformat()}T{start}:00-03:00"),
        instant(f"{day.isoformat()}T{end}:00-03:00"),
        ZONE,
        "Processo seletivo fictício",
    )


class AgendaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(TemporaryDirectory())
        self.db_path = Path(temporary) / "agenda.sqlite3"
        self.db = Database(self.db_path)
        self.backend = CalendarHttpFake()
        self.stack.enter_context(patch("app.google_transport.requests.Session", side_effect=self.backend.session))
        self.stack.enter_context(patch.object(google_calendar, "database", self.db))
        self.stack.enter_context(patch.object(google_calendar, "settings", SimpleNamespace(
            google_calendar_name=self.backend.calendar["summary"],
            google_calendar_id=self.backend.calendar["id"],
        )))
        self.stack.enter_context(patch.object(google_calendar, "get_credentials", side_effect=AssertionError(
            "Este teste não pode consultar credenciais locais."
        )))
        # Mantém as tentativas reais do SDK e elimina somente a espera aleatória.
        self.stack.enter_context(patch("googleapiclient.http.time.sleep", return_value=None))
        self.gateway = self.new_gateway()

    def new_gateway(self):
        transport = SingleAttemptHttp(timeout=20)
        authorized = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
        service = build(
            "calendar", "v3", http=authorized, cache_discovery=False,
            static_discovery=True,
        )
        self.stack.callback(service.close)
        return google_calendar.GoogleCalendarGateway(service)

    def writes(self, method=None):
        return [call for call in self.backend.calls
                if call["method"] in {"POST", "PATCH", "DELETE"}
                and (method is None or call["method"] == method)]

    def creation_record(self, value):
        content = creation_content_hash(value.title, value.description, value.start, value.end)
        return Database(self.db_path).get_creation(self.backend.calendar["id"], content)

    def create(self, value, operation_id):
        return InterviewService(self.gateway).create(value, operation_id=operation_id)

    def delete(self, event_id):
        service = EventDeletionService(self.gateway)
        preview = service.delete(event_id)
        self.assertEqual(preview.status, "needs_confirmation")
        return service.delete(event_id, execute=True, confirmation_token=preview.confirmation_token)

    def test_full_lifecycle_preserves_identity_then_refuses_recreation_after_deletion(self):
        original = draft()
        ready = RequestPreparationService(self.gateway).prepare({
            "action": "create", "title": original.title,
            "description": original.description, "start": original.start.isoformat(),
            "end": original.end.isoformat(), "time_zone": ZONE,
        })
        self.assertEqual(ready.status, "ready")
        self.assertEqual(self.backend.calls, [])
        prepared = InterviewDraft(
            title=ready.parameters["title"], start=instant(ready.parameters["start"]),
            end=instant(ready.parameters["end"]), time_zone=ready.parameters["time_zone"],
            description=ready.parameters["description"],
        )
        created = self.create(prepared, "lifecycle-original")
        self.assertFalse(created.reused)
        self.assertEqual(self.creation_record(original).state, "confirmed")
        self.assertEqual(set(self.backend.saved), {created.event_id})

        agenda = AgendaService(self.gateway)
        for listing in (agenda.list_day(DAY), agenda.list_week(DAY), agenda.list_month(DAY)):
            self.assertEqual([event.event_id for event in listing.events], [created.event_id])
        selected = EventSelectionService(self.gateway).select(
            title="entrevista tecnica", period=day_period(DAY, ZONE), time_zone=ZONE,
        )
        self.assertEqual(selected.event.event_id, created.event_id)
        changed = EventDetailsService(self.gateway).update(
            selected.event.event_id,
            DetailsChange(title="Entrevista final", description="Preparar portfólio", location="Sala virtual"),
            execute=True,
        )
        self.assertEqual(changed.status, "updated")
        self.assertEqual((changed.after.title, changed.after.description, changed.after.location),
                         ("Entrevista final", "Preparar portfólio", "Sala virtual"))
        new_time = draft(start="11:00", end="11:45")
        moved = RescheduleService(self.gateway).reschedule(
            created.event_id, ScheduleChange(new_time.start, new_time.end, ZONE), execute=True,
        )
        self.assertEqual(moved.status, "rescheduled")
        self.assertEqual(moved.after.start, new_time.start.isoformat())
        self.assertEqual(moved.after.title, "Entrevista final")
        self.assertEqual(self.delete(created.event_id).status, "deleted")
        self.assertIsNone(self.gateway.get_event(created.event_id).event)
        self.assertEqual(self.backend.saved, {})
        with self.assertRaisesRegex(CalendarOperationError, "não recria"):
            self.create(original, "lifecycle-alias-after-delete")
        self.assertEqual(self.creation_record(original).state, "confirmed")
        self.assertEqual([call["method"] for call in self.writes()], ["POST", "PATCH", "PATCH", "DELETE"])
        self.assertEqual({call["event_id"] for call in self.writes()}, {created.event_id})
        for call in self.writes():
            self.assertEqual(call["query"]["sendUpdates"], "none")
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM creation_operations").fetchone()[0], 2)

    def test_conflict_and_replays_share_persisted_identity_then_pending_can_proceed(self):
        first_draft = draft("Entrevista A")
        first = self.create(first_draft, "conflicts-a")
        second_draft = draft("Entrevista B", start="10:30", end="11:00")
        second = self.create(second_draft, "conflicts-b")
        # Uma primeira página vazia não autoriza ignorar a página seguinte.
        self.backend.empty_first_page = True
        self.backend.page_size = 1
        blocked_draft = draft("Entrevista C", start="10:15", end="10:30")
        with self.assertRaises(CalendarConflictError) as creation_error:
            self.create(blocked_draft, "conflicts-c")
        self.assertEqual([event.event_id for event in creation_error.exception.report.conflicts], [first.event_id])
        self.assertTrue(any(call["query"].get("pageToken") == "page-0"
                            for call in self.backend.calls))
        reserved = self.creation_record(blocked_draft)
        self.assertEqual(reserved.state, "pending")
        self.assertNotIn(reserved.event_id, self.backend.saved)
        move = draft(start="10:15", end="10:45")
        with self.assertRaises(CalendarConflictError):
            RescheduleService(self.gateway).reschedule(
                second.event_id, ScheduleChange(move.start, move.end, ZONE), execute=True,
            )
        self.assertEqual(self.backend.saved[second.event_id]["start"]["dateTime"], second_draft.start.isoformat())
        self.assertEqual(len(self.writes()), 2)
        for operation_id in ("conflicts-a", "conflicts-a-new-message"):
            repeated = self.create(first_draft, operation_id)
            self.assertTrue(repeated.reused)
            self.assertEqual(repeated.event_id, first.event_id)
        self.assertEqual(len(self.writes("POST")), 2)
        self.assertEqual(self.delete(first.event_id).status, "deleted")
        unblocked = self.create(blocked_draft, "conflicts-c")
        self.assertEqual(unblocked.event_id, reserved.event_id)
        self.assertFalse(unblocked.reused)
        self.assertEqual(self.creation_record(blocked_draft).state, "confirmed")
        self.assertEqual(set(self.backend.saved), {second.event_id, reserved.event_id})
        self.assertEqual(len(self.writes("POST")), 3)
        self.assertEqual(len(self.writes("PATCH")), 0)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM creation_requests").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM creation_operations").fetchone()[0], 4)

    def test_lost_creation_and_failed_reconciliation_reopen_with_new_message_without_posting_again(self):
        value = draft()
        self.backend.lose_response_once.add("POST")
        self.backend.fail_reads_after_write = 3
        with self.assertRaises(CalendarOperationError):
            self.create(value, "uncertain-original-message")
        reservation = self.creation_record(value)
        self.assertEqual(reservation.state, "pending")
        self.assertEqual(set(self.backend.saved), {reservation.event_id})
        self.assertEqual(len(self.writes("POST")), 1)
        failed_reads = [call for call in self.backend.calls
                        if call["method"] == "GET" and call["status"] == 503]
        self.assertEqual(len(failed_reads), 3)
        self.assertEqual({call["event_id"] for call in failed_reads}, {reservation.event_id})

        # Novo objeto de banco e novo cliente: nenhum estado em memória do gateway anterior.
        self.db = Database(self.db_path)
        google_calendar.database = self.db
        reopened_gateway = self.new_gateway()
        recovered = InterviewService(reopened_gateway).create(value, operation_id="uncertain-new-message")
        self.assertTrue(recovered.reused)
        self.assertEqual(recovered.event_id, reservation.event_id)
        self.assertEqual(self.creation_record(value).state, "confirmed")
        self.assertEqual(len(self.writes("POST")), 1)
        self.assertEqual(set(self.backend.saved), {reservation.event_id})
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM creation_operations").fetchone()[0], 2)

    def test_recovered_patch_invalidates_old_confirmation_then_recovered_delete_is_single_attempt(self):
        value = draft()
        created = self.create(value, "confirmation-and-loss")
        deletion = EventDeletionService(self.gateway)
        old_preview = deletion.delete(created.event_id)
        self.backend.lose_response_once.add("PATCH")
        shifted = draft(start="12:00", end="12:30")
        moved = RescheduleService(self.gateway).reschedule(
            created.event_id, ScheduleChange(shifted.start, shifted.end, ZONE), execute=True,
        )
        self.assertTrue(moved.recovered)
        self.assertEqual(moved.after.start, shifted.start.isoformat())
        with self.assertRaisesRegex(CalendarOperationError, "confirmação não corresponde"):
            deletion.delete(created.event_id, execute=True, confirmation_token=old_preview.confirmation_token)
        self.assertEqual(len(self.writes("DELETE")), 0)
        new_preview = deletion.delete(created.event_id)
        self.assertNotEqual(old_preview.confirmation_token, new_preview.confirmation_token)
        self.backend.lose_response_once.add("DELETE")
        removed = deletion.delete(created.event_id, execute=True, confirmation_token=new_preview.confirmation_token)
        self.assertEqual(removed.status, "deleted")
        self.assertTrue(removed.recovered)
        self.assertEqual(self.backend.saved, {})
        self.assertEqual(len(self.writes("PATCH")), 1)
        self.assertEqual(len(self.writes("DELETE")), 1)
        self.assertEqual(self.creation_record(value).state, "confirmed")

    def test_ambiguous_request_is_read_only_and_exact_selection_changes_only_chosen_event(self):
        first = self.create(draft("Entrevista técnica A"), "ambiguity-a")
        second = self.create(draft("Entrevista técnica B", start="11:00", end="11:30"), "ambiguity-b")
        unchanged_first = deepcopy(self.backend.saved[first.event_id])
        preparation = RequestPreparationService(self.gateway)
        result = preparation.prepare({
            "action": "edit", "search_title": "entrevista tecnica", "date": DAY.isoformat(),
            "location": "Sala escolhida", "time_zone": ZONE,
        })
        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual({event.event_id for event in result.candidates}, {first.event_id, second.event_id})
        self.assertIsNone(result.parameters)
        self.assertEqual(len(self.writes()), 2)
        ready = preparation.prepare({
            "action": "edit", "event_id": second.event_id, "location": "Sala escolhida", "time_zone": ZONE,
        })
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.parameters["event_id"], second.event_id)
        self.assertEqual(len(self.writes()), 2)
        outcome = EventDetailsService(self.gateway).update(
            ready.parameters["event_id"], DetailsChange(location=ready.parameters["location"]), execute=True,
        )
        self.assertEqual(outcome.after.location, "Sala escolhida")
        self.assertEqual(self.backend.saved[first.event_id], unchanged_first)
        self.assertEqual(len(self.writes("PATCH")), 1)
        self.assertEqual(self.writes("PATCH")[0]["event_id"], second.event_id)

    def test_concurrent_etag_change_refuses_patch_without_reapplying_or_losing_external_changes(self):
        value = draft()
        created = self.create(value, "concurrent-version")
        previous_etag = self.backend.saved[created.event_id]["etag"]

        def concurrent_change(method, event_id, body):
            if method == "PATCH":
                self.backend.saved[event_id]["etag"] = '"external-v99"'
                self.backend.saved[event_id]["summary"] = "Alteração feita em outro cliente"
                self.backend.saved[event_id]["location"] = "Local externo"

        self.backend.before_write = concurrent_change
        request_offset = len(self.backend.calls)
        with self.assertRaisesRegex(CalendarOperationError, "mudou durante a edição"):
            EventDetailsService(self.gateway).update(
                created.event_id, DetailsChange(location="Minha alteração"), execute=True,
            )
        attempted = self.writes("PATCH")
        self.assertEqual(len(attempted), 1)
        self.assertEqual(attempted[0]["status"], 412)
        headers = {key.lower(): value for key, value in attempted[0]["headers"].items()}
        self.assertEqual(headers["if-match"], previous_etag)
        operation_calls = self.backend.calls[request_offset:]
        self.assertEqual(operation_calls[-1]["method"], "PATCH")
        self.assertEqual(sum(call["method"] == "GET" and call["event_id"] == created.event_id
                             for call in operation_calls), 1)
        current = self.gateway.get_event(created.event_id).event
        self.assertEqual(current.title, "Alteração feita em outro cliente")
        self.assertEqual(self.backend.saved[created.event_id]["location"], "Local externo")
        self.assertEqual(self.backend.saved[created.event_id]["start"]["dateTime"], value.start.isoformat())
        self.assertEqual(self.creation_record(value).state, "confirmed")

    def test_cross_midnight_queries_and_utc_replay_agree_on_exclusive_end(self):
        value = InterviewDraft(
            "Entrevista internacional", instant("2030-06-17T23:45:00-03:00"),
            instant("2030-06-18T00:15:00-03:00"), ZONE,
        )
        created = self.create(value, "overnight-original")
        agenda = AgendaService(self.gateway)
        for reference in (DAY, date(2030, 6, 18)):
            self.assertEqual([event.event_id for event in agenda.list_day(reference).events], [created.event_id])
        utc_events = agenda.list_day(date(2030, 6, 18), time_zone="UTC").events
        self.assertEqual(utc_events[0].event_id, created.event_id)
        self.assertEqual(instant(utc_events[0].start), instant("2030-06-18T02:45:00+00:00"))
        conflict_period = CalendarPeriod(
            instant("2030-06-18T00:00:00-03:00"), instant("2030-06-18T00:10:00-03:00"), ZONE,
        )
        self.assertEqual(self.gateway.check_conflicts(conflict_period).status, "conflict")
        adjacent_period = CalendarPeriod(
            value.end, instant("2030-06-18T00:30:00-03:00"), ZONE,
        )
        self.assertEqual(self.gateway.check_conflicts(adjacent_period).status, "available")
        alias = InterviewDraft(value.title, instant("2030-06-18T02:45:00+00:00"),
                               instant("2030-06-18T03:15:00+00:00"), "UTC")
        replay = self.create(alias, "overnight-utc-alias")
        self.assertTrue(replay.reused)
        self.assertEqual(replay.event_id, created.event_id)
        self.assertEqual(len(self.writes("POST")), 1)
        self.assertEqual(set(self.backend.saved), {created.event_id})
        self.assertEqual(self.creation_record(alias), self.creation_record(value))

    def test_primary_calendar_is_rejected_during_discovery_before_reservation_or_mutation(self):
        self.backend.calendar["primary"] = True
        with self.assertRaisesRegex(google_calendar.CalendarSetupError, "agenda principal"):
            self.create(draft(), "unsafe-calendar")
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.backend.saved, {})
        self.assertTrue(self.backend.calls)
        self.assertTrue(all(call["method"] == "GET" for call in self.backend.calls))
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM creation_requests").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM creation_operations").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
