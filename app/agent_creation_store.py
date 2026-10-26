"""Identidade e resultado da criação pelo agente, antes de qualquer gravação externa."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
import unicodedata
from uuid import UUID, uuid4

from app.confirmation_state import canonical_parameters
from app.services.event_selection import validate_event_id

SCHEMA_VERSION = '1'
BUSY_TIMEOUT_MS = 1000
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_OPERATION = re.compile(r'agent-create-[0-9a-f]{64}\Z', re.ASCII)
_STATES = {'running', 'unknown', 'confirmed', 'not_applied', 'conflict'}
_ERRORS = {
    'creation_invalid': (422, 'Os dados da criação são inválidos.'),
    'creation_unavailable': (503, 'O registro da criação está temporariamente indisponível.'),
    'creation_unresolved': (409, 'Existe uma criação sem resultado confirmado nesta conversa. Confira o estado antes de continuar.'),
    'creation_calendar_changed': (409, 'A agenda mudou desde o registro do pedido. Confira o destino antes de continuar.'),
    'creation_claim_conflict': (409, 'A criação recebeu outra atualização. Confira o estado antes de continuar.'),
}
_COLUMNS = ('binding_hash', 'request_hash', 'operation_id', 'calendar_id', 'parameters_json',
            'state', 'result_json', 'attempt_id', 'created_at', 'updated_at')
_INDEX_SQL = "CREATE UNIQUE INDEX agent_creation_active ON agent_creations(binding_hash) WHERE state IN ('running','unknown')"

class AgentCreationError(RuntimeError):
    def __init__(self, code='creation_unavailable'):
        self.code = code if isinstance(code, str) and code in _ERRORS else 'creation_unavailable'
        self.http_status, message = _ERRORS[self.code]
        self.status_code = self.http_status
        super().__init__(message)

@dataclass(frozen=True, repr=False)
class AgentCreationRecord:
    operation_id: str
    state: str
    parameters: dict
    calendar_id: str
    result: dict | None
    attempt_id: str
    created_at: str
    updated_at: str

    def __repr__(self):
        return 'AgentCreationRecord(<private>)'

@dataclass(frozen=True, repr=False)
class Reservation:
    record: AgentCreationRecord
    claimed: bool

    def __repr__(self):
        return 'Reservation(<private>)'

def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)

def _text(value, maximum, *, nonempty=True, spaces=True):
    if (not isinstance(value, str) or len(value) > maximum or nonempty and not value.strip()
            or any(unicodedata.category(char).startswith('C') for char in value)
            or not spaces and any(char.isspace() for char in value)):
        raise ValueError()
    value.encode('utf-8')
    return value

def _uuid(value):
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError()
    return value

def _binding(owner, conversation):
    try:
        if not isinstance(owner, str) or not _HASH.fullmatch(owner):
            raise ValueError()
        _uuid(conversation)
        return sha256(b'genius-agent-create-owner:v1\0' + owner.encode() + b'\0' + conversation.encode()).hexdigest()
    except (ValueError, TypeError, AttributeError):
        raise AgentCreationError('creation_invalid') from None

def _parameters(parameters):
    try:
        return canonical_parameters('create', parameters)
    except Exception:
        raise AgentCreationError('creation_invalid') from None

def _request_hash(parameters):
    return sha256(b'genius-agent-create-content:v1\0' + _json(parameters).encode()).hexdigest()

def _operation(binding, parameters):
    return 'agent-create-' + sha256(b'genius-agent-create-operation:v1\0' + binding.encode() + b'\0' + _json(parameters).encode()).hexdigest()

def creation_operation_id(owner_key, conversation_id, parameters):
    return _operation(_binding(owner_key, conversation_id), _parameters(parameters))

def _result(value):
    try:
        if type(value) is not dict or set(value) != {'event_id', 'html_link', 'calendar_name', 'reused', 'recovered'}:
            raise ValueError()
        validate_event_id(value['event_id'])
        _text(value['html_link'], 8000)
        _text(value['calendar_name'], 4000)
        if type(value['reused']) is not bool or type(value['recovered']) is not bool:
            raise ValueError()
        return dict(value)
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise AgentCreationError('creation_invalid') from None

def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError()
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0 or parsed.isoformat() != value:
        raise ValueError()
    return parsed

def _decoded_json(value):
    if not isinstance(value, str) or len(value) > 256 * 1024:
        raise ValueError()
    decoded = json.loads(value)
    if _json(decoded) != value:
        raise ValueError()
    return decoded

def _decode(row):
    try:
        binding, request_hash, operation, calendar, raw_parameters, state, raw_result, attempt, created, updated = row
        if not _HASH.fullmatch(binding) or not _HASH.fullmatch(request_hash) or not _OPERATION.fullmatch(operation):
            raise ValueError()
        params = _parameters(_decoded_json(raw_parameters))
        if _json(params) != raw_parameters or _request_hash(params) != request_hash or _operation(binding, params) != operation:
            raise ValueError()
        _text(calendar, 1024, spaces=False)
        _uuid(attempt)
        if state not in _STATES or _timestamp(created) > _timestamp(updated):
            raise ValueError()
        result = None if raw_result is None else _result(_decoded_json(raw_result))
        if (state == 'confirmed') != (result is not None):
            raise ValueError()
        return AgentCreationRecord(operation, state, params, calendar, result, attempt, created, updated)
    except Exception:
        raise AgentCreationError() from None

class AgentCreationStore:
    def __init__(self, path, *, clock=None, max_entries=10000):
        if (clock is not None and not callable(clock) or type(max_entries) is not int or not 1 <= max_entries <= 10000):
            raise AgentCreationError('creation_invalid')
        self.path = Path(path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_entries = max_entries
        self._initialized = False
        self._initialization_lock = Lock()

    def _now(self):
        try:
            now = self._clock()
            if not isinstance(now, datetime) or now.utcoffset() is None:
                raise ValueError()
            return now.astimezone(timezone.utc)
        except Exception:
            raise AgentCreationError() from None

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)) as connection:
                yield connection
        except (OSError, sqlite3.Error):
            raise AgentCreationError() from None

    @staticmethod
    @contextmanager
    def _transaction(connection):
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    @staticmethod
    def _check_schema(connection):
        objects = connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if set(objects) != {('table', 'agent_creation_metadata'), ('table', 'agent_creations'), ('index', 'agent_creation_active')}:
            raise AgentCreationError()
        metadata = tuple((row[1], row[2], row[3], row[5]) for row in connection.execute('PRAGMA table_info(agent_creation_metadata)'))
        if metadata != (('name', 'TEXT', 1, 1), ('value', 'TEXT', 1, 0)):
            raise AgentCreationError()
        columns = tuple((row[1], row[2], row[3], row[5]) for row in connection.execute('PRAGMA table_info(agent_creations)'))
        expected = tuple((name, 'TEXT', 0 if name == 'result_json' else 1, 1 if name == 'operation_id' else 0) for name in _COLUMNS)
        if columns != expected:
            raise AgentCreationError()
        if connection.execute('SELECT name,value FROM agent_creation_metadata').fetchall() != [('schema_version', SCHEMA_VERSION)]:
            raise AgentCreationError()
        if connection.execute("SELECT sql FROM sqlite_master WHERE name='agent_creation_active'").fetchone() != (_INDEX_SQL,):
            raise AgentCreationError()
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise AgentCreationError()
        for row in connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_creations'):
            _decode(row)

    def initialize(self):
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            self._now()
            with self._connection() as connection, self._transaction(connection):
                if not connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall():
                    connection.execute('CREATE TABLE agent_creation_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)')
                    connection.execute("INSERT INTO agent_creation_metadata VALUES('schema_version',?)", (SCHEMA_VERSION,))
                    connection.execute('CREATE TABLE agent_creations(binding_hash TEXT NOT NULL,request_hash TEXT NOT NULL,'
                        'operation_id TEXT NOT NULL PRIMARY KEY,calendar_id TEXT NOT NULL,parameters_json TEXT NOT NULL,'
                        'state TEXT NOT NULL,result_json TEXT,attempt_id TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,'
                        'UNIQUE(binding_hash,request_hash))')
                    connection.execute(_INDEX_SQL)
                self._check_schema(connection)
            self._initialized = True

    @contextmanager
    def _active(self):
        self._now()
        self.initialize()
        with self._connection() as connection, self._transaction(connection):
            self._check_schema(connection)
            yield connection, self._now()

    @staticmethod
    def _row(connection, binding, operation):
        return connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_creations WHERE binding_hash=? AND operation_id=?',
                                  (binding, operation)).fetchone()

    def get(self, owner_key, conversation_id, parameters):
        binding = _binding(owner_key, conversation_id)
        params = _parameters(parameters)
        with self._active() as (connection, _):
            row = self._row(connection, binding, _operation(binding, params))
        return None if row is None else _decode(row)

    def active(self, owner_key, conversation_id):
        binding = _binding(owner_key, conversation_id)
        with self._active() as (connection, _):
            row = connection.execute('SELECT ' + ','.join(_COLUMNS) + " FROM agent_creations WHERE binding_hash=? AND state IN ('running','unknown')", (binding,)).fetchone()
        return None if row is None else _decode(row)

    def reserve(self, owner_key, conversation_id, parameters, calendar_id):
        binding = _binding(owner_key, conversation_id)
        params = _parameters(parameters)
        try:
            _text(calendar_id, 1024, spaces=False)
        except (ValueError, TypeError, UnicodeError):
            raise AgentCreationError('creation_invalid') from None
        operation = _operation(binding, params)
        with self._active() as (connection, now):
            row = self._row(connection, binding, operation)
            old = None if row is None else _decode(row)
            if old is not None and old.calendar_id != calendar_id:
                raise AgentCreationError('creation_calendar_changed')
            if old is not None and old.state in {'running', 'unknown', 'confirmed'}:
                return Reservation(old, False)
            if connection.execute("SELECT 1 FROM agent_creations WHERE binding_hash=? AND state IN ('running','unknown')", (binding,)).fetchone():
                raise AgentCreationError('creation_unresolved')
            if old is None:
                if connection.execute('SELECT COUNT(*) FROM agent_creations').fetchone()[0] >= self.max_entries:
                    raise AgentCreationError()
                connection.execute('INSERT INTO agent_creations VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (binding, _request_hash(params), operation, calendar_id, _json(params), 'running', None,
                     str(uuid4()), now.isoformat(), now.isoformat()))
            else:
                if now < _timestamp(old.updated_at):
                    raise AgentCreationError()
                connection.execute("UPDATE agent_creations SET state='running',result_json=NULL,attempt_id=?,updated_at=? WHERE operation_id=?",
                                   (str(uuid4()), now.isoformat(), operation))
            return Reservation(_decode(self._row(connection, binding, operation)), True)

    def finish(self, owner_key, conversation_id, operation_id, attempt_id, state, result=None):
        binding = _binding(owner_key, conversation_id)
        try:
            if not isinstance(operation_id, str) or not _OPERATION.fullmatch(operation_id) or state not in _STATES - {'running'}:
                raise ValueError()
            _uuid(attempt_id)
            result = _result(result) if state == 'confirmed' else result
            if (state == 'confirmed') != (result is not None):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise AgentCreationError('creation_invalid') from None
        with self._active() as (connection, now):
            row = self._row(connection, binding, operation_id)
            if row is None:
                raise AgentCreationError('creation_claim_conflict')
            old = _decode(row)
            if old.attempt_id != attempt_id:
                raise AgentCreationError('creation_claim_conflict')
            if old.state == state and old.result == result:
                return old
            if old.state == 'confirmed' or old.state != 'running' and not (old.state == 'unknown' and state == 'confirmed'):
                raise AgentCreationError('creation_claim_conflict')
            if now < _timestamp(old.updated_at):
                raise AgentCreationError()
            changed = connection.execute('UPDATE agent_creations SET state=?,result_json=?,updated_at=? WHERE operation_id=? AND attempt_id=? AND state=?',
                (state, None if result is None else _json(result), now.isoformat(), operation_id, attempt_id, old.state))
            if changed.rowcount != 1:
                raise AgentCreationError('creation_claim_conflict')
            return _decode(self._row(connection, binding, operation_id))
