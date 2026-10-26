"""API de pulso por ASGI: autenticação primeiro e dependências só fictícias."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import unittest
from unittest.mock import Mock, patch

from app.api_reminders import default_recipient_binding, default_reminder_gateway, default_reminder_store
from app.main import create_app
from app.reminder_store import ReminderPlan, ReminderStore
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
from app.services.reminder_tick import reminder_tick_lock
from app.services.reminders import ReminderCalendarEvent, ReminderCalendarSnapshot
from app.settings import PROJECT_ROOT, Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi


URL = "/api/v1/reminders/tick"
CALENDAR = "tick-api-calendar@example.invalid"
RECIPIENT = "a" * 64
TITLE = "Título privado fictício do evento"
ZONE = "America/Recife"
HEADERS = ((b"authorization", f"Bearer {TOKEN}".encode("ascii")),
           (b"content-type", b"application/json"), (b"x-request-id", b"tick-test-001"))


class ReminderTickApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "reminders.sqlite3"
        self.store = ReminderStore(self.path)
        self.start = datetime.now(timezone.utc) + timedelta(hours=2)
        self.event = ReminderCalendarEvent("event123", TITLE, self.start,
            self.start + timedelta(hours=1), False, "confirmed")
        self.gateway = Mock()
        self.gateway.lookup_snapshot.return_value = ReminderLookupSnapshot(CALENDAR, ZONE,
            (ReminderEventLookup("event123", "found", self.event),))
        self.gateway.snapshot.return_value = ReminderCalendarSnapshot(CALENDAR, ZONE, (self.event,))
        self.store_factory = Mock(return_value=self.store)
        self.binding_loader = Mock(return_value=RECIPIENT)
        self.gateway_opened = 0
        self.gateway_closed = 0

        @contextmanager
        def gateway_factory():
            self.gateway_opened += 1
            try:
                yield self.gateway
            finally:
                self.gateway_closed += 1

        self.gateway_factory = gateway_factory
        self.config = Settings(_env_file=None, api_auth_token=TOKEN, app_env="test")
        self.app = create_app(config=self.config, db=InjectedDatabase(),
            reminder_store_factory=self.store_factory, reminder_gateway_factory=gateway_factory,
            reminder_recipient_binding_loader=self.binding_loader)

    def configure(self, **changes):
        old = self.store.config()
        values = dict(calendar_id=CALENDAR, recipient_key=RECIPIENT, enabled=True,
            permission_granted=True, expected_revision=None if old is None else old.revision)
        values.update(changes)
        self.store.configure(**values)

    def seed(self):
        self.configure()
        return self.store.plan_batch(tuple(ReminderPlan(self.event.event_id, self.start,
            slot, TITLE, ZONE) for slot in ("preparation", "imminent")), config_revision=1)

    def request(self, *, headers=HEADERS, body=b"{}", **changes):
        response = asyncio.run(invoke_asgi(self.app, URL, method="POST",
            headers=headers, body=body, **changes))
        for private in (TOKEN, WRONG_TOKEN, TITLE, CALENDAR, RECIPIENT, str(self.path), "raw secret"):
            self.assertNotIn(private, json.dumps(response))
        return response

    def assert_unused(self):
        self.store_factory.assert_not_called()
        self.binding_loader.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)
        self.assertFalse(self.path.exists())

    def assert_envelope(self, response, *, status, tick_status=None):
        _http, headers, payload = response
        self.assertEqual(set(payload), {"api_version", "request_id", "operation_id", "status", "action",
                                       "mutation_state", "message", "data", "error"})
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], headers["x-request-id"])
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["status"], status)
        self.assertEqual(payload["action"], "reminder_tick")
        self.assertEqual(payload["mutation_state"], "not_applicable")
        self.assertEqual(headers["cache-control"], "no-store")
        if tick_status is not None:
            data = payload["data"]
            self.assertEqual(set(data), {"tick_status", "skip_reason", "local_mutation_state",
                "synchronization", "generation", "messages_sent", "external_mutations"})
            self.assertEqual(data["tick_status"], tick_status)
            self.assertEqual(data["messages_sent"], 0)
            self.assertEqual(data["external_mutations"], 0)
            self.assertEqual(set(data["synchronization"]), {"status", "events_checked", "tracked_reminders",
                "reminders_considered", "cancelled_count", "replanned_count", "unchanged_count", "state_counts"})
            self.assertEqual(set(data["generation"]), {"status", "events_seen", "eligible_events",
                "skipped_count", "reminders_considered", "state_counts"})

    def test_authentication_precedes_body_parsing_queries_and_factories(self):
        for headers in ((), ((b"authorization", f"Bearer {WRONG_TOKEN}".encode("ascii")),),
                        ((b"authorization", b"Basic ignored"),)):
            response = self.request(headers=headers, body=b"{invalid raw secret", query=b"llm=rawsecret")
            self.assertEqual(response[0], 401)
            self.assertEqual(response[2]["error"]["code"], "unauthorized")
        self.assert_unused()

    def test_invalid_request_id_is_rejected_before_factories(self):
        headers = (*HEADERS[:2], (b"x-request-id", b"raw secret"))
        response = self.request(headers=headers)
        self.assertEqual(response[0], 422)
        self.assertEqual(response[2]["error"]["fields"], ["X-Request-ID"])
        self.assert_unused()

    def test_empty_json_is_required_and_no_arguments_or_override_are_accepted(self):
        bodies = (b"", b"null", b"[]", b"true", b"0", b'""', b"{", b"\xff",
            b'{"request_id":"tick-test-001"}', b'{"force":true}', b'{"recipient":"raw secret"}',
            b'{"llm":{"tool":"send"}}', b'{"foo":1,"foo":2}', b" " * 1025 + b"{}")
        for body in bodies:
            with self.subTest(body=body[:25]):
                response = self.request(body=body)
                self.assertEqual(response[0], 422)
                self.assert_envelope(response, status="failed")
                self.assertEqual(response[2]["error"], {
                    "code": "invalid_request", "retryable": False, "fields": ["body"]})
        self.assert_unused()

    def test_content_type_and_query_are_strict_and_do_not_echo_unknown_keys(self):
        headers = (HEADERS[:1], (*HEADERS[:1], (b"content-type", b"text/plain")),
                   (*HEADERS, (b"content-type", b"application/json")))
        for supplied in headers:
            self.assertEqual(self.request(headers=supplied)[0], 422)
        for query in (b"force=true", b"recipient=rawsecret", b"x=1&x=2", b"?"):
            response = self.request(query=query)
            self.assertEqual(response[0], 422)
            self.assertEqual(response[2]["error"]["fields"], ["query"])
        self.assert_unused()

    def test_missing_profile_skips_policy_and_google_with_aggregate_only_dto(self):
        response = self.request(body=b" \n {} \t")
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="skipped", tick_status="skipped")
        self.assertEqual(response[2]["data"]["skip_reason"], "not_configured")
        self.binding_loader.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)

    def test_disabled_or_no_permission_skips_policy_and_google(self):
        for changes, reason in (({"enabled": False}, "disabled"),
                                ({"permission_granted": False}, "permission_required")):
            self.configure(**changes)
            response = self.request()
            self.assertEqual(response[0], 200)
            self.assertEqual(response[2]["data"]["skip_reason"], reason)
        self.binding_loader.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)

    def test_completed_tick_uses_injected_factories_in_worker_and_preserves_request_id(self):
        self.configure()
        main_thread = get_ident()
        observed = []
        snapshot = self.gateway.snapshot.return_value
        def read(*args, **kwargs):
            observed.append(get_ident())
            return snapshot
        self.gateway.snapshot.side_effect = read
        response = self.request()
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="succeeded", tick_status="completed")
        self.assertEqual(response[2]["request_id"], "tick-test-001")
        self.assertEqual(response[2]["data"]["generation"]["reminders_considered"], 2)
        self.assertEqual((self.gateway_opened, self.gateway_closed), (1, 1))
        self.assertTrue(observed and all(thread != main_thread for thread in observed))
        self.assertEqual(len(self.store.list_reminders()), 2)

    def test_binding_mismatch_and_private_policy_errors_fail_before_google(self):
        self.configure()
        self.binding_loader.return_value = "b" * 64
        response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed", tick_status="failed")
        self.assertEqual(response[2]["error"], {
            "code": "reminder_tick_failed", "retryable": False, "fields": []})
        self.assertEqual(self.gateway_opened, 0)
        self.binding_loader.side_effect = OSError("raw secret")
        self.assertEqual(self.request()[0], 503)

    def test_busy_is_successful_skip_without_google_or_policy(self):
        self.configure()
        with reminder_tick_lock(self.path) as acquired:
            self.assertTrue(acquired)
            response = self.request()
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="skipped", tick_status="skipped")
        self.assertEqual(response[2]["data"]["skip_reason"], "busy")
        self.binding_loader.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)

    def test_google_sync_timeout_is_not_empty_success_and_prevents_generation(self):
        records = self.seed()
        self.gateway.lookup_snapshot.side_effect = TimeoutError("raw secret")
        response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed", tick_status="failed")
        self.assertEqual(response[2]["data"]["synchronization"]["status"], "failed")
        self.assertEqual(response[2]["data"]["generation"]["status"], "not_run")
        self.gateway.snapshot.assert_not_called()
        self.assertEqual(self.store.list_reminders(), records)

    def test_cancelled_http_wait_keeps_real_worker_lock_until_completion(self):
        self.configure()
        entered, release, finished = Event(), Event(), Event()
        snapshot = self.gateway.snapshot.return_value
        def read(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Teste não liberou a leitura fictícia.")
            return snapshot
        self.gateway.snapshot.side_effect = read
        @contextmanager
        def factory():
            try:
                yield self.gateway
            finally:
                finished.set()
        self.app.state.reminder_gateway_factory = factory
        async def scenario():
            first = asyncio.create_task(invoke_asgi(self.app, URL, method="POST", headers=HEADERS, body=b"{}"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                response = await invoke_asgi(self.app, URL, method="POST", headers=HEADERS, body=b"{}")
                self.assertEqual(response[0], 200)
                self.assertEqual(response[2]["data"]["skip_reason"], "busy")
                self.assertFalse(self.store.list_reminders())
            finally:
                release.set()
                if not first.done():
                    first.cancel()
                await asyncio.to_thread(finished.wait, 5)
        asyncio.run(scenario())
        self.assertEqual(len(self.store.list_reminders()), 2)
        self.gateway.snapshot.assert_called_once()

    def test_generation_failure_after_sync_retains_counts_and_partial_local_result(self):
        self.seed()
        self.gateway.lookup_snapshot.return_value = ReminderLookupSnapshot(CALENDAR, ZONE,
            (ReminderEventLookup("event123", "absent"),))
        self.gateway.snapshot.side_effect = TimeoutError("raw secret")
        response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed", tick_status="partial")
        data = response[2]["data"]
        self.assertEqual(data["local_mutation_state"], "partial")
        self.assertEqual(data["synchronization"]["cancelled_count"], 2)
        self.assertEqual(data["generation"]["status"], "failed")
        self.assertEqual(response[2]["error"]["code"], "reminder_tick_partial")
        self.assertTrue(all(row.state == "cancelled" for row in self.store.list_reminders()))

    def test_store_factory_errors_are_safe_and_do_not_create_default_store(self):
        self.store_factory.side_effect = RuntimeError("raw secret")
        with patch("app.api_reminders.default_reminder_store", side_effect=AssertionError("real store forbidden")):
            response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed", tick_status="failed")
        self.binding_loader.assert_not_called()

    def test_default_factories_are_lazy_and_reuse_existing_policy_and_google_adapter(self):
        path = Path(self.temp.name) / "never-created" / "private.sqlite3"
        config = Settings(_env_file=None, reminder_database_path=path)
        store = default_reminder_store(config)
        self.assertEqual(store.path, path)
        self.assertFalse(path.parent.exists())
        config = Settings(_env_file=None, reminder_database_path="data/fixture-reminders.sqlite3")
        self.assertEqual(default_reminder_store(config).path, PROJECT_ROOT / "data/fixture-reminders.sqlite3")
        with patch("app.api_reminders.load_recipient_binding", return_value=RECIPIENT) as binding:
            self.assertEqual(default_recipient_binding(), RECIPIENT)
        binding.assert_called_once_with(PROJECT_ROOT)
        @contextmanager
        def client_factory(**kwargs):
            self.assertEqual(kwargs, {"interactive": False})
            yield "fixture-client"
        with patch("app.google_calendar.calendar_client", side_effect=client_factory) as client, \
             patch("app.reminder_calendar.GoogleReminderCalendarGateway", return_value=self.gateway) as gateway:
            with default_reminder_gateway() as result:
                self.assertIs(result, self.gateway)
        client.assert_called_once_with(interactive=False)
        gateway.assert_called_once_with("fixture-client")

    def test_settings_path_default_and_environment_override_do_not_add_a_scheduler(self):
        self.assertEqual(Settings(_env_file=None).reminder_database_path, Path("data/reminders.sqlite3"))
        with patch.dict(os.environ, {"REMINDER_DATABASE_PATH": "data/fixture-reminders.sqlite3"}):
            self.assertEqual(Settings(_env_file=None).reminder_database_path,
                             Path("data/fixture-reminders.sqlite3"))
        async def lifespan():
            async with self.app.router.lifespan_context(self.app):
                self.assert_unused()
        asyncio.run(lifespan())

    def test_openapi_documents_bearer_and_required_empty_object(self):
        schema = self.app.openapi()
        operation = schema["paths"][URL]["post"]
        self.assertIn({"ApiKeyBearer": []}, operation["security"])
        self.assertTrue(operation["requestBody"]["required"])
        body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(body_schema["type"], "object")
        self.assertFalse(body_schema["additionalProperties"])
        self.assertEqual(body_schema["maxProperties"], 0)
        self.assertIn("422", operation["responses"])
        self.assertIn("503", operation["responses"])
        self.assert_unused()


if __name__ == "__main__":
    unittest.main()
