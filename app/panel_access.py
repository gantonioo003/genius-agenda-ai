"""Private, local browser pairing. Raw browser session IDs are never persisted."""

from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import argparse
import hashlib
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time


PENDING_SECONDS = 300
IDLE_SECONDS = 1800
ABSOLUTE_SECONDS = 8 * 3600
START_WINDOW_SECONDS = 60
MAX_STARTS = 10
MAX_PENDING = 20
DEFAULT_PATH = Path("data/panel-access.sqlite3")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z", flags=re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}\Z", flags=re.ASCII)
_CODE = re.compile(r"[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}\Z", flags=re.ASCII)
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class PanelStoreError(RuntimeError):
    """A private store could not be read consistently."""


class PanelRateLimitError(PanelStoreError):
    """Too many new browser requests were created recently."""


@dataclass(frozen=True)
class PanelSession:
    id_hash: str = field(repr=False)
    state: str
    created_at: float
    approved_at: float | None
    last_seen_at: float
    absolute_expires_at: float
    pairing_code: str | None = None
    csrf_token: str | None = field(default=None, repr=False)

    @property
    def expires_at(self):
        if self.state == "active":
            return min(self.absolute_expires_at, self.last_seen_at + IDLE_SECONDS)
        return self.absolute_expires_at


def normalize_pairing_code(value):
    if not isinstance(value, str):
        raise ValueError("Informe o código exibido no painel.")
    normalized = value.strip().upper()
    if len(normalized) == 8:
        normalized = normalized[:4] + "-" + normalized[4:]
    if not _CODE.fullmatch(normalized):
        raise ValueError("Informe o código exibido no painel.")
    return normalized


def _session_hash(sid):
    if not isinstance(sid, str) or not _TOKEN.fullmatch(sid):
        return None
    return hashlib.sha256(sid.encode("ascii")).hexdigest()


class PanelAccessStore:
    def __init__(self, path=DEFAULT_PATH, *, clock=None):
        self.path = Path(path)
        self.clock = clock or time.time

    def _now(self):
        value = self.clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise PanelStoreError("Relógio do painel indisponível.")
            value = value.timestamp()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise PanelStoreError("Relógio do painel indisponível.")
        return float(value)

    @contextmanager
    def _connection(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute("CREATE TABLE IF NOT EXISTS panel_sessions (id_hash TEXT PRIMARY KEY NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')), created_at REAL NOT NULL, approved_at REAL, last_seen_at REAL NOT NULL, absolute_expires_at REAL NOT NULL, pairing_code TEXT UNIQUE, csrf_token TEXT)")
                    connection.execute("CREATE TABLE IF NOT EXISTS panel_starts (at REAL NOT NULL)")
                    connection.execute("CREATE INDEX IF NOT EXISTS panel_starts_at ON panel_starts(at)")
                    yield connection
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise
        except (OSError, sqlite3.Error):
            raise PanelStoreError("O acesso local do painel está indisponível.") from None

    @staticmethod
    def _record(row):
        if row is None:
            return None
        try:
            record = PanelSession(**dict(row))
            def valid(condition):
                if not condition:
                    raise ValueError()
            valid(isinstance(record.id_hash, str) and _HASH.fullmatch(record.id_hash))
            valid(record.state in {"pending", "active", "revoked"})
            times = [record.created_at, record.last_seen_at, record.absolute_expires_at]
            valid(all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in times))
            valid(record.created_at <= record.last_seen_at <= record.absolute_expires_at)
            if record.state == "pending":
                valid(record.approved_at is None and record.csrf_token is None)
                valid(isinstance(record.pairing_code, str) and _CODE.fullmatch(record.pairing_code))
                valid(record.absolute_expires_at == record.created_at + PENDING_SECONDS)
            elif record.state == "active":
                valid(record.pairing_code is None)
                valid(isinstance(record.csrf_token, str) and _TOKEN.fullmatch(record.csrf_token))
                valid(isinstance(record.approved_at, (int, float)) and not isinstance(record.approved_at, bool) and math.isfinite(record.approved_at))
                valid(record.created_at <= record.approved_at <= record.last_seen_at)
                valid(record.absolute_expires_at == record.approved_at + ABSOLUTE_SECONDS)
            else:
                valid(record.pairing_code is None and record.csrf_token is None)
            return record
        except (TypeError, ValueError):
            raise PanelStoreError("O estado local do painel é inválido.") from None

    @staticmethod
    def _revoke(connection, digest):
        connection.execute("UPDATE panel_sessions SET state='revoked', pairing_code=NULL, csrf_token=NULL WHERE id_hash=?", (digest,))

    def _current(self, connection, digest, now, *, touch):
        record = self._record(connection.execute("SELECT * FROM panel_sessions WHERE id_hash=?", (digest,)).fetchone())
        if record is None or record.state == "revoked":
            return None
        if now < record.last_seen_at or now >= record.expires_at:
            self._revoke(connection, digest)
            return None
        if touch and record.state == "active":
            connection.execute("UPDATE panel_sessions SET last_seen_at=? WHERE id_hash=?", (now, digest))
            record = self._record(connection.execute("SELECT * FROM panel_sessions WHERE id_hash=?", (digest,)).fetchone())
        return record

    def begin(self, sid=None):
        now = self._now()
        digest = _session_hash(sid)
        with self._connection() as connection:
            if digest:
                existing = self._current(connection, digest, now, touch=False)
                if existing is not None:
                    return sid, existing
            connection.execute("DELETE FROM panel_starts WHERE at<=?", (now - START_WINDOW_SECONDS,))
            connection.execute("DELETE FROM panel_sessions WHERE absolute_expires_at<=?", (now,))
            count = connection.execute("SELECT COUNT(*) FROM panel_starts").fetchone()[0]
            pending = connection.execute("SELECT COUNT(*) FROM panel_sessions WHERE state='pending'").fetchone()[0]
            if count >= MAX_STARTS or pending >= MAX_PENDING:
                raise PanelRateLimitError("Há muitas solicitações de acesso. Aguarde um minuto.")
            sid = secrets.token_urlsafe(32)
            digest = _session_hash(sid)
            code = None
            for _ in range(8):
                chars = "".join(secrets.choice(_ALPHABET) for _ in range(8))
                candidate = chars[:4] + "-" + chars[4:]
                if connection.execute("SELECT 1 FROM panel_sessions WHERE pairing_code=?", (candidate,)).fetchone() is None:
                    code = candidate
                    break
            if code is None:
                raise PanelStoreError("Não foi possível criar o acesso local.")
            connection.execute("INSERT INTO panel_sessions VALUES (?, 'pending', ?, NULL, ?, ?, ?, NULL)", (digest, now, now, now + PENDING_SECONDS, code))
            connection.execute("INSERT INTO panel_starts(at) VALUES (?)", (now,))
            return sid, self._record(connection.execute("SELECT * FROM panel_sessions WHERE id_hash=?", (digest,)).fetchone())

    def get(self, sid, *, touch=True):
        digest = _session_hash(sid)
        if digest is None:
            return None
        now = self._now()
        with self._connection() as connection:
            return self._current(connection, digest, now, touch=touch)

    def authorize(self, code):
        try:
            code = normalize_pairing_code(code)
        except ValueError:
            return False
        now = self._now()
        with self._connection() as connection:
            record = self._record(connection.execute("SELECT * FROM panel_sessions WHERE pairing_code=?", (code,)).fetchone())
            if record is None or record.state != "pending":
                return False
            if now < record.created_at or now >= record.expires_at:
                self._revoke(connection, record.id_hash)
                return False
            connection.execute("UPDATE panel_sessions SET state='active', approved_at=?, last_seen_at=?, absolute_expires_at=?, pairing_code=NULL, csrf_token=? WHERE id_hash=? AND state='pending'", (now, now, now + ABSOLUTE_SECONDS, secrets.token_urlsafe(32), record.id_hash))
            return True

    def revoke(self, sid):
        digest = _session_hash(sid)
        if digest is None:
            return False
        with self._connection() as connection:
            record = self._record(connection.execute("SELECT * FROM panel_sessions WHERE id_hash=?", (digest,)).fetchone())
            if record is None or record.state == "revoked":
                return False
            self._revoke(connection, digest)
            return True


def configured_panel_store(config):
    from app.settings import PROJECT_ROOT
    path = Path(getattr(config, "panel_database_path", DEFAULT_PATH))
    return PanelAccessStore(path if path.is_absolute() else PROJECT_ROOT / path)


def main():
    parser = argparse.ArgumentParser(description="Autorizar uma aba local do painel Genius.")
    parser.add_argument("--database", type=Path, default=None, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    authorize = commands.add_parser("authorize", help="Autorizar o código mostrado na aba do painel")
    authorize.add_argument("code")
    args = parser.parse_args()
    if args.database is None:
        from app.settings import settings
        store = configured_panel_store(settings)
    else:
        store = PanelAccessStore(args.database)
    try:
        success = store.authorize(args.code)
    except PanelStoreError:
        parser.exit(1, "O acesso local do painel está indisponível.\n")
    if not success:
        parser.exit(1, "Código inexistente, expirado ou já utilizado. Solicite um novo código no painel.\n")
    print("Aba autorizada. Volte ao painel para consultar sua agenda.")


if __name__ == "__main__":
    main()
