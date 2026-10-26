"""Preparação HTTP sem executar gravações, confirmar exclusões ou usar LLM."""

import asyncio
from io import StringIO
import json
import logging
import unittest
from unittest.mock import Mock

from app.google_calendar import CalendarSetupError
from app.main import create_app
from app.services.event_details import EventDetails
from app.services.interviews import CalendarOperationError
from app.settings import Settings
from test_agent_queries import ReadOnlyGateway
from test_api_queries import EVENT_FIELDS, QueryFactory, ZONE, event
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi


ENDPOINT = "/api/v1/requests/prepare"
PRIVATE_ERROR = "PRIVATE_PREPARATION_PROVIDER_112233"
EVENT_ID = "OpaqueMixedID"


def create_request(**changes):
    return {"action": "create", "title": "Entrevista fictícia",
            "start": "2030-06-17T10:00:00-03:00", "end": "2030-06-17T10:30:00-03:00", **changes}


def selected_request(action="delete", **changes):
    values = {"action": action, "event_id": EVENT_ID}
    if action == "reschedule":
        values.update(start="2030-06-18T10:00:00-03:00", end="2030-06-18T10:30:00-03:00")
    if action == "edit":
        values["location"] = "Sala de demonstração"
    values.update(changes)
    return values


class RequestPreparationApiTests(unittest.TestCase):
    def setUp(self):
        self.gateway = ReadOnlyGateway()
        self.factory = QueryFactory(self.gateway)
        self.db = InjectedDatabase()
        self.model_factory = Mock(side_effect=AssertionError("Preparação não pode construir um modelo."))
        self.reply_factory = Mock(side_effect=AssertionError("Preparação não pode enviar WhatsApp."))
        self.app = create_app(
            config=Settings(_env_file=None, api_auth_token=TOKEN, openai_api_key=None, app_env="test"),
            db=self.db, calendar_gateway_factory=self.factory,
            agent_preview_service_factory=self.model_factory,
            agent_query_service_factory=self.model_factory,
            sandbox_reply_service_factory=self.reply_factory,
        )
        self.logs = StringIO()
        logger = logging.getLogger()
        previous_level = logger.level
        handler = logging.StreamHandler(self.logs)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        self.addCleanup(logger.setLevel, previous_level)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(self.check_invariants)

    def check_invariants(self):
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(self.factory.opened, self.factory.closed)
        self.assertEqual(self.db.initializations, 0)
        self.model_factory.assert_not_called()
        self.reply_factory.assert_not_called()
        for private in (TOKEN, WRONG_TOKEN, PRIVATE_ERROR):
            self.assertNotIn(private, self.logs.getvalue())

    def invoke(self, payload=None, *, body=None, headers=None, query=b""):
        if body is None:
            body = json.dumps({"request": {} if payload is None else payload}, ensure_ascii=False).encode("utf-8")
        if headers is None:
            headers = [(b"authorization", ("Bearer " + TOKEN).encode("ascii")),
                       (b"content-type", b"application/json"), (b"x-request-id", b"prepare-001")]
        response = asyncio.run(invoke_asgi(self.app, ENDPOINT, method="POST", headers=headers, body=body, query=query))
        for private in (TOKEN, WRONG_TOKEN, PRIVATE_ERROR):
            self.assertNotIn(private, json.dumps(response, ensure_ascii=True))
        return response

    def assert_preparation(self, response, *, status, action):
        code, headers, payload = response
        self.assertEqual(code, 200)
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], "prepare-001")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(payload["mutation_state"], "not_applied")
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["status"], status)
        self.assertEqual(payload["action"], action)
        self.assertIsNone(payload["error"])
        self.assertEqual(set(payload["data"]), {"reason", "question", "missing_fields", "invalid_fields",
                                               "event", "candidates", "parameters"})
        return payload["data"]

    def test_empty_request_asks_for_action_without_opening_google(self):
        data = self.assert_preparation(self.invoke({}), status="needs_clarification", action=None)
        self.assertEqual(data["reason"], "missing_information")
        self.assertEqual(data["missing_fields"], ["action"])
        self.assertTrue(data["question"])
        self.assertIsNone(data["parameters"])
        self.assertEqual(self.factory.opened, 0)

    def test_complete_create_is_only_prepared_without_google_or_confirmation_token(self):
        data = self.assert_preparation(self.invoke(create_request(description=None)), status="ready", action="create")
        self.assertEqual(data["parameters"], {
            "title": "Entrevista fictícia", "description": "", "time_zone": ZONE,
            "start": "2030-06-17T10:00:00-03:00", "end": "2030-06-17T10:30:00-03:00",
        })
        self.assertIsNone(data["event"])
        self.assertNotIn("execute", data["parameters"])
        self.assertNotIn("confirmation_token", data["parameters"])
        self.assertEqual(self.factory.opened, 0)

    def test_ready_list_periods_are_prepared_locally_and_do_not_list_events(self):
        for period in ("day", "week", "month"):
            with self.subTest(period=period):
                data = self.assert_preparation(
                    self.invoke({"action": "list", "date": "2030-06-17", "period": period}),
                    status="ready", action="list",
                )
                self.assertEqual(data["parameters"], {"date": "2030-06-17", "period": period, "time_zone": ZONE})
                self.assertIsNone(data["event"])
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_missing_or_invalid_semantics_ask_or_correct_without_accessing_google(self):
        cases = (
            ({"action": "create"}, "needs_clarification", "create"),
            (create_request(start="invalid-date"), "needs_correction", "create"),
            ({"action": "list", "date": "2030-02-30"}, "needs_correction", "list"),
            ({"action": "list", "date": "2030-06-17", "period": "year"}, "needs_correction", "list"),
            ({"action": "delete"}, "needs_clarification", "delete"),
            (selected_request("edit", location=None), "needs_clarification", "edit"),
            (selected_request("reschedule", time_zone="invalid-private-zone"), "needs_correction", "reschedule"),
            (selected_request(search_title="Entrevista"), "needs_correction", "delete"),
            ({"action": "private-invalid-action"}, "needs_correction", None),
        )
        for index, (payload, status, action) in enumerate(cases):
            with self.subTest(case=index):
                data = self.assert_preparation(self.invoke(payload), status=status, action=action)
                self.assertTrue(data["question"])
                self.assertIsNone(data["parameters"])
        self.assertEqual(self.factory.opened, 0)

    def test_complete_targeted_actions_select_one_exact_event_and_never_execute(self):
        for action in ("reschedule", "edit", "delete"):
            with self.subTest(action=action):
                data = self.assert_preparation(self.invoke(selected_request(action)), status="ready", action=action)
                self.assertEqual(data["parameters"]["event_id"], EVENT_ID)
                self.assertEqual(data["parameters"]["time_zone"], ZONE)
                self.assertEqual(set(data["event"]), EVENT_FIELDS)
                self.assertEqual(data["event"]["event_id"], EVENT_ID)
                self.assertIsNone(data["question"])
                self.assertNotIn("confirmation_token", data["parameters"])
                self.assertNotIn("execute", data["parameters"])
                self.assertEqual(self.gateway.calls[-1], ("get", EVENT_ID, ZONE))
        self.assertEqual(self.factory.opened, 3)
        self.assertEqual([call[0] for call in self.gateway.calls], ["get", "get", "get"])

    def test_unique_title_and_date_are_revalidated_by_id_in_one_gateway_session(self):
        self.gateway.events = (event(),)
        data = self.assert_preparation(self.invoke({
            "action": "delete", "search_title": "entrevista", "date": "2030-06-17", "period": "week",
        }), status="ready", action="delete")
        self.assertEqual(data["parameters"]["event_id"], EVENT_ID)
        self.assertEqual([call[0] for call in self.gateway.calls], ["list", "get"])
        self.assertEqual(self.gateway.calls[0][1].start.isoformat(), "2030-06-17T00:00:00-03:00")
        self.assertEqual(self.gateway.calls[0][1].end.isoformat(), "2030-06-24T00:00:00-03:00")
        self.assertEqual(self.gateway.calls[1], ("get", EVENT_ID, ZONE))
        self.assertEqual(self.factory.opened, 1)

    def test_ambiguous_and_absent_matches_ask_instead_of_preparing_a_write(self):
        for events, reason in (((event(), event("OtherID")), "event_selection"), ((), "event_not_found")):
            with self.subTest(reason=reason):
                self.gateway.events = events
                self.gateway.calls.clear()
                data = self.assert_preparation(self.invoke({
                    "action": "delete", "search_title": "entrevista", "date": "2030-06-17",
                }), status="needs_clarification", action="delete")
                self.assertEqual(data["reason"], reason)
                self.assertIsNone(data["parameters"])
                self.assertTrue(data["question"])
                self.assertEqual([call[0] for call in self.gateway.calls], ["list"])
                self.assertEqual(len(data["candidates"]), len(events))
                for candidate in data["candidates"]:
                    self.assertEqual(set(candidate), EVENT_FIELDS)

    def test_exact_absent_event_does_not_fallback_to_title_or_produce_parameters(self):
        self.gateway.lookup = None
        data = self.assert_preparation(self.invoke(selected_request()), status="needs_clarification", action="delete")
        self.assertEqual(data["reason"], "event_not_found")
        self.assertIsNone(data["parameters"])
        self.assertEqual(self.gateway.calls, [("get", EVENT_ID, ZONE)])

    def test_edit_null_fields_are_omitted_and_empty_description_is_preserved(self):
        data = self.assert_preparation(self.invoke(selected_request(
            "edit", title=None, description="", location=None,
        )), status="ready", action="edit")
        self.assertEqual(data["parameters"], {"event_id": EVENT_ID, "time_zone": ZONE, "description": ""})

    def test_selected_event_description_and_location_are_not_exposed_in_public_preview(self):
        self.gateway.lookup = EventDetails(**event().__dict__, description=PRIVATE_ERROR, location=PRIVATE_ERROR)
        data = self.assert_preparation(self.invoke(selected_request()), status="ready", action="delete")
        self.assertEqual(set(data["event"]), EVENT_FIELDS)
        self.assertNotIn(PRIVATE_ERROR, json.dumps(data))

    def test_authentication_precedes_parsing_and_selection(self):
        auth = (b"authorization", ("Bearer " + TOKEN).encode("ascii"))
        wrong = (b"authorization", ("Bearer " + WRONG_TOKEN).encode("ascii"))
        for headers in ([], [wrong], [auth, auth], [auth, wrong]):
            with self.subTest(headers=len(headers)):
                response = self.invoke(body=b"invalid-json", headers=headers)
                self.assertEqual((response[0], response[2]["error"]["code"]), (401, "unauthorized"))
        self.assertEqual(self.factory.opened, 0)

    def test_strict_body_keys_types_duplicates_and_scalar_limits_are_rejected_locally(self):
        cases = (
            b"{}", b"[]", b"null", b"not-json", b'{"request":[]}', b'{"request":null}',
            b'{"request":{},"request":{}}', b'{"request":{"action":"list","action":"delete"}}',
            b'{"request":{},"message":"override"}', b'{"request":{"execute":true}}',
            b'{"request":{"confirmation_token":"fake"}}', b'{"request":{"action":4}}',
            b'{"request":{"title":[]}}', b'{"request":{"title":{"private":"x"}}}',
            b'{"request":{"title":true}}', b'{"request":{"title":"\\ud800"}}',
            b'{"request":{"title":"\xff"}}',
            json.dumps({"request": {"action": "create", "title": "x" * 4001}}).encode(),
        )
        for index, body in enumerate(cases):
            with self.subTest(case=index):
                response = self.invoke(body=body)
                self.assertEqual((response[0], response[2]["error"]["code"]), (422, "invalid_request"))
                self.assertFalse(response[2]["error"]["retryable"])
        self.assertEqual(self.factory.opened, 0)

    def test_query_content_type_and_raw_byte_limit_are_independent_of_request_semantics(self):
        auth = (b"authorization", ("Bearer " + TOKEN).encode("ascii"))
        json_header = (b"content-type", b"application/json")
        for headers in ([auth], [auth, (b"content-type", b"text/plain")], [auth, json_header, json_header]):
            with self.subTest(headers=len(headers)):
                self.assertEqual(self.invoke(headers=headers)[0], 422)
        for query in (b"execute=true", b"api_key=fake", b"force=true"):
            with self.subTest(query=query):
                self.assertEqual(self.invoke(query=query)[0], 422)
        base = b'{"request":{}}'
        at_limit = base + b" " * (24 * 1024 - len(base))
        self.assertEqual(self.invoke(body=at_limit)[0], 200)
        self.assertEqual(self.invoke(body=at_limit + b" ")[0], 422)
        self.assertEqual(self.factory.opened, 0)

    def test_provider_failures_are_unavailable_not_user_corrections_or_no_matches(self):
        for failure, status, code in (
            (CalendarSetupError(PRIVATE_ERROR), 503, "calendar_setup_unavailable"),
            (CalendarOperationError(PRIVATE_ERROR), 502, "operation_failed"),
            (ValueError(PRIVATE_ERROR), 502, "operation_failed"),
            (OSError(PRIVATE_ERROR), 502, "operation_failed"),
        ):
            with self.subTest(exception=type(failure).__name__):
                self.gateway.failure = failure
                response = self.invoke(selected_request())
                self.assertEqual((response[0], response[2]["error"]["code"]), (status, code))
                self.assertEqual(response[2]["status"], "failed")
                self.assertIsNone(response[2]["data"])
                self.assertFalse(response[2]["error"]["retryable"])

    def test_openapi_protects_preparation_without_exposing_tokens(self):
        schema = self.app.openapi()
        self.assertIn({"ApiKeyBearer": []}, schema["paths"][ENDPOINT]["post"]["security"])
        self.assertNotIn(TOKEN, json.dumps(schema))


if __name__ == "__main__":
    unittest.main()
