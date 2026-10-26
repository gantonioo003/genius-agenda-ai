"""Disparo por ASGI com SQLite e provedores fictícios; sem tráfego externo."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import unittest
from unittest.mock import Mock, patch

from app.api_reminder_delivery import default_reminder_recipient, default_reminder_sender
from app.main import create_app
from app.reminder_store import ReminderStore
from app.services.reminder_delivery import ReminderDeliverySummary, ReminderRecipient
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
from app.services.reminder_tick import reminder_tick_lock
from app.services.reminders import ReminderCalendarEvent
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi


URL = "/api/v1/reminders/dispatch"
CALENDAR = "dispatch-api-calendar@example.invalid"
BINDING = "a" * 64
PHONE = "5511999999999"
TITLE = "Título privado fictício do lembrete"
ZONE = "America/Recife"
HEADERS = ((b"authorization", f"Bearer {TOKEN}".encode("ascii")),
           (b"content-type", b"application/json"), (b"x-request-id", b"reminder-tick-api-001"))
COUNTERS = {"reminders_considered", "expired_count", "cancelled_count", "blocked_count",
            "accepted_count", "uncertain_count", "failed_count", "transport_attempts"}


class ReminderDeliveryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "reminders.sqlite3"
        self.store = ReminderStore(self.path)
        self.start = datetime.now(timezone.utc) + timedelta(minutes=14, seconds=30)
        self.event = ReminderCalendarEvent("event123", TITLE, self.start,
            self.start + timedelta(hours=1), False, "confirmed")
        self.gateway = Mock()
        self.gateway.lookup_snapshot.return_value = ReminderLookupSnapshot(CALENDAR, ZONE,
            (ReminderEventLookup("event123", "found", self.event),))
        self.store_factory = Mock(return_value=self.store)
        self.recipient_loader = Mock(return_value=ReminderRecipient(PHONE, BINDING))
        self.sender = Mock()
        self.sender.send.return_value = "f" * 64
        self.sender_factory = Mock(return_value=self.sender)
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
            reminder_delivery_recipient_loader=self.recipient_loader,
            reminder_delivery_sender_factory=self.sender_factory)

    def configure(self, **changes):
        old = self.store.config()
        values = dict(calendar_id=CALENDAR, recipient_key=BINDING, enabled=True,
            permission_granted=True, expected_revision=None if old is None else old.revision)
        values.update(changes)
        self.store.configure(**values)

    def seed(self, *, channel=True):
        self.configure()
        record = self.store.plan("event123", self.start, "imminent", title=TITLE,
            calendar_timezone=ZONE, config_revision=1)
        if channel:
            received = datetime.now(timezone.utc)
            self.store.observe_inbound("b" * 64, BINDING, str(int(received.timestamp())), received)
        return record

    def request(self, *, headers=HEADERS, body=b"{}", **changes):
        response = asyncio.run(invoke_asgi(self.app, URL, method="POST", headers=headers, body=body, **changes))
        for private in (TOKEN, WRONG_TOKEN, TITLE, CALENDAR, BINDING, PHONE, str(self.path), "raw secret"):
            self.assertNotIn(private, json.dumps(response))
        return response

    def assert_unused(self):
        self.store_factory.assert_not_called()
        self.recipient_loader.assert_not_called()
        self.sender_factory.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)
        self.assertFalse(self.path.exists())

    def assert_envelope(self, response, *, status, mutation_state, dispatch_status=None):
        _http, headers, payload = response
        self.assertEqual(set(payload), {"api_version", "request_id", "operation_id", "status", "action",
                                       "mutation_state", "message", "data", "error"})
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], headers["x-request-id"])
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["status"], status)
        self.assertEqual(payload["action"], "reminder_dispatch")
        self.assertEqual(payload["mutation_state"], mutation_state)
        self.assertEqual(headers["cache-control"], "no-store")
        if dispatch_status is not None:
            data = payload["data"]
            self.assertEqual(set(data), {"dispatch_status", "skip_reason", *COUNTERS})
            self.assertEqual(data["dispatch_status"], dispatch_status)
            self.assertEqual(data["transport_attempts"], data["accepted_count"] + data["uncertain_count"])
            self.assertTrue(all(type(data[key]) is int and data[key] >= 0 for key in COUNTERS))

    def test_authentication_precedes_malformed_body_query_and_private_factories(self):
        for headers in ((), ((b"authorization", f"Bearer {WRONG_TOKEN}".encode("ascii")),),
                        ((b"authorization", b"Basic ignored"),)):
            response = self.request(headers=headers, body=b"{raw secret", query=b"recipient=rawsecret")
            self.assertEqual(response[0], 401)
            self.assertEqual(response[2]["error"]["code"], "unauthorized")
        self.assert_unused()

    def test_request_id_validation_precedes_factories(self):
        response = self.request(headers=(*HEADERS[:2], (b"x-request-id", b"raw secret")))
        self.assertEqual(response[0], 422)
        self.assertEqual(response[2]["error"]["fields"], ["X-Request-ID"])
        self.assert_unused()

    def test_requires_empty_json_without_any_llm_destination_force_or_body_request_id(self):
        for body in (b"", b"null", b"[]", b"true", b"0", b'""', b"{", b"\xff",
                     b'{"request_id":"x"}', b'{"recipient":"raw secret"}', b'{"force":true}',
                     b'{"llm":{"tool":"send"}}', b'{"x":1,"x":2}', b" " * 1025 + b"{}"):
            with self.subTest(body=body[:25]):
                response = self.request(body=body)
                self.assertEqual(response[0], 422)
                self.assert_envelope(response, status="failed", mutation_state="not_applied")
                self.assertEqual(response[2]["error"], {
                    "code": "invalid_request", "retryable": False, "fields": ["body"]})
        self.assert_unused()

    def test_query_and_content_type_are_strict(self):
        for query in (b"force=true", b"recipient=rawsecret", b"x=1&x=2", b"?"):
            response = self.request(query=query)
            self.assertEqual(response[0], 422)
            self.assertEqual(response[2]["error"]["fields"], ["query"])
        for headers in (HEADERS[:1], (*HEADERS[:1], (b"content-type", b"text/plain")),
                        (*HEADERS, (b"content-type", b"application/json"))):
            self.assertEqual(self.request(headers=headers)[0], 422)
        self.assert_unused()

    def test_not_configured_disabled_or_without_permission_skips_all_private_dependencies(self):
        for changes, reason in ((None, "not_configured"), ({"enabled": False}, "disabled"),
                                ({"permission_granted": False}, "permission_required")):
            if changes is not None:
                self.configure(**changes)
            response = self.request(body=b" \n {} \t")
            self.assertEqual(response[0], 200)
            self.assert_envelope(response, status="skipped", mutation_state="not_applied", dispatch_status="skipped")
            self.assertEqual(response[2]["data"]["skip_reason"], reason)
        self.recipient_loader.assert_not_called()
        self.sender_factory.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)

    def test_busy_uses_same_lock_as_tick_and_does_not_load_private_policy(self):
        self.configure()
        with reminder_tick_lock(self.path) as acquired:
            self.assertTrue(acquired)
            response = self.request()
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="skipped", mutation_state="not_applied", dispatch_status="skipped")
        self.assertEqual(response[2]["data"]["skip_reason"], "busy")
        self.recipient_loader.assert_not_called()
        self.sender_factory.assert_not_called()

    def test_empty_active_profile_is_completed_without_loading_policy_google_or_sender(self):
        self.configure()
        response = self.request()
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="succeeded", mutation_state="not_applied", dispatch_status="completed")
        self.recipient_loader.assert_not_called()
        self.sender_factory.assert_not_called()
        self.assertEqual(self.gateway_opened, 0)

    def test_valid_due_reminder_is_accepted_once_and_response_contains_only_aggregates(self):
        self.seed()
        main_thread, observed = get_ident(), []
        self.sender.send.side_effect = lambda *args: observed.append(get_ident()) or "f" * 64
        first = self.request()
        self.assertEqual(first[0], 200)
        self.assert_envelope(first, status="succeeded", mutation_state="confirmed", dispatch_status="completed")
        self.assertEqual(first[2]["data"]["accepted_count"], 1)
        self.assertEqual(first[2]["data"]["transport_attempts"], 1)
        second = self.request()
        self.assertEqual(second[0], 200)
        self.assertEqual(second[2]["data"]["transport_attempts"], 0)
        self.sender.send.assert_called_once()
        self.assertTrue(observed and all(thread != main_thread for thread in observed))
        self.assertEqual(self.store.list_reminders()[0].state, "accepted")

    def test_unknown_channel_blocks_locally_without_opening_google_or_sender(self):
        self.seed(channel=False)
        response = self.request()
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="succeeded", mutation_state="not_applied", dispatch_status="completed")
        self.assertEqual(response[2]["data"]["blocked_count"], 1)
        self.assertEqual(self.gateway_opened, 0)
        self.sender_factory.assert_not_called()

    def test_recipient_mismatch_fails_before_google_and_sender(self):
        self.seed()
        self.recipient_loader.return_value = ReminderRecipient(PHONE, "c" * 64)
        response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed", mutation_state="not_applied", dispatch_status="failed")
        self.assertEqual(self.gateway_opened, 0)
        self.sender_factory.assert_not_called()

    def test_google_failure_is_not_empty_success_and_does_not_send(self):
        self.seed()
        self.gateway.lookup_snapshot.side_effect = TimeoutError("raw secret")
        response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed", mutation_state="not_applied", dispatch_status="failed")
        self.sender_factory.assert_not_called()

    def test_sender_timeout_is_uncertain_and_same_reminder_is_never_retried(self):
        self.seed()
        self.sender.send.side_effect = TimeoutError("raw secret")
        first = self.request()
        self.assertEqual(first[0], 503)
        self.assert_envelope(first, status="failed", mutation_state="unknown", dispatch_status="partial")
        self.assertEqual(first[2]["data"]["uncertain_count"], 1)
        self.assertEqual(first[2]["error"], {
            "code": "reminder_dispatch_partial", "retryable": False, "fields": []})
        self.assertEqual(self.store.list_reminders()[0].state, "uncertain")
        self.assertEqual(self.request()[2]["data"]["transport_attempts"], 0)
        self.sender.send.assert_called_once()

    def test_lost_http_wait_keeps_worker_lock_and_does_not_resend_after_completion(self):
        self.seed()
        entered, release, finished = Event(), Event(), Event()
        def send(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Teste não liberou o transporte fictício.")
            return "f" * 64
        self.sender.send.side_effect = send
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
                busy = await invoke_asgi(self.app, URL, method="POST", headers=HEADERS, body=b"{}")
                self.assertEqual(busy[2]["data"]["skip_reason"], "busy")
            finally:
                release.set()
                if not first.done():
                    first.cancel()
                await asyncio.to_thread(finished.wait, 5)
        asyncio.run(scenario())
        self.assertEqual(self.request()[2]["data"]["transport_attempts"], 0)
        self.sender.send.assert_called_once()
        self.assertEqual(self.store.list_reminders()[0].state, "accepted")

    def test_unexpected_factory_and_service_errors_do_not_invent_counters(self):
        self.store_factory.side_effect = RuntimeError("raw secret")
        before = self.request()
        self.assertEqual(before[0], 503)
        self.assert_envelope(before, status="failed", mutation_state="not_applied")
        self.assertIsNone(before[2]["data"])
        self.store_factory.side_effect = None
        with patch("app.api_reminder_delivery.ReminderDeliveryService") as service:
            service.return_value.dispatch.side_effect = RuntimeError("raw secret")
            after = self.request()
        self.assertEqual(after[0], 503)
        self.assert_envelope(after, status="failed", mutation_state="unknown")
        self.assertIsNone(after[2]["data"])

    def test_invalid_result_projection_fails_closed_as_unknown_without_private_values(self):
        results = (object(), ReminderDeliverySummary("completed", accepted_count=1),
                   ReminderDeliverySummary("completed", failed_count=1),
                   ReminderDeliverySummary("failed"), ReminderDeliverySummary("skipped", "raw secret"))
        for result in results:
            with patch("app.api_reminder_delivery.ReminderDeliveryService") as service:
                service.return_value.dispatch.return_value = result
                response = self.request()
            self.assertEqual(response[0], 503)
            self.assert_envelope(response, status="failed", mutation_state="unknown")
            self.assertIsNone(response[2]["data"])

    def test_default_recipient_uses_one_snapshot_and_same_hmac_as_existing_binding(self):
        token = "F" * 43
        fixture_root = Path(self.temp.name) / "fixtures"
        policy = json.dumps({"allowed_phone": PHONE}).encode()
        config = json.dumps({"callback_token": token, "webhook_token": "W" * 43,
                             "workflow_id": "fixture", "webhook_credential_id": "fixture"}).encode()
        calls = []
        def read(path):
            calls.append(path)
            return policy if path.name == "whatsapp-sandbox-security.json" else config
        with patch("app.api_reminder_delivery.PROJECT_ROOT", fixture_root), \
             patch.object(Path, "read_bytes", read):
            result = default_reminder_recipient()
        expected = hmac.new(token.encode("ascii"),
            b"genius-whatsapp-agent-sender:v1\0" + PHONE.encode("ascii"), sha256).hexdigest()
        self.assertEqual(result, ReminderRecipient(PHONE, expected))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 2)
        self.assertNotIn(PHONE, repr(result))
        self.assertNotIn(expected, repr(result))
        with patch.object(Path, "read_bytes", side_effect=OSError("raw secret")):
            with self.assertRaises(ValueError) as error:
                default_reminder_recipient()
        self.assertNotIn("raw secret", str(error.exception))

    def test_default_sender_is_constructed_only_when_explicit_factory_is_called(self):
        config = Settings(_env_file=None, d360_api_key="FixtureSandboxKey_0123456789")
        with patch("app.whatsapp_sandbox_sender.SandboxSender", return_value=self.sender) as sender:
            self.assertIs(default_reminder_sender(config), self.sender)
        sender.assert_called_once_with(config.d360_api_key)
        self.sender.send.assert_not_called()

    def test_startup_does_not_load_private_dependencies_or_start_reminder_loop(self):
        async def lifespan():
            async with self.app.router.lifespan_context(self.app):
                self.assert_unused()
        asyncio.run(lifespan())

    def test_openapi_documents_bearer_and_required_empty_object(self):
        operation = self.app.openapi()["paths"][URL]["post"]
        self.assertIn({"ApiKeyBearer": []}, operation["security"])
        self.assertTrue(operation["requestBody"]["required"])
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["maxProperties"], 0)
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("422", operation["responses"])
        self.assertIn("503", operation["responses"])
        self.assert_unused()


if __name__ == "__main__":
    unittest.main()
