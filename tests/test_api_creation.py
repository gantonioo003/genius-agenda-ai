"""Criação HTTP autenticada com domínio real e calendário inteiramente local."""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from io import StringIO
import json
import logging
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import RefreshError, TransportError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app import google_calendar
from app.database import Database
from app.google_transport import SingleAttemptHttp
from app.main import create_app
from app.services.conflicts import CalendarConflictError, ConflictReport
from app.services.creation_identity import creation_content_hash
from app.services.event_details import EventDetails
from app.services.interviews import CalendarOperationError, CreatedInterview, InterviewDraft
from app.settings import Settings
from calendar_http_fake import CalendarHttpFake
from test_api_security import InjectedDatabase, invoke_asgi


TOKEN = "CreationHttpToken_ForTests_0123456789AbCd"
PRIVATE = "PRIVATE_CREATION_CANARY_DoNotEcho"
ZONE = "America/Recife"
OPERATION = "creation-http-intent-20300617-001"
CREATED_FIELDS = {"event_id", "html_link", "calendar_name", "reused", "recovered"}
EVENT_FIELDS = {"event_id", "title", "start", "end", "all_day", "time_zone", "status", "html_link"}


def valid_body():
    return {
        "operation_id": OPERATION,
        "event": {
            "title": "Entrevista técnica de demonstração",
            "start": "2030-06-17T10:00:00-03:00",
            "end": "2030-06-17T10:30:00-03:00",
            "time_zone": ZONE,
            "description": PRIVATE,
        },
    }


def result(**changes):
    return replace(CreatedInterview(
        "GeniusCaseSensitiveID", "https://example.invalid/event", "Genius Testes",
    ), **changes)


def conflict_event():
    return EventDetails(
        event_id="OtherInterviewCaseSensitiveID", title="Entrevista já marcada",
        start="2030-06-17T10:00:00-03:00", end="2030-06-17T10:30:00-03:00",
        all_day=False, time_zone=ZONE, status="confirmed",
        html_link="https://example.invalid/conflict", description=PRIVATE, location=PRIVATE,
    )


class CreationGateway:
    def __init__(self):
        self.calls = []
        self.result = result()
        self.failure = None

    def create_event(self, draft, *, operation_id):
        self.calls.append((draft, operation_id))
        if self.failure is not None:
            raise self.failure
        return self.result


class CreationFactory:
    def __init__(self, gateway):
        self.gateway = gateway
        self.opened = 0
        self.closed = 0
        self.open_failure = None
        self.close_failure = None

    @contextmanager
    def __call__(self):
        self.opened += 1
        try:
            if self.open_failure is not None:
                raise self.open_failure
            yield self.gateway
        finally:
            self.closed += 1
            if self.close_failure is not None:
                raise self.close_failure


class ApiCreationTests(unittest.TestCase):
    def setUp(self):
        self.config = Settings(_env_file=None, app_name="Agenda de teste HTTP", app_env="test", api_auth_token=TOKEN)
        self.db = InjectedDatabase()
        self.gateway = CreationGateway()
        self.factory = CreationFactory(self.gateway)
        self.app = create_app(config=self.config, db=self.db, calendar_gateway_factory=self.factory)
        self.logs = StringIO()
        logger = logging.getLogger()
        handler = logging.StreamHandler(self.logs)
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        self.addCleanup(logger.setLevel, previous_level)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(self.check_logs)

    def check_logs(self):
        self.assertNotIn(TOKEN, self.logs.getvalue())
        self.assertNotIn(PRIVATE, self.logs.getvalue())

    def request(self, body=None, *, raw_body=None, authorized=True, query=(), app=None,
                content_type=b"application/json", extra_headers=()):
        if raw_body is None:
            body = valid_body() if body is None else body
            raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = [(b"x-request-id", b"creation-test-001"), (b"content-type", content_type)]
        if authorized:
            headers.append((b"authorization", f"Bearer {TOKEN}".encode("ascii")))
        response = asyncio.run(invoke_asgi(
            self.app if app is None else app, "/api/v1/events", method="POST",
            headers=(*headers, *extra_headers), body=raw_body, query=urlencode(query).encode("ascii"),
        ))
        safe_output = json.dumps(response, ensure_ascii=False)
        self.assertNotIn(TOKEN, safe_output)
        self.assertNotIn(PRIVATE, safe_output)
        return response

    def assert_envelope(self, response, http_status, *, operation_id=OPERATION, status="succeeded",
                        mutation_state="confirmed", error=None, fields=()):
        code, headers, payload = response
        self.assertEqual(code, http_status)
        self.assertEqual(set(payload), {
            "api_version", "request_id", "operation_id", "status", "action", "mutation_state", "message", "data", "error",
        })
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], "creation-test-001")
        self.assertEqual(headers["x-request-id"], "creation-test-001")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(payload["operation_id"], operation_id)
        self.assertEqual(payload["action"], "create")
        self.assertEqual(payload["status"], status)
        self.assertEqual(payload["mutation_state"], mutation_state)
        if error is None:
            self.assertIsNone(payload["error"])
        else:
            self.assertEqual(payload["error"], {"code": error, "retryable": False, "fields": list(fields)})
        return payload["data"]

    def test_new_reused_and_recovered_creation_have_confirmed_distinct_http_outcomes(self):
        for reused, recovered, code in ((False, False, 201), (True, False, 200), (False, True, 201)):
            with self.subTest(reused=reused, recovered=recovered):
                self.gateway.result = result(reused=reused, recovered=recovered)
                data = self.assert_envelope(self.request(), code)
                self.assertEqual(set(data), CREATED_FIELDS)
                self.assertEqual(data["reused"], reused)
                self.assertEqual(data["recovered"], recovered)
                self.assertEqual(data["event_id"], "GeniusCaseSensitiveID")
                draft, supplied_operation = self.gateway.calls[-1]
                self.assertIsInstance(draft, InterviewDraft)
                self.assertEqual(supplied_operation, OPERATION)
                self.assertEqual(draft.start, datetime.fromisoformat(valid_body()["event"]["start"]))
                self.assertEqual(draft.description, PRIVATE)
        self.assertEqual(self.factory.opened, 3)
        self.assertEqual(self.factory.closed, 3)
        self.assertEqual(self.db.initializations, 0)

    def test_operation_identity_is_preserved_exactly_and_omitted_description_defaults_only_when_absent(self):
        for operation in ("A", "  Intenção.CaseSensitive-2030  ", "á" * 128):
            with self.subTest(operation_length=len(operation)):
                body = valid_body()
                body["operation_id"] = operation
                body["event"].pop("description")
                self.assert_envelope(self.request(body), 201, operation_id=operation)
                draft, actual_operation = self.gateway.calls[-1]
                self.assertEqual(actual_operation, operation)
                self.assertEqual(draft.description, "")

    def test_public_creation_dto_never_serializes_subclass_private_fields(self):
        @dataclass(frozen=True)
        class ProviderDetails(CreatedInterview):
            description: str = PRIVATE
            location: str = PRIVATE
            internal_diagnostic: str = PRIVATE

        self.gateway.result = ProviderDetails(**result().__dict__)
        data = self.assert_envelope(self.request(), 201)
        self.assertEqual(set(data), CREATED_FIELDS)
        self.assertEqual(self.factory.closed, 1)

    def test_invalid_structure_and_strict_field_types_are_rejected_before_provider_access(self):
        cases = []
        for field, values in (
            ("operation_id", (None, 1, True, "", "  \t\n", "x" * 129)),
            ("event", (None, [], "text", 1)),
        ):
            missing = valid_body()
            missing.pop(field)
            cases.append((missing, field))
            for value in values:
                body = valid_body()
                body[field] = value
                cases.append((body, field))
        for field, values in (
            ("title", (None, 1, True, "", "  \t\n")),
            ("start", (None, 123, True, PRIVATE)),
            ("end", (None, 123, True, PRIVATE)),
            ("time_zone", (None, 1, True, "", PRIVATE)),
            ("description", (None, 1, True, [])),
        ):
            if field != "description":
                missing = valid_body()
                missing["event"].pop(field)
                cases.append((missing, field))
            for value in values:
                body = valid_body()
                body["event"][field] = value
                cases.append((body, field))
        for number, (body, field) in enumerate(cases):
            with self.subTest(case=number, field=field):
                response = self.request(body)
                self.assertEqual(response[0], 422)
                self.assertEqual(response[2]["action"], "create")
                self.assertEqual(response[2]["mutation_state"], "not_applied")
                self.assertEqual(response[2]["error"], {
                    "code": "invalid_request", "retryable": False, "fields": [field],
                })
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_datetime_offsets_positive_interval_and_zone_consistency_reuse_domain_rules(self):
        cases = (
            ({"start": "2030-06-17T10:00:00"}, "start"),
            ({"end": "2030-06-17T10:30:00"}, "end"),
            ({"start": "2030-02-30T10:00:00-03:00"}, "start"),
            ({"end": "2030-06-17T10:00:00-03:00"}, "end"),
            ({"end": "2030-06-17T09:00:00-03:00"}, "end"),
            ({"start": "2030-06-17T10:00:00+00:00"}, "start"),
            ({"end": "2030-06-17T10:30:00+00:00"}, "end"),
            ({"time_zone": " America/Recife"}, "time_zone"),
            ({"time_zone": "America/New_York", "start": "2030-03-10T02:00:00-05:00", "end": "2030-03-10T03:30:00-04:00"}, "start"),
        )
        for number, (changes, field) in enumerate(cases):
            with self.subTest(case=number, field=field):
                body = valid_body()
                body["event"].update(changes)
                response = self.request(body)
                self.assertEqual(response[0], 422)
                self.assertEqual(response[2]["mutation_state"], "not_applied")
                self.assertEqual(response[2]["error"]["code"], "invalid_request")
                self.assertTrue(response[2]["error"]["fields"])
        self.assertEqual(self.factory.opened, 0)
        body = valid_body()
        body["event"].update(time_zone="America/New_York", start="2030-11-03T01:30:00-04:00", end="2030-11-03T01:15:00-05:00")
        self.assert_envelope(self.request(body), 201)
        self.assertEqual(self.factory.closed, 1)

    def test_unknown_top_event_and_query_fields_do_not_echo_names_or_values_or_open_provider(self):
        body_extra = valid_body()
        body_extra[PRIVATE] = PRIVATE
        event_extra = valid_body()
        event_extra["event"][PRIVATE] = PRIVATE
        for body, query, expected in (
            (body_extra, (), "body"),
            (event_extra, (), "event"),
            (valid_body(), ((PRIVATE, PRIVATE),), "query"),
            (valid_body(), (("calendar_id", PRIVATE),), "query"),
            (valid_body(), (("user_id", PRIVATE),), "query"),
        ):
            with self.subTest(field=expected):
                response = self.request(body, query=query)
                self.assert_envelope(response, 422, status="failed", mutation_state="not_applied", error="invalid_request", fields=(expected,))
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_malformed_json_non_objects_and_non_utf8_body_are_safe_without_factory_access(self):
        cases = (
            b"", b"{", ('{"' + PRIVATE + '":').encode(), b"null", b"[]", b"true", b"42", b'"text"',
            b'{"event":"\xff"}',
        )
        for number, raw in enumerate(cases):
            with self.subTest(case=number):
                response = self.request(raw_body=raw)
                self.assertEqual(response[0], 422)
                self.assertEqual(response[2]["action"], "create")
                self.assertEqual(response[2]["mutation_state"], "not_applied")
                self.assertEqual(response[2]["error"]["code"], "invalid_request")
        self.assertEqual(self.factory.opened, 0)

    def test_unpaired_unicode_is_invalid_before_provider_and_valid_operation_is_retained_on_validation_failure(self):
        for field in ("operation_id", "title", "description", "start", "end", "time_zone"):
            with self.subTest(field=field):
                body = valid_body()
                if field == "operation_id":
                    body[field] = "invalid-\ud800"
                else:
                    body["event"][field] = "invalid-\ud800"
                raw = json.dumps(body, ensure_ascii=True).encode("ascii")
                response = self.request(raw_body=raw)
                self.assert_envelope(
                    response, 422, operation_id=None if field == "operation_id" else OPERATION,
                    status="failed", mutation_state="not_applied", error="invalid_request", fields=(field,),
                )
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_unauthorized_or_unconfigured_api_never_opens_creation_provider(self):
        response = self.request(authorized=False)
        self.assertEqual(response[0], 401)
        self.assertEqual(response[1]["www-authenticate"], "Bearer")
        self.assertIsNone(response[2]["action"])
        self.assertIsNone(response[2]["operation_id"])
        self.assertEqual(response[2]["mutation_state"], "not_applied")
        no_key = Settings(_env_file=None, api_auth_token=None)
        app = create_app(config=no_key, db=self.db, calendar_gateway_factory=self.factory)
        response = self.request(app=app)
        self.assertEqual(response[0], 503)
        self.assertEqual(response[2]["error"]["code"], "api_auth_unavailable")
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_conflict_is_typed_not_applied_with_only_minimal_public_event_fields(self):
        event = conflict_event()
        report = ConflictReport("conflict", "Genius Testes", event.start, event.end, ZONE, (event,))
        self.gateway.failure = CalendarConflictError(report)
        data = self.assert_envelope(
            self.request(), 409, status="conflict", mutation_state="not_applied", error="schedule_conflict",
        )
        self.assertEqual(set(data), {"status", "calendar_name", "start", "end", "time_zone", "conflicts"})
        self.assertEqual(data["status"], "conflict")
        self.assertEqual(len(data["conflicts"]), 1)
        self.assertEqual(set(data["conflicts"][0]), EVENT_FIELDS)
        self.assertEqual(data["conflicts"][0]["event_id"], event.event_id)
        self.assertEqual(self.factory.closed, 1)

    def test_setup_before_creation_and_uncertain_failures_have_different_mutation_states(self):
        self.factory.open_failure = google_calendar.CalendarSetupError(PRIVATE)
        self.assert_envelope(
            self.request(), 503, status="failed", mutation_state="not_applied", error="calendar_setup_unavailable",
        )
        self.assertEqual(self.gateway.calls, [])
        self.factory.open_failure = None
        for failure in (
            CalendarOperationError(PRIVATE), ValueError(PRIVATE), OSError(PRIVATE),
            RefreshError(PRIVATE), TransportError(PRIVATE), sqlite3.OperationalError(PRIVATE),
            google_calendar.CalendarSetupError(PRIVATE),
        ):
            with self.subTest(exception=type(failure).__name__):
                self.gateway.failure = failure
                data = self.assert_envelope(
                    self.request(), 502, status="failed", mutation_state="unknown", error="operation_not_confirmed",
                )
                self.assertEqual(data, {"event_id": None})
        self.assertEqual(self.factory.opened, self.factory.closed)

    def test_cleanup_failures_cannot_downgrade_a_sent_creation_to_not_applied(self):
        for failure in (OSError(PRIVATE), google_calendar.CalendarSetupError(PRIVATE)):
            with self.subTest(exception=type(failure).__name__):
                self.factory.close_failure = failure
                self.assert_envelope(
                    self.request(), 502, status="failed", mutation_state="unknown", error="operation_not_confirmed",
                )
        self.assertEqual(len(self.gateway.calls), 2)
        self.assertEqual(self.factory.closed, 2)

    def test_invalid_provider_result_is_uncertain_and_does_not_claim_confirmed_creation(self):
        bad_results = (
            None, {}, result(event_id=""), result(html_link=""), result(calendar_name=""),
            result(reused=1), result(recovered="yes"), result(event_id="bad-\ud800"),
            result(html_link="bad-\ud800"), result(calendar_name="bad-\ud800"),
        )
        for number, bad in enumerate(bad_results):
            with self.subTest(case=number):
                self.gateway.result = bad
                self.assert_envelope(
                    self.request(), 502, status="failed", mutation_state="unknown", error="operation_not_confirmed",
                )
        self.assertEqual(self.factory.opened, self.factory.closed)

    def test_inconsistent_conflict_and_cleanup_conflict_cannot_claim_not_applied(self):
        event = conflict_event()
        valid = ConflictReport("conflict", "Genius Testes", event.start, event.end, ZONE, (event,))
        outside = replace(event, start="2030-06-17T12:00:00-03:00", end="2030-06-17T12:30:00-03:00")
        for report in (
            replace(valid, conflicts=()), replace(valid, status="available"),
            replace(valid, conflicts=(event, event)), replace(valid, conflicts=(outside,)),
            replace(valid, start="2030-06-17T09:00:00-03:00"),
        ):
            with self.subTest(status=report.status, conflict_count=len(report.conflicts)):
                self.gateway.failure = CalendarConflictError(report)
                self.assert_envelope(
                    self.request(), 502, status="failed", mutation_state="unknown", error="operation_not_confirmed",
                )
        self.gateway.failure = None
        self.factory.close_failure = CalendarConflictError(valid)
        self.assert_envelope(
            self.request(), 502, status="failed", mutation_state="unknown", error="operation_not_confirmed",
        )
        self.assertEqual(self.factory.opened, self.factory.closed)

    def test_openapi_documents_bearer_strict_creation_body_and_failure_responses(self):
        schema = self.app.openapi()
        operation = schema["paths"]["/api/v1/events"]["post"]
        self.assertIn({"ApiKeyBearer": []}, operation["security"])
        self.assertTrue({"200", "201", "409", "422", "502", "503"}.issubset(operation["responses"]))
        body = operation["requestBody"]
        self.assertTrue(body["required"])
        root_ref = body["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        root = schema["components"]["schemas"][root_ref]
        self.assertFalse(root["additionalProperties"])
        self.assertEqual(set(root["required"]), {"operation_id", "event"})
        self.assertEqual(root["properties"]["operation_id"]["type"], "string")
        self.assertEqual(root["properties"]["operation_id"]["maxLength"], 128)
        event_ref = root["properties"]["event"]["$ref"].split("/")[-1]
        event_schema = schema["components"]["schemas"][event_ref]
        self.assertFalse(event_schema["additionalProperties"])
        self.assertEqual(set(event_schema["required"]), {"title", "start", "end", "time_zone"})
        self.assertNotIn(TOKEN, json.dumps(schema))

    @contextmanager
    def sdk_app(self, backend):
        sessions = []
        counts = {"opened": 0, "closed": 0}

        def local_session():
            session = backend.session()
            sessions.append(session)
            return session

        @contextmanager
        def sdk_factory():
            counts["opened"] += 1
            transport = SingleAttemptHttp(timeout=20)
            http = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
            sdk = build("calendar", "v3", http=http, cache_discovery=False, static_discovery=True)
            try:
                yield google_calendar.GoogleCalendarGateway(sdk)
            finally:
                sdk.close()
                counts["closed"] += 1

        with TemporaryDirectory() as temporary:
            db = Database(Path(temporary) / "creation-ledger.sqlite3")
            settings = SimpleNamespace(google_calendar_name="Genius Testes", google_calendar_id=backend.calendar["id"])
            with patch("app.google_transport.requests.Session", side_effect=local_session), \
                    patch.object(google_calendar, "database", db), \
                    patch.object(google_calendar, "settings", settings), \
                    patch.object(google_calendar, "get_credentials", side_effect=AssertionError("OAuth real proibido.")), \
                    patch("googleapiclient.http.time.sleep"):
                app = create_app(config=self.config, db=InjectedDatabase(), calendar_gateway_factory=sdk_factory)
                yield app, db, counts
        self.assertEqual(counts["opened"], counts["closed"])
        self.assertTrue(sessions)
        self.assertTrue(all(session.closed for session in sessions))

    def test_http_sdk_sqlite_deduplicate_identical_intents_and_reject_changed_identity(self):
        backend = CalendarHttpFake()
        with self.sdk_app(backend) as (app, db, counts):
            created = self.assert_envelope(self.request(app=app), 201)
            first_id = created["event_id"]
            self.assertEqual(first_id, "genius" + sha256(OPERATION.encode("utf-8")).hexdigest())
            snapshot = deepcopy(backend.saved)
            repeated = self.assert_envelope(self.request(app=app), 200)
            self.assertTrue(repeated["reused"])
            body = valid_body()
            body["operation_id"] = OPERATION + "-second-intent"
            reused = self.assert_envelope(self.request(body, app=app), 200, operation_id=body["operation_id"])
            self.assertEqual(reused["event_id"], first_id)
            self.assertTrue(reused["reused"])
            self.assertEqual(backend.saved, snapshot)
            writes = [call for call in backend.calls if call["method"] != "GET"]
            self.assertEqual([call["method"] for call in writes], ["POST"])
            self.assertEqual(writes[0]["query"].get("sendUpdates"), "none")
            self.assertEqual(writes[0]["body"]["visibility"], "private")
            self.assertEqual(writes[0]["body"]["reminders"], {"useDefault": False})
            body["operation_id"] = OPERATION
            body["event"]["title"] = "Outra intenção com identidade indevidamente reutilizada"
            self.assert_envelope(self.request(body, app=app), 502, status="failed", mutation_state="unknown", error="operation_not_confirmed")
            self.assertEqual(len([call for call in backend.calls if call["method"] == "POST"]), 1)
            request = valid_body()["event"]
            request_hash = creation_content_hash(request["title"], request["description"], datetime.fromisoformat(request["start"]), datetime.fromisoformat(request["end"]))
            persisted = Database(db.path).get_creation(backend.calendar["id"], request_hash)
            self.assertEqual(persisted.state, "confirmed")
            self.assertEqual(persisted.event_id, first_id)
            with db.connection() as connection:
                operations = connection.execute("SELECT operation_hash, request_hash FROM creation_operations").fetchall()
            self.assertEqual(len(operations), 2)
            self.assertEqual({row["operation_hash"] for row in operations}, {
                sha256(OPERATION.encode()).hexdigest(), sha256((OPERATION + "-second-intent").encode()).hexdigest(),
            })
            self.assertEqual(counts["opened"], 4)

    def test_http_sdk_conflict_blocks_post_and_preserves_existing_event(self):
        backend = CalendarHttpFake()
        backend.saved["ExistingConflictID"] = {
            "id": "ExistingConflictID", "summary": "Entrevista já existente", "status": "confirmed", "etag": '"v1"',
            "description": PRIVATE, "location": PRIVATE,
            "start": {"dateTime": "2030-06-17T10:00:00-03:00", "timeZone": ZONE},
            "end": {"dateTime": "2030-06-17T10:30:00-03:00", "timeZone": ZONE},
        }
        snapshot = deepcopy(backend.saved)
        with self.sdk_app(backend) as (app, db, _counts):
            data = self.assert_envelope(self.request(app=app), 409, status="conflict", mutation_state="not_applied", error="schedule_conflict")
            self.assertEqual(data["conflicts"][0]["event_id"], "ExistingConflictID")
            self.assertEqual(backend.saved, snapshot)
            self.assertTrue(all(call["method"] == "GET" for call in backend.calls))
            with db.connection() as connection:
                row = connection.execute("SELECT state FROM creation_requests").fetchone()
            self.assertEqual(row["state"], "pending")

    def test_http_sdk_lost_response_is_reconciled_before_success_without_second_post(self):
        backend = CalendarHttpFake()
        backend.lose_response_once.add("POST")
        with self.sdk_app(backend) as (app, _db, _counts):
            data = self.assert_envelope(self.request(app=app), 201)
            self.assertTrue(data["recovered"])
            self.assertFalse(data["reused"])
            self.assertIn(data["event_id"], backend.saved)
            self.assertEqual(len([call for call in backend.calls if call["method"] == "POST"]), 1)
            repeated = self.assert_envelope(self.request(app=app), 200)
            self.assertEqual(repeated["event_id"], data["event_id"])
            self.assertTrue(repeated["reused"])
            self.assertEqual(len([call for call in backend.calls if call["method"] == "POST"]), 1)

    def test_http_sdk_unknown_response_keeps_pending_identity_and_later_read_reuses_single_event(self):
        backend = CalendarHttpFake()
        backend.lose_response_once.add("POST")
        backend.fail_reads_after_write = 3
        with self.sdk_app(backend) as (app, db, _counts):
            data = self.assert_envelope(self.request(app=app), 502, status="failed", mutation_state="unknown", error="operation_not_confirmed")
            self.assertEqual(data, {"event_id": None})
            self.assertEqual(len(backend.saved), 1)
            with db.connection() as connection:
                row = connection.execute("SELECT event_id, state FROM creation_requests").fetchone()
            self.assertEqual(row["state"], "pending")
            self.assertIn(row["event_id"], backend.saved)
            backend.fail_reads_after_write = 0
            recovered = self.assert_envelope(self.request(app=app), 200)
            self.assertTrue(recovered["reused"])
            self.assertEqual(recovered["event_id"], row["event_id"])
            self.assertEqual(len([call for call in backend.calls if call["method"] == "POST"]), 1)
            with db.connection() as connection:
                state = connection.execute("SELECT state FROM creation_requests").fetchone()["state"]
            self.assertEqual(state, "confirmed")


if __name__ == "__main__":
    unittest.main()
