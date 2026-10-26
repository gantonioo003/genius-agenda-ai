"""SDK e transporte reais sobre HTTP em memória; sem OAuth ou sockets."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
import requests

from app.availability_calendar import GoogleAvailabilityCalendarGateway, default_availability_gateway
from app.google_calendar import CalendarSetupError
from app.google_transport import SingleAttemptHttp
from app.services.agenda import CalendarPeriod, month_period, week_period
from app.services.availability import (
    AvailabilityBinding, AvailabilityEvent, AvailabilitySnapshot,
    AvailabilityCollectionError, AvailabilityLimitError, AvailabilitySetupError,
)
from calendar_http_fake import CalendarHttpFake


UTC = timezone.utc
START = datetime(2030, 6, 17, tzinfo=UTC)
END = START + timedelta(days=7)
PRIVATE = "private provider error"


def event(identity="occupancy-event", **changes):
    value = {"id": identity, "status": "confirmed",
        "start": {"dateTime": "2030-06-17T10:00:00-03:00"},
        "end": {"dateTime": "2030-06-17T11:00:00-03:00"}}
    value.update(changes)
    return value


class AvailabilityHttpFake(CalendarHttpFake):
    def __init__(self):
        super().__init__()
        self.pages = [{"items": []}]
        self.page_number = 0
        self.http_requests = 0
        self.failure_after = None
        self.failure_status = 503
        self.transport_failure = False

    def _list_events(self, query):
        if self.page_number >= len(self.pages):
            raise AssertionError("A consulta tentou ler além das páginas da fixture.")
        page = deepcopy(self.pages[self.page_number])
        self.page_number += 1
        return page

    def request(self, method, url, data=None, headers=None, **kwargs):
        if method.upper() != "GET":
            raise AssertionError("A fixture de ocupação permite somente GET.")
        self.http_requests += 1
        if self.transport_failure:
            raise requests.ConnectionError(PRIVATE)
        if self.failure_after is not None and self.http_requests > self.failure_after:
            parsed = urlsplit(url)
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            call = {"method": "GET", "path": unquote(parsed.path), "query": query,
                    "headers": dict(headers or {}), "body": None, "status": None, "event_id": None}
            self.calls.append(call)
            return self._reply(call, self.failure_status, self._error(self.failure_status))
        return super().request(method, url, data=data, headers=headers, **kwargs)


class AvailabilityCalendarTests(unittest.TestCase):
    def setUp(self):
        self.backend = AvailabilityHttpFake()
        sessions = patch("app.google_transport.requests.Session", side_effect=self.backend.session)
        sessions.start(); self.addCleanup(sessions.stop)
        resolver = patch("app.availability_calendar.resolve_calendar", side_effect=lambda _service: deepcopy(self.backend.calendar))
        self.resolve = resolver.start(); self.addCleanup(resolver.stop)
        oauth = patch("app.google_calendar.get_credentials", side_effect=AssertionError("OAuth real proibido."))
        oauth.start(); self.addCleanup(oauth.stop)
        sleeping = patch("googleapiclient.http.time.sleep", return_value=None)
        sleeping.start(); self.addCleanup(sleeping.stop)
        transport = SingleAttemptHttp(timeout=20)
        http = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
        self.service = build("calendar", "v3", http=http, cache_discovery=False, static_discovery=True)
        self.addCleanup(self.service.close)
        self.gateway = GoogleAvailabilityCalendarGateway(self.service)
        self.binding = AvailabilityBinding(self.backend.calendar["id"], self.backend.calendar["timeZone"])
        self.period = CalendarPeriod(START, END, self.binding.time_zone)

    def pages(self, *pages):
        self.backend.pages = list(pages)
        self.backend.page_number = 0
        self.backend.http_requests = 0
        self.backend.calls.clear()

    def snapshot(self, period=None, binding=None):
        return self.gateway.list_occupancy(self.period if period is None else period,
            expected_binding=self.binding if binding is None else binding)

    def error(self, callback, error_type=AvailabilityCollectionError):
        with self.assertRaises(error_type) as caught:
            callback()
        self.assertIs(type(caught.exception), error_type)
        for private in (PRIVATE, self.binding.calendar_id, "secret-title", "secret-description"):
            self.assertNotIn(private, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def invalid(self, item):
        self.pages({"items": [item]})
        self.error(self.snapshot)

    def with_zone(self, name, reference, *, month=False):
        self.backend.calendar["timeZone"] = name
        binding = AvailabilityBinding(self.binding.calendar_id, name)
        period = (month_period if month else week_period)(reference, name)
        return binding, period

    def test_binding_is_private_current_and_does_not_query_events(self):
        binding = self.gateway.binding()
        self.assertEqual(binding, self.binding)
        self.assertNotIn(binding.calendar_id, repr(binding))
        self.assertEqual(self.backend.http_requests, 0)
        self.resolve.assert_called_once_with(self.service)

    def test_binding_requires_explicit_owner_valid_id_zone_and_nonprimary_nondeleted(self):
        original = deepcopy(self.backend.calendar)
        for changes in ({"id": "primary"}, {"id": ""}, {"id": "bad\n"}, {"id": "bad\ud800"},
            {"id": "x" * 1025}, {"timeZone": "Mars/Olympus"}, {"timeZone": " UTC"},
            {"timeZone": ""}, {"primary": True}, {"primary": 0}, {"deleted": True}, {"deleted": 0},
            {"accessRole": "reader"}, {"accessRole": "writer"}, {"accessRole": None}):
            with self.subTest(changes=ascii(changes)):
                self.backend.calendar = {**original, **changes}
                self.error(self.gateway.binding)
        for missing in ("id", "timeZone", "accessRole"):
            self.backend.calendar = {key: value for key, value in original.items() if key != missing}
            self.error(self.gateway.binding)
        self.assertEqual(self.backend.http_requests, 0)

    def test_discovery_setup_failure_preserved_as_sanitized_domain_setup(self):
        self.resolve.side_effect = CalendarSetupError(PRIVATE)
        self.error(self.gateway.binding, AvailabilitySetupError)
        self.error(self.snapshot, AvailabilitySetupError)
        self.assertEqual(self.backend.http_requests, 0)

    def test_discovery_unexpected_failures_and_malformed_response_are_sanitized(self):
        self.resolve.side_effect = OSError(PRIVATE)
        self.error(self.gateway.binding)
        self.resolve.side_effect = None
        for malformed in (None, [], "secret-description", {}):
            self.resolve.return_value = malformed
            self.error(self.gateway.binding)

    def test_binding_drift_in_id_zone_or_permission_prevents_event_query(self):
        self.gateway.binding()
        original = deepcopy(self.backend.calendar)
        for changes in ({"id": "other@example.invalid"}, {"timeZone": "UTC"}, {"accessRole": "reader"}):
            self.backend.calendar = {**original, **changes}
            self.error(self.snapshot)
        self.assertEqual(self.backend.http_requests, 0)

    def test_invalid_period_binding_and_imaginary_query_time_fail_before_discovery(self):
        for period in (None, {}, CalendarPeriod(START, START, self.binding.time_zone),
            CalendarPeriod(END, START, self.binding.time_zone), CalendarPeriod(START.replace(tzinfo=None), END, self.binding.time_zone),
            CalendarPeriod("bad", END, self.binding.time_zone), CalendarPeriod(START, END, "UTC")):
            self.error(lambda: self.snapshot(period=period) if period is not None else
                       self.gateway.list_occupancy(None, expected_binding=self.binding))
        for binding in (None, {}, AvailabilityBinding("primary", self.binding.time_zone),
            AvailabilityBinding("bad\ud800", self.binding.time_zone), AvailabilityBinding(self.binding.calendar_id, " UTC")):
            self.error(lambda: self.gateway.list_occupancy(self.period, expected_binding=binding))
        zone = ZoneInfo("America/New_York")
        imaginary = CalendarPeriod(datetime(2030, 3, 10, 2, 30, tzinfo=zone), datetime(2030, 3, 10, 4, tzinfo=zone), zone.key)
        self.error(lambda: self.snapshot(period=imaginary, binding=AvailabilityBinding(self.binding.calendar_id, zone.key)))
        self.resolve.assert_not_called()
        self.assertEqual(self.backend.http_requests, 0)

    def test_sdk_query_uses_only_authorized_calendar_minimal_fields_and_hidden_invitations(self):
        self.pages({"items": [event()]})
        result = self.snapshot()
        query = self.backend.calls[0]["query"]
        self.assertEqual((query["timeMin"], query["timeMax"]), (START.isoformat(), END.isoformat()))
        self.assertEqual(query["timeZone"], self.binding.time_zone)
        for key, value in (("singleEvents", "true"), ("showDeleted", "false"), ("showHiddenInvitations", "true"),
                           ("orderBy", "startTime"), ("maxResults", "250")):
            self.assertEqual(query[key], value)
        self.assertIn(self.binding.calendar_id, self.backend.calls[0]["path"])
        self.assertEqual(query["fields"], self.gateway.EVENT_FIELDS)
        for forbidden in ("summary", "description", "location", "htmlLink", "attendees", "etag"):
            self.assertNotIn(forbidden, query["fields"])
        self.assertIn("transparency", query["fields"])
        self.assertNotIn("pageToken", query)
        self.assertIs(result.period, self.period)
        self.assertTrue(all(call["method"] == "GET" for call in self.backend.calls))

    def test_terminal_empty_pages_prove_complete_empty_source(self):
        for page in ({}, {"items": []}, {"nextPageToken": ""}):
            self.pages(page)
            result = self.snapshot()
            self.assertIsInstance(result, AvailabilitySnapshot)
            self.assertEqual((result.events, result.pages_read, result.raw_events_seen), ((), 1, 0))
            self.assertIs(result.complete, True)

    def test_empty_forward_page_and_identical_cross_page_duplicate_fully_read(self):
        value = event()
        self.pages({"nextPageToken": "second"}, {"items": [value], "nextPageToken": "third"}, {"items": [value]})
        result = self.snapshot()
        self.assertEqual((len(result.events), result.raw_events_seen, result.pages_read), (1, 2, 3))
        self.assertEqual([call["query"].get("pageToken") for call in self.backend.calls], [None, "second", "third"])

    def test_transparency_omission_is_opaque_and_transparent_is_preserved_not_ignored(self):
        self.pages({"items": [event("default"), event("opaque", transparency="opaque"),
                              event("transparent", transparency="transparent", status="tentative")]})
        result = self.snapshot()
        self.assertEqual({entry.event_id: entry.transparency for entry in result.events},
                         {"default": "opaque", "opaque": "opaque", "transparent": "transparent"})
        self.assertEqual(next(entry.status for entry in result.events if entry.event_id == "transparent"), "tentative")
        self.assertEqual({field.name for field in fields(AvailabilityEvent)},
            {"event_id", "start_utc", "end_utc", "status", "transparency", "all_day"})
        self.assertNotIn("occupancy-event", repr(result))
        self.assertTrue(all(entry.start_utc.tzinfo is UTC and entry.end_utc.tzinfo is UTC for entry in result.events))

    def test_explicit_invalid_transparency_and_status_fail_closed(self):
        for value in (None, "", "OPAQUE", "unknown", True, 1, [], {}):
            self.invalid(event(transparency=value))
        for value in (None, "", "unknown", True, 1, [], {}):
            self.invalid(event(status=value))

    def test_transparent_and_outside_events_are_still_temporally_validated(self):
        self.invalid(event(transparency="transparent", start={"dateTime": "2030-06-17T10:00:00+01:60"}))
        self.invalid(event(transparency="transparent", end={"dateTime": "2030-06-17T09:00:00-03:00"}))
        self.invalid(event(start={"dateTime": "2030-07-01T10:00:00Z"}, end={"dateTime": "bad"}))

    def test_tombstones_count_and_skip_only_after_id_validation(self):
        self.pages({"items": [{"id": "gone", "status": "cancelled"}, event()]})
        result = self.snapshot()
        self.assertEqual((len(result.events), result.raw_events_seen), (1, 2))
        for identity in (None, "", "bad\x00", "bad\u202e", "bad\ud800", "bad space", "x" * 1025):
            self.invalid({"id": identity, "status": "cancelled"})

    def test_raw_duplicate_divergence_in_status_transparency_or_unselected_data_aborts(self):
        for changes in ({"status": "tentative"}, {"status": "cancelled"}, {"transparency": "transparent"},
                        {"transparency": "opaque"}, {"summary": "secret-title"},
                        {"start": {"dateTime": "2030-06-17T10:00:00-03:00", "timeZone": self.binding.time_zone}}):
            self.pages({"items": [event()], "nextPageToken": "later"}, {"items": [event(**changes)]})
            self.error(self.snapshot)

    def test_conflicting_outside_transparent_or_cancelled_copies_also_abort(self):
        for original in (event(transparency="transparent"), {"id": "occupancy-event", "status": "cancelled"},
            event(start={"dateTime": "2030-07-01T10:00:00Z"}, end={"dateTime": "2030-07-01T11:00:00Z"})):
            self.pages({"items": [original], "nextPageToken": "later"}, {"items": [{**original, "extra": "secret-description"}]})
            self.error(self.snapshot)

    def test_case_sensitive_opaque_ids_and_utc_order_are_preserved(self):
        self.pages({"items": [event("opaque_ID@A"), event("Opaque_ID@A"), event("earlier",
            start={"dateTime": "2030-06-17T09:00:00-03:00"}, end={"dateTime": "2030-06-17T09:30:00-03:00"})]})
        self.assertEqual(tuple(entry.event_id for entry in self.snapshot().events), ("earlier", "Opaque_ID@A", "opaque_ID@A"))

    def test_rrule_series_is_rejected_even_cancelled_or_transparent(self):
        for recurrence in (None, [], "RRULE:FREQ=DAILY", ["RRULE:FREQ=DAILY"]):
            for changes in ({}, {"status": "cancelled"}, {"transparency": "transparent"}):
                self.invalid(event(recurrence=recurrence, **changes))

    def test_expanded_moved_occurrences_keep_own_identity_and_current_interval(self):
        self.pages({"items": [event("series_current", recurringEventId="series",
            originalStartTime={"dateTime": "2030-06-16T10:00:00-03:00"})]})
        entry = self.snapshot().events[0]
        self.assertEqual(entry.event_id, "series_current")
        self.assertEqual(entry.start_utc, datetime(2030, 6, 17, 13, tzinfo=UTC))

    def test_occurrence_metadata_is_paired_valid_and_same_all_day_kind(self):
        for changes in ({"recurringEventId": "series"}, {"originalStartTime": event()["start"]},
            {"recurringEventId": "occupancy-event", "originalStartTime": event()["start"]},
            {"recurringEventId": "bad\n", "originalStartTime": event()["start"]},
            {"recurringEventId": "series", "originalStartTime": {"date": "2030-06-17"}},
            {"recurringEventId": "series", "originalStartTime": {"dateTime": "2030-06-17T10:00:00+01:60"}}):
            self.invalid(event(**changes))

    def test_temporal_shape_offset_components_and_positive_interval_are_strict(self):
        for value in ({}, None, {"date": "20300617"}, {"date": "2030-06-17", "dateTime": "2030-06-17T10:00:00Z"},
            {"dateTime": "2030-06-17T10:00:00"}, {"dateTime": "2030-06-17 10:00:00Z"},
            {"dateTime": "2030-06-17T25:00:00Z"}, {"dateTime": "2030-06-17T10:00:00+01:60"},
            {"dateTime": "2030-06-17T10:00:00-00:99"}, {"dateTime": "2030-06-17T10:00:00+24:00"},
            {"dateTime": "2030-06-17T10:00:00.1234567Z"},
            {"dateTime": "2030-06-17T10:00:00Z", "timeZone": "Mars/Olympus"}, {"date": "2030-06-17"}):
            self.invalid(event(start=value))
        for value in ({"dateTime": "2030-06-17T10:00:00-03:00"}, {"dateTime": "2030-06-17T09:00:00-03:00"},
                      {"dateTime": "2030-06-17T11:00:00+00:99"}):
            self.invalid(event(end=value))

    def test_original_iana_is_validated_but_explicit_response_offset_is_authoritative(self):
        self.pages({"items": [event(start={"dateTime": "2030-06-17T14:00:00+01:00", "timeZone": "America/New_York"},
            end={"dateTime": "2030-06-17T15:00:00+01:00", "timeZone": "Europe/London"})]})
        entry = self.snapshot().events[0]
        self.assertEqual((entry.start_utc.hour, entry.end_utc.hour), (13, 14))

    def test_dst_fold_explicit_offsets_are_distinct_instants(self):
        binding, period = self.with_zone("America/New_York", date(2030, 11, 3))
        self.pages({"items": [event("late", start={"dateTime": "2030-11-03T01:30:00-05:00"},
            end={"dateTime": "2030-11-03T02:00:00-05:00"}), event("early",
            start={"dateTime": "2030-11-03T01:30:00-04:00"}, end={"dateTime": "2030-11-03T01:45:00-04:00"})]})
        entries = self.snapshot(period, binding).events
        self.assertEqual(tuple(entry.event_id for entry in entries), ("early", "late"))
        self.assertEqual([entry.start_utc.hour for entry in entries], [5, 6])

    def test_all_day_uses_calendar_zone_exclusive_end_and_exact_overlap(self):
        period = week_period(date(2030, 6, 17), self.binding.time_zone)
        self.pages({"items": [event("prior", start={"date": "2030-06-16"}, end={"date": "2030-06-17"}),
            event("span", start={"date": "2030-06-16"}, end={"date": "2030-06-18"}),
            event("next", start={"date": "2030-06-24"}, end={"date": "2030-06-25"})]})
        entries = self.snapshot(period).events
        self.assertEqual(tuple(entry.event_id for entry in entries), ("span",))
        self.assertTrue(entries[0].all_day)
        self.assertEqual(entries[0].end_utc, datetime(2030, 6, 18, 3, tzinfo=UTC))
        self.invalid(event(start={"date": "2030-06-17", "timeZone": "UTC"}, end={"date": "2030-06-18"}))

    def test_all_day_midnight_gap_normalizes_within_date_to_first_valid_instant(self):
        binding, period = self.with_zone("America/Sao_Paulo", date(2018, 11, 4), month=True)
        self.pages({"items": [event(start={"date": "2018-11-04"}, end={"date": "2018-11-05"})]})
        entry = self.snapshot(period, binding).events[0]
        self.assertEqual(entry.start_utc, datetime(2018, 11, 4, 3, tzinfo=UTC))
        self.assertEqual(entry.start_utc.astimezone(ZoneInfo(binding.time_zone)).hour, 1)
        self.assertEqual(entry.end_utc - entry.start_utc, timedelta(hours=23))

    def test_all_day_ambiguous_midnight_uses_earliest_valid_boundary(self):
        binding, period = self.with_zone("America/Havana", date(2020, 11, 1))
        self.pages({"items": [event(start={"date": "2020-11-01"}, end={"date": "2020-11-02"})]})
        entry = self.snapshot(period, binding).events[0]
        self.assertEqual(entry.start_utc, datetime(2020, 11, 1, 4, tzinfo=UTC))
        self.assertEqual(entry.end_utc - entry.start_utc, timedelta(hours=25))

    def test_wholly_removed_civil_date_and_nonpositive_all_day_interval_rejected(self):
        binding, period = self.with_zone("Pacific/Apia", date(2011, 12, 1), month=True)
        for start, end in (("2011-12-30", "2011-12-31"), ("2011-12-29", "2011-12-30"), ("2011-12-31", "2011-12-31")):
            self.pages({"items": [event(start={"date": start}, end={"date": end})]})
            self.error(lambda: self.snapshot(period, binding))

    def test_fractional_query_expands_outward_and_filters_exact_half_open_interval(self):
        period = CalendarPeriod(START + timedelta(microseconds=500000), END + timedelta(microseconds=500000), self.binding.time_zone)
        self.pages({"items": [event("before", start={"dateTime": (START - timedelta(hours=1)).isoformat()},
            end={"dateTime": (START + timedelta(microseconds=250000)).isoformat()}), event("inside",
            start={"dateTime": (END + timedelta(microseconds=250000)).isoformat()}, end={"dateTime": (END + timedelta(hours=1)).isoformat()}),
            event("after", start={"dateTime": (END + timedelta(microseconds=500000)).isoformat()},
                  end={"dateTime": (END + timedelta(hours=1)).isoformat()})]})
        self.assertEqual(tuple(entry.event_id for entry in self.snapshot(period).events), ("inside",))
        query = self.backend.calls[0]["query"]
        self.assertEqual((query["timeMin"], query["timeMax"]), (START.isoformat(), (END + timedelta(seconds=1)).isoformat()))

    def test_malformed_page_items_and_item_shape_cannot_return_valid_prefix(self):
        for malformed in (None, [], "secret-description", {"items": None}, {"items": {}}, {"items": "bad"}):
            self.pages({"items": [event()], "nextPageToken": "later"}, malformed)
            self.error(self.snapshot)
        for malformed in (None, [], 1, "secret-title", {}):
            self.invalid(malformed)

    def test_malformed_or_repeated_page_tokens_fail_closed(self):
        for token in (1, [], {}, True, " ", "page\n2", "page\u202e2", "bad\ud800", "x" * 4097):
            self.pages({"items": [event()], "nextPageToken": token})
            self.error(self.snapshot)
        self.pages({"nextPageToken": "same"}, {"nextPageToken": "same"})
        self.error(self.snapshot)
        self.assertEqual(self.backend.page_number, 2)

    def test_twentieth_terminal_page_allowed_but_pending_page_is_limit_error(self):
        prefix = [{"nextPageToken": f"page-{index + 1}"} for index in range(19)]
        self.pages(*prefix, {"items": [event()]})
        result = self.snapshot()
        self.assertEqual((result.pages_read, result.raw_events_seen), (20, 1))
        self.pages(*prefix, {"nextPageToken": "page-20"})
        self.error(self.snapshot, AvailabilityLimitError)
        self.assertEqual(self.backend.page_number, 20)

    def test_raw_limit_counts_tombstones_transparent_outside_and_duplicates(self):
        tombstone = {"id": "gone", "status": "cancelled"}
        outside = event("outside", start={"dateTime": "2030-07-01T10:00:00Z"}, end={"dateTime": "2030-07-01T11:00:00Z"})
        values = [tombstone] * 500 + [outside] * 500 + [event("transparent", transparency="transparent")] * 998 + [event(), event()]
        self.pages({"items": values})
        result = self.snapshot()
        self.assertEqual((result.raw_events_seen, len(result.events)), (2000, 2))
        self.pages({"items": values + [tombstone]})
        self.error(self.snapshot, AvailabilityLimitError)
        self.pages({"items": values, "nextPageToken": "later"})
        self.error(self.snapshot, AvailabilityLimitError)
        self.assertEqual(self.backend.page_number, 1)

    def test_late_http_failure_is_sanitized_without_complete_prefix(self):
        self.pages({"items": [event()], "nextPageToken": "later"})
        self.backend.failure_after = 1
        self.error(self.snapshot)
        self.assertEqual((self.backend.page_number, self.backend.http_requests), (1, 4))

    def test_transport_and_http_failures_do_not_expose_provider_payload(self):
        self.backend.transport_failure = True
        self.error(self.snapshot)
        self.backend.transport_failure = False
        self.backend.failure_after = 0
        for status in (403, 404, 410, 429, 500):
            self.backend.failure_status = status
            self.error(self.snapshot)

    def test_default_factory_lazy_noninteractive_closes_and_maps_setup(self):
        opened, closed = [], []
        @contextmanager
        def client(**kwargs):
            opened.append(kwargs)
            try:
                yield self.service
            finally:
                closed.append(True)
        with patch("app.availability_calendar.calendar_client", side_effect=client):
            manager = default_availability_gateway()
            self.assertEqual(opened, [])
            with manager as gateway:
                self.assertIsInstance(gateway, GoogleAvailabilityCalendarGateway)
                self.assertIs(gateway.service, self.service)
            self.assertEqual(opened, [{"interactive": False}])
            self.assertEqual(closed, [True])
        @contextmanager
        def unavailable(**kwargs):
            raise CalendarSetupError(PRIVATE)
            yield
        with patch("app.availability_calendar.calendar_client", side_effect=unavailable):
            self.error(lambda: default_availability_gateway().__enter__(), AvailabilitySetupError)

    def test_factory_preserves_domain_limit_and_sanitizes_unexpected_cleanup(self):
        @contextmanager
        def client(**kwargs):
            yield self.service
        with patch("app.availability_calendar.calendar_client", side_effect=client):
            with self.assertRaises(AvailabilityLimitError):
                with default_availability_gateway():
                    raise AvailabilityLimitError()
        @contextmanager
        def broken(**kwargs):
            yield self.service
            raise OSError(PRIVATE)
        with patch("app.availability_calendar.calendar_client", side_effect=broken):
            self.error(self._close_factory)

    @staticmethod
    def _close_factory():
        with default_availability_gateway():
            pass


if __name__ == "__main__":
    unittest.main()
