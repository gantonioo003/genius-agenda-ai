"""Identidade e resultado da alteração pelo agente, antes de qualquer gravação externa."""
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
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
from app.services.update_snapshot import validate_update_snapshot
from app.services.event_details import DetailsResult, EventDetails
from app.services.rescheduling import ScheduleChange, RescheduleResult
from app.services.agenda import AgendaEvent

SCHEMA_VERSION = '1'
BUSY_TIMEOUT_MS = 1000
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_OPERATION = re.compile(r'agent-update-[0-9a-f]{64}\Z', re.ASCII)
_STATES = {'running', 'unknown', 'confirmed', 'not_applied', 'conflict'}
_ERRORS = {
    'update_invalid': (422, 'Os dados da alteração são inválidos.'),
    'update_unavailable': (503, 'O registro da alteração está temporariamente indisponível.'),
    'update_unresolved': (409, 'Existe uma alteração sem resultado confirmado nesta conversa. Confira o estado antes de continuar.'),
    'update_calendar_changed': (409, 'A agenda mudou desde o registro do pedido. Confira o destino antes de continuar.'),
    'update_snapshot_changed': (409, 'O evento mudou desde o registro do pedido. Confira o estado antes de continuar.'),
    'update_claim_conflict': (409, 'A alteração recebeu outra atualização. Confira o estado antes de continuar.'),
}
_COLUMNS = ('binding_hash', 'request_hash', 'operation_id', 'action', 'calendar_id', 'event_version',
            'before_json', 'transparency', 'parameters_json', 'state', 'result_json', 'attempt_id', 'created_at', 'updated_at')
_INDEX_SQL = "CREATE UNIQUE INDEX agent_update_active ON agent_updates(binding_hash) WHERE state IN ('running','unknown')"

class AgentUpdateError(RuntimeError):
    def __init__(self, code='update_unavailable'):
        self.code = code if isinstance(code, str) and code in _ERRORS else 'update_unavailable'
        self.http_status, message = _ERRORS[self.code]
        self.status_code = self.http_status
        super().__init__(message)

@dataclass(frozen=True, repr=False)
class UpdateRecord:
    operation_id: str
    action: str
    parameters: dict
    calendar_id: str
    event_version: str
    before: dict
    transparency: str
    state: str
    result: dict | None
    attempt_id: str
    created_at: str
    updated_at: str

    def __repr__(self):
        return 'UpdateRecord(<private>)'

@dataclass(frozen=True, repr=False)
class Reservation:
    record: UpdateRecord
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
        return sha256(b'genius-agent-update-owner:v1\0' + owner.encode() + b'\0' + conversation.encode()).hexdigest()
    except (ValueError, TypeError, AttributeError):
        raise AgentUpdateError('update_invalid') from None

def _parameters(action, parameters):
    try:
        if action not in {'edit', 'reschedule'}:
            raise ValueError()
        return canonical_parameters(action, parameters)
    except Exception:
        raise AgentUpdateError('update_invalid') from None


def _content(action, parameters):
    return {'action': action, 'parameters': parameters}


def _request_hash(action, parameters):
    return sha256(b'genius-agent-update-content:v1\0' + _json(_content(action, parameters)).encode()).hexdigest()


def _operation(binding, action, parameters):
    return 'agent-update-' + sha256(b'genius-agent-update-operation:v1\0' + binding.encode() + b'\0' + _json(_content(action, parameters)).encode()).hexdigest()


def update_operation_id(owner_key, conversation_id, action, parameters):
    return _operation(_binding(owner_key, conversation_id), action, _parameters(action, parameters))


def _snapshot(value, action, parameters):
    try:
        snapshot = validate_update_snapshot(value, parameters['event_id'], parameters['time_zone'])
        before = _before(asdict(snapshot.event), parameters['event_id'], parameters['time_zone'], action)
        return snapshot.calendar_id, snapshot.version, before, snapshot.transparency
    except Exception:
        raise AgentUpdateError('update_invalid') from None


def _before(value, event_id, time_zone, action):
    from app.api_updates import _public_details
    if type(value) is not dict or set(value) != {'event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link', 'description', 'location'}:
        raise ValueError()
    event = EventDetails(**value)
    if _public_details(event, event_id) != value:
        raise ValueError()
    if event.status not in {'confirmed', 'tentative'}:
        raise ValueError()
    if not event.all_day and event.time_zone != time_zone or action == 'reschedule' and event.all_day:
        raise ValueError()
    return dict(value)


def _result(value, action, parameters, before):
    try:
        if type(value) is not dict:
            raise ValueError()
        event_id = parameters['event_id']
        zone = parameters['time_zone']
        if action == 'edit':
            from app.api_updates import _public_update
            if set(value) != {'result', 'calendar_name', 'before', 'after', 'requested', 'recovered'}:
                raise ValueError()
            requested = {name: value for name, value in parameters.items() if name not in {'event_id', 'time_zone'}}
            obj = DetailsResult(value['result'], value['calendar_name'], EventDetails(**value['before']),
                EventDetails(**value['after']), value['requested'], value['recovered'])
            projected = _public_update(obj, event_id, requested, zone)
            expected_before = before
            for name in ('event_id', 'status', 'html_link'):
                if value['after'][name] != expected_before[name]:
                    raise ValueError()
        else:
            from app.api_updates import _public_reschedule
            if set(value) != {'result', 'calendar_name', 'before', 'after', 'requested_start', 'requested_end', 'time_zone', 'recovered'}:
                raise ValueError()
            obj = RescheduleResult(value['result'], value['calendar_name'], AgendaEvent(**value['before']),
                AgendaEvent(**value['after']), value['requested_start'], value['requested_end'], value['time_zone'], value['recovered'])
            change = ScheduleChange(datetime.fromisoformat(parameters['start']), datetime.fromisoformat(parameters['end']), zone)
            projected = _public_reschedule(obj, event_id, change)
            expected_before = {name: item for name, item in before.items() if name not in {'description', 'location'}}
            if not expected_before['title'].strip():
                expected_before['title'] = '(sem título)'
            for name in ('event_id', 'title', 'all_day', 'status', 'html_link'):
                if value['after'][name] != expected_before[name]:
                    raise ValueError()
        if projected != value or value['before'] != expected_before:
            raise ValueError()
        return _decoded_json(_json(value))
    except Exception:
        raise AgentUpdateError('update_invalid') from None


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
        (binding, request_hash, operation, action, calendar, version, raw_before, transparency,
            raw_parameters, state, raw_result, attempt, created, updated) = row
        if not _HASH.fullmatch(binding) or not _HASH.fullmatch(request_hash) or not _OPERATION.fullmatch(operation):
            raise ValueError()
        params = _parameters(action, _decoded_json(raw_parameters))
        if _json(params) != raw_parameters or _request_hash(action, params) != request_hash or _operation(binding, action, params) != operation:
            raise ValueError()
        _text(calendar, 1024, spaces=False)
        _text(version, 2048)
        if transparency not in {'opaque', 'transparent'}:
            raise ValueError()
        before = _before(_decoded_json(raw_before), params['event_id'], params['time_zone'], action)
        _uuid(attempt)
        if state not in _STATES or _timestamp(created) > _timestamp(updated):
            raise ValueError()
        result = None if raw_result is None else _result(_decoded_json(raw_result), action, params, before)
        if (state == 'confirmed') != (result is not None):
            raise ValueError()
        return UpdateRecord(operation, action, params, calendar, version, before, transparency, state, result, attempt, created, updated)
    except Exception:
        raise AgentUpdateError() from None

class AgentUpdateStore:
    def __init__(self, path, *, clock=None, max_entries=10000):
        if (clock is not None and not callable(clock) or type(max_entries) is not int or not 1 <= max_entries <= 10000):
            raise AgentUpdateError('update_invalid')
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
            raise AgentUpdateError() from None

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)) as connection:
                yield connection
        except (OSError, sqlite3.Error):
            raise AgentUpdateError() from None

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
        if set(objects) != {('table', 'agent_update_metadata'), ('table', 'agent_updates'), ('index', 'agent_update_active')}:
            raise AgentUpdateError()
        metadata = tuple((row[1], row[2], row[3], row[5]) for row in connection.execute('PRAGMA table_info(agent_update_metadata)'))
        if metadata != (('name', 'TEXT', 1, 1), ('value', 'TEXT', 1, 0)):
            raise AgentUpdateError()
        columns = tuple((row[1], row[2], row[3], row[5]) for row in connection.execute('PRAGMA table_info(agent_updates)'))
        expected = tuple((name, 'TEXT', 0 if name == 'result_json' else 1, 1 if name == 'operation_id' else 0) for name in _COLUMNS)
        if columns != expected:
            raise AgentUpdateError()
        if connection.execute('SELECT name,value FROM agent_update_metadata').fetchall() != [('schema_version', SCHEMA_VERSION)]:
            raise AgentUpdateError()
        if connection.execute("SELECT sql FROM sqlite_master WHERE name='agent_update_active'").fetchone() != (_INDEX_SQL,):
            raise AgentUpdateError()
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise AgentUpdateError()
        for row in connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_updates'):
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
                    connection.execute('CREATE TABLE agent_update_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)')
                    connection.execute("INSERT INTO agent_update_metadata VALUES('schema_version',?)", (SCHEMA_VERSION,))
                    connection.execute('CREATE TABLE agent_updates(binding_hash TEXT NOT NULL,request_hash TEXT NOT NULL,'
                        'operation_id TEXT NOT NULL PRIMARY KEY,action TEXT NOT NULL,calendar_id TEXT NOT NULL,event_version TEXT NOT NULL,'
                        'before_json TEXT NOT NULL,transparency TEXT NOT NULL,parameters_json TEXT NOT NULL,'
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
        return connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_updates WHERE binding_hash=? AND operation_id=?',
                                  (binding, operation)).fetchone()

    def get(self, owner_key, conversation_id, action, parameters):
        binding = _binding(owner_key, conversation_id)
        params = _parameters(action, parameters)
        with self._active() as (connection, _):
            row = self._row(connection, binding, _operation(binding, action, params))
        return None if row is None else _decode(row)

    def active(self, owner_key, conversation_id):
        binding = _binding(owner_key, conversation_id)
        with self._active() as (connection, _):
            row = connection.execute('SELECT ' + ','.join(_COLUMNS) + " FROM agent_updates WHERE binding_hash=? AND state IN ('running','unknown')", (binding,)).fetchone()
        return None if row is None else _decode(row)

    def reserve(self, owner_key, conversation_id, action, parameters, snapshot):
        binding = _binding(owner_key, conversation_id)
        params = _parameters(action, parameters)
        calendar, version, before, transparency = _snapshot(snapshot, action, params)
        operation = _operation(binding, action, params)
        with self._active() as (connection, now):
            row = self._row(connection, binding, operation)
            old = None if row is None else _decode(row)
            if old is not None and old.calendar_id != calendar:
                raise AgentUpdateError('update_calendar_changed')
            if old is not None and old.state in {'running', 'unknown', 'confirmed'}:
                return Reservation(old, False)
            if old is not None and (old.event_version != version or old.before != before or old.transparency != transparency):
                raise AgentUpdateError('update_snapshot_changed')
            if connection.execute("SELECT 1 FROM agent_updates WHERE binding_hash=? AND state IN ('running','unknown')", (binding,)).fetchone():
                raise AgentUpdateError('update_unresolved')
            if old is None:
                if connection.execute('SELECT COUNT(*) FROM agent_updates').fetchone()[0] >= self.max_entries:
                    raise AgentUpdateError()
                connection.execute('INSERT INTO agent_updates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (binding, _request_hash(action, params), operation, action, calendar, version, _json(before), transparency,
                        _json(params), 'running', None, str(uuid4()), now.isoformat(), now.isoformat()))
            else:
                if now < _timestamp(old.updated_at):
                    raise AgentUpdateError()
                connection.execute("UPDATE agent_updates SET state='running',result_json=NULL,attempt_id=?,updated_at=? WHERE operation_id=?",
                                   (str(uuid4()), now.isoformat(), operation))
            return Reservation(_decode(self._row(connection, binding, operation)), True)

    def finish(self, owner_key, conversation_id, operation_id, attempt_id, state, result=None):
        binding = _binding(owner_key, conversation_id)
        try:
            if not isinstance(operation_id, str) or not _OPERATION.fullmatch(operation_id) or state not in _STATES - {'running'}:
                raise ValueError()
            _uuid(attempt_id)
            if (state == 'confirmed') != (result is not None):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise AgentUpdateError('update_invalid') from None
        with self._active() as (connection, now):
            row = self._row(connection, binding, operation_id)
            if row is None:
                raise AgentUpdateError('update_claim_conflict')
            old = _decode(row)
            result = _result(result, old.action, old.parameters, old.before) if state == 'confirmed' else result
            if old.attempt_id != attempt_id:
                raise AgentUpdateError('update_claim_conflict')
            if old.state == state and old.result == result:
                return old
            if old.state == 'confirmed' or old.state != 'running' and not (old.state == 'unknown' and state == 'confirmed'):
                raise AgentUpdateError('update_claim_conflict')
            if now < _timestamp(old.updated_at):
                raise AgentUpdateError()
            changed = connection.execute('UPDATE agent_updates SET state=?,result_json=?,updated_at=? WHERE operation_id=? AND attempt_id=? AND state=?',
                (state, None if result is None else _json(result), now.isoformat(), operation_id, attempt_id, old.state))
            if changed.rowcount != 1:
                raise AgentUpdateError('update_claim_conflict')
            return _decode(self._row(connection, binding, operation_id))
