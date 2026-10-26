"""Reserva durável e de uso único para excluir apenas a proposta aprovada."""
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

from app.confirmation_state import canonical_parameters, proposal_request_hash, validate_confirmation
from app.services.agenda import AgendaEvent
from app.services.confirmation_snapshot import ConfirmationSnapshot, validate_confirmation_snapshot

SCHEMA_VERSION = '1'
# Espera limitada pela transação local concorrente; não repete uma exclusão.
BUSY_TIMEOUT_MS = 5000
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_OPERATION = re.compile(r'agent-delete-[0-9a-f]{64}\Z', re.ASCII)
_STATES = {'running', 'unknown', 'confirmed', 'not_applied'}
_ERRORS = {
    'deletion_invalid': (422, 'Os dados da exclusão são inválidos.'),
    'deletion_unavailable': (503, 'O registro da exclusão está indisponível.'),
    'deletion_unresolved': (409, 'Existe uma exclusão sem resultado confirmado nesta conversa. Confira seu estado antes de continuar.'),
    'deletion_proposal_changed': (409, 'A proposta não corresponde ao registro da exclusão.'),
    'deletion_claim_conflict': (409, 'A exclusão recebeu outra atualização. Confira o estado antes de continuar.'),
}
_COLUMNS = ('binding_hash', 'operation_id', 'proposal_id', 'request_hash', 'calendar_id', 'event_version',
            'event_json', 'parameters_json', 'state', 'result_json', 'attempt_id', 'created_at', 'updated_at')
_INDEX_SQL = "CREATE UNIQUE INDEX agent_deletion_active ON agent_deletions(binding_hash) WHERE state IN ('running','unknown')"

class AgentDeletionError(RuntimeError):
    def __init__(self, code='deletion_unavailable'):
        self.code = code if isinstance(code, str) and code in _ERRORS else 'deletion_unavailable'
        self.http_status, message = _ERRORS[self.code]
        self.status_code = self.http_status
        super().__init__(message)

@dataclass(frozen=True, repr=False)
class DeletionRecord:
    operation_id: str
    proposal_id: str
    parameters: dict
    calendar_id: str
    event_version: str
    event: dict
    request_hash: str
    state: str
    result: dict | None
    attempt_id: str
    created_at: str
    updated_at: str
    def __repr__(self):
        return 'DeletionRecord(<private>)'

@dataclass(frozen=True, repr=False)
class Reservation:
    record: DeletionRecord
    claimed: bool
    def __repr__(self):
        return 'Reservation(<private>)'

def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)

def _text(value, maximum, *, spaces=True):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(unicodedata.category(char).startswith('C') for char in value)
            or not spaces and any(char.isspace() for char in value)):
        raise ValueError()
    value.encode('utf-8')
    return value

def _uuid(value):
    if not isinstance(value, str):
        raise ValueError()
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError()
    return value

def _binding(owner, conversation):
    try:
        if not isinstance(owner, str) or not _HASH.fullmatch(owner):
            raise ValueError()
        _uuid(conversation)
        return sha256(b'genius-agent-deletion-owner:v1\0' + owner.encode() + b'\0' + conversation.encode()).hexdigest()
    except (ValueError, TypeError, AttributeError):
        raise AgentDeletionError('deletion_invalid') from None

def _operation(binding, proposal_id, request_hash):
    return 'agent-delete-' + sha256(b'genius-agent-deletion-operation:v1\0' + binding.encode()
        + b'\0' + proposal_id.encode() + b'\0' + request_hash.encode()).hexdigest()

def _proposal(value):
    try:
        proposal = validate_confirmation(value)
        if proposal.action != 'delete' or proposal.state != 'approved':
            raise ValueError()
        _text(proposal.calendar_id, 1024, spaces=False)
        _text(proposal.event_version, 2048)
        return proposal
    except Exception:
        raise AgentDeletionError('deletion_invalid') from None

def deletion_operation_id(owner_key, conversation_id, proposal):
    proposal = _proposal(proposal)
    return _operation(_binding(owner_key, conversation_id), proposal.proposal_id, proposal.request_hash)

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

def _event(value, parameters, calendar, version):
    if type(value) is not dict or set(value) != {'event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status', 'html_link'}:
        raise ValueError()
    snapshot = validate_confirmation_snapshot(ConfirmationSnapshot(calendar, 'Validação privada',
        AgendaEvent(**value), version), parameters['event_id'], parameters['time_zone'])
    if snapshot.event is None or asdict(snapshot.event) != value:
        raise ValueError()
    return dict(value)

def _result(value, event_id):
    try:
        if (type(value) is not dict or set(value) != {'result', 'calendar_name', 'event_id', 'recovered'}
                or value['result'] not in {'deleted', 'already_absent'} or value['event_id'] != event_id
                or type(value['recovered']) is not bool
                or value['result'] == 'already_absent' and value['recovered']):
            raise ValueError()
        _text(value['calendar_name'], 4000)
        return _decoded_json(_json(value))
    except Exception:
        raise AgentDeletionError('deletion_invalid') from None

def _decode(row):
    try:
        (binding, operation, proposal, request_hash, calendar, version, raw_event, raw_parameters,
            state, raw_result, attempt, created, updated) = row
        if (not _HASH.fullmatch(binding) or not _HASH.fullmatch(request_hash)
                or not _OPERATION.fullmatch(operation)):
            raise ValueError()
        _uuid(proposal)
        _text(calendar, 1024, spaces=False)
        _text(version, 2048)
        parameters = canonical_parameters('delete', _decoded_json(raw_parameters))
        if _json(parameters) != raw_parameters:
            raise ValueError()
        event = _event(_decoded_json(raw_event), parameters, calendar, version)
        if (proposal_request_hash('delete', parameters, calendar, parameters['event_id'], version, event) != request_hash
                or _operation(binding, proposal, request_hash) != operation):
            raise ValueError()
        _uuid(attempt)
        if state not in _STATES or _timestamp(created) > _timestamp(updated):
            raise ValueError()
        result = None if raw_result is None else _result(_decoded_json(raw_result), parameters['event_id'])
        if (state == 'confirmed') != (result is not None):
            raise ValueError()
        return DeletionRecord(operation, proposal, parameters, calendar, version, event,
                              request_hash, state, result, attempt, created, updated)
    except Exception:
        raise AgentDeletionError() from None

class AgentDeletionStore:
    def __init__(self, path, *, clock=None, max_entries=10000):
        if (clock is not None and not callable(clock) or type(max_entries) is not int or not 1 <= max_entries <= 10000):
            raise AgentDeletionError('deletion_invalid')
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
            raise AgentDeletionError() from None
    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)) as connection:
                yield connection
        except (OSError, sqlite3.Error):
            raise AgentDeletionError() from None
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
        if set(objects) != {('table', 'agent_deletion_metadata'), ('table', 'agent_deletions'), ('index', 'agent_deletion_active')}:
            raise AgentDeletionError()
        metadata = tuple((row[1], row[2], row[3], row[5]) for row in connection.execute('PRAGMA table_info(agent_deletion_metadata)'))
        if metadata != (('name', 'TEXT', 1, 1), ('value', 'TEXT', 1, 0)):
            raise AgentDeletionError()
        columns = tuple((row[1], row[2], row[3], row[5]) for row in connection.execute('PRAGMA table_info(agent_deletions)'))
        if columns != tuple((name, 'TEXT', 0 if name == 'result_json' else 1, 1 if name == 'operation_id' else 0) for name in _COLUMNS):
            raise AgentDeletionError()
        if connection.execute('SELECT name,value FROM agent_deletion_metadata').fetchall() != [('schema_version', SCHEMA_VERSION)]:
            raise AgentDeletionError()
        if connection.execute("SELECT sql FROM sqlite_master WHERE name='agent_deletion_active'").fetchone() != (_INDEX_SQL,):
            raise AgentDeletionError()
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise AgentDeletionError()
        for row in connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_deletions'):
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
                    connection.execute('CREATE TABLE agent_deletion_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)')
                    connection.execute("INSERT INTO agent_deletion_metadata VALUES('schema_version',?)", (SCHEMA_VERSION,))
                    connection.execute('CREATE TABLE agent_deletions(binding_hash TEXT NOT NULL,operation_id TEXT NOT NULL PRIMARY KEY,'
                        'proposal_id TEXT NOT NULL,request_hash TEXT NOT NULL,calendar_id TEXT NOT NULL,event_version TEXT NOT NULL,'
                        'event_json TEXT NOT NULL,parameters_json TEXT NOT NULL,state TEXT NOT NULL,result_json TEXT,'
                        'attempt_id TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(binding_hash,proposal_id))')
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
    def _row(connection, binding, proposal):
        return connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_deletions WHERE binding_hash=? AND proposal_id=?',
                                  (binding, proposal)).fetchone()
    def get(self, owner_key, conversation_id, proposal_id):
        binding = _binding(owner_key, conversation_id)
        try:
            _uuid(proposal_id)
        except Exception:
            raise AgentDeletionError('deletion_invalid') from None
        with self._active() as (connection, _):
            row = self._row(connection, binding, proposal_id)
        return None if row is None else _decode(row)
    def active(self, owner_key, conversation_id):
        binding = _binding(owner_key, conversation_id)
        with self._active() as (connection, _):
            row = connection.execute('SELECT ' + ','.join(_COLUMNS) + " FROM agent_deletions WHERE binding_hash=? AND state IN ('running','unknown')", (binding,)).fetchone()
        return None if row is None else _decode(row)
    def reserve(self, owner_key, conversation_id, proposal):
        binding = _binding(owner_key, conversation_id)
        proposal = _proposal(proposal)
        operation = _operation(binding, proposal.proposal_id, proposal.request_hash)
        with self._active() as (connection, now):
            row = self._row(connection, binding, proposal.proposal_id)
            if row is not None:
                old = _decode(row)
                if (old.operation_id != operation or old.parameters != proposal.parameters
                        or old.calendar_id != proposal.calendar_id or old.event_version != proposal.event_version
                        or old.event != proposal.event or old.request_hash != proposal.request_hash):
                    raise AgentDeletionError('deletion_proposal_changed')
                return Reservation(old, False)
            if connection.execute("SELECT 1 FROM agent_deletions WHERE binding_hash=? AND state IN ('running','unknown')", (binding,)).fetchone():
                raise AgentDeletionError('deletion_unresolved')
            if connection.execute('SELECT COUNT(*) FROM agent_deletions').fetchone()[0] >= self.max_entries:
                raise AgentDeletionError()
            connection.execute('INSERT INTO agent_deletions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (binding, operation, proposal.proposal_id, proposal.request_hash, proposal.calendar_id,
                    proposal.event_version, _json(proposal.event), _json(proposal.parameters), 'running', None,
                    str(uuid4()), now.isoformat(), now.isoformat()))
            return Reservation(_decode(self._row(connection, binding, proposal.proposal_id)), True)
    def finish(self, owner_key, conversation_id, operation_id, attempt_id, state, result=None):
        binding = _binding(owner_key, conversation_id)
        try:
            if not isinstance(operation_id, str) or not _OPERATION.fullmatch(operation_id) or state not in _STATES - {'running'}:
                raise ValueError()
            _uuid(attempt_id)
            if (state == 'confirmed') != (result is not None):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise AgentDeletionError('deletion_invalid') from None
        with self._active() as (connection, now):
            row = connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_deletions WHERE binding_hash=? AND operation_id=?',
                                     (binding, operation_id)).fetchone()
            if row is None:
                raise AgentDeletionError('deletion_claim_conflict')
            old = _decode(row)
            result = _result(result, old.parameters['event_id']) if state == 'confirmed' else result
            if old.attempt_id != attempt_id:
                raise AgentDeletionError('deletion_claim_conflict')
            if old.state == state and old.result == result:
                return old
            if old.state == 'confirmed' or old.state != 'running' and not (old.state == 'unknown' and state == 'confirmed'):
                raise AgentDeletionError('deletion_claim_conflict')
            if now < _timestamp(old.updated_at):
                raise AgentDeletionError()
            changed = connection.execute('UPDATE agent_deletions SET state=?,result_json=?,updated_at=? WHERE operation_id=? AND attempt_id=? AND state=?',
                (state, None if result is None else _json(result), now.isoformat(), operation_id, attempt_id, old.state))
            if changed.rowcount != 1:
                raise AgentDeletionError('deletion_claim_conflict')
            return _decode(connection.execute('SELECT ' + ','.join(_COLUMNS) + ' FROM agent_deletions WHERE operation_id=?',
                                              (operation_id,)).fetchone())
