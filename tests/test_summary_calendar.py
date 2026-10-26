"""Gateway/SDK/transporte reais sobre HTTP em memória, sem OAuth ou sockets."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
import requests

from app.google_calendar import CalendarSetupError
from app.google_transport import SingleAttemptHttp
from app.services.agenda import CalendarPeriod, month_period, week_period
from app.services.summary_context import (
    SummaryCalendarBinding, SummaryCalendarSnapshot, SummaryCollectionError,
    SummaryLimitError, SummarySetupError,
)
from app.summary_calendar import GoogleSummaryCalendarGateway, default_summary_calendar_gateway
from calendar_http_fake import CalendarHttpFake


UTC = timezone.utc
START = datetime(2030, 6, 17, tzinfo=UTC)
END = START + timedelta(days=7)
TITLE = "Título privado fictício"


def event(event_id="summary-event", **changes):
    value = {"id": event_id, "summary": TITLE, "status": "confirmed",
        "start": {"dateTime": "2030-06-17T10:00:00-03:00"},
        "end": {"dateTime": "2030-06-17T11:00:00-03:00"}}
    value.update(changes)
    return value


class SummaryHttpFake(CalendarHttpFake):
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
            raise AssertionError("Nenhuma operação de escrita é permitida nesta fixture.")
        self.http_requests += 1
        if self.transport_failure:
            raise requests.ConnectionError("private provider error")
        if self.failure_after is not None and self.http_requests > self.failure_after:
            parsed = urlsplit(url)
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            call = {"method": "GET", "path": unquote(parsed.path), "query": query,
                    "headers": dict(headers or {}), "body": None, "status": None, "event_id": None}
            self.calls.append(call)
            return self._reply(call, self.failure_status, self._error(self.failure_status))
        return super().request(method, url, data=data, headers=headers, **kwargs)


class SummaryCalendarTests(unittest.TestCase):
    def setUp(self):
        self.backend = SummaryHttpFake()
        session_patch = patch("app.google_transport.requests.Session", side_effect=self.backend.session)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        resolver = patch("app.summary_calendar.resolve_calendar", side_effect=lambda _service: deepcopy(self.backend.calendar))
        self.resolve = resolver.start()
        self.addCleanup(resolver.stop)
        forbidden = patch("app.google_calendar.get_credentials", side_effect=AssertionError("OAuth real proibido."))
        forbidden.start()
        self.addCleanup(forbidden.stop)
        sleeping = patch("googleapiclient.http.time.sleep", return_value=None)
        sleeping.start()
        self.addCleanup(sleeping.stop)
        transport = SingleAttemptHttp(timeout=20)
        http = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
        self.service = build("calendar", "v3", http=http, cache_discovery=False, static_discovery=True)
        self.addCleanup(self.service.close)
        self.gateway = GoogleSummaryCalendarGateway(self.service)
        self.binding = SummaryCalendarBinding(self.backend.calendar["id"], self.backend.calendar["timeZone"])
        self.period = CalendarPeriod(START, END, self.binding.time_zone)

    def pages(self, *pages):
        self.backend.pages = list(pages)
        self.backend.page_number = 0
        self.backend.http_requests = 0
        self.backend.calls.clear()

    def snapshot(self, period=None, binding=None):
        return self.gateway.list_events(self.period if period is None else period,
            expected_binding=self.binding if binding is None else binding)

    def error(self, callback, error_type=SummaryCollectionError):
        with self.assertRaises(error_type) as caught:
            callback()
        if error_type is SummaryCollectionError:
            self.assertIs(type(caught.exception), SummaryCollectionError)
        for private in (TITLE, self.binding.calendar_id, "private provider error", "secret query"):
            self.assertNotIn(private, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def invalid_event(self, value):
        self.pages({"items": [value]})
        self.error(self.snapshot)

    def test_binding_is_current_private_and_requires_no_event_query(self):
        binding = self.gateway.binding()
        self.assertEqual(binding, self.binding)
        self.assertNotIn(binding.calendar_id, repr(binding))
        self.assertEqual(self.backend.http_requests, 0)
        self.resolve.assert_called_once_with(self.service)

    def test_binding_has_no_primary_timezone_or_invalid_calendar_fallback(self):
        original = deepcopy(self.backend.calendar)
        for changes in ({"id": "primary"}, {"id": ""}, {"id": "bad\nprivate"},
                        {"id": "bad\ud800"}, {"id": "x" * 1025}, {"timeZone": ""},
                        {"timeZone": "Mars/Olympus"}, {"timeZone": " UTC"},
                        {"primary": True}, {"primary": 0}, {"deleted": True}, {"accessRole": "reader"}):
            with self.subTest(changes=ascii(changes)):
                self.backend.calendar = {**original, **changes}
                self.error(self.gateway.binding)
        for field in ("id", "timeZone"):
            self.backend.calendar = {key: value for key, value in original.items() if key != field}
            self.error(self.gateway.binding)
        self.assertEqual(self.backend.http_requests, 0)

    def test_resolver_setup_failure_is_mapped_to_pure_sanitized_setup_error(self):
        self.resolve.side_effect = CalendarSetupError("private provider error")
        self.error(self.gateway.binding, SummarySetupError)
        self.error(self.snapshot, SummarySetupError)
        self.assertEqual(self.backend.http_requests, 0)

    def test_resolver_transport_or_malformed_data_is_sanitized_collection_error(self):
        for failure in (OSError("private provider error"), ValueError("secret query")):
            self.resolve.side_effect = failure
            self.error(self.gateway.binding)
        self.resolve.side_effect = None
        self.resolve.return_value = []
        self.error(self.gateway.binding)

    def test_sdk_query_has_exact_calendar_zone_bounds_and_only_minimal_read_fields(self):
        value = event(description="PRIVATE", location="PRIVATE", attendees=[{"email": "PRIVATE"}],
                      htmlLink="https://example.invalid/PRIVATE")
        self.pages({"items": [value]})
        result = self.snapshot()
        self.assertIs(type(result), SummaryCalendarSnapshot)
        self.assertEqual((result.binding, result.period, result.complete, result.pages_read, result.raw_events_seen),
                         (self.binding, self.period, True, 1, 1))
        self.assertIs(result.period, self.period)
        self.assertEqual(result.events[0].event_id, value["id"])
        self.assertIsNone(result.events[0].html_link)
        self.assertNotIn("PRIVATE", repr(result))
        query = self.backend.calls[0]["query"]
        self.assertEqual(query["timeMin"], START.isoformat())
        self.assertEqual(query["timeMax"], END.isoformat())
        self.assertEqual(query["timeZone"], self.binding.time_zone)
        self.assertEqual(query["singleEvents"], "true")
        self.assertEqual(query["showDeleted"], "false")
        self.assertEqual(query["orderBy"], "startTime")
        self.assertEqual(query["maxResults"], "250")
        self.assertNotIn("pageToken", query)
        self.assertIn(self.binding.calendar_id, self.backend.calls[0]["path"])
        self.assertEqual(query["fields"], self.gateway.EVENT_FIELDS)
        for excluded in ("description", "location", "htmlLink", "attendees", "etag", "transparency"):
            self.assertNotIn(excluded, query["fields"])
        self.assertTrue(all(call["method"] == "GET" for call in self.backend.calls))

    def test_binding_is_reread_and_timezone_or_id_change_prevents_event_query(self):
        self.gateway.binding()
        original = deepcopy(self.backend.calendar)
        for changes in ({"id": "other-calendar@example.invalid"}, {"timeZone": "UTC"}):
            self.backend.calendar = {**original, **changes}
            self.error(self.snapshot)
        self.assertEqual(self.backend.http_requests, 0)

    def test_invalid_period_and_expected_binding_are_rejected_before_resolver(self):
        invalid = (None, {}, CalendarPeriod(START, START, self.binding.time_zone),
                   CalendarPeriod(END, START, self.binding.time_zone),
                   CalendarPeriod(START.replace(tzinfo=None), END, self.binding.time_zone),
                   CalendarPeriod("secret query", END, self.binding.time_zone),
                   CalendarPeriod(START, END, "UTC"))
        for period in invalid:
            with self.subTest(period=period):
                self.error(lambda: self.gateway.list_events(period, expected_binding=self.binding))
        for binding in (None, {}, SummaryCalendarBinding("primary", self.binding.time_zone),
                        SummaryCalendarBinding("bad\ud800", self.binding.time_zone),
                        SummaryCalendarBinding(self.binding.calendar_id, " UTC")):
            self.error(lambda: self.gateway.list_events(self.period, expected_binding=binding))
        self.resolve.assert_not_called()
        self.assertEqual(self.backend.http_requests, 0)

    def test_empty_terminal_page_is_complete_and_omitted_items_is_valid_empty(self):
        for page in ({"items": []}, {}, {"nextPageToken": ""}):
            self.pages(page)
            result = self.snapshot()
            self.assertEqual((result.events, result.pages_read, result.raw_events_seen), ((), 1, 0))
            self.assertTrue(result.complete)

    def test_empty_forward_page_and_identical_cross_page_duplicate_are_fully_read(self):
        value = event()
        self.pages({"items": [], "nextPageToken": "page-2"},
                   {"items": [value], "nextPageToken": "page-3"}, {"items": [value]})
        result = self.snapshot()
        self.assertEqual((len(result.events), result.pages_read, result.raw_events_seen), (1, 3, 2))
        self.assertEqual([call["query"].get("pageToken") for call in self.backend.calls], [None, "page-2", "page-3"])

    def test_conflicting_duplicate_rejects_entire_collection_including_outside_window(self):
        for first in (event(), event(start={"dateTime": "2030-07-01T10:00:00Z"},
                                    end={"dateTime": "2030-07-01T11:00:00Z"}),
                      {"id": "summary-event", "status": "cancelled"}):
            second = {**first, "summary": "changed"}
            self.pages({"items": [first], "nextPageToken": "later"}, {"items": [second]})
            self.error(self.snapshot)

    def test_cancelled_tombstones_are_skipped_but_still_counted_and_ids_validated(self):
        self.pages({"items": [{"id": "gone", "status": "cancelled"}, event()]})
        result = self.snapshot()
        self.assertEqual((len(result.events), result.raw_events_seen), (1, 2))
        for identity in (None, "", "bad\x00", "bad\u202e", "bad\ud800", "bad space", "x" * 1025):
            self.invalid_event({"id": identity, "status": "cancelled"})

    def test_opaque_case_sensitive_occurrence_identifiers_are_preserved(self):
        self.pages({"items": [event("Opaque_ID@AbC"), event("opaque_ID@AbC")]})
        result = self.snapshot()
        self.assertEqual({entry.event_id for entry in result.events}, {"Opaque_ID@AbC", "opaque_ID@AbC"})

    def test_series_resource_is_rejected_even_when_cancelled_or_outside_window(self):
        for recurrence in ([], None, ["RRULE:FREQ=DAILY"], "RRULE:FREQ=DAILY"):
            for status in ("confirmed", "cancelled"):
                self.invalid_event(event(recurrence=recurrence, status=status))

    def test_expanded_occurrences_allow_changed_original_start_and_keep_own_identity(self):
        value = event("series_20300617", recurringEventId="series",
                      originalStartTime={"dateTime": "2030-06-16T10:00:00-03:00"})
        self.pages({"items": [value]})
        result = self.snapshot()
        self.assertEqual(result.events[0].event_id, value["id"])
        self.assertEqual(result.events[0].start, "2030-06-17T10:00:00-03:00")

    def test_incomplete_or_incoherent_occurrence_metadata_is_not_ignored(self):
        for changes in ({"recurringEventId": "series"}, {"originalStartTime": event()["start"]},
                        {"recurringEventId": "summary-event", "originalStartTime": event()["start"]},
                        {"recurringEventId": "series", "originalStartTime": {"date": "2030-06-17"}},
                        {"recurringEventId": "bad\n", "originalStartTime": event()["start"]}):
            self.invalid_event(event(**changes))

    def test_confirmed_tentative_and_blank_title_are_minimal_with_no_link(self):
        blank = event("blank", status="tentative")
        del blank["summary"]
        self.pages({"items": [blank, event()]})
        result = self.snapshot()
        self.assertEqual({value.status for value in result.events}, {"confirmed", "tentative"})
        self.assertEqual(next(value.title for value in result.events if value.event_id == "blank"), "")
        self.assertTrue(all(value.html_link is None for value in result.events))

    def test_unknown_status_wrong_item_shape_or_invalid_title_is_sanitized(self):
        for status in (None, "unknown", "", 1, [], {}):
            self.invalid_event(event(status=status))
        for title in (None, 1, [], {}, "bad\ud800", "x" * 10001):
            self.invalid_event(event(summary=title))
        for item in (None, [], 1, "secret query", {}):
            self.invalid_event(item)

    def test_strict_dates_datetimes_offset_and_homogeneous_bounds(self):
        invalid_start = ({}, None, {"date": "2030-06-17", "dateTime": "2030-06-17T10:00:00Z"},
            {"date": "20300617"}, {"dateTime": "2030-06-17T10:00:00"},
            {"dateTime": "2030-06-17 10:00:00Z"}, {"dateTime": "2030-06-17T25:00:00Z"},
            {"dateTime": "2030-06-17T10:00:00+01:60"}, {"dateTime": "2030-06-17T10:00:00-00:99"},
            {"dateTime": "2030-06-17T10:00:00+24:00"}, {"dateTime": "2030-06-17T10:00:00-23:60"},
            {"dateTime": "2030-06-17T10:00:00.1234567Z"},
            {"dateTime": "2030-06-17T10:00:00Z", "timeZone": "Mars/Olympus"},
            {"date": "2030-06-17"})
        for start in invalid_start:
            self.invalid_event(event(start=start))
        for end in ({"dateTime": "2030-06-17T10:00:00-03:00"},
                    {"dateTime": "2030-06-17T09:00:00-03:00"},
                    {"dateTime": "2030-06-17T12:00:00+01:60"}):
            self.invalid_event(event(end=end))
        self.invalid_event(event(recurringEventId="series",
            originalStartTime={"dateTime": "2030-06-17T09:00:00+01:60"}))

    def test_all_day_dates_use_authorized_zone_and_exact_overlap_with_exclusive_end(self):
        period = week_period(date(2030, 6, 17), self.binding.time_zone)
        self.pages({"items": [event("prior", start={"date": "2030-06-16"}, end={"date": "2030-06-17"}),
            event("span", start={"date": "2030-06-16"}, end={"date": "2030-06-18"}),
            event("next", start={"date": "2030-06-24"}, end={"date": "2030-06-25"})]})
        result = self.snapshot(period=period)
        self.assertEqual(tuple(value.event_id for value in result.events), ("span",))
        self.assertEqual((result.events[0].start, result.events[0].end, result.events[0].all_day),
                         ("2030-06-16", "2030-06-18", True))
        self.invalid_event(event(start={"date": "2030-06-17", "timeZone": "UTC"},
                                 end={"date": "2030-06-18", "timeZone": "UTC"}))

    def test_nonexistent_all_day_midnight_and_period_wall_time_are_rejected(self):
        self.backend.calendar["timeZone"] = "Pacific/Apia"
        binding = SummaryCalendarBinding(self.binding.calendar_id, "Pacific/Apia")
        period = month_period(date(2011, 12, 1), binding.time_zone)
        self.pages({"items": [event(start={"date": "2011-12-30"}, end={"date": "2011-12-31"})]})
        self.error(lambda: self.snapshot(period=period, binding=binding))
        zone = ZoneInfo("America/New_York")
        invalid = CalendarPeriod(datetime(2030, 3, 10, 2, 30, tzinfo=zone),
                                 datetime(2030, 3, 10, 4, tzinfo=zone), "America/New_York")
        self.error(lambda: self.snapshot(period=invalid,
            binding=SummaryCalendarBinding(self.binding.calendar_id, "America/New_York")))

    def test_explicit_offsets_across_dst_fold_preserve_distinct_instants_and_calendar_display(self):
        self.backend.calendar["timeZone"] = "America/New_York"
        binding = SummaryCalendarBinding(self.binding.calendar_id, "America/New_York")
        period = week_period(date(2030, 11, 3), binding.time_zone)
        self.pages({"items": [event("late", start={"dateTime": "2030-11-03T01:30:00-05:00"},
            end={"dateTime": "2030-11-03T02:00:00-05:00"}),
            event("early", start={"dateTime": "2030-11-03T01:30:00-04:00"},
                  end={"dateTime": "2030-11-03T01:45:00-04:00"})]})
        result = self.snapshot(period=period, binding=binding)
        self.assertEqual(tuple(value.event_id for value in result.events), ("early", "late"))
        self.assertEqual([value.start[-6:] for value in result.events], ["-04:00", "-05:00"])

    def test_declared_original_timezone_does_not_reinterpret_response_offset(self):
        self.pages({"items": [event(start={"dateTime": "2030-06-17T14:00:00+01:00", "timeZone": "America/New_York"},
            end={"dateTime": "2030-06-17T15:00:00+01:00", "timeZone": "America/New_York"})]})
        result = self.snapshot()
        self.assertEqual(result.events[0].start, "2030-06-17T10:00:00-03:00")
        self.assertEqual(result.events[0].time_zone, self.binding.time_zone)

    def test_fractional_query_bounds_expand_outward_but_filter_exact_local_overlap(self):
        period = CalendarPeriod(START + timedelta(microseconds=500000), END + timedelta(microseconds=500000), self.binding.time_zone)
        self.pages({"items": [event("before", start={"dateTime": (START - timedelta(hours=1)).isoformat()},
                end={"dateTime": (START + timedelta(microseconds=250000)).isoformat()}),
            event("inside", start={"dateTime": (END + timedelta(microseconds=250000)).isoformat()},
                  end={"dateTime": (END + timedelta(hours=1)).isoformat()}),
            event("after", start={"dateTime": (END + timedelta(microseconds=750000)).isoformat()},
                  end={"dateTime": (END + timedelta(hours=1)).isoformat()})]})
        result = self.snapshot(period=period)
        self.assertEqual(tuple(value.event_id for value in result.events), ("inside",))
        query = self.backend.calls[0]["query"]
        self.assertEqual((query["timeMin"], query["timeMax"]), (START.isoformat(), (END + timedelta(seconds=1)).isoformat()))

    def test_malformed_page_or_items_never_returns_a_valid_prefix(self):
        for invalid in (None, [], "secret query", {"items": None}, {"items": {}}, {"items": "bad"}):
            self.pages({"items": [event()], "nextPageToken": "later"}, invalid)
            self.error(self.snapshot)

    def test_repeated_or_malformed_tokens_do_not_complete_snapshot(self):
        for token in (1, [], {}, True, " ", "page\n2", "page\u202e2", "bad\ud800", "x" * 4097):
            self.pages({"items": [event()], "nextPageToken": token})
            self.error(self.snapshot)
        self.pages({"nextPageToken": "same"}, {"nextPageToken": "same"})
        self.error(self.snapshot)
        self.assertEqual(self.backend.page_number, 2)

    def test_twentieth_terminal_page_is_complete_but_next_page_token_is_limit_error(self):
        pages = [{"items": [], "nextPageToken": "page-" + str(index + 1)} for index in range(19)]
        self.pages(*pages, {"items": [event()]})
        result = self.snapshot()
        self.assertEqual((result.pages_read, result.raw_events_seen, len(result.events)), (20, 1, 1))
        self.pages(*pages, {"items": [], "nextPageToken": "page-20"})
        self.error(self.snapshot, SummaryLimitError)
        self.assertEqual(self.backend.page_number, 20)

    def test_raw_limit_includes_cancelled_outside_and_duplicate_items(self):
        tombstone = {"id": "gone", "status": "cancelled"}
        outside = event("outside", start={"dateTime": "2030-07-01T10:00:00Z"},
                        end={"dateTime": "2030-07-01T11:00:00Z"})
        values = [tombstone] * 998 + [outside] * 1000 + [event(), event()]
        self.pages({"items": values})
        result = self.snapshot()
        self.assertEqual((result.raw_events_seen, len(result.events)), (2000, 1))
        self.pages({"items": values + [tombstone]})
        self.error(self.snapshot, SummaryLimitError)

    def test_at_raw_limit_pending_pagination_is_limit_not_false_completion(self):
        self.pages({"items": [{"id": "gone", "status": "cancelled"}] * 2000, "nextPageToken": "later"})
        self.error(self.snapshot, SummaryLimitError)
        self.assertEqual(self.backend.page_number, 1)

    def test_late_google_http_error_cannot_return_first_page_as_complete(self):
        self.pages({"items": [event()], "nextPageToken": "later"})
        self.backend.failure_after = 1
        self.error(self.snapshot)
        self.assertEqual(self.backend.page_number, 1)
        self.assertEqual(self.backend.http_requests, 4)  # uma página e três tentativas de leitura do SDK.

    def test_google_network_http_and_malformed_failure_are_sanitized(self):
        self.backend.transport_failure = True
        self.error(self.snapshot)
        self.backend.transport_failure = False
        self.backend.failure_after = 0
        for status in (403, 404, 410, 429, 500):
            self.backend.failure_status = status
            self.error(self.snapshot)

    def test_default_factory_is_lazy_noninteractive_closes_client_and_maps_setup(self):
        opened, closed = [], []
        @contextmanager
        def client(**kwargs):
            opened.append(kwargs)
            try:
                yield self.service
            finally:
                closed.append(True)
        with patch("app.summary_calendar.calendar_client", side_effect=client):
            manager = default_summary_calendar_gateway()
            self.assertEqual(opened, [])
            with manager as gateway:
                self.assertIsInstance(gateway, GoogleSummaryCalendarGateway)
                self.assertIs(gateway.service, self.service)
            self.assertEqual(opened, [{"interactive": False}])
            self.assertEqual(closed, [True])
        @contextmanager
        def unavailable(**kwargs):
            raise CalendarSetupError("private provider error")
            yield
        with patch("app.summary_calendar.calendar_client", side_effect=unavailable):
            self.error(lambda: default_summary_calendar_gateway().__enter__(), SummarySetupError)

    def test_default_factory_does_not_reclassify_domain_limit_from_body(self):
        @contextmanager
        def client(**kwargs):
            yield self.service
        with patch("app.summary_calendar.calendar_client", side_effect=client):
            with self.assertRaises(SummaryLimitError):
                with default_summary_calendar_gateway():
                    raise SummaryLimitError()
        @contextmanager
        def broken_cleanup(**kwargs):
            yield self.service
            raise OSError("private provider error")
        with patch("app.summary_calendar.calendar_client", side_effect=broken_cleanup):
            self.error(lambda: self._close_factory())

    @staticmethod
    def _close_factory():
        with default_summary_calendar_gateway():
            pass


if __name__ == "__main__":
    unittest.main()
