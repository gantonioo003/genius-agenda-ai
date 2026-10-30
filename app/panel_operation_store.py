"""Session-bound panel proposals; execution remains in the existing agent ledgers."""

from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from uuid import UUID, uuid4


PREVIEW_SECONDS = 600
MAX_PENDING = 20
MAX_ENTRIES = 10000
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)
_STATES = {'preparing', 'pending', 'confirming', 'succeeded', 'failed', 'unknown', 'conflict', 'cancelled', 'expired'}
_TERMINAL = {'succeeded', 'failed', 'unknown', 'conflict'}
_COLUMNS = ('operation_id', 'session_hash', 'request_id', 'action', 'payload_digest', 'state',
            'conversation_id', 'proposal_id', 'expires_at', 'created_at', 'updated_at', 'dto_json')
_ERRORS = {
    'panel_operation_invalid': (422, 'Os dados da operação são inválidos.'),
    'panel_operation_unavailable': (503, 'O registro da operação está indisponível.'),
    'panel_operation_not_found': (404, 'Esta operação não está disponível neste navegador.'),
    'panel_operation_conflict': (409, 'A operação mudou. Confira seu estado antes de continuar.'),
    'panel_operation_request_changed': (409, 'Este pedido já foi usado com outros dados. Gere uma nova prévia.'),
    'panel_operation_limit': (429, 'Há muitas operações abertas. Conclua ou cancele uma prévia.'),
}


class PanelOperationError(RuntimeError):
    def __init__(self, code='panel_operation_unavailable'):
        self.code = code if code in _ERRORS else 'panel_operation_unavailable'
        self.http_status, message = _ERRORS[self.code]
        self.status_code = self.http_status
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class PanelOperation:
    operation_id: str
    request_id: str
    action: str
    payload_digest: str = field(repr=False)
    state: str
    conversation_id: str | None = field(repr=False)
    proposal_id: str | None = field(repr=False)
    expires_at: float
    created_at: float
    updated_at: float
    dto: dict | None = field(repr=False)

    def __repr__(self):
        return f'PanelOperation(state={self.state!r}, <private>)'


@dataclass(frozen=True, repr=False)
class OperationReservation:
    record: PanelOperation
    claimed: bool

    @property
    def created(self):
        return self.claimed

    def __repr__(self):
        return f'OperationReservation(claimed={self.claimed!r}, <private>)'


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError()
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError()
    return value


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError()
    return value


def _seconds(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError()
        value = value.timestamp()
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError()
    return float(value)


def _json(value):
    if type(value) is not dict:
        raise ValueError()
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    if len(raw) > 131072:
        raise ValueError()
    return raw


class PanelOperationStore:
    def __init__(self, path, *, clock=None, ttl_seconds=PREVIEW_SECONDS,
                 max_entries=MAX_ENTRIES, max_pending=MAX_PENDING):
        try:
            if (type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 1800
                    or type(max_entries) is not int or not 1 <= max_entries <= MAX_ENTRIES
                    or type(max_pending) is not int or not 1 <= max_pending <= MAX_PENDING):
                raise ValueError()
            self.path = Path(path)
        except (TypeError, ValueError):
            raise PanelOperationError('panel_operation_invalid') from None
        self.clock = clock or time.time
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_pending = max_pending

    def _now(self):
        try:
            return _seconds(self.clock())
        except (TypeError, ValueError, OverflowError):
            raise PanelOperationError() from None

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('BEGIN IMMEDIATE')
                try:
                    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='panel_operations'").fetchone()
                    if not exists:
                        connection.execute('''CREATE TABLE panel_operations (
                            operation_id TEXT PRIMARY KEY NOT NULL,
                            session_hash TEXT NOT NULL, request_id TEXT NOT NULL,
                            action TEXT NOT NULL, payload_digest TEXT NOT NULL, state TEXT NOT NULL,
                            conversation_id TEXT, proposal_id TEXT, expires_at REAL NOT NULL,
                            created_at REAL NOT NULL, updated_at REAL NOT NULL, dto_json TEXT,
                            UNIQUE(session_hash, request_id))''')
                        connection.execute('CREATE INDEX panel_operations_session_state ON panel_operations(session_hash, state)')
                    table_info = connection.execute('PRAGMA table_info(panel_operations)').fetchall()
                    fields = tuple(row['name'] for row in table_info)
                    primary = tuple(row['name'] for row in table_info if row['pk'])
                    if fields != _COLUMNS or primary != ('operation_id',):
                        raise PanelOperationError()
                    indexes = connection.execute('PRAGMA index_list(panel_operations)').fetchall()
                    unique_indexes = []
                    for row in indexes:
                        if row['unique']:
                            quoted = row['name'].replace('"', '""')
                            unique_indexes.append(tuple(v['name'] for v in connection.execute(f'PRAGMA index_info("{quoted}")')))
                    if ('session_hash', 'request_id') not in unique_indexes:
                        raise PanelOperationError()
                    yield connection
                    connection.execute('COMMIT')
                except BaseException:
                    connection.execute('ROLLBACK')
                    raise
        except (OSError, sqlite3.Error):
            raise PanelOperationError() from None

    def initialize(self):
        with self._connection():
            pass

    @staticmethod
    def _record(row):
        if row is None:
            return None
        try:
            values = dict(row)
            _hash(values.pop('session_hash'))
            _uuid(values['operation_id'])
            _uuid(values['request_id'])
            _hash(values['payload_digest'])
            if values['action'] not in {'create', 'edit', 'reschedule', 'delete'} or values['state'] not in _STATES:
                raise ValueError()
            for name in ('created_at', 'updated_at', 'expires_at'):
                values[name] = _seconds(values[name])
            if not values['created_at'] <= values['updated_at'] or values['expires_at'] < values['created_at']:
                raise ValueError()
            for name in ('conversation_id', 'proposal_id'):
                if values[name] is not None:
                    _uuid(values[name])
            dto_raw = values.pop('dto_json')
            values['dto'] = None if dto_raw is None else json.loads(dto_raw)
            if values['dto'] is not None:
                _json(values['dto'])
            if values['state'] in {'pending', 'confirming', 'succeeded', 'unknown'} and (
                    values['conversation_id'] is None or values['proposal_id'] is None or values['dto'] is None):
                raise ValueError()
            return PanelOperation(**values)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, OverflowError):
            raise PanelOperationError() from None

    @staticmethod
    def _row(connection, session_hash, operation_id):
        return connection.execute('SELECT * FROM panel_operations WHERE session_hash=? AND operation_id=?',
                                  (session_hash, operation_id)).fetchone()

    def _current(self, connection, session_hash, operation_id, now):
        record = self._record(self._row(connection, session_hash, operation_id))
        if record is not None and now < record.updated_at:
            raise PanelOperationError()
        if record is not None and record.state in {'preparing', 'pending'} and now >= record.expires_at:
            connection.execute("UPDATE panel_operations SET state='expired', updated_at=? WHERE operation_id=?", (now, operation_id))
            record = self._record(self._row(connection, session_hash, operation_id))
        return record

    @staticmethod
    def _identity(session_hash, operation_id=None):
        try:
            _hash(session_hash)
            if operation_id is not None:
                _uuid(operation_id)
        except (ValueError, TypeError, AttributeError):
            raise PanelOperationError('panel_operation_invalid') from None

    def get(self, session_hash, operation_id):
        self._identity(session_hash, operation_id)
        with self._connection() as connection:
            return self._current(connection, session_hash, operation_id, self._now())

    def reserve(self, session_hash, request_id, action, payload_digest):
        self._identity(session_hash)
        try:
            _uuid(request_id)
            _hash(payload_digest)
            if action not in {'create', 'edit', 'reschedule', 'delete'}:
                raise ValueError()
        except (TypeError, ValueError):
            raise PanelOperationError('panel_operation_invalid') from None
        now = self._now()
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM panel_operations WHERE session_hash=? AND request_id=?', (session_hash, request_id)).fetchone()
            if row is not None:
                record = self._current(connection, session_hash, row['operation_id'], now)
                if record.action != action or record.payload_digest != payload_digest:
                    raise PanelOperationError('panel_operation_request_changed')
                return OperationReservation(record, False)
            count = connection.execute('SELECT COUNT(*) FROM panel_operations').fetchone()[0]
            if count >= self.max_entries:
                raise PanelOperationError()
            connection.execute("UPDATE panel_operations SET state='expired', updated_at=? WHERE session_hash=? AND state IN ('preparing','pending') AND expires_at<=?", (now, session_hash, now))
            open_count = connection.execute("SELECT COUNT(*) FROM panel_operations WHERE session_hash=? AND state IN ('preparing','pending','confirming','unknown')", (session_hash,)).fetchone()[0]
            if open_count >= self.max_pending:
                raise PanelOperationError('panel_operation_limit')
            operation_id = str(uuid4())
            connection.execute('INSERT INTO panel_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                               (operation_id, session_hash, request_id, action, payload_digest, 'preparing', None, None,
                                now + self.ttl_seconds, now, now, None))
            return OperationReservation(self._record(self._row(connection, session_hash, operation_id)), True)

    def attach_context(self, session_hash, operation_id, conversation_id):
        self._identity(session_hash, operation_id)
        try:
            _uuid(conversation_id)
        except (ValueError, TypeError):
            raise PanelOperationError('panel_operation_invalid') from None
        now = self._now()
        with self._connection() as connection:
            record = self._current(connection, session_hash, operation_id, now)
            if record is None:
                raise PanelOperationError('panel_operation_not_found')
            if record.state != 'preparing' or record.conversation_id not in (None, conversation_id):
                raise PanelOperationError('panel_operation_conflict')
            connection.execute('UPDATE panel_operations SET conversation_id=?, updated_at=? WHERE operation_id=?', (conversation_id, now, operation_id))
            return self._record(self._row(connection, session_hash, operation_id))

    def attach_preview(self, session_hash, operation_id, proposal_id, expires_at, dto):
        self._identity(session_hash, operation_id)
        try:
            _uuid(proposal_id)
            expiry = _seconds(expires_at)
            raw = _json(dto)
        except (ValueError, TypeError, OverflowError):
            raise PanelOperationError('panel_operation_invalid') from None
        now = self._now()
        with self._connection() as connection:
            record = self._current(connection, session_hash, operation_id, now)
            if record is None:
                raise PanelOperationError('panel_operation_not_found')
            if record.state != 'preparing' or record.conversation_id is None or expiry <= now:
                raise PanelOperationError('panel_operation_conflict')
            connection.execute("UPDATE panel_operations SET state='pending', proposal_id=?, expires_at=?, dto_json=?, updated_at=? WHERE operation_id=?",
                               (proposal_id, min(record.expires_at, expiry), raw, now, operation_id))
            return self._record(self._row(connection, session_hash, operation_id))

    def claim_confirm(self, session_hash, operation_id):
        self._identity(session_hash, operation_id)
        now = self._now()
        with self._connection() as connection:
            record = self._current(connection, session_hash, operation_id, now)
            if record is None:
                raise PanelOperationError('panel_operation_not_found')
            if record.state != 'pending':
                return OperationReservation(record, False)
            connection.execute("UPDATE panel_operations SET state='confirming', updated_at=? WHERE operation_id=?", (now, operation_id))
            return OperationReservation(self._record(self._row(connection, session_hash, operation_id)), True)

    def cancel(self, session_hash, operation_id):
        self._identity(session_hash, operation_id)
        now = self._now()
        with self._connection() as connection:
            record = self._current(connection, session_hash, operation_id, now)
            if record is None:
                raise PanelOperationError('panel_operation_not_found')
            if record.state == 'cancelled':
                return record
            if record.state != 'pending':
                raise PanelOperationError('panel_operation_conflict')
            connection.execute("UPDATE panel_operations SET state='cancelled', updated_at=? WHERE operation_id=?", (now, operation_id))
            return self._record(self._row(connection, session_hash, operation_id))

    def finish(self, session_hash, operation_id, state, dto):
        self._identity(session_hash, operation_id)
        try:
            if state not in _TERMINAL:
                raise ValueError()
            raw = _json(dto)
        except (TypeError, ValueError):
            raise PanelOperationError('panel_operation_invalid') from None
        now = self._now()
        with self._connection() as connection:
            record = self._current(connection, session_hash, operation_id, now)
            if record is None:
                raise PanelOperationError('panel_operation_not_found')
            if record.state in _TERMINAL:
                if record.state == state and _json(record.dto) == raw:
                    return record
                raise PanelOperationError('panel_operation_conflict')
            if record.state != 'confirming' and not (record.state == 'preparing' and state in {'failed', 'conflict'}):
                raise PanelOperationError('panel_operation_conflict')
            connection.execute('UPDATE panel_operations SET state=?, dto_json=?, updated_at=? WHERE operation_id=?', (state, raw, now, operation_id))
            return self._record(self._row(connection, session_hash, operation_id))
