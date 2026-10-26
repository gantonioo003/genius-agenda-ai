"""Disponibilidade pura: fonte completa, intervalos reais e fronteiras civis."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock

from app.services.agenda import CalendarPeriod
from app.services.availability import (
    AvailabilityBinding, AvailabilityCollectionError, AvailabilityEvent,
    AvailabilityInputError, AvailabilityLimitError, AvailabilityService,
    AvailabilitySetupError, AvailabilitySnapshot, MAX_EVENTS, MAX_PAGES,
    validate_request,
)


UTC = timezone.utc
NOW = datetime(2030, 6, 19, 11, tzinfo=UTC)
BINDING = AvailabilityBinding("private-calendar@example.invalid", "America/Recife")
REQUEST = {"date_from": "2030-06-19", "date_to": "2030-06-19", "window_start": "09:00",
           "window_end": "12:00", "duration_minutes": 30, "buffer_minutes": 0}


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event(identity="private-event-id", *, start="2030-06-19T12:00:00Z",
          end="2030-06-19T12:30:00Z", **changes):
    values = dict(event_id=identity, start_utc=stamp(start), end_utc=stamp(end),
                  status="confirmed", transparency="opaque", all_day=False)
    values.update(changes)
    return AvailabilityEvent(**values)


class FixtureGateway:
    def __init__(self, events=(), *, binding=BINDING, transform=None, error=None, cleanup_error=None):
        self.events, self.selected_binding = events, binding
        self.transform, self.error, self.cleanup_error = transform, error, cleanup_error
        self.calls = []
        self.opened = self.closed = 0

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

    def list_occupancy(self, selected, *, expected_binding):
        self.calls.append(("list_occupancy", selected, expected_binding))
        if self.error:
            raise self.error
        snapshot = AvailabilitySnapshot(self.selected_binding, selected, self.events,
                                        True, 1, len(self.events))
        return self.transform(snapshot) if self.transform else snapshot


class AvailabilityTests(unittest.TestCase):
    def find(self, events=(), *, request=None, now=NOW, clock=None, **options):
        gateway = FixtureGateway(events, **options)
        result = AvailabilityService(gateway.factory, clock=clock or (lambda: now)).find(
            **(REQUEST | (request or {})))
        return result, gateway

    def reject_source(self, *, events=(), exception=AvailabilityCollectionError, **options):
        gateway = FixtureGateway(events, **options)
        with self.assertRaises(exception) as caught:
            AvailabilityService(gateway.factory, clock=lambda: NOW).find(**REQUEST)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertNotIn(BINDING.calendar_id, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        return gateway

    def test_invalid_request_precedes_clock_and_gateway_and_reports_only_fields(self):
        cases = [({"date_from": "2030-6-19"}, ("date_from",)),
                 ({"date_from": "2030-02-29"}, ("date_from",)),
                 ({"date_to": "2030-06-18"}, ("date_from", "date_to")),
                 ({"date_to": "2030-07-20"}, ("date_from", "date_to")),
                 ({"date_from": None}, ("date_from",)),
                 ({"date_to": True}, ("date_to",)),
                 ({"window_start": "9:00"}, ("window_start",)),
                 ({"window_start": "24:00"}, ("window_start",)),
                 ({"window_end": "24:01"}, ("window_end",)),
                 ({"window_end": "09:00"}, ("window_start", "window_end")),
                 ({"window_end": "08:00"}, ("window_start", "window_end")),
                 ({"window_start": "０９:00"}, ("window_start",)),
                 ({"duration_minutes": True}, ("duration_minutes",)),
                 ({"duration_minutes": "30"}, ("duration_minutes",)),
                 ({"duration_minutes": 0}, ("duration_minutes",)),
                 ({"duration_minutes": 1441}, ("duration_minutes",)),
                 ({"buffer_minutes": False}, ("buffer_minutes",)),
                 ({"buffer_minutes": -1}, ("buffer_minutes",)),
                 ({"buffer_minutes": 241}, ("buffer_minutes",)),
                 ({"date_from": "9999-12-31", "date_to": "9999-12-31", "window_end": "24:00"}, ("date_to",)),
                 ({"date_from": "PRIVATE", "duration_minutes": []}, ("date_from", "duration_minutes"))]
        for changes, fields in cases:
            with self.subTest(changes=changes):
                factory, clock = Mock(), Mock()
                with self.assertRaises(AvailabilityInputError) as caught:
                    AvailabilityService(factory, clock=clock).find(**(REQUEST | changes))
                self.assertEqual(caught.exception.fields, fields)
                self.assertNotIn("PRIVATE", str(caught.exception))
                factory.assert_not_called()
                clock.assert_not_called()

    def test_request_accepts_leap_day_and_exact_31_inclusive_days(self):
        request = REQUEST | {"date_from": "2028-02-01", "date_to": "2028-03-02"}
        validate_request(**request)
        result, gateway = self.find(request=request, now=stamp("2028-01-31T00:00:00Z"))
        self.assertEqual(result["count"], 3)
        self.assertEqual(gateway.calls[1][1].end.date().isoformat(), "2028-03-02")

    def test_schema_exposes_only_suggestions_and_explicit_scope_without_private_ids(self):
        result, gateway = self.find()
        self.assertEqual(set(result), {"schema_version", "request", "period", "snapshot", "slots",
                         "count", "limit", "more_available", "scope", "reservation_created", "execution_allowed"})
        self.assertEqual(result["request"], REQUEST | {"step_minutes": 15})
        self.assertEqual(result["scope"], "authorized_calendar_only")
        self.assertFalse(result["reservation_created"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual((result["count"], result["limit"], result["more_available"]), (3, 3, True))
        self.assertEqual(result["slots"][0], {"ref": "S1", "start": "2030-06-19T09:00:00-03:00",
                         "end": "2030-06-19T09:30:00-03:00", "time_zone": BINDING.time_zone})
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual((gateway.opened, gateway.closed), (1, 1))
        self.assertEqual([call[0] for call in gateway.calls], ["binding", "list_occupancy"])
        self.assertEqual(gateway.calls[1][2], BINDING)

    def test_private_dto_representations_do_not_reveal_identifiers_or_timestamps(self):
        selected = CalendarPeriod(NOW, NOW + timedelta(hours=1), BINDING.time_zone)
        for value in (BINDING, event(), AvailabilitySnapshot(BINDING, selected, (event(),), True, 1, 1)):
            self.assertEqual(repr(value), type(value).__name__ + "(<private>)")

    def test_step_is_global_quarter_hour_instead_of_window_start(self):
        result, _ = self.find(request={"window_start": "09:07", "window_end": "10:00"})
        self.assertEqual([slot["start"][11:16] for slot in result["slots"]], ["09:15", "09:30"])
        self.assertFalse(result["more_available"])

    def test_one_minute_duration_uses_grid_and_fits_exclusive_end(self):
        result, _ = self.find(request={"window_start": "09:59", "window_end": "10:01", "duration_minutes": 1})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["slots"][0]["start"][11:16], "10:00")
        self.assertEqual(result["slots"][0]["end"][11:16], "10:01")

    def test_alternative_suggestions_may_overlap_and_fourth_sets_more_available(self):
        result, _ = self.find(request={"duration_minutes": 60})
        self.assertEqual([slot["start"][11:16] for slot in result["slots"]], ["09:00", "09:15", "09:30"])
        self.assertLess(stamp(result["slots"][1]["start"]), stamp(result["slots"][0]["end"]))
        self.assertTrue(result["more_available"])

    def test_valid_empty_and_too_short_window_are_complete_not_read_failure(self):
        result, gateway = self.find(request={"window_end": "09:15", "duration_minutes": 30})
        self.assertEqual((result["count"], result["slots"], result["more_available"]), (0, [], False))
        self.assertTrue(result["snapshot"]["complete"])
        self.assertEqual(len(gateway.calls), 2)

    def test_confirmed_and_tentative_block_but_transparent_and_cancelled_do_not(self):
        events = (event(), event("tentative", start="2030-06-19T12:30:00Z", end="2030-06-19T13:00:00Z", status="tentative"),
                  event("transparent", end="2030-06-19T15:00:00Z", transparency="transparent"),
                  event("cancelled", end="2030-06-19T15:00:00Z", status="cancelled"))
        result, _ = self.find(events)
        self.assertEqual([slot["start"][11:16] for slot in result["slots"]], ["10:00", "10:15", "10:30"])

    def test_busy_union_is_half_open_and_adjacent_intervals_merge_without_extra_block(self):
        events = (event("a", start="2030-06-19T12:30:00Z", end="2030-06-19T12:45:00Z"),
                  event("b", start="2030-06-19T12:45:00Z", end="2030-06-19T13:00:00Z"))
        result, _ = self.find(events, request={"window_end": "10:30"})
        self.assertEqual([slot["start"][11:16] for slot in result["slots"]], ["09:00", "10:00"])

    def test_buffer_includes_events_outside_window_and_expands_single_query(self):
        events = (event("before", start="2030-06-19T11:45:00Z", end="2030-06-19T12:00:00Z"),
                  event("after", start="2030-06-19T13:00:00Z", end="2030-06-19T13:15:00Z"))
        result, gateway = self.find(events, request={"window_end": "10:00", "duration_minutes": 15, "buffer_minutes": 15})
        self.assertEqual([slot["start"][11:16] for slot in result["slots"]], ["09:15", "09:30"])
        selected = gateway.calls[1][1]
        self.assertEqual((selected.start.isoformat(), selected.end.isoformat()),
                         ("2030-06-19T08:45:00-03:00", "2030-06-19T10:15:00-03:00"))
        self.assertEqual((result["period"]["start"], result["period"]["end"]),
                         ("2030-06-19T09:00:00-03:00", "2030-06-19T10:00:00-03:00"))

    def test_buffer_does_not_shrink_empty_daily_window(self):
        result, _ = self.find(request={"window_end": "10:00", "duration_minutes": 60, "buffer_minutes": 240})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["slots"][0]["start"][11:16], "09:00")

    def test_all_day_blocks_interval_across_days_without_invented_hours(self):
        full_day = event(start="2030-06-19T03:00:00Z", end="2030-06-20T03:00:00Z", all_day=True)
        result, _ = self.find((full_day,), request={"date_to": "2030-06-20"})
        self.assertEqual(result["slots"][0]["start"], "2030-06-20T09:00:00-03:00")

    def test_past_range_still_reads_complete_source_once(self):
        result, gateway = self.find(now=stamp("2030-06-20T00:00:00Z"))
        self.assertEqual(result["count"], 0)
        self.assertEqual([call[0] for call in gateway.calls], ["binding", "list_occupancy"])

    def test_clock_after_read_and_cleanup_excludes_starts_that_became_past(self):
        gateway = FixtureGateway()
        calls = []
        def clock():
            calls.append(gateway.closed)
            return stamp("2030-06-19T12:00:00Z") if len(calls) == 1 else stamp("2030-06-19T12:16:00Z")
        result = AvailabilityService(gateway.factory, clock=clock).find(**REQUEST)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(result["slots"][0]["start"][11:16], "09:30")
        self.assertEqual(result["snapshot"]["observed_at_utc"], "2030-06-19T12:16:00+00:00")

    def test_clock_must_be_aware_and_nonregressive(self):
        for values in ((NOW.replace(tzinfo=None),), (NOW, NOW - timedelta(microseconds=1)),
                       (NOW, NOW.replace(tzinfo=None))):
            with self.subTest(values=values):
                clock = Mock(side_effect=values)
                gateway = FixtureGateway()
                with self.assertRaises(AvailabilityCollectionError):
                    AvailabilityService(gateway.factory, clock=clock).find(**REQUEST)

    def test_read_failure_never_returns_empty_suggestions_or_connector_data(self):
        gateway = self.reject_source(error=RuntimeError("PRIVATE provider payload"))
        self.assertEqual((gateway.opened, gateway.closed), (1, 1))

    def test_cleanup_failure_prevents_successful_snapshot(self):
        self.reject_source(cleanup_error=RuntimeError("PRIVATE cleanup"))

    def test_domain_setup_and_limit_errors_remain_distinct_and_sanitized(self):
        for exception in (AvailabilitySetupError, AvailabilityLimitError):
            with self.subTest(exception=exception):
                self.reject_source(error=exception(), exception=exception)
                factory = Mock(side_effect=exception())
                with self.assertRaises(exception):
                    AvailabilityService(factory, clock=lambda: NOW).find(**REQUEST)

    def test_invalid_binding_fails_closed_without_occupancy_query(self):
        for binding in (AvailabilityBinding("primary", "UTC"), AvailabilityBinding("", "UTC"),
                        AvailabilityBinding(BINDING.calendar_id, "Invalid/PRIVATE"),
                        AvailabilityBinding("private\ud800", "UTC"), {"time_zone": "UTC"}):
            with self.subTest(binding=repr(binding)):
                gateway = self.reject_source(binding=binding)
                self.assertEqual(gateway.calls, [("binding",)])

    def test_snapshot_binding_period_complete_and_metadata_are_revalidated(self):
        transforms = [lambda s: replace(s, complete=False), lambda s: replace(s, complete=1),
                      lambda s: replace(s, binding=AvailabilityBinding("other", BINDING.time_zone)),
                      lambda s: replace(s, period=replace(s.period, time_zone="UTC")),
                      lambda s: replace(s, period=replace(s.period, end=s.period.end + timedelta(minutes=1))),
                      lambda s: replace(s, period=replace(s.period, start=s.period.start.replace(tzinfo=None))),
                      lambda s: replace(s, events=list(s.events)), lambda s: replace(s, pages_read=0),
                      lambda s: replace(s, pages_read=True), lambda s: replace(s, raw_events_seen=True),
                      lambda s: replace(s, raw_events_seen=-1), lambda s: replace(s, raw_events_seen=0),
                      lambda s: {"complete": True}]
        for transform in transforms:
            with self.subTest(transform=transforms.index(transform)):
                self.reject_source(events=(event(),), transform=transform)

    def test_equivalent_snapshot_offsets_are_not_period_drift(self):
        result, _ = self.find(transform=lambda s: replace(s, period=CalendarPeriod(
            s.period.start.astimezone(UTC), s.period.end.astimezone(UTC), s.period.time_zone)))
        self.assertEqual(result["count"], 3)

    def test_collection_limits_fail_without_partial_results(self):
        for transform in (lambda s: replace(s, pages_read=MAX_PAGES + 1),
                          lambda s: replace(s, raw_events_seen=MAX_EVENTS + 1)):
            self.reject_source(transform=transform, exception=AvailabilityLimitError)
        self.reject_source(events=tuple(event(str(i)) for i in range(MAX_EVENTS + 1)), exception=AvailabilityLimitError)

    def test_terminal_2000_events_are_merged_before_search_and_exact_limits_are_accepted(self):
        events = tuple(event(str(i), end="2030-06-19T15:00:00Z") for i in range(MAX_EVENTS))
        result, gateway = self.find(events, transform=lambda s: replace(s, pages_read=MAX_PAGES))
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["snapshot"]["complete"])
        self.assertEqual(len(gateway.calls), 2)

    def test_same_id_equivalent_utc_duplicates_are_deduplicated_but_conflicts_fail(self):
        first = event()
        equivalent = replace(first, start_utc=stamp("2030-06-19T09:00:00-03:00"),
                             end_utc=stamp("2030-06-19T09:30:00-03:00"))
        result, _ = self.find((first, equivalent))
        self.assertEqual(result["slots"][0]["start"][11:16], "09:30")
        for changed in (replace(first, end_utc=NOW + timedelta(hours=3)),
                        replace(first, transparency="transparent"), replace(first, status="cancelled")):
            with self.subTest(changed=repr(changed)):
                self.reject_source(events=(first, changed))

    def test_every_event_is_strictly_validated_including_transparent_cancelled_and_outside_range(self):
        original = event(transparency="transparent")
        changes = [{"event_id": "PRIVATE invalid"}, {"event_id": "private\ud800"},
                   {"start_utc": NOW.replace(tzinfo=None)}, {"end_utc": NOW},
                   {"all_day": 1}, {"status": "unknown"}, {"status": []},
                   {"transparency": None}, {"transparency": "PRIVATE"}]
        for change in changes:
            with self.subTest(change=repr(change)):
                self.reject_source(events=(replace(original, **change),))
        self.reject_source(events=(replace(original, status="cancelled", end_utc=NOW),))
        self.reject_source(events=(event(start="2029-01-01T00:00:00Z", end="2029-01-01T01:00:00Z", transparency="unknown"),))
        self.reject_source(events=({"status": "confirmed"},))

    def test_24_hour_end_uses_following_midnight_but_preserves_request(self):
        result, gateway = self.find(request={"window_start": "23:00", "window_end": "24:00", "duration_minutes": 60})
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["slots"][0]["end"], "2030-06-20T00:00:00-03:00")
        self.assertEqual(result["request"]["window_end"], "24:00")
        self.assertEqual(gateway.calls[1][1].end.isoformat(), "2030-06-20T00:00:00-03:00")

    def test_dst_gap_inside_window_skips_nonexistent_grid_without_losing_real_duration(self):
        binding = AvailabilityBinding(BINDING.calendar_id, "America/New_York")
        result, _ = self.find(binding=binding, now=stamp("2030-03-09T00:00:00Z"), request={
            "date_from": "2030-03-10", "date_to": "2030-03-10", "window_start": "01:45",
            "window_end": "03:30", "duration_minutes": 15})
        self.assertEqual([slot["start"] for slot in result["slots"]],
                         ["2030-03-10T01:45:00-05:00", "2030-03-10T03:00:00-04:00", "2030-03-10T03:15:00-04:00"])
        self.assertEqual(result["slots"][0]["end"], "2030-03-10T03:00:00-04:00")
        self.assertFalse(result["more_available"])

    def test_dst_fold_interior_keeps_both_offsets_in_utc_order_after_now(self):
        binding = AvailabilityBinding(BINDING.calendar_id, "America/New_York")
        result, _ = self.find(binding=binding, now=stamp("2030-11-03T05:30:00Z"), request={
            "date_from": "2030-11-03", "date_to": "2030-11-03", "window_start": "00:00",
            "window_end": "03:00", "duration_minutes": 15})
        self.assertEqual([slot["start"] for slot in result["slots"]],
                         ["2030-11-03T01:30:00-04:00", "2030-11-03T01:45:00-04:00", "2030-11-03T01:00:00-05:00"])
        starts = [stamp(slot["start"]).astimezone(UTC) for slot in result["slots"]]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(result["more_available"])

    def test_ambiguous_or_nonexistent_window_boundaries_are_input_errors_before_listing(self):
        cases = [("2030-03-10", "02:00", "04:00", ("window_start",)),
                 ("2030-03-10", "01:00", "02:30", ("window_end",)),
                 ("2030-11-03", "01:00", "03:00", ("window_start",)),
                 ("2030-11-03", "00:00", "01:30", ("window_end",))]
        for day, start, end, fields in cases:
            with self.subTest(day=day, start=start, end=end):
                gateway = FixtureGateway(binding=AvailabilityBinding(BINDING.calendar_id, "America/New_York"))
                with self.assertRaises(AvailabilityInputError) as caught:
                    AvailabilityService(gateway.factory, clock=lambda: NOW).find(**(REQUEST | {
                        "date_from": day, "date_to": day, "window_start": start, "window_end": end}))
                self.assertEqual(caught.exception.fields, fields)
                self.assertEqual(gateway.calls, [("binding",)])

    def test_midnight_gap_and_eliminated_civil_date_fail_instead_of_false_free(self):
        for zone, day in (("America/Sao_Paulo", "2018-11-04"), ("Pacific/Apia", "2011-12-30")):
            with self.subTest(zone=zone):
                gateway = FixtureGateway(binding=AvailabilityBinding(BINDING.calendar_id, zone))
                with self.assertRaises(AvailabilityInputError):
                    AvailabilityService(gateway.factory, clock=lambda: NOW).find(**(REQUEST | {
                        "date_from": day, "date_to": day, "window_start": "00:00", "window_end": "24:00"}))
                self.assertEqual(gateway.calls, [("binding",)])

    def test_real_duration_and_all_day_occupancy_on_23_hour_dst_day(self):
        binding = AvailabilityBinding(BINDING.calendar_id, "America/New_York")
        request = {"date_from": "2030-03-10", "date_to": "2030-03-10", "window_start": "00:00",
                   "window_end": "24:00", "duration_minutes": 1440}
        result, _ = self.find(binding=binding, now=stamp("2030-03-09T00:00:00Z"), request=request)
        self.assertEqual(result["count"], 0)
        full_day = event(start="2030-03-10T05:00:00Z", end="2030-03-11T04:00:00Z", all_day=True)
        result, _ = self.find((full_day,), binding=binding, now=stamp("2030-03-09T00:00:00Z"),
                              request=request | {"duration_minutes": 30})
        self.assertEqual(result["count"], 0)

    def test_datetime_edge_buffer_overflow_fails_with_safe_input_field(self):
        gateway = FixtureGateway(binding=AvailabilityBinding(BINDING.calendar_id, "UTC"))
        with self.assertRaises(AvailabilityInputError) as caught:
            AvailabilityService(gateway.factory, clock=lambda: NOW).find(**(REQUEST | {
                "date_from": "9999-12-31", "date_to": "9999-12-31", "window_start": "23:00",
                "window_end": "23:59", "buffer_minutes": 240}))
        self.assertEqual(caught.exception.fields, ("buffer_minutes",))
        self.assertEqual(gateway.calls, [("binding",)])

    def test_event_exclusion_is_not_an_accepted_capability(self):
        factory = Mock()
        with self.assertRaises(TypeError):
            AvailabilityService(factory, clock=lambda: NOW).find(**REQUEST, exclude_event_id="private-event-id")
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
