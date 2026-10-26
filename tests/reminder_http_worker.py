"""Servidor de fixture isolado para testes TCP; nunca usar como servidor real.

Só escuta loopback, exige um diretório temporário filho do checkout e injeta
agenda, destinatário, transporte e relógio fictícios. Não importa TestCase.
"""

import argparse
import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import time
from uuid import UUID


FIXTURE_TOKEN = "SystemHttpFixtureToken_OnlyTests_0123456789AbCd"
FIXTURE_CALENDAR = "system-fixture-calendar@example.invalid"
FIXTURE_BINDING = "a" * 64
FIXTURE_PHONE = "5511999999999"
FIXTURE_CLOCK = datetime(2034, 6, 17, 12, tzinfo=timezone.utc)
FIXTURE_ZONE = "America/Recife"
CHECKPOINTS = {"none", "after_reserve", "after_sending", "after_provider_accept", "after_accepted"}


class FixtureState:
    def __init__(self, root, instance):
        self.root, self.instance = root, instance
        self.path = root / "fixture-state.sqlite3"
        with self.connection() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS values_store(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS receipts(sequence INTEGER PRIMARY KEY,fingerprint TEXT NOT NULL)")
            defaults = {"clock": FIXTURE_CLOCK.isoformat(), "events": self.events_after([
                {"event_id": "fixture-event", "minutes_until_start": 15, "status": "confirmed"}], FIXTURE_CLOCK),
                "checkpoint": {"mode": "none", "reached": None},
                "calendar_snapshots": 0, "calendar_lookups": 0, "external_attempts": 0, "generation": 0}
            for key, value in defaults.items():
                connection.execute("INSERT OR IGNORE INTO values_store VALUES(?,?)", (key, json.dumps(value)))
            generation = self.read("generation", connection) + 1
            self.write("generation", generation, connection)
            # Um processo novo nunca herda a espera de teste do processo morto.
            self.write("checkpoint", {"mode": "none", "reached": None}, connection)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=2)) as connection:
            connection.execute("PRAGMA busy_timeout=2000")
            with connection:
                yield connection

    @staticmethod
    def read(key, connection):
        return json.loads(connection.execute("SELECT value FROM values_store WHERE name=?", (key,)).fetchone()[0])

    @staticmethod
    def write(key, value, connection):
        connection.execute("UPDATE values_store SET value=? WHERE name=?", (json.dumps(value), key))

    def get(self, key):
        with self.connection() as connection:
            return self.read(key, connection)

    def increment(self, key):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.write(key, self.read(key, connection) + 1, connection)

    def now(self):
        return datetime.fromisoformat(self.get("clock"))

    @staticmethod
    def events_after(events, now):
        result = []
        seen = set()
        if type(events) is not list or len(events) > 5:
            raise ValueError()
        for event in events:
            if (type(event) is not dict or set(event) != {"event_id", "minutes_until_start", "status"}
                    or not isinstance(event["event_id"], str)
                    or re.fullmatch(r"fixture-[a-z0-9-]{1,48}", event["event_id"]) is None
                    or event["event_id"] in seen or event["status"] not in {"confirmed", "cancelled"}
                    or type(event["minutes_until_start"]) is not int or not 1 <= event["minutes_until_start"] <= 10080):
                raise ValueError()
            seen.add(event["event_id"])
            start = now + timedelta(minutes=event["minutes_until_start"])
            result.append({"event_id": event["event_id"], "status": event["status"],
                           "start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()})
        return result

    def control(self, body):
        if (type(body) is not dict or not body or set(body) - {"advance_seconds", "events", "checkpoint"}):
            raise ValueError()
        advance = body.get("advance_seconds", 0)
        if type(advance) is not int or not 0 <= advance <= 86400:
            raise ValueError()
        if "checkpoint" in body and body["checkpoint"] not in CHECKPOINTS:
            raise ValueError()
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.fromisoformat(self.read("clock", connection)) + timedelta(seconds=advance)
            events = self.events_after(body["events"], now) if "events" in body else None
            self.write("clock", now.isoformat(), connection)
            if events is not None:
                self.write("events", events, connection)
            if "checkpoint" in body:
                self.write("checkpoint", {"mode": body["checkpoint"], "reached": None}, connection)

    def checkpoint(self, name):
        with self.connection() as connection:
            value = self.read("checkpoint", connection)
            if value["mode"] != name:
                return
            self.write("checkpoint", {"mode": name, "reached": name}, connection)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.get("checkpoint")["mode"] != name:
                return
            time.sleep(0.025)
        raise RuntimeError("Checkpoint de fixture não liberado dentro do limite.")

    def accept(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            sequence = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] + 1
            fingerprint = sha256(f"fixture-provider-accept-{sequence}".encode()).hexdigest()
            connection.execute("INSERT INTO receipts VALUES(?,?)", (sequence, fingerprint))
            return fingerprint

    def summary(self, store):
        records = store.list_reminders()
        with self.connection() as connection:
            accepts = connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
            return {"instance": self.instance, "pid": os.getpid(), "generation": self.read("generation", connection),
                    "checkpoint": self.read("checkpoint", connection), "sender_accepts_count": accepts,
                    "calendar_snapshots": self.read("calendar_snapshots", connection),
                    "calendar_lookups": self.read("calendar_lookups", connection),
                    "external_attempts": self.read("external_attempts", connection),
                    "records": [{"event_id": record.event_id, "state": record.state,
                                 "start": record.start_at.isoformat(), "reason": record.reason}
                                for record in records]}


def restrict_network(state):
    """Falhar antes de DNS/conexão externos; o servidor usa só TCP loopback."""
    original_resolve = socket.getaddrinfo
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    allowed = {None, "127.0.0.1", "::1", "localhost", b"127.0.0.1", b"::1", b"localhost"}

    def checked_host(host):
        if host not in allowed:
            state.increment("external_attempts")
            raise RuntimeError("Rede externa proibida no worker de fixture.")

    def resolve(host, *args, **kwargs):
        checked_host(host)
        return original_resolve(host, *args, **kwargs)

    def connect(sock, address):
        checked_host(address[0] if isinstance(address, tuple) else address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        checked_host(address[0] if isinstance(address, tuple) else address)
        return original_connect_ex(sock, address)

    socket.getaddrinfo = resolve
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex


def build_app(root, instance):
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from app.api_security import require_api_key
    from app.main import create_app
    from app.reminder_store import ReminderStore
    from app.services.reminder_delivery import ReminderDeliveryService, ReminderRecipient
    from app.services.reminder_recovery import ReminderRecoveryService
    from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
    from app.services.reminder_tick import ReminderTickService
    from app.services.reminders import ReminderCalendarEvent, ReminderCalendarSnapshot
    from app.settings import Settings
    import app.api_reminder_delivery as delivery_api
    import app.api_reminder_recovery as recovery_api
    import app.api_reminders as tick_api

    state = FixtureState(root, instance)
    restrict_network(state)

    class FixtureStore(ReminderStore):
        def reserve(self, *args, **kwargs):
            claim = super().reserve(*args, **kwargs)
            if claim.status == "new":
                state.checkpoint("after_reserve")
            return claim

        def begin_delivery(self, *args, **kwargs):
            record = super().begin_delivery(*args, **kwargs)
            if record.state == "sending":
                state.checkpoint("after_sending")
            return record

        def mark_accepted(self, *args, **kwargs):
            result = super().mark_accepted(*args, **kwargs)
            state.checkpoint("after_accepted")
            return result

    store = FixtureStore(root / "reminders.sqlite3", clock=state.now)
    if store.config() is None:
        store.configure(FIXTURE_CALENDAR, FIXTURE_BINDING,
            offsets={"day_before": None, "preparation": None, "imminent": 15},
            enabled=True, permission_granted=True)
        inbound = state.now() - timedelta(minutes=5)
        store.observe_inbound(sha256(b"fixture-inbound").hexdigest(), FIXTURE_BINDING,
            str(int(inbound.timestamp())), state.now())

    def event(value):
        return ReminderCalendarEvent(value["event_id"], "Fixture appointment " + value["event_id"],
            datetime.fromisoformat(value["start"]), datetime.fromisoformat(value["end"]), False, value["status"])

    class FixtureCalendar:
        def snapshot(self, start, end, *, expected_calendar_id):
            if expected_calendar_id != FIXTURE_CALENDAR:
                raise ValueError("Escopo de agenda divergente na fixture.")
            state.increment("calendar_snapshots")
            events = tuple(event(value) for value in state.get("events")
                if value["status"] != "cancelled" and datetime.fromisoformat(value["end"]) > start
                and datetime.fromisoformat(value["start"]) < end)
            return ReminderCalendarSnapshot(FIXTURE_CALENDAR, FIXTURE_ZONE, events)

        def lookup_snapshot(self, event_ids, *, expected_calendar_id):
            if expected_calendar_id != FIXTURE_CALENDAR:
                raise ValueError("Escopo de agenda divergente na fixture.")
            state.increment("calendar_lookups")
            events = {value["event_id"]: value for value in state.get("events")}
            lookups = []
            for event_id in event_ids:
                value = events.get(event_id)
                lookups.append(ReminderEventLookup(event_id, "absent") if value is None else
                    ReminderEventLookup(event_id, "cancelled") if value["status"] == "cancelled" else
                    ReminderEventLookup(event_id, "found", event(value)))
            return ReminderLookupSnapshot(FIXTURE_CALENDAR, FIXTURE_ZONE, tuple(lookups))

    @contextmanager
    def calendar_factory():
        yield FixtureCalendar()

    class FixtureSender:
        def send(self, recipient, text):
            if (recipient != FIXTURE_PHONE or not isinstance(text, str)
                    or not text.startswith("Sua entrevista começa em ") or "\nFixture appointment " not in text):
                raise ValueError("Payload divergente na fixture.")
            fingerprint = state.accept()
            state.checkpoint("after_provider_accept")
            return fingerprint

    class FixtureDatabase:
        def initialize(self):
            return None

    def forbidden(*args, **kwargs):
        raise RuntimeError("Dependência externa proibida no worker de fixture.")

    # Injeção de relógio restrita a este processo de teste. As rotas e os três
    # serviços reais continuam sendo exercitados pelo HTTP/ASGI do uvicorn.
    tick_api.ReminderTickService = lambda selected, gateway, binding: ReminderTickService(
        selected, gateway, binding, clock=state.now)
    delivery_api.ReminderDeliveryService = lambda selected, gateway, recipient, sender: ReminderDeliveryService(
        selected, gateway, recipient, sender, clock=state.now)
    recovery_api.ReminderRecoveryService = lambda selected: ReminderRecoveryService(selected, clock=state.now)
    config = Settings(_env_file=None, app_env="test", api_auth_token=FIXTURE_TOKEN,
        d360_api_key=None, openai_api_key=None, gemini_api_key=None, whatsapp_agent_enabled=False,
        database_path=root / "unused-agenda.sqlite3", reminder_database_path=root / "reminders.sqlite3",
        google_client_file=root / "absent-google-client.json", google_token_file=root / "absent-google-token.json")
    app = create_app(config=config, db=FixtureDatabase(),
        calendar_gateway_factory=forbidden, sandbox_reply_service_factory=forbidden,
        agent_preview_service_factory=forbidden, agent_query_service_factory=forbidden,
        conversation_store_factory=forbidden, agent_dialogue_service_factory=forbidden,
        confirmation_service_factory=forbidden, agent_creation_store_factory=forbidden,
        agent_creation_service_factory=forbidden, agent_update_store_factory=forbidden,
        agent_update_service_factory=forbidden, agent_deletion_store_factory=forbidden,
        agent_deletion_service_factory=forbidden, whatsapp_agent_store_factory=forbidden,
        whatsapp_agent_worker_factory=forbidden, whatsapp_agent_service_factory=forbidden,
        reminder_store_factory=lambda: store, reminder_gateway_factory=calendar_factory,
        reminder_recipient_binding_loader=lambda: FIXTURE_BINDING,
        reminder_delivery_recipient_loader=lambda: ReminderRecipient(FIXTURE_PHONE, FIXTURE_BINDING),
        reminder_delivery_sender_factory=FixtureSender)

    @app.get("/__fixture/state", dependencies=[Depends(require_api_key)])
    async def fixture_summary():
        return state.summary(store)

    @app.post("/__fixture/control", dependencies=[Depends(require_api_key)])
    async def fixture_control(request: Request):
        try:
            if request.scope.get("query_string"):
                raise ValueError()
            if request.headers.get("content-type") != "application/json":
                raise ValueError()
            raw = bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 4096:
                        raise ValueError()
                    raw.extend(chunk)
            state.control(json.loads(raw))
        except Exception:
            return JSONResponse(status_code=422, content={"status": "invalid_fixture_control"})
        return {"status": "fixture_control_applied"}

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--instance", required=True)
    args = parser.parse_args(argv)
    project = Path(__file__).resolve().parents[1]
    root = args.root.resolve()
    identifier = UUID(args.instance)
    if (root.parent != project or not root.name.startswith("system-http-") or not root.is_dir()
            or not 1024 <= args.port <= 65535 or identifier.version != 4 or str(identifier) != args.instance):
        parser.error("O worker aceita somente fixtures temporárias locais e identidade de teste.")
    sys.path.insert(0, str(project))
    import uvicorn
    uvicorn.run(build_app(root, args.instance), host="127.0.0.1", port=args.port,
                log_level="error", access_log=False, timeout_graceful_shutdown=1)


if __name__ == "__main__":
    main()
