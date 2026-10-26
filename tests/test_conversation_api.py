"""Memória por conversa atravessando HTTP ASGI, sem rede ou credenciais reais."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, get_ident
import unittest
from unittest.mock import Mock
from uuid import UUID, uuid1, uuid4

from app.api_conversations import conversation_owner_key
from app.conversation_store import ConversationError, ConversationStore, QueryMemory
from app.llm import ModelError
from app.main import create_app
from app.services.agenda_tools import QueryResult
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN


BASE = "/api/v1/agent/conversations"
QUERY = "/api/v1/agent/query"
PRIVATE_ERROR = "PRIVATE_CONVERSATION_PROVIDER_ERROR_001122"
ZONE = "America/Recife"
LIST_MEMORY = QueryMemory(last_list={"date": "2030-06-17", "period": "day", "time_zone": ZONE})


def result():
    return QueryResult("succeeded", "list", "Consulta concluída.", {
        "calendar_name": "Agenda de teste",
        "period": {"start": "2030-06-17T00:00:00-03:00",
                   "end": "2030-06-18T00:00:00-03:00", "time_zone": ZONE},
        "events": [], "execution_allowed": False,
    })


class QueryService:
    def __init__(self):
        self.context_calls = []
        self.stateless_calls = []
        self.returned_result = result()
        self.next_memory = LIST_MEMORY
        self.error = None
        self.barrier = None

    def query(self, message):
        self.stateless_calls.append(message)
        return self.returned_result

    def query_with_context(self, message, memory):
        self.context_calls.append((message, memory))
        if self.barrier is not None:
            self.barrier.wait(timeout=3)
        if self.error is not None:
            raise self.error
        return self.returned_result, self.next_memory


async def invoke(app, path, *, method="POST", headers=(), body=b"", query=b""):
    messages = []
    delivered = False
    finished = asyncio.Event()
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "scheme": "http", "method": method,
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query, "headers": list(headers),
        "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
    }

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            finished.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    starts = [item for item in messages if item["type"] == "http.response.start"]
    if len(starts) != 1:
        raise AssertionError("A resposta deve possuir um único início.")
    headers = {key.decode("latin-1"): value.decode("latin-1")
               for key, value in starts[0].get("headers", [])}
    raw = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return starts[0]["status"], headers, json.loads(raw) if raw else None, raw


class ConversationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="genius-conversation-api-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "conversations.sqlite3"
        self.service = QueryService()
        self.service_factory = Mock(return_value=self.service)
        self.calendar_factory = Mock(side_effect=AssertionError("Sem acesso ao Google neste teste."))
        self.store = ConversationStore(self.path)
        self.store_factory = Mock(return_value=self.store)
        self.db = InjectedDatabase()
        self.app = self.application()
        self.addCleanup(self.assert_no_google)

    def assert_no_google(self):
        self.calendar_factory.assert_not_called()
        self.assertEqual(self.db.initializations, 0)

    def application(self, *, token=TOKEN, store_factory="injected", **changes):
        values = {"_env_file": None, "api_auth_token": token, "app_env": "test",
                  "openai_api_key": None, "conversation_database_path": self.path}
        values.update(changes)
        return create_app(
            config=Settings(**values), db=self.db,
            calendar_gateway_factory=self.calendar_factory,
            agent_query_service_factory=self.service_factory,
            conversation_store_factory=self.store_factory if store_factory == "injected" else store_factory,
        )

    @staticmethod
    def headers(token=TOKEN):
        return [(b"authorization", ("Bearer " + token).encode("ascii")),
                (b"content-type", b"application/json"), (b"x-request-id", b"conversation-test")]

    def request(self, path=BASE, *, app=None, headers=None, **kwargs):
        response = asyncio.run(invoke(app or self.app, path,
            headers=self.headers() if headers is None else headers, **kwargs))
        encoded = json.dumps(response[:3], ensure_ascii=True)
        for private in (TOKEN, WRONG_TOKEN, PRIVATE_ERROR, str(self.path)):
            self.assertNotIn(private, encoded)
        self.assertEqual(response[1]["cache-control"], "no-store")
        self.assertIn("x-request-id", response[1])
        return response

    def create(self, **kwargs):
        response = self.request(body=b"{}", **kwargs)
        self.assertEqual(response[0], 201)
        return response[2]["data"]["conversation_id"]

    def query(self, conversation_id=None, *, message="Consulte a agenda.", **kwargs):
        body = {"message": message}
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        return self.request(QUERY, body=json.dumps(body).encode(), **kwargs)

    def snapshot(self, conversation_id, app=None):
        return self.store.load(conversation_owner_key((app or self.app).state.settings), conversation_id)

    def assert_error(self, response, status, code):
        self.assertEqual(response[0], status)
        self.assertEqual(response[2]["status"], "failed")
        self.assertEqual(response[2]["error"]["code"], code)
        self.assertFalse(response[2]["error"]["retryable"])
        self.assertIsNone(response[2]["data"])

    def test_create_returns_only_server_uuid_ttl_and_no_calendar_authorization(self):
        response = self.request(body=b"{}")
        self.assertEqual(response[0], 201)
        payload = response[2]
        self.assertEqual(payload["action"], "conversation_create")
        self.assertEqual(payload["mutation_state"], "not_applicable")
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(set(payload["data"]), {"conversation_id", "expires_in_seconds", "execution_allowed"})
        self.assertEqual(payload["data"]["expires_in_seconds"], 1800)
        self.assertFalse(payload["data"]["execution_allowed"])
        issued = payload["data"]["conversation_id"]
        self.assertEqual(str(UUID(issued)), issued)
        self.assertEqual(UUID(issued).version, 4)
        self.assertEqual(self.snapshot(issued).memory, QueryMemory())
        self.service_factory.assert_not_called()

    def test_create_strict_empty_object_and_byte_limit_precede_factory(self):
        cases = [b"", b"[]", b"null", b"not-json", b'{"message":"x"}',
                 b'{"owner":"x"}', b'{"conversation_id":"x"}',
                 b'{"x":1,"x":2}', b'{"x":NaN}', b"{}" + b" " * 1023]
        for body in cases:
            with self.subTest(body_kind=len(body)):
                self.assert_error(self.request(body=body), 422, "invalid_request")
        self.store_factory.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_create_accepts_empty_object_at_the_exact_byte_limit(self):
        self.assertEqual(self.request(body=b"{}" + b" " * 1022)[0], 201)

    def test_create_rejects_query_content_type_and_duplicate_headers_before_store(self):
        self.assertEqual(self.request(body=b"{}", query=b"ttl=10")[0], 422)
        auth = self.headers()[:1]
        for headers in (auth, auth + [(b"content-type", b"text/plain")],
                        self.headers() + [(b"content-type", b"application/json")]):
            self.assertEqual(self.request(body=b"{}", headers=headers)[0], 422)
        self.store_factory.assert_not_called()

    def test_authentication_precedes_all_parsing_and_storage(self):
        for path, method in ((BASE, "POST"), (BASE + "/invalid", "DELETE"), (QUERY, "POST")):
            for headers in ([], self.headers(WRONG_TOKEN)):
                with self.subTest(path=path, headers=len(headers)):
                    self.assert_error(self.request(path, method=method, body=b"invalid", headers=headers), 401, "unauthorized")
        self.store_factory.assert_not_called()
        self.service_factory.assert_not_called()

    def test_unconfigured_api_does_not_touch_store(self):
        app = self.application(token=None)
        self.assert_error(self.request(body=b"{}", app=app), 503, "api_auth_unavailable")
        self.store_factory.assert_not_called()

    def test_delete_has_empty_204_and_preserves_correlation_and_cache_headers(self):
        issued = self.create()
        response = self.request(BASE + "/" + issued, method="DELETE")
        self.assertEqual(response[0], 204)
        self.assertEqual(response[3], b"")
        self.assertIsNone(response[2])
        self.assertEqual(response[1]["x-request-id"], "conversation-test")
        self.assert_error(self.query(issued), 404, "conversation_not_found")
        self.service_factory.assert_not_called()

    def test_delete_requires_canonical_uuid4_and_empty_body_before_factory(self):
        issued = str(uuid4())
        for value in ("invalid", str(uuid1()), issued.upper(), "{" + issued + "}", issued.replace("-", "")):
            with self.subTest(identifier=value):
                self.assert_error(self.request(BASE + "/" + value, method="DELETE"), 422, "invalid_request")
        for body, query in ((b"{}", b""), (b" ", b""), (b"", b"force=true")):
            self.assertEqual(self.request(BASE + "/" + issued, method="DELETE", body=body, query=query)[0], 422)
        self.store_factory.assert_not_called()

    def test_missing_delete_is_safe_404_without_disclosing_identifier(self):
        issued = str(uuid4())
        response = self.request(BASE + "/" + issued, method="DELETE")
        self.assert_error(response, 404, "conversation_not_found")
        self.assertNotIn(issued, response[3].decode())

    def test_sequential_queries_receive_only_the_same_conversation_memory(self):
        issued = self.create()
        first = self.query(issued, message="Agenda do dia 17 de junho.")
        second = self.query(issued, message="E na semana seguinte?")
        self.assertEqual((first[0], second[0]), (200, 200))
        self.assertEqual(self.service.context_calls[0][1], QueryMemory())
        self.assertEqual(self.service.context_calls[1][1], LIST_MEMORY)
        self.assertEqual(self.snapshot(issued).revision, 2)
        self.assertEqual(self.service.stateless_calls, [])
        for response in (first, second):
            self.assertNotIn(issued, response[3].decode())
            self.assertNotIn("last_list", response[3].decode())
            self.assertEqual(response[2]["mutation_state"], "not_applicable")
            self.assertFalse(response[2]["data"]["execution_allowed"])

    def test_two_conversations_do_not_share_state(self):
        first, second = self.create(), self.create()
        self.query(first)
        self.query(second)
        self.assertEqual([memory for _, memory in self.service.context_calls], [QueryMemory(), QueryMemory()])
        self.assertEqual(self.snapshot(first).revision, 1)
        self.assertEqual(self.snapshot(second).revision, 1)

    def test_rotated_api_key_cannot_load_or_delete_previous_owner_conversation(self):
        issued = self.create()
        rotated = self.application(token=WRONG_TOKEN)
        self.assert_error(self.query(issued, app=rotated, headers=self.headers(WRONG_TOKEN)), 404, "conversation_not_found")
        self.assert_error(self.request(BASE + "/" + issued, method="DELETE", app=rotated,
                                       headers=self.headers(WRONG_TOKEN)), 404, "conversation_not_found")
        self.assertEqual(self.snapshot(issued).revision, 0)
        self.service_factory.assert_not_called()

    def test_unknown_conversation_is_checked_before_model_factory(self):
        self.assert_error(self.query(str(uuid4())), 404, "conversation_not_found")
        self.service_factory.assert_not_called()
        self.assertEqual(self.service.context_calls, [])

    def test_stateless_queries_preserve_old_method_and_create_no_database(self):
        app = self.application(store_factory=None)
        self.assertEqual(self.query(app=app)[0], 200)
        self.assertEqual(self.service.stateless_calls, ["Consulte a agenda."])
        self.assertEqual(self.service.context_calls, [])
        self.assertFalse(self.path.exists())
        self.store_factory.assert_not_called()

    def test_optional_id_rejects_null_noncanonical_and_unknown_fields_before_store(self):
        issued = str(uuid4())
        values = (None, True, 4, [], {}, "", "invalid", str(uuid1()), issued.upper(),
                  "{" + issued + "}", issued.replace("-", ""))
        for value in values:
            body = json.dumps({"message": "Agenda.", "conversation_id": value}).encode()
            self.assertEqual(self.request(QUERY, body=body)[0], 422)
        self.assertEqual(self.request(QUERY, body=json.dumps({
            "message": "Agenda.", "conversation_id": issued, "memory": {},
        }).encode())[0], 422)
        self.store_factory.assert_not_called()
        self.service_factory.assert_not_called()

    def test_invalid_or_oversize_message_precedes_store_even_with_valid_id(self):
        for message in (None, "", " " * 3, "x" * 4001):
            body = json.dumps({"message": message, "conversation_id": str(uuid4())}).encode()
            self.assertEqual(self.request(QUERY, body=body)[0], 422)
        self.assertEqual(self.request(QUERY, body=b" " * (24 * 1024 + 1))[0], 422)
        self.store_factory.assert_not_called()
        self.service_factory.assert_not_called()

    def test_memory_survives_new_application_and_default_store_factory(self):
        first = self.application(store_factory=None)
        issued = self.create(app=first)
        self.query(issued, app=first)
        restarted = self.application(store_factory=None)
        self.query(issued, app=restarted)
        self.assertEqual(self.service.context_calls[-1][1], LIST_MEMORY)

    def clocked_store(self):
        clock = [datetime(2030, 6, 17, tzinfo=timezone.utc)]
        self.store = ConversationStore(self.path, clock=lambda: clock[0])
        self.store_factory.return_value = self.store
        return clock

    def test_expired_conversation_is_unavailable_before_model_and_cannot_be_deleted(self):
        clock = self.clocked_store()
        issued = self.create()
        clock[0] += timedelta(seconds=1800)
        self.assert_error(self.query(issued), 404, "conversation_not_found")
        self.assert_error(self.request(BASE + "/" + issued, method="DELETE"), 404, "conversation_not_found")
        self.service_factory.assert_not_called()

    def test_failed_result_does_not_refresh_expiration(self):
        clock = self.clocked_store()
        issued = self.create()
        clock[0] += timedelta(seconds=1799)
        self.service.returned_result = QueryResult("failed", None, "Falha na consulta.", None,
                                                  http_status=502, error_code="operation_failed")
        self.assert_error(self.query(issued), 502, "operation_failed")
        clock[0] += timedelta(seconds=1)
        self.service_factory.reset_mock()
        self.assert_error(self.query(issued), 404, "conversation_not_found")
        self.service_factory.assert_not_called()

    def test_saved_question_renews_ttl_for_follow_up(self):
        clock = self.clocked_store()
        issued = self.create()
        clock[0] += timedelta(seconds=1799)
        self.service.next_memory = QueryMemory()
        self.service.returned_result = QueryResult("needs_clarification", None, "Qual período?", {
            "reason": "missing_information", "question": "Qual período?", "execution_allowed": False,
        })
        self.assertEqual(self.query(issued)[0], 200)
        clock[0] += timedelta(seconds=2)
        self.assertEqual(self.query(issued)[0], 200)
        self.assertEqual(self.snapshot(issued).revision, 2)

    def test_default_factory_resolves_relative_path_against_project_root(self):
        from app.api_conversations import default_conversation_store
        from app.settings import PROJECT_ROOT
        config = Settings(_env_file=None, conversation_database_path=Path("data/temporary-context.sqlite3"))
        from unittest.mock import patch
        with patch("app.api_conversations.ConversationStore") as constructor:
            default_conversation_store(config)
        constructor.assert_called_once_with(PROJECT_ROOT / "data/temporary-context.sqlite3", ttl_seconds=1800)

    def test_create_respects_capacity_without_exposing_store_error(self):
        self.store = ConversationStore(self.path, max_entries=1)
        self.store_factory.return_value = self.store
        self.create()
        self.assert_error(self.request(body=b"{}"), 503, "conversation_capacity")
        self.service_factory.assert_not_called()

    def test_injected_store_errors_have_safe_fixed_messages(self):
        for code, expected in (("conversation_unavailable", 503), ("conversation_capacity", 503),
                               ("conversation_conflict", 409), ("conversation_not_found", 404),
                               ("invalid_conversation", 422)):
            self.store_factory.side_effect = ConversationError(code)
            self.assert_error(self.request(body=b"{}"), expected, code)
        self.store_factory.side_effect = OSError(PRIVATE_ERROR)
        self.assert_error(self.request(body=b"{}"), 503, "conversation_unavailable")

    def test_invalid_factory_issued_id_fails_closed(self):
        self.store_factory.return_value = Mock(create=Mock(return_value="private-invalid-id"))
        self.assert_error(self.request(body=b"{}"), 503, "conversation_unavailable")

    def test_storage_failure_before_model_is_not_reported_as_an_llm_failure(self):
        self.store_factory.side_effect = OSError(PRIVATE_ERROR)
        self.assert_error(self.query(str(uuid4())), 503, "conversation_unavailable")
        self.service_factory.assert_not_called()

    def test_model_error_does_not_advance_revision_or_invent_memory(self):
        issued = self.create()
        self.service.error = ModelError("llm_timeout")
        self.assert_error(self.query(issued), 504, "llm_timeout")
        snapshot = self.snapshot(issued)
        self.assertEqual((snapshot.revision, snapshot.memory), (0, QueryMemory()))
        self.service_factory.assert_called_once_with()

    def test_default_service_without_llm_key_preserves_existing_context(self):
        issued = self.create()
        self.app.state.agent_query_service_factory = None
        self.assert_error(self.query(issued), 503, "llm_not_configured")
        self.assertEqual(self.snapshot(issued).revision, 0)
        self.assertEqual(self.snapshot(issued).memory, QueryMemory())
        self.service_factory.assert_not_called()

    def test_failed_calendar_result_does_not_save_even_a_valid_new_memory(self):
        issued = self.create()
        self.service.returned_result = QueryResult("failed", None, "Falha na consulta.", None,
                                                  http_status=502, error_code="operation_failed")
        self.assert_error(self.query(issued), 502, "operation_failed")
        self.assertEqual(self.snapshot(issued).revision, 0)
        self.assertEqual(self.snapshot(issued).memory, QueryMemory())

    def test_valid_question_can_refresh_activity_without_inventing_memory(self):
        issued = self.create()
        self.service.next_memory = QueryMemory()
        self.service.returned_result = QueryResult("needs_clarification", None, "Qual período?", {
            "reason": "missing_information", "question": "Qual período?", "execution_allowed": False,
        })
        response = self.query(issued)
        self.assertEqual((response[0], response[2]["status"]), (200, "needs_clarification"))
        self.assertEqual(self.snapshot(issued).revision, 1)
        self.assertEqual(self.snapshot(issued).memory, QueryMemory())

    def test_invalid_memory_is_rejected_before_any_save(self):
        issued = self.create()
        for invalid in (None, {}, {"last_list": LIST_MEMORY.last_list},
                        QueryMemory(last_list={"date": "2030-99-99", "period": "day", "time_zone": ZONE})):
            self.service.next_memory = invalid
            self.assert_error(self.query(issued), 502, "llm_invalid_response")
            self.assertEqual(self.snapshot(issued).revision, 0)
            self.assertEqual(self.snapshot(issued).memory, QueryMemory())

    def test_invalid_result_is_rejected_before_any_save(self):
        issued = self.create()
        self.service.returned_result = replace(result(), data={"execution_allowed": True})
        self.assert_error(self.query(issued), 502, "llm_unavailable")
        self.assertEqual(self.snapshot(issued).revision, 0)

    def test_compare_and_swap_conflict_is_409_without_model_retry(self):
        issued = self.create()
        self.service.barrier = Barrier(2)
        body = json.dumps({"message": "Agenda.", "conversation_id": issued}).encode()

        async def simultaneous():
            return await asyncio.gather(*[
                invoke(self.app, QUERY, headers=self.headers(), body=body) for _ in range(2)
            ])

        responses = asyncio.run(simultaneous())
        self.assertEqual(sorted(response[0] for response in responses), [200, 409])
        failed = next(response for response in responses if response[0] == 409)
        self.assert_error(failed, 409, "conversation_conflict")
        self.assertEqual(self.snapshot(issued).revision, 1)
        self.assertEqual(len(self.service.context_calls), 2)
        self.assertEqual(self.service_factory.call_count, 2)

    def test_store_service_and_cas_run_outside_event_loop(self):
        issued = self.create()
        observed = []
        loop_thread = get_ident()
        real_load, real_save = self.store.load, self.store.save

        def load(*args):
            observed.append(get_ident())
            return real_load(*args)

        def save(*args):
            observed.append(get_ident())
            return real_save(*args)

        def factory():
            observed.append(get_ident())
            return self.service

        self.store.load, self.store.save = load, save
        self.service_factory.side_effect = factory
        self.assertEqual(self.query(issued)[0], 200)
        self.assertEqual(len(observed), 3)
        self.assertTrue(all(thread != loop_thread for thread in observed))

    def test_deleted_conversation_can_never_be_recreated_by_querying_its_id(self):
        issued = self.create()
        self.query(issued)
        self.request(BASE + "/" + issued, method="DELETE")
        self.service_factory.reset_mock()
        self.assert_error(self.query(issued), 404, "conversation_not_found")
        self.service_factory.assert_not_called()

    def test_openapi_preserves_bearer_authentication_on_conversation_routes(self):
        schema = self.app.openapi()
        for path, method in ((BASE, "post"), (BASE + "/{conversation_id}", "delete"), (QUERY, "post")):
            self.assertIn({"ApiKeyBearer": []}, schema["paths"][path][method]["security"])


if __name__ == "__main__":
    unittest.main()
