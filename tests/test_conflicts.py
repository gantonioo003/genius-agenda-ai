"""Conflitos e proteção de escrita usando somente calendário e lock simulados."""

from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httplib2
from googleapiclient.errors import HttpError

from app import check_conflicts as command
from app import google_calendar
from app.database import Database
from app.services.agenda import AgendaEvent, CalendarPeriod
from app.services.conflicts import CalendarConflictError, build_conflict_report, require_available
from app.services.interviews import CalendarOperationError, InterviewDraft
from app.services.rescheduling import ScheduleChange


def timestamp(value):
    return datetime.fromisoformat(value)


def period(start="2026-10-23T16:00:00-03:00", end="2026-10-23T16:30:00-03:00", zone="America/Recife"):
    return CalendarPeriod(timestamp(start), timestamp(end), zone)


def event(event_id="BusyID", start="2026-10-23T16:10:00-03:00", end="2026-10-23T16:20:00-03:00", **changes):
    item = AgendaEvent(event_id, f"Compromisso {event_id}", start, end, False, "America/Recife", "confirmed")
    return replace(item, **changes)


def raw_event(event_id="BusyID", **changes):
    item = {
        "id": event_id, "summary": f"Compromisso {event_id}", "status": "confirmed",
        "start": {"dateTime": "2026-10-23T16:10:00-03:00"},
        "end": {"dateTime": "2026-10-23T16:20:00-03:00"},
        "htmlLink": "https://example.invalid/event", "etag": '"version 1"',
    }
    item.update(changes)
    return item


def google_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": "Fake Google error"}), b"{}",
    )


class ConflictDomainTests(unittest.TestCase):
    def test_exclusive_boundaries_allow_adjacency_and_keep_actual_overlaps(self):
        events = (
            event("Before", "2026-10-23T15:30:00-03:00", "2026-10-23T16:00:00-03:00"),
            event("After", "2026-10-23T16:30:00-03:00", "2026-10-23T17:00:00-03:00"),
        )
        available = build_conflict_report(period(), "Genius Testes", events)
        self.assertEqual(available.status, "available")
        self.assertEqual(available.conflicts, ())
        busy = event("Overlapping", "2026-10-23T15:59:00-03:00", "2026-10-23T16:01:00-03:00")
        report = build_conflict_report(period(), "Genius Testes", (*events, busy))
        self.assertEqual(report.status, "conflict")
        self.assertEqual(report.conflicts, (busy,))
        self.assertEqual(report.start, "2026-10-23T16:00:00-03:00")
        self.assertEqual(report.end, "2026-10-23T16:30:00-03:00")

    def test_midnight_and_different_offsets_compare_absolute_instants(self):
        query = period("2026-10-23T23:45:00-03:00", "2026-10-24T00:15:00-03:00")
        busy = event("CrossesMidnight", "2026-10-24T02:30:00+00:00", "2026-10-24T03:00:00+00:00")
        adjacent = event("EndsAtStart", "2026-10-24T02:00:00+00:00", "2026-10-24T02:45:00+00:00")
        report = build_conflict_report(query, "Genius Testes", (busy, adjacent))
        self.assertEqual(report.conflicts, (busy,))

    def test_all_day_uses_event_calendar_zone_and_exclusive_end(self):
        all_day = event("AllDay", "2026-10-23", "2026-10-24", all_day=True, time_zone="America/New_York")
        cases = (
            ("2026-10-23T00:00:00-03:00", "2026-10-23T00:30:00-03:00", "available"),
            ("2026-10-23T01:00:00-03:00", "2026-10-23T01:30:00-03:00", "conflict"),
            ("2026-10-24T01:00:00-03:00", "2026-10-24T01:30:00-03:00", "available"),
        )
        for start, end, expected in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(build_conflict_report(period(start, end), "Genius Testes", (all_day,)).status, expected)

    def test_repeated_dst_hours_are_distinct_instants_and_all_day_spans_25_hours(self):
        first_fold = event("FirstFold", "2026-11-01T01:20:00-04:00", "2026-11-01T01:30:00-04:00")
        second_fold = event("SecondFold", "2026-11-01T01:20:00-05:00", "2026-11-01T01:30:00-05:00")
        query = period("2026-11-01T01:15:00-05:00", "2026-11-01T01:45:00-05:00", "America/New_York")
        report = build_conflict_report(query, "Genius Testes", (first_fold, second_fold))
        self.assertEqual(report.conflicts, (second_fold,))
        all_day = event("AllDay", "2026-11-01", "2026-11-02", all_day=True, time_zone="America/New_York")
        last_hour = period("2026-11-02T04:15:00+00:00", "2026-11-02T04:45:00+00:00", "UTC")
        self.assertEqual(build_conflict_report(last_hour, "Genius Testes", (all_day,)).status, "conflict")

    def test_exact_exclusion_preserves_case_and_tentative_events_still_occupy_time(self):
        own = event("OwnID")
        other_case = event("ownid", status="tentative")
        cancelled = event("Cancelled", status="cancelled")
        report = build_conflict_report(period(), "Genius Testes", (own, other_case, cancelled), exclude_event_id="OwnID")
        self.assertEqual(report.conflicts, (other_case,))
        self.assertEqual(report.conflicts[0].status, "tentative")

    def test_conflicts_are_sorted_deduplicated_and_changed_duplicate_is_an_error(self):
        later, earlier = event("Later"), event("Earlier", "2026-10-23T16:01:00-03:00", "2026-10-23T16:05:00-03:00")
        report = build_conflict_report(period(), "Genius Testes", (later, earlier, later))
        self.assertEqual(report.conflicts, (earlier, later))
        with self.assertRaises(ValueError):
            build_conflict_report(period(), "Genius Testes", (later, replace(later, title="Changed")))

    def test_invalid_states_dates_periods_and_exclusion_fail_closed(self):
        for bad in (event(status="unknown"), event(start="invalid"), event(end="2026-10-23T16:00:00-03:00"),
                    event(start="2026-10-23T16:10:00"), event(event_id="with space")):
            with self.subTest(event=bad):
                with self.assertRaises(ValueError):
                    build_conflict_report(period(), "Genius Testes", (bad,))
        with self.assertRaises(ValueError):
            build_conflict_report(period(), "Genius Testes", (), exclude_event_id="with space")
        with self.assertRaises(ValueError):
            build_conflict_report(replace(period(), end=period().start), "Genius Testes", ())

    def test_require_available_exposes_exact_report_on_controlled_conflict_error(self):
        available = build_conflict_report(period(), "Genius Testes", ())
        self.assertIsNone(require_available(available))
        report = build_conflict_report(period(), "Genius Testes", (event(),))
        with self.assertRaises(CalendarConflictError) as failure:
            require_available(report)
        self.assertIsInstance(failure.exception, CalendarOperationError)
        self.assertIs(failure.exception.report, report)
        self.assertIn(event().title, str(failure.exception))


class ConflictGatewayTests(unittest.TestCase):
    def setUp(self):
        self.calendar = {
            "id": "genius-test@example.invalid", "summary": "Genius Testes",
            "timeZone": "America/Recife", "accessRole": "owner", "primary": False,
        }
        self.settings = SimpleNamespace(google_calendar_name="Genius Testes", google_calendar_id="")
        self.database = Mock(name="database_without_io")
        self.database.get_metadata.return_value = None
        self.creation_temp = TemporaryDirectory()
        self.addCleanup(self.creation_temp.cleanup)
        self.creation_ledger_number = 0
        self._use_empty_creation_ledger()
        for target, value in (("settings", self.settings), ("database", self.database)):
            patcher = patch.object(google_calendar, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        credentials = patch.object(google_calendar, "get_credentials", side_effect=AssertionError("Sem credenciais."))
        credentials.start()
        self.addCleanup(credentials.stop)
        lock = patch.object(google_calendar, "calendar_write_lock", side_effect=lambda: nullcontext())
        self.lock = lock.start()
        self.addCleanup(lock.stop)
        self.client = Mock(name="fake_google_client")
        self.client.calendarList.return_value.list.return_value.execute.return_value = {"items": [self.calendar]}
        self.events = self.client.events.return_value
        self.events.list.return_value.execute.return_value = {"items": []}
        self.events.insert.return_value.execute.side_effect = self._insert_response
        self.events.patch.return_value.headers = {}
        self.events.patch.return_value.execute.return_value = {"id": "OwnID"}
        for method in ("update", "delete"):
            getattr(self.events, method).side_effect = AssertionError("Operação fora do escopo.")
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        self.query = period()
        self.draft = InterviewDraft("Entrevista fictícia", self.query.start, self.query.end, self.query.time_zone, "Fictícia.")
        self.change = ScheduleChange(self.query.start, self.query.end, self.query.time_zone)
        self.operation_id = "stable-conflict-test"
        self.addCleanup(self._assert_no_other_mutations)

    def _assert_no_other_mutations(self):
        self.events.update.assert_not_called()
        self.events.delete.assert_not_called()

    def _use_empty_creation_ledger(self):
        self.creation_ledger_number += 1
        self.creation_database = Database(
            Path(self.creation_temp.name) / f"creation-{self.creation_ledger_number}.db"
        )
        self.creation_database.initialize()
        for name in ("reserve_creation", "get_creation", "confirm_creation"):
            getattr(self.database, name).side_effect = getattr(self.creation_database, name)

    def assert_read_only(self):
        self.events.insert.assert_not_called()
        self.events.patch.assert_not_called()

    def _insert_response(self, **_arguments):
        self.saved_event = deepcopy(self.events.insert.call_args.kwargs["body"])
        self.saved_event.update(status="confirmed", htmlLink="https://example.invalid/event")
        return deepcopy(self.saved_event)

    def _prepare_create(self):
        self.events.get.return_value.execute.side_effect = google_error(404)

    def _prepare_reschedule(self, *, transparency=None, already_current=False):
        before = raw_event(
            "OwnID", start={"dateTime": "2026-10-22T15:00:00-03:00"},
            end={"dateTime": "2026-10-22T15:30:00-03:00"},
        )
        after = raw_event(
            "OwnID", start={"dateTime": "2026-10-23T16:00:00-03:00"},
            end={"dateTime": "2026-10-23T16:30:00-03:00"}, etag='"version 2"',
        )
        if transparency is not None:
            before["transparency"] = after["transparency"] = transparency
        self.events.get.return_value.execute.side_effect = [after if already_current else before, after]
        return before, after

    def test_read_query_expands_recurring_instances_uses_minimum_fields_and_no_lock(self):
        self.events.list.return_value.execute.return_value = {"items": [raw_event("Series_20261023T191000Z")]}

        result = self.gateway.check_conflicts(self.query)

        self.assertEqual(result.status, "conflict")
        self.assertEqual(result.conflicts[0].event_id, "Series_20261023T191000Z")
        self.events.list.assert_called_once_with(
            calendarId=self.calendar["id"], timeMin="2026-10-23T19:00:00+00:00",
            timeMax="2026-10-23T19:30:00+00:00", timeZone="America/Recife",
            singleEvents=True, showDeleted=False, showHiddenInvitations=True, orderBy="startTime",
            maxResults=250, pageToken=None,
            fields="items(id,summary,status,start,end,htmlLink,transparency),nextPageToken",
        )
        self.events.list.return_value.execute.assert_called_once_with(num_retries=2)
        self.lock.assert_not_called()
        self.assert_read_only()

    def test_fractional_interval_broadens_google_query_but_keeps_exact_local_boundaries(self):
        query = period("2026-10-23T16:00:00.100000-03:00", "2026-10-23T16:00:00.500000-03:00")
        self.events.list.return_value.execute.return_value = {"items": [
            raw_event("Partial", start={"dateTime": "2026-10-23T16:00:00.400000-03:00"},
                      end={"dateTime": "2026-10-23T16:00:00.600000-03:00"}),
            raw_event("Before", start={"dateTime": "2026-10-23T16:00:00.050000-03:00"},
                      end={"dateTime": "2026-10-23T16:00:00.100000-03:00"}),
            raw_event("After", start={"dateTime": "2026-10-23T16:00:00.500000-03:00"},
                      end={"dateTime": "2026-10-23T16:00:00.900000-03:00"}),
        ]}

        report = self.gateway.check_conflicts(query)

        self.assertEqual(report.status, "conflict")
        self.assertEqual([item.event_id for item in report.conflicts], ["Partial"])
        self.assertEqual(report.start, "2026-10-23T16:00:00.100000-03:00")
        self.assertEqual(report.end, "2026-10-23T16:00:00.500000-03:00")
        arguments = self.events.list.call_args.kwargs
        self.assertEqual(arguments["timeMin"], "2026-10-23T19:00:00+00:00")
        self.assertEqual(arguments["timeMax"], "2026-10-23T19:00:01+00:00")
        self.assert_read_only()

    def test_cancelled_and_transparent_do_not_occupy_but_tentative_and_default_opaque_do(self):
        self.events.list.return_value.execute.return_value = {"items": [
            {"id": "Cancelled", "status": "cancelled"},
            raw_event("Transparent", transparency="transparent"),
            raw_event("Tentative", status="tentative", transparency="opaque"),
            raw_event("DefaultOpaque"),
        ]}

        result = self.gateway.check_conflicts(self.query)

        self.assertEqual([item.event_id for item in result.conflicts], ["DefaultOpaque", "Tentative"])
        self.assert_read_only()

    def test_pagination_continues_after_empty_page_and_deduplicates_same_instance(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [], "nextPageToken": "second"},
            {"items": [raw_event("Instance")], "nextPageToken": "third"},
            {"items": [raw_event("Instance"), raw_event("Other")]},
        ]

        result = self.gateway.check_conflicts(self.query)

        self.assertEqual([item.event_id for item in result.conflicts], ["Instance", "Other"])
        self.assertEqual([entry.kwargs["pageToken"] for entry in self.events.list.call_args_list], [None, "second", "third"])
        self.assert_read_only()

    def test_page_loop_changed_duplicate_or_later_error_is_not_partial_availability(self):
        cases = (
            [{"items": [raw_event()], "nextPageToken": "loop"}, {"items": [], "nextPageToken": "loop"}],
            [{"items": [raw_event()], "nextPageToken": "next"}, {"items": [raw_event(summary="Changed")]}],
            [{"items": [raw_event()], "nextPageToken": "next"}, google_error(503)],
            [{"items": [], "nextPageToken": "next"}, TimeoutError("fake second page timeout")],
        )
        for pages in cases:
            with self.subTest(pages=pages):
                self.client.reset_mock()
                self.events.list.return_value.execute.side_effect = pages
                with self.assertRaises(CalendarOperationError):
                    self.gateway.check_conflicts(self.query)
                self.assertEqual(self.events.list.call_count, 2)
                self.assert_read_only()

    def test_malformed_unknown_or_unreadable_results_fail_closed(self):
        missing_status = raw_event()
        del missing_status["status"]
        cases = (
            None, [], {"items": None}, {"items": {}}, {"items": [None]},
            {"items": [], "nextPageToken": 123},
            {"items": [missing_status]}, {"items": [raw_event(status="unknown")]},
            {"items": [raw_event(transparency="unknown")]}, {"items": [raw_event(transparency=None)]},
            {"items": [raw_event(start={})]}, {"items": [raw_event(start={"dateTime": "2026-10-23T16:10:00"})]},
            {"items": [raw_event("with space")]}, google_error(403), TimeoutError("fake query timeout"),
        )
        for response in cases:
            with self.subTest(response=response):
                self.client.reset_mock()
                request = self.events.list.return_value.execute
                request.side_effect = response if isinstance(response, Exception) else None
                request.return_value = response
                with self.assertRaises(CalendarOperationError):
                    self.gateway.check_conflicts(self.query)
                self.assert_read_only()

    def test_raw_all_day_event_uses_calendar_timezone_and_exclusive_end(self):
        self.calendar["timeZone"] = "America/New_York"
        self.events.list.return_value.execute.return_value = {"items": [raw_event(
            "AllDay", start={"date": "2026-10-23"}, end={"date": "2026-10-24"},
        )]}
        for start, end, status in (
            ("2026-10-23T00:00:00-03:00", "2026-10-23T00:30:00-03:00", "available"),
            ("2026-10-23T01:00:00-03:00", "2026-10-23T01:30:00-03:00", "conflict"),
            ("2026-10-24T01:00:00-03:00", "2026-10-24T01:30:00-03:00", "available"),
        ):
            with self.subTest(start=start, end=end):
                result = self.gateway.check_conflicts(period(start, end))
                self.assertEqual(result.status, status)
                if result.conflicts:
                    self.assertEqual(result.conflicts[0].time_zone, "America/New_York")
                    self.assertEqual(result.conflicts[0].end, "2026-10-24")
        self.assert_read_only()

    def test_public_check_preserves_exact_excluded_id_and_validates_before_io(self):
        self.events.list.return_value.execute.return_value = {"items": [raw_event("OwnID"), raw_event("ownid")]}
        result = self.gateway.check_conflicts(self.query, exclude_event_id="OwnID")
        self.assertEqual([item.event_id for item in result.conflicts], ["ownid"])
        self.client.reset_mock()
        for query, excluded in ((replace(self.query, end=self.query.start), None), (self.query, "with space")):
            with self.subTest(query=query, excluded=excluded):
                with self.assertRaises(ValueError):
                    self.gateway.check_conflicts(query, exclude_event_id=excluded)
                self.client.calendarList.assert_not_called()
                self.client.events.assert_not_called()
        self.assert_read_only()

    def test_unsafe_or_missing_pinned_calendar_is_rejected_before_query(self):
        for calendar, configured in ((dict(self.calendar, primary=True), ""),
                                      (dict(self.calendar, accessRole="writer"), ""),
                                      (self.calendar, "missing@example.invalid")):
            with self.subTest(calendar=calendar, configured=configured):
                self.client.reset_mock()
                self.settings.google_calendar_id = configured
                self.client.calendarList.return_value.list.return_value.execute.return_value = {"items": [calendar]}
                with self.assertRaises(google_calendar.CalendarSetupError):
                    self.gateway.check_conflicts(self.query)
                self.client.events.assert_not_called()

    def test_creation_conflict_raises_report_before_insert_on_same_calendar(self):
        self._prepare_create()
        self.events.list.return_value.execute.return_value = {"items": [raw_event()]}

        with self.assertRaises(CalendarConflictError) as failure:
            self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.assertEqual(failure.exception.report.status, "conflict")
        self.assertEqual(failure.exception.report.conflicts[0].event_id, "BusyID")
        self.assertEqual(self.events.list.call_args.kwargs["calendarId"], self.calendar["id"])
        self.client.calendarList.return_value.list.assert_called_once()
        self.events.insert.assert_not_called()
        self.events.patch.assert_not_called()

    def test_available_mutations_check_and_write_within_one_local_lock(self):
        self._prepare_create()
        active = {"value": False}
        observed = []

        @contextmanager
        def tracked_lock():
            self.assertFalse(active["value"])
            active["value"] = True
            try:
                yield
            finally:
                active["value"] = False

        def query(**_arguments):
            self.assertTrue(active["value"])
            observed.append("check")
            return {"items": []}

        def insert(**arguments):
            self.assertTrue(active["value"])
            observed.append("insert")
            return self._insert_response(**arguments)

        self.events.list.return_value.execute.side_effect = query
        self.events.insert.return_value.execute.side_effect = insert
        with patch.object(google_calendar, "calendar_write_lock", side_effect=tracked_lock):
            result = self.gateway.create_event(self.draft, operation_id=self.operation_id)
        self.assertFalse(active["value"])
        self.assertEqual(observed, ["check", "insert"])
        self.assertFalse(result.reused)
        self.assertEqual(self.events.list.call_args.kwargs["calendarId"], self.events.insert.call_args.kwargs["calendarId"])
        self.events.insert.return_value.execute.assert_called_once_with(num_retries=0)
        self.client.reset_mock()
        observed.clear()
        self._prepare_reschedule()

        def apply_patch(**_arguments):
            self.assertTrue(active["value"])
            observed.append("patch")
            return {"id": "OwnID"}

        self.events.patch.return_value.execute.side_effect = apply_patch
        with patch.object(google_calendar, "calendar_write_lock", side_effect=tracked_lock):
            result = self.gateway.reschedule_event("OwnID", self.change, execute=True)
        self.assertFalse(active["value"])
        self.assertEqual(observed, ["check", "patch"])
        self.assertEqual(result.status, "rescheduled")
        self.events.patch.return_value.execute.assert_called_once_with(num_retries=0)

    def test_stable_id_reuse_skips_occupancy_check_and_preserves_identity(self):
        self._prepare_create()
        self.gateway.create_event(self.draft, operation_id=self.operation_id)
        saved = deepcopy(self.saved_event)
        self.client.reset_mock()
        self.events.get.return_value.execute.side_effect = None
        self.events.get.return_value.execute.return_value = saved
        self.events.list.return_value.execute.side_effect = AssertionError("Reutilização não deve verificar ocupação.")

        result = self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.assertTrue(result.reused)
        self.assertEqual(result.event_id, "genius" + sha256(self.operation_id.encode()).hexdigest())
        self.events.list.assert_not_called()
        self.events.insert.assert_not_called()

    def test_create_race_ignores_only_derived_id_and_reuses_confirmed_409_result(self):
        self._prepare_create()
        self.gateway.create_event(self.draft, operation_id=self.operation_id)
        saved = deepcopy(self.saved_event)
        # O 409 representa uma corrida com evento ainda não confirmado neste banco.
        self._use_empty_creation_ledger()
        self.client.reset_mock()
        self.events.get.return_value.execute.side_effect = [google_error(404), saved]
        self.events.list.return_value.execute.return_value = {"items": [saved]}
        self.events.insert.return_value.execute.side_effect = google_error(409)

        result = self.gateway.create_event(self.draft, operation_id=self.operation_id)

        self.assertTrue(result.reused)
        self.assertEqual(result.event_id, saved["id"])
        self.events.list.assert_called_once()
        self.events.insert.return_value.execute.assert_called_once_with(num_retries=0)
        self.assertEqual(self.events.get.call_count, 2)

    def test_reschedule_conflict_blocks_preview_and_execute_before_patch(self):
        for execute in (False, True):
            with self.subTest(execute=execute):
                self.client.reset_mock()
                self._prepare_reschedule()
                self.events.list.return_value.execute.return_value = {"items": [raw_event()]}
                with self.assertRaises(CalendarConflictError) as failure:
                    self.gateway.reschedule_event("OwnID", self.change, execute=execute)
                self.assertEqual(failure.exception.report.conflicts[0].event_id, "BusyID")
                self.events.patch.assert_not_called()
                self.events.insert.assert_not_called()
                self.assertEqual(self.events.get.call_count, 1)

    def test_reschedule_excludes_own_exact_id_and_preserves_etag_when_available(self):
        self._prepare_reschedule()
        self.events.list.return_value.execute.return_value = {"items": [raw_event("OwnID")]}

        result = self.gateway.reschedule_event("OwnID", self.change, execute=True)

        self.assertEqual(result.status, "rescheduled")
        self.assertEqual(self.events.patch.return_value.headers["If-Match"], '"version 1"')
        self.events.patch.return_value.execute.assert_called_once_with(num_retries=0)
        self.assertEqual(self.events.list.call_args.kwargs["calendarId"], self.events.patch.call_args.kwargs["calendarId"])
        self.assertEqual(set(self.events.patch.call_args.kwargs["body"]), {"start", "end"})

    def test_reschedule_noop_skips_check_and_patch_for_preview_and_execute(self):
        for execute, status in ((False, "preview"), (True, "unchanged")):
            with self.subTest(execute=execute):
                self.client.reset_mock()
                self._prepare_reschedule(already_current=True)
                self.events.list.return_value.execute.side_effect = AssertionError("No-op não precisa consultar ocupação.")
                result = self.gateway.reschedule_event("OwnID", self.change, execute=execute)
                self.assertEqual(result.status, status)
                self.events.list.assert_not_called()
                self.events.patch.assert_not_called()

    def test_transparent_event_can_move_without_occupancy_but_must_preserve_transparency(self):
        self._prepare_reschedule(transparency="transparent")
        self.events.list.return_value.execute.side_effect = AssertionError("Evento livre não bloqueia outros horários.")
        result = self.gateway.reschedule_event("OwnID", self.change, execute=True)
        self.assertEqual(result.status, "rescheduled")
        self.events.list.assert_not_called()
        self.assertNotIn("transparency", self.events.patch.call_args.kwargs["body"])
        self.client.reset_mock()
        before, after = self._prepare_reschedule(transparency="transparent")
        after["transparency"] = "opaque"
        self.events.get.return_value.execute.side_effect = [before, after]
        with self.assertRaisesRegex(CalendarOperationError, "pode ter sido aplicada"):
            self.gateway.reschedule_event("OwnID", self.change, execute=True)
        self.events.patch.return_value.execute.assert_called_once_with(num_retries=0)

    def test_conflict_query_failure_blocks_creation_and_rescheduling_without_claiming_free(self):
        for failure in (google_error(403), TimeoutError("fake timeout"), {"items": [raw_event(status="unknown")]}):
            for action in ("create", "reschedule"):
                with self.subTest(action=action, failure=failure):
                    self.client.reset_mock()
                    request = self.events.list.return_value.execute
                    request.side_effect = failure if isinstance(failure, Exception) else None
                    request.return_value = failure
                    if action == "create":
                        self._prepare_create()
                        operation = lambda: self.gateway.create_event(self.draft, operation_id=self.operation_id)
                    else:
                        self._prepare_reschedule()
                        operation = lambda: self.gateway.reschedule_event("OwnID", self.change, execute=True)
                    with self.assertRaises(CalendarOperationError) as error:
                        operation()
                    self.assertNotIsInstance(error.exception, CalendarConflictError)
                    self.assert_read_only()

    def _run_cli(self, arguments=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(command, "calendar_client") as client:
            client.return_value.__enter__.return_value = self.client
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = command.main([
                    "--start", "2026-10-23T16:00:00-03:00", "--end", "2026-10-23T16:30:00-03:00", *arguments,
                ])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_cli_distinguishes_available_and_conflict_as_read_only_reports(self):
        for items, expected in (([], "available"), ([raw_event()], "conflict")):
            with self.subTest(expected=expected):
                self.client.reset_mock()
                self.events.list.return_value.execute.return_value = {"items": items}
                status, stdout, stderr = self._run_cli()
                self.assertEqual(status, 0)
                response = json.loads(stdout)
                self.assertEqual(response["status"], expected)
                self.assertEqual(response["calendar_name"], "Genius Testes")
                self.assertEqual(len(response["conflicts"]), len(items))
                self.assertEqual(stderr, "")
                self.assert_read_only()
        self.events.list.return_value.execute.return_value = {"items": [raw_event("OwnID")]}
        status, stdout, _ = self._run_cli(["--exclude-event-id", "OwnID"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout)["status"], "available")

    def test_cli_query_error_never_prints_available_or_partial_report(self):
        for failure in (google_error(403), TimeoutError("fake timeout")):
            with self.subTest(failure=type(failure).__name__):
                self.events.list.return_value.execute.side_effect = failure
                status, stdout, stderr = self._run_cli()
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.strip())
                self.assert_read_only()

    def test_cli_invalid_interval_offset_zone_and_exclusion_fail_before_client(self):
        valid = ["--start", "2026-10-23T16:00:00-03:00", "--end", "2026-10-23T16:30:00-03:00"]
        cases = (
            ([], 2), (["--start", "2026-10-23", *valid[2:]], 2),
            (["--start", "2026-10-23T16:00:00", *valid[2:]], 2),
            ([*valid, "--timezone", "UTC"], 1), ([*valid, "--timezone", "Invalid/NoSuchZone"], 1),
            ([*valid, "--exclude-event-id", "with space"], 1),
            ([*valid[:3], "2026-10-23T16:00:00-03:00"], 1),
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
