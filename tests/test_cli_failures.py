"""Falhas na abertura e no fechamento do cliente, sem Google ou credenciais reais."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError
import httplib2
from oauthlib.oauth2 import OAuth2Error

from app import (
    check_conflicts, create_test_interview, delete_event, edit_event,
    google_calendar, list_events, prepare_request, reschedule_event, select_event,
)
from app.services.agenda import AgendaEvent, CalendarListing
from app.services.event_selection import EventLookup


PRIVATE = "token=PRIVADO_NAO_EXIBIR user@example.invalid /secrets/oauth.json"
EVENT_ID = "AbC123_20261023T190000Z"
START = "2026-10-23T16:00:00-03:00"
END = "2026-10-23T16:30:00-03:00"


@dataclass(frozen=True)
class CommandCase:
    module: object
    arguments: tuple[str, ...]
    writing: bool = False


def failures():
    return (
        ("transport", TransportError(PRIVATE)),
        ("refresh", RefreshError(PRIVATE)),
        ("oauth", OAuth2Error(description=PRIVATE)),
        ("os", OSError(PRIVATE)),
        ("httplib2", httplib2.HttpLib2Error(PRIVATE)),
        ("sqlite", sqlite3.OperationalError(PRIVATE)),
        ("http", HttpError(
            httplib2.Response({"status": "403", "reason": PRIVATE}),
            json.dumps({"error": {"message": PRIVATE}}).encode("utf-8"),
            uri="https://example.invalid/" + PRIVATE,
        )),
    )


@contextmanager
def client_that_fails(error, *, after_yield=False):
    if not after_yield:
        raise error
    yield object()
    raise error


class CliFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.request_file = Path(self.temporary.name) / "request.json"
        self.request_file.write_text(
            json.dumps({"action": "delete", "event_id": EVENT_ID}), encoding="utf-8",
        )
        self.cases = (
            CommandCase(create_test_interview, ("--execute",), True),
            CommandCase(list_events, ("--date", "2026-10-23")),
            CommandCase(select_event, ("--event-id", EVENT_ID)),
            CommandCase(reschedule_event, (
                "--event-id", EVENT_ID, "--start", START, "--end", END, "--execute",
            ), True),
            CommandCase(edit_event, ("--event-id", EVENT_ID, "--title", "Entrevista fictícia", "--execute"), True),
            CommandCase(delete_event, ("--event-id", EVENT_ID, "--execute", "--confirm", "a" * 64), True),
            CommandCase(prepare_request, ("--request-file", str(self.request_file))),
            CommandCase(check_conflicts, ("--start", START, "--end", END)),
        )
        # Se uma regressão escapar dos mocks da CLI, o teste falha antes de usar
        # arquivos de autorização, banco local ou transporte de verdade.
        for name in ("get_credentials", "build"):
            guard = patch.object(google_calendar, name, side_effect=AssertionError("I/O real proibido."))
            guard.start()
            self.addCleanup(guard.stop)

    def invoke(self, case, failure, *, after_yield=False):
        output, errors = io.StringIO(), io.StringIO()
        event = AgendaEvent(EVENT_ID, "Entrevista fictícia", START, END, False,
                            "America/Recife", "confirmed")
        gateway = Mock(name="gateway_without_network")
        gateway.get_event.return_value = EventLookup("Genius Testes", event)
        gateway.list_events.return_value = CalendarListing("Genius Testes", (event,))
        context = client_that_fails(failure, after_yield=after_yield)
        with patch.object(case.module, "calendar_client", return_value=context) as factory:
            with patch.object(case.module, "GoogleCalendarGateway", return_value=gateway) as gateway_factory:
                with redirect_stdout(output), redirect_stderr(errors):
                    status = case.module.main(list(case.arguments))
        factory.assert_called_once_with()
        if after_yield:
            gateway_factory.assert_called_once()
        else:
            gateway_factory.assert_not_called()
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "", "Falha não pode publicar JSON de sucesso.")
        message = errors.getvalue()
        self.assertTrue(message.strip())
        self.assertNotIn("Traceback", message)
        for private_fragment in (PRIVATE, "PRIVADO_NAO_EXIBIR", "user@example.invalid", "/secrets/oauth.json"):
            self.assertNotIn(private_fragment, message)
        return message

    def opening_failures_for(self, module):
        case = next(item for item in self.cases if item.module is module)
        for kind, failure in failures():
            with self.subTest(command=module.__name__, failure=kind):
                message = self.invoke(case, failure)
                if kind in ("refresh", "oauth"):
                    self.assertIn("Reautorize", message)
                    self.assertIn("--authorize", message)
                if case.writing and kind != "http":
                    self.assertIn("Consulte o evento pelo ID antes de repetir", message)

    def test_creation_client_opening_failures_are_safe(self):
        self.opening_failures_for(create_test_interview)

    def test_list_client_opening_failures_are_safe(self):
        self.opening_failures_for(list_events)

    def test_selection_client_opening_failures_are_safe(self):
        self.opening_failures_for(select_event)

    def test_rescheduling_client_opening_failures_are_safe(self):
        self.opening_failures_for(reschedule_event)

    def test_details_client_opening_failures_are_safe(self):
        self.opening_failures_for(edit_event)

    def test_deletion_client_opening_failures_are_safe(self):
        self.opening_failures_for(delete_event)

    def test_request_preparation_client_opening_failures_are_safe(self):
        self.opening_failures_for(prepare_request)

    def test_conflicts_client_opening_failures_are_safe(self):
        self.opening_failures_for(check_conflicts)

    def test_write_result_is_not_printed_if_context_exit_fails(self):
        for case in self.cases:
            if not case.writing:
                continue
            for kind, failure in failures():
                with self.subTest(command=case.module.__name__, failure=kind):
                    self.invoke(case, failure, after_yield=True)

    def test_read_result_is_not_printed_if_context_exit_fails(self):
        for case in self.cases:
            if case.writing:
                continue
            for kind, failure in failures():
                with self.subTest(command=case.module.__name__, failure=kind):
                    self.invoke(case, failure, after_yield=True)

    def test_creation_preview_still_never_opens_google(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(create_test_interview, "calendar_client") as factory:
            with redirect_stdout(output), redirect_stderr(errors):
                status = create_test_interview.main([])
        factory.assert_not_called()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "preview")
        self.assertEqual(errors.getvalue(), "")

    def test_local_preparation_still_never_opens_google(self):
        for request, expected in (({"action": "create"}, "needs_clarification"), ({
            "action": "create", "title": "Entrevista fictícia", "start": START, "end": END,
        }, "ready")):
            with self.subTest(expected=expected):
                self.request_file.write_text(json.dumps(request), encoding="utf-8")
                output, errors = io.StringIO(), io.StringIO()
                with patch.object(prepare_request, "calendar_client") as factory:
                    with redirect_stdout(output), redirect_stderr(errors):
                        status = prepare_request.main(["--request-file", str(self.request_file)])
                factory.assert_not_called()
                self.assertEqual(status, 0)
                self.assertEqual(json.loads(output.getvalue())["status"], expected)
                self.assertEqual(errors.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
