"""Alterações HTTP com domínio real e Google simulado na fronteira de transporte.

Nenhum teste carrega OAuth, abre a agenda real ou cria/apaga compromissos.
Os percursos integrados usam SDK, ETags, serviços e SQLite temporário reais.
"""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
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
from zoneinfo import ZoneInfo

from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import RefreshError, TransportError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app import google_calendar
from app.database import Database
from app.google_transport import SingleAttemptHttp
from app.main import create_app
from app.services.agenda import AgendaEvent
from app.services.conflicts import CalendarConflictError, ConflictReport
from app.services.event_details import DetailsChange, DetailsResult, EventDetails, validate_details_change
from app.services.interviews import CalendarOperationError
from app.services.rescheduling import RescheduleResult, ScheduleChange
from app.settings import Settings
from calendar_http_fake import CalendarHttpFake
from test_api_security import InjectedDatabase, invoke_asgi


TOKEN = "UpdatesHttpToken_ForTests_0123456789AbCd"
PRIVATE = "PRIVATE_UPDATE_CANARY_DoNotEcho"
ZONE = "America/Recife"
EVENT_ID = "InterviewCaseSensitiveID_20300617T130000Z"
EVENT_FIELDS = {"event_id", "title", "start", "end", "all_day", "time_zone", "status", "html_link"}
DETAILS_FIELDS = EVENT_FIELDS | {"description", "location"}


def schedule_body():
    return {"start": "2030-06-17T11:00:00-03:00", "end": "2030-06-17T11:45:00-03:00", "time_zone": ZONE}


def details_body():
    return {"changes": {"title": "Entrevista final", "description": "Preparar portfólio", "location": "Sala virtual"}, "time_zone": ZONE}


def original_event(event_id=EVENT_ID):
    return EventDetails(
        event_id=event_id, title="Entrevista técnica", start="2030-06-17T10:00:00-03:00",
        end="2030-06-17T10:30:00-03:00", all_day=False, time_zone=ZONE, status="confirmed",
        html_link="https://example.invalid/event", description="Descrição anterior", location="Local anterior",
    )


class UpdateGateway:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.schedule_result = None
        self.details_result = None

    def reschedule_event(self, event_id, change, *, execute):
        self.calls.append(("reschedule", event_id, change, execute))
        if self.failure is not None:
            raise self.failure
        if self.schedule_result is not None:
            return self.schedule_result
        before = original_event(event_id)
        if change.time_zone != before.time_zone:
            zone = ZoneInfo(change.time_zone)
            before = replace(before, time_zone=change.time_zone,
                start=datetime.fromisoformat(before.start).astimezone(zone).isoformat(),
                end=datetime.fromisoformat(before.end).astimezone(zone).isoformat())
        after = replace(before, start=change.start.isoformat(), end=change.end.isoformat(), time_zone=change.time_zone)
        return RescheduleResult("rescheduled", "Genius Testes", before, after, change.start.isoformat(), change.end.isoformat(), change.time_zone)

    def update_event_details(self, event_id, change, *, time_zone, execute):
        self.calls.append(("edit", event_id, change, time_zone, execute))
        if self.failure is not None:
            raise self.failure
        if self.details_result is not None:
            return self.details_result
        before = original_event(event_id)
        requested = validate_details_change(event_id, change, time_zone=time_zone)
        return DetailsResult("updated", "Genius Testes", before, replace(before, **requested), requested)


class UpdateFactory:
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


class UpdatesOnlyHttpFake(CalendarHttpFake):
    def request(self, method, url, **kwargs):
        if method.upper() not in ("GET", "PATCH"):
            raise AssertionError("O percurso de alterações não autoriza POST ou DELETE.")
        return super().request(method, url, **kwargs)


class ApiUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.config = Settings(_env_file=None, app_name="Agenda HTTP de teste", app_env="test", api_auth_token=TOKEN)
        self.db = InjectedDatabase()
        self.gateway = UpdateGateway()
        self.factory = UpdateFactory(self.gateway)
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

    def request(self, action="edit", body=None, *, raw_body=None, event_id=EVENT_ID, authorized=True,
                query=(), app=None, content_type=b"application/json", extra_headers=()):
        if raw_body is None:
            body = (schedule_body() if action == "reschedule" else details_body()) if body is None else body
            raw_body = json.dumps(body, ensure_ascii=True).encode("ascii")
        raw_path = "/api/v1/events/" + quote(event_id, safe="") + ("/schedule" if action == "reschedule" else "")
        # ASGI entrega path decodificado e raw_path com a codificação original.
        # Isso exercita IDs opacos contendo %2F sem usar rede ou um cliente HTTP.
        selected_app = self.app if app is None else app

        async def encoded_app(scope, receive, send):
            scope["raw_path"] = raw_path.encode("ascii")
            return await selected_app(scope, receive, send)

        headers = [(b"content-type", content_type), (b"x-request-id", b"updates-test-001")]
        if authorized:
            headers.append((b"authorization", f"Bearer {TOKEN}".encode("ascii")))
        response = asyncio.run(invoke_asgi(
            encoded_app, unquote(raw_path), method="PATCH", headers=(*headers, *extra_headers),
            body=raw_body, query=urlencode(query).encode("ascii"),
        ))
        rendered = json.dumps(response, ensure_ascii=True)
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn(PRIVATE, rendered)
        return response

    def assert_envelope(self, response, http_status=200, *, action="edit", status="succeeded",
                        mutation_state="confirmed", error=None, fields=None):
        code, headers, payload = response
        self.assertEqual(code, http_status)
        self.assertEqual(set(payload), {"api_version", "request_id", "operation_id", "status", "action", "mutation_state", "message", "data", "error"})
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], "updates-test-001")
        self.assertEqual(headers["x-request-id"], "updates-test-001")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["action"], action)
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

    def assert_invalid(self, response, action, *, field=None):
        self.assert_envelope(response, 422, action=action, status="failed", mutation_state="not_applied", error="invalid_request")
        if field:
            self.assertIn(field, response[2]["error"]["fields"])
        self.assertIsNone(response[2]["data"])

    def assert_unknown(self, response, action):
        return self.assert_envelope(response, 502, action=action, status="failed", mutation_state="unknown", error="operation_not_confirmed", fields=())

    def test_schedule_and_edit_use_existing_services_and_confirm_public_result_snapshots(self):
        schedule = self.assert_envelope(self.request("reschedule"), action="reschedule")
        self.assertEqual(set(schedule), {"result", "calendar_name", "before", "after", "requested_start", "requested_end", "time_zone", "recovered"})
        self.assertEqual(schedule["result"], "rescheduled")
        self.assertEqual(schedule["requested_start"], schedule_body()["start"])
        self.assertEqual(schedule["requested_end"], schedule_body()["end"])
        self.assertEqual(schedule["time_zone"], ZONE)
        self.assertIs(schedule["recovered"], False)
        for name in ("before", "after"):
            self.assertEqual(set(schedule[name]), EVENT_FIELDS)
        self.assertEqual(schedule["after"]["event_id"], EVENT_ID)
        self.assertEqual(schedule["after"]["start"], schedule_body()["start"])
        call = self.gateway.calls[-1]
        self.assertEqual(call[0:2], ("reschedule", EVENT_ID))
        self.assertIsInstance(call[2], ScheduleChange)
        self.assertEqual(call[2].start, datetime.fromisoformat(schedule_body()["start"]))
        self.assertIs(call[3], True)
        details = self.assert_envelope(self.request())
        self.assertEqual(set(details), {"result", "calendar_name", "before", "after", "requested", "recovered"})
        self.assertEqual(details["result"], "updated")
        self.assertEqual(details["requested"], details_body()["changes"])
        for name in ("before", "after"):
            self.assertEqual(set(details[name]), DETAILS_FIELDS)
        self.assertEqual(details["after"]["start"], original_event().start)
        self.assertEqual(details["after"]["location"], "Sala virtual")
        self.assertIsInstance(self.gateway.calls[-1][2], DetailsChange)
        self.assertEqual(self.gateway.calls[-1][3:], (ZONE, True))
        self.assertEqual((self.factory.opened, self.factory.closed, self.db.initializations), (2, 2, 0))

    def test_opaque_case_sensitive_recurring_and_encoded_slash_ids_are_forwarded_exactly(self):
        for event_id in (EVENT_ID, "AbC123", "Series_20300617T130000Z", "AbC/Occurrence_20300617T130000Z"):
            for action in ("edit", "reschedule"):
                with self.subTest(event_id=event_id, action=action):
                    data = self.assert_envelope(self.request(action, event_id=event_id), action=action)
                    self.assertEqual(data["after"]["event_id"], event_id)
                    self.assertEqual(self.gateway.calls[-1][1], event_id)

    def test_omitted_or_null_fields_preserve_state_but_empty_text_clears_optional_information(self):
        cases = (
            ({"description": ""}, {"description": ""}),
            ({"location": "", "description": None, "title": None}, {"location": ""}),
            ({"title": "  Entrevista final  ", "location": None}, {"title": "Entrevista final"}),
        )
        for changes, expected in cases:
            with self.subTest(fields=list(changes)):
                data = self.assert_envelope(self.request(body={"changes": changes, "time_zone": ZONE}))
                self.assertEqual(data["requested"], expected)
                for field in ("title", "description", "location"):
                    self.assertEqual(data["after"][field], expected.get(field, getattr(original_event(), field)))
        self.assertEqual(self.factory.closed, 3)

    def test_success_can_be_recovered_or_unchanged_without_inventing_a_new_operation_identity(self):
        before = original_event()
        schedule = schedule_body()
        changed = replace(before, start=schedule["start"], end=schedule["end"])
        for recovered in (False, True):
            with self.subTest(action="reschedule", recovered=recovered):
                self.gateway.schedule_result = RescheduleResult("rescheduled", "Genius Testes", before, changed, schedule["start"], schedule["end"], ZONE, recovered)
                data = self.assert_envelope(self.request("reschedule"), action="reschedule")
                self.assertEqual(data["recovered"], recovered)
            with self.subTest(action="edit", recovered=recovered):
                self.gateway.details_result = DetailsResult("updated", "Genius Testes", before, replace(before, **details_body()["changes"]), details_body()["changes"], recovered)
                data = self.assert_envelope(self.request())
                self.assertEqual(data["recovered"], recovered)
        self.gateway.schedule_result = RescheduleResult("unchanged", "Genius Testes", changed, changed, schedule["start"], schedule["end"], ZONE)
        data = self.assert_envelope(self.request("reschedule"), action="reschedule")
        self.assertEqual(data["result"], "unchanged")
        self.assertEqual(data["before"], data["after"])
        updated = replace(before, **details_body()["changes"])
        self.gateway.details_result = DetailsResult("unchanged", "Genius Testes", updated, updated, details_body()["changes"])
        data = self.assert_envelope(self.request())
        self.assertEqual(data["result"], "unchanged")
        self.assertEqual(data["before"], data["after"])

    def test_authentication_and_request_correlation_fail_before_provider_access(self):
        no_key = create_app(config=Settings(_env_file=None, api_auth_token=None), db=self.db, calendar_gateway_factory=self.factory)
        for action in ("edit", "reschedule"):
            with self.subTest(action=action):
                unauthorized = self.request(action, authorized=False)
                self.assertEqual(unauthorized[0], 401)
                self.assertEqual(unauthorized[1]["www-authenticate"], "Bearer")
                self.assertIsNone(unauthorized[2]["action"])
                unavailable = self.request(action, app=no_key)
                self.assertEqual(unavailable[0], 503)
                self.assertEqual(unavailable[2]["error"]["code"], "api_auth_unavailable")
                bad_correlation = self.request(action, extra_headers=((b"x-request-id", b"bad private value"),))
                self.assertEqual(bad_correlation[0], 422)
                self.assertEqual(bad_correlation[2]["error"]["fields"], ["X-Request-ID"])
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_schedule_requires_strict_nonempty_strings_and_no_extra_fields(self):
        cases = []
        for field in ("start", "end", "time_zone"):
            missing = schedule_body()
            missing.pop(field)
            cases.append((missing, field))
            for invalid in (None, 123, True, [], {}, "", PRIVATE):
                body = schedule_body()
                body[field] = invalid
                cases.append((body, field))
        extra = schedule_body()
        extra[PRIVATE] = PRIVATE
        cases.append((extra, "body"))
        for number, (body, field) in enumerate(cases):
            with self.subTest(case=number, field=field):
                self.assert_invalid(self.request("reschedule", body), "reschedule", field=field)
        self.assertEqual(self.factory.opened, 0)

    def test_schedule_reuses_duration_offset_zone_and_dst_rules_before_factory(self):
        cases = (
            {"start": "2030-02-30T11:00:00-03:00"}, {"end": "2030-02-30T11:45:00-03:00"},
            {"start": "2030-06-17T11:00:00"}, {"end": "2030-06-17T11:45:00"},
            {"end": "2030-06-17T11:00:00-03:00"}, {"end": "2030-06-17T10:59:00-03:00"},
            {"start": "2030-06-17T11:00:00+00:00"}, {"time_zone": " America/Recife"},
            {"time_zone": "America/New_York", "start": "2030-03-10T02:00:00-05:00", "end": "2030-03-10T03:30:00-04:00"},
        )
        for number, changes in enumerate(cases):
            with self.subTest(case=number):
                self.assert_invalid(self.request("reschedule", {**schedule_body(), **changes}), "reschedule")
        self.assertEqual(self.factory.opened, 0)
        fallback = {"time_zone": "America/New_York", "start": "2030-11-03T01:30:00-04:00", "end": "2030-11-03T01:15:00-05:00"}
        self.assert_envelope(self.request("reschedule", fallback), action="reschedule")
        self.assertEqual(self.factory.closed, 1)

    def test_details_requires_meaningful_changes_strict_types_zone_and_known_fields(self):
        cases = [({}, "changes"), ({"changes": None, "time_zone": ZONE}, "changes"),
                 ({"changes": [], "time_zone": ZONE}, "changes"), ({"changes": {}, "time_zone": ZONE}, "changes"),
                 ({"changes": {"title": None, "description": None, "location": None}, "time_zone": ZONE}, "changes")]
        for field in ("title", "description", "location"):
            for value in (1, True, [], {}):
                cases.append(({"changes": {field: value}, "time_zone": ZONE}, field))
        for blank in ("", "  ", "\t\n"):
            cases.append(({"changes": {"title": blank}, "time_zone": ZONE}, "title"))
        for invalid_zone in (None, 1, True, "", PRIVATE):
            cases.append(({**details_body(), "time_zone": invalid_zone}, "time_zone"))
        cases.append(({"changes": {PRIVATE: PRIVATE}, "time_zone": ZONE}, "changes"))
        cases.append(({**details_body(), PRIVATE: PRIVATE}, "body"))
        cases.append(({"changes": {"title": "Ok"}}, "time_zone"))
        for number, (body, field) in enumerate(cases):
            with self.subTest(case=number, field=field):
                self.assert_invalid(self.request(body=body), "edit", field=field)
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_invalid_event_ids_and_any_query_parameter_are_local_errors_without_echoing_values(self):
        for action in ("edit", "reschedule"):
            for event_id in ("bad id", "bad\tID", "bad\x00ID", "A" * 1025):
                with self.subTest(action=action, invalid_id_length=len(event_id)):
                    self.assert_invalid(self.request(action, event_id=event_id), action, field="event_id")
            for query in (((PRIVATE, PRIVATE),), (("time_zone", ZONE),), (("calendar_id", PRIVATE),), (("x", "1"), ("x", "2"))):
                with self.subTest(action=action, query_count=len(query)):
                    self.assert_invalid(self.request(action, query=query), action, field="query")
        self.assertEqual(self.factory.opened, 0)

    def test_malformed_json_non_objects_invalid_utf8_and_unpaired_surrogates_are_safe(self):
        for action in ("edit", "reschedule"):
            for number, raw in enumerate((b"", b"{", ("{\"" + PRIVATE + "\":").encode(), b"null", b"[]", b"true", b"42", b'"text"', b'{"value":"\xff"}')):
                with self.subTest(action=action, raw_case=number):
                    self.assert_invalid(self.request(action, raw_body=raw), action)
            if action == "reschedule":
                for field in ("start", "end", "time_zone"):
                    body = schedule_body()
                    body[field] = "bad-\ud800"
                    self.assert_invalid(self.request(action, body), action, field=field)
            else:
                for field in ("title", "description", "location"):
                    body = details_body()
                    body["changes"][field] = "bad-\ud800"
                    self.assert_invalid(self.request(action, body), action, field=field)
                self.assert_invalid(self.request(body={**details_body(), "time_zone": "bad-\ud800"}), action, field="time_zone")
        self.assertEqual(self.factory.opened, 0)

    def test_setup_at_entry_differs_from_service_errors_and_cleanup_after_operation_start(self):
        for action in ("edit", "reschedule"):
            with self.subTest(action=action, boundary="entry"):
                self.factory.open_failure = google_calendar.CalendarSetupError(PRIVATE)
                self.assert_envelope(self.request(action), 503, action=action, status="failed", mutation_state="not_applied", error="calendar_setup_unavailable", fields=())
                self.assertEqual(self.gateway.calls, [])
                self.factory.open_failure = None
            for failure in (CalendarOperationError(PRIVATE), CalendarOperationError("HTTP 404 409 412 " + PRIVATE),
                            ValueError(PRIVATE), OSError(PRIVATE), RefreshError(PRIVATE), TransportError(PRIVATE),
                            sqlite3.OperationalError(PRIVATE), google_calendar.CalendarSetupError(PRIVATE)):
                with self.subTest(action=action, error=type(failure).__name__):
                    self.gateway.failure = failure
                    self.assert_unknown(self.request(action), action)
            self.gateway.failure = None
            for failure in (OSError(PRIVATE), google_calendar.CalendarSetupError(PRIVATE)):
                with self.subTest(action=action, boundary="cleanup", error=type(failure).__name__):
                    self.factory.close_failure = failure
                    self.assert_unknown(self.request(action), action)
            self.factory.close_failure = None
            self.gateway.calls.clear()
        self.assertEqual(self.factory.opened, self.factory.closed)

    def test_only_a_valid_typed_schedule_conflict_returns_409_and_snapshot_stays_minimal(self):
        body = schedule_body()
        conflict = replace(original_event("OtherInterview"), start=body["start"], end=body["end"])
        report = ConflictReport("conflict", "Genius Testes", body["start"], body["end"], ZONE, (conflict,))
        self.gateway.failure = CalendarConflictError(report)
        data = self.assert_envelope(self.request("reschedule"), 409, action="reschedule", status="conflict", mutation_state="not_applied", error="schedule_conflict", fields=())
        self.assertEqual(data["conflicts"][0]["event_id"], "OtherInterview")
        self.assertEqual(set(data["conflicts"][0]), EVENT_FIELDS)
        self.assert_unknown(self.request(), "edit")
        malformed_reports = (
            replace(report, status="available"), replace(report, conflicts=()), replace(report, conflicts=(conflict, conflict)),
            replace(report, start="2030-06-17T09:00:00-03:00"), replace(report, conflicts=(original_event(EVENT_ID),)),
        )
        for malformed in malformed_reports:
            with self.subTest(conflict_status=malformed.status, conflict_count=len(malformed.conflicts)):
                self.gateway.failure = CalendarConflictError(malformed)
                self.assert_unknown(self.request("reschedule"), "reschedule")
        self.gateway.failure = None
        self.factory.close_failure = CalendarConflictError(report)
        self.assert_unknown(self.request("reschedule"), "reschedule")

    def test_projection_excludes_provider_extensions_including_schedule_subclass_details(self):
        @dataclass(frozen=True)
        class PrivateDetails(EventDetails):
            internal_diagnostic: str = PRIVATE

        @dataclass(frozen=True)
        class PrivateSchedule(RescheduleResult):
            internal_diagnostic: str = PRIVATE

        @dataclass(frozen=True)
        class PrivateResult(DetailsResult):
            internal_diagnostic: str = PRIVATE

        before = PrivateDetails(**original_event().__dict__)
        schedule = schedule_body()
        self.gateway.schedule_result = PrivateSchedule("rescheduled", "Genius Testes", before,
            replace(before, start=schedule["start"], end=schedule["end"]), schedule["start"], schedule["end"], ZONE)
        self.gateway.details_result = PrivateResult("updated", "Genius Testes", before,
            replace(before, **details_body()["changes"]), details_body()["changes"])
        scheduled = self.assert_envelope(self.request("reschedule"), action="reschedule")
        self.assertEqual(set(scheduled["after"]), EVENT_FIELDS)
        edited = self.assert_envelope(self.request())
        self.assertEqual(set(edited["after"]), DETAILS_FIELDS)

    def test_invalid_provider_results_never_claim_confirmed_changes(self):
        before = original_event()
        schedule = schedule_body()
        good_schedule = RescheduleResult("rescheduled", "Genius Testes", before,
            replace(before, start=schedule["start"], end=schedule["end"]), schedule["start"], schedule["end"], ZONE)
        good_details = DetailsResult("updated", "Genius Testes", before, replace(before, **details_body()["changes"]), details_body()["changes"])
        invalid_schedule = (
            {}, replace(good_schedule, status="preview"), replace(good_schedule, after=None),
            replace(good_schedule, recovered=1), replace(good_schedule, calendar_name=""),
            replace(good_schedule, requested_start=before.start), replace(good_schedule, time_zone="UTC"),
            replace(good_schedule, before=replace(before, event_id="differentID")),
            replace(good_schedule, after=before), replace(good_schedule, status="unchanged"),
            replace(good_schedule, after=replace(good_schedule.after, title="bad-\ud800")),
        )
        invalid_details = (
            {}, replace(good_details, status="preview"), replace(good_details, after=None),
            replace(good_details, recovered="yes"), replace(good_details, calendar_name=""),
            replace(good_details, requested={"title": "unexpected"}),
            replace(good_details, requested={**good_details.requested, PRIVATE: PRIVATE}),
            replace(good_details, after=replace(good_details.after, start=schedule["start"])),
            replace(good_details, after=replace(good_details.after, event_id="differentID")),
            replace(good_details, status="unchanged"),
            replace(good_details, after=replace(good_details.after, description="bad-\ud800")),
        )
        for action, attribute, values in (("reschedule", "schedule_result", invalid_schedule), ("edit", "details_result", invalid_details)):
            for number, result in enumerate(values):
                with self.subTest(action=action, case=number):
                    setattr(self.gateway, attribute, result)
                    self.assert_unknown(self.request(action), action)
            setattr(self.gateway, attribute, None)
        self.assertEqual(self.factory.opened, self.factory.closed)

    def test_openapi_documents_protected_strict_update_bodies_and_conservative_failures(self):
        schema = self.app.openapi()
        for path, required in (("/api/v1/events/{event_id}/schedule", {"start", "end", "time_zone"}),
                               ("/api/v1/events/{event_id}", {"changes", "time_zone"})):
            with self.subTest(path=path):
                operation = schema["paths"][path]["patch"]
                self.assertIn({"ApiKeyBearer": []}, operation["security"])
                self.assertTrue({"200", "422", "502", "503"}.issubset(operation["responses"]))
                self.assertNotIn("404", operation["responses"])
                body_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
                body = schema["components"]["schemas"][body_ref]
                self.assertFalse(body["additionalProperties"])
                self.assertEqual(set(body["required"]), required)
        edit = schema["paths"]["/api/v1/events/{event_id}"]["patch"]
        edit_ref = edit["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        changes_ref = schema["components"]["schemas"][edit_ref]["properties"]["changes"]["$ref"].split("/")[-1]
        changes = schema["components"]["schemas"][changes_ref]
        self.assertFalse(changes["additionalProperties"])
        self.assertEqual(set(changes["properties"]), {"title", "description", "location"})
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
            db = Database(Path(temporary) / "updates.sqlite3")
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
        backend = UpdatesOnlyHttpFake()
        event = original_event()
        backend.saved[EVENT_ID] = {
            "id": EVENT_ID, "summary": event.title, "status": "confirmed", "etag": '"v1"',
            "description": event.description, "location": event.location, "eventType": "default", "transparency": "opaque",
            "htmlLink": event.html_link, "start": {"dateTime": event.start, "timeZone": ZONE},
            "end": {"dateTime": event.end, "timeZone": ZONE}, "visibility": "private",
            "attendees": [{"email": "fictitious@example.invalid"}], "reminders": {"useDefault": False},
        }
        return backend

    def test_http_sdk_sqlite_updates_both_operations_reads_state_and_replays_without_second_patch(self):
        backend = self.seeded_backend()
        original = deepcopy(backend.saved[EVENT_ID])
        with self.sdk_app(backend) as (app, db, counts):
            scheduled = self.assert_envelope(self.request("reschedule", app=app), action="reschedule")
            self.assertEqual(scheduled["result"], "rescheduled")
            saved_schedule = deepcopy(backend.saved[EVENT_ID])
            repeated = self.assert_envelope(self.request("reschedule", app=app), action="reschedule")
            self.assertEqual(repeated["result"], "unchanged")
            self.assertEqual(backend.saved[EVENT_ID], saved_schedule)
            body = {"changes": {"title": "  Entrevista final  ", "description": "", "location": None}, "time_zone": ZONE}
            edited = self.assert_envelope(self.request(body=body, app=app))
            self.assertEqual(edited["requested"], {"title": "Entrevista final", "description": ""})
            repeated_edit = self.assert_envelope(self.request(body=body, app=app))
            self.assertEqual(repeated_edit["result"], "unchanged")
            final = backend.saved[EVENT_ID]
            self.assertEqual(final["summary"], "Entrevista final")
            self.assertEqual(final["description"], "")
            self.assertEqual(final["location"], original["location"])
            self.assertEqual(final["start"]["dateTime"], schedule_body()["start"])
            self.assertEqual(final["end"]["dateTime"], schedule_body()["end"])
            for field in ("visibility", "attendees", "reminders", "htmlLink", "transparency"):
                self.assertEqual(final[field], original[field])
            writes = [call for call in backend.calls if call["method"] != "GET"]
            self.assertEqual([call["method"] for call in writes], ["PATCH", "PATCH"])
            self.assertEqual(set(writes[0]["body"]), {"start", "end"})
            self.assertEqual(writes[1]["body"], {"summary": "Entrevista final", "description": ""})
            for call in writes:
                self.assertEqual(call["query"]["sendUpdates"], "none")
                self.assertEqual(call["event_id"], EVENT_ID)
                self.assertTrue({key.lower(): value for key, value in call["headers"].items()}.get("if-match"))
            self.assertEqual(counts["opened"], 4)
            with db.connection() as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM creation_requests").fetchone()[0], 0)

    def test_http_sdk_schedule_conflict_does_not_patch_or_change_existing_state(self):
        backend = self.seeded_backend()
        other = deepcopy(backend.saved[EVENT_ID])
        other.update(id="OtherInterview", summary="Outro compromisso", start={"dateTime": schedule_body()["start"], "timeZone": ZONE}, end={"dateTime": schedule_body()["end"], "timeZone": ZONE})
        backend.saved["OtherInterview"] = other
        backend.page_size = 1
        backend.empty_first_page = True
        snapshot = deepcopy(backend.saved)
        with self.sdk_app(backend) as (app, _db, _counts):
            data = self.assert_envelope(self.request("reschedule", app=app), 409, action="reschedule", status="conflict", mutation_state="not_applied", error="schedule_conflict", fields=())
            self.assertEqual([event["event_id"] for event in data["conflicts"]], ["OtherInterview"])
            self.assertEqual(backend.saved, snapshot)
            self.assertTrue(all(call["method"] == "GET" for call in backend.calls))
            self.assertTrue(any(call["query"].get("pageToken") == "page-0" for call in backend.calls))

    def test_http_sdk_existing_empty_title_can_be_filled_or_preserved_during_details_edit(self):
        for raw_title in ("", " \t"):
            for changes in ({"title": "Entrevista com título"}, {"description": "Descrição atualizada"}):
                with self.subTest(raw_title_length=len(raw_title), changed_fields=list(changes)):
                    backend = self.seeded_backend()
                    backend.saved[EVENT_ID]["summary"] = raw_title
                    with self.sdk_app(backend) as (app, _db, _counts):
                        body = {"changes": changes, "time_zone": ZONE}
                        data = self.assert_envelope(self.request(body=body, app=app))
                        self.assertEqual(data["before"]["title"], raw_title)
                        self.assertEqual(data["after"]["title"], changes.get("title", raw_title))
                        self.assertEqual(backend.saved[EVENT_ID]["summary"], changes.get("title", raw_title))
                        self.assertEqual(len([call for call in backend.calls if call["method"] == "PATCH"]), 1)
                        replay = self.assert_envelope(self.request(body=body, app=app))
                        self.assertEqual(replay["result"], "unchanged")
                        self.assertEqual(len([call for call in backend.calls if call["method"] == "PATCH"]), 1)

    def test_http_sdk_412_never_becomes_generic_schedule_conflict_or_not_applied(self):
        for action in ("edit", "reschedule"):
            with self.subTest(action=action):
                backend = self.seeded_backend()

                def concurrent_update(_method, event_id, _body):
                    backend.saved[event_id]["etag"] = '"v99"'
                    backend.saved[event_id]["description"] = "Alteração concorrente"

                backend.before_write = concurrent_update
                with self.sdk_app(backend) as (app, _db, _counts):
                    self.assert_unknown(self.request(action, app=app), action)
                    writes = [call for call in backend.calls if call["method"] != "GET"]
                    self.assertEqual(len(writes), 1)
                    self.assertEqual((writes[0]["method"], writes[0]["status"]), ("PATCH", 412))
                    self.assertEqual(backend.saved[EVENT_ID]["description"], "Alteração concorrente")

    def test_http_sdk_absence_is_not_inferred_as_404_from_a_generic_mutation_error(self):
        for action in ("edit", "reschedule"):
            with self.subTest(action=action):
                backend = UpdatesOnlyHttpFake()
                with self.sdk_app(backend) as (app, _db, _counts):
                    self.assert_unknown(self.request(action, app=app), action)
                    self.assertTrue(all(call["method"] == "GET" for call in backend.calls))
                    self.assertTrue(any(call["status"] == 404 for call in backend.calls))
                    self.assertEqual(backend.saved, {})

    def test_http_sdk_lost_patch_response_recovers_via_read_without_repeating_write(self):
        for action in ("edit", "reschedule"):
            with self.subTest(action=action):
                backend = self.seeded_backend()
                backend.lose_response_once.add("PATCH")
                with self.sdk_app(backend) as (app, _db, _counts):
                    data = self.assert_envelope(self.request(action, app=app), action=action)
                    self.assertTrue(data["recovered"])
                    self.assertEqual(len([call for call in backend.calls if call["method"] == "PATCH"]), 1)
                    repeated = self.assert_envelope(self.request(action, app=app), action=action)
                    self.assertEqual(repeated["result"], "unchanged")
                    self.assertEqual(len([call for call in backend.calls if call["method"] == "PATCH"]), 1)

    def test_http_sdk_uncertain_final_read_returns_unknown_and_later_replay_only_reads(self):
        for action in ("edit", "reschedule"):
            with self.subTest(action=action):
                backend = self.seeded_backend()
                backend.lose_response_once.add("PATCH")
                backend.fail_reads_after_write = 3
                with self.sdk_app(backend) as (app, _db, _counts):
                    self.assert_unknown(self.request(action, app=app), action)
                    self.assertEqual(len([call for call in backend.calls if call["method"] == "PATCH"]), 1)
                    if action == "reschedule":
                        self.assertEqual(backend.saved[EVENT_ID]["start"]["dateTime"], schedule_body()["start"])
                    else:
                        self.assertEqual(backend.saved[EVENT_ID]["summary"], details_body()["changes"]["title"])
                    backend.fail_reads_after_write = 0
                    checked = self.assert_envelope(self.request(action, app=app), action=action)
                    self.assertEqual(checked["result"], "unchanged")
                    self.assertEqual(len([call for call in backend.calls if call["method"] == "PATCH"]), 1)


if __name__ == "__main__":
    unittest.main()
