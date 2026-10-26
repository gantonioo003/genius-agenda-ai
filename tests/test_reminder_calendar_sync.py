"""GETs exatos simulados; sem OAuth, sockets ou dados de agenda real."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import traceback
import unittest
from unittest.mock import Mock, call, patch

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
import httplib2

from app.google_calendar import CalendarSetupError, SCOPES
from app.reminder_calendar import GoogleReminderCalendarGateway, ReminderCalendarError
from app.services.reminders import ReminderCalendarEvent
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot


UTC = timezone.utc
CALENDAR = {"id": "fixture-calendar@example.invalid", "timeZone": "America/Recife"}
CALENDAR_ACCESS = dict(CALENDAR, accessRole="owner", primary=False, deleted=False)
PRIVATE = "Título privado inteiramente fictício"


def event(event_id="fixture-event", **changes):
    result = {
        "id": event_id, "summary": PRIVATE, "status": "confirmed",
        "start": {"dateTime": "2030-06-17T18:00:00-03:00"},
        "end": {"dateTime": "2030-06-17T19:00:00-03:00"},
    }
    result.update(changes)
    return result


def http_error(status, *, content=None):
    if content is None:
        content = (json.dumps({"error": {"code": 410, "errors": [{"reason": "deleted"}]}}).encode("utf-8")
                   if status == 410 else (PRIVATE + " provider-secret fixture-event").encode("utf-8"))
    return HttpError(httplib2.Response({"status": str(status)}),
                     content)


class ReminderCalendarSyncTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.gateway = GoogleReminderCalendarGateway(self.service)
        self.resolve = patch("app.reminder_calendar.resolve_calendar", return_value=deepcopy(CALENDAR))
        self.resolve_mock = self.resolve.start()
        self.addCleanup(self.resolve.stop)
        self.event_request = self.service.events.return_value.get.return_value
        self.access_request = self.service.calendarList.return_value.get.return_value
        self.access_request.execute.return_value = deepcopy(CALENDAR_ACCESS)

    def responses(self, *responses):
        self.event_request.execute.side_effect = list(responses)

    def lookup(self, ids=("fixture-event",)):
        return self.gateway.lookup_snapshot(ids, expected_calendar_id=CALENDAR["id"])

    def invalid_event(self, item):
        self.responses(item)
        with self.assertRaises(ReminderCalendarError):
            self.lookup()

    def assert_readonly(self):
        events = self.service.events.return_value
        for method in ("insert", "patch", "update", "delete", "move", "import_"):
            getattr(events, method).assert_not_called()
        events.list.assert_not_called()
        self.service.calendars.assert_not_called()

    def test_exact_get_uses_bound_calendar_zone_and_minimal_fields(self):
        self.responses(event())
        result = self.lookup()
        self.assertIs(type(result), ReminderLookupSnapshot)
        self.assertEqual((result.calendar_id, result.calendar_timezone),
                         (CALENDAR["id"], CALENDAR["timeZone"]))
        lookup, = result.lookups
        self.assertIs(type(lookup), ReminderEventLookup)
        self.assertEqual((lookup.event_id, lookup.outcome), ("fixture-event", "found"))
        self.assertIs(type(lookup.event), ReminderCalendarEvent)
        self.assertEqual(lookup.event.start_utc, datetime(2030, 6, 17, 21, tzinfo=UTC))
        self.assertEqual(lookup.event.end_utc, datetime(2030, 6, 17, 22, tzinfo=UTC))
        self.assertEqual(self.service.events.return_value.get.call_args.kwargs, {
            "calendarId": CALENDAR["id"], "eventId": "fixture-event",
            "timeZone": CALENDAR["timeZone"],
            "fields": "id,summary,status,start,end,recurrence,recurringEventId,originalStartTime",
        })
        self.event_request.execute.assert_called_once_with(num_retries=2)
        self.resolve_mock.assert_called_once_with(self.service)
        self.service.calendarList.return_value.get.assert_not_called()
        self.assert_readonly()

    def test_exact_ids_are_opaque_case_sensitive_and_preserve_input_order(self):
        ids = ("Series_20300617T210000Z", "series_20300617T210000Z", "Other-Event")
        self.responses(*(event(event_id) for event_id in ids))
        result = self.lookup(ids)
        self.assertEqual(tuple(item.event_id for item in result.lookups), ids)
        self.assertEqual(tuple(item.kwargs["eventId"]
                               for item in self.service.events.return_value.get.call_args_list), ids)
        self.resolve_mock.assert_called_once()

    def test_empty_tuple_still_confirms_actual_binding_without_event_query(self):
        result = self.lookup(())
        self.assertEqual(result.lookups, ())
        self.assertEqual(result.calendar_timezone, CALENDAR["timeZone"])
        self.resolve_mock.assert_called_once()
        self.service.events.assert_not_called()
        self.service.calendarList.return_value.get.assert_not_called()

    def test_200_ids_are_allowed(self):
        ids = tuple(f"event-{index}" for index in range(200))
        self.responses(*(event(event_id) for event_id in ids))
        self.assertEqual(len(self.lookup(ids).lookups), 200)
        self.assertEqual(self.service.events.return_value.get.call_count, 200)
        self.resolve_mock.assert_called_once()

    def test_invalid_tuple_or_limits_fail_before_any_google_access(self):
        for ids in (None, [], ["fixture-event"], "fixture-event", {"fixture-event"},
                    ("fixture-event", "fixture-event"), tuple(f"event-{index}" for index in range(201))):
            with self.subTest(ids=type(ids).__name__):
                with self.assertRaises(ReminderCalendarError):
                    self.lookup(ids)
        self.resolve_mock.assert_not_called()
        self.service.events.assert_not_called()
        self.service.calendarList.assert_not_called()

    def test_entire_tuple_is_validated_before_binding_or_get(self):
        for bad_id in (None, "", " ", "a b", "bad\n", "x\x00", "x\ud800", b"bytes", 7, "x" * 1025):
            with self.subTest(event_id=ascii(bad_id)[:60]):
                with self.assertRaises(ReminderCalendarError):
                    self.lookup(("valid", bad_id))
        self.resolve_mock.assert_not_called()
        self.service.events.assert_not_called()

    def test_expected_calendar_is_required_and_validated_before_google(self):
        for calendar_id in (None, "", "primary", "a b", "x\x00", "x\ud800", "x" * 1025):
            with self.subTest(calendar_id=ascii(calendar_id)[:60]):
                with self.assertRaises(ReminderCalendarError):
                    self.gateway.lookup_snapshot(("fixture-event",), calendar_id)
        self.resolve_mock.assert_not_called()
        self.service.events.assert_not_called()

    def test_wrong_actual_binding_prevents_event_queries(self):
        self.resolve_mock.return_value = dict(CALENDAR, id="other-calendar@example.invalid")
        with self.assertRaises(ReminderCalendarError):
            self.lookup()
        self.service.events.assert_not_called()
        self.service.calendarList.return_value.get.assert_not_called()

    def test_invalid_binding_or_missing_timezone_fails_before_event_query(self):
        for calendar in (None, [], {}, {"id": CALENDAR["id"]}, {"timeZone": CALENDAR["timeZone"]},
                         dict(CALENDAR, id="primary"), dict(CALENDAR, id="bad\ud800"),
                         dict(CALENDAR, timeZone=""), dict(CALENDAR, timeZone="Mars/Olympus"),
                         dict(CALENDAR, timeZone=" America/Recife")):
            with self.subTest(calendar=ascii(calendar)):
                self.resolve_mock.return_value = calendar
                with self.assertRaises(ReminderCalendarError):
                    self.lookup()
        self.service.events.assert_not_called()
        self.service.calendarList.return_value.get.assert_not_called()

    def test_binding_404_or_410_never_proves_event_absence(self):
        for error in (http_error(404), http_error(410), CalendarSetupError(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.resolve_mock.side_effect = error
                with self.assertRaises(ReminderCalendarError):
                    self.lookup()
        self.service.events.assert_not_called()
        self.service.calendarList.return_value.get.assert_not_called()

    def test_minimal_cancelled_tombstone_needs_no_dates_or_recurring_fields(self):
        self.responses({"id": "fixture-event", "status": "cancelled"})
        result, = self.lookup().lookups
        self.assertEqual((result.event_id, result.outcome, result.event), ("fixture-event", "cancelled", None))
        self.service.calendarList.return_value.get.assert_not_called()
        self.assert_readonly()

    def test_cancelled_tombstone_does_not_depend_on_present_dates(self):
        self.responses(event(status="cancelled", start=None, end="unusable", summary=False))
        result, = self.lookup().lookups
        self.assertEqual(result.outcome, "cancelled")
        self.assertIsNone(result.event)

    def test_404_and_410_need_exact_calendar_access_recheck(self):
        for status in (404, 410):
            with self.subTest(status=status):
                self.responses(http_error(status))
                result, = self.lookup().lookups
                self.assertEqual((result.event_id, result.outcome, result.event),
                                 ("fixture-event", "absent", None))
                self.assertEqual(self.service.calendarList.return_value.get.call_args.kwargs, {
                    "calendarId": CALENDAR["id"], "fields": "id,timeZone,accessRole,primary,deleted",
                })
                self.access_request.execute.assert_called_with(num_retries=2)
        self.assert_readonly()

    def test_missing_checks_follow_all_gets_and_only_recheck_calendar_once(self):
        ids = ("first-missing", "present", "second-missing", "cancelled")
        self.responses(http_error(404), event("present"), http_error(410),
                       {"id": "cancelled", "status": "cancelled"})
        snapshot = self.lookup(ids)
        self.assertEqual(tuple(item.outcome for item in snapshot.lookups),
                         ("absent", "found", "absent", "cancelled"))
        self.assertEqual(tuple(item.event_id for item in snapshot.lookups), ids)
        self.service.calendarList.return_value.get.assert_called_once()
        self.resolve_mock.assert_called_once()
        calls = self.service.mock_calls
        last_event = max(index for index, invocation in enumerate(calls)
                         if invocation == call.events().get().execute(num_retries=2))
        calendar_check = calls.index(call.calendarList().get(
            calendarId=CALENDAR["id"], fields="id,timeZone,accessRole,primary,deleted"))
        self.assertGreater(calendar_check, last_event)

    def test_get_request_construction_404_is_not_treated_as_resource_absence(self):
        self.service.events.return_value.get.side_effect = http_error(404)
        with self.assertRaises(ReminderCalendarError):
            self.lookup()
        self.service.calendarList.return_value.get.assert_not_called()

    def test_410_requires_only_explicit_deleted_reasons(self):
        for content in (
            b"private malformed payload", b"null", b"[]", b"{}", b'{"error": null}',
            b'{"error": {"errors": []}}', b'{"error": {"errors": {"reason": "deleted"}}}',
            b'{"error": {"errors": [null]}}', b'{"error": {"errors": [{}]}}',
            b'{"error": {"errors": [{"reason": "fullSyncRequired"}]}}',
            b'{"error": {"errors": [{"reason": "updatedMinTooLongAgo"}]}}',
            b'{"error": {"errors": [{"reason": "deleted"}, {"reason": "fullSyncRequired"}]}}',
            b'{"error": {"errors": [{"reason": null}]}}',
        ):
            with self.subTest(content=content):
                self.responses(event("valid"), http_error(410, content=content))
                with self.assertRaises(ReminderCalendarError):
                    self.lookup(("valid", "missing"))
        self.service.calendarList.return_value.get.assert_not_called()

    def test_calendar_access_404_410_or_failure_aborts_whole_snapshot(self):
        for error in (http_error(403), http_error(404), http_error(410), http_error(429),
                      http_error(503), TimeoutError(PRIVATE), TransportError(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.responses(event("valid"), http_error(404))
                self.access_request.execute.side_effect = error
                with self.assertRaises(ReminderCalendarError):
                    self.lookup(("valid", "missing"))

    def test_calendar_access_response_must_match_id_owner_and_original_timezone(self):
        for calendar in (None, [], {}, dict(CALENDAR_ACCESS, id="other@example.invalid"),
                         dict(CALENDAR_ACCESS, id="primary"), dict(CALENDAR_ACCESS, id="bad\ud800"),
                         dict(CALENDAR_ACCESS, accessRole="reader"),
                         dict(CALENDAR_ACCESS, accessRole="writer"),
                         dict(CALENDAR_ACCESS, accessRole=None),
                         dict(CALENDAR_ACCESS, primary=True), dict(CALENDAR_ACCESS, deleted=True),
                         dict(CALENDAR_ACCESS, primary=0), dict(CALENDAR_ACCESS, deleted=None),
                         dict(CALENDAR_ACCESS, timeZone="America/New_York"),
                         dict(CALENDAR_ACCESS, timeZone="Mars/Olympus"),
                         dict(CALENDAR_ACCESS, timeZone=" America/Recife"),
                         {key: value for key, value in CALENDAR_ACCESS.items() if key != "timeZone"}):
            with self.subTest(calendar=ascii(calendar)):
                self.responses(http_error(404))
                self.access_request.execute.return_value = calendar
                with self.assertRaises(ReminderCalendarError):
                    self.lookup()

    def test_optional_false_primary_deleted_flags_can_be_omitted(self):
        self.responses(http_error(404))
        self.access_request.execute.return_value = dict(CALENDAR, accessRole="owner")
        self.assertEqual(self.lookup().lookups[0].outcome, "absent")

    def test_event_403_429_5xx_or_network_errors_never_prove_absence(self):
        for error in (http_error(400), http_error(403), http_error(429), http_error(500),
                      http_error(503), TimeoutError(PRIVATE), OSError(PRIVATE),
                      TransportError(PRIVATE), httplib2.HttpLib2Error(PRIVATE), RuntimeError(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.responses(error)
                with self.assertRaises(ReminderCalendarError):
                    self.lookup()
        self.service.calendarList.return_value.get.assert_not_called()

    def test_later_event_failure_never_returns_valid_prefix_or_verifies_missing(self):
        for error in (http_error(403), http_error(429), http_error(503), TimeoutError(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.responses(event("valid"), http_error(404), error)
                with self.assertRaises(ReminderCalendarError):
                    self.lookup(("valid", "missing", "failure"))
        self.service.calendarList.return_value.get.assert_not_called()
        self.assert_readonly()

    def test_malformed_or_mismatching_event_response_fails_whole_snapshot(self):
        for bad in (None, [], {}, event("other-event"),
                    {"id": "other-event", "status": "cancelled"},
                    {"id": "bad\ud800", "status": "cancelled"},
                    {"status": "cancelled"}):
            with self.subTest(response=ascii(bad)):
                self.responses(event("valid"), bad)
                with self.assertRaises(ReminderCalendarError):
                    self.lookup(("valid", "fixture-event"))

    def test_unknown_or_missing_status_never_means_absent(self):
        for status in (None, "", "unknown", 0, []):
            with self.subTest(status=status):
                self.invalid_event(event(status=status))
        missing = event()
        del missing["status"]
        self.invalid_event(missing)
        self.service.calendarList.return_value.get.assert_not_called()

    def test_any_series_recurrence_field_is_rejected_including_cancelled(self):
        for status in ("confirmed", "tentative", "cancelled"):
            for recurrence in (["RRULE:FREQ=DAILY"], [], None, "RRULE:FREQ=DAILY"):
                with self.subTest(status=status, recurrence=recurrence):
                    self.invalid_event(event(status=status, recurrence=recurrence))

    def test_moved_recurring_occurrence_keeps_own_id_and_current_instant(self):
        self.responses(event("series_20300616T210000Z", recurringEventId="series",
            originalStartTime={"dateTime": "2030-06-16T18:00:00-03:00"}))
        result, = self.lookup(("series_20300616T210000Z",)).lookups
        self.assertEqual(result.outcome, "found")
        self.assertEqual(result.event.event_id, "series_20300616T210000Z")
        self.assertEqual(result.event.recurring_event_id, "series")
        self.assertTrue(result.event.expanded)
        self.assertEqual(result.event.start_utc, datetime(2030, 6, 17, 21, tzinfo=UTC))

    def test_incomplete_or_incoherent_expanded_occurrence_fails(self):
        for changes in (
            {"recurringEventId": "series"},
            {"originalStartTime": event()["start"]},
            {"recurringEventId": "fixture-event", "originalStartTime": event()["start"]},
            {"recurringEventId": "series", "originalStartTime": {"date": "2030-06-17"}},
            {"recurringEventId": "series", "originalStartTime": {"dateTime": "2030-06-17T18:00:00"}},
        ):
            with self.subTest(changes=changes):
                self.invalid_event(event(**changes))

    def test_tentative_and_all_day_are_found_for_service_to_classify(self):
        self.responses(event("tentative", status="tentative"),
                       event("all-day", start={"date": "2030-06-17"}, end={"date": "2030-06-18"}))
        tentative, all_day = self.lookup(("tentative", "all-day")).lookups
        self.assertEqual((tentative.outcome, tentative.event.status), ("found", "tentative"))
        self.assertEqual((all_day.outcome, all_day.event.all_day), ("found", True))
        self.assertEqual(all_day.event.start_utc, datetime(2030, 6, 17, 3, tzinfo=UTC))

    def test_event_instants_and_zones_reuse_strict_snapshot_rules(self):
        for changes in (
            {"start": None}, {"start": {}},
            {"start": {"date": "2030-06-17", "dateTime": "2030-06-17T18:00:00-03:00"}},
            {"start": {"dateTime": "2030-06-17T18:00:00"}},
            {"start": {"dateTime": "2030-06-17 18:00:00-03:00"}},
            {"start": {"dateTime": "2030-06-17T18:00:00-03:00", "timeZone": "Mars/Olympus"}},
            {"start": {"date": "2030-06-17", "timeZone": "America/New_York"}, "end": {"date": "2030-06-18"}},
            {"end": event()["start"]},
            {"end": {"dateTime": "2030-06-17T17:00:00-03:00"}},
            {"summary": None}, {"summary": "bad\ud800"},
        ):
            with self.subTest(changes=ascii(changes)):
                self.invalid_event(event(**changes))

    def test_explicit_offsets_remain_authoritative_when_event_zone_differs(self):
        self.responses(event("series_20300616T210000Z", recurringEventId="series",
            start={"dateTime": "2030-06-17T18:00:00-03:00", "timeZone": "America/New_York"},
            end={"dateTime": "2030-06-17T19:00:00-03:00", "timeZone": "America/New_York"},
            originalStartTime={"dateTime": "2030-06-16T20:00:00Z", "timeZone": "America/New_York"}))
        result, = self.lookup(("series_20300616T210000Z",)).lookups
        self.assertEqual(result.event.start_utc, datetime(2030, 6, 17, 21, tzinfo=UTC))
        self.assertEqual(result.event.end_utc - result.event.start_utc, timedelta(hours=1))

    def test_timezone_change_between_runs_is_returned_as_actual_binding(self):
        self.responses(event())
        self.resolve_mock.return_value = dict(CALENDAR, timeZone="America/New_York")
        snapshot = self.lookup()
        self.assertEqual(snapshot.calendar_timezone, "America/New_York")
        self.assertEqual(self.service.events.return_value.get.call_args.kwargs["timeZone"], "America/New_York")
        self.assertEqual(snapshot.lookups[0].event.start_utc, datetime(2030, 6, 17, 21, tzinfo=UTC))

    def test_dtos_and_generic_errors_do_not_expose_provider_or_event_values(self):
        self.responses(event())
        snapshot = self.lookup()
        for representation in (repr(snapshot), repr(snapshot.lookups[0]), repr(snapshot.lookups[0].event)):
            for private in (CALENDAR["id"], "fixture-event", PRIVATE):
                self.assertNotIn(private, representation)
        for error in (http_error(403), OSError(PRIVATE + CALENDAR["id"]), RuntimeError("provider-secret")):
            self.responses(error)
            try:
                self.lookup()
            except ReminderCalendarError as exc:
                rendered = "".join(traceback.format_exception(exc))
                for private in (CALENDAR["id"], "fixture-event", PRIVATE, "provider-secret"):
                    self.assertNotIn(private, str(exc))
                    self.assertNotIn(private, repr(exc))
                    self.assertNotIn(private, rendered)
            else:
                self.fail("Provider error did not abort the snapshot")

    def test_existing_calendarlist_readonly_scope_covers_access_recheck(self):
        self.assertIn("https://www.googleapis.com/auth/calendar.calendarlist.readonly", SCOPES)
        self.assertNotIn("https://www.googleapis.com/auth/calendar.calendars.readonly", SCOPES)


if __name__ == "__main__":
    unittest.main()
