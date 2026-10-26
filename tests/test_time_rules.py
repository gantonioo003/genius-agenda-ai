"""Regras comuns de tempo: instantes UTC, fuso civil e validação antes de I/O."""

from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import httplib2
from googleapiclient.errors import HttpError

from app import google_calendar
from app.database import Database
from app.services import agenda
from app.services.interviews import InterviewDraft, InterviewService
from app.services.rescheduling import RescheduleService, ScheduleChange
from app.services.time_rules import load_zone, validate_event_interval, validate_interval


def timestamp(value):
    return datetime.fromisoformat(value)


def draft(start="2026-10-23T16:00:00-03:00", end="2026-10-23T16:30:00-03:00", zone="America/Recife"):
    return InterviewDraft("Entrevista fictícia", timestamp(start), timestamp(end), zone, "Descrição fictícia.")


VALID_EVENTS = (
    ("2026-10-23T16:00:00-03:00", "2026-10-23T16:30:00-03:00", "America/Recife", 30),
    ("2026-10-22T23:45:00+00:00", "2026-10-23T00:15:00+00:00", "UTC", 30),
    ("2026-03-08T01:30:00-05:00", "2026-03-08T03:30:00-04:00", "America/New_York", 60),
    ("2026-11-01T01:00:00-04:00", "2026-11-01T01:30:00-04:00", "America/New_York", 30),
    ("2026-11-01T01:00:00-05:00", "2026-11-01T01:30:00-05:00", "America/New_York", 30),
    ("2026-11-01T01:45:00-04:00", "2026-11-01T01:15:00-05:00", "America/New_York", 30),
    ("2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00", "America/New_York", 60),
    ("2026-10-23T16:00:00+05:45", "2026-10-23T16:30:00+05:45", "Asia/Kathmandu", 30),
)


INVALID_EVENTS = (
    draft("2026-10-23T16:00:00+00:00", "2026-10-23T16:30:00+00:00", "America/Recife"),
    draft("2026-03-08T02:15:00-05:00", "2026-03-08T03:30:00-04:00", "America/New_York"),
    draft("2026-03-08T02:15:00-04:00", "2026-03-08T03:30:00-04:00", "America/New_York"),
    draft("2026-11-01T01:00:00-03:00", "2026-11-01T01:30:00-03:00", "America/New_York"),
    draft("2026-10-23T16:00:00-03:00", "2026-10-23T16:00:00-03:00"),
    draft("2026-10-23T16:00:00-03:00", "2026-10-23T15:30:00-03:00"),
    draft(zone="Invalid/NoSuchZone"),
    replace(draft(), start=datetime(2026, 10, 23, 16)),
    replace(draft(), end=datetime(2026, 10, 23, 16, 30)),
    replace(draft(), start="2026-10-23T16:00:00-03:00"),
    replace(draft(), end=None),
    replace(draft(), time_zone=123),
)


class CommonTimeRulesTests(unittest.TestCase):
    def test_load_zone_and_legacy_agenda_import_accept_existing_iana_names(self):
        for name in ("America/Recife", "America/New_York", "Asia/Kathmandu", "UTC"):
            with self.subTest(name=name):
                self.assertIsInstance(load_zone(name), ZoneInfo)
                self.assertEqual(load_zone(name).key, name)
                self.assertEqual(agenda.load_zone(name).key, name)

    def test_load_zone_wrong_types_empty_whitespace_and_unknown_names_are_value_errors(self):
        invalid = (None, True, 123, [], {}, b"UTC", ZoneInfo("UTC"), "", " UTC", "UTC ", "Invalid/NoSuchZone")
        for value in invalid:
            for loader in (load_zone, agenda.load_zone):
                with self.subTest(value=value, loader=loader.__module__):
                    with self.assertRaises(ValueError):
                        loader(value)

    def test_absolute_interval_uses_utc_across_offsets_and_local_date_order(self):
        cases = (
            ("2026-10-23T16:00:00-03:00", "2026-10-23T20:00:00+00:00", 60),
            ("2026-10-23T12:00:00+14:00", "2026-10-22T12:00:00-12:00", 120),
            ("2026-10-22T23:45:00+00:00", "2026-10-23T00:15:00+00:00", 30),
            ("2026-11-01T01:45:00-04:00", "2026-11-01T01:15:00-05:00", 30),
        )
        for start, end, minutes in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(validate_interval(timestamp(start), timestamp(end)), timedelta(minutes=minutes))

    def test_same_zoneinfo_object_still_measures_elapsed_23_and_25_hour_days(self):
        zone = ZoneInfo("America/New_York")
        cases = (
            (datetime(2026, 3, 8, tzinfo=zone), datetime(2026, 3, 9, tzinfo=zone), 23),
            (datetime(2026, 11, 1, tzinfo=zone), datetime(2026, 11, 2, tzinfo=zone), 25),
        )
        for start, end, hours in cases:
            with self.subTest(start=start, end=end):
                self.assertEqual(validate_interval(start, end), timedelta(hours=hours))
                self.assertEqual(validate_event_interval(start, end, "America/New_York"), timedelta(hours=hours))

    def test_interval_rejects_naive_non_datetime_equal_and_reverse_instants(self):
        valid = timestamp("2026-10-23T16:00:00-03:00")
        cases = (
            (None, valid), (valid, "2026-10-23"), (date(2026, 10, 23), valid),
            (valid.replace(tzinfo=None), valid), (valid, valid.replace(tzinfo=None)),
            (valid, valid), (valid, valid - timedelta(minutes=1)),
            (timestamp("2026-10-23T16:00:00-03:00"), timestamp("2026-10-23T19:00:00+00:00")),
        )
        for start, end in cases:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    validate_interval(start, end)

    def test_interval_utc_conversion_overflow_is_a_controlled_value_error(self):
        cases = (
            ("0001-01-01T00:00:00+14:00", "0001-01-01T01:00:00+14:00"),
            ("9999-12-31T22:00:00-12:00", "9999-12-31T23:00:00-12:00"),
        )
        for start, end in cases:
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    validate_interval(timestamp(start), timestamp(end))

    def test_event_interval_accepts_real_local_times_both_folds_and_fractional_offsets(self):
        for start, end, zone, minutes in VALID_EVENTS:
            with self.subTest(start=start, end=end, zone=zone):
                self.assertEqual(validate_event_interval(timestamp(start), timestamp(end), zone), timedelta(minutes=minutes))

    def test_event_interval_rejects_gaps_wrong_offsets_and_invalid_temporal_values(self):
        for item in INVALID_EVENTS:
            with self.subTest(start=item.start, end=item.end, zone=item.time_zone):
                with self.assertRaises(ValueError):
                    validate_event_interval(item.start, item.end, item.time_zone)

    def test_event_roundtrip_zone_conversion_overflow_is_a_value_error(self):
        cases = (
            ("0001-01-01T00:00:00+00:00", "0001-01-01T01:00:00+00:00", "America/Recife"),
            ("9999-12-31T22:00:00+00:00", "9999-12-31T23:00:00+00:00", "Asia/Tokyo"),
        )
        for start, end, zone in cases:
            with self.subTest(start=start, end=end, zone=zone):
                self.assertEqual(validate_interval(timestamp(start), timestamp(end)), timedelta(hours=1))
                with self.assertRaises(ValueError):
                    validate_event_interval(timestamp(start), timestamp(end), zone)


class SharedOperationTimeRulesTests(unittest.TestCase):
    def test_creation_and_rescheduling_accept_same_valid_matrix_without_changing_values(self):
        for start, end, zone, _ in VALID_EVENTS:
            with self.subTest(start=start, end=end, zone=zone):
                item = draft(start, end, zone)
                change = ScheduleChange(item.start, item.end, item.time_zone)
                gateway = Mock(name="injected_gateway_without_io")
                created, rescheduled = object(), object()
                gateway.create_event.return_value = created
                gateway.reschedule_event.return_value = rescheduled

                self.assertIs(InterviewService(gateway).create(item, operation_id="stable-request"), created)
                self.assertIs(RescheduleService(gateway).reschedule("ExactEventID", change), rescheduled)
                gateway.create_event.assert_called_once_with(item, operation_id="stable-request")
                gateway.reschedule_event.assert_called_once_with("ExactEventID", change, execute=False)
                self.assertEqual(item.start.isoformat(), start)
                self.assertEqual(item.end.isoformat(), end)

    def test_creation_and_rescheduling_reject_same_matrix_before_injected_gateway(self):
        overflow = (
            draft("0001-01-01T00:00:00+14:00", "0001-01-01T01:00:00+14:00", "Pacific/Kiritimati"),
            draft("9999-12-31T22:00:00-12:00", "9999-12-31T23:00:00-12:00", "Etc/GMT+12"),
        )
        for item in (*INVALID_EVENTS, *overflow):
            with self.subTest(start=item.start, end=item.end, zone=item.time_zone):
                gateway = Mock(name="injected_gateway_without_io")
                with self.assertRaises(ValueError):
                    InterviewService(gateway).create(item, operation_id="stable-request")
                with self.assertRaises(ValueError):
                    RescheduleService(gateway).reschedule(
                        "ExactEventID", ScheduleChange(item.start, item.end, item.time_zone), execute=True,
                    )
                gateway.create_event.assert_not_called()
                gateway.reschedule_event.assert_not_called()

    def test_creation_rejects_invalid_draft_and_text_fields_before_gateway(self):
        cases = (
            (None, "stable-request"), ({}, "stable-request"), ("not a draft", "stable-request"),
            (replace(draft(), title=None), "stable-request"),
            (replace(draft(), title=123), "stable-request"),
            (replace(draft(), title=" \t"), "stable-request"),
            (replace(draft(), description=None), "stable-request"),
            (replace(draft(), description=[]), "stable-request"),
            (replace(draft(), time_zone={}), "stable-request"),
            (draft(), None), (draft(), 123), (draft(), b"stable-request"), (draft(), " \t"),
        )
        for item, operation_id in cases:
            with self.subTest(item=item, operation_id=operation_id):
                gateway = Mock(name="injected_gateway_without_io")
                with self.assertRaises(ValueError):
                    InterviewService(gateway).create(item, operation_id=operation_id)
                gateway.create_event.assert_not_called()

    def test_invalid_events_fail_before_google_calendar_selection_or_any_io(self):
        client = Mock(name="google_client_that_must_not_be_used")
        gateway = google_calendar.GoogleCalendarGateway(client)
        with patch.object(google_calendar, "resolve_calendar", side_effect=AssertionError("Seleção é I/O.")) as resolve:
            for item in INVALID_EVENTS:
                with self.subTest(start=item.start, end=item.end, zone=item.time_zone):
                    with self.assertRaises(ValueError):
                        gateway.create_event(item, operation_id="stable-request")
                    with self.assertRaises(ValueError):
                        gateway.reschedule_event(
                            "ExactEventID", ScheduleChange(item.start, item.end, item.time_zone), execute=True,
                        )
                    resolve.assert_not_called()
                    self.assertEqual(client.mock_calls, [])

    def test_valid_operation_id_keeps_original_identity_in_service_and_google_event_id(self):
        item = draft()
        gateway = Mock(name="injected_gateway_without_io")
        operation_id = "  stable-request  "
        InterviewService(gateway).create(item, operation_id=operation_id)
        gateway.create_event.assert_called_once_with(item, operation_id=operation_id)

        calendar = {"id": "test@example.invalid", "summary": "Genius Testes"}
        client = Mock(name="fake_google_client")
        events = client.events.return_value
        events.list.return_value.execute.return_value = {"items": []}
        events.get.return_value.execute.side_effect = HttpError(
            httplib2.Response({"status": "404", "reason": "Fake not found"}), b"{}",
        )

        def insert(**arguments):
            event = dict(arguments["body"], status="confirmed", htmlLink="https://example.invalid/event")
            return SimpleNamespace(execute=Mock(return_value=event))

        events.insert.side_effect = insert
        creation_temp = TemporaryDirectory()
        self.addCleanup(creation_temp.cleanup)
        creation_database = Database(Path(creation_temp.name) / "creation.db")
        creation_database.initialize()
        database = Mock(name="temporary_creation_database")
        for name in ("reserve_creation", "get_creation", "confirm_creation"):
            getattr(database, name).side_effect = getattr(creation_database, name)
        with (
            patch.object(google_calendar, "resolve_calendar", return_value=calendar),
            patch.object(google_calendar, "calendar_write_lock", nullcontext),
            patch.object(google_calendar, "database", database),
        ):
            result = google_calendar.GoogleCalendarGateway(client).create_event(item, operation_id=operation_id)
        self.assertEqual(result.event_id, "genius" + sha256(operation_id.encode("utf-8")).hexdigest())
        self.assertNotEqual(result.event_id, "genius" + sha256(operation_id.strip().encode("utf-8")).hexdigest())

    def test_query_period_accepts_utc_bounds_in_another_display_timezone(self):
        start = timestamp("2026-10-23T19:00:00+00:00")
        end = timestamp("2026-10-23T19:30:00+00:00")
        period = agenda.CalendarPeriod(start, end, "America/Recife")
        self.assertEqual(agenda.validate_period(period).key, "America/Recife")
        with self.assertRaises(ValueError):
            validate_event_interval(start, end, "America/Recife")
        client = Mock(name="fake_google_client")
        client.events.return_value.list.return_value.execute.return_value = {"items": []}
        calendar = {"id": "test@example.invalid", "summary": "Genius Testes", "timeZone": "America/Recife"}
        with patch.object(google_calendar, "resolve_calendar", return_value=calendar):
            listing = google_calendar.GoogleCalendarGateway(client).list_events(period)
        self.assertEqual(listing.events, ())
        arguments = client.events.return_value.list.call_args.kwargs
        self.assertEqual(arguments["timeMin"], "2026-10-23T19:00:00+00:00")
        self.assertEqual(arguments["timeMax"], "2026-10-23T19:30:00+00:00")
        self.assertEqual(arguments["timeZone"], "America/Recife")
        for method in ("insert", "update", "patch", "delete"):
            getattr(client.events.return_value, method).assert_not_called()

    def test_invalid_query_bounds_or_zone_fail_before_calendar_selection(self):
        good = agenda.CalendarPeriod(
            timestamp("2026-10-23T19:00:00+00:00"), timestamp("2026-10-23T19:30:00+00:00"), "America/Recife",
        )
        cases = (
            replace(good, time_zone=123), replace(good, start=good.start.replace(tzinfo=None)),
            replace(good, end=good.start),
            agenda.CalendarPeriod(timestamp("0001-01-01T00:00:00+14:00"),
                                 timestamp("0001-01-01T01:00:00+14:00"), "UTC"),
        )
        client = Mock(name="google_client_that_must_not_be_used")
        with patch.object(google_calendar, "resolve_calendar", side_effect=AssertionError("Seleção é I/O.")) as resolve:
            for period in cases:
                with self.subTest(period=period):
                    with self.assertRaises(ValueError):
                        google_calendar.GoogleCalendarGateway(client).list_events(period)
                    resolve.assert_not_called()
                    self.assertEqual(client.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
