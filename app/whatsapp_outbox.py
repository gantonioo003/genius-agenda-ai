"""Reserva persistente de uma única resposta por claim; sem expiração ou retry."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from threading import Lock
from uuid import UUID


DEFAULT_OUTBOX_PATH = Path("data/whatsapp-outbox.sqlite3")
BUSY_TIMEOUT_MS = 1000
SCHEMA_VERSION = "1"
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ERROR = "Estado local do envio indisponível ou inconsistente."
_INPUT_ERROR = "Reserva de envio inválida."
_SIGNATURES = {
    "outbox_metadata": (("name", "TEXT", 1, 1), ("value", "TEXT", 1, 0)),
    "outbox_messages": (("request_id", "TEXT", 1, 1), ("state", "TEXT", 1, 0),
                        ("message_id_fingerprint", "TEXT", 0, 0),
                        ("created_at", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0)),
}


class OutboxStoreError(RuntimeError):
    """Falha genérica sem caminho, SQL, telefone ou dados de mensagens."""


@dataclass(frozen=True)
class OutboxReservation:
    status: str
    request_id: str
    message_id_fingerprint: str | None = None


def _valid_request_id(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _valid_fingerprint(value) -> bool:
    return isinstance(value, str) and _FINGERPRINT.fullmatch(value) is not None


def _valid_timestamp(value) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        return False
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)


def _valid_state(state, fingerprint) -> bool:
    return ((state == "accepted" and _valid_fingerprint(fingerprint))
            or (state in ("sending", "uncertain") and fingerprint is None))


class OutboxStore:
    def __init__(self, path: Path | str = DEFAULT_OUTBOX_PATH):
        self.path = Path(path)
        self._initialized = False
        self._initialization_lock = Lock()

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000,
                                         isolation_level=None)) as connection:
                connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                yield connection
        except (sqlite3.Error, OSError):
            raise OutboxStoreError(_ERROR) from None

    @staticmethod
    @contextmanager
    def _transaction(connection):
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    @staticmethod
    def _check_schema(connection):
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        if tables != set(_SIGNATURES):
            raise OutboxStoreError(_ERROR)
        for table, signature in _SIGNATURES.items():
            actual = tuple((row[1], row[2], row[3], row[5]) for row in
                           connection.execute(f"PRAGMA table_info({table})"))
            if actual != signature:
                raise OutboxStoreError(_ERROR)
        if connection.execute("SELECT name,value FROM outbox_metadata").fetchall() != [
            ("schema_version", SCHEMA_VERSION)
        ]:
            raise OutboxStoreError(_ERROR)

    def initialize(self):
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            with self._connection() as connection, self._transaction(connection):
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )}
                if not tables:
                    connection.execute(
                        "CREATE TABLE outbox_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO outbox_metadata(name,value) VALUES('schema_version',?)", (SCHEMA_VERSION,)
                    )
                    connection.execute(
                        "CREATE TABLE outbox_messages(request_id TEXT NOT NULL PRIMARY KEY,"
                        "state TEXT NOT NULL CHECK(state IN ('sending','accepted','uncertain')),"
                        "message_id_fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
                        "CHECK((state='accepted' AND message_id_fingerprint IS NOT NULL) OR "
                        "(state IN ('sending','uncertain') AND message_id_fingerprint IS NULL)))"
                    )
                self._check_schema(connection)
                if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise OutboxStoreError(_ERROR)
                for request_id, state, fingerprint, created_at, updated_at in connection.execute(
                    "SELECT request_id,state,message_id_fingerprint,created_at,updated_at FROM outbox_messages"
                ):
                    if (not _valid_request_id(request_id) or not _valid_state(state, fingerprint)
                            or not _valid_timestamp(created_at) or not _valid_timestamp(updated_at)):
                        raise OutboxStoreError(_ERROR)
            self._initialized = True

    def reserve(self, request_id: str) -> OutboxReservation:
        if not _valid_request_id(request_id):
            raise ValueError(_INPUT_ERROR)
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            existing = connection.execute(
                "SELECT state,message_id_fingerprint FROM outbox_messages WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing is not None:
                state, fingerprint = existing
                if not _valid_state(state, fingerprint):
                    raise OutboxStoreError(_ERROR)
                if state == "accepted":
                    return OutboxReservation("accepted", request_id, fingerprint)
                return OutboxReservation("blocked", request_id)
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO outbox_messages(request_id,state,message_id_fingerprint,created_at,updated_at) "
                "VALUES(?,'sending',NULL,?,?)", (request_id, now, now),
            )
            return OutboxReservation("new", request_id)

    def _transition(self, reservation: OutboxReservation, state: str, fingerprint: str | None):
        if (not isinstance(reservation, OutboxReservation) or reservation.status != "new"
                or not _valid_request_id(reservation.request_id)
                or reservation.message_id_fingerprint is not None
                or not _valid_state(state, fingerprint)):
            raise ValueError(_INPUT_ERROR)
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            changed = connection.execute(
                "UPDATE outbox_messages SET state=?,message_id_fingerprint=?,updated_at=? "
                "WHERE request_id=? AND state='sending' AND message_id_fingerprint IS NULL",
                (state, fingerprint, datetime.now(timezone.utc).isoformat(), reservation.request_id),
            )
            if changed.rowcount != 1:
                raise OutboxStoreError(_ERROR)

    def mark_accepted(self, reservation: OutboxReservation, message_id_fingerprint: str):
        self._transition(reservation, "accepted", message_id_fingerprint)

    def mark_uncertain(self, reservation: OutboxReservation):
        self._transition(reservation, "uncertain", None)
