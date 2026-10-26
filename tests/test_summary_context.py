"""Fatos completos de semana/mês: serviço puro, sem SDK, modelo ou banco."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock, patch

from app.services.agenda import AgendaEvent, CalendarPeriod
from app.services.event_details import EventDetails
from app.services.summary_context import (
    MAX_CONTEXT_BYTES, MAX_DETAILS, MAX_EVENTS, MAX_PAGES, MAX_TITLE_CHARS,
    SummaryCalendarBinding, SummaryCalendarSnapshot, SummaryCollectionError,
    SummaryContextService, SummaryInputError, SummaryLimitError, SummarySetupError, validate_input,
)


UTC = timezone.utc
NOW = datetime(2030, 6, 19, 12, tzinfo=UTC)
REFERENCE = "2030-06-19"
BINDING = SummaryCalendarBinding("private-calendar@example.invalid", "America/Recife")


def event(identity="private-event-id", *, start="2030-06-19T14:00:00+00:00",
          end="2030-06-19T15:00:00+00:00", **changes):
    values = dict(event_id=identity, title="Entrevista", start=start, end=end, all_day=False,
                  time_zone="America/Recife", status="confirmed", html_link="https://private.invalid/event")
    values.update(changes)
    return AgendaEvent(**values)


class FixtureGateway:
    def __init__(self, events=(), *, binding=BINDING, transform=None, error=None):
        self.events, self.selected_binding = events, binding
        self.transform, self.error = transform, error
        self.calls = []
        self.opened = self.closed = 0
        self.cleanup_error = None

    @contextmanager
    def factory(self):
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1
            if self.cleanup_error:
                raise self.cleanup_error

    def binding(self):
        self.calls.append(("binding",))
        return self.selected_binding

    def list_events(self, selected, *, expected_binding):
        self.calls.append(("list_events", selected, expected_binding))
        if self.error:
            raise self.error
        snapshot = SummaryCalendarSnapshot(self.selected_binding, selected, self.events,
                                           True, 1, len(self.events))
        return self.transform(snapshot) if self.transform else snapshot


class SummaryContextTests(unittest.TestCase):
    def build(self, events=(), *, period="week", reference_date=REFERENCE, now=NOW, **gateway_options):
        gateway = FixtureGateway(events, **gateway_options)
        service = SummaryContextService(gateway.factory, clock=lambda: now)
        return service.build(period=period, reference_date=reference_date), gateway

    def rejects(self, *, events=(), exception=SummaryCollectionError, period="week", reference_date=REFERENCE, **gateway_options):
        gateway = FixtureGateway(events, **gateway_options)
        with self.assertRaises(exception) as caught:
            SummaryContextService(gateway.factory, clock=lambda: NOW).build(period=period, reference_date=reference_date)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertNotIn(BINDING.calendar_id, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        return gateway

    def test_input_validation_precedes_factory_and_clock_and_reports_only_field_names(self):
        invalid = [("day", REFERENCE, ("period",)), ("WEEK", None, ("period",)),
                   (None, None, ("period",)), ([], None, ("period",)),
                   ("week", "2030-6-19", ("reference_date",)),
                   ("week", "2030-02-29", ("reference_date",)),
                   ("month", "9999-12-01", ("reference_date",)),
                   ("week", "9999-12-31", ("reference_date",)),
                   ("month", True, ("reference_date",)),
                   ("day", "PRIVATE", ("period", "reference_date"))]
        for kind, reference, fields in invalid:
            with self.subTest(kind=kind, reference=reference):
                factory, clock = Mock(), Mock()
                with self.assertRaises(SummaryInputError) as caught:
                    SummaryContextService(factory, clock=clock).build(period=kind, reference_date=reference)
                self.assertEqual(caught.exception.fields, fields)
                self.assertNotIn("PRIVATE", str(caught.exception))
                factory.assert_not_called(); clock.assert_not_called()

    def test_validate_input_returns_typed_date_and_accepts_real_leap_day(self):
        kind, reference = validate_input("month", "2028-02-29")
        self.assertEqual((kind, reference.isoformat()), ("month", "2028-02-29"))
        self.assertEqual(validate_input("week", None), ("week", None))

    def test_empty_complete_week_has_exact_projection_seven_zero_days_and_closed_gateway(self):
        result, gateway = self.build()
        self.assertEqual(set(result), {"schema_version", "period", "snapshot", "totals", "days", "events", "details", "execution_allowed"})
        self.assertEqual(result["schema_version"], 1)
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["period"], {"kind": "week", "reference_date": REFERENCE,
            "start": "2030-06-17T00:00:00-03:00", "end": "2030-06-24T00:00:00-03:00", "time_zone": BINDING.time_zone})
        self.assertEqual(result["snapshot"], {"collected_at_utc": NOW.isoformat(), "complete": True})
        self.assertEqual(result["totals"], dict.fromkeys(("events", "confirmed", "tentative", "all_day", "elapsed", "ongoing", "upcoming"), 0))
        self.assertEqual(len(result["days"]), 7)
        self.assertTrue(all(day["event_count"] == 0 for day in result["days"]))
        self.assertEqual(result["events"], [])
        self.assertEqual(result["details"], {"shown": 0, "total": 0, "omitted": 0, "limit": MAX_DETAILS, "truncated": False})
        self.assertEqual((gateway.opened, gateway.closed), (1, 1))
        self.assertEqual(gateway.calls[-1][2], BINDING)

    def test_week_uses_monday_even_when_reference_is_in_previous_year(self):
        result, _ = self.build(reference_date="2030-01-01")
        self.assertEqual(result["period"]["start"][:10], "2029-12-31")
        self.assertEqual(result["period"]["end"][:10], "2030-01-07")

    def test_month_keeps_every_day_including_leap_day_and_thirty_first(self):
        for reference, total in (("2028-02-29", 29), ("2030-01-15", 31)):
            with self.subTest(reference=reference):
                result, _ = self.build(period="month", reference_date=reference)
                self.assertEqual(len(result["days"]), total)
                self.assertEqual(result["days"][-1]["date"], reference[:7] + f"-{total:02}")

    def test_reference_defaults_to_today_in_agenda_zone_not_server_date(self):
        now = datetime(2030, 6, 24, 1, tzinfo=UTC)  # Domingo ainda em Recife.
        result, _ = self.build(reference_date=None, now=now)
        self.assertEqual(result["period"]["reference_date"], "2030-06-23")
        self.assertEqual(result["period"]["start"][:10], "2030-06-17")

    def test_dst_week_keeps_civil_days_with_167_or_169_real_hours(self):
        binding = SummaryCalendarBinding("dst-calendar", "America/New_York")
        for reference, hours in (("2030-03-10", 167), ("2030-11-03", 169)):
            with self.subTest(reference=reference):
                result, _ = self.build(reference_date=reference, binding=binding)
                start, end = (datetime.fromisoformat(result["period"][key]).astimezone(UTC) for key in ("start", "end"))
                self.assertEqual((end - start).total_seconds(), hours * 3600)
                self.assertEqual(len(result["days"]), 7)

    def test_historical_nonexistent_civil_day_never_receives_a_false_overlap_count(self):
        binding = SummaryCalendarBinding("apia-calendar", "Pacific/Apia")
        spanning = event(start="2011-12-29T00:00:00-10:00", end="2012-01-01T00:00:00+14:00",
                         time_zone=binding.time_zone)
        # Apia saltou 30/12/2011: o bucket tem início e fim no mesmo UTC.
        # A política escolhida recusa o contexto inteiro, inclusive se vazio.
        for source in ((spanning,), ()):
            with self.subTest(has_event=bool(source)):
                self.rejects(events=source, period="month", reference_date="2011-12-15", binding=binding)

    def test_whole_source_drives_totals_and_thirty_one_days_beyond_ten_details(self):
        source = tuple(event(f"private-{day:02}", start=f"2030-01-{day:02}T15:00:00Z",
                             end=f"2030-01-{day:02}T16:00:00Z",
                             status="tentative" if day % 2 else "confirmed") for day in range(1, 32))
        result, _ = self.build(source[::-1], period="month", reference_date="2030-01-10")
        self.assertEqual(result["totals"]["events"], 31)
        self.assertEqual((result["totals"]["confirmed"], result["totals"]["tentative"]), (15, 16))
        self.assertEqual(len(result["days"]), 31)
        self.assertTrue(all(day["event_count"] == 1 for day in result["days"]))
        self.assertEqual(result["details"], {"shown": 10, "total": 31, "omitted": 21, "limit": 10, "truncated": True})
        self.assertEqual([entry["ref"] for entry in result["events"]], [f"E{i}" for i in range(1, 11)])
        self.assertEqual(result["events"][-1]["start"][:10], "2030-01-10")

    def test_overlap_is_half_open_and_daily_counts_do_not_duplicate_period_total(self):
        source = (event("cross", start="2030-06-16T23:00:00-03:00", end="2030-06-19T00:00:00-03:00"),
                  event("ends-at-period", start="2030-06-16T22:00:00-03:00", end="2030-06-17T00:00:00-03:00"),
                  event("starts-at-end", start="2030-06-24T00:00:00-03:00", end="2030-06-24T01:00:00-03:00"))
        result, _ = self.build(source)
        self.assertEqual(result["totals"]["events"], 1)
        self.assertEqual([day["event_count"] for day in result["days"]], [1, 1, 0, 0, 0, 0, 0])
        self.assertEqual(result["details"]["total"], 1)

    def test_multiday_all_day_keeps_dates_and_exclusive_end_without_fabricated_time(self):
        source = (event("allday", start="2030-06-16", end="2030-06-19", all_day=True, status="tentative"),)
        result, _ = self.build(source)
        detail = result["events"][0]
        self.assertEqual((detail["start"], detail["end"], detail["all_day"]), ("2030-06-16", "2030-06-19", True))
        self.assertEqual(result["totals"]["all_day"], 1)
        self.assertEqual(result["totals"]["tentative"], 1)
        self.assertEqual([day["all_day"] for day in result["days"]], [1, 1, 0, 0, 0, 0, 0])

    def test_temporal_totals_use_exact_start_and_exclusive_end_at_collected_clock(self):
        source = (event("past", start="2030-06-19T10:00:00Z", end=NOW.isoformat()),
                  event("current", start=NOW.isoformat(), end="2030-06-19T13:00:00Z"),
                  event("future", start="2030-06-19T12:00:00.000001Z", end="2030-06-19T14:00:00Z", status="tentative"))
        result, _ = self.build(source)
        self.assertEqual({name: result["totals"][name] for name in ("elapsed", "ongoing", "upcoming")},
                         {"elapsed": 1, "ongoing": 1, "upcoming": 1})
        self.assertEqual([row["temporal_state"] for row in result["events"]], ["elapsed", "ongoing", "upcoming"])

    def test_event_instants_normalize_to_agenda_zone_and_ties_sort_by_private_id(self):
        source = (event("z-id", title="Segundo", start="2030-06-20T00:00:00+09:00", end="2030-06-20T01:00:00+09:00", time_zone="Asia/Tokyo"),
                  event("a-id", title="Primeiro", start="2030-06-19T15:00:00Z", end="2030-06-19T16:00:00Z"))
        result, _ = self.build(source)
        self.assertEqual([row["title"] for row in result["events"]], ["Primeiro", "Segundo"])
        self.assertTrue(all(row["start"] == "2030-06-19T12:00:00-03:00" for row in result["events"]))

    def test_identical_occurrence_duplicates_count_once_but_divergence_is_not_silently_chosen(self):
        original = event()
        result, _ = self.build((original, replace(original)))
        self.assertEqual(result["totals"]["events"], 1)
        for duplicate in (replace(original, title="Outro"), replace(original, status="tentative"),
                          replace(original, html_link="https://other.invalid")):
            with self.subTest(duplicate=duplicate):
                self.rejects(events=(original, duplicate))

    def test_titles_use_nfc_and_explicit_truncation_without_claiming_anonymization(self):
        title = "  Cafe\u0301\n\tcom\u202e  telefone 5511999990000 " + "x" * 200
        result, _ = self.build((event(title=title),))
        detail = result["events"][0]
        self.assertTrue(detail["title"].startswith("Café com telefone 5511999990000 "))
        self.assertEqual(len(detail["title"]), MAX_TITLE_CHARS)
        self.assertTrue(detail["title"].endswith("…"))
        self.assertTrue(detail["title_truncated"])
        self.assertFalse(result["details"]["truncated"])

    def test_projection_drops_ids_links_calendar_name_and_detail_subclass_private_fields(self):
        raw = EventDetails(**vars(event()), description="PRIVATE DESCRIPTION", location="PRIVATE PHONE")
        result, _ = self.build((raw,))
        serialized = json.dumps(result)
        for private in (raw.event_id, raw.html_link, BINDING.calendar_id, raw.description, raw.location):
            self.assertNotIn(private, serialized)
        self.assertEqual(set(result["events"][0]), {"ref", "title", "start", "end", "all_day", "status", "temporal_state", "title_truncated"})
        self.assertNotIn("time_zone", result["events"][0])

    def test_private_dto_repr_never_discloses_nested_source(self):
        binding = SummaryCalendarBinding("PRIVATE CALENDAR", "PRIVATE ZONE")
        snapshot = SummaryCalendarSnapshot(binding, None, (event(title="PRIVATE TITLE"),), True, 1, 1)
        self.assertEqual(repr(binding), "SummaryCalendarBinding(<private>)")
        self.assertEqual(repr(snapshot), "SummaryCalendarSnapshot(<private>)")

    def test_bad_binding_is_rejected_before_listing(self):
        for binding in (None, {}, SummaryCalendarBinding("primary", "America/Recife"),
                        SummaryCalendarBinding("with space", "America/Recife"),
                        SummaryCalendarBinding("valid-id", "Mars/PRIVATE"),
                        SummaryCalendarBinding("\ud800", "America/Recife")):
            with self.subTest(binding=repr(binding)):
                gateway = self.rejects(binding=binding)
                self.assertEqual(gateway.calls, [("binding",)])

    def test_snapshot_binding_period_completeness_and_metadata_are_strict(self):
        mutations = (lambda s: replace(s, complete=False), lambda s: replace(s, complete=1),
                     lambda s: replace(s, binding=SummaryCalendarBinding("other-id", s.binding.time_zone)),
                     lambda s: replace(s, period=replace(s.period, end=s.period.end + timedelta(days=1))),
                     lambda s: replace(s, period=replace(s.period, time_zone="UTC")),
                     lambda s: replace(s, events=[]), lambda s: replace(s, pages_read=0),
                     lambda s: replace(s, pages_read=True), lambda s: replace(s, raw_events_seen=True),
                     lambda s: replace(s, raw_events_seen=-1), lambda s: {},
                     lambda s: replace(s, events=(event(),), raw_events_seen=0))
        for transform in mutations:
            with self.subTest(transform=transform):
                self.rejects(transform=transform)

    def test_explicit_source_limits_raise_limit_error_without_partial_success(self):
        for transform in (lambda s: replace(s, pages_read=MAX_PAGES + 1),
                          lambda s: replace(s, raw_events_seen=MAX_EVENTS + 1),
                          lambda s: replace(s, events=(event(),) * (MAX_EVENTS + 1), raw_events_seen=MAX_EVENTS)):
            with self.subTest(transform=transform):
                self.rejects(exception=SummaryLimitError, transform=transform)
        result, _ = self.build(transform=lambda s: replace(s, pages_read=MAX_PAGES, raw_events_seen=MAX_EVENTS))
        self.assertEqual(result["totals"]["events"], 0)

    def test_maximum_complete_source_still_counts_all_occurrences_with_only_ten_details(self):
        source = tuple(event(f"source-{index:04}") for index in range(MAX_EVENTS))
        result, _ = self.build(source)
        self.assertEqual(result["totals"]["events"], MAX_EVENTS)
        self.assertEqual(sum(day["event_count"] for day in result["days"]), MAX_EVENTS)
        self.assertEqual(len(result["events"]), MAX_DETAILS)
        self.assertEqual(result["details"]["omitted"], MAX_EVENTS - MAX_DETAILS)

    def test_invalid_event_even_when_omitted_from_details_refuses_whole_context(self):
        source = tuple(event(str(index), start=f"2030-06-19T{index:02}:00:00Z", end=f"2030-06-19T{index:02}:30:00Z")
                       for index in range(10)) + (event("last", status="PRIVATE"),)
        self.rejects(events=source)

    def test_malformed_timestamps_unicode_status_or_all_day_source_cannot_become_empty(self):
        original = event()
        invalid = (replace(original, title="\ud800"), replace(original, event_id="private id"),
                   replace(original, status="cancelled"), replace(original, status="unknown"),
                   replace(original, all_day=1), replace(original, time_zone="PRIVATE"),
                   replace(original, start="2030-06-19T14:00:00"),
                   replace(original, start="2030-06-19T14:00:00+00:60"),
                   replace(original, html_link={"private": "invalid"}), replace(original, html_link="\ud800"),
                   replace(original, start=original.end), replace(original, start="2030-06-19"),
                   replace(original, start="2030-06-19", end="2030-06-20", all_day=True, time_zone="UTC"),
                   replace(original, start="2030-06-19", end="2030-06-19", all_day=True))
        for raw in invalid:
            with self.subTest(raw=repr(raw)):
                self.rejects(events=(raw,))

    def test_clock_is_aware_and_nonregressive_and_collection_timestamp_is_after_read(self):
        for invalid in (None, "PRIVATE", NOW.replace(tzinfo=None)):
            factory = Mock()
            with self.assertRaises(SummaryCollectionError):
                SummaryContextService(factory, clock=lambda: invalid).build(period="week")
            factory.assert_not_called()
        gateway = FixtureGateway()
        after = NOW + timedelta(seconds=10)
        result = SummaryContextService(gateway.factory, clock=Mock(side_effect=[NOW, after])).build(period="week", reference_date=REFERENCE)
        self.assertEqual(result["snapshot"]["collected_at_utc"], after.isoformat())
        gateway.events = (event(start="2030-06-19T12:00:05Z", end="2030-06-19T13:00:00Z"),)
        result = SummaryContextService(gateway.factory, clock=Mock(side_effect=[NOW, after])).build(period="week", reference_date=REFERENCE)
        self.assertEqual(result["totals"]["ongoing"], 1)
        with self.assertRaises(SummaryCollectionError):
            SummaryContextService(gateway.factory, clock=Mock(side_effect=[NOW, NOW - timedelta(microseconds=1)])).build(period="week")

    def test_provider_factory_or_cleanup_failure_is_sanitized_and_never_returns_empty(self):
        gateway = self.rejects(error=OSError("PRIVATE SDK TOKEN"))
        self.assertEqual(gateway.closed, 1)
        factory = Mock(side_effect=OSError("PRIVATE FACTORY"))
        with self.assertRaises(SummaryCollectionError) as caught:
            SummaryContextService(factory, clock=lambda: NOW).build(period="month", reference_date=REFERENCE)
        self.assertNotIn("PRIVATE", str(caught.exception))
        gateway = FixtureGateway()
        gateway.cleanup_error = OSError("PRIVATE CLEANUP")
        with self.assertRaises(SummaryCollectionError):
            SummaryContextService(gateway.factory, clock=lambda: NOW).build(period="week", reference_date=REFERENCE)

    def test_setup_and_limit_domain_errors_keep_their_class_without_importing_provider_exceptions(self):
        for exception in (SummarySetupError, SummaryLimitError):
            with self.subTest(exception=exception):
                self.rejects(exception=exception, error=exception())
                factory = Mock(side_effect=exception())
                with self.assertRaises(exception):
                    SummaryContextService(factory, clock=lambda: NOW).build(period="week", reference_date=REFERENCE)

    def test_context_budget_is_enforced_after_projection_without_truncating_days_or_totals(self):
        result, _ = self.build((event(title="💼" * MAX_TITLE_CHARS),), period="month")
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_CONTEXT_BYTES)
        with patch("app.services.summary_context.MAX_CONTEXT_BYTES", len(encoded) - 1):
            self.rejects(exception=SummaryLimitError, period="month", events=(event(title="💼" * MAX_TITLE_CHARS),))


if __name__ == "__main__":
    unittest.main()
