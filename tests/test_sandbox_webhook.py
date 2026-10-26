"""Fronteira pública isolada: autenticação, privacidade e falhas do fluxo local."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.sandbox_webhook import (
    MAX_BODY_BYTES, N8N_URL, create_app, load_allowed_sender, main, make_forwarder,
)
from app.whatsapp_inbox import InboxStore


CALLBACK = "C" * 64
WEBHOOK = "W" * 64
PRIVATE = "PRIVATE_PHONE_TEXT_AND_ID_NOT_FORWARDED"
PHONE = "15555550123"
OTHER_PHONE = "15555550124"
BR_PHONE = "5511955550123"
BR_LEGACY_PHONE = "551155550123"
CONFIG = {
    "callback_token": CALLBACK, "webhook_token": WEBHOOK,
    "workflow_id": "workflow-test", "webhook_credential_id": "credential-test",
}
PATH = "/webhooks/360dialog/" + CALLBACK


def payload():
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": PRIVATE, "profile": {"name": PRIVATE}}],
        "messages": [{
            "id": PRIVATE, "from": PHONE, "timestamp": "1700000000",
            "type": "text", "text": {"body": PRIVATE},
        }],
    }}]}]}


def status_payload():
    return {"entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": PRIVATE, "recipient_id": PHONE,
                      "timestamp": "1700000000", "status": "delivered"}],
    }}]}]}


async def invoke(app, body, *, path=PATH, query=b"", content_type=b"application/json", chunks=None,
                 stall_body=False):
    responses = []
    parts = list(chunks if chunks is not None else [body])
    async def receive():
        if parts:
            return {"type": "http.request", "body": parts.pop(0), "more_body": bool(parts) or stall_body}
        if stall_body:
            await asyncio.Event().wait()
        return {"type": "http.disconnect"}
    async def send(message):
        responses.append(message)
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode("utf-8"), "query_string": query,
        "headers": [(b"content-type", content_type)],
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 12345),
    }
    await app(scope, receive, send)
    code = next(message["status"] for message in responses if message["type"] == "http.response.start")
    raw = b"".join(message.get("body", b"") for message in responses if message["type"] == "http.response.body")
    return code, json.loads(raw)


class SandboxWebhookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.receipts = Path(self.temporary.name) / "data" / "receipts.jsonl"
        self.diagnostics = Path(self.temporary.name) / "data" / "ingress.jsonl"
        self.inbox = self.receipts.parent / "whatsapp-inbox.sqlite3"
        self.forwarded = []
        def forward(summary):
            self.forwarded.append(deepcopy(summary))
            return {"status": "received", "api_reachable": True, "request_id": summary["request_id"], "reply_state": "accepted" if summary["message_count"] else "skipped"}
        self.forward = forward
        self.app = create_app(CONFIG, allowed_sender=PHONE, forward=forward, receipts_path=self.receipts,
                              diagnostics_path=self.diagnostics)

    def diagnostic_rows(self):
        return [json.loads(line) for line in self.diagnostics.read_text().splitlines()]

    def request(self, value=None, *, raw=None, **options):
        body = json.dumps(payload() if value is None else value).encode() if raw is None else raw
        return asyncio.run(invoke(self.app, body, **options))

    def assert_rejected(self, value=None, *, code=400, raw=None, **options):
        status, result = self.request(value, raw=raw, **options)
        self.assertEqual(status, code)
        self.assertEqual(result, {"status": "rejected"})
        self.assertEqual(self.forwarded, [])
        self.assertFalse(self.receipts.exists())
        self.assertFalse(self.inbox.exists())

    def test_message_passes_only_counts_to_n8n_and_receipt(self):
        self.assertEqual(self.request(), (200, {"status": "received"}))
        summary, = self.forwarded
        self.assertEqual(set(summary), {"source", "request_id", "message_count", "status_count", "event_types"})
        self.assertEqual(summary["source"], "360dialog_sandbox")
        self.assertEqual(summary["message_count"], 1)
        self.assertEqual(summary["status_count"], 0)
        self.assertEqual(summary["event_types"], ["message_text"])
        self.assertEqual(str(UUID(summary["request_id"])), summary["request_id"])
        self.assertEqual(json.loads(self.receipts.read_text()), summary)
        for secret in (PRIVATE, PHONE, CALLBACK, WEBHOOK):
            self.assertNotIn(secret, self.receipts.read_text())
            self.assertNotIn(secret, json.dumps(summary))
            self.assertNotIn(secret, self.diagnostics.read_text())
        stages = [row["stage"] for row in self.diagnostic_rows()]
        self.assertEqual(stages, ["received", "succeeded"])
        self.assertTrue(self.diagnostic_rows()[-1]["has_entry"])
        self.assertTrue(self.diagnostic_rows()[-1]["has_messages"])
        self.assertTrue(self.diagnostic_rows()[-1]["all_events_phone_matches_policy"])
        self.assertFalse(self.diagnostic_rows()[-1]["all_events_phone_matches_br_legacy"])

    def use_br_policy(self):
        self.app = create_app(CONFIG, allowed_sender=BR_PHONE, forward=self.forward,
                              receipts_path=self.receipts, diagnostics_path=self.diagnostics)

    def test_legacy_br_relation_is_diagnostic_only_and_remains_forbidden(self):
        self.use_br_policy()
        value = payload()
        message = value["entry"][0]["changes"][0]["value"]["messages"][0]
        message["from"] = BR_LEGACY_PHONE
        message["from_user_id"] = PRIVATE
        self.assert_rejected(value, code=403)
        row = self.diagnostic_rows()[-1]
        self.assertFalse(row["all_events_phone_matches_policy"])
        self.assertTrue(row["all_events_phone_matches_br_legacy"])
        for private in (BR_PHONE, BR_LEGACY_PHONE, PRIVATE):
            self.assertNotIn(private, self.diagnostics.read_text())

    def test_unrelated_phone_has_no_policy_or_br_legacy_relation(self):
        self.use_br_policy()
        self.assert_rejected(code=403)
        row = self.diagnostic_rows()[-1]
        self.assertFalse(row["all_events_phone_matches_policy"])
        self.assertFalse(row["all_events_phone_matches_br_legacy"])

    def test_exact_br_policy_match_is_accepted_without_legacy_relation(self):
        self.use_br_policy()
        value = payload()
        value["entry"][0]["changes"][0]["value"]["messages"][0]["from"] = BR_PHONE
        self.assertEqual(self.request(value)[0], 200)
        row = self.diagnostic_rows()[-1]
        self.assertTrue(row["all_events_phone_matches_policy"])
        self.assertFalse(row["all_events_phone_matches_br_legacy"])
        self.assertNotIn(BR_PHONE, self.diagnostics.read_text())
        self.assertNotIn(BR_PHONE, self.receipts.read_text())

    def test_mixed_exact_and_legacy_batch_reports_both_all_flags_false(self):
        self.use_br_policy()
        value = payload()
        contents = value["entry"][0]["changes"][0]["value"]
        contents["messages"][0]["from"] = BR_PHONE
        contents["statuses"] = status_payload()["entry"][0]["changes"][0]["value"]["statuses"]
        contents["statuses"][0]["recipient_id"] = BR_LEGACY_PHONE
        self.assert_rejected(value, code=403)
        row = self.diagnostic_rows()[-1]
        self.assertFalse(row["all_events_phone_matches_policy"])
        self.assertFalse(row["all_events_phone_matches_br_legacy"])

    def test_status_only_is_acknowledged(self):
        self.assertEqual(self.request(status_payload())[0], 200)
        self.assertEqual(self.forwarded[0]["message_count"], 0)
        self.assertEqual(self.forwarded[0]["status_count"], 1)
        self.assertEqual(self.forwarded[0]["event_types"], ["status_delivered"])
        self.assertTrue(self.diagnostic_rows()[-1]["has_statuses"])
        self.assertFalse(self.diagnostic_rows()[-1]["has_messages"])

    def test_bsuid_alone_never_authorizes_sender(self):
        value = payload()
        message = value["entry"][0]["changes"][0]["value"]["messages"][0]
        message["from_user_id"] = message.pop("from")
        self.assert_rejected(value, code=403)
        self.assertNotIn(PRIVATE, json.dumps(self.forwarded))
        self.assertEqual(self.diagnostic_rows()[-1]["stage"], "forbidden")

    def test_both_sender_identifiers_and_status_recipients_are_accepted_and_stripped(self):
        value = payload()
        contents = value["entry"][0]["changes"][0]["value"]
        contents["messages"][0]["from_user_id"] = PRIVATE
        contents["statuses"] = status_payload()["entry"][0]["changes"][0]["value"]["statuses"]
        contents["statuses"][0]["recipient_user_id"] = PRIVATE
        self.assertEqual(self.request(value)[0], 200)
        self.assertEqual(self.forwarded[0]["message_count"], 1)
        self.assertEqual(self.forwarded[0]["status_count"], 1)
        self.assertNotIn(PRIVATE, json.dumps(self.forwarded))
        self.assertNotIn(PRIVATE, self.receipts.read_text())
        row = self.diagnostic_rows()[-1]
        self.assertEqual(row["sender_key"], "both")
        self.assertTrue(row["firstsender_phone_valid"])
        self.assertTrue(row["firstsender_scoped_valid"])

    def test_optional_null_bsuid_is_allowed_but_null_phone_never_authorizes(self):
        cases = [("messages", "from", "from_user_id", payload),
                 ("statuses", "recipient_id", "recipient_user_id", status_payload)]
        for group, normal, scoped, factory in cases:
            for null_key, valid_key in ((normal, scoped), (scoped, normal)):
                with self.subTest(group=group, null_key=null_key):
                    value = factory()
                    event = value["entry"][0]["changes"][0]["value"][group][0]
                    event[null_key] = None
                    event[valid_key] = PHONE if valid_key == normal else PRIVATE
                    previous = len(self.forwarded)
                    expected = 403 if null_key == normal else 200
                    self.assertEqual(self.request(value)[0], expected)
                    self.assertEqual(len(self.forwarded), previous + (expected == 200))

    def test_any_non_null_invalid_identity_is_rejected_even_with_valid_other_identity(self):
        cases = [("messages", "from", "from_user_id", payload),
                 ("statuses", "recipient_id", "recipient_user_id", status_payload)]
        for group, normal, scoped, factory in cases:
            for bad_key, good_key in ((normal, scoped), (scoped, normal)):
                for invalid in ("", "x" * 1025, 42, False, []):
                    with self.subTest(group=group, bad_key=bad_key, invalid_type=type(invalid).__name__):
                        value = factory()
                        event = value["entry"][0]["changes"][0]["value"][group][0]
                        event[good_key] = PHONE if good_key == normal else PRIVATE
                        event[bad_key] = invalid
                        self.assert_rejected(value)
            value = factory()
            event = value["entry"][0]["changes"][0]["value"][group][0]
            event[normal] = event[scoped] = None
            self.assert_rejected(value)

    def test_multiple_changes_are_counted_once_with_unique_labels(self):
        value = payload()
        changes = value["entry"][0]["changes"]
        changes.append(deepcopy(changes[0]))
        changes.extend(status_payload()["entry"][0]["changes"])
        self.assertEqual(self.request(value)[0], 200)
        self.assertEqual(self.forwarded[0]["message_count"], 1)
        self.assertEqual(self.forwarded[0]["status_count"], 1)
        self.assertEqual(self.forwarded[0]["event_types"], ["message_text", "status_delivered"])

    def test_wrong_and_unicode_tokens_do_not_forward(self):
        for token in ("wrong", "ç" * 64):
            with self.subTest(token=token):
                self.assert_rejected(code=404, path="/webhooks/360dialog/" + token)
        self.assertFalse(self.diagnostics.exists())

    def test_flat_payload_shape_is_recorded_without_expanding_accepted_schema(self):
        flat = payload()["entry"][0]["changes"][0]["value"]
        self.assert_rejected(flat)
        row = self.diagnostic_rows()[-1]
        self.assertEqual(row["stage"], "rejected")
        self.assertEqual(row["http_status"], 400)
        self.assertFalse(row["has_entry"])
        self.assertTrue(row["has_messages"])
        self.assertTrue(row["message_has_timestamp"])
        self.assertEqual(row["timestamp_kind"], "string_digits")
        self.assertEqual(row["firstmessage_type"], "text")
        self.assertEqual(row["sender_key"], "from")
        self.assertNotIn(PRIVATE, self.diagnostics.read_text())
        self.assertNotIn(CALLBACK, self.diagnostics.read_text())
        self.assertNotIn(WEBHOOK, self.diagnostics.read_text())

    def test_diagnostic_message_shape_uses_only_fixed_enums(self):
        cases = [(17, "int"), (PRIVATE, "string_other"), (True, "other"), (None, "other")]
        for timestamp, expected in cases:
            with self.subTest(timestamp_kind=expected):
                flat = payload()["entry"][0]["changes"][0]["value"]
                message = flat["messages"][0]
                message["timestamp"] = timestamp
                message["type"] = PRIVATE
                message["from_user_id"] = PRIVATE
                self.assert_rejected(flat)
                row = self.diagnostic_rows()[-1]
                self.assertEqual(row["timestamp_kind"], expected)
                self.assertEqual(row["firstmessage_type"], "other")
                self.assertEqual(row["sender_key"], "both")
                self.assertNotIn(PRIVATE, self.diagnostics.read_text())

    def test_query_string_is_rejected_without_forwarding(self):
        self.assert_rejected(query=b"secret=never-log")

    def test_non_json_content_type_is_rejected(self):
        self.assert_rejected(code=415, content_type=b"text/plain")

    def test_stream_size_limit_is_checked_without_content_length(self):
        self.assert_rejected(code=413, chunks=[b" " * 40000, b" " * 30000])

    def test_stalled_body_is_timed_out_without_forwarding(self):
        self.assert_rejected(code=408, chunks=[b'{"entry":'], stall_body=True)

    def test_body_at_exact_limit_is_allowed(self):
        raw = json.dumps(payload()).encode()
        self.assertEqual(self.request(raw=raw + b" " * (MAX_BODY_BYTES - len(raw)))[0], 200)

    def test_json_duplicates_nan_invalid_utf8_and_depth_are_rejected(self):
        invalid = [b'{"entry":[],"entry":[]}', b'{"entry":NaN}', b'{"entry":Infinity}',
                   b'{"entry":-Infinity}', b'\xff', b'[]', b'null', b'{broken', b'[' * 1200]
        for body in invalid:
            with self.subTest(body=body[:30]):
                self.assert_rejected(raw=body)

    def test_duplicate_nested_private_key_is_rejected(self):
        raw = json.dumps(payload()).replace('"body":', '"body":"first","body":').encode()
        self.assert_rejected(raw=raw)

    def test_invalid_envelopes_do_not_forward(self):
        invalid = [{}, {"entry": []}, {"entry": [None]}, {"entry": [{"changes": []}]},
                   {"entry": [{"changes": [{"field": "messages", "value": {}}]}]},
                   {"entry": [{"changes": [{"field": "messages", "value": {"messages": {}}}]}]},
                   {"entry": [{"changes": [{"field": "messages", "value": {"messages": []}}]}]}]
        for value in invalid:
            with self.subTest(value=value):
                self.assert_rejected(value)

    def test_malformed_messages_are_rejected(self):
        for field, replacement in (("text", {}), ("type", PRIVATE), ("id", ""),
                                   ("from", None), ("timestamp", True)):
            with self.subTest(field=field):
                value = payload()
                value["entry"][0]["changes"][0]["value"]["messages"][0][field] = replacement
                self.assert_rejected(value)

    def test_malformed_status_is_rejected(self):
        value = status_payload()
        value["entry"][0]["changes"][0]["value"]["statuses"][0]["status"] = PRIVATE
        self.assert_rejected(value)

    def test_more_than_one_hundred_messages_is_rejected(self):
        value = payload()
        messages = value["entry"][0]["changes"][0]["value"]["messages"]
        messages *= 101
        self.assert_rejected(value)

    def test_docs_admin_and_unrelated_api_paths_are_not_exposed(self):
        for path in ("/docs", "/openapi.json", "/redoc", "/", "/api/v1/status", "/admin"):
            with self.subTest(path=path):
                self.assert_rejected(code=404, path=path)

    def test_upstream_faults_return_safe_503_without_receipts(self):
        def failure(_summary):
            raise RuntimeError(PRIVATE + CALLBACK + WEBHOOK)
        self.app = create_app(CONFIG, allowed_sender=PHONE, forward=failure, receipts_path=self.receipts,
                              diagnostics_path=self.diagnostics)
        status, result = self.request()
        self.assertEqual((status, result), (503, {"status": "unavailable"}))
        self.assertFalse(self.receipts.exists())
        self.assertEqual(self.diagnostic_rows()[-1]["stage"], "upstreamfail")
        self.assertEqual(self.diagnostic_rows()[-1]["http_status"], 503)
        self.assertNotIn(PRIVATE, self.diagnostics.read_text())

    def test_upstream_response_must_match_exact_contract(self):
        for index, reply in enumerate(({"status": "received", "api_reachable": True, "request_id": "wrong"},
                      {"status": "received", "api_reachable": 1, "request_id": "wrong"},
                      {"status": "received"}, [], None)):
            with self.subTest(reply=reply):
                self.app = create_app(CONFIG, allowed_sender=PHONE, forward=lambda _s: reply, receipts_path=self.receipts,
                                      diagnostics_path=self.diagnostics)
                value = payload()
                value["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "bad-reply-" + str(index)
                self.assertEqual(self.request(value), (503, {"status": "unavailable"}))
                self.assertFalse(self.receipts.exists())
                self.assertEqual(self.diagnostic_rows()[-1]["stage"], "upstreamfail")

    def test_receipt_storage_failure_preserves_completed_business_result(self):
        self.receipts.parent.mkdir(exist_ok=True)
        self.receipts.mkdir()
        self.assertEqual(self.request(), (200, {"status": "received"}))
        self.assertEqual(self.diagnostic_rows()[-1]["stage"], "receiptfail")
        with closing(sqlite3.connect(self.inbox)) as connection:
            self.assertEqual(connection.execute("SELECT state FROM inbox_events").fetchall(), [("processed",)])
        self.assertEqual(self.request(), (200, {"status": "received"}))
        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(self.diagnostic_rows()[-1]["stage"], "duplicate")

    def test_import_and_factory_never_load_secrets(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("No secret read")), \
             patch("app.whatsapp_inbox.sqlite3.connect", side_effect=AssertionError("No database I/O")):
            create_app(CONFIG, allowed_sender=PHONE, forward=lambda _s: {}, receipts_path=self.receipts,
                       diagnostics_path=self.diagnostics)

    def test_invalid_config_does_not_echo_secret(self):
        value = dict(CONFIG, callback_token=PRIVATE)
        with self.assertRaises(ValueError) as result:
            create_app(value, allowed_sender=PHONE)
        self.assertNotIn(PRIVATE, str(result.exception))

    def test_unknown_phone_is_forbidden_for_messages_and_statuses(self):
        for group, field, factory in (("messages", "from", payload),
                                      ("statuses", "recipient_id", status_payload)):
            with self.subTest(group=group):
                value = factory()
                value["entry"][0]["changes"][0]["value"][group][0][field] = OTHER_PHONE
                self.assert_rejected(value, code=403)
                self.assertEqual(self.diagnostic_rows()[-1]["stage"], "forbidden")
                self.assertNotIn(PHONE, self.diagnostics.read_text())
                self.assertNotIn(OTHER_PHONE, self.diagnostics.read_text())

    def test_entire_mixed_batch_is_forbidden_before_n8n(self):
        for mode in ("messages", "statuses", "message_and_status", "entries"):
            with self.subTest(mode=mode):
                if mode == "statuses":
                    value = status_payload()
                    events = value["entry"][0]["changes"][0]["value"]["statuses"]
                    events.append(dict(events[0], recipient_id=OTHER_PHONE))
                elif mode == "message_and_status":
                    value = payload()
                    values = value["entry"][0]["changes"][0]["value"]
                    values["statuses"] = status_payload()["entry"][0]["changes"][0]["value"]["statuses"]
                    values["statuses"][0]["recipient_id"] = OTHER_PHONE
                elif mode == "entries":
                    value = payload()
                    other = payload()["entry"][0]
                    other["changes"][0]["value"]["messages"][0]["from"] = OTHER_PHONE
                    value["entry"].append(other)
                else:
                    value = payload()
                    events = value["entry"][0]["changes"][0]["value"]["messages"]
                    events.append(dict(events[0], **{"from": OTHER_PHONE}))
                self.assert_rejected(value, code=403)

    def test_contacts_and_bsuid_do_not_override_wrong_message_phone(self):
        value = payload()
        values = value["entry"][0]["changes"][0]["value"]
        values["contacts"][0]["wa_id"] = PHONE
        values["contacts"][0]["user_id"] = PRIVATE
        values["messages"][0]["from"] = OTHER_PHONE
        values["messages"][0]["from_user_id"] = PRIVATE
        self.assert_rejected(value, code=403)

    def test_status_bsuid_alone_never_authorizes(self):
        value = status_payload()
        event = value["entry"][0]["changes"][0]["value"]["statuses"][0]
        event["recipient_user_id"] = event.pop("recipient_id")
        self.assert_rejected(value, code=403)

    def test_malformed_batch_is_400_even_when_earlier_phone_is_unknown(self):
        value = payload()
        events = value["entry"][0]["changes"][0]["value"]["messages"]
        events[0]["from"] = OTHER_PHONE
        events.append(dict(events[0], **{"from": ""}))
        self.assert_rejected(value)
        self.assertEqual(self.diagnostic_rows()[-1]["stage"], "rejected")

    def test_sender_policy_is_required_and_invalid_policy_fails_factory_closed(self):
        with self.assertRaises(TypeError):
            create_app(CONFIG)
        for invalid in (None, "", PRIVATE, "+" + PHONE, "0" + PHONE, PHONE + " ",
                        "123456", "1" * 16, "١٢٣٤٥٦٧٨", 15555550123, True, []):
            with self.subTest(invalid_type=type(invalid).__name__):
                with self.assertRaises(ValueError) as result:
                    create_app(CONFIG, allowed_sender=invalid)
                self.assertEqual(str(result.exception), "Política local de remetente inválida.")
        self.assertFalse(self.receipts.exists())
        self.assertFalse(self.diagnostics.exists())


class SandboxDedupTests(unittest.TestCase):
    """Reenvio, concorrência e falhas usam a inbox SQLite real e isolada."""

    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.receipts = self.folder / "data" / "receipts.jsonl"
        self.diagnostics = self.folder / "data" / "ingress.jsonl"
        self.inbox = self.folder / "data" / "whatsapp-inbox.sqlite3"
        self.forwarded = []
        self.app = self.new_app()

    def forward(self, summary):
        self.forwarded.append(deepcopy(summary))
        return {"status": "received", "api_reachable": True, "request_id": summary["request_id"], "reply_state": "accepted" if summary["message_count"] else "skipped"}

    def new_app(self, **overrides):
        options = dict(allowed_sender=PHONE, forward=self.forward, receipts_path=self.receipts,
                       diagnostics_path=self.diagnostics, inbox_path=self.inbox)
        options.update(overrides)
        return create_app(CONFIG, **options)

    def request(self, value=None, *, app=None, **options):
        return asyncio.run(invoke(self.app if app is None else app,
                                  json.dumps(payload() if value is None else value).encode(), **options))

    def rows(self):
        with closing(sqlite3.connect(self.inbox)) as connection:
            return connection.execute(
                "SELECT event_key,event_type,request_id,state FROM inbox_events ORDER BY event_key"
            ).fetchall()

    def last_stage(self):
        return json.loads(self.diagnostics.read_text().splitlines()[-1])["stage"]

    @staticmethod
    def message(identifier, *, phone=PHONE, kind="text"):
        value = payload()
        event = value["entry"][0]["changes"][0]["value"]["messages"][0]
        event.update(id=identifier, **{"from": phone, "type": kind})
        if kind != "text":
            event.pop("text")
            event[kind] = {"id": PRIVATE}
        return value

    @staticmethod
    def status(identifier, phase, timestamp="1700000000"):
        value = status_payload()
        value["entry"][0]["changes"][0]["value"]["statuses"][0].update(
            id=identifier, status=phase, timestamp=timestamp,
        )
        return value

    @staticmethod
    def batch(*values):
        return {"entry": [entry for value in values for entry in value["entry"]]}

    def test_replay_is_acknowledged_without_forward_or_additional_json_receipt(self):
        value = self.message("provider-replay")
        self.assertEqual(self.request(value), (200, {"status": "received"}))
        receipt = self.receipts.read_bytes()
        self.assertEqual(self.request(value), (200, {"status": "received"}))
        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(self.receipts.read_bytes(), receipt)
        self.assertEqual(self.last_stage(), "duplicate")
        self.assertEqual(self.rows()[0][3], "processed")
        for private in ("provider-replay", PRIVATE, PHONE, CALLBACK, WEBHOOK):
            self.assertNotIn(private.encode(), self.inbox.read_bytes())

    def test_restarted_application_with_second_store_preserves_dedup(self):
        value = self.message("persisted-provider-id")
        self.assertEqual(self.request(value)[0], 200)
        restarted = self.new_app(inbox_store=InboxStore(self.inbox))
        self.assertEqual(self.request(value, app=restarted)[0], 200)
        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_concurrent_instances_forward_once_and_pending_claim_blocks(self):
        entered = Event()
        release = Event()
        def slow_forward(summary):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test timeout")
            return self.forward(summary)
        first = self.new_app(forward=slow_forward)
        second = self.new_app(inbox_store=InboxStore(self.inbox))
        value = self.message("concurrent-provider-id")
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(self.request, value, app=first)
            try:
                self.assertTrue(entered.wait(3))
                other = pool.submit(self.request, value, app=second)
                self.assertEqual(other.result(timeout=3), (503, {"status": "unavailable"}))
            finally:
                release.set()
            self.assertEqual(future.result(timeout=3), (200, {"status": "received"}))
        self.assertEqual(self.request(value, app=second)[0], 200)
        self.assertEqual(len(self.forwarded), 1)

    def test_processed_and_new_events_forward_only_new_counts_and_types(self):
        known = self.message("known")
        fresh = self.status("fresh", "read")
        self.assertEqual(self.request(known)[0], 200)
        mixed = self.batch(known, fresh, fresh)
        self.assertEqual(self.request(mixed)[0], 200)
        summary = self.forwarded[-1]
        self.assertEqual(summary["message_count"], 0)
        self.assertEqual(summary["status_count"], 1)
        self.assertEqual(summary["event_types"], ["status_read"])
        self.assertNotEqual(self.forwarded[0]["request_id"], summary["request_id"])
        self.assertEqual(self.request(mixed)[0], 200)
        self.assertEqual(len(self.forwarded), 2)
        self.assertEqual(len(self.rows()), 2)

    def test_repeated_id_inside_batch_is_one_event_even_if_message_type_changes(self):
        value = self.batch(self.message("one-id"), self.message("one-id", kind="image"),
                           self.message("second-id"))
        self.assertEqual(self.request(value)[0], 200)
        self.assertEqual(self.forwarded[0]["message_count"], 2)
        self.assertEqual(self.forwarded[0]["event_types"], ["message_text"])
        self.assertEqual(len(self.rows()), 2)

    def test_status_phases_are_independent_out_of_order_and_ignore_timestamp(self):
        for phase in ("read", "delivered", "sent"):
            with self.subTest(phase=phase):
                self.assertEqual(self.request(self.status("shared-id", phase))[0], 200)
                self.assertEqual(self.request(self.status("shared-id", phase, "1800000000"))[0], 200)
        self.assertEqual(len(self.forwarded), 3)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual([row["event_types"] for row in self.forwarded],
                         [["status_read"], ["status_delivered"], ["status_sent"]])

    def test_message_and_status_with_same_provider_id_have_separate_identity(self):
        self.assertEqual(self.request(self.message("shared-id"))[0], 200)
        self.assertEqual(self.request(self.status("shared-id", "delivered"))[0], 200)
        self.assertEqual(len(self.forwarded), 2)
        self.assertEqual(len(self.rows()), 2)

    def test_same_text_with_different_provider_ids_is_a_new_message(self):
        for identifier in ("first-real-message", "second-real-message"):
            self.assertEqual(self.request(self.message(identifier))[0], 200)
        self.assertEqual(len(self.forwarded), 2)
        self.assertEqual(len(self.rows()), 2)

    def test_changed_message_type_with_same_id_remains_duplicate_after_restart(self):
        self.assertEqual(self.request(self.message("stable-id"))[0], 200)
        self.assertEqual(self.request(self.message("stable-id", kind="image"), app=self.new_app())[0], 200)
        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(self.rows()[0][1], "message_text")

    def test_entire_batch_is_authorized_before_any_hash_or_database_poisoning(self):
        value = self.batch(self.message("shared-id"), self.message("unknown", phone=OTHER_PHONE))
        with patch("app.sandbox_webhook.make_event_key", side_effect=AssertionError("must not hash")):
            self.assertEqual(self.request(value), (403, {"status": "rejected"}))
        self.assertFalse(self.inbox.exists())
        self.assertEqual(self.forwarded, [])
        self.assertEqual(self.request(self.message("shared-id"))[0], 200)
        self.assertEqual(len(self.rows()), 1)

    def test_replay_from_wrong_sender_is_still_forbidden_before_inbox_lookup(self):
        self.assertEqual(self.request(self.message("known-id"))[0], 200)
        before = self.rows()
        with patch.object(InboxStore, "claim", side_effect=AssertionError("must not claim")):
            self.assertEqual(self.request(self.message("known-id", phone=OTHER_PHONE)),
                             (403, {"status": "rejected"}))
        self.assertEqual(self.rows(), before)
        self.assertEqual(len(self.forwarded), 1)

    def test_invalid_later_event_is_rejected_before_any_hash(self):
        invalid = self.message("malformed")
        invalid["entry"][0]["changes"][0]["value"]["messages"][0].pop("timestamp")
        with patch("app.sandbox_webhook.make_event_key", side_effect=AssertionError("must not hash")):
            self.assertEqual(self.request(self.batch(self.message("valid"), invalid))[0], 400)
        self.assertFalse(self.inbox.exists())
        self.assertEqual(self.forwarded, [])

    def test_lost_forward_response_becomes_uncertain_and_restart_blocks_retry(self):
        def lost_response(summary):
            self.forwarded.append(deepcopy(summary))
            raise TimeoutError(PRIVATE)
        value = self.message("lost-response")
        self.assertEqual(self.request(value, app=self.new_app(forward=lost_response)),
                         (503, {"status": "unavailable"}))
        self.assertEqual(self.rows()[0][3], "uncertain")
        self.assertEqual(self.request(value, app=self.new_app()), (503, {"status": "unavailable"}))
        self.assertEqual(len(self.forwarded), 1)
        self.assertFalse(self.receipts.exists())
        self.assertEqual(self.last_stage(), "pending")
        self.assertNotIn(PRIVATE, self.diagnostics.read_text())

    def test_pending_event_blocks_entire_mixed_batch_without_claiming_fresh_event(self):
        def failed(_summary):
            raise TimeoutError()
        pending = self.message("pending")
        self.assertEqual(self.request(pending, app=self.new_app(forward=failed))[0], 503)
        self.assertEqual(self.request(self.batch(pending, self.message("fresh")))[0], 503)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.forwarded, [])
        self.assertEqual(self.request(self.message("fresh"))[0], 200)
        self.assertEqual(len(self.rows()), 2)

    def test_failed_completion_marks_uncertain_and_does_not_repeat_forward(self):
        store = InboxStore(self.inbox)
        app = self.new_app(inbox_store=store)
        value = self.message("failed-completion")
        with patch.object(store, "complete", side_effect=OSError(PRIVATE)):
            self.assertEqual(self.request(value, app=app)[0], 503)
        self.assertEqual(self.rows()[0][3], "uncertain")
        self.assertEqual(self.request(value, app=self.new_app())[0], 503)
        self.assertEqual(len(self.forwarded), 1)
        self.assertFalse(self.receipts.exists())

    def test_failed_uncertain_write_retains_processing_and_blocks_restart(self):
        store = InboxStore(self.inbox)
        def failed(_summary):
            raise TimeoutError()
        app = self.new_app(inbox_store=store, forward=failed)
        value = self.message("processing-remains")
        with patch.object(store, "mark_uncertain", side_effect=OSError(PRIVATE)):
            self.assertEqual(self.request(value, app=app)[0], 503)
        self.assertEqual(self.rows()[0][3], "processing")
        self.assertEqual(self.request(value, app=self.new_app())[0], 503)
        self.assertEqual(self.forwarded, [])

    def test_unavailable_database_returns_generic_503_without_forwarding(self):
        self.inbox.parent.mkdir(parents=True)
        self.inbox.mkdir()
        self.assertEqual(self.request(self.message("database-unavailable")),
                         (503, {"status": "unavailable"}))
        self.assertEqual(self.forwarded, [])
        self.assertFalse(self.receipts.exists())
        self.assertEqual(self.last_stage(), "inboxfail")

    def test_lifespan_initializes_inbox_before_serving_and_failure_is_generic(self):
        store = MagicMock()
        app = self.new_app(inbox_store=store)
        async def startup():
            async with app.router.lifespan_context(app):
                store.initialize.assert_called_once_with()
        asyncio.run(startup())
        store.initialize.reset_mock()
        store.initialize.side_effect = OSError(PRIVATE)
        with self.assertRaisesRegex(RuntimeError, "Não foi possível inicializar a inbox local do sandbox") as error:
            asyncio.run(startup())
        self.assertNotIn(PRIVATE, str(error.exception))
        self.assertFalse(self.inbox.exists())


class SenderPolicyStartupTests(unittest.TestCase):
    def test_policy_file_has_exact_key_and_e164_ascii_boundaries(self):
        for phone in ("1234567", PHONE, "123456789012345"):
            self.assertEqual(load_allowed_sender(json.dumps({"allowed_phone": phone}).encode()), phone)

    def test_policy_json_duplicates_nan_extra_keys_and_bad_values_fail_safely(self):
        invalid = [b"{}", b"[]", b"null", b"{broken", b"\xff",
                   b'{"allowed_phone":NaN}',
                   ('{"allowed_phone":"' + PHONE + '","allowed_phone":"' + PHONE + '"}').encode(),
                   json.dumps({"allowed_phone": PHONE, "extra": PRIVATE}).encode()]
        for phone in (None, "", PRIVATE, "+" + PHONE, "0" + PHONE, PHONE + " ",
                      "123456", "1" * 16, "١٢٣٤٥٦٧٨", 15555550123, True, []):
            invalid.append(json.dumps({"allowed_phone": phone}).encode())
        for raw in invalid:
            with self.subTest(raw_kind=raw[:15]):
                with self.assertRaises(ValueError) as result:
                    load_allowed_sender(raw)
                self.assertEqual(str(result.exception), "Política local de remetente inválida.")

    def test_missing_or_malformed_policy_prevents_server_start(self):
        for policy in (OSError(PRIVATE), b'{"allowed_phone":null}', b'{"extra":"PRIVATE"}'):
            with self.subTest(policy_type=type(policy).__name__):
                with patch.object(Path, "read_bytes", side_effect=[json.dumps(CONFIG).encode(), policy]), \
                     patch("uvicorn.run") as server:
                    with self.assertRaises(SystemExit) as result:
                        main()
                    server.assert_not_called()
                    self.assertEqual(str(result.exception), "Não foi possível carregar a configuração local do sandbox.")

    def test_main_loads_explicit_private_policy_before_starting_local_server(self):
        raw_policy = json.dumps({"allowed_phone": PHONE}).encode()
        with patch.object(Path, "read_bytes", side_effect=[json.dumps(CONFIG).encode(), raw_policy]), \
             patch("uvicorn.run") as server:
            main()
        server.assert_called_once()
        self.assertEqual(server.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(server.call_args.kwargs["port"], 8787)


class FixedForwarderTests(unittest.TestCase):
    def test_only_fixed_endpoint_with_no_proxy_redirect_retry_and_three_second_timeout(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.post.return_value.status_code = 200
        session.post.return_value.content = b'{"status":"received"}'
        with patch("app.sandbox_webhook.requests.Session", return_value=session):
            make_forwarder(WEBHOOK)({"source": "test"})
        self.assertIs(session.trust_env, False)
        session.post.assert_called_once_with(
            N8N_URL, json={"source": "test"}, headers={"X-Genius-Webhook-Key": WEBHOOK},
            timeout=(2, 20), allow_redirects=False,
        )
        adapter = session.mount.call_args.args[1]
        self.assertEqual(adapter.max_retries.total, 0)

    def test_redirect_and_non_200_upstream_are_never_accepted(self):
        for status in (201, 302, 401, 500):
            with self.subTest(status=status):
                session = MagicMock()
                session.__enter__.return_value = session
                session.post.return_value.status_code = status
                with patch("app.sandbox_webhook.requests.Session", return_value=session):
                    with self.assertRaises(ValueError):
                        make_forwarder(WEBHOOK)({})


if __name__ == "__main__":
    unittest.main()
