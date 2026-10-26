"""Contexto mínimo por chamada; ferramentas reais com agenda e modelo simulados."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest

from app.agent_query_contract import QUERY_TOOLS, QueryPlan
from app.conversation_store import ConversationError, QueryMemory
from app.llm import ModelError
from app.services.agenda_tools import AgendaReadTools
from app.services.agent_queries import AgentQueriesService
from app.services.interviews import CalendarOperationError
from test_api_queries import QueryFactory, QueryGateway, ZONE, event


NOW = datetime(2030, 6, 17, 2, 0, tzinfo=timezone.utc)
CONTEXT_LABEL = "Contexto mínimo validado (JSON): "
PRIVATE_ID = "PrivateOpaqueEventID"
PRIVATE_TITLE = "PRIVATE_CONTEXT_EVENT_TITLE"
PRIVATE_MESSAGE = "PRIVATE_CONTEXT_PREVIOUS_MESSAGE"


def listing_plan(*, date="2030-06-17", period="week", time_zone=ZONE):
    return QueryPlan("list_events", {"date": date, "period": period, "time_zone": time_zone},
                     "fake-context-model", 12, 4)


def context_plan(time_zone=ZONE):
    return QueryPlan("get_context_event", {"time_zone": time_zone}, "fake-context-model", 12, 4)


def memory(*, date="2030-06-17", period="week", selected_id=None, time_zone=ZONE):
    return QueryMemory(
        last_list={"date": date, "period": period, "time_zone": time_zone},
        selected_event_id=selected_id, selected_time_zone=time_zone if selected_id is not None else None,
    )


class FakeModel:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []
        self.error = None

    def plan_query(self, *, instructions, message):
        self.calls.append({"instructions": instructions, "message": message})
        if self.error is not None:
            raise self.error
        return self.plan


class ReadOnlyGateway(QueryGateway):
    def __init__(self):
        super().__init__()
        self.writes = []

    def _write(self, *args, **kwargs):
        self.writes.append((args, kwargs))
        raise AssertionError("Contexto de consulta não autoriza escritas.")

    create_event = _write
    create_interview = _write
    edit_event = _write
    reschedule_event = _write
    delete_event = _write


class AgentQueryContextTests(unittest.TestCase):
    def setUp(self):
        self.gateway = ReadOnlyGateway()
        self.factory = QueryFactory(self.gateway)
        self.model = FakeModel(listing_plan())
        self.service = AgentQueriesService(self.model, AgendaReadTools(self.factory), clock=lambda: NOW)
        self.addCleanup(self.check_read_only)

    def check_read_only(self):
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(self.factory.opened, self.factory.closed)

    def sent_context(self, index=-1):
        instructions = self.model.calls[index]["instructions"]
        line = instructions.split(CONTEXT_LABEL, 1)[1].splitlines()[0]
        return json.loads(line)

    def test_next_week_continues_only_the_previous_confirmed_period(self):
        self.gateway.events = (event(PRIVATE_ID, title=PRIVATE_TITLE),)
        first, saved = self.service.query_with_context(PRIVATE_MESSAGE, QueryMemory())
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(saved, memory(selected_id=PRIVATE_ID))
        self.model.plan = listing_plan(date="2030-06-24")
        second, updated = self.service.query_with_context("E na semana seguinte?", saved)
        self.assertEqual(second.data["period"]["start"], "2030-06-24T00:00:00-03:00")
        self.assertEqual(updated.last_list, {"date": "2030-06-24", "period": "week", "time_zone": ZONE})
        self.assertEqual(self.sent_context(), {"last_list": saved.last_list,
                         "has_selected_event": True, "selected_time_zone": ZONE})
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(self.factory.opened, 2)
        self.assertNotIn(PRIVATE_MESSAGE, self.model.calls[1]["instructions"])
        self.assertNotIn(PRIVATE_ID, json.dumps(self.model.calls))
        self.assertNotIn(PRIVATE_TITLE, json.dumps(self.model.calls))

    def test_next_month_and_explicit_zone_can_continue_without_sharing_events(self):
        previous = memory(date="2030-06-30", period="month", selected_id=PRIVATE_ID)
        self.model.plan = listing_plan(date="2030-07-30", period="month", time_zone="UTC")
        result, updated = self.service.query_with_context("E no mês seguinte, em UTC?", previous)
        self.assertEqual(result.data["period"], {"start": "2030-07-01T00:00:00+00:00",
                         "end": "2030-08-01T00:00:00+00:00", "time_zone": "UTC"})
        self.assertEqual(updated.last_list["date"], "2030-07-30")
        self.assertEqual(updated.last_list["time_zone"], "UTC")
        self.assertEqual(self.sent_context()["last_list"], previous.last_list)
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.factory.opened, 1)

    def test_server_reference_is_fresh_even_when_the_context_is_old(self):
        first, saved = self.service.query_with_context("Agenda da semana.", QueryMemory())
        self.assertEqual(first.status, "succeeded")
        self.service._clock = lambda: datetime(2031, 1, 2, 1, 0, tzinfo=timezone.utc)
        self.service.query_with_context("E amanhã?", saved)
        prompt = self.model.calls[-1]["instructions"]
        self.assertIn("2031-01-02T01:00:00+00:00", prompt)
        self.assertIn("Data civil de referência: 2031-01-01", prompt)
        self.assertIn("2030-06-17", prompt)

    def test_selected_event_is_reread_and_its_id_stays_local(self):
        previous = memory(selected_id=PRIVATE_ID)
        self.gateway.lookup = event(PRIVATE_ID, title="Título alterado no calendário")
        self.model.plan = context_plan("UTC")
        result, updated = self.service.query_with_context("Me mostre esse evento em UTC.", previous)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data["event"]["title"], "Título alterado no calendário")
        self.assertEqual(self.gateway.calls, [("get", PRIVATE_ID, "UTC")])
        self.assertEqual(updated.selected_event_id, PRIVATE_ID)
        self.assertEqual(updated.selected_time_zone, "UTC")
        self.assertEqual(updated.last_list, previous.last_list)
        self.assertEqual(len(self.model.calls), 1)
        self.assertNotIn(PRIVATE_ID, json.dumps(self.model.calls))
        self.assertNotIn("Título alterado", json.dumps(self.model.calls))

    def test_one_event_selects_it_while_empty_or_multiple_results_clear_the_selection(self):
        previous = memory(selected_id="PreviousPrivateID")
        for count in (0, 1, 3):
            with self.subTest(event_count=count):
                self.gateway.events = tuple(event(f"Event{index}") for index in range(count))
                result, updated = self.service.query_with_context("Agenda da semana.", previous)
                self.assertEqual(result.status, "succeeded")
                self.assertEqual(updated.selected_event_id, "Event0" if count == 1 else None)
                self.assertEqual(updated.selected_time_zone, ZONE if count == 1 else None)
                self.assertEqual(updated.last_list, previous.last_list)

    def test_ambiguous_list_followup_asks_instead_of_picking_an_event(self):
        self.gateway.events = tuple(event(f"Event{index}") for index in range(3))
        _, saved = self.service.query_with_context("Consulte minha semana.", QueryMemory())
        self.model.plan = context_plan()
        result, updated = self.service.query_with_context("E esse evento?", saved)
        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual(result.data["reason"], "missing_information")
        self.assertFalse(result.data["execution_allowed"])
        self.assertEqual(updated, saved)
        self.assertEqual(self.factory.opened, 1)
        self.assertEqual([call[0] for call in self.gateway.calls], ["list"])
        self.assertFalse(self.sent_context()["has_selected_event"])

    def test_missing_selection_context_plan_asks_without_google(self):
        self.model.plan = context_plan()
        for previous in (QueryMemory(), memory()):
            with self.subTest(previous_list=previous.last_list is not None):
                result, updated = self.service.query_with_context("Esse evento.", previous)
                self.assertEqual(result.status, "needs_clarification")
                self.assertEqual(result.data["reason"], "missing_information")
                self.assertEqual(updated, previous)
        self.assertEqual(self.factory.opened, 0)

    def test_stateless_calls_do_not_reuse_selection_from_another_call(self):
        self.service.query("Consulte minha semana.")
        self.model.plan = context_plan()
        result = self.service.query("Qual é esse evento?")
        self.assertEqual(result.status, "needs_clarification")
        self.assertEqual(result.data["reason"], "missing_information")
        self.assertEqual(self.factory.opened, 1)
        self.assertNotIn(CONTEXT_LABEL, self.model.calls[-1]["instructions"])

    def test_unrelated_memories_are_isolated_on_the_same_service(self):
        first_memory = memory(date="2030-06-17", selected_id="FirstConversationID")
        second_memory = memory(date="2030-08-05", period="day", selected_id="SecondConversationID")
        self.model.plan = context_plan()
        for original in (first_memory, second_memory, first_memory):
            self.gateway.lookup = event(original.selected_event_id)
            _, updated = self.service.query_with_context("Mostre esse evento.", original)
            self.assertEqual(updated, original)
            self.assertEqual(self.sent_context()["last_list"], original.last_list)
        self.assertEqual([call[1] for call in self.gateway.calls],
                         ["FirstConversationID", "SecondConversationID", "FirstConversationID"])
        transcript = json.dumps(self.model.calls)
        self.assertNotIn("FirstConversationID", transcript)
        self.assertNotIn("SecondConversationID", transcript)

    def test_explicit_id_still_must_occur_literally_in_current_message(self):
        previous = memory(selected_id=PRIVATE_ID)
        self.model.plan = QueryPlan("get_event", {"event_id": PRIVATE_ID, "time_zone": ZONE},
                                    "fake-context-model")
        with self.assertRaises(ModelError) as failure:
            self.service.query_with_context("Consulte esse evento.", previous)
        self.assertEqual(failure.exception.code, "llm_invalid_response")
        self.assertEqual(self.factory.opened, 0)
        self.gateway.lookup = event(PRIVATE_ID)
        result, updated = self.service.query_with_context("Consulte " + PRIVATE_ID, QueryMemory())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(updated.selected_event_id, PRIVATE_ID)
        self.assertIsNone(updated.last_list)

    def test_disappeared_event_clears_selection_without_losing_last_period(self):
        self.model.plan = context_plan()
        self.gateway.lookup = None
        previous = memory(selected_id=PRIVATE_ID)
        result, updated = self.service.query_with_context("Mostre esse evento.", previous)
        self.assertEqual((result.status, result.http_status), ("not_found", 404))
        self.assertIsNone(updated.selected_event_id)
        self.assertIsNone(updated.selected_time_zone)
        self.assertEqual(updated.last_list, previous.last_list)
        second, unchanged = self.service.query_with_context("E agora esse evento?", updated)
        self.assertEqual(second.status, "needs_clarification")
        self.assertEqual(unchanged, updated)
        self.assertEqual(self.factory.opened, 1)

    def test_asks_and_calendar_failures_preserve_the_confirmed_context(self):
        previous = memory(selected_id=PRIVATE_ID)
        for reason in ("missing_information", "unsupported_request"):
            self.model.plan = QueryPlan("ask_query", {"reason": reason}, "fake-context-model")
            result, updated = self.service.query_with_context("Qual deles?", previous)
            self.assertEqual(result.status, "needs_clarification")
            self.assertEqual(updated, previous)
        self.gateway.failure = CalendarOperationError("PRIVATE_PROVIDER_FAILURE")
        for plan in (context_plan(), listing_plan(date="2030-07-01", period="month")):
            self.model.plan = plan
            result, updated = self.service.query_with_context("Consulte a agenda.", previous)
            self.assertEqual((result.status, result.error_code), ("failed", "operation_failed"))
            self.assertEqual(updated, previous)
            self.assertNotIn("PRIVATE_PROVIDER_FAILURE", repr(result))

    def test_reread_does_not_accept_changed_identity_or_cancelled_event(self):
        self.model.plan = context_plan()
        previous = memory(selected_id=PRIVATE_ID)
        for lookup in (event("DifferentID"), event(PRIVATE_ID, status="cancelled")):
            self.gateway.lookup = lookup
            result, updated = self.service.query_with_context("Esse evento.", previous)
            self.assertEqual(result.status, "failed")
            self.assertEqual(updated, previous)
        self.assertEqual(len(self.model.calls), 2)
        self.assertEqual(self.factory.opened, 2)

    def test_invalid_context_fails_before_model_or_google(self):
        invalid = (
            None, {}, [], "conversation-private-id",
            QueryMemory(last_list={}),
            QueryMemory(last_list={"date": "2030-02-30", "period": "day", "time_zone": ZONE}),
            QueryMemory(last_list={"date": "2030-06-17", "period": "year", "time_zone": ZONE}),
            QueryMemory(last_list={"date": "2030-06-17", "period": "day", "time_zone": ZONE,
                                   "message": PRIVATE_MESSAGE}),
            QueryMemory(selected_event_id=PRIVATE_ID),
            QueryMemory(selected_time_zone=ZONE),
            QueryMemory(selected_event_id="Bad Event", selected_time_zone=ZONE),
            QueryMemory(selected_event_id=PRIVATE_ID, selected_time_zone="Invalid/Zone"),
        )
        for index, previous in enumerate(invalid):
            with self.subTest(case=index):
                with self.assertRaises(ConversationError):
                    self.service.query_with_context("Consulte a agenda.", previous)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.factory.opened, 0)

    def test_context_arguments_are_validated_before_selection_resolution(self):
        for args in ({}, {"time_zone": ZONE, "event_id": PRIVATE_ID},
                     {"time_zone": "Invalid/Zone"}, {"time_zone": None},
                     {"time_zone": True}, {"time_zone": ""}, {"time_zone": "UTC\x00"}):
            for previous in (QueryMemory(), memory(selected_id=PRIVATE_ID)):
                with self.subTest(args=args, selected=previous.selected_event_id is not None):
                    self.model.plan = QueryPlan("get_context_event", args, "fake-context-model")
                    with self.assertRaises(ModelError) as failure:
                        self.service.query_with_context("Esse evento.", previous)
                    self.assertEqual(failure.exception.code, "llm_invalid_response")
        self.assertEqual(self.factory.opened, 0)

    def test_context_plan_metadata_is_still_checked(self):
        invalid = (replace(context_plan(), input_tokens=True),
                   replace(context_plan(), output_tokens=-1),
                   replace(context_plan(), model="https://private.invalid"))
        for plan in invalid:
            self.model.plan = plan
            with self.assertRaises(ModelError):
                self.service.query_with_context("Esse evento.", memory(selected_id=PRIVATE_ID))
        self.assertEqual(self.factory.opened, 0)

    def test_model_failure_does_not_change_input_or_retry(self):
        previous = memory(selected_id=PRIVATE_ID)
        self.model.error = ModelError("llm_timeout")
        with self.assertRaises(ModelError) as failure:
            self.service.query_with_context("Esse evento.", previous)
        self.assertEqual(failure.exception.code, "llm_timeout")
        self.assertEqual(previous, memory(selected_id=PRIVATE_ID))
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.factory.opened, 0)

    def test_validated_context_and_returned_memory_do_not_alias_input_dictionaries(self):
        previous = memory(selected_id=PRIVATE_ID)
        self.model.plan = QueryPlan("ask_query", {"reason": "missing_information"}, "fake-context-model")
        _, updated = self.service.query_with_context("Qual deles?", previous)
        updated.last_list["date"] = "2030-01-01"
        self.assertEqual(previous.last_list["date"], "2030-06-17")
        self.assertEqual(self.sent_context()["last_list"]["date"], "2030-06-17")

    def test_new_tool_is_strict_and_does_not_accept_an_event_id(self):
        tool = next(item for item in QUERY_TOOLS if item["name"] == "get_context_event")
        self.assertIs(tool["strict"], True)
        self.assertIs(tool["parameters"]["additionalProperties"], False)
        self.assertEqual(tool["parameters"]["required"], ["time_zone"])
        self.assertEqual(set(tool["parameters"]["properties"]), {"time_zone"})


if __name__ == "__main__":
    unittest.main()
