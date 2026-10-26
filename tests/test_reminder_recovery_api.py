"""Recuperação via ASGI e SQLite reais; nenhum provedor ou transporte."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import unittest
from unittest.mock import Mock, patch

from app.main import create_app
from app.reminder_store import ReminderStore
from app.services.reminder_recovery import ReminderRecoveryService, ReminderRecoverySummary
from app.services.reminder_tick import reminder_tick_lock
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi


URL = "/api/v1/reminders/recover"
CALENDAR = "recovery-api-calendar@example.invalid"
BINDING = "a" * 64
TITLE = "Título privado fictício de recuperação"
ZONE = "America/Recife"
HEADERS = ((b"authorization", f"Bearer {TOKEN}".encode("ascii")),
           (b"content-type", b"application/json"), (b"x-request-id", b"reminder-tick-recovery-001"))
COUNTERS = {"reservations_recovered", "expired_count", "uncertain_count", "protected_count"}


class ReminderRecoveryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private" / "reminders.sqlite3"
        self.store = ReminderStore(self.path)
        self.start = datetime.now(timezone.utc) + timedelta(minutes=14, seconds=30)
        self.store_factory = Mock(return_value=self.store)
        self.gateway_factory = Mock(side_effect=AssertionError("Consulta externa proibida."))
        self.binding_loader = Mock(side_effect=AssertionError("Política privada proibida."))
        self.recipient_loader = Mock(side_effect=AssertionError("Destinatário privado proibido."))
        self.sender_factory = Mock(side_effect=AssertionError("Transporte proibido."))
        self.config = Settings(_env_file=None, api_auth_token=TOKEN, app_env="test")
        self.app = create_app(config=self.config, db=InjectedDatabase(),
            reminder_store_factory=self.store_factory, reminder_gateway_factory=self.gateway_factory,
            reminder_recipient_binding_loader=self.binding_loader,
            reminder_delivery_recipient_loader=self.recipient_loader,
            reminder_delivery_sender_factory=self.sender_factory)
        self.addCleanup(self.assert_no_external_dependencies)

    def assert_no_external_dependencies(self):
        for factory in (self.gateway_factory, self.binding_loader, self.recipient_loader, self.sender_factory):
            factory.assert_not_called()

    def configure(self, **changes):
        old = self.store.config()
        values = dict(calendar_id=CALENDAR, recipient_key=BINDING, enabled=True,
            permission_granted=True, expected_revision=None if old is None else old.revision)
        values.update(changes)
        self.store.configure(**values)

    def reserve(self, event_id="event123"):
        if self.store.config() is None:
            self.configure()
        revision = self.store.config().revision
        record = self.store.plan(event_id, self.start, "imminent", title=TITLE,
                                 calendar_timezone=ZONE, config_revision=revision)
        return self.store.reserve(record.key, config_revision=revision)

    def request(self, *, app=None, headers=HEADERS, body=b"{}", **changes):
        response = asyncio.run(invoke_asgi(app or self.app, URL, method="POST", headers=headers, body=body, **changes))
        for private in (TOKEN, WRONG_TOKEN, TITLE, CALENDAR, BINDING, str(self.path), "raw secret", "SELECT "):
            self.assertNotIn(private, json.dumps(response))
        return response

    def assert_unused(self):
        self.store_factory.assert_not_called()
        self.assert_no_external_dependencies()
        self.assertFalse(self.path.exists())

    def assert_envelope(self, response, *, status, recovery_status=None):
        _http, headers, payload = response
        self.assertEqual(set(payload), {"api_version", "request_id", "operation_id", "status", "action",
                                       "mutation_state", "message", "data", "error"})
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], headers["x-request-id"])
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["status"], status)
        self.assertEqual(payload["action"], "reminder_recovery")
        self.assertEqual(payload["mutation_state"], "not_applicable")
        self.assertEqual(headers["cache-control"], "no-store")
        if recovery_status is not None:
            data = payload["data"]
            self.assertEqual(set(data), {"recovery_status", "skip_reason", *COUNTERS,
                                         "messages_sent", "external_mutations"})
            self.assertEqual(data["recovery_status"], recovery_status)
            self.assertEqual(data["messages_sent"], 0)
            self.assertEqual(data["external_mutations"], 0)
            self.assertTrue(all(type(data[key]) is int and data[key] >= 0 for key in COUNTERS))

    def test_authentication_precedes_malformed_body_query_and_factories(self):
        for headers in ((), ((b"authorization", f"Bearer {WRONG_TOKEN}".encode("ascii")),),
                        ((b"authorization", b"Basic ignored"),)):
            response = self.request(headers=headers, body=b"{raw secret", query=b"force=true")
            self.assertEqual(response[0], 401)
            self.assertEqual(response[2]["error"]["code"], "unauthorized")
        self.assert_unused()

    def test_missing_api_configuration_fails_before_factory(self):
        app = create_app(config=Settings(_env_file=None, api_auth_token=None), db=InjectedDatabase(),
                         reminder_store_factory=self.store_factory)
        response = self.request(app=app)
        self.assertEqual(response[0], 503)
        self.assertEqual(response[2]["error"]["code"], "api_auth_unavailable")
        self.assert_unused()

    def test_invalid_request_id_precedes_factories(self):
        response = self.request(headers=(*HEADERS[:2], (b"x-request-id", b"raw secret")))
        self.assertEqual(response[0], 422)
        self.assertEqual(response[2]["error"]["fields"], ["X-Request-ID"])
        self.assert_unused()

    def test_empty_json_is_required_and_no_force_release_key_or_llm_override_is_accepted(self):
        bodies = (b"", b"null", b"[]", b"true", b"0", b'""', b"{", b"\xff",
                  b'{"request_id":"x"}', b'{"force":true}', b'{"release_key":"raw secret"}',
                  b'{"llm":{"tool":"recover"}}', b'{"x":1,"x":2}', b" " * 1025 + b"{}")
        for body in bodies:
            with self.subTest(body=body[:25]):
                response = self.request(body=body)
                self.assertEqual(response[0], 422)
                self.assert_envelope(response, status="failed")
                self.assertEqual(response[2]["error"], {
                    "code": "invalid_request", "retryable": False, "fields": ["body"]})
        self.assert_unused()

    def test_content_type_is_required_and_single(self):
        for headers in (HEADERS[:1], (*HEADERS[:1], (b"content-type", b"text/plain")),
                        (*HEADERS, (b"content-type", b"application/json"))):
            response = self.request(headers=headers)
            self.assertEqual(response[0], 422)
            self.assertEqual(response[2]["error"]["fields"], ["body"])
        self.assert_unused()

    def test_query_is_rejected_without_echoing_unknown_keys(self):
        for query in (b"force=true", b"release_key=rawsecret", b"x=1&x=2", b"?"):
            response = self.request(query=query)
            self.assertEqual(response[0], 422)
            self.assertEqual(response[2]["error"]["fields"], ["query"])
        self.assert_unused()

    def test_missing_profile_is_a_safe_skip_with_zero_counts(self):
        response = self.request(body=b" \n {} \t")
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="skipped", recovery_status="skipped")
        self.assertEqual(response[2]["data"]["skip_reason"], "not_configured")
        self.assertTrue(all(response[2]["data"][key] == 0 for key in COUNTERS))

    def test_disabled_or_no_permission_skips_with_no_private_dependencies(self):
        for changes, reason in (({"enabled": False}, "disabled"),
                                ({"permission_granted": False}, "permission_required")):
            self.configure(**changes)
            response = self.request()
            self.assertEqual(response[0], 200)
            self.assert_envelope(response, status="skipped", recovery_status="skipped")
            self.assertEqual(response[2]["data"]["skip_reason"], reason)

    def test_active_empty_profile_is_completed_without_claiming_new_mutations(self):
        self.configure()
        response = self.request()
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="succeeded", recovery_status="completed")
        self.assertIsNone(response[2]["data"]["skip_reason"])
        self.assertTrue(all(response[2]["data"][key] == 0 for key in COUNTERS))

    def test_live_os_lock_returns_busy_without_releasing_reserved_record(self):
        claim = self.reserve()
        with reminder_tick_lock(self.path) as acquired:
            self.assertTrue(acquired)
            response = self.request()
            self.assertEqual(self.store.get(claim.reminder.key).state, "reserved")
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="skipped", recovery_status="skipped")
        self.assertEqual(response[2]["data"]["skip_reason"], "busy")

    def test_reserved_without_potential_send_returns_to_pending_once(self):
        claim = self.reserve()
        first = self.request()
        self.assertEqual(first[0], 200)
        self.assert_envelope(first, status="succeeded", recovery_status="completed")
        self.assertEqual(first[2]["data"]["reservations_recovered"], 1)
        self.assertEqual(self.store.get(claim.reminder.key).state, "pending")
        history = self.store.history(claim.reminder.key)
        second = self.request()
        self.assertEqual(second[2]["data"]["reservations_recovered"], 0)
        self.assertEqual(self.store.history(claim.reminder.key), history)

    def test_sending_becomes_uncertain_and_existing_accepted_is_preserved(self):
        sending = self.reserve("sending-event")
        self.store.mark_sending(sending)
        accepted = self.reserve("accepted-event")
        self.store.mark_sending(accepted)
        self.store.mark_accepted(accepted, "f" * 64)
        response = self.request()
        self.assertEqual(response[0], 200)
        self.assert_envelope(response, status="succeeded", recovery_status="completed")
        self.assertEqual(response[2]["data"]["uncertain_count"], 1)
        self.assertEqual(response[2]["data"]["protected_count"], 1)
        self.assertEqual(self.store.get(sending.reminder.key).state, "uncertain")
        self.assertEqual(self.store.get(accepted.reminder.key).state, "accepted")
        again = self.request()
        self.assertEqual(again[2]["data"]["uncertain_count"], 0)
        self.assertEqual(again[2]["data"]["protected_count"], 2)

    def test_expired_reservation_is_not_released_for_an_immediate_retry(self):
        claim = self.reserve()
        late = claim.reminder.deadline_at + timedelta(seconds=1)
        self.store._clock = lambda: late
        with patch("app.api_reminder_recovery.ReminderRecoveryService",
                   side_effect=lambda store: ReminderRecoveryService(store, clock=lambda: late)):
            response = self.request()
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2]["data"]["expired_count"], 1)
        self.assertEqual(response[2]["data"]["reservations_recovered"], 0)
        self.assertEqual(self.store.get(claim.reminder.key).state, "expired")

    def test_recovery_runs_in_worker_thread(self):
        self.configure()
        main_thread, observed = get_ident(), []
        original = self.store.apply_recovery
        def apply(*args, **kwargs):
            observed.append(get_ident())
            return original(*args, **kwargs)
        with patch.object(self.store, "apply_recovery", side_effect=apply):
            self.assertEqual(self.request()[0], 200)
        self.assertTrue(observed and all(thread != main_thread for thread in observed))

    def test_unexpected_factory_or_service_errors_return_no_imagined_counts(self):
        self.store_factory.side_effect = OSError("raw secret")
        response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed")
        self.assertIsNone(response[2]["data"])
        self.store_factory.side_effect = None
        with patch("app.api_reminder_recovery.ReminderRecoveryService") as service:
            service.return_value.recover.side_effect = RuntimeError("raw secret")
            response = self.request()
        self.assertEqual(response[0], 503)
        self.assertIsNone(response[2]["data"])
        self.assertEqual(response[2]["error"], {
            "code": "reminder_recovery_failed", "retryable": False, "fields": []})

    def test_commit_then_raise_returns_null_data_without_claiming_rollback_or_zero(self):
        claim = self.reserve()
        original = self.store.apply_recovery
        def commit_then_raise(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("raw secret")
        with patch.object(self.store, "apply_recovery", side_effect=commit_then_raise):
            response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed")
        self.assertIsNone(response[2]["data"])
        self.assertEqual(self.store.get(claim.reminder.key).state, "pending")

    def test_known_result_survives_lock_cleanup_failure_in_failed_dto(self):
        claim = self.reserve()
        @contextmanager
        def broken_cleanup(path):
            with reminder_tick_lock(path) as acquired:
                yield acquired
            raise OSError("raw secret")
        with patch("app.services.reminder_recovery.reminder_tick_lock", broken_cleanup):
            response = self.request()
        self.assertEqual(response[0], 503)
        self.assert_envelope(response, status="failed", recovery_status="failed")
        self.assertEqual(response[2]["data"]["reservations_recovered"], 1)
        self.assertEqual(self.store.get(claim.reminder.key).state, "pending")

    def test_invalid_projection_cannot_expose_private_or_contradictory_values(self):
        results = (object(), ReminderRecoverySummary("raw secret"),
            ReminderRecoverySummary("completed", protected_count=True),
            ReminderRecoverySummary("completed", protected_count=-1),
            ReminderRecoverySummary("completed", protected_count=9007199254740992),
            ReminderRecoverySummary("completed", messages_sent=1),
            ReminderRecoverySummary("completed", external_mutations=1),
            ReminderRecoverySummary("skipped", "raw secret"),
            ReminderRecoverySummary("skipped", "busy", uncertain_count=1))
        for result in results:
            with patch("app.api_reminder_recovery.ReminderRecoveryService") as service:
                service.return_value.recover.return_value = result
                response = self.request()
            self.assertEqual(response[0], 503)
            self.assert_envelope(response, status="failed")
            self.assertIsNone(response[2]["data"])

    def test_default_store_factory_is_late_and_receives_application_settings(self):
        app = create_app(config=self.config, db=InjectedDatabase())
        with patch("app.api_reminder_recovery.default_reminder_store", return_value=self.store) as factory:
            factory.assert_not_called()
            response = self.request(app=app)
        self.assertEqual(response[0], 200)
        factory.assert_called_once_with(self.config)

    def test_cancelled_http_wait_keeps_lock_until_real_local_work_finishes(self):
        claim = self.reserve()
        entered, release, finished = Event(), Event(), Event()
        original = self.store.apply_recovery
        def apply(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Teste não liberou o commit local.")
            return original(*args, **kwargs)
        @contextmanager
        def observed_lock(path):
            with reminder_tick_lock(path) as acquired:
                yield acquired
            if acquired:
                finished.set()
        async def scenario():
            first = asyncio.create_task(invoke_asgi(self.app, URL, method="POST", headers=HEADERS, body=b"{}"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                busy = await invoke_asgi(self.app, URL, method="POST", headers=HEADERS, body=b"{}")
                self.assertEqual(busy[2]["data"]["skip_reason"], "busy")
                self.assertEqual(self.store.get(claim.reminder.key).state, "reserved")
            finally:
                release.set()
                if not first.done():
                    first.cancel()
                self.assertTrue(await asyncio.to_thread(finished.wait, 5))
        with patch.object(self.store, "apply_recovery", side_effect=apply), \
             patch("app.services.reminder_recovery.reminder_tick_lock", observed_lock):
            asyncio.run(scenario())
        self.assertEqual(self.store.get(claim.reminder.key).state, "pending")

    def test_startup_does_not_initialize_recovery_or_any_provider(self):
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
