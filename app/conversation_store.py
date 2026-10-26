"""Memória mínima de consultas, isolada por credencial e com validade curta."""

from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
import unicodedata
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.confirmation_state import (ConfirmationProposal, ConfirmationClaim, ConfirmationError,
    validate_confirmation, encode_confirmation, decode_confirmation, confirmation_timestamp, canonical_parameters)
from app.dialogue_state import CandidateReference, PendingRequest, validate_pending_request


SCHEMA_VERSION = "3"
BUSY_TIMEOUT_MS = 1000
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_NAMESPACE = b"genius-conversation:v1\0"
_ERRORS = {
    "conversation_not_found": (404, "Conversa indisponível ou expirada. Inicie outra conversa."),
    "conversation_conflict": (409, "A conversa mudou. Consulte o estado atual antes de continuar."),
    "conversation_unavailable": (503, "O contexto da conversa está temporariamente indisponível."),
    "conversation_capacity": (503, "O limite temporário de conversas foi atingido."),
    "invalid_conversation": (422, "Os dados do contexto da conversa são inválidos."),
}
_SIGNATURES = {
    "conversation_metadata": (("name", "TEXT", 1, 1), ("value", "TEXT", 1, 0)),
    "conversation_contexts": (
        ("binding_hash", "TEXT", 1, 1), ("revision", "INTEGER", 1, 0),
        ("memory_json", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0),
        ("updated_at", "TEXT", 1, 0), ("expires_at", "TEXT", 1, 0),
    ),
}


class ConversationError(RuntimeError):
    """Erro público fixo, sem valores, SQL, caminho ou exceção subjacente."""

    def __init__(self, code="conversation_unavailable"):
        if not isinstance(code, str) or code not in _ERRORS:
            code = "conversation_unavailable"
        self.code = code
        self.status_code, message = _ERRORS[code]
        self.http_status = self.status_code
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class QueryMemory:
    last_list: dict | None = None
    selected_event_id: str | None = None
    selected_time_zone: str | None = None
    pending_request: PendingRequest | None = None
    confirmation: ConfirmationProposal | None = None

    def __repr__(self):
        return "QueryMemory(<private>)"


@dataclass(frozen=True, repr=False)
class ConversationSnapshot:
    revision: int
    memory: QueryMemory

    def __repr__(self):
        return "ConversationSnapshot(<private>)"


def _valid_zone(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        return False
    return True


def _valid_event_id(value):
    return (isinstance(value, str) and 1 <= len(value) <= 1024
            and not any(char.isspace() or unicodedata.category(char).startswith("C") for char in value))


def validate_memory(memory: QueryMemory) -> QueryMemory:
    """Copia a consulta e o rascunho estruturado permitido, sem transcrições."""
    if type(memory) is not QueryMemory:
        raise ConversationError("invalid_conversation")
    last_list = memory.last_list
    if last_list is not None:
        if (type(last_list) is not dict or set(last_list) != {"date", "period", "time_zone"}
                or not isinstance(last_list["date"], str) or not _DATE.fullmatch(last_list["date"])
                or not isinstance(last_list["period"], str) or last_list["period"] not in {"day", "week", "month"}
                or not _valid_zone(last_list["time_zone"])):
            raise ConversationError("invalid_conversation")
        try:
            date.fromisoformat(last_list["date"])
        except ValueError:
            raise ConversationError("invalid_conversation") from None
        last_list = {key: last_list[key] for key in ("date", "period", "time_zone")}
    if (memory.selected_event_id is None) != (memory.selected_time_zone is None):
        raise ConversationError("invalid_conversation")
    if memory.selected_event_id is not None and (
        not _valid_event_id(memory.selected_event_id) or not _valid_zone(memory.selected_time_zone)
    ):
        raise ConversationError("invalid_conversation")
    try:
        pending = None if memory.pending_request is None else validate_pending_request(memory.pending_request)
        confirmation = None if memory.confirmation is None else validate_confirmation(memory.confirmation)
    except (ValueError, ConfirmationError):
        raise ConversationError("invalid_conversation") from None
    return QueryMemory(last_list, memory.selected_event_id, memory.selected_time_zone, pending, confirmation)


def _binding(owner_key, conversation_id):
    if not isinstance(owner_key, str) or not _HASH.fullmatch(owner_key):
        raise ConversationError("invalid_conversation")
    if not isinstance(conversation_id, str):
        raise ConversationError("invalid_conversation")
    try:
        parsed = UUID(conversation_id)
    except (ValueError, AttributeError):
        raise ConversationError("invalid_conversation") from None
    if parsed.version != 4 or str(parsed) != conversation_id:
        raise ConversationError("invalid_conversation")
    return sha256(_NAMESPACE + owner_key.encode("ascii") + b"\0" + conversation_id.encode("ascii")).hexdigest()


def _encode_memory(memory):
    pending = memory.pending_request
    return json.dumps({
        "last_list": memory.last_list, "selected_event_id": memory.selected_event_id,
        "selected_time_zone": memory.selected_time_zone,
        "confirmation": None if memory.confirmation is None else encode_confirmation(memory.confirmation),
        "pending_request": None if pending is None else {
            "fields": pending.fields, "awaiting": list(pending.awaiting),
            "candidates": [{"event_id": candidate.event_id, "fingerprint": candidate.fingerprint}
                           for candidate in pending.candidates],
        },
    }, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _decode_memory(value, *, legacy=False, version=None):
    version = ("1" if legacy else SCHEMA_VERSION) if version is None else version
    if not isinstance(value, str) or len(value) > 1024 * 1024:
        raise ConversationError()
    try:
        decoded = json.loads(value)
        names = {"last_list", "selected_event_id", "selected_time_zone"}
        if version != "1":
            names.add("pending_request")
        if version == SCHEMA_VERSION:
            names.add("confirmation")
        if type(decoded) is not dict or set(decoded) != names:
            raise ConversationError()
        canonical = json.dumps(decoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if canonical != value:
            raise ConversationError()
        if version != "1" and decoded["pending_request"] is not None:
            pending = decoded["pending_request"]
            if (type(pending) is not dict or set(pending) != {"fields", "awaiting", "candidates"}
                    or type(pending["awaiting"]) is not list or type(pending["candidates"]) is not list):
                raise ConversationError()
            candidates = []
            for candidate in pending["candidates"]:
                if type(candidate) is not dict or set(candidate) != {"event_id", "fingerprint"}:
                    raise ConversationError()
                candidates.append(CandidateReference(**candidate))
            decoded["pending_request"] = PendingRequest(
                pending["fields"], tuple(pending["awaiting"]), tuple(candidates),
            )
        if version == SCHEMA_VERSION and decoded["confirmation"] is not None:
            decoded["confirmation"] = decode_confirmation(decoded["confirmation"])
        memory = validate_memory(QueryMemory(**decoded))
        encoded = _encode_memory(memory)
        if version in {"1", "2"}:
            old_encoded = json.loads(encoded)
            del old_encoded["confirmation"]
            if version == "1":
                del old_encoded["pending_request"]
            encoded = json.dumps(old_encoded, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if encoded != value:
            raise ConversationError()
        return memory
    except (TypeError, ValueError, RecursionError, ConversationError, ConfirmationError):
        raise ConversationError() from None


def _parse_timestamp(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ConversationError()
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
            raise ConversationError()
        return parsed
    except (ValueError, OverflowError):
        raise ConversationError() from None


class ConversationStore:
    """CAS persistente; cada chamada fecha sua transação antes de retornar."""

    def __init__(self, path: Path | str, *, ttl_seconds=1800, max_entries=1000, clock=None):
        if (type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 2147483647
                or type(max_entries) is not int or not 1 <= max_entries <= 2147483647
                or clock is not None and not callable(clock)):
            raise ConversationError("invalid_conversation")
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialized = False
        self._initialization_lock = Lock()

    def _now(self):
        try:
            now = self._clock()
            if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
                raise ConversationError()
            return now.astimezone(timezone.utc)
        except Exception:
            raise ConversationError() from None

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(
                self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None,
            )) as connection:
                connection.execute("PRAGMA secure_delete=ON")
                yield connection
        except (sqlite3.Error, OSError):
            raise ConversationError() from None

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
    def _check_schema(connection, *, version=SCHEMA_VERSION):
        objects = connection.execute(
            "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "AND type IN ('table','view','trigger')"
        ).fetchall()
        if set(objects) != {("table", name) for name in _SIGNATURES}:
            raise ConversationError()
        for name, signature in _SIGNATURES.items():
            actual = tuple((row[1], row[2], row[3], row[5]) for row in
                           connection.execute(f"PRAGMA table_info({name})"))
            if actual != signature:
                raise ConversationError()
        if connection.execute("SELECT name,value FROM conversation_metadata").fetchall() != [
            ("schema_version", version),
        ]:
            raise ConversationError()
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ConversationError()
        for binding, revision, memory, created, updated, expires in connection.execute(
            "SELECT binding_hash,revision,memory_json,created_at,updated_at,expires_at "
            "FROM conversation_contexts"
        ):
            if (not isinstance(binding, str) or not _HASH.fullmatch(binding)
                    or type(revision) is not int or not 0 <= revision < 9223372036854775807):
                raise ConversationError()
            _decode_memory(memory, version=version)
            if not _parse_timestamp(created) <= _parse_timestamp(updated) < _parse_timestamp(expires):
                raise ConversationError()

    def initialize(self):
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            with self._connection() as connection, self._transaction(connection):
                objects = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if not objects:
                    connection.execute(
                        "CREATE TABLE conversation_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO conversation_metadata(name,value) VALUES('schema_version',?)", (SCHEMA_VERSION,),
                    )
                    connection.execute(
                        "CREATE TABLE conversation_contexts("
                        "binding_hash TEXT NOT NULL PRIMARY KEY,revision INTEGER NOT NULL CHECK(revision>=0),"
                        "memory_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
                        "expires_at TEXT NOT NULL)"
                    )
                else:
                    metadata_exists = connection.execute(
                        "SELECT type FROM sqlite_master WHERE name='conversation_metadata'"
                    ).fetchall() == [("table",)]
                    metadata = connection.execute("SELECT name,value FROM conversation_metadata").fetchall() if metadata_exists else []
                    if metadata in ([('schema_version', '1')], [('schema_version', '2')]):
                        old_version = metadata[0][1]
                        self._check_schema(connection, version=old_version)
                        rows = connection.execute("SELECT binding_hash,memory_json FROM conversation_contexts").fetchall()
                        for binding, memory in rows:
                            connection.execute(
                                "UPDATE conversation_contexts SET memory_json=? WHERE binding_hash=?",
                                (_encode_memory(_decode_memory(memory, version=old_version)), binding),
                            )
                        connection.execute(
                            "UPDATE conversation_metadata SET value=? WHERE name='schema_version'", (SCHEMA_VERSION,),
                        )
                self._check_schema(connection)
            self._initialized = True

    @staticmethod
    def _purge(connection, now):
        expired = [row[0] for row in connection.execute(
            "SELECT binding_hash,expires_at FROM conversation_contexts"
        ) if _parse_timestamp(row[1]) <= now]
        connection.executemany("DELETE FROM conversation_contexts WHERE binding_hash=?", [(key,) for key in expired])
        return len(expired)

    @contextmanager
    def _active(self):
        self._now()  # Recusar relógio inválido antes de criar qualquer arquivo.
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            now = self._now()  # Não usar um instante anterior à espera pela transação.
            self._purge(connection, now)
            yield connection, now

    def create(self, owner_key: str) -> str:
        conversation_id = str(uuid4())
        binding = _binding(owner_key, conversation_id)
        full = False
        with self._active() as (connection, now):
            if connection.execute("SELECT COUNT(*) FROM conversation_contexts").fetchone()[0] >= self.max_entries:
                full = True
            else:
                try:
                    expires = now + timedelta(seconds=self.ttl_seconds)
                except OverflowError:
                    raise ConversationError() from None
                connection.execute(
                    "INSERT INTO conversation_contexts(binding_hash,revision,memory_json,created_at,updated_at,expires_at) "
                    "VALUES(?,0,?,?,?,?)", (binding, _encode_memory(QueryMemory()), now.isoformat(),
                                          now.isoformat(), expires.isoformat()),
                )
        if full:
            raise ConversationError("conversation_capacity")
        return conversation_id

    def load(self, owner_key: str, conversation_id: str) -> ConversationSnapshot:
        binding = _binding(owner_key, conversation_id)
        with self._active() as (connection, _):
            row = connection.execute(
                "SELECT revision,memory_json FROM conversation_contexts WHERE binding_hash=?", (binding,),
            ).fetchone()
        if row is None:
            raise ConversationError("conversation_not_found")
        return ConversationSnapshot(row[0], _decode_memory(row[1]))

    def save(self, owner_key: str, conversation_id: str, expected_revision: int, memory: QueryMemory) -> None:
        binding = _binding(owner_key, conversation_id)
        memory = validate_memory(memory)
        if type(expected_revision) is not int or not 0 <= expected_revision < 9223372036854775806:
            raise ConversationError("invalid_conversation")
        error = None
        with self._active() as (connection, now):
            row = connection.execute(
                "SELECT revision,updated_at,memory_json FROM conversation_contexts WHERE binding_hash=?", (binding,),
            ).fetchone()
            if row is None:
                error = "conversation_not_found"
            elif row[0] != expected_revision:
                error = "conversation_conflict"
            else:
                if now < _parse_timestamp(row[1]):
                    raise ConversationError()
                previous = _decode_memory(row[2])
                old = previous.confirmation
                new = memory.confirmation
                if old is not None and new is not None and old.proposal_id == new.proposal_id:
                    if (old.request_hash != new.request_hash or old.created_at != new.created_at
                            or old.expires_at != new.expires_at):
                        raise ConfirmationError("confirmation_stale")
                    if old.approved_at is not None and (old.approved_at != new.approved_at
                            or old.approval_message_hash != new.approval_message_hash):
                        raise ConfirmationError("confirmation_stale")
                    if old.state == "approved" and new.state == "pending":
                        raise ConfirmationError("confirmation_stale")
                    if old.state == "consumed" and new != old:
                        raise ConfirmationError("confirmation_consumed")
                    if old.state in {"expired", "revoked", "invalidated"} and new.state in {"pending", "approved"}:
                        raise ConfirmationError("confirmation_stale")
                    if previous.pending_request != memory.pending_request and new.state != "consumed":
                        memory = replace(memory, confirmation=replace(new, state="invalidated"))
                try:
                    expires = now + timedelta(seconds=self.ttl_seconds)
                except OverflowError:
                    raise ConversationError() from None
                changed = connection.execute(
                    "UPDATE conversation_contexts SET revision=revision+1,memory_json=?,updated_at=?,expires_at=? "
                    "WHERE binding_hash=? AND revision=?",
                    (_encode_memory(memory), now.isoformat(), expires.isoformat(), binding, expected_revision),
                )
                if changed.rowcount != 1:
                    raise ConversationError()
        if error is not None:
            raise ConversationError(error)

    def consume_confirmation(self, owner_key, conversation_id, expected_revision, proposal_id, request_hash, event_version):
        """Consumir aprovação uma vez, depois de o executor conferir a versão externa."""
        binding = _binding(owner_key, conversation_id)
        try:
            parsed = UUID(proposal_id)
            if str(parsed) != proposal_id or parsed.version != 4 or not isinstance(request_hash, str) or not _HASH.fullmatch(request_hash):
                raise ValueError()
            if type(expected_revision) is not int or not 0 <= expected_revision < 9223372036854775806:
                raise ValueError()
            if event_version is not None and (not isinstance(event_version, str) or not event_version.strip()
                    or len(event_version) > 2048 or any(unicodedata.category(c).startswith("C") for c in event_version)):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ConfirmationError("confirmation_invalid") from None
        claim = None
        error = None
        with self._active() as (connection, now):
            row = connection.execute("SELECT revision,memory_json,updated_at FROM conversation_contexts WHERE binding_hash=?", (binding,)).fetchone()
            if row is None:
                error = ConversationError("conversation_not_found")
            else:
                memory = _decode_memory(row[1])
                proposal = memory.confirmation
                if proposal is None or proposal.proposal_id != proposal_id:
                    error = ConfirmationError("confirmation_not_found")
                elif proposal.request_hash != request_hash or proposal.event_version != event_version:
                    error = ConfirmationError("confirmation_stale")
                elif proposal.state == "consumed":
                    error = ConfirmationError("confirmation_consumed")
                elif now >= confirmation_timestamp(proposal.expires_at):
                    error = ConfirmationError("confirmation_expired")
                elif proposal.state != "approved":
                    error = ConfirmationError("confirmation_not_approved")
                elif row[0] != expected_revision:
                    error = ConversationError("conversation_conflict")
                else:
                    pending = memory.pending_request
                    try:
                        if pending is None or pending.awaiting or pending.candidates or pending.fields.get("action") != proposal.action:
                            raise ValueError()
                        current = canonical_parameters(proposal.action, {key: value for key, value in pending.fields.items() if key != "action"})
                        if current != proposal.parameters:
                            raise ValueError()
                    except (ValueError, ConfirmationError):
                        error = ConfirmationError("confirmation_stale")
                    if error is None:
                        if now < _parse_timestamp(row[2]) or now < confirmation_timestamp(proposal.approved_at):
                            raise ConversationError()
                        updated = replace(memory, confirmation=replace(proposal, state="consumed", consumed_at=now.isoformat()))
                        validate_memory(updated)
                        changed = connection.execute(
                            "UPDATE conversation_contexts SET revision=revision+1,memory_json=?,updated_at=?,expires_at=? WHERE binding_hash=? AND revision=?",
                            (_encode_memory(updated), now.isoformat(), (now + timedelta(seconds=self.ttl_seconds)).isoformat(), binding, expected_revision),
                        )
                        if changed.rowcount != 1:
                            raise ConversationError()
                        claim = ConfirmationClaim(proposal.proposal_id, proposal.action, dict(proposal.parameters),
                            proposal.calendar_id, proposal.event_id, proposal.event_version, proposal.request_hash)
        if error is not None:
            raise error
        return claim

    def delete(self, owner_key: str, conversation_id: str) -> None:
        binding = _binding(owner_key, conversation_id)
        with self._active() as (connection, _):
            changed = connection.execute("DELETE FROM conversation_contexts WHERE binding_hash=?", (binding,))
        if changed.rowcount != 1:
            raise ConversationError("conversation_not_found")

    def purge_expired(self) -> int:
        self._now()
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            return self._purge(connection, self._now())
