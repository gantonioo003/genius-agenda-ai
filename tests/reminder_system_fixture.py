"""SDK/transport/gateway reais, HTTP Google e sender somente em memória.

resolve_calendar é uma fronteira fixture explícita: não abre OAuth nem os
metadados privados. Observações do canal pertencem apenas ao SQLite temporário
deste ensaio; não demonstram proveniência de callback real.
"""
from collections import Counter
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, unquote, urlsplit
from unittest.mock import patch

from google.auth.credentials import AnonymousCredentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.google_transport import SingleAttemptHttp
from app.reminder_calendar import GoogleReminderCalendarGateway
from app.reminder_store import ReminderStore
from app.services.reminder_delivery import ReminderDeliveryService, ReminderRecipient
from app.services.reminder_recovery import ReminderRecoveryService
from app.services.reminder_tick import ReminderTickService
from calendar_http_fake import CalendarHttpFake


UTC = timezone.utc
NOW = datetime(2030, 6, 17, 12, tzinfo=UTC)
BINDING = "a" * 64
PHONE = "5511999990000"


class ControlledClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance_to(self, now):
        if now < self.now:
            raise AssertionError("O percurso integrado não pode voltar o relógio.")
        self.now = now


class ReminderCalendarHttpFake(CalendarHttpFake):
    """Extensão somente de leitura: calendarList.get, falhas e corrida de GET."""
    def __init__(self):
        super().__init__()
        self.calendar_access_status = 200
        self.fault = None
        self.before_event_get = None
        self.after_event_get = None
        self.event_get_counts = Counter()

    def request(self, method, url, data=None, headers=None, **kwargs):
        if method.upper() != "GET":
            raise AssertionError("A suíte de lembretes não pode escrever no Google.")
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        event_prefix = f"/calendar/v3/calendars/{self.calendar['id']}/events/"
        event_id = path[len(event_prefix):] if path.startswith(event_prefix) else None
        if event_id:
            self.event_get_counts[event_id] += 1
            if self.before_event_get:
                self.before_event_get(event_id, self.event_get_counts[event_id])
        override = self.fault(method.upper(), path, query) if self.fault else None
        calendar_get = path == f"/calendar/v3/users/me/calendarList/{self.calendar['id']}"
        if override is not None or calendar_get:
            call = {"method": "GET", "path": path, "query": query, "headers": dict(headers or {}),
                    "body": None, "status": None, "event_id": event_id}
            self.calls.append(call)
            if override is None:
                status = self.calendar_access_status
                body = self.calendar if status == 200 else self._error(status)
            else:
                status, body = override
            response = self._reply(call, status, body)
        else:
            response = super().request(method, url, data=data, headers=headers, **kwargs)
        if event_id and self.after_event_get:
            # A resposta já copiou o evento. A alteração ocorre após o GET,
            # antes dos gates locais finais; não simula atomicidade com Google.
            self.after_event_get(event_id, self.event_get_counts[event_id])
        return response


class ReminderSystemFixture:
    def __init__(self, case, *, now=NOW, offsets=None, zone="America/Recife"):
        self.case = case
        self.stack = ExitStack()
        temp = self.stack.enter_context(TemporaryDirectory(dir=Path(__file__).resolve().parents[1],
                                                         prefix=".reminder-system-"))
        self.path = Path(temp) / "reminders.sqlite3"
        self.clock = ControlledClock(now)
        self.backend = ReminderCalendarHttpFake()
        self.backend.calendar["timeZone"] = zone
        self.attempts = []
        self._window_number = 0
        self.stack.enter_context(patch("app.google_transport.requests.Session", side_effect=self.backend.session))
        self.stack.enter_context(patch("app.reminder_calendar.resolve_calendar",
            side_effect=lambda _service: deepcopy(self.backend.calendar)))
        self.stack.enter_context(patch("app.google_calendar.get_credentials",
            side_effect=AssertionError("OAuth/metadados reais proibidos neste percurso.")))
        # Repetições explícitas de leitura continuam reais; só a espera é removida.
        self.stack.enter_context(patch("googleapiclient.http.time.sleep", return_value=None))
        self.restart()
        self.store.configure(self.backend.calendar["id"], BINDING, offsets=offsets,
                             enabled=True, permission_granted=True)
        self.window()

    def close(self):
        try:
            self.case.assertTrue(all(call["method"] == "GET" for call in self.backend.calls))
        finally:
            self.stack.close()

    def restart(self):
        # Novos objetos; o ledger e as observações persistidas permanecem.
        self.store = ReminderStore(self.path, clock=self.clock)

    def window(self):
        self._window_number += 1
        key = sha256(f"isolated-window-{self._window_number}".encode()).hexdigest()
        self.store.observe_inbound(key, BINDING, str(int(self.clock().timestamp())), self.clock(),
                                  provenance="sandbox_callback_capacity")

    def event(self, event_id="system-event", *, start=None, title="Entrevista de teste", **changes):
        start = start or self.clock() + timedelta(hours=2)
        value = {"id": event_id, "summary": title, "status": "confirmed",
                 "start": {"dateTime": start.isoformat()},
                 "end": {"dateTime": (start + timedelta(minutes=30)).isoformat()}}
        value.update(changes)
        self.backend.saved[event_id] = value
        return value

    def move_event(self, event_id, start):
        event = self.backend.saved[event_id]
        event["start"] = {"dateTime": start.isoformat()}
        event["end"] = {"dateTime": (start + timedelta(minutes=30)).isoformat()}

    @contextmanager
    def gateway(self):
        transport = SingleAttemptHttp(timeout=20)
        authorized = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
        service = build("calendar", "v3", http=authorized, cache_discovery=False, static_discovery=True)
        try:
            yield GoogleReminderCalendarGateway(service)
        finally:
            service.close()

    def recipient(self):
        return ReminderRecipient(PHONE, BINDING)

    def send(self, recipient, text):
        records = self.store.list_reminders(states=("sending",), limit=10000)
        self.case.assertEqual(len(records), 1)
        record = records[0]
        self.case.assertLessEqual(record.due_at, self.clock())
        self.case.assertLess(self.clock(), record.deadline_at)
        self.case.assertTrue(self.store.config().enabled)
        self.case.assertTrue(self.store.config().permission_granted)
        self.case.assertEqual(recipient, PHONE)
        self.attempts.append({"key": record.key, "attempt_id": record.attempt_id,
                              "recipient": recipient, "text": text, "at": self.clock()})
        return sha256(f"isolated-provider-accept-{len(self.attempts)}".encode()).hexdigest()

    def recover(self):
        return ReminderRecoveryService(self.store, clock=self.clock).recover()

    def tick(self):
        return ReminderTickService(self.store, self.gateway, lambda: BINDING, clock=self.clock).tick()

    def dispatch(self):
        return ReminderDeliveryService(self.store, self.gateway, self.recipient,
            lambda: self, clock=self.clock).dispatch()

    def cycle(self):
        recovery = self.recover()
        self.case.assertIn(recovery.recovery_status, {"completed", "skipped"})
        tick = self.tick()
        # Replica o gate do workflow, não continua depois de falha/parcial.
        delivery = self.dispatch() if tick.tick_status in {"completed", "skipped"} else None
        return recovery, tick, delivery

    def records(self):
        return self.store.list_reminders(limit=10000)

    def record(self, event_id="system-event", *, slot="preparation", start=None):
        values = [record for record in self.records() if record.event_id == event_id and record.slot == slot
                  and (start is None or record.start_at == start)]
        self.case.assertEqual(len(values), 1)
        return values[0]

    def history(self):
        return {record.key: self.store.history(record.key) for record in self.records()}
