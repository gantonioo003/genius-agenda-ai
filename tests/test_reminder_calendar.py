"""Snapshot Google simulado; sem OAuth, sockets ou dados da agenda real."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch

from googleapiclient.errors import HttpError
import httplib2

from app.reminder_calendar import GoogleReminderCalendarGateway, ReminderCalendarError


UTC = timezone.utc
START = datetime(2026, 11, 1, 0, tzinfo=UTC)
END = START + timedelta(days=2)
CALENDAR = {"id": "reminders@example.invalid", "timeZone": "America/Recife"}


def event(event_id="instance-1", **changes):
    result = {
        "id": event_id, "summary": "Entrevista fictícia", "status": "confirmed",
        "start": {"dateTime": "2026-11-01T18:00:00-03:00"},
        "end": {"dateTime": "2026-11-01T19:00:00-03:00"},
    }
    result.update(changes)
    return result


class ReminderCalendarTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.gateway = GoogleReminderCalendarGateway(self.service)
        self.resolve = patch("app.reminder_calendar.resolve_calendar", return_value=deepcopy(CALENDAR))
        self.resolve_mock = self.resolve.start()
        self.addCleanup(self.resolve.stop)
        self.request = self.service.events.return_value.list.return_value

    def pages(self, *pages):
        self.request.execute.side_effect = list(pages)

    def snapshot(self):
        return self.gateway.snapshot(START, END, expected_calendar_id=CALENDAR["id"])

    def invalid(self, item):
        self.pages({"items": [item]})
        with self.assertRaises(ReminderCalendarError):
            self.snapshot()

    def test_snapshot_carries_real_binding_and_utc_instants(self):
        self.pages({"items": [event()]})
        result = self.snapshot()
        self.assertEqual((result.calendar_id, result.calendar_timezone),
                         (CALENDAR["id"], CALENDAR["timeZone"]))
        self.assertEqual(result.events[0].start_utc, datetime(2026, 11, 1, 21, tzinfo=UTC))
        self.assertEqual(result.events[0].end_utc, datetime(2026, 11, 1, 22, tzinfo=UTC))
        self.resolve_mock.assert_called_once_with(self.service)
        self.assertEqual(self.request.execute.call_args.kwargs, {"num_retries": 2})
        query = self.service.events.return_value.list.call_args.kwargs
        self.assertEqual(query["calendarId"], CALENDAR["id"])
        self.assertEqual(query["timeMin"], START.isoformat())
        self.assertEqual(query["timeMax"], END.isoformat())
        self.assertEqual(query["timeZone"], CALENDAR["timeZone"])
        self.assertIs(query["singleEvents"], True)
        self.assertIs(query["showDeleted"], False)
        for name in ("recurrence", "recurringEventId", "originalStartTime"):
            self.assertIn(name, query["fields"])
        self.service.events.return_value.insert.assert_not_called()
        self.service.events.return_value.patch.assert_not_called()
        self.service.events.return_value.delete.assert_not_called()

    def test_query_bounds_convert_other_offsets_to_utc(self):
        self.pages({"items": []})
        self.gateway.snapshot(START.astimezone(timezone(timedelta(hours=9))),
                              END.astimezone(timezone(timedelta(hours=-5))))
        query = self.service.events.return_value.list.call_args.kwargs
        self.assertEqual((query["timeMin"], query["timeMax"]), (START.isoformat(), END.isoformat()))

    def test_fractional_bounds_are_expanded_for_google_and_filtered_exactly(self):
        start = START + timedelta(microseconds=500000)
        end = END + timedelta(microseconds=500000)
        inside = event("inside", start={"dateTime": (END + timedelta(microseconds=250000)).isoformat()},
                       end={"dateTime": (END + timedelta(hours=1)).isoformat()})
        outside = event("outside", start={"dateTime": (END + timedelta(microseconds=750000)).isoformat()},
                        end={"dateTime": (END + timedelta(hours=1)).isoformat()})
        before = event("before", start={"dateTime": (START - timedelta(hours=1)).isoformat()},
                       end={"dateTime": (START + timedelta(microseconds=250000)).isoformat()})
        self.pages({"items": [outside, inside, before]})
        snapshot = self.gateway.snapshot(start, end)
        self.assertEqual(tuple(entry.event_id for entry in snapshot.events), ("inside",))
        query = self.service.events.return_value.list.call_args.kwargs
        self.assertEqual(query["timeMin"], START.isoformat())
        self.assertEqual(query["timeMax"], (END + timedelta(seconds=1)).isoformat())

    def test_binding_has_no_fallback_and_exposes_no_values_in_repr(self):
        binding = self.gateway.binding()
        self.assertEqual(binding.calendar_id, CALENDAR["id"])
        self.assertEqual(binding.calendar_timezone, "America/Recife")
        self.assertNotIn(CALENDAR["id"], repr(binding))
        self.service.events.assert_not_called()

    def test_wrong_expected_binding_prevents_event_query(self):
        with self.assertRaises(ReminderCalendarError):
            self.gateway.snapshot(START, END, "another@example.invalid")
        self.service.events.assert_not_called()

    def test_missing_or_invalid_binding_fails_before_event_query(self):
        for calendar in ({"id": CALENDAR["id"]}, {"timeZone": "America/Recife"},
                         dict(CALENDAR, timeZone=""), dict(CALENDAR, timeZone="Mars/Olympus"),
                         dict(CALENDAR, timeZone=" America/Recife"),
                         dict(CALENDAR, id="primary"), dict(CALENDAR, id="bad\ud800")):
            with self.subTest(calendar=ascii(calendar)):
                self.resolve_mock.return_value = calendar
                with self.assertRaises(ReminderCalendarError):
                    self.snapshot()
        self.service.events.assert_not_called()

    def test_invalid_windows_do_not_even_resolve_calendar(self):
        for start, end in ((START.replace(tzinfo=None), END), (START, START),
                           (END, START), ("2026-11-01", END)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ReminderCalendarError):
                    self.gateway.snapshot(start, end)
        self.resolve_mock.assert_not_called()

    def test_expanded_occurrences_keep_own_ids_including_moved_start(self):
        first = event("series_20261101T210000Z", recurringEventId="series",
                      originalStartTime={"dateTime": "2026-11-01T17:00:00-03:00"})
        second = event("series_20261102T210000Z", recurringEventId="series",
                       originalStartTime={"dateTime": "2026-11-02T18:00:00-03:00"},
                       start={"dateTime": "2026-11-02T18:00:00-03:00"},
                       end={"dateTime": "2026-11-02T19:00:00-03:00"})
        self.pages({"items": [second, first]})
        entries = self.snapshot().events
        self.assertEqual(tuple(entry.event_id for entry in entries), (first["id"], second["id"]))
        self.assertEqual(tuple(entry.recurring_event_id for entry in entries), ("series", "series"))
        self.assertTrue(all(entry.expanded for entry in entries))

    def test_series_resource_is_never_used_as_an_occurrence(self):
        for rules in (["RRULE:FREQ=DAILY"], [], None, "RRULE:FREQ=DAILY"):
            with self.subTest(rules=rules):
                self.invalid(event(recurrence=rules))

    def test_incomplete_or_incoherent_occurrence_is_rejected(self):
        for changes in (
            {"recurringEventId": "series"},
            {"originalStartTime": {"dateTime": "2026-11-01T18:00:00-03:00"}},
            {"recurringEventId": "instance-1", "originalStartTime": event()["start"]},
            {"recurringEventId": "series", "originalStartTime": {"date": "2026-11-01"}},
            {"recurringEventId": "series", "originalStartTime": {"dateTime": "2026-11-01T18:00:00"}},
        ):
            with self.subTest(changes=changes):
                self.invalid(event(**changes))

    def test_all_day_and_tentative_are_preserved_for_service_to_skip(self):
        all_day = event("whole-day", start={"date": "2026-11-01"}, end={"date": "2026-11-02"})
        self.pages({"items": [event(status="tentative"), all_day]})
        entries = self.snapshot().events
        self.assertEqual(entries[0].start_utc, datetime(2026, 11, 1, 3, tzinfo=UTC))
        self.assertTrue(entries[0].all_day)
        self.assertEqual(entries[1].status, "tentative")

    def test_cancelled_tombstone_without_dates_is_ignored(self):
        self.pages({"items": [{"id": "gone", "status": "cancelled"}, event()]})
        self.assertEqual(len(self.snapshot().events), 1)

    def test_unknown_or_missing_status_fails_closed(self):
        for status in (None, "unknown", "", 0, []):
            with self.subTest(status=status):
                self.invalid(event(status=status))
        missing = event()
        del missing["status"]
        self.invalid(missing)

    def test_date_shapes_offsets_and_intervals_are_strict(self):
        for changes in (
            {"start": {}}, {"start": None},
            {"start": {"date": "2026-11-01", "dateTime": "2026-11-01T18:00:00-03:00"}},
            {"start": {"date": "2026-11-01"}},
            {"start": {"dateTime": "2026-11-01T18:00:00"}},
            {"start": {"dateTime": "2026-11-01 18:00:00-03:00"}},
            {"start": {"dateTime": "2026-11-01T25:00:00-03:00"}},
            {"start": {"dateTime": "2026-11-01T18:00:00-03:00", "timeZone": "Mars/Olympus"}},
            {"end": {"dateTime": "2026-11-01T18:00:00-03:00"}},
            {"end": {"dateTime": "2026-11-01T17:59:00-03:00"}},
            {"summary": False}, {"summary": None}, {"summary": "bad\ud800"},
        ):
            with self.subTest(changes=ascii(changes)):
                self.invalid(event(**changes))

    def test_explicit_offsets_preserve_elapsed_time_across_repeated_dst(self):
        self.resolve_mock.return_value = dict(CALENDAR, timeZone="America/New_York")
        self.pages({"items": [event(
            start={"dateTime": "2026-11-01T01:30:00-04:00", "timeZone": "America/New_York"},
            end={"dateTime": "2026-11-01T01:30:00-05:00", "timeZone": "America/New_York"},
        )]})
        entry = self.snapshot().events[0]
        self.assertEqual(entry.end_utc - entry.start_utc, timedelta(hours=1))

    def test_response_offsets_are_authoritative_when_original_zone_differs(self):
        self.pages({"items": [event(
            "series_20261101T210000Z", recurringEventId="series",
            start={"dateTime": "2026-11-01T18:00:00-03:00", "timeZone": "America/New_York"},
            end={"dateTime": "2026-11-01T19:00:00-03:00", "timeZone": "America/New_York"},
            originalStartTime={"dateTime": "2026-11-01T20:00:00Z", "timeZone": "America/New_York"},
        )]})
        snapshot = self.snapshot()
        entry, = snapshot.events
        self.assertEqual(snapshot.calendar_timezone, "America/Recife")
        self.assertEqual(entry.start_utc, datetime(2026, 11, 1, 21, tzinfo=UTC))
        self.assertEqual(entry.end_utc, datetime(2026, 11, 1, 22, tzinfo=UTC))
        self.assertEqual(entry.recurring_event_id, "series")
        query = self.service.events.return_value.list.call_args.kwargs
        self.assertEqual(query["timeZone"], "America/Recife")

    def test_response_offsets_do_not_recreate_wall_time_in_original_zone(self):
        self.resolve_mock.return_value = dict(CALENDAR, timeZone="Etc/GMT+5")
        self.pages({"items": [event(
            start={"dateTime": "2026-03-08T02:30:00-05:00", "timeZone": "America/New_York"},
            end={"dateTime": "2026-03-08T03:30:00-05:00", "timeZone": "America/New_York"},
        )]})
        snapshot = self.gateway.snapshot(datetime(2026, 3, 8, tzinfo=UTC),
                                         datetime(2026, 3, 9, tzinfo=UTC))
        entry, = snapshot.events
        self.assertEqual(entry.start_utc, datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
        self.assertEqual(entry.end_utc, datetime(2026, 3, 8, 8, 30, tzinfo=UTC))

    def test_missing_summary_stays_minimal_for_domain_normalization(self):
        item = event()
        del item["summary"]
        self.pages({"items": [item]})
        self.assertEqual(self.snapshot().events[0].title, "")

    def test_invalid_event_id_including_tombstone_is_rejected(self):
        for event_id in (None, "", "a b", "x\x00", "x\ud800"):
            with self.subTest(event_id=ascii(event_id)):
                self.invalid({"id": event_id, "status": "cancelled"})

    def test_valid_events_outside_overlap_window_are_excluded(self):
        before = event("before", start={"dateTime": "2026-10-31T23:00:00Z"},
                       end={"dateTime": START.isoformat()})
        after = event("after", start={"dateTime": END.isoformat()},
                      end={"dateTime": (END + timedelta(hours=1)).isoformat()})
        self.pages({"items": [before, after, event()]})
        self.assertEqual(tuple(entry.event_id for entry in self.snapshot().events), ("instance-1",))

    def test_empty_first_page_and_duplicate_across_pages_are_complete(self):
        self.pages({"items": [], "nextPageToken": "first"},
                   {"items": [event()], "nextPageToken": "second"},
                   {"items": [event()]})
        self.assertEqual(len(self.snapshot().events), 1)
        calls = self.service.events.return_value.list.call_args_list
        self.assertEqual([call.kwargs["pageToken"] for call in calls], [None, "first", "second"])
        self.resolve_mock.assert_called_once()

    def test_conflicting_duplicate_invalidates_entire_snapshot(self):
        for changed in (event(summary="Mudou"), event(status="tentative"),
                        {"id": "instance-1", "status": "cancelled"}):
            with self.subTest(changed=changed):
                self.pages({"items": [event()], "nextPageToken": "p2"}, {"items": [changed]})
                with self.assertRaises(ReminderCalendarError):
                    self.snapshot()

    def test_conflicting_duplicates_outside_window_also_fail(self):
        before = event(start={"dateTime": "2026-10-31T22:00:00Z"},
                       end={"dateTime": "2026-10-31T23:00:00Z"})
        self.pages({"items": [before, dict(before, summary="Mudou")]})
        with self.assertRaises(ReminderCalendarError):
            self.snapshot()

    def test_malformed_page_or_later_item_never_returns_partial(self):
        for page in (None, [], {"items": {}}, {"items": None}, {"items": [False]}):
            with self.subTest(page=page):
                self.pages({"items": [event()], "nextPageToken": "p2"}, page)
                with self.assertRaises(ReminderCalendarError):
                    self.snapshot()

    def test_bad_or_repeated_next_page_token_fails(self):
        for token in (0, [], " ", " p2", "p\n2", "x" * 4097):
            with self.subTest(token=repr(token)[:40]):
                self.pages({"items": [event()], "nextPageToken": token})
                with self.assertRaises(ReminderCalendarError):
                    self.snapshot()
        self.pages({"items": [], "nextPageToken": "repeat"},
                   {"items": [event()], "nextPageToken": "repeat"})
        with self.assertRaises(ReminderCalendarError):
            self.snapshot()

    def test_twenty_pages_are_allowed_only_if_last_one_is_terminal(self):
        self.pages(*({"items": [], "nextPageToken": f"page-{index}"} for index in range(19)),
                   {"items": [event()]})
        self.assertEqual(len(self.snapshot().events), 1)
        self.pages(*({"items": [], "nextPageToken": f"page-{index}"} for index in range(20)))
        with self.assertRaises(ReminderCalendarError):
            self.snapshot()

    def test_item_limit_counts_ignored_and_duplicate_entries(self):
        tombstone = {"id": "gone", "status": "cancelled"}
        self.pages({"items": [tombstone] * 2000})
        self.assertEqual(self.snapshot().events, ())
        self.pages({"items": [tombstone] * 2001})
        with self.assertRaises(ReminderCalendarError):
            self.snapshot()

    def test_provider_errors_are_generic_and_do_not_leak_values(self):
        for error in (OSError("Entrevista fictícia reminders@example.invalid"),
                      HttpError(httplib2.Response({"status": "403"}), b'private payload')):
            with self.subTest(error=type(error).__name__):
                self.request.execute.side_effect = error
                with self.assertRaises(ReminderCalendarError) as raised:
                    self.snapshot()
                self.assertNotIn("Entrevista", str(raised.exception))
                self.assertNotIn(CALENDAR["id"], str(raised.exception))
                self.assertNotIn("private payload", str(raised.exception))

    def test_snapshot_and_events_do_not_expose_ids_or_title_in_repr(self):
        self.pages({"items": [event()]})
        snapshot = self.snapshot()
        for representation in (repr(snapshot), repr(snapshot.events[0])):
            self.assertNotIn(CALENDAR["id"], representation)
            self.assertNotIn("instance-1", representation)
            self.assertNotIn("Entrevista", representation)


if __name__ == "__main__":
    unittest.main()
