"""Busca global por título com Google falso, sem credenciais, rede ou escrita."""

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
import httplib2

from app import google_calendar
from app.services.interviews import CalendarOperationError


CALENDAR = {
    "id": "agenda-teste@example.invalid", "summary": "Genius Testes",
    "timeZone": "America/Recife", "accessRole": "owner", "primary": False,
}
PRIVATE = "PRIVATE_PROVIDER_DIAGNOSTIC"


def event(event_id="one", title="Reunião técnica", day="2030-06-17", **changes):
    item = {
        "id": event_id, "summary": title, "status": "confirmed",
        "start": {"dateTime": day + "T15:00:00-03:00"},
        "end": {"dateTime": day + "T16:00:00-03:00"},
        "htmlLink": "https://example.invalid/event/" + event_id,
    }
    item.update(changes)
    return item


def instance(event_id, master_id="series", title="Reunião técnica", day="2030-06-17"):
    item = event(event_id, title, day)
    item.update(recurringEventId=master_id, originalStartTime=deepcopy(item["start"]))
    return item


def google_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": PRIVATE}),
        ('{"error":{"message":"' + PRIVATE + '"}}').encode(),
    )


class GlobalTitleGoogleTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(name="google_without_network")
        self.events = self.client.events.return_value
        self.events.list.return_value.execute.return_value = {"items": []}
        self.events.instances.return_value.execute.return_value = {"items": []}
        self.gateway = google_calendar.GoogleCalendarGateway(self.client)
        for target, value in (
            ("resolve_calendar", Mock(return_value=deepcopy(CALENDAR))),
            ("get_credentials", Mock(side_effect=AssertionError("Sem credenciais reais."))),
            ("database", Mock(name="no_database_io")),
        ):
            guard = patch.object(google_calendar, target, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.addCleanup(self.assert_read_only)

    def assert_read_only(self):
        for method in ("insert", "patch", "update", "delete"):
            getattr(self.events, method).assert_not_called()
        google_calendar.get_credentials.assert_not_called()
        google_calendar.database.assert_not_called()

    def assert_no_hidden_window_or_fulltext(self):
        for method in (self.events.list, self.events.instances):
            for call in method.call_args_list:
                arguments = call.kwargs
                self.assertEqual(arguments["calendarId"], CALENDAR["id"])
                self.assertFalse(arguments["showDeleted"])
                for name in ("q", "timeMin", "timeMax", "orderBy", "originalStart"):
                    self.assertNotIn(name, arguments)
                for field in ("recurrence", "recurringEventId", "originalStartTime"):
                    self.assertIn(field, arguments["fields"])

    def test_complete_empty_result_uses_bound_calendar_and_read_retries(self):
        result = self.gateway.search_events_by_title("reuniao")
        self.assertEqual(result.calendar_name, "Genius Testes")
        self.assertEqual(result.events, ())
        google_calendar.resolve_calendar.assert_called_once_with(self.client)
        self.events.list.return_value.execute.assert_called_once_with(num_retries=2)
        self.assertFalse(self.events.list.call_args.kwargs["singleEvents"])
        self.assertTrue(self.events.list.call_args.kwargs["showHiddenInvitations"])
        self.assert_no_hidden_window_or_fulltext()

    def test_empty_first_page_still_reads_next_page_and_normalizes_substring(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [], "nextPageToken": "next"},
            {"items": [event(title="Pré-reunião TÉCNICA da equipe")]},
        ]
        result = self.gateway.search_events_by_title("REUNIAO  TECNICA")
        self.assertEqual([item.event_id for item in result.events], ["one"])
        self.assertEqual(self.events.list.call_count, 2)
        self.assertEqual(self.events.list.call_args_list[1].kwargs["pageToken"], "next")
        self.assert_no_hidden_window_or_fulltext()

    def test_second_page_collision_prevents_false_uniqueness_even_far_in_time(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [event("future", day="2090-06-17")], "nextPageToken": "older"},
            {"items": [event("past", day="1990-06-17")]},
        ]
        result = self.gateway.search_events_by_title("reunião")
        self.assertEqual([item.event_id for item in result.events], ["past", "future"])
        self.assert_no_hidden_window_or_fulltext()

    def test_title_only_match_ignores_description_and_other_fulltext_fields(self):
        self.events.list.return_value.execute.return_value = {"items": [
            event("description", "Outro assunto", description="reunião técnica"),
            event("substring", "Pré-reunião técnica"),
        ]}
        result = self.gateway.search_events_by_title("reuniao")
        self.assertEqual([item.event_id for item in result.events], ["substring"])
        self.assert_no_hidden_window_or_fulltext()

    def test_cancelled_events_are_not_candidates(self):
        self.events.list.return_value.execute.return_value = {"items": [
            {"id": "cancelled", "status": "cancelled"}, event(),
        ]}
        result = self.gateway.search_events_by_title("reunião")
        self.assertEqual([item.event_id for item in result.events], ["one"])

    def test_matching_recurring_master_is_expanded_without_becoming_appointment(self):
        first = instance("series_20300617")
        second = instance("series_20300624", day="2030-06-24")
        self.events.list.return_value.execute.return_value = {"items": [
            event("series", recurrence=["RRULE:FREQ=WEEKLY;COUNT=2"]), first,
        ]}
        self.events.instances.return_value.execute.side_effect = [
            {"items": [deepcopy(first)], "nextPageToken": "instances-next"},
            {"items": [second]},
        ]
        result = self.gateway.search_events_by_title("reunião")
        self.assertEqual([item.event_id for item in result.events], [first["id"], second["id"]])
        self.assertEqual(self.events.instances.call_count, 2)
        self.assertEqual(self.events.instances.call_args.kwargs["eventId"], "series")
        self.assertEqual(self.events.instances.call_args.kwargs["pageToken"], "instances-next")
        self.assert_no_hidden_window_or_fulltext()

    def test_empty_instance_page_and_matching_renamed_exception_are_both_handled(self):
        renamed = instance("renamed", master_id="other", title="Reunião técnica especial")
        self.events.list.return_value.execute.return_value = {"items": [
            event("series", recurrence=["RRULE:FREQ=WEEKLY;COUNT=1"]),
            event("other", "Assunto diferente", recurrence=["RRULE:FREQ=WEEKLY"]),
            renamed,
        ]}
        self.events.instances.return_value.execute.side_effect = [
            {"items": [], "nextPageToken": "next"},
            {"items": [instance("actual"), instance("changed", title="Assunto diferente")]},
        ]
        result = self.gateway.search_events_by_title("reuniao")
        self.assertEqual({item.event_id for item in result.events}, {"renamed", "actual"})
        self.assertEqual({call.kwargs["eventId"] for call in self.events.instances.call_args_list}, {"series"})

    def test_matching_series_with_no_active_matching_instance_does_not_match_master(self):
        self.events.list.return_value.execute.return_value = {"items": [
            event("series", recurrence=["RRULE:FREQ=WEEKLY;COUNT=1"]),
        ]}
        self.events.instances.return_value.execute.return_value = {"items": [
            instance("renamed", title="Outro assunto"),
            {"id": "cancelled", "status": "cancelled", "recurringEventId": "series"},
        ]}
        self.assertEqual(self.gateway.search_events_by_title("reuniao").events, ())

    def test_identical_duplicates_do_not_create_ambiguity(self):
        item = event()
        self.events.list.return_value.execute.side_effect = [
            {"items": [item], "nextPageToken": "next"}, {"items": [deepcopy(item)]},
        ]
        self.assertEqual(len(self.gateway.search_events_by_title("reuniao").events), 1)

    def test_conflicting_duplicates_fail_even_when_new_title_no_longer_matches(self):
        for change in ({"summary": "Outro assunto"}, {"status": "cancelled"}):
            with self.subTest(change=change):
                self.events.list.return_value.execute.side_effect = [
                    {"items": [event()], "nextPageToken": "next"},
                    {"items": [event(**change)]},
                ]
                with self.assertRaises(CalendarOperationError):
                    self.gateway.search_events_by_title("reuniao")

    def test_conflicting_list_and_instance_duplicate_fails_closed(self):
        original = instance("occurrence")
        changed = deepcopy(original)
        changed["summary"] = "Outro assunto"
        self.events.list.return_value.execute.return_value = {"items": [
            event("series", recurrence=["RRULE:FREQ=WEEKLY"]), original,
        ]}
        self.events.instances.return_value.execute.return_value = {"items": [changed]}
        with self.assertRaises(CalendarOperationError):
            self.gateway.search_events_by_title("reuniao")

    def test_twenty_one_distinct_matches_prove_ambiguity_without_more_requests(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [event(str(index)) for index in range(21)], "nextPageToken": "unneeded"},
            AssertionError("Não é preciso continuar para comprovar muitas opções."),
        ]
        self.assertEqual(len(self.gateway.search_events_by_title("reuniao").events), 21)
        self.assertEqual(self.events.list.call_count, 1)

    def test_unbounded_recurrence_stops_once_twenty_one_instances_match(self):
        self.events.list.return_value.execute.return_value = {"items": [
            event("series", recurrence=["RRULE:FREQ=DAILY"]),
        ]}
        self.events.instances.return_value.execute.side_effect = [
            {"items": [instance(str(index)) for index in range(21)], "nextPageToken": "unneeded"},
            AssertionError("Uma recorrência infinita já está ambígua."),
        ]
        result = self.gateway.search_events_by_title("reuniao")
        self.assertEqual(len(result.events), 21)
        self.assertEqual(self.events.instances.call_count, 1)

    def test_transport_or_http_failure_after_one_match_cannot_return_unique_result(self):
        for failure in (TransportError(PRIVATE), OSError(PRIVATE), httplib2.HttpLib2Error(PRIVATE),
                        google_error(403), google_error(500)):
            with self.subTest(failure=type(failure).__name__):
                self.events.list.return_value.execute.side_effect = [
                    {"items": [event()], "nextPageToken": "next"}, failure,
                ]
                with self.assertRaises(CalendarOperationError) as caught:
                    self.gateway.search_events_by_title("reuniao")
                self.assertNotIn(PRIVATE, str(caught.exception))

    def test_failed_instance_expansion_cannot_return_an_unrelated_unique_match(self):
        self.events.list.return_value.execute.return_value = {"items": [
            event("single"), event("series", recurrence=["RRULE:FREQ=WEEKLY"]),
        ]}
        self.events.instances.return_value.execute.side_effect = TransportError(PRIVATE)
        with self.assertRaises(CalendarOperationError) as caught:
            self.gateway.search_events_by_title("reuniao")
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_bad_pages_items_tokens_and_recurrence_fail_closed(self):
        for page in (
            None, [], {"items": None}, {"items": {}}, {"items": [None]},
            {"items": [event(status="unknown")]}, {"items": [event(id="bad id")]},
            {"items": [event()], "nextPageToken": []},
            {"items": [event()], "nextPageToken": "   "},
            {"items": [event(recurrence="RRULE:FREQ=WEEKLY")]},
            {"items": [event(originalStartTime="not-a-time-object")]},
        ):
            with self.subTest(page=page):
                self.events.list.return_value.execute.return_value = page
                with self.assertRaises(CalendarOperationError):
                    self.gateway.search_events_by_title("reuniao")

    def test_repeated_tokens_never_become_unique_or_empty_success(self):
        for items in ([], [event()]):
            with self.subTest(items=items):
                self.events.list.return_value.execute.side_effect = [
                    {"items": items, "nextPageToken": "repeat"},
                    {"items": [], "nextPageToken": "repeat"},
                ]
                with self.assertRaises(CalendarOperationError):
                    self.gateway.search_events_by_title("reuniao")

    def test_instance_token_repetition_and_wrong_parent_fail_closed(self):
        self.events.list.return_value.execute.return_value = {"items": [
            event("series", recurrence=["RRULE:FREQ=WEEKLY"]),
        ]}
        self.events.instances.return_value.execute.side_effect = [
            {"items": [instance("one")], "nextPageToken": "repeat"},
            {"items": [], "nextPageToken": "repeat"},
        ]
        with self.assertRaises(CalendarOperationError):
            self.gateway.search_events_by_title("reuniao")
        self.events.instances.return_value.execute.side_effect = None
        self.events.instances.return_value.execute.return_value = {"items": [
            instance("wrong", master_id="other"),
        ]}
        with self.assertRaises(CalendarOperationError):
            self.gateway.search_events_by_title("reuniao")

    def test_page_budget_failure_preserves_unknown_instead_of_false_uniqueness(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [event()], "nextPageToken": "two"},
            {"items": [], "nextPageToken": "three"},
        ]
        with patch.object(self.gateway, "TITLE_SEARCH_MAX_PAGES", 2):
            with self.assertRaises(CalendarOperationError):
                self.gateway.search_events_by_title("reuniao")
        self.assertEqual(self.events.list.call_count, 2)

    def test_invalid_title_and_zone_are_rejected_before_google_io(self):
        for title, zone in ((None, "America/Recife"), ("", "America/Recife"),
                            ("  \t", "America/Recife"), ("reuniao", "Invalid/Zone")):
            with self.subTest(title=title, zone=zone):
                with self.assertRaises(ValueError):
                    self.gateway.search_events_by_title(title, time_zone=zone)
        google_calendar.resolve_calendar.assert_not_called()
        self.client.events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
