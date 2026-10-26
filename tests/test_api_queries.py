"""Consultas v1 por ASGI, com autenticação real e calendário controlado."""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
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
from app.services.agenda import AgendaEvent, CalendarListing
from app.services.event_details import EventDetails
from app.services.event_selection import EventLookup
from app.services.interviews import CalendarOperationError
from app.settings import Settings
from calendar_http_fake import CalendarHttpFake
from test_api_security import InjectedDatabase, TOKEN, invoke_asgi


PRIVATE = "PRIVATE_QUERY_DIAGNOSTIC_DO_NOT_ECHO"
CALENDAR_NAME = "Genius Testes"
ZONE = "America/Recife"
EVENT_FIELDS = {
    "event_id", "title", "start", "end", "all_day", "time_zone", "status", "html_link",
}


def event(event_id="OpaqueMixedID", **changes):
    value = AgendaEvent(
        event_id=event_id, title="Entrevista fictícia",
        start="2030-06-17T10:00:00-03:00", end="2030-06-17T10:30:00-03:00",
        all_day=False, time_zone=ZONE, status="confirmed",
        html_link="https://example.invalid/event",
    )
    return replace(value, **changes)


class QueryGateway:
    def __init__(self):
        self.events = (event(),)
        self.lookup = event()
        self.calls = []
        self.failure = None
        self.listing_override = None
        self.lookup_override = None

    def list_events(self, period):
        self.calls.append(("list", period))
        if self.failure is not None:
            raise self.failure
        if self.listing_override is not None:
            return self.listing_override
        return CalendarListing(CALENDAR_NAME, self.events)

    def get_event(self, event_id, *, time_zone):
        self.calls.append(("get", event_id, time_zone))
        if self.failure is not None:
            raise self.failure
        if self.lookup_override is not None:
            return self.lookup_override
        return EventLookup(CALENDAR_NAME, self.lookup)


class QueryFactory:
    def __init__(self, gateway):
        self.gateway = gateway
        self.opened = 0
        self.closed = 0

    @contextmanager
    def __call__(self):
        self.opened += 1
        try:
            yield self.gateway
        finally:
            self.closed += 1


class ApiQueriesTests(unittest.TestCase):
    def setUp(self):
        self.config = Settings(_env_file=None, app_name="Agenda HTTP de teste", app_env="test", api_auth_token=TOKEN)
        self.db = InjectedDatabase()
        self.gateway = QueryGateway()
        self.factory = QueryFactory(self.gateway)
        self.app = create_app(config=self.config, db=self.db, calendar_gateway_factory=self.factory)
        self.logs = StringIO()
        logger = logging.getLogger()
        handler = logging.StreamHandler(self.logs)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        self.addCleanup(logger.setLevel, old_level)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(self.check_logs)

    def check_logs(self):
        self.assertNotIn(TOKEN, self.logs.getvalue())
        self.assertNotIn(PRIVATE, self.logs.getvalue())

    def request(self, *, event_id=None, query=(), authorized=True, app=None):
        path = "/api/v1/events" if event_id is None else "/api/v1/events/" + quote(event_id, safe="")
        target = self.app if app is None else app

        async def decoded_asgi(scope, receive, send):
            # O servidor ASGI fornece path decodificado e raw_path percent-encoded.
            # invoke_asgi conserva bytes ASCII; esta ponte reproduz essa decodificação.
            await target({**scope, "path": unquote(scope["path"])}, receive, send)

        headers = [(b"x-request-id", b"query-test-001")]
        if authorized:
            headers.append((b"authorization", f"Bearer {TOKEN}".encode("ascii")))
        response = asyncio.run(invoke_asgi(
            decoded_asgi, path, headers=headers, query=urlencode(query).encode("ascii"),
        ))
        safe_output = json.dumps(response, ensure_ascii=False)
        self.assertNotIn(TOKEN, safe_output)
        self.assertNotIn(PRIVATE, safe_output)
        return response

    def assert_envelope(self, response, http_status, *, action, status="succeeded", error=None, fields=()):
        code, headers, payload = response
        self.assertEqual(code, http_status)
        self.assertEqual(set(payload), {
            "api_version", "request_id", "operation_id", "status", "action",
            "mutation_state", "message", "data", "error",
        })
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], "query-test-001")
        self.assertEqual(headers["x-request-id"], "query-test-001")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["action"], action)
        self.assertEqual(payload["status"], status)
        self.assertEqual(payload["mutation_state"], "not_applicable")
        if error is None:
            self.assertIsNone(payload["error"])
        else:
            self.assertEqual(payload["error"], {"code": error, "retryable": False, "fields": list(fields)})
        return payload["data"]

    def test_period_queries_use_existing_civil_boundaries_and_explicit_defaults(self):
        cases = (
            ((), "2030-06-17T00:00:00-03:00", "2030-06-18T00:00:00-03:00", ZONE),
            ((("period", "day"),), "2030-06-17T00:00:00-03:00", "2030-06-18T00:00:00-03:00", ZONE),
            ((("period", "week"),), "2030-06-17T00:00:00-03:00", "2030-06-24T00:00:00-03:00", ZONE),
            ((("period", "month"),), "2030-06-01T00:00:00-03:00", "2030-07-01T00:00:00-03:00", ZONE),
            ((("time_zone", "UTC"),), "2030-06-17T00:00:00+00:00", "2030-06-18T00:00:00+00:00", "UTC"),
        )
        for extra_query, start, end, zone in cases:
            with self.subTest(query=extra_query):
                self.gateway.events = (event(time_zone=zone),)
                response = self.request(query=(("date", "2030-06-17"), *extra_query))
                data = self.assert_envelope(response, 200, action="list")
                self.assertEqual(data["calendar_name"], CALENDAR_NAME)
                self.assertEqual(data["period"], {"start": start, "end": end, "time_zone": zone})
                self.assertEqual(set(data["events"][0]), EVENT_FIELDS)
                _, supplied_period = self.gateway.calls[-1]
                self.assertEqual(supplied_period.start.isoformat(), start)
                self.assertEqual(supplied_period.end.isoformat(), end)
                self.assertEqual(supplied_period.time_zone, zone)
        self.assertEqual(self.factory.opened, len(cases))
        self.assertEqual(self.factory.closed, len(cases))
        self.assertEqual(self.db.initializations, 0)

    def test_exact_ids_preserve_case_occurrence_unicode_and_encoded_slash(self):
        ids = ("CaseSensitiveID", "MixedID_20300617T130000Z", "EventoÇãñ", "Opaque/CaseSensitiveID")
        for event_id in ids:
            with self.subTest(event_id=event_id):
                self.gateway.lookup = event(event_id)
                data = self.assert_envelope(self.request(event_id=event_id), 200, action="get")
                self.assertEqual(data["event"]["event_id"], event_id)
                self.assertEqual(self.gateway.calls[-1], ("get", event_id, ZONE))
        self.gateway.lookup = event("CaseSensitiveID", time_zone="UTC")
        utc = self.request(event_id="CaseSensitiveID", query=(("time_zone", "UTC"),))
        self.assert_envelope(utc, 200, action="get")
        self.assertEqual(self.gateway.calls[-1], ("get", "CaseSensitiveID", "UTC"))
        self.assertEqual(self.factory.opened, self.factory.closed)

    def test_minimal_public_dto_excludes_details_subclass_fields_and_preserves_all_day_dates(self):
        detailed = EventDetails(
            **event().__dict__, description=PRIVATE + "_description", location=PRIVATE + "_location",
        )
        all_day = event("AllDayID", start="2030-06-17", end="2030-06-18", all_day=True)
        self.gateway.events = (detailed, all_day)
        listing = self.assert_envelope(self.request(query=(("date", "2030-06-17"),)), 200, action="list")
        self.assertEqual(len(listing["events"]), 2)
        for public_event in listing["events"]:
            self.assertEqual(set(public_event), EVENT_FIELDS)
        self.assertEqual(listing["events"][1]["end"], "2030-06-18")
        self.assertTrue(listing["events"][1]["all_day"])
        self.gateway.lookup = detailed
        found = self.assert_envelope(self.request(event_id=detailed.event_id), 200, action="get")
        self.assertEqual(set(found["event"]), EVENT_FIELDS)
        self.assertEqual(self.factory.closed, 2)

    def test_empty_listing_absent_lookup_and_failed_lookup_remain_distinct(self):
        self.gateway.events = ()
        empty = self.assert_envelope(self.request(query=(("date", "2030-06-17"),)), 200, action="list")
        self.assertEqual(empty["events"], [])
        self.gateway.lookup = None
        missing = self.assert_envelope(
            self.request(event_id="AbsentID"), 404, action="get", status="not_found", error="event_not_found",
        )
        self.assertEqual(missing, {"calendar_name": CALENDAR_NAME, "event": None})
        self.gateway.failure = CalendarOperationError(PRIVATE)
        self.assert_envelope(
            self.request(event_id="AbsentID"), 502, action="get", status="failed", error="operation_failed",
        )
        self.assertEqual(self.factory.opened, 3)
        self.assertEqual(self.factory.closed, 3)

    def test_invalid_dates_periods_zones_and_duplicate_parameters_do_not_open_the_gateway(self):
        cases = (
            ((), "date"),
            ((("date", ""),), "date"),
            ((("date", "2030-06-17T00:00:00-03:00"),), "date"),
            ((("date", "2030-02-30"),), "date"),
            ((("date", "2030-6-17"),), "date"),
            ((("date", "2030-06-17 "),), "date"),
            ((("date", PRIVATE),), "date"),
            ((("date", "2030-06-17"), ("period", "")), "period"),
            ((("date", "2030-06-17"), ("period", "year")), "period"),
            ((("date", "2030-06-17"), ("time_zone", "")), "time_zone"),
            ((("date", "2030-06-17"), ("time_zone", PRIVATE)), "time_zone"),
            ((("date", "2030-06-17"), ("date", "2030-06-17")), "date"),
            ((("date", "2030-06-17"), ("period", "day"), ("period", "day")), "period"),
            ((("date", "2030-06-17"), ("time_zone", "UTC"), ("time_zone", "UTC")), "time_zone"),
            ((("date", "2030-06-17"), (PRIVATE, PRIVATE)), "query"),
        )
        for case_number, (query, field) in enumerate(cases):
            with self.subTest(field=field, case_number=case_number):
                self.assert_envelope(
                    self.request(query=query), 422, action="list", status="failed", error="invalid_request", fields=(field,),
                )
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.factory.closed, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_invalid_lookup_id_or_query_is_rejected_without_reflecting_input(self):
        cases = (
            ("Bad Event", (), "event_id"),
            ("Bad\x00Event", (), "event_id"),
            ("A" * 1025, (), "event_id"),
            ("OpaqueMixedID", (("time_zone", ""),), "time_zone"),
            ("OpaqueMixedID", (("time_zone", "UTC"), ("time_zone", "UTC")), "time_zone"),
            ("OpaqueMixedID", (("date", "2030-06-17"),), "query"),
            ("OpaqueMixedID", ((PRIVATE, PRIVATE),), "query"),
        )
        for event_id, query, field in cases:
            with self.subTest(field=field, id_length=len(event_id)):
                self.assert_envelope(
                    self.request(event_id=event_id, query=query), 422,
                    action="get", status="failed", error="invalid_request", fields=(field,),
                )
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_authentication_failure_prevents_opening_factory_for_both_routes(self):
        for event_id, query in ((None, (("date", "2030-06-17"),)), ("OpaqueMixedID", ())):
            response = self.request(event_id=event_id, query=query, authorized=False)
            self.assertEqual(response[0], 401)
            self.assertEqual(response[1]["www-authenticate"], "Bearer")
            self.assertIsNone(response[2]["action"])
            self.assertEqual(response[2]["mutation_state"], "not_applied")
            self.assertEqual(response[2]["error"]["code"], "unauthorized")
        self.assertEqual(self.factory.opened, 0)
        self.assertEqual(self.gateway.calls, [])

    def test_provider_failures_are_safe_and_close_factory_without_becoming_input_errors(self):
        failures = (
            (google_calendar.CalendarSetupError(PRIVATE), 503, "calendar_setup_unavailable"),
            (CalendarOperationError(PRIVATE), 502, "operation_failed"),
            (ValueError(PRIVATE), 502, "operation_failed"),
            (OSError(PRIVATE), 502, "operation_failed"),
            (RefreshError(PRIVATE), 502, "operation_failed"),
            (TransportError(PRIVATE), 502, "operation_failed"),
            (sqlite3.OperationalError(PRIVATE), 502, "operation_failed"),
        )
        for failure, code, error in failures:
            self.gateway.failure = failure
            for event_id, query, action in ((None, (("date", "2030-06-17"),), "list"), ("OpaqueMixedID", (), "get")):
                with self.subTest(exception=type(failure).__name__, action=action):
                    self.assert_envelope(
                        self.request(event_id=event_id, query=query), code,
                        action=action, status="failed", error=error,
                    )
        self.assertEqual(self.factory.opened, len(failures) * 2)
        self.assertEqual(self.factory.closed, self.factory.opened)

    def test_invalid_provider_shapes_ids_statuses_and_times_are_not_success_or_absence(self):
        bad_events = (
            event(status=PRIVATE), event(event_id=""),
            event(start=PRIVATE), event(end="2030-06-17T09:00:00-03:00"),
        )
        for bad in bad_events:
            with self.subTest(invalid_field="status" if bad.status == PRIVATE else "event"):
                self.gateway.events = (bad,)
                self.assert_envelope(
                    self.request(query=(("date", "2030-06-17"),)), 502,
                    action="list", status="failed", error="operation_failed",
                )
        self.gateway.listing_override = CalendarListing(CALENDAR_NAME, ("invalid-event",))
        self.assert_envelope(
            self.request(query=(("date", "2030-06-17"),)), 502,
            action="list", status="failed", error="operation_failed",
        )
        for wrong in (event("DifferentID"), event(status="cancelled"), event(status=PRIVATE)):
            with self.subTest(lookup_status=wrong.status if wrong.status != PRIVATE else "private"):
                self.gateway.lookup = wrong
                self.assert_envelope(
                    self.request(event_id="OpaqueMixedID"), 502,
                    action="get", status="failed", error="operation_failed",
                )
        self.assertEqual(self.factory.closed, self.factory.opened)

    def test_openapi_documents_types_bearer_and_failure_responses(self):
        schema = self.app.openapi()
        routes = schema["paths"]
        self.assertIn("/api/v1/events/{event_id}", routes)
        self.assertNotIn("/api/v1/events/{event_id:path}", routes)
        for path in ("/api/v1/events", "/api/v1/events/{event_id}"):
            operation = routes[path]["get"]
            self.assertIn({"ApiKeyBearer": []}, operation["security"])
            self.assertTrue({"200", "422", "502", "503"}.issubset(operation["responses"]))
        listing = {parameter["name"]: parameter for parameter in routes["/api/v1/events"]["get"]["parameters"]}
        self.assertEqual(set(listing), {"date", "period", "time_zone"})
        self.assertTrue(listing["date"]["required"])
        self.assertEqual(listing["date"]["schema"]["type"], "string")
        self.assertEqual(listing["period"]["schema"]["default"], "day")
        self.assertEqual(listing["time_zone"]["schema"]["default"], ZONE)
        lookup = {parameter["name"]: parameter for parameter in routes["/api/v1/events/{event_id}"]["get"]["parameters"]}
        self.assertEqual(set(lookup), {"event_id", "time_zone"})
        self.assertEqual(lookup["event_id"]["in"], "path")
        self.assertEqual(lookup["event_id"]["schema"]["type"], "string")
        self.assertIn("404", routes["/api/v1/events/{event_id}"]["get"]["responses"])
        self.assertNotIn(TOKEN, json.dumps(schema))

    def test_http_routes_traverse_real_sdk_and_transport_paginate_and_only_read_calendar(self):
        backend = CalendarHttpFake()
        backend.empty_first_page = True
        backend.page_size = 1
        for event_id, hour in (("PagedEventA", "10"), ("MixedOccurrence_20300617T140000Z", "11")):
            backend.saved[event_id] = {
                "id": event_id, "summary": "Entrevista de demonstração", "status": "confirmed",
                "etag": '"v1"', "htmlLink": "https://example.invalid/event",
                "description": PRIVATE, "location": PRIVATE,
                "extendedProperties": {"private": {"private-note": PRIVATE}},
                "start": {"dateTime": f"2030-06-17T{hour}:00:00-03:00", "timeZone": ZONE},
                "end": {"dateTime": f"2030-06-17T{hour}:30:00-03:00", "timeZone": ZONE},
            }
        initial = deepcopy(backend.saved)
        sessions = []
        counts = {"opened": 0, "closed": 0}

        def local_session():
            session = backend.session()
            original_request = session.request

            def only_get(method, url, **options):
                self.assertEqual(method.upper(), "GET", "Uma consulta não pode enviar escrita ao calendário.")
                return original_request(method, url, **options)

            session.request = only_get
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
            db = Database(Path(temporary) / "query-metadata.sqlite3")
            settings = SimpleNamespace(google_calendar_name=CALENDAR_NAME, google_calendar_id=backend.calendar["id"])
            with patch("app.google_transport.requests.Session", side_effect=local_session), \
                    patch.object(google_calendar, "database", db), \
                    patch.object(google_calendar, "settings", settings), \
                    patch.object(google_calendar, "get_credentials", side_effect=AssertionError("OAuth real proibido.")):
                app = create_app(config=self.config, db=InjectedDatabase(), calendar_gateway_factory=sdk_factory)
                listing = self.assert_envelope(
                    self.request(app=app, query=(("date", "2030-06-17"),)), 200, action="list",
                )
                self.assertEqual([item["event_id"] for item in listing["events"]], list(backend.saved))
                occurrence_id = "MixedOccurrence_20300617T140000Z"
                found = self.assert_envelope(self.request(app=app, event_id=occurrence_id), 200, action="get")
                self.assertEqual(found["event"]["event_id"], occurrence_id)
                self.assertEqual(db.get_metadata("google_calendar_id"), backend.calendar["id"])
        self.assertEqual(counts, {"opened": 2, "closed": 2})
        self.assertTrue(sessions)
        self.assertTrue(all(session.closed for session in sessions))
        self.assertEqual(backend.saved, initial)
        self.assertTrue(all(call["method"] == "GET" for call in backend.calls))
        self.assertTrue(any(call["query"].get("pageToken") == "page-0" for call in backend.calls))
        self.assertTrue(any(call["query"].get("pageToken") == "page-1" for call in backend.calls))
        looked_up = [call for call in backend.calls if call["event_id"] is not None]
        self.assertEqual([call["event_id"] for call in looked_up], [occurrence_id])


if __name__ == "__main__":
    unittest.main()
