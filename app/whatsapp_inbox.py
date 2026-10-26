"""Caixa de entrada local independente; nunca armazena conteúdo ou remetentes.

Uma operação pendente/incerta bloqueia reenvios até reconciliação explícita.
Isso evita repetir automaticamente um efeito cujo resultado não foi confirmado;
não representa uma garantia de execução exatamente uma vez no serviço externo.
"""

from collections.abc import Iterable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
from uuid import UUID, uuid4


DEFAULT_INBOX_PATH = Path("data/whatsapp-inbox.sqlite3")
SCHEMA_VERSION = "1"
MAX_BATCH_EVENTS = 200
BUSY_TIMEOUT_MS = 1000
KEY_NAMESPACE = "genius-agenda:360dialog:whatsapp-inbox:v1"
_KEY = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TYPE = re.compile(r"[a-z][a-z0-9_]{0,39}\Z", re.ASCII)
_EVENT_ERROR = "Evento de recebimento inválido."
_STORE_ERROR = "Estado local de recebimento indisponível ou inconsistente."
_STATES = frozenset({"processing", "processed", "uncertain"})
_REPLY_CLAIM_TYPES = frozenset({
    "message_" + value for value in (
        "text", "image", "audio", "video", "document", "sticker", "location",
        "contacts", "interactive", "button", "reaction", "order", "system",
        "unknown", "unsupported",
    )
}) | frozenset({"status_" + value for value in ("sent", "delivered", "read", "failed", "played")})
_SIGNATURES = {
    "inbox_metadata": (("name", "TEXT", 1, 1), ("value", "TEXT", 1, 0)),
    "inbox_events": (("event_key", "TEXT", 1, 1), ("event_type", "TEXT", 1, 0),
                     ("request_id", "TEXT", 1, 0), ("state", "TEXT", 1, 0),
                     ("created_at", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0)),
}


class InboxStoreError(RuntimeError):
    """Falha local genérica; não revela SQL, caminhos ou dados recebidos."""


def _valid_type(value) -> bool:
    return isinstance(value, str) and _TYPE.fullmatch(value) is not None


def _valid_request_id(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _valid_timestamp(value) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        return False
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return False
    return timestamp.tzinfo is not None and timestamp.utcoffset() == timezone.utc.utcoffset(timestamp)


def make_event_key(kind: str, provider_id: str, event_type: str) -> str:
    """Hash canônico; mensagem usa ID, status usa ID e fase de entrega."""
    if (kind not in ("message", "status") or not isinstance(kind, str)
            or not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 1024
            or not _valid_type(event_type) or not event_type.startswith(kind + "_")):
        raise ValueError(_EVENT_ERROR)
    components = [KEY_NAMESPACE, kind, provider_id]
    if kind == "status":
        components.append(event_type)
    canonical = json.dumps(components, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InboxEvent:
    event_key: str
    event_type: str

    def __post_init__(self):
        if (not isinstance(self.event_key, str) or _KEY.fullmatch(self.event_key) is None
                or not _valid_type(self.event_type)):
            raise ValueError(_EVENT_ERROR)


@dataclass(frozen=True)
class InboxClaim:
    status: str
    request_id: str | None
    events: tuple[InboxEvent, ...]
    duplicate_count: int


class InboxStore:
    """Transações SQLite curtas; nenhuma conexão fica aberta entre chamadas."""

    def __init__(self, path: Path | str = DEFAULT_INBOX_PATH):
        # Path não resolve, cria diretório ou abre arquivo no construtor.
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
            raise InboxStoreError(_STORE_ERROR) from None

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
            raise InboxStoreError(_STORE_ERROR)
        for table, signature in _SIGNATURES.items():
            actual = tuple((row[1], row[2], row[3], row[5]) for row in
                           connection.execute(f"PRAGMA table_info({table})"))
            if actual != signature:
                raise InboxStoreError(_STORE_ERROR)
        metadata = connection.execute("SELECT name,value FROM inbox_metadata").fetchall()
        if metadata != [("schema_version", SCHEMA_VERSION)]:
            raise InboxStoreError(_STORE_ERROR)

    def initialize(self):
        """Cria somente uma base vazia própria; recusa versões ou bases alheias."""
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            self._initialize_once()
            self._initialized = True

    def _initialize_once(self):
        with self._connection() as connection, self._transaction(connection):
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if not tables:
                connection.execute(
                    "CREATE TABLE inbox_metadata (name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO inbox_metadata(name,value) VALUES('schema_version',?)", (SCHEMA_VERSION,)
                )
                connection.execute(
                    "CREATE TABLE inbox_events (event_key TEXT NOT NULL PRIMARY KEY,"
                    "event_type TEXT NOT NULL,request_id TEXT NOT NULL,state TEXT NOT NULL "
                    "CHECK(state IN ('processing','processed','uncertain')),"
                    "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
                )
                connection.execute("CREATE INDEX inbox_events_request_id ON inbox_events(request_id)")
            self._check_schema(connection)
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise InboxStoreError(_STORE_ERROR)
            for key, event_type, request_id, state, created_at, updated_at in connection.execute(
                "SELECT event_key,event_type,request_id,state,created_at,updated_at FROM inbox_events"
            ):
                try:
                    InboxEvent(key, event_type)
                except ValueError:
                    raise InboxStoreError(_STORE_ERROR) from None
                if (state not in _STATES or not _valid_request_id(request_id)
                        or not _valid_timestamp(created_at) or not _valid_timestamp(updated_at)):
                    raise InboxStoreError(_STORE_ERROR)

    @staticmethod
    def _events(events: Iterable[InboxEvent]) -> tuple[InboxEvent, ...]:
        try:
            collected = tuple(islice(iter(events), MAX_BATCH_EVENTS + 1))
        except (TypeError, ValueError):
            raise ValueError(_EVENT_ERROR) from None
        if not collected or len(collected) > MAX_BATCH_EVENTS:
            raise ValueError(_EVENT_ERROR)
        for event in collected:
            if not isinstance(event, InboxEvent):
                raise ValueError(_EVENT_ERROR)
            # Não confiar apenas no construtor de um objeto fornecido pelo chamador.
            InboxEvent(event.event_key, event.event_type)
        return collected

    def claim(self, events: Iterable[InboxEvent]) -> InboxClaim:
        """Reserva eventos inéditos; pendências bloqueiam todo o lote sem inserir."""
        supplied = self._events(events)
        unique = dict()
        for event in supplied:
            unique.setdefault(event.event_key, event)
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            placeholders = ",".join("?" for _key in unique)
            rows = dict(connection.execute(
                f"SELECT event_key,state FROM inbox_events WHERE event_key IN ({placeholders})",
                tuple(unique),
            ))
            fresh = tuple(event for key, event in unique.items() if key not in rows)
            duplicates = len(supplied) - len(fresh)
            if any(state in ("processing", "uncertain") for state in rows.values()):
                return InboxClaim("blocked", None, (), duplicates)
            if any(state != "processed" for state in rows.values()):
                raise InboxStoreError(_STORE_ERROR)
            if not fresh:
                return InboxClaim("duplicate", None, (), duplicates)
            request_id = str(uuid4())
            recorded_at = datetime.now(timezone.utc).isoformat()
            connection.executemany(
                "INSERT INTO inbox_events(event_key,event_type,request_id,state,created_at,updated_at) "
                "VALUES(?,?,?,'processing',?,?)",
                ((event.event_key, event.event_type, request_id, recorded_at, recorded_at) for event in fresh),
            )
            return InboxClaim("new", request_id, fresh, duplicates)

    def _transition(self, claim: InboxClaim, new_state: str):
        if (not isinstance(claim, InboxClaim) or claim.status != "new"
                or not _valid_request_id(claim.request_id)
                or not isinstance(claim.events, tuple)
                or not isinstance(claim.duplicate_count, int) or isinstance(claim.duplicate_count, bool)
                or claim.duplicate_count < 0):
            raise ValueError(_EVENT_ERROR)
        events = self._events(claim.events)
        expected = {event.event_key: event.event_type for event in events}
        if len(expected) != len(events):
            raise ValueError(_EVENT_ERROR)
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            owned = connection.execute(
                "SELECT event_key,event_type,state FROM inbox_events WHERE request_id=?", (claim.request_id,)
            ).fetchall()
            if (len(owned) != len(expected)
                    or any(expected.get(key) != event_type or state != "processing"
                           for key, event_type, state in owned)):
                raise InboxStoreError(_STORE_ERROR)
            placeholders = ",".join("?" for _event in events)
            changed = connection.execute(
                "UPDATE inbox_events SET state=?,updated_at=? WHERE request_id=? AND state='processing' "
                f"AND event_key IN ({placeholders})",
                (new_state, datetime.now(timezone.utc).isoformat(), claim.request_id, *expected),
            )
            if changed.rowcount != len(expected):
                raise InboxStoreError(_STORE_ERROR)

    def lookup_processing_claim(self, request_id: str) -> InboxClaim | None:
        """Ler um claim ativo inteiro sem reservar, finalizar ou reabrir eventos.

        A inicialização mantém a verificação normal da base própria. Depois,
        somente leitura em um snapshot SQLite: nenhum lock ou conexão é
        mantido após o retorno para a futura chamada externa. Status-only é
        um claim válido; o serviço de resposta decide não enviar nesse caso.
        """
        if not _valid_request_id(request_id):
            return None
        self.initialize()
        with self._connection() as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                self._check_schema(connection)
                rows = connection.execute(
                    "SELECT event_key,event_type,request_id,state,created_at,updated_at "
                    "FROM inbox_events WHERE request_id=? ORDER BY event_key LIMIT ?",
                    (request_id, MAX_BATCH_EVENTS + 1),
                ).fetchall()
                if not rows:
                    return None
                if len(rows) > MAX_BATCH_EVENTS:
                    raise InboxStoreError(_STORE_ERROR)
                events = []
                active = True
                for key, event_type, owner, state, created_at, updated_at in rows:
                    try:
                        event = InboxEvent(key, event_type)
                    except ValueError:
                        raise InboxStoreError(_STORE_ERROR) from None
                    if (event_type not in _REPLY_CLAIM_TYPES or owner != request_id
                            or state not in _STATES or not _valid_timestamp(created_at)
                            or not _valid_timestamp(updated_at)):
                        raise InboxStoreError(_STORE_ERROR)
                    active = active and state == "processing"
                    events.append(event)
                if not active:
                    return None
                return InboxClaim("new", request_id, tuple(events), 0)
            finally:
                connection.rollback()

    def complete(self, claim: InboxClaim):
        """Confirma atomicamente todo o claim; inconsistência não é sucesso parcial."""
        self._transition(claim, "processed")

    def mark_uncertain(self, claim: InboxClaim):
        """Impede repetição após resultado externo incerto; falha mantém processing."""
        self._transition(claim, "uncertain")
