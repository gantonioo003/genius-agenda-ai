"""Histórico separado de ensaios explícitos; não recebe conteúdo ou destinatário."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from threading import Lock
from uuid import UUID


SCHEMA_VERSION = "1"
BUSY_TIMEOUT_MS = 1000
_CASE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ERROR = "Estado local do ensaio indisponível ou inconsistente."
_SIGNATURES = {
    "probe_metadata": (("name", "TEXT", 1, 1), ("value", "TEXT", 1, 0)),
    "probe_operations": (
        ("operation_id", "TEXT", 1, 1), ("case_name", "TEXT", 1, 0),
        ("payload_fingerprint", "TEXT", 1, 0), ("state", "TEXT", 1, 0),
        ("message_id_fingerprint", "TEXT", 0, 0),
        ("created_at", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0),
    ),
}


class ProbeLedgerError(RuntimeError):
    """Erro seguro; não incorpora SQL, caminhos ou valores do chamador."""


def _valid_operation_id(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _valid_case(value):
    return isinstance(value, str) and _CASE.fullmatch(value) is not None


def _valid_fingerprint(value):
    return isinstance(value, str) and _FINGERPRINT.fullmatch(value) is not None


def _valid_timestamp(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _valid_binding(operation_id, case_name, payload_fingerprint):
    return (_valid_operation_id(operation_id) and _valid_case(case_name)
            and _valid_fingerprint(payload_fingerprint))


def _valid_state(state, message_id_fingerprint):
    return ((state == "accepted" and _valid_fingerprint(message_id_fingerprint))
            or (state in ("sending", "uncertain") and message_id_fingerprint is None))


@dataclass(frozen=True)
class ProbeReservation:
    status: str
    operation_id: str
    case_name: str
    payload_fingerprint: str
    message_id_fingerprint: str | None = None


class ProbeLedger:
    """Reserva durável; nenhuma conexão ou trava permanece durante o envio."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._initialized = False
        self._initialization_lock = Lock()

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(
                self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
            )) as connection:
                yield connection
        except (sqlite3.Error, OSError):
            raise ProbeLedgerError(_ERROR) from None

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
            raise ProbeLedgerError(_ERROR)
        for name, signature in _SIGNATURES.items():
            actual = tuple((row[1], row[2], row[3], row[5]) for row in
                           connection.execute(f"PRAGMA table_info({name})"))
            if actual != signature:
                raise ProbeLedgerError(_ERROR)
        if connection.execute("SELECT name,value FROM probe_metadata").fetchall() != [
            ("schema_version", SCHEMA_VERSION)
        ]:
            raise ProbeLedgerError(_ERROR)

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
                        "CREATE TABLE probe_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO probe_metadata(name,value) VALUES('schema_version',?)", (SCHEMA_VERSION,)
                    )
                    connection.execute(
                        "CREATE TABLE probe_operations("
                        "operation_id TEXT NOT NULL PRIMARY KEY,case_name TEXT NOT NULL,"
                        "payload_fingerprint TEXT NOT NULL,"
                        "state TEXT NOT NULL CHECK(state IN ('sending','accepted','uncertain')),"
                        "message_id_fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
                        "CHECK((state='accepted' AND message_id_fingerprint IS NOT NULL) OR "
                        "(state IN ('sending','uncertain') AND message_id_fingerprint IS NULL)))"
                    )
                self._check_schema(connection)
                if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ProbeLedgerError(_ERROR)
                for operation_id, case_name, payload, state, message, created, updated in connection.execute(
                    "SELECT operation_id,case_name,payload_fingerprint,state,message_id_fingerprint,"
                    "created_at,updated_at FROM probe_operations"
                ):
                    if (not _valid_binding(operation_id, case_name, payload)
                            or not _valid_state(state, message)
                            or not _valid_timestamp(created) or not _valid_timestamp(updated)):
                        raise ProbeLedgerError(_ERROR)
            self._initialized = True

    def reserve(self, operation_id: str, case_name: str, payload_fingerprint: str) -> ProbeReservation:
        """Mesmo UUID exige o mesmo caso e definição; divergência nunca é reuso."""
        if not _valid_binding(operation_id, case_name, payload_fingerprint):
            raise ProbeLedgerError(_ERROR)
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            row = connection.execute(
                "SELECT case_name,payload_fingerprint,state,message_id_fingerprint "
                "FROM probe_operations WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if row is not None:
                saved_case, saved_payload, state, message = row
                if (saved_case != case_name or saved_payload != payload_fingerprint
                        or not _valid_state(state, message)):
                    raise ProbeLedgerError(_ERROR)
                status = "accepted" if state == "accepted" else "blocked"
                return ProbeReservation(status, operation_id, case_name, payload_fingerprint, message)
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO probe_operations(operation_id,case_name,payload_fingerprint,state,"
                "message_id_fingerprint,created_at,updated_at) VALUES(?,?,?,'sending',NULL,?,?)",
                (operation_id, case_name, payload_fingerprint, now, now),
            )
            return ProbeReservation("new", operation_id, case_name, payload_fingerprint)

    def _transition(self, reservation: ProbeReservation, state: str, fingerprint: str | None):
        if (not isinstance(reservation, ProbeReservation) or reservation.status != "new"
                or not _valid_binding(reservation.operation_id, reservation.case_name, reservation.payload_fingerprint)
                or reservation.message_id_fingerprint is not None or not _valid_state(state, fingerprint)):
            raise ProbeLedgerError(_ERROR)
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            changed = connection.execute(
                "UPDATE probe_operations SET state=?,message_id_fingerprint=?,updated_at=? "
                "WHERE operation_id=? AND case_name=? AND payload_fingerprint=? "
                "AND state='sending' AND message_id_fingerprint IS NULL",
                (state, fingerprint, datetime.now(timezone.utc).isoformat(), reservation.operation_id,
                 reservation.case_name, reservation.payload_fingerprint),
            )
            if changed.rowcount != 1:
                raise ProbeLedgerError(_ERROR)

    def mark_accepted(self, reservation: ProbeReservation, message_id_fingerprint: str):
        self._transition(reservation, "accepted", message_id_fingerprint)

    def mark_uncertain(self, reservation: ProbeReservation):
        self._transition(reservation, "uncertain", None)
