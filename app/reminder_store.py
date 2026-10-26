"""Ledger local de lembretes e observações de canal; sem agenda ou transporte.

Uma reserva é persistente e só o claim ``new`` pode avançá-la. Não existe TTL,
reset de tentativas nem recuperação automática de envio potencialmente iniciado.
"""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from threading import Lock
import unicodedata
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_REMINDER_PATH = Path("data/reminders.sqlite3")
DEFAULT_REMINDERS_PATH = DEFAULT_REMINDER_PATH
SCHEMA_VERSION = "1"
_ERROR = "Estado local dos lembretes indisponível ou inconsistente."
_INPUT_ERROR = "Dados do lembrete inválidos."
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SLOTS = ("day_before", "preparation", "imminent")
_DEFAULT_OFFSETS = (("day_before", None), ("preparation", 60), ("imminent", 15))
_STATES = frozenset({"pending", "blocked", "reserved", "sending", "accepted",
                     "uncertain", "cancelled", "expired"})
_BLOCK_REASONS = frozenset({"channel_window_unknown", "channel_window_closed",
    "channel_window_margin", "template_unavailable", "permission_missing",
    "recipient_mismatch", "invalid_format", "invalid_event", "calendar_unavailable"})
_CANCEL_REASONS = frozenset({"event_cancelled", "event_rescheduled", "event_changed",
    "configuration_changed", "configuration_disabled", "slot_disabled", "binding_changed"})
_REASONS = _BLOCK_REASONS | _CANCEL_REASONS | frozenset({"planned", "replanned",
    "reminder_expired", "reserved", "sending", "accepted", "uncertain", "released"})
_OBS_REASONS = frozenset({"valid", "invalid_timestamp", "future_timestamp",
                          "unsupported_type", "untrusted_provenance"})
_PROVENANCES = frozenset({"sandbox_callback_capacity", "synthetic"})
_POTENTIAL_STATES = frozenset({"sending", "accepted", "uncertain"})
_STATE_REASONS = {
    "pending": frozenset({"planned", "replanned", "configuration_changed", "released"}),
    "blocked": _BLOCK_REASONS, "reserved": frozenset({"reserved"}),
    "sending": frozenset({"sending"}), "accepted": frozenset({"accepted"}),
    "uncertain": frozenset({"uncertain"}), "cancelled": _CANCEL_REASONS,
    "expired": frozenset({"reminder_expired"}),
}
_PREVIOUS_STATES = {
    "pending": {None, "pending", "blocked", "reserved", "cancelled", "expired"},
    "blocked": {"pending", "blocked"}, "reserved": {"pending", "blocked"},
    "sending": {"reserved"}, "accepted": {"sending"}, "uncertain": {"sending"},
    "cancelled": {"pending", "blocked", "reserved"},
    "expired": {None, "pending", "blocked", "reserved", "cancelled", "expired"},
}
_SCHEMA = {
    "reminder_metadata": "CREATE TABLE reminder_metadata(name TEXT NOT NULL PRIMARY KEY,value TEXT NOT NULL)",
    "reminder_config": "CREATE TABLE reminder_config(singleton INTEGER NOT NULL PRIMARY KEY CHECK(singleton=1),calendar_id TEXT NOT NULL,recipient_key TEXT NOT NULL,day_before INTEGER,preparation INTEGER,imminent INTEGER,enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),permission_granted INTEGER NOT NULL CHECK(permission_granted IN (0,1)),revision INTEGER NOT NULL CHECK(revision>0))",
    "reminders": "CREATE TABLE reminders(key TEXT NOT NULL PRIMARY KEY,calendar_id TEXT NOT NULL,event_id TEXT NOT NULL,recipient_key TEXT NOT NULL,start_at TEXT NOT NULL,due_at TEXT NOT NULL,deadline_at TEXT NOT NULL,slot TEXT NOT NULL,offset_minutes INTEGER NOT NULL,title TEXT NOT NULL,calendar_timezone TEXT NOT NULL,config_revision INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,attempt_id TEXT,message_id_fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(calendar_id,event_id,start_at,slot,recipient_key))",
    "reminder_history": "CREATE TABLE reminder_history(sequence INTEGER NOT NULL PRIMARY KEY,reminder_key TEXT NOT NULL REFERENCES reminders(key),from_state TEXT,to_state TEXT NOT NULL,reason TEXT NOT NULL,attempt_id TEXT,at TEXT NOT NULL)",
    "channel_observations": "CREATE TABLE channel_observations(event_key TEXT NOT NULL PRIMARY KEY,recipient_key TEXT NOT NULL,message_at TEXT,received_at TEXT NOT NULL,valid INTEGER NOT NULL CHECK(valid IN (0,1)),reason TEXT NOT NULL,provenance TEXT NOT NULL)",
    "reminders_due": "CREATE INDEX reminders_due ON reminders(state,due_at,key)",
    "reminder_history_order": "CREATE INDEX reminder_history_order ON reminder_history(reminder_key,sequence)",
    "channel_observations_latest": "CREATE INDEX channel_observations_latest ON channel_observations(recipient_key,valid,message_at,event_key)",
}
_RECORD_COLUMNS = ("key,calendar_id,event_id,recipient_key,start_at,due_at,deadline_at,"
    "slot,offset_minutes,title,calendar_timezone,config_revision,state,reason,attempt_id,"
    "message_id_fingerprint,created_at,updated_at")
_COLUMN_SIGNATURES = {
    "reminder_metadata": (("name", "TEXT", 1, 1), ("value", "TEXT", 1, 0)),
    "reminder_config": (("singleton", "INTEGER", 1, 1), ("calendar_id", "TEXT", 1, 0),
        ("recipient_key", "TEXT", 1, 0), *((slot, "INTEGER", 0, 0) for slot in _SLOTS),
        ("enabled", "INTEGER", 1, 0), ("permission_granted", "INTEGER", 1, 0),
        ("revision", "INTEGER", 1, 0)),
    "reminders": tuple((name, "INTEGER" if name in {"offset_minutes", "config_revision"} else "TEXT",
        0 if name in {"attempt_id", "message_id_fingerprint"} else 1, int(name == "key"))
        for name in _RECORD_COLUMNS.split(",")),
    "reminder_history": (("sequence", "INTEGER", 1, 1), ("reminder_key", "TEXT", 1, 0),
        ("from_state", "TEXT", 0, 0), ("to_state", "TEXT", 1, 0), ("reason", "TEXT", 1, 0),
        ("attempt_id", "TEXT", 0, 0), ("at", "TEXT", 1, 0)),
    "channel_observations": (("event_key", "TEXT", 1, 1), ("recipient_key", "TEXT", 1, 0),
        ("message_at", "TEXT", 0, 0), ("received_at", "TEXT", 1, 0),
        ("valid", "INTEGER", 1, 0), ("reason", "TEXT", 1, 0), ("provenance", "TEXT", 1, 0)),
}
_INDEX_SIGNATURES = {
    "reminder_metadata": {("sqlite_autoindex_reminder_metadata_1", 1, "pk", 0, ("name",))},
    "reminder_config": set(),
    "reminders": {("sqlite_autoindex_reminders_1", 1, "pk", 0, ("key",)),
        ("sqlite_autoindex_reminders_2", 1, "u", 0, ("calendar_id", "event_id", "start_at", "slot", "recipient_key")),
        ("reminders_due", 0, "c", 0, ("state", "due_at", "key"))},
    "reminder_history": {("reminder_history_order", 0, "c", 0, ("reminder_key", "sequence"))},
    "channel_observations": {("sqlite_autoindex_channel_observations_1", 1, "pk", 0, ("event_key",)),
        ("channel_observations_latest", 0, "c", 0, ("recipient_key", "valid", "message_at", "event_key"))},
}


class ReminderStoreError(RuntimeError):
    """Erro sanitizado, sem SQL, caminhos, identificadores ou conteúdo privado."""

    def __init__(self):
        super().__init__(_ERROR)


class _Private:
    def __repr__(self):
        return type(self).__name__ + "(<private>)"


@dataclass(frozen=True, repr=False)
class ReminderConfig(_Private):
    calendar_id: str
    recipient_key: str
    offsets: tuple[tuple[str, int | None], ...]
    enabled: bool
    permission_granted: bool
    revision: int


@dataclass(frozen=True, repr=False)
class ReminderPlan(_Private):
    event_id: str
    start_at: datetime
    slot: str
    title: str
    calendar_timezone: str


@dataclass(frozen=True, repr=False)
class ReminderRecord(_Private):
    key: str
    calendar_id: str
    event_id: str
    recipient_key: str
    start_at: datetime
    due_at: datetime
    deadline_at: datetime
    slot: str
    offset_minutes: int
    title: str
    calendar_timezone: str
    config_revision: int
    state: str
    reason: str
    attempt_id: str | None
    message_id_fingerprint: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, repr=False)
class ReminderSyncSnapshot(_Private):
    config: ReminderConfig | None
    records: tuple[ReminderRecord, ...]
    history_sequence: int


@dataclass(frozen=True, repr=False)
class ReminderSyncCancellation(_Private):
    key: str
    reason: str


@dataclass(frozen=True, repr=False)
class ReminderSyncResult(_Private):
    records: tuple[ReminderRecord, ...]
    cancelled_count: int
    replanned_count: int
    unchanged_count: int


@dataclass(frozen=True, repr=False)
class ReminderDeliverySnapshot(_Private):
    config: ReminderConfig | None
    records: tuple[ReminderRecord, ...]
    expired_count: int


@dataclass(frozen=True, repr=False)
class ReminderRecoverySnapshot(_Private):
    config: ReminderConfig | None
    records: tuple[ReminderRecord, ...]
    protected_count: int
    history_sequence: int
    captured_at: datetime
    limit: int


@dataclass(frozen=True, repr=False)
class ReminderRecoveryResult(_Private):
    reservations_recovered: int
    expired_count: int
    uncertain_count: int
    protected_count: int


@dataclass(frozen=True, repr=False)
class ReminderClaim(_Private):
    status: str
    reminder: ReminderRecord
    attempt_id: str | None


@dataclass(frozen=True, repr=False)
class ReminderHistory(_Private):
    from_state: str | None
    to_state: str
    reason: str
    attempt_id: str | None
    at: datetime


@dataclass(frozen=True, repr=False)
class ChannelObservation(_Private):
    event_key: str
    recipient_key: str
    message_at: datetime | None
    received_at: datetime
    valid: bool
    reason: str
    provenance: str


def _hash(value):
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError(_INPUT_ERROR)
    return value


def _identifier(value, limit=1024):
    if (not isinstance(value, str) or not 1 <= len(value) <= limit or not value.strip()
            or any(unicodedata.category(char).startswith("C") for char in value)):
        raise ValueError(_INPUT_ERROR)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(_INPUT_ERROR) from None
    return value


def _instant(value):
    try:
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(_INPUT_ERROR) from None


def _stamp(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError(_INPUT_ERROR)
    result = _instant(datetime.fromisoformat(value))
    if result.isoformat() != value:
        raise ValueError(_INPUT_ERROR)
    return result


def _revision(value):
    if type(value) is not int or value < 1:
        raise ValueError(_INPUT_ERROR)
    return value


def _offsets(value):
    if value is None:
        return _DEFAULT_OFFSETS
    if not isinstance(value, dict) or set(value) != set(_SLOTS):
        raise ValueError(_INPUT_ERROR)
    result = tuple((slot, value[slot]) for slot in _SLOTS)
    active = []
    for _, minutes in result:
        if minutes is not None:
            if type(minutes) is not int or not 1 <= minutes <= 10080:
                raise ValueError(_INPUT_ERROR)
            active.append(minutes)
    if any(first <= second for first, second in zip(active, active[1:])):
        raise ValueError(_INPUT_ERROR)
    return result


def _title(value):
    if not isinstance(value, str) or len(value) > 10000:
        raise ValueError(_INPUT_ERROR)
    value = unicodedata.normalize("NFC", value)
    value = "".join(" " if char.isspace() else char for char in value)
    value = "".join(char for char in value if unicodedata.category(char) not in {"Cc", "Cf"})
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError(_INPUT_ERROR) from None
    value = " ".join(value.split()) or "Compromisso"
    return value if len(value) <= 160 else value[:159] + "…"


def _zone(value):
    _identifier(value, 128)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(_INPUT_ERROR) from None
    return value


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError(_INPUT_ERROR)
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise ValueError(_INPUT_ERROR) from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError(_INPUT_ERROR)
    return value


def _identity(calendar_id, event_id, start_at, slot, recipient_key):
    payload = ["genius-agenda-reminder-v1", calendar_id, event_id,
               start_at.isoformat(), slot, recipient_key]
    return sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class ReminderStore:
    """Persistência explícita; construir/importar não cria diretório ou banco."""

    def __init__(self, path=DEFAULT_REMINDER_PATH, clock=None):
        try:
            self.path = Path(path)
            if "\x00" in str(self.path):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError(_INPUT_ERROR) from None
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._initialized = False
        self._lock = Lock()

    def _now(self):
        try:
            return _instant(self._clock())
        except Exception:
            raise ReminderStoreError() from None

    @contextmanager
    def _connection(self, *, create=False):
        try:
            if create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=1, isolation_level=None)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=1000")
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
        except (sqlite3.Error, OSError, OverflowError):
            raise ReminderStoreError() from None

    @staticmethod
    def _config(connection):
        row = connection.execute("SELECT * FROM reminder_config").fetchone()
        if row is None:
            return None
        if row["singleton"] != 1:
            raise ValueError()
        _identifier(row["calendar_id"])
        _hash(row["recipient_key"])
        offsets = _offsets({slot: row[slot] for slot in _SLOTS})
        if type(row["enabled"]) is not int or row["enabled"] not in (0, 1):
            raise ValueError()
        if type(row["permission_granted"]) is not int or row["permission_granted"] not in (0, 1):
            raise ValueError()
        return ReminderConfig(row["calendar_id"], row["recipient_key"], offsets,
                              bool(row["enabled"]), bool(row["permission_granted"]), _revision(row["revision"]))

    @staticmethod
    def _record(row):
        if row is None:
            return None
        values = dict(row)
        _hash(values["key"])
        _identifier(values["calendar_id"])
        _identifier(values["event_id"])
        _hash(values["recipient_key"])
        for field in ("start_at", "due_at", "deadline_at", "created_at", "updated_at"):
            values[field] = _stamp(values[field])
        if values["slot"] not in _SLOTS:
            raise ValueError()
        minutes = values["offset_minutes"]
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise ValueError()
        if (values["due_at"] != values["start_at"] - timedelta(minutes=minutes)
                or values["deadline_at"] != min(values["due_at"] + timedelta(seconds=120), values["start_at"])
                or values["updated_at"] < values["created_at"]
                or _title(values["title"]) != values["title"]):
            raise ValueError()
        _zone(values["calendar_timezone"])
        _revision(values["config_revision"])
        state, reason = values["state"], values["reason"]
        if state not in _STATES or reason not in _STATE_REASONS[state]:
            raise ValueError()
        if state in {"reserved", "sending", "accepted", "uncertain"}:
            _uuid(values["attempt_id"])
        elif values["attempt_id"] is not None:
            raise ValueError()
        if state == "accepted":
            _hash(values["message_id_fingerprint"])
        elif values["message_id_fingerprint"] is not None:
            raise ValueError()
        if values["key"] != _identity(values["calendar_id"], values["event_id"],
                values["start_at"], values["slot"], values["recipient_key"]):
            raise ValueError()
        return ReminderRecord(**values)

    @staticmethod
    def _observation(row):
        _hash(row["event_key"])
        _hash(row["recipient_key"])
        received_at = _stamp(row["received_at"])
        message_at = None if row["message_at"] is None else _stamp(row["message_at"])
        valid = row["valid"]
        if (type(valid) is not int or valid not in (0, 1) or row["reason"] not in _OBS_REASONS
                or row["provenance"] not in _PROVENANCES):
            raise ValueError()
        if valid:
            if (row["reason"] != "valid" or row["provenance"] != "sandbox_callback_capacity"
                    or message_at is None or message_at.timestamp() <= 0 or message_at > received_at):
                raise ValueError()
        elif message_at is not None or row["reason"] == "valid":
            raise ValueError()
        return ChannelObservation(row["event_key"], row["recipient_key"], message_at,
                                  received_at, bool(valid), row["reason"], row["provenance"])

    @classmethod
    def _check(cls, connection):
        """Valida também no uso, pois outro processo pode ter alterado o arquivo."""
        try:
            objects = connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            expected = {(name, "index" if sql.startswith("CREATE INDEX") else "table", sql)
                        for name, sql in _SCHEMA.items()}
            if {(row["name"], row["type"], row["sql"]) for row in objects} != expected:
                raise ValueError()
            for table, signature in _COLUMN_SIGNATURES.items():
                columns = tuple((row[1], row[2], row[3], row[5])
                                for row in connection.execute("PRAGMA table_info(" + table + ")"))
                if columns != signature:
                    raise ValueError()
                indexes = set()
                for row in connection.execute("PRAGMA index_list(" + table + ")"):
                    names = tuple(item[2] for item in connection.execute("PRAGMA index_info(" + row[1] + ")"))
                    indexes.add((row[1], row[2], row[3], row[4], names))
                if indexes != _INDEX_SIGNATURES[table]:
                    raise ValueError()
                foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_list(" + table + ")")]
                expected_keys = [(0, 0, "reminders", "reminder_key", "key", "NO ACTION", "NO ACTION", "NONE")] if table == "reminder_history" else []
                if foreign_keys != expected_keys:
                    raise ValueError()
            if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError()
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise ValueError()
            metadata = [tuple(row) for row in connection.execute("SELECT name,value FROM reminder_metadata ORDER BY name")]
            if metadata != [("schema_version", SCHEMA_VERSION), ("store_kind", "genius_reminders")]:
                raise ValueError()
            if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
                raise ValueError()
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError()
            config = cls._config(connection)
            rows = connection.execute("SELECT " + _RECORD_COLUMNS + " FROM reminders").fetchall()
            for row in rows:
                record = cls._record(row)
                if config is None or record.config_revision > config.revision:
                    raise ValueError()
                if record.state in {"pending", "blocked", "reserved"}:
                    if (not config.enabled or not config.permission_granted
                            or record.config_revision != config.revision
                            or record.calendar_id != config.calendar_id
                            or record.recipient_key != config.recipient_key
                            or dict(config.offsets)[record.slot] != record.offset_minutes):
                        raise ValueError()
                history = connection.execute(
                    "SELECT sequence,from_state,to_state,reason,attempt_id,at FROM reminder_history WHERE reminder_key=? ORDER BY sequence",
                    (record.key,)).fetchall()
                previous, previous_at, previous_attempt = None, record.created_at, None
                if not history:
                    raise ValueError()
                for index, item in enumerate(history):
                    at = _stamp(item["at"])
                    target = item["to_state"]
                    if (type(item["sequence"]) is not int or item["sequence"] < 1
                            or item["from_state"] != previous or target not in _STATES
                            or previous not in _PREVIOUS_STATES[target]
                            or item["reason"] not in _STATE_REASONS[target] or at < previous_at
                            or index == 0 and at != record.created_at):
                        raise ValueError()
                    if index == 0 and (target not in {"pending", "expired"}
                            or item["reason"] != ("planned" if target == "pending" else "reminder_expired")):
                        raise ValueError()
                    if item["attempt_id"] is not None:
                        _uuid(item["attempt_id"])
                    if target in {"reserved", "sending", "accepted", "uncertain"}:
                        _uuid(item["attempt_id"])
                    elif item["attempt_id"] is not None:
                        raise ValueError()
                    if target in _POTENTIAL_STATES and item["attempt_id"] != previous_attempt:
                        raise ValueError()
                    if previous == "reserved" and target == "pending" and item["reason"] != "released":
                        raise ValueError()
                    if item["reason"] == "released" and previous != "reserved":
                        raise ValueError()
                    previous, previous_at, previous_attempt = target, at, item["attempt_id"]
                if (previous != record.state or previous_at != record.updated_at
                        or previous_attempt != record.attempt_id or history[-1]["reason"] != record.reason):
                    raise ValueError()
            for row in connection.execute("SELECT * FROM channel_observations"):
                cls._observation(row)
        except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
            raise ReminderStoreError() from None

    def initialize(self):
        with self._lock:
            with self._connection(create=not self._initialized) as connection:
                objects = connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
                if not objects and not self._initialized:
                    if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
                        raise ReminderStoreError()
                    for sql in _SCHEMA.values():
                        connection.execute(sql)
                    connection.executemany("INSERT INTO reminder_metadata(name,value) VALUES(?,?)",
                        [("schema_version", SCHEMA_VERSION), ("store_kind", "genius_reminders")])
                    connection.execute("PRAGMA user_version=1")
                self._check(connection)
            self._initialized = True

    @contextmanager
    def _operation(self):
        if not self._initialized:
            self.initialize()
        with self._connection() as connection:
            self._check(connection)
            yield connection

    @staticmethod
    def _get(connection, key):
        return ReminderStore._record(connection.execute(
            "SELECT " + _RECORD_COLUMNS + " FROM reminders WHERE key=?", (key,)).fetchone())

    @staticmethod
    def _history(connection, key, before, after, reason, attempt_id, now):
        connection.execute("INSERT INTO reminder_history(reminder_key,from_state,to_state,reason,attempt_id,at) VALUES(?,?,?,?,?,?)",
                           (key, before, after, reason, attempt_id, now.isoformat()))

    def _move(self, connection, record, state, reason, now, *, attempt_id=None, fingerprint=None, changes=None):
        if now < record.updated_at:
            raise ReminderStoreError()
        fields = {"state": state, "reason": reason, "attempt_id": attempt_id,
                  "message_id_fingerprint": fingerprint, "updated_at": now.isoformat()}
        fields.update(changes or {})
        connection.execute("UPDATE reminders SET " + ",".join(name + "=?" for name in fields) + " WHERE key=?",
                           (*fields.values(), record.key))
        self._history(connection, record.key, record.state, state, reason, attempt_id, now)
        return self._get(connection, record.key)

    @staticmethod
    def _current(config, revision):
        if config is None or config.revision != revision:
            raise ReminderStoreError()
        return config

    def config(self):
        with self._operation() as connection:
            return self._config(connection)

    def configure(self, calendar_id, recipient_key, *, offsets=None, enabled=False,
                  permission_granted=False, expected_revision=None):
        _identifier(calendar_id)
        _hash(recipient_key)
        offsets = _offsets(offsets)
        if type(enabled) is not bool or type(permission_granted) is not bool:
            raise ValueError(_INPUT_ERROR)
        if expected_revision is not None:
            _revision(expected_revision)
        with self._operation() as connection:
            old = self._config(connection)
            if (old is None and expected_revision is not None
                    or old is not None and old.revision != expected_revision):
                raise ReminderStoreError()
            values = (calendar_id, recipient_key, offsets, enabled, permission_granted)
            if old is not None and values == (old.calendar_id, old.recipient_key, old.offsets, old.enabled, old.permission_granted):
                return old
            revision = 1 if old is None else old.revision + 1
            connection.execute("INSERT INTO reminder_config VALUES(1,?,?,?,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET calendar_id=excluded.calendar_id,recipient_key=excluded.recipient_key,day_before=excluded.day_before,preparation=excluded.preparation,imminent=excluded.imminent,enabled=excluded.enabled,permission_granted=excluded.permission_granted,revision=excluded.revision",
                (calendar_id, recipient_key, *(minutes for _, minutes in offsets), int(enabled), int(permission_granted), revision))
            now = self._now()
            for row in connection.execute("SELECT " + _RECORD_COLUMNS + " FROM reminders WHERE state IN ('pending','blocked','reserved')").fetchall():
                record = self._record(row)
                minutes = dict(offsets)[record.slot]
                if record.calendar_id != calendar_id or record.recipient_key != recipient_key:
                    state, reason, changes = "cancelled", "binding_changed", {}
                elif not enabled or not permission_granted:
                    state, reason, changes = "cancelled", "configuration_disabled", {}
                elif minutes is None:
                    state, reason, changes = "cancelled", "slot_disabled", {}
                elif record.state == "reserved":
                    state, reason, changes = "cancelled", "configuration_changed", {}
                else:
                    due = record.start_at - timedelta(minutes=minutes)
                    deadline = min(due + timedelta(seconds=120), record.start_at)
                    state = "pending" if now < deadline else "expired"
                    reason = "configuration_changed" if state == "pending" else "reminder_expired"
                    changes = {"offset_minutes": minutes, "due_at": due.isoformat(),
                               "deadline_at": deadline.isoformat(), "config_revision": revision}
                self._move(connection, record, state, reason, now, changes=changes)
            return self._config(connection)

    def plan(self, event_id, start_at, slot, *, title, calendar_timezone, config_revision):
        return self.plan_batch((ReminderPlan(event_id, start_at, slot, title, calendar_timezone),),
                               config_revision=config_revision)[0]

    @staticmethod
    def _normalize_plans(plans):
        if type(plans) is not tuple or len(plans) > 6000:
            raise ValueError(_INPUT_ERROR)
        normalized = {}
        for plan in plans:
            if type(plan) is not ReminderPlan:
                raise ValueError(_INPUT_ERROR)
            _identifier(plan.event_id)
            start_at = _instant(plan.start_at)
            if plan.slot not in _SLOTS:
                raise ValueError(_INPUT_ERROR)
            item = ReminderPlan(plan.event_id, start_at, plan.slot,
                                _title(plan.title), _zone(plan.calendar_timezone))
            identity = (item.event_id, item.start_at, item.slot)
            if identity in normalized and normalized[identity] != item:
                raise ValueError(_INPUT_ERROR)
            normalized[identity] = item
        return tuple(normalized.values())

    @staticmethod
    def _prepare_plans(config, plans):
        prepared = []
        for plan in plans:
            minutes = dict(config.offsets)[plan.slot]
            if minutes is None:
                raise ReminderStoreError()
            try:
                due = plan.start_at - timedelta(minutes=minutes)
                deadline = min(due + timedelta(seconds=120), plan.start_at)
            except OverflowError:
                raise ValueError(_INPUT_ERROR) from None
            key = _identity(config.calendar_id, plan.event_id, plan.start_at,
                            plan.slot, config.recipient_key)
            prepared.append((plan, minutes, due, deadline, key))
        return tuple(prepared)

    def plan_batch(self, plans, *, config_revision):
        """Valida o lote inteiro e aplica todos os planos em uma única transação.

        O CAS da revisão também protege lotes vazios. Não reconcilia eventos que
        ficaram ausentes nem cancela identidades de outro início.
        """
        normalized = self._normalize_plans(plans)
        _revision(config_revision)
        with self._operation() as connection:
            config = self._current(self._config(connection), config_revision)
            if not config.enabled or not config.permission_granted:
                raise ReminderStoreError()
            prepared = self._prepare_plans(config, normalized)
            now = self._now()
            return tuple(self._plan(connection, config, *item, now=now) for item in prepared)

    @classmethod
    def _sync_snapshot(cls, connection):
        rows = connection.execute("SELECT " + _RECORD_COLUMNS +
            " FROM reminders WHERE state IN ('pending','blocked','reserved') ORDER BY key LIMIT 6001").fetchall()
        if len(rows) > 6000:
            raise ReminderStoreError()
        records = tuple(cls._record(row) for row in rows)
        if len({record.event_id for record in records}) > 200:
            raise ReminderStoreError()
        sequence = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM reminder_history").fetchone()[0]
        return ReminderSyncSnapshot(cls._config(connection), records, sequence)

    def sync_snapshot(self):
        """Captura perfil, escopo ativo completo e watermark na mesma transação."""
        with self._operation() as connection:
            return self._sync_snapshot(connection)

    def apply_sync(self, snapshot, plans, cancellations):
        """Confere a fotografia completa e reconcilia em um único BEGIN IMMEDIATE.

        O watermark global detecta inclusive transições ABA no mesmo instante.
        Nenhum evento alheio ao escopo capturado pode receber um novo plano.
        """
        if (type(snapshot) is not ReminderSyncSnapshot
                or type(snapshot.records) is not tuple
                or type(snapshot.history_sequence) is not int or snapshot.history_sequence < 0
                or type(cancellations) is not tuple or len(cancellations) > 6000):
            raise ValueError(_INPUT_ERROR)
        normalized = self._normalize_plans(plans)
        old = {record.key: record for record in snapshot.records if type(record) is ReminderRecord}
        if len(old) != len(snapshot.records):
            raise ValueError(_INPUT_ERROR)
        event_ids = {record.event_id for record in old.values()}
        if any(plan.event_id not in event_ids for plan in normalized):
            raise ValueError(_INPUT_ERROR)
        cancelled = {}
        for item in cancellations:
            if (type(item) is not ReminderSyncCancellation or item.key not in old
                    or item.reason not in {"event_cancelled", "event_rescheduled", "event_changed"}
                    or item.key in cancelled):
                raise ValueError(_INPUT_ERROR)
            cancelled[item.key] = item.reason
        with self._operation() as connection:
            if self._sync_snapshot(connection) != snapshot:
                raise ReminderStoreError()
            config = snapshot.config
            if config is None or not config.enabled or not config.permission_granted:
                raise ReminderStoreError()
            prepared = self._prepare_plans(config, normalized)
            now = self._now()
            if any(now < record.updated_at for record in old.values()):
                raise ReminderStoreError()
            # Comparar o título já normalizado evita revogar reservas quando o
            # Google só mudou espaços, controles ou a representação Unicode.
            before_records = dict(old)
            for plan, _, _, _, key in prepared:
                record = self._get(connection, key)
                if record is not None:
                    before_records[key] = record
                if (record is not None and record.state == "reserved"
                        and (record.title != plan.title or record.calendar_timezone != plan.calendar_timezone)):
                    cancelled.setdefault(key, "event_changed")
            # Toda validação de lote terminou antes da primeira escrita.
            for key, reason in cancelled.items():
                self._move(connection, old[key], "cancelled", reason, now)
            replanned = 0
            result = dict(old)
            for item in prepared:
                before = self._get(connection, item[-1])
                after = self._plan(connection, config, *item, now=now)
                replanned += after != before
                result[after.key] = after
            for key in cancelled:
                result[key] = self._get(connection, key)
            unchanged = sum(record == before_records.get(key) for key, record in result.items())
            return ReminderSyncResult(tuple(result[key] for key in sorted(result)),
                                      len(cancelled), replanned, unchanged)

    def _plan(self, connection, config, plan, minutes, due, deadline, key, *, now):
        record = self._get(connection, key)
        if record is not None:
            if record.state in {"reserved", "sending", "accepted", "uncertain"}:
                return record
            if connection.execute("SELECT 1 FROM reminder_history WHERE reminder_key=? AND to_state IN ('sending','accepted','uncertain') LIMIT 1", (key,)).fetchone():
                return record
            state = "pending" if now < deadline else "expired"
            changes = {"offset_minutes": minutes, "due_at": due.isoformat(),
                "deadline_at": deadline.isoformat(), "title": plan.title,
                "calendar_timezone": plan.calendar_timezone, "config_revision": config.revision}
            if record.state == state and all(getattr(record, name) == (datetime.fromisoformat(value) if name in {"due_at", "deadline_at"} else value) for name, value in changes.items()):
                return record
            return self._move(connection, record, state,
                "replanned" if state == "pending" else "reminder_expired", now, changes=changes)
        state = "pending" if now < deadline else "expired"
        reason = "planned" if state == "pending" else "reminder_expired"
        connection.execute("INSERT INTO reminders(" + _RECORD_COLUMNS + ") VALUES(" + ",".join("?" for _ in range(18)) + ")",
            (key, config.calendar_id, plan.event_id, config.recipient_key, plan.start_at.isoformat(), due.isoformat(),
             deadline.isoformat(), plan.slot, minutes, plan.title, plan.calendar_timezone, config.revision,
             state, reason, None, None, now.isoformat(), now.isoformat()))
        self._history(connection, key, None, state, reason, None, now)
        return self._get(connection, key)

    def get(self, key):
        _hash(key)
        with self._operation() as connection:
            return self._get(connection, key)

    def list_reminders(self, *, states=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError(_INPUT_ERROR)
        if states is not None:
            if (not isinstance(states, (tuple, list, set, frozenset))
                    or any(not isinstance(state, str) or state not in _STATES for state in states)):
                raise ValueError(_INPUT_ERROR)
            states = tuple(sorted(set(states)))
        with self._operation() as connection:
            sql, parameters = "SELECT " + _RECORD_COLUMNS + " FROM reminders", []
            if states is not None:
                if not states:
                    return ()
                sql += " WHERE state IN (" + ",".join("?" for _ in states) + ")"
                parameters.extend(states)
            sql += " ORDER BY due_at,key LIMIT ?"
            return tuple(self._record(row) for row in connection.execute(sql, (*parameters, limit)))

    def history(self, key):
        _hash(key)
        with self._operation() as connection:
            return tuple(ReminderHistory(row[0], row[1], row[2], row[3], _stamp(row[4])) for row in connection.execute(
                "SELECT from_state,to_state,reason,attempt_id,at FROM reminder_history WHERE reminder_key=? ORDER BY sequence", (key,)))

    def mark_blocked(self, key, reason):
        _hash(key)
        if not isinstance(reason, str) or reason not in _BLOCK_REASONS:
            raise ValueError(_INPUT_ERROR)
        with self._operation() as connection:
            record = self._get(connection, key)
            if record is None or record.state not in {"pending", "blocked"}:
                raise ReminderStoreError()
            now = self._now()
            if now >= record.deadline_at:
                return self._move(connection, record, "expired", "reminder_expired", now)
            if record.state == "blocked" and record.reason == reason:
                return record
            return self._move(connection, record, "blocked", reason, now)

    def cancel(self, key, reason="event_cancelled"):
        _hash(key)
        if not isinstance(reason, str) or reason not in _CANCEL_REASONS:
            raise ValueError(_INPUT_ERROR)
        with self._operation() as connection:
            record = self._get(connection, key)
            if record is None or record.state not in {"pending", "blocked", "reserved"}:
                raise ReminderStoreError()
            return self._move(connection, record, "cancelled", reason, self._now())

    def expire_due(self):
        with self._operation() as connection:
            now = self._now()
            records = [self._record(row) for row in connection.execute(
                "SELECT " + _RECORD_COLUMNS + " FROM reminders WHERE state IN ('pending','blocked')")]
            expired = [record for record in records if now >= record.deadline_at]
            for record in expired:
                self._move(connection, record, "expired", "reminder_expired", now)
            return len(expired)

    @classmethod
    def _recovery_snapshot(cls, connection, *, captured_at, limit):
        rows = connection.execute("SELECT " + _RECORD_COLUMNS +
            " FROM reminders WHERE state IN ('reserved','sending') ORDER BY key LIMIT ?",
            (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise ReminderStoreError()
        records = tuple(cls._record(row) for row in rows)
        protected_count = connection.execute(
            "SELECT COUNT(*) FROM reminders WHERE state IN ('accepted','uncertain')").fetchone()[0]
        sequence = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM reminder_history").fetchone()[0]
        return ReminderRecoverySnapshot(cls._config(connection), records, protected_count,
                                        sequence, captured_at, limit)

    def recovery_snapshot(self, limit=200):
        """Fotografia completa limitada; o serviço mantém o lock de SO positivo."""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError(_INPUT_ERROR)
        with self._operation() as connection:
            now = self._now()
            snapshot = self._recovery_snapshot(connection, captured_at=now, limit=limit)
            if any(record.updated_at > now for record in snapshot.records):
                raise ReminderStoreError()
            return snapshot

    def apply_recovery(self, snapshot, *, not_before=None):
        """Reconcilia somente após posse exclusiva do lock de SO pelo chamador.

        Não deduz abandono pela idade da reserva. O serviço conserva o mesmo
        reminder_tick_lock da entrega durante snapshot e commit. CAS do perfil,
        escopo completo e watermark global recusa inserções/ABA; qualquer erro
        recusa o lote inteiro. Este método nunca inicia transporte.
        """
        if (type(snapshot) is not ReminderRecoverySnapshot
                or type(snapshot.records) is not tuple
                or any(type(record) is not ReminderRecord or record.state not in {"reserved", "sending"}
                       for record in snapshot.records)
                or len({record.key for record in snapshot.records}) != len(snapshot.records)
                or type(snapshot.limit) is not int or not 1 <= snapshot.limit <= 200
                or len(snapshot.records) > snapshot.limit
                or type(snapshot.history_sequence) is not int or snapshot.history_sequence < 0
                or type(snapshot.protected_count) is not int or snapshot.protected_count < 0):
            raise ValueError(_INPUT_ERROR)
        captured_at = _instant(snapshot.captured_at)
        not_before = captured_at if not_before is None else _instant(not_before)
        with self._operation() as connection:
            if self._recovery_snapshot(connection, captured_at=captured_at, limit=snapshot.limit) != snapshot:
                raise ReminderStoreError()
            config = snapshot.config
            if config is None or not config.enabled or not config.permission_granted:
                raise ReminderStoreError()
            now = self._now()
            if now < captured_at or now < not_before or any(now < record.updated_at for record in snapshot.records):
                raise ReminderStoreError()
            transitions = []
            recovered, expired, uncertain = 0, 0, 0
            for record in snapshot.records:
                if record.state == "sending":
                    transitions.append((record, "uncertain", "uncertain", record.attempt_id))
                    uncertain += 1
                    continue
                if (not self._binding(record, config, config.revision)
                        or connection.execute("SELECT 1 FROM reminder_history WHERE reminder_key=?"
                            " AND to_state IN ('sending','accepted','uncertain') LIMIT 1", (record.key,)).fetchone()):
                    raise ReminderStoreError()
                if now < record.due_at:
                    raise ReminderStoreError()
                if now >= record.deadline_at:
                    transitions.append((record, "expired", "reminder_expired", None))
                    expired += 1
                else:
                    transitions.append((record, "pending", "released", None))
                    recovered += 1
            # Validar todo o lote antes da primeira escrita preserva a atomicidade.
            for record, state, reason, attempt in transitions:
                self._move(connection, record, state, reason, now, attempt_id=attempt)
            return ReminderRecoveryResult(recovered, expired, uncertain, snapshot.protected_count)

    def delivery_snapshot(self, limit=200):
        """Captura somente candidatos vencidos, sem truncar um lote excedente.

        O limite também inclui os candidatos que serão expirados. Todas as
        expirações compartilham a transação da fotografia; excesso ou relógio
        regressivo não deixa uma expiração parcial. Reservas e estados de envio
        potencial nunca são recuperados por este método.
        """
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError(_INPUT_ERROR)
        with self._operation() as connection:
            config = self._config(connection)
            if config is None or not config.enabled or not config.permission_granted:
                return ReminderDeliverySnapshot(config, (), 0)
            now = self._now()
            records = tuple(self._record(row) for row in connection.execute(
                "SELECT " + _RECORD_COLUMNS +
                " FROM reminders WHERE state IN ('pending','blocked') AND due_at<=?"
                " ORDER BY due_at,key LIMIT ?", (now.isoformat(), limit + 1)))
            if len(records) > limit or any(record.updated_at > now for record in records):
                raise ReminderStoreError()
            ready, expired_count = [], 0
            for record in records:
                if now >= record.deadline_at:
                    self._move(connection, record, "expired", "reminder_expired", now)
                    expired_count += 1
                else:
                    ready.append(record)
            return ReminderDeliverySnapshot(config, tuple(ready), expired_count)

    def finish_delivery_candidate(self, expected_record, reason):
        """Bloqueia/cancela somente a fotografia ainda idêntica, antes de reservar."""
        if (type(expected_record) is not ReminderRecord
                or expected_record.state not in {"pending", "blocked"}
                or not isinstance(reason, str) or reason not in _BLOCK_REASONS | _CANCEL_REASONS):
            raise ValueError(_INPUT_ERROR)
        _hash(expected_record.key)
        with self._operation() as connection:
            record = self._get(connection, expected_record.key)
            if record != expected_record:
                raise ReminderStoreError()
            now = self._now()
            if now >= record.deadline_at:
                return self._move(connection, record, "expired", "reminder_expired", now)
            state = "blocked" if reason in _BLOCK_REASONS else "cancelled"
            if record.state == state and record.reason == reason:
                if now < record.updated_at:
                    raise ReminderStoreError()
                return record
            return self._move(connection, record, state, reason, now)

    def cancel_reserved(self, claim, reason):
        """Revoga apenas a reserva própria, sem cancelar outra tentativa por chave."""
        self._claim(claim)
        if not isinstance(reason, str) or reason not in _CANCEL_REASONS:
            raise ValueError(_INPUT_ERROR)
        with self._operation() as connection:
            record = self._claimed(connection, claim, "reserved")
            now = self._now()
            if now >= record.deadline_at:
                return self._move(connection, record, "expired", "reminder_expired", now)
            return self._move(connection, record, "cancelled", reason, now)

    @staticmethod
    def _last_inbound(connection, recipient_key):
        row = connection.execute("SELECT * FROM channel_observations WHERE recipient_key=? AND valid=1"
            " ORDER BY message_at DESC,event_key DESC LIMIT 1", (recipient_key,)).fetchone()
        return None if row is None else ReminderStore._observation(row)

    @staticmethod
    def _delivery_channel_reason(observation, recipient_key, now):
        if (observation is None or type(observation) is not ChannelObservation
                or not observation.valid or observation.reason != "valid"
                or observation.provenance != "sandbox_callback_capacity"
                or observation.recipient_key != recipient_key or observation.message_at is None
                or observation.message_at > observation.received_at
                or observation.received_at > now or observation.message_at > now):
            return "channel_window_unknown"
        closing = observation.message_at + timedelta(hours=24)
        if now >= closing:
            return "channel_window_closed"
        if now >= closing - timedelta(seconds=60):
            return "channel_window_margin"
        return None

    def begin_delivery(self, claim, *, config_revision, title, calendar_timezone, not_before=None):
        """CAS final de perfil, reserva, relógio e canal antes de qualquer transporte.

        A última leitura externa fornece somente título/fuso já validados pelo
        serviço. O instante e a identidade permanecem os da reserva exata. Uma
        janela fechada libera somente esse claim e o bloqueia na mesma transação;
        nenhum estado que já pode ter iniciado transporte é liberado aqui.
        """
        self._claim(claim)
        _revision(config_revision)
        title, calendar_timezone = _title(title), _zone(calendar_timezone)
        not_before = None if not_before is None else _instant(not_before)
        with self._operation() as connection:
            record = self._claimed(connection, claim, "reserved")
            if record != claim.reminder:
                raise ReminderStoreError()
            now = self._now()
            if now < record.updated_at or not_before is not None and now < not_before:
                raise ReminderStoreError()
            if now >= record.deadline_at:
                return self._move(connection, record, "expired", "reminder_expired", now)
            if now < record.due_at:
                raise ReminderStoreError()
            if not self._binding(record, self._config(connection), config_revision):
                return self._move(connection, record, "cancelled", "configuration_changed", now)
            reason = self._delivery_channel_reason(
                self._last_inbound(connection, record.recipient_key), record.recipient_key, now)
            if reason is not None:
                released = self._move(connection, record, "pending", "released", now)
                return self._move(connection, released, "blocked", reason, now)
            return self._move(connection, record, "sending", "sending", now,
                attempt_id=claim.attempt_id, changes={"title": title, "calendar_timezone": calendar_timezone})

    @staticmethod
    def _binding(record, config, revision):
        return (config is not None and config.revision == revision == record.config_revision
                and config.enabled and config.permission_granted
                and config.calendar_id == record.calendar_id and config.recipient_key == record.recipient_key
                and dict(config.offsets)[record.slot] == record.offset_minutes)

    def reserve(self, key, *, config_revision):
        """Só ``new`` permite avançar o ledger; não é autorização de transporte.

        O chamador precisa revalidar Google/canal antes de reservar inclusive um
        aviso bloqueado. ``blocked`` também cobre reserva/envio/incerteza prévios.
        """
        _hash(key)
        _revision(config_revision)
        with self._operation() as connection:
            record = self._get(connection, key)
            if record is None:
                raise ReminderStoreError()
            config = self._current(self._config(connection), config_revision)
            now = self._now()
            if record.state == "accepted":
                return ReminderClaim("accepted", record, None)
            if record.state == "expired":
                return ReminderClaim("expired", record, None)
            if record.state not in {"pending", "blocked"} or not self._binding(record, config, config_revision):
                return ReminderClaim("blocked", record, None)
            if now >= record.deadline_at:
                record = self._move(connection, record, "expired", "reminder_expired", now)
                return ReminderClaim("expired", record, None)
            if now < record.due_at:
                return ReminderClaim("pending", record, None)
            attempt_id = str(uuid4())
            record = self._move(connection, record, "reserved", "reserved", now, attempt_id=attempt_id)
            return ReminderClaim("new", record, attempt_id)

    @staticmethod
    def _claim(claim):
        if type(claim) is not ReminderClaim or claim.status != "new" or type(claim.reminder) is not ReminderRecord:
            raise ValueError(_INPUT_ERROR)
        _hash(claim.reminder.key)
        _uuid(claim.attempt_id)
        if claim.reminder.attempt_id != claim.attempt_id or claim.reminder.state != "reserved":
            raise ValueError(_INPUT_ERROR)

    def _claimed(self, connection, claim, state):
        record = self._get(connection, claim.reminder.key)
        if record is None or record.state != state or record.attempt_id != claim.attempt_id:
            raise ReminderStoreError()
        return record

    def mark_sending(self, claim):
        self._claim(claim)
        with self._operation() as connection:
            record = self._claimed(connection, claim, "reserved")
            now = self._now()
            if now >= record.deadline_at:
                return self._move(connection, record, "expired", "reminder_expired", now)
            if now < record.due_at:
                raise ReminderStoreError()
            if not self._binding(record, self._config(connection), record.config_revision):
                return self._move(connection, record, "cancelled", "configuration_changed", now)
            return self._move(connection, record, "sending", "sending", now, attempt_id=claim.attempt_id)

    def mark_accepted(self, claim, fingerprint):
        self._claim(claim)
        _hash(fingerprint)
        with self._operation() as connection:
            record = self._claimed(connection, claim, "sending")
            return self._move(connection, record, "accepted", "accepted", self._now(),
                              attempt_id=claim.attempt_id, fingerprint=fingerprint)

    def mark_uncertain(self, claim):
        self._claim(claim)
        with self._operation() as connection:
            record = self._claimed(connection, claim, "sending")
            return self._move(connection, record, "uncertain", "uncertain", self._now(), attempt_id=claim.attempt_id)

    def release_reserved(self, claim):
        self._claim(claim)
        with self._operation() as connection:
            record = self._claimed(connection, claim, "reserved")
            now = self._now()
            if now >= record.deadline_at:
                return self._move(connection, record, "expired", "reminder_expired", now)
            return self._move(connection, record, "pending", "released", now)

    def observe_inbound(self, event_key, recipient_key, provider_timestamp, received_at, *,
                        message_type="text", provenance="sandbox_callback_capacity"):
        _hash(event_key)
        _hash(recipient_key)
        received_at = _instant(received_at)
        if not isinstance(provenance, str) or provenance not in _PROVENANCES:
            raise ValueError(_INPUT_ERROR)
        if not isinstance(message_type, str) or not 1 <= len(message_type) <= 64:
            raise ValueError(_INPUT_ERROR)
        now = self._now()
        if received_at > now:
            raise ValueError(_INPUT_ERROR)
        with self._operation() as connection:
            existing = connection.execute("SELECT * FROM channel_observations WHERE event_key=?", (event_key,)).fetchone()
            if existing is not None:
                return self._observation(existing)
            message_at, reason = None, "valid"
            if provenance != "sandbox_callback_capacity":
                reason = "untrusted_provenance"
            elif message_type != "text":
                reason = "unsupported_type"
            elif (not isinstance(provider_timestamp, str) or not 1 <= len(provider_timestamp) <= 20
                    or not provider_timestamp.isascii() or not provider_timestamp.isdigit()):
                reason = "invalid_timestamp"
            else:
                try:
                    seconds = int(provider_timestamp)
                    if seconds <= 0:
                        raise ValueError()
                    parsed = datetime.fromtimestamp(seconds, timezone.utc)
                    if parsed > received_at:
                        reason = "future_timestamp"
                    else:
                        message_at = parsed
                except (ValueError, OverflowError, OSError):
                    reason = "invalid_timestamp"
            connection.execute("INSERT INTO channel_observations VALUES(?,?,?,?,?,?,?)",
                (event_key, recipient_key, None if message_at is None else message_at.isoformat(),
                 received_at.isoformat(), int(reason == "valid"), reason, provenance))
            return self._observation(connection.execute("SELECT * FROM channel_observations WHERE event_key=?", (event_key,)).fetchone())

    def last_inbound(self, recipient_key):
        _hash(recipient_key)
        with self._operation() as connection:
            return self._last_inbound(connection, recipient_key)
