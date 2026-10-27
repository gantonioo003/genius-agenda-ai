"""Seleção por título sem data original, com consultas simuladas e sem escrita."""

from dataclasses import replace
from datetime import date
import unittest
from unittest.mock import Mock, call

from app.services.agenda import AgendaEvent, CalendarListing, day_period, month_period, week_period
from app.services.event_selection import EventLookup, EventSelectionGateway, EventSelectionService
from app.services.interviews import CalendarOperationError
from app.services.request_preparation import RequestPreparationService


def event(event_id="TargetID", title="Entrevista técnica", **changes):
    return replace(AgendaEvent(
        event_id, title, "2026-10-22T15:00:00-03:00", "2026-10-22T15:30:00-03:00",
        False, "America/Recife", "confirmed", "https://example.invalid/event",
    ), **changes)


class TitleOnlySelectionTests(unittest.TestCase):
    def setUp(self):
        self.event = event()
        self.gateway = Mock(spec_set=EventSelectionGateway)
        self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", (self.event,))
        self.gateway.list_events.return_value = CalendarListing("Genius Testes", (self.event,))
        self.gateway.get_event.return_value = EventLookup("Genius Testes", self.event)
        self.service = EventSelectionService(self.gateway)

    def test_title_alone_uses_global_search_and_revalidates_unique_event(self):
        title = "  ENTREVÍSTA   TÉCNICA  "

        result = self.service.select(title=title)

        self.assertEqual(result.status, "selected")
        self.assertEqual(result.event, self.event)
        self.assertEqual(result.candidates, ())
        self.assertEqual(self.gateway.method_calls, [
            call.search_events_by_title(title, time_zone="America/Recife"),
            call.get_event("TargetID", time_zone="America/Recife"),
        ])

    def test_same_title_on_distant_dates_requires_choice_before_get(self):
        other = event("OtherID", start="2032-06-10T15:00:00-03:00", end="2032-06-10T15:30:00-03:00")
        self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", (self.event, other))

        result = self.service.select(title="Entrevista técnica")

        self.assertEqual(result.status, "needs_clarification")
        self.assertIsNone(result.event)
        self.assertEqual(result.candidates, (self.event, other))
        self.gateway.list_events.assert_not_called()
        self.gateway.get_event.assert_not_called()

    def test_exact_and_partial_title_matches_remain_ambiguous(self):
        exact, partial = event("ExactID", "Entrevista"), event("PartialID", "Entrevista técnica")
        self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", (exact, partial))

        result = self.service.select(title="entrevista")

        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual(result.candidates, (exact, partial))
        self.gateway.get_event.assert_not_called()

    def test_repeated_same_event_id_does_not_create_false_ambiguity(self):
        self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", (self.event, self.event))

        result = self.service.select(title="Entrevista técnica")

        self.assertEqual(result.status, "selected")
        self.gateway.get_event.assert_called_once_with("TargetID", time_zone="America/Recife")

    def test_conflicting_snapshots_for_same_event_id_fail_closed(self):
        changed = replace(self.event, end="2026-10-22T16:00:00-03:00")
        self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", (self.event, changed))

        with self.assertRaises(CalendarOperationError):
            self.service.select(title="Entrevista técnica")

        self.gateway.get_event.assert_not_called()

    def test_explicit_period_uses_only_bounded_listing(self):
        period = week_period(date(2026, 10, 22), "America/Recife")

        result = self.service.select(title="Entrevista técnica", period=period)

        self.assertEqual(result.status, "selected")
        self.gateway.list_events.assert_called_once_with(period)
        self.gateway.search_events_by_title.assert_not_called()

    def test_empty_cancelled_and_unrelated_matches_do_not_select_an_event(self):
        for events in ((), (event(status="cancelled"),), (event(title="Outro compromisso"),)):
            with self.subTest(events=events):
                self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", events)

                result = self.service.select(title="Entrevista técnica")

                self.assertEqual(result.status, "not_found")
                self.assertIsNone(result.event)
                self.assertEqual(result.candidates, ())
                self.gateway.get_event.assert_not_called()
                self.gateway.list_events.assert_not_called()

    def test_global_match_must_still_be_current_and_available(self):
        changed = replace(self.event, start="2026-10-23T15:00:00-03:00", end="2026-10-23T15:30:00-03:00")
        self.gateway.get_event.return_value = EventLookup("Genius Testes", changed)

        result = self.service.select(title="Entrevista técnica")

        self.assertEqual(result.status, "needs_clarification")
        self.assertIsNone(result.event)
        self.assertEqual(result.candidates, (changed,))
        self.gateway.get_event.return_value = EventLookup("Genius Testes", None)
        self.assertEqual(self.service.select(title="Entrevista técnica").status, "not_found")

    def test_global_search_failure_never_becomes_not_found_or_selection(self):
        self.gateway.search_events_by_title.side_effect = CalendarOperationError("Busca incompleta.")

        with self.assertRaises(CalendarOperationError):
            self.service.select(title="Entrevista técnica")

        self.gateway.get_event.assert_not_called()
        self.gateway.list_events.assert_not_called()

    def test_id_selection_and_invalid_title_do_not_fall_back_to_search(self):
        self.assertEqual(self.service.select(event_id="TargetID").status, "selected")
        for title in ("", "  ", "\u0301", 123):
            with self.subTest(title=title):
                with self.assertRaises(ValueError):
                    self.service.select(title=title)
        self.gateway.search_events_by_title.assert_not_called()
        self.gateway.list_events.assert_not_called()


class TitleOnlyPreparationTests(unittest.TestCase):
    def setUp(self):
        self.event = event()
        self.gateway = Mock(spec_set=EventSelectionGateway)
        self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", (self.event,))
        self.gateway.list_events.return_value = CalendarListing("Genius Testes", (self.event,))
        self.gateway.get_event.return_value = EventLookup("Genius Testes", self.event)
        self.service = RequestPreparationService(self.gateway)

    def assert_no_reads(self):
        self.assertEqual(self.gateway.method_calls, [])

    def test_title_only_delete_edit_and_reschedule_prepare_exact_internal_id(self):
        cases = (
            ("delete", {}),
            ("edit", {"title": "Novo título", "description": "", "location": "Sala 2"}),
            ("reschedule", {"start": "2026-10-24T10:00:00-03:00", "end": "2026-10-24T10:30:00-03:00"}),
        )
        for action, changes in cases:
            with self.subTest(action=action):
                self.gateway.reset_mock()
                request = {"action": action, "search_title": "Entrevista técnica", **changes}

                result = self.service.prepare(request)

                self.assertEqual(result.status, "ready")
                self.assertEqual(result.event, self.event)
                self.assertEqual(result.parameters, {"time_zone": "America/Recife", "event_id": "TargetID", **changes})
                self.assertEqual(result.missing_fields, ())
                self.assertEqual(result.invalid_fields, ())
                self.gateway.search_events_by_title.assert_called_once_with("Entrevista técnica", time_zone="America/Recife")
                self.gateway.get_event.assert_called_once_with("TargetID", time_zone="America/Recife")
                self.gateway.list_events.assert_not_called()
                self.assertNotIn("event_id", request)
                self.assertNotIn("date", request)

    def test_missing_destination_and_changes_do_not_request_original_date(self):
        cases = (
            ({"action": "reschedule", "search_title": "Entrevista"}, ("start", "end")),
            ({"action": "reschedule", "search_title": "Entrevista", "start": "2026-10-24T10:00:00-03:00"}, ("end",)),
            ({"action": "edit", "search_title": "Entrevista"}, ("changes",)),
            ({"action": "delete"}, ("search_title",)),
        )
        for request, missing in cases:
            with self.subTest(request=request):
                result = self.service.prepare(request)

                self.assertEqual(result.status, "needs_clarification")
                self.assertEqual(result.missing_fields, missing)
                self.assertEqual(result.invalid_fields, ())
                self.assert_no_reads()

    def test_optional_date_and_period_constrain_selection_when_provided(self):
        for kind, builder in ((None, day_period), ("day", day_period), ("week", week_period), ("month", month_period)):
            with self.subTest(period=kind):
                self.gateway.reset_mock()
                request = {"action": "delete", "search_title": "Entrevista", "date": "2026-10-22"}
                if kind is not None:
                    request["period"] = kind

                result = self.service.prepare(request)

                self.assertEqual(result.status, "ready")
                self.gateway.list_events.assert_called_once_with(builder(date(2026, 10, 22), "America/Recife"))
                self.gateway.search_events_by_title.assert_not_called()
                self.gateway.get_event.assert_called_once()

    def test_explicit_period_without_date_requires_its_anchor_before_reads(self):
        for kind in ("day", "week", "month"):
            with self.subTest(period=kind):
                result = self.service.prepare({"action": "delete", "search_title": "Entrevista", "period": kind})

                self.assertEqual(result.status, "needs_clarification")
                self.assertEqual(result.missing_fields, ("date",))
                self.assertEqual(result.invalid_fields, ())
                self.assert_no_reads()

    def test_omitted_or_null_optional_date_does_not_invent_a_filter(self):
        result = self.service.prepare({"action": "delete", "search_title": "Entrevista", "date": None})

        self.assertEqual(result.status, "ready")
        self.gateway.search_events_by_title.assert_called_once_with("Entrevista", time_zone="America/Recife")
        self.gateway.list_events.assert_not_called()

    def test_supplied_invalid_dates_and_periods_are_not_silently_ignored(self):
        cases = (
            ({"date": "2026-02-30"}, (), ("date",)),
            ({"date": "20261022"}, (), ("date",)),
            ({"date": date(2026, 10, 22)}, (), ("date",)),
            ({"date": ""}, ("date",), ()),
            ({"date": "9999-12-31", "period": "month"}, (), ("date",)),
            ({"period": "year"}, ("date",), ("period",)),
        )
        for changes, missing, invalid in cases:
            with self.subTest(changes=changes):
                result = self.service.prepare({"action": "delete", "search_title": "Entrevista", **changes})

                self.assertEqual(result.status, "needs_correction" if invalid else "needs_clarification")
                self.assertEqual(result.missing_fields, missing)
                self.assertEqual(result.invalid_fields, invalid)
                self.assert_no_reads()

    def test_list_still_requires_date_and_does_not_read_to_prepare(self):
        for request in ({"action": "list"}, {"action": "list", "period": "week"}):
            with self.subTest(request=request):
                result = self.service.prepare(request)

                self.assertEqual(result.status, "needs_clarification")
                self.assertEqual(result.missing_fields, ("date",))
                self.assert_no_reads()

    def test_global_ambiguity_and_no_match_never_produce_ready_parameters(self):
        other = event("OtherID", start="2027-01-10T15:00:00-03:00", end="2027-01-10T15:30:00-03:00")
        for events, reason in (((), "event_not_found"), ((self.event, other), "event_selection")):
            with self.subTest(reason=reason):
                self.gateway.reset_mock()
                self.gateway.search_events_by_title.return_value = CalendarListing("Genius Testes", events)

                result = self.service.prepare({"action": "delete", "search_title": "Entrevista técnica"})

                self.assertEqual(result.status, "needs_clarification")
                self.assertEqual(result.reason, reason)
                self.assertEqual(result.candidates, events)
                self.assertIsNone(result.parameters)
                self.assertIsNone(result.event)
                self.gateway.get_event.assert_not_called()
                self.gateway.list_events.assert_not_called()

    def test_explicit_id_cannot_compete_with_title_or_optional_filters(self):
        for changes in ({"search_title": "Entrevista"}, {"date": "2026-10-22"}, {"period": "week"}):
            with self.subTest(changes=changes):
                result = self.service.prepare({"action": "delete", "event_id": "TargetID", **changes})

                self.assertEqual(result.status, "needs_correction")
                self.assertIn("event_id", result.invalid_fields)
                self.assertTrue(set(changes).issubset(result.invalid_fields))
                self.assert_no_reads()


if __name__ == "__main__":
    unittest.main()
