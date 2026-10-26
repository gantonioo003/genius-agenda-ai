"""Autorização, ciclo do cliente e leituras interrompidas com I/O inteiramente simulado."""

from copy import deepcopy
from datetime import date
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
import httplib2

from app import google_calendar as connector
from app.services.agenda import day_period
from app.services.interviews import CalendarOperationError


PRIVATE = "secret=NAO_MOSTRAR_PRIVATE token@example.invalid /private/token.json"
EVENT_ID = "AbC123_20261023T190000Z"
CALENDAR = {
    "id": "calendar@example.invalid", "summary": "Genius Testes",
    "timeZone": "America/Recife", "accessRole": "owner", "primary": False,
}


def http_error(status):
    return HttpError(
        httplib2.Response({"status": str(status), "reason": PRIVATE}),
        b'{"error":{"message":"NAO_MOSTRAR_PRIVATE"}}',
        uri="https://example.invalid/?" + PRIVATE,
    )


def event(**changes):
    return {
        "id": EVENT_ID, "summary": "Entrevista fictícia", "status": "confirmed",
        "start": {"dateTime": "2026-10-23T16:00:00-03:00"},
        "end": {"dateTime": "2026-10-23T16:30:00-03:00"},
    } | changes


class NoRealDatabaseTests(unittest.TestCase):
    def setUp(self):
        forbidden = Mock(name="real_database_forbidden")
        for method in ("initialize", "get_metadata", "set_metadata", "calendar_write_lock", "reserve_creation"):
            getattr(forbidden, method).side_effect = AssertionError("Banco real proibido neste teste.")
        guard = patch.object(connector, "database", forbidden)
        guard.start()
        self.addCleanup(guard.stop)

    def assert_safe(self, error):
        self.assertNotIn(PRIVATE, str(error))
        self.assertNotIn("NAO_MOSTRAR_PRIVATE", str(error))
        self.assertNotIn("token@example.invalid", str(error))
        self.assertNotIn("/private/token.json", str(error))


class CredentialFailureTests(NoRealDatabaseTests):
    def setUp(self):
        super().setUp()
        self.token = Mock(name="fake_token_path")
        self.token.exists.return_value = True
        self.credentials = Mock(name="fake_credentials")
        self.credentials.has_scopes.return_value = True
        self.credentials.valid = False
        self.credentials.expired = True
        self.credentials.refresh_token = "fake-refresh-only"
        values = {
            "settings": SimpleNamespace(google_token_file=Path("fake-token.json")),
            "local_path": Mock(return_value=self.token),
            "Request": Mock(name="request_factory_without_network"),
            "save_token": Mock(name="save_without_files"),
            "build": Mock(side_effect=AssertionError("Discovery real proibido.")),
            "InstalledAppFlow": Mock(side_effect=AssertionError("Login real proibido.")),
        }
        for name, value in values.items():
            guard = patch.object(connector, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.loader = patch.object(connector.Credentials, "from_authorized_user_file", return_value=self.credentials)
        self.from_file = self.loader.start()
        self.addCleanup(self.loader.stop)

    def test_valid_cached_credentials_do_not_refresh_or_write(self):
        self.credentials.valid = True

        result = connector.get_credentials()

        self.assertIs(result, self.credentials)
        self.credentials.has_scopes.assert_called_once_with(connector.SCOPES)
        self.credentials.refresh.assert_not_called()
        connector.Request.assert_not_called()
        connector.save_token.assert_not_called()

    def test_refresh_request_has_twenty_second_timeout_and_overrides_unbounded_value(self):
        response = object()
        request = connector.Request.return_value
        request.return_value = response

        def refresh(callback):
            self.assertIs(callback("https://example.invalid/token", method="POST", timeout=999), response)

        self.credentials.refresh.side_effect = refresh

        self.assertIs(connector.get_credentials(), self.credentials)

        request.assert_called_once_with("https://example.invalid/token", method="POST", timeout=20)
        connector.save_token.assert_called_once_with(self.credentials, self.token)

    def test_revoked_refresh_is_safe_and_requests_reauthorization(self):
        self.credentials.refresh.side_effect = RefreshError(PRIVATE)

        with self.assertRaises(connector.CalendarSetupError) as caught:
            connector.get_credentials()

        self.assert_safe(caught.exception)
        self.assertIn("--authorize", str(caught.exception))
        connector.save_token.assert_not_called()

    def test_network_failure_during_refresh_is_safe_and_does_not_save(self):
        for error in (TransportError(PRIVATE), OSError(PRIVATE), httplib2.HttpLib2Error(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.credentials.refresh.side_effect = error
                with self.assertRaises(connector.CalendarSetupError) as caught:
                    connector.get_credentials()
                self.assert_safe(caught.exception)
                connector.save_token.assert_not_called()

    def test_invalid_token_content_never_leaks_parser_details(self):
        for error in (ValueError(PRIVATE), KeyError(PRIVATE), TypeError(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.from_file.side_effect = error
                with self.assertRaises(connector.CalendarSetupError) as caught:
                    connector.get_credentials()
                self.assert_safe(caught.exception)
                self.assertIn("--authorize", str(caught.exception))
        self.credentials.refresh.assert_not_called()
        connector.save_token.assert_not_called()

    def test_missing_token_does_not_launch_login_without_authorize(self):
        self.token.exists.return_value = False

        with self.assertRaises(connector.CalendarSetupError) as caught:
            connector.get_credentials()

        self.assertIn("--authorize", str(caught.exception))
        self.from_file.assert_not_called()
        connector.InstalledAppFlow.assert_not_called()
        connector.save_token.assert_not_called()

    def test_missing_scopes_does_not_refresh_or_write(self):
        self.credentials.has_scopes.return_value = False

        with self.assertRaises(connector.CalendarSetupError) as caught:
            connector.get_credentials()

        self.assertIn("--authorize", str(caught.exception))
        self.credentials.refresh.assert_not_called()
        connector.save_token.assert_not_called()


class ClientLifecycleFailureTests(NoRealDatabaseTests):
    def setUp(self):
        super().setUp()
        self.credentials = Mock(name="fake_credentials")
        self.transport = Mock(name="fake_transport")
        self.http = Mock(name="fake_authorized_http")
        self.service = Mock(name="fake_discovery_service")
        for name, value in {
            "get_credentials": Mock(return_value=self.credentials),
            "SingleAttemptHttp": Mock(return_value=self.transport),
            "AuthorizedHttp": Mock(return_value=self.http),
            "build": Mock(return_value=self.service),
        }.items():
            guard = patch.object(connector, name, value)
            guard.start()
            self.addCleanup(guard.stop)

    def test_client_disables_implicit_write_retry_and_closes_service(self):
        with connector.calendar_client() as service:
            self.assertIs(service, self.service)

        connector.get_credentials.assert_called_once_with(interactive=False)
        connector.SingleAttemptHttp.assert_called_once_with(timeout=20)
        connector.AuthorizedHttp.assert_called_once_with(
            self.credentials, http=self.transport, max_refresh_attempts=0,
        )
        connector.build.assert_called_once_with("calendar", "v3", http=self.http, cache_discovery=False)
        self.service.close.assert_called_once_with()
        self.http.close.assert_not_called()

    def test_discovery_failure_closes_http_and_preserves_original_failure(self):
        original = TransportError(PRIVATE)
        connector.build.side_effect = original

        with self.assertRaises(TransportError) as caught:
            with connector.calendar_client():
                self.fail("Discovery interrompido não pode abrir o contexto.")

        self.assertIs(caught.exception, original)
        self.http.close.assert_called_once_with()
        self.service.close.assert_not_called()

    def test_authorized_http_constructor_failure_closes_original_transport(self):
        original = TransportError(PRIVATE)
        connector.AuthorizedHttp.side_effect = original

        with self.assertRaises(TransportError) as caught:
            with connector.calendar_client():
                self.fail("Cliente não pode abrir se o transporte autorizado falhou.")

        self.assertIs(caught.exception, original)
        self.transport.close.assert_called_once_with()
        connector.build.assert_not_called()
        self.http.close.assert_not_called()

    def test_transport_cleanup_does_not_mask_authorized_http_constructor_failure(self):
        original = RuntimeError("Construção interrompida.")
        connector.AuthorizedHttp.side_effect = original
        self.transport.close.side_effect = TransportError(PRIVATE)

        with self.assertLogs(connector.__name__, level="WARNING") as logs:
            with self.assertRaises(RuntimeError) as caught:
                with connector.calendar_client():
                    self.fail("Cliente não pode abrir.")

        self.assertIs(caught.exception, original)
        self.assert_safe(" ".join(logs.output))

    def test_missing_auth_is_safe_before_constructing_any_transport(self):
        for error in (RefreshError(PRIVATE), TransportError(PRIVATE), OSError(PRIVATE), httplib2.HttpLib2Error(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                connector.get_credentials.side_effect = error
                with self.assertRaises(connector.CalendarSetupError) as caught:
                    with connector.calendar_client():
                        self.fail("Cliente não pode abrir sem autorização.")
                self.assert_safe(caught.exception)
        connector.SingleAttemptHttp.assert_not_called()
        connector.AuthorizedHttp.assert_not_called()
        connector.build.assert_not_called()

    def test_cleanup_network_failure_preserves_completed_result_and_safe_log(self):
        for error in (TransportError(PRIVATE), OSError(PRIVATE), httplib2.HttpLib2Error(PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.service.close.side_effect = error
                result = None
                with self.assertLogs(connector.__name__, level="WARNING") as logs:
                    with connector.calendar_client() as service:
                        result = service
                self.assertIs(result, self.service)
                self.assert_safe(" ".join(logs.output))

    def test_cleanup_network_failure_does_not_mask_original_body_error(self):
        original = RuntimeError("Erro original controlado.")
        self.service.close.side_effect = TransportError(PRIVATE)

        with self.assertLogs(connector.__name__, level="WARNING") as logs:
            with self.assertRaises(RuntimeError) as caught:
                with connector.calendar_client():
                    raise original

        self.assertIs(caught.exception, original)
        self.assert_safe(" ".join(logs.output))

    def test_cleanup_network_failure_does_not_mask_original_discovery_error(self):
        original = RuntimeError("Discovery interrompido.")
        connector.build.side_effect = original
        self.http.close.side_effect = TransportError(PRIVATE)

        with self.assertLogs(connector.__name__, level="WARNING") as logs:
            with self.assertRaises(RuntimeError) as caught:
                with connector.calendar_client():
                    self.fail("Não pode abrir o contexto.")

        self.assertIs(caught.exception, original)
        self.assert_safe(" ".join(logs.output))


class CalendarDiscoveryFailureTests(NoRealDatabaseTests):
    def setUp(self):
        super().setUp()
        self.service = Mock(name="fake_calendar_discovery")
        self.request = self.service.calendarList.return_value.list.return_value
        self.db = Mock(name="fake_metadata_database")
        self.db.get_metadata.return_value = None
        for name, value in {
            "settings": SimpleNamespace(google_calendar_name="Genius Testes", google_calendar_id=""),
            "database": self.db,
            "get_credentials": Mock(side_effect=AssertionError("Não deve abrir credenciais.")),
        }.items():
            guard = patch.object(connector, name, value)
            guard.start()
            self.addCleanup(guard.stop)

    def assert_database_untouched(self):
        self.db.initialize.assert_not_called()
        self.db.get_metadata.assert_not_called()
        self.db.set_metadata.assert_not_called()

    def test_complete_paginated_discovery_pins_only_after_last_page(self):
        other = dict(CALENDAR, id="other@example.invalid", summary="Outra agenda")

        def execute(**options):
            self.assert_database_untouched()
            if self.request.execute.call_count == 1:
                return {"items": [other], "nextPageToken": "next"}
            return {"items": [deepcopy(CALENDAR)]}

        self.request.execute.side_effect = execute

        self.assertEqual(connector.resolve_calendar(self.service), CALENDAR)

        calls = self.service.calendarList.return_value.list.call_args_list
        self.assertEqual([item.kwargs["pageToken"] for item in calls], [None, "next"])
        self.assertEqual(self.request.execute.call_args_list, [call(num_retries=2), call(num_retries=2)])
        self.db.initialize.assert_called_once_with()
        self.db.set_metadata.assert_has_calls([
            call("google_calendar_id", CALENDAR["id"]),
            call("google_calendar_name", CALENDAR["summary"]),
        ])

    def test_malformed_discovery_response_fails_before_database(self):
        pages = (
            None, [], {"items": None}, {"items": {}}, {"items": [None]},
            {"items": [{"id": None, "summary": "Genius Testes"}]},
            {"items": [dict(CALENDAR, summary=None)]},
            {"items": [deepcopy(CALENDAR)], "nextPageToken": []},
            {"items": [deepcopy(CALENDAR)], "nextPageToken": "   "},
        )
        for page in pages:
            with self.subTest(page=page):
                self.request.execute.return_value = page
                with self.assertRaises(connector.CalendarSetupError) as caught:
                    connector.resolve_calendar(self.service)
                self.assert_safe(caught.exception)
                self.assert_database_untouched()

    def test_repeated_discovery_page_token_does_not_pin_partial_data(self):
        self.request.execute.side_effect = [
            {"items": [deepcopy(CALENDAR)], "nextPageToken": "repeat"},
            {"items": [], "nextPageToken": "repeat"},
        ]

        with self.assertRaises(connector.CalendarSetupError):
            connector.resolve_calendar(self.service)

        self.assertEqual(self.request.execute.call_count, 2)
        self.assert_database_untouched()

    def test_discovery_network_or_http_failure_is_safe_before_database(self):
        for failure in (TransportError(PRIVATE), OSError(PRIVATE), httplib2.HttpLib2Error(PRIVATE), http_error(403)):
            with self.subTest(failure=type(failure).__name__):
                self.request.execute.side_effect = failure
                with self.assertRaises(connector.CalendarSetupError) as caught:
                    connector.resolve_calendar(self.service)
                self.assert_safe(caught.exception)
                self.assert_database_untouched()

    def test_failure_after_a_valid_discovery_page_discards_partial_data(self):
        self.request.execute.side_effect = [
            {"items": [deepcopy(CALENDAR)], "nextPageToken": "next"},
            TransportError(PRIVATE),
        ]

        with self.assertRaises(connector.CalendarSetupError) as caught:
            connector.resolve_calendar(self.service)

        self.assert_safe(caught.exception)
        self.assert_database_untouched()

    def test_unavailable_or_incompatible_metadata_store_is_safe(self):
        self.request.execute.return_value = {"items": [deepcopy(CALENDAR)]}
        for failure in (sqlite3.OperationalError(PRIVATE), RuntimeError(PRIVATE)):
            with self.subTest(failure=type(failure).__name__):
                self.db.initialize.side_effect = failure
                with self.assertRaises(connector.CalendarSetupError) as caught:
                    connector.resolve_calendar(self.service)
                self.assert_safe(caught.exception)
                self.db.get_metadata.assert_not_called()
                self.db.set_metadata.assert_not_called()


class EventReadFailureTests(NoRealDatabaseTests):
    def setUp(self):
        super().setUp()
        self.service = Mock(name="fake_event_service")
        self.events = self.service.events.return_value
        self.gateway = connector.GoogleCalendarGateway(self.service)
        self.period = day_period(date(2026, 10, 23), "America/Recife")
        guard = patch.object(connector, "resolve_calendar", return_value=deepcopy(CALENDAR))
        guard.start()
        self.addCleanup(guard.stop)

    def test_cancelled_wrong_id_is_not_treated_as_missing(self):
        self.events.get.return_value.execute.return_value = {"id": "different-id", "status": "cancelled"}

        with self.assertRaises(CalendarOperationError):
            self.gateway.get_event(EVENT_ID)

    def test_cancelled_matching_id_and_definite_http_absence_are_missing(self):
        self.events.get.return_value.execute.return_value = {"id": EVENT_ID, "status": "cancelled"}
        self.assertIsNone(self.gateway.get_event(EVENT_ID).event)
        for status in (404, 410):
            with self.subTest(status=status):
                self.events.get.return_value.execute.side_effect = http_error(status)
                self.assertIsNone(self.gateway.get_event(EVENT_ID).event)

    def test_get_transport_and_permission_failures_never_become_not_found(self):
        for failure in (TransportError(PRIVATE), OSError(PRIVATE), httplib2.HttpLib2Error(PRIVATE),
                        http_error(403), http_error(500)):
            with self.subTest(failure=type(failure).__name__):
                self.events.get.return_value.execute.side_effect = failure
                with self.assertRaises(CalendarOperationError) as caught:
                    self.gateway.get_event(EVENT_ID)
                self.assert_safe(caught.exception)

    def test_get_malformed_response_and_unknown_status_cannot_select_event(self):
        for response in (None, [], {}, event(id="other"), event(status="unknown")):
            with self.subTest(response=response):
                self.events.get.return_value.execute.return_value = response
                with self.assertRaises(CalendarOperationError):
                    self.gateway.get_event(EVENT_ID)

    def test_list_malformed_page_token_item_or_status_never_returns_empty_success(self):
        for page in (
            None, [], {"items": None}, {"items": {}}, {"items": [None]},
            {"items": [event(status="unknown")]},
            {"items": [event()], "nextPageToken": []},
            {"items": [event()], "nextPageToken": "   "},
        ):
            with self.subTest(page=page):
                self.events.list.return_value.execute.return_value = page
                with self.assertRaises(CalendarOperationError):
                    self.gateway.list_events(self.period)

    def test_list_duplicate_id_with_different_data_is_not_silently_overwritten(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [event()], "nextPageToken": "next"},
            {"items": [event(summary="Entrevista alterada durante consulta")]},
        ]

        with self.assertRaises(CalendarOperationError):
            self.gateway.list_events(self.period)

    def test_list_transport_after_valid_page_does_not_return_partial_success(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [event()], "nextPageToken": "next"}, TransportError(PRIVATE),
        ]

        with self.assertRaises(CalendarOperationError) as caught:
            self.gateway.list_events(self.period)

        self.assert_safe(caught.exception)
        self.assertEqual(self.events.list.return_value.execute.call_count, 2)

    def test_list_first_page_transport_failure_does_not_return_empty_success(self):
        self.events.list.return_value.execute.side_effect = TransportError(PRIVATE)

        with self.assertRaises(CalendarOperationError) as caught:
            self.gateway.list_events(self.period)

        self.assert_safe(caught.exception)

    def test_list_repeated_token_stops_without_returning_partial_results(self):
        self.events.list.return_value.execute.side_effect = [
            {"items": [event()], "nextPageToken": "repeat"},
            {"items": [], "nextPageToken": "repeat"},
        ]

        with self.assertRaises(CalendarOperationError):
            self.gateway.list_events(self.period)

        self.assertEqual(self.events.list.return_value.execute.call_count, 2)

    def test_complete_empty_list_is_a_valid_read(self):
        self.events.list.return_value.execute.return_value = {}

        result = self.gateway.list_events(self.period)

        self.assertEqual(result.calendar_name, "Genius Testes")
        self.assertEqual(result.events, ())
        self.events.list.return_value.execute.assert_called_once_with(num_retries=2)


if __name__ == "__main__":
    unittest.main()
