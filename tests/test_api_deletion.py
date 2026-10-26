"""Exclusão HTTP confirmada por versão, com SDK e transporte local sem OAuth.

Os DELETEs deste arquivo atingem somente CalendarHttpFake. Nenhum teste abre
uma agenda real ou transforma a prévia em consentimento humano automático.
"""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from io import StringIO
import json
import logging
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote, unquote, urlencode

from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import RefreshError, TransportError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app import google_calendar
from app.database import Database
from app.google_transport import SingleAttemptHttp
from app.main import create_app
from app.services.event_deletion import DeletionResult, deletion_confirmation, deletion_question
from app.services.event_details import EventDetails
from app.services.interviews import CalendarOperationError
from app.settings import Settings
from calendar_http_fake import CalendarHttpFake
from test_api_security import InjectedDatabase, invoke_asgi


TOKEN = "DeletionHttpToken_ForTests_0123456789AbCd"
PRIVATE = "PRIVATE_DELETION_CANARY_DoNotEcho"
ZONE = "America/Recife"
EVENT_ID = "InterviewCaseSensitiveID_20300617T130000Z"
CALENDAR_ID = "suite-calendar@example.invalid"
ETAG = '"v1"'
CONFIRMATION = deletion_confirmation(CALENDAR_ID, EVENT_ID, ETAG)
EVENT_FIELDS = {"event_id", "title", "start", "end", "all_day", "time_zone", "status", "html_link"}


def event_snapshot(event_id=EVENT_ID):
    return EventDetails(
        event_id=event_id, title="Entrevista técnica", start="2030-06-17T10:00:00-03:00",
        end="2030-06-17T10:30:00-03:00", all_day=False, time_zone=ZONE, status="confirmed",
        html_link="https://example.invalid/event", description=PRIVATE, location=PRIVATE,
    )


def preview_body():
    return {"time_zone": ZONE}


def delete_body(token=CONFIRMATION):
    return {"time_zone": ZONE, "confirmation_token": token}


class DeletionGateway:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.preview_result = None
        self.delete_result = None

    def delete_event(self, event_id, *, time_zone, execute, confirmation_token):
        self.calls.append((event_id, time_zone, execute, confirmation_token))
        if self.failure is not None:
            raise self.failure
        if execute:
            return self.delete_result if self.delete_result is not None else DeletionResult(
                "deleted", "Genius Testes", event_snapshot(event_id), recovered=False,
            )
        event = event_snapshot(event_id)
        return self.preview_result if self.preview_result is not None else DeletionResult(
            "needs_confirmation", "Genius Testes", event,
            deletion_confirmation(CALENDAR_ID, event_id, ETAG), deletion_question(event),
        )


class DeletionFactory:
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


class DeletionOnlyHttpFake(CalendarHttpFake):
    def request(self, method, url, **kwargs):
        if method.upper() not in ("GET", "DELETE"):
            raise AssertionError("O percurso de exclusão não autoriza POST, PATCH ou PUT.")
        return super().request(method, url, **kwargs)


class ApiDeletionTests(unittest.TestCase):
    def setUp(self):
        self.config = Settings(_env_file=None, app_name="Agenda HTTP de teste", app_env="test", api_auth_token=TOKEN)
        self.gateway = DeletionGateway()
        self.factory = DeletionFactory(self.gateway)
        self.app = create_app(config=self.config, db=InjectedDatabase(), calendar_gateway_factory=self.factory)
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
        for private in (TOKEN, PRIVATE, CONFIRMATION):
            self.assertNotIn(private, self.logs.getvalue())

    def request(self, execute=False, body=None, *, raw_body=None, event_id=EVENT_ID,
                authorized=True, query=(), app=None, content_type=b"application/json", extra_headers=()):
        if raw_body is None:
            body = (delete_body() if execute else preview_body()) if body is None else body
            raw_body = json.dumps(body, ensure_ascii=True).encode("ascii")
        raw_path = "/api/v1/events/" + quote(event_id, safe="") + ("/delete" if execute else "/deletion-preview")
        selected_app = self.app if app is None else app

        async def encoded_app(scope, receive, send):
            scope["raw_path"] = raw_path.encode("ascii")
            scope["path"] = unquote(raw_path)
            return await selected_app(scope, receive, send)

        headers = [(b"content-type", content_type), (b"x-request-id", b"deletion-test-001")]
        if authorized:
            headers.append((b"authorization", f"Bearer {TOKEN}".encode("ascii")))
        response = asyncio.run(invoke_asgi(
            encoded_app, raw_path, method="POST", headers=(*headers, *extra_headers),
            body=raw_body, query=urlencode(query).encode("ascii"),
        ))
        rendered = json.dumps(response, ensure_ascii=True)
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn(PRIVATE, rendered)
        if execute or response[0] != 200:
            self.assertNotIn(CONFIRMATION, rendered)
        return response

    def assert_envelope(self, response, http_status=200, *, status="succeeded",
                        mutation_state="confirmed", error=None, fields=None):
        code, headers, payload = response
        self.assertEqual(code, http_status)
        self.assertEqual(set(payload), {"api_version", "request_id", "operation_id", "status", "action", "mutation_state", "message", "data", "error"})
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], "deletion-test-001")
        self.assertEqual(headers["x-request-id"], "deletion-test-001")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["action"], "delete")
        self.assertEqual(payload["status"], status)
        self.assertEqual(payload["mutation_state"], mutation_state)
        if error is None:
            self.assertIsNone(payload["error"])
        else:
            self.assertEqual(payload["error"]["code"], error)
            self.assertIs(payload["error"]["retryable"], False)
            if fields is not None:
                self.assertEqual(payload["error"]["fields"], list(fields))
        return payload["data"]

    def assert_invalid(self, response, field=None):
        self.assert_envelope(response, 422, status="failed", mutation_state="not_applied", error="invalid_request")
        if field is not None:
            self.assertIn(field, response[2]["error"]["fields"])
        self.assertIsNone(response[2]["data"])

    def assert_unknown(self, response):
        return self.assert_envelope(response, 502, status="failed", mutation_state="unknown", error="operation_not_confirmed", fields=())

    def assert_preview(self, response):
        return self.assert_envelope(response, status="needs_confirmation", mutation_state="not_applied")

    def test_preview_has_minimal_snapshot_and_question_without_executing_delete(self):
        data = self.assert_preview(self.request())
        self.assertEqual(set(data), {"result", "calendar_name", "event", "confirmation_token", "question"})
        self.assertEqual(data["result"], "needs_confirmation")
        self.assertEqual(data["calendar_name"], "Genius Testes")
        self.assertEqual(set(data["event"]), EVENT_FIELDS)
        self.assertEqual(data["event"]["event_id"], EVENT_ID)
        self.assertEqual(data["confirmation_token"], CONFIRMATION)
        self.assertIn("Entrevista técnica", data["question"])
        self.assertEqual(self.gateway.calls, [(EVENT_ID, ZONE, False, None)])
        self.assertEqual((self.factory.opened, self.factory.closed), (1, 1))

    def test_execution_forwards_explicit_confirmation_without_exposing_token_or_private_event(self):
        data = self.assert_envelope(self.request(True))
        self.assertEqual(data, {"result": "deleted", "calendar_name": "Genius Testes", "event_id": EVENT_ID, "recovered": False})
        self.assertEqual(self.gateway.calls, [(EVENT_ID, ZONE, True, CONFIRMATION)])

    def test_absence_has_separate_preview_and_execution_states(self):
        absent = DeletionResult("already_absent", "Genius Testes", None)
        self.gateway.preview_result = absent
        self.gateway.delete_result = absent
        preview = self.assert_envelope(self.request(), mutation_state="not_applied")
        self.assertEqual(preview, {"result": "already_absent", "calendar_name": "Genius Testes", "event": None, "confirmation_token": None, "question": None})
        execution = self.assert_envelope(self.request(True))
        self.assertEqual(execution, {"result": "already_absent", "calendar_name": "Genius Testes", "event_id": EVENT_ID, "recovered": False})

    def test_bearer_is_required_and_invalid_authorization_never_opens_calendar(self):
        for execute in (False, True):
            for headers in ((), ((b"authorization", b"Basic abc"),), ((b"authorization", b"Bearer wrong"),),
                            ((b"authorization", f"Bearer {TOKEN}".encode()), (b"authorization", f"Bearer {TOKEN}".encode()))):
                with self.subTest(execute=execute, authorization_headers=len(headers)):
                    code, response_headers, payload = self.request(execute, authorized=False, extra_headers=headers)
                    self.assertEqual(code, 401)
                    self.assertEqual(response_headers["www-authenticate"], "Bearer")
                    self.assertEqual(payload["error"]["code"], "unauthorized")
                    self.assertIsNone(payload["action"])
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_missing_or_malformed_confirmation_is_rejected_before_factory(self):
        self.assert_invalid(self.request(True, body=preview_body()), "confirmation_token")
        invalid_tokens = (None, "", 1, True, [], {}, "0" * 63, "0" * 65, "g" * 64, "A" * 64, " " + CONFIRMATION, CONFIRMATION + "\n", "bad-\ud800")
        for token in invalid_tokens:
            with self.subTest(token_type=type(token).__name__, token_length=len(token) if isinstance(token, str) else None):
                self.assert_invalid(self.request(True, body=delete_body(token)), "confirmation_token")
        self.assertEqual(self.factory.opened, 0)

    def test_strict_bodies_reject_unknown_values_and_preview_cannot_enable_execution(self):
        for execute in (False, True):
            body = delete_body() if execute else preview_body()
            for key, value in ((PRIVATE, PRIVATE), ("execute", True), ("operation_id", PRIVATE), ("calendar_id", PRIVATE), ("user_id", PRIVATE)):
                with self.subTest(execute=execute, extra=key != PRIVATE):
                    self.assert_invalid(self.request(execute, body={**body, key: value}), "body")
            for raw in (b"", b"{", b"null", b"[]", b'"string"', b"true", b"42", b'{"time_zone":"\xff"}', b'{"time_zone":'):
                with self.subTest(execute=execute, raw_length=len(raw)):
                    self.assert_invalid(self.request(execute, raw_body=raw), "body")
            self.assert_invalid(self.request(execute, body=body, content_type=b"text/plain"), "body")
        self.assert_invalid(self.request(body={**preview_body(), "confirmation_token": CONFIRMATION}), "body")
        self.assertEqual(self.factory.opened, 0)

    def test_identity_timezone_and_all_query_parameters_are_validated_locally(self):
        for execute in (False, True):
            body = delete_body() if execute else preview_body()
            for zone in (None, 0, True, [], "", "Invalid/Zone", "bad-\ud800"):
                with self.subTest(execute=execute, zone_type=type(zone).__name__):
                    self.assert_invalid(self.request(execute, body={**body, "time_zone": zone}), "time_zone")
            no_zone = {key: value for key, value in body.items() if key != "time_zone"}
            self.assert_invalid(self.request(execute, body=no_zone), "time_zone")
            for event_id in ("", "bad id", "hidden\u200bcharacter", "line\tbreak", "a" * 1025):
                with self.subTest(execute=execute, event_id_length=len(event_id)):
                    self.assert_invalid(self.request(execute, event_id=event_id), "event_id")
            for query in ((("time_zone", ZONE),), ((PRIVATE, PRIVATE),), (("execute", "true"), ("execute", "true"))):
                self.assert_invalid(self.request(execute, query=query), "query")
        self.assertEqual(self.factory.opened, 0)

    def test_opaque_event_ids_keep_case_recurring_instance_suffix_and_decoded_slashes(self):
        for event_id in (EVENT_ID, "CaseSensitive_20300617T130000Z", "opaque/with/slash", "ends/delete", "ends/deletion-preview"):
            for execute in (False, True):
                with self.subTest(event_id=event_id, execute=execute):
                    response = self.request(execute, event_id=event_id)
                    data = self.assert_envelope(response) if execute else self.assert_preview(response)
                    self.assertEqual(data["event_id"] if execute else data["event"]["event_id"], event_id)
                    self.assertEqual(self.gateway.calls[-1][0], event_id)

    def test_setup_at_entry_differs_from_read_errors_execution_errors_and_cleanup(self):
        for execute in (False, True):
            self.factory.open_failure = google_calendar.CalendarSetupError(PRIVATE)
            self.assert_envelope(self.request(execute), 503, status="failed", mutation_state="not_applied" if execute else "not_applicable", error="calendar_setup_unavailable", fields=())
            self.factory.open_failure = None
            for failure in (CalendarOperationError(PRIVATE), CalendarOperationError("HTTP 404 409 412 " + PRIVATE),
                            ValueError(PRIVATE), OSError(PRIVATE), RefreshError(PRIVATE), TransportError(PRIVATE),
                            sqlite3.OperationalError(PRIVATE), google_calendar.CalendarSetupError(PRIVATE)):
                with self.subTest(execute=execute, failure=type(failure).__name__):
                    self.gateway.failure = failure
                    response = self.request(execute)
                    if execute:
                        self.assert_unknown(response)
                    elif isinstance(failure, google_calendar.CalendarSetupError):
                        self.assert_envelope(response, 503, status="failed", mutation_state="not_applicable", error="calendar_setup_unavailable", fields=())
                    else:
                        self.assert_envelope(response, 502, status="failed", mutation_state="not_applicable", error="operation_failed", fields=())
            self.gateway.failure = None
            for failure in (OSError(PRIVATE), google_calendar.CalendarSetupError(PRIVATE)):
                self.factory.close_failure = failure
                if execute:
                    self.assert_unknown(self.request(execute))
                elif isinstance(failure, google_calendar.CalendarSetupError):
                    self.assert_envelope(self.request(execute), 503, status="failed", mutation_state="not_applicable", error="calendar_setup_unavailable", fields=())
                else:
                    self.assert_envelope(self.request(execute), 502, status="failed", mutation_state="not_applicable", error="operation_failed", fields=())
            self.factory.close_failure = None
        self.assertEqual(self.factory.opened, self.factory.closed)

    def test_projection_excludes_provider_subclasses_and_only_preview_can_return_confirmation(self):
        @dataclass(frozen=True)
        class PrivateResult(DeletionResult):
            internal_diagnostic: str = PRIVATE

        @dataclass(frozen=True)
        class PrivateEvent(EventDetails):
            internal_diagnostic: str = PRIVATE

        event = PrivateEvent(**event_snapshot().__dict__)
        self.gateway.preview_result = PrivateResult("needs_confirmation", "Genius Testes", event, CONFIRMATION, deletion_question(event))
        self.gateway.delete_result = PrivateResult("deleted", "Genius Testes", event)
        preview = self.assert_preview(self.request())
        self.assertEqual(set(preview["event"]), EVENT_FIELDS)
        executed = self.assert_envelope(self.request(True))
        self.assertEqual(set(executed), {"result", "calendar_name", "event_id", "recovered"})

    def test_malformed_provider_results_never_claim_confirmation_or_safe_absence(self):
        event = event_snapshot()
        preview = DeletionResult("needs_confirmation", "Genius Testes", event, CONFIRMATION, deletion_question(event))
        deletion = DeletionResult("deleted", "Genius Testes", event)
        malformed_previews = (
            {}, replace(preview, status="deleted"), replace(preview, event=None),
            replace(preview, calendar_name=""), replace(preview, confirmation_token="0" * 63),
            replace(preview, question=None), replace(preview, question=""), replace(preview, recovered=True),
            replace(preview, event=replace(event, event_id="WrongID")),
            replace(preview, event=replace(event, title="bad-\ud800")),
            DeletionResult("already_absent", "Genius Testes", event),
            DeletionResult("already_absent", "Genius Testes", None, CONFIRMATION),
        )
        malformed_deletions = (
            {}, replace(deletion, status="needs_confirmation"), replace(deletion, event=None),
            replace(deletion, calendar_name=""), replace(deletion, recovered=1),
            replace(deletion, event=replace(event, event_id="WrongID")),
            replace(deletion, confirmation_token=CONFIRMATION), replace(deletion, question="Confirma?"),
            DeletionResult("already_absent", "Genius Testes", event),
        )
        for execute, attribute, cases in ((False, "preview_result", malformed_previews), (True, "delete_result", malformed_deletions)):
            for case, result in enumerate(cases):
                with self.subTest(execute=execute, case=case):
                    setattr(self.gateway, attribute, result)
                    if execute:
                        self.assert_unknown(self.request(True))
                    else:
                        self.assert_envelope(self.request(), 502, status="failed", mutation_state="not_applicable", error="operation_failed", fields=())
            setattr(self.gateway, attribute, None)

    def test_openapi_exposes_two_protected_strict_post_bodies_and_no_implicit_delete(self):
        schema = self.app.openapi()
        for suffix, required in (("deletion-preview", {"time_zone"}), ("delete", {"time_zone", "confirmation_token"})):
            path = "/api/v1/events/{event_id}/" + suffix
            operation = schema["paths"][path]["post"]
            self.assertIn({"ApiKeyBearer": []}, operation["security"])
            self.assertTrue({"200", "422", "502", "503"}.issubset(operation["responses"]))
            self.assertNotIn("404", operation["responses"])
            reference = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
            body = schema["components"]["schemas"][reference]
            self.assertFalse(body["additionalProperties"])
            self.assertEqual(set(body["required"]), required)
            self.assertEqual(set(body["properties"]), required)
        self.assertNotIn("delete", schema["paths"]["/api/v1/events/{event_id}"])
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
            db = Database(Path(temporary) / "deletion.sqlite3")
            settings = SimpleNamespace(google_calendar_name="Genius Testes", google_calendar_id=backend.calendar["id"])
            with patch("app.google_transport.requests.Session", side_effect=local_session), \
                    patch.object(google_calendar, "database", db), patch.object(google_calendar, "settings", settings), \
                    patch.object(google_calendar, "get_credentials", side_effect=AssertionError("OAuth real proibido.")), \
                    patch("googleapiclient.http.time.sleep"):
                app = create_app(config=self.config, db=InjectedDatabase(), calendar_gateway_factory=sdk_factory)
                yield app, db, counts
        self.assertEqual(counts["opened"], counts["closed"])
        self.assertTrue(sessions)
        self.assertTrue(all(session.closed for session in sessions))

    @staticmethod
    def seeded_backend():
        backend = DeletionOnlyHttpFake()
        event = event_snapshot()
        backend.saved[EVENT_ID] = {
            "id": EVENT_ID, "summary": event.title, "status": "confirmed", "etag": ETAG,
            "description": event.description, "location": event.location, "eventType": "default",
            "htmlLink": event.html_link, "start": {"dateTime": event.start, "timeZone": ZONE},
            "end": {"dateTime": event.end, "timeZone": ZONE}, "visibility": "private",
            "attendees": [{"email": "fictitious@example.invalid"}], "reminders": {"useDefault": False},
        }
        return backend

    @staticmethod
    def deletes(backend):
        return [call for call in backend.calls if call["method"] == "DELETE"]

    def test_http_sdk_preview_wrong_confirmation_correct_deletion_and_safe_replay(self):
        backend = self.seeded_backend()
        before = deepcopy(backend.saved)
        with self.sdk_app(backend) as (app, db, counts):
            preview = self.assert_preview(self.request(app=app))
            self.assertEqual(preview["confirmation_token"], CONFIRMATION)
            self.assertEqual(backend.saved, before)
            self.assertEqual(self.deletes(backend), [])
            bad_tokens = ("0" * 64, deletion_confirmation("other-calendar", EVENT_ID, ETAG), deletion_confirmation(CALENDAR_ID, "OtherID", ETAG), deletion_confirmation(CALENDAR_ID, EVENT_ID, '"v0"'))
            for token in bad_tokens:
                self.assert_unknown(self.request(True, body=delete_body(token), app=app))
                self.assertEqual(self.deletes(backend), [])
                self.assertEqual(backend.saved, before)
            deleted = self.assert_envelope(self.request(True, body=delete_body(preview["confirmation_token"]), app=app))
            self.assertEqual(deleted["result"], "deleted")
            self.assertEqual(deleted["event_id"], EVENT_ID)
            self.assertNotIn(EVENT_ID, backend.saved)
            writes = self.deletes(backend)
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]["event_id"], EVENT_ID)
            self.assertEqual(writes[0]["query"]["sendUpdates"], "none")
            self.assertEqual({key.lower(): value for key, value in writes[0]["headers"].items()}["if-match"], ETAG)
            self.assertIsNone(writes[0]["body"])
            self.assertEqual(writes[0]["status"], 204)
            self.assertTrue(any(call["status"] == 404 for call in backend.calls if call["method"] == "GET"))
            repeated = self.assert_envelope(self.request(True, app=app))
            self.assertEqual(repeated["result"], "already_absent")
            self.assertEqual(len(self.deletes(backend)), 1)
            self.assertEqual(counts["opened"], 7)
            with db.connection() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM creation_requests").fetchone()[0], 0)

    def test_http_sdk_stale_preview_cannot_delete_changed_version(self):
        backend = self.seeded_backend()
        with self.sdk_app(backend) as (app, _db, _counts):
            preview = self.assert_preview(self.request(app=app))
            backend.saved[EVENT_ID]["etag"] = '"v2"'
            backend.saved[EVENT_ID]["summary"] = "Entrevista alterada externamente"
            self.assert_unknown(self.request(True, body=delete_body(preview["confirmation_token"]), app=app))
            self.assertEqual(self.deletes(backend), [])
            self.assertEqual(backend.saved[EVENT_ID]["etag"], '"v2"')

    def test_http_sdk_412_does_not_retry_delete_or_claim_a_schedule_conflict(self):
        backend = self.seeded_backend()

        def concurrent_update(_method, event_id, _body):
            backend.saved[event_id]["etag"] = '"v99"'
            backend.saved[event_id]["description"] = "Alteração concorrente"

        backend.before_write = concurrent_update
        with self.sdk_app(backend) as (app, _db, _counts):
            self.assert_unknown(self.request(True, app=app))
            writes = self.deletes(backend)
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]["status"], 412)
            self.assertEqual(backend.saved[EVENT_ID]["description"], "Alteração concorrente")

    def test_http_sdk_lost_delete_response_is_recovered_by_read_without_second_delete(self):
        backend = self.seeded_backend()
        backend.lose_response_once.add("DELETE")
        with self.sdk_app(backend) as (app, _db, _counts):
            deleted = self.assert_envelope(self.request(True, app=app))
            self.assertEqual(deleted["result"], "deleted")
            self.assertIs(deleted["recovered"], True)
            self.assertEqual(len(self.deletes(backend)), 1)
            replay = self.assert_envelope(self.request(True, app=app))
            self.assertEqual(replay["result"], "already_absent")
            self.assertEqual(len(self.deletes(backend)), 1)

    def test_http_sdk_uncertain_final_read_stays_unknown_until_later_absence_check(self):
        backend = self.seeded_backend()
        backend.lose_response_once.add("DELETE")
        backend.fail_reads_after_write = 3
        with self.sdk_app(backend) as (app, _db, _counts):
            self.assert_unknown(self.request(True, app=app))
            self.assertNotIn(EVENT_ID, backend.saved)
            self.assertEqual(len(self.deletes(backend)), 1)
            backend.fail_reads_after_write = 0
            checked = self.assert_envelope(self.request(True, app=app))
            self.assertEqual(checked["result"], "already_absent")
            self.assertEqual(len(self.deletes(backend)), 1)

    def test_http_sdk_initial_absence_is_success_but_malformed_or_failed_read_is_not(self):
        for execute in (False, True):
            backend = DeletionOnlyHttpFake()
            with self.sdk_app(backend) as (app, _db, _counts):
                absent = self.assert_envelope(self.request(execute, app=app), mutation_state="confirmed" if execute else "not_applied")
                self.assertEqual(absent["result"], "already_absent")
                self.assertEqual(self.deletes(backend), [])
            for invalid in ({}, {"id": "WrongID", "status": "cancelled"}, {"id": EVENT_ID, "status": "unknown"}):
                with self.subTest(execute=execute, malformed_keys=list(invalid)):
                    backend = self.seeded_backend()
                    backend.read_override = lambda _event_id, value=invalid: value
                    with self.sdk_app(backend) as (app, _db, _counts):
                        if execute:
                            self.assert_unknown(self.request(True, app=app))
                        else:
                            self.assert_envelope(self.request(app=app), 502, status="failed", mutation_state="not_applicable", error="operation_failed", fields=())
                        self.assertEqual(self.deletes(backend), [])

    def test_http_sdk_transport_read_failure_is_not_reported_as_absence(self):
        def failed_read(_event_id):
            raise TimeoutError("simulated read timeout")

        for execute in (False, True):
            backend = self.seeded_backend()
            backend.read_override = failed_read
            before = deepcopy(backend.saved)
            with self.sdk_app(backend) as (app, _db, _counts):
                if execute:
                    self.assert_unknown(self.request(True, app=app))
                else:
                    self.assert_envelope(self.request(app=app), 502, status="failed", mutation_state="not_applicable", error="operation_failed", fields=())
                self.assertEqual(backend.saved, before)
                self.assertEqual(self.deletes(backend), [])
                self.assertTrue(all(call["method"] == "GET" for call in backend.calls))

    def test_http_sdk_all_day_preview_uses_exclusive_end_and_blocks_recurring_series(self):
        backend = self.seeded_backend()
        backend.saved[EVENT_ID]["start"] = {"date": "2030-06-17"}
        backend.saved[EVENT_ID]["end"] = {"date": "2030-06-19"}
        with self.sdk_app(backend) as (app, _db, _counts):
            preview = self.assert_preview(self.request(app=app))
            self.assertIs(preview["event"]["all_day"], True)
            self.assertEqual(preview["event"]["end"], "2030-06-19")
            self.assertIn("18/06/2030", preview["question"])
            self.assertNotIn("19/06/2030", preview["question"])
            self.assertEqual(self.deletes(backend), [])
        backend = self.seeded_backend()
        backend.saved[EVENT_ID]["recurrence"] = ["RRULE:FREQ=WEEKLY"]
        with self.sdk_app(backend) as (app, _db, _counts):
            self.assert_envelope(self.request(app=app), 502, status="failed", mutation_state="not_applicable", error="operation_failed", fields=())
            self.assertEqual(self.deletes(backend), [])


if __name__ == "__main__":
    unittest.main()
