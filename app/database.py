"""Banco SQLite inicial e verificação manual de persistência."""

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import re
from typing import Iterator
from uuid import uuid4

from app.settings import PROJECT_ROOT, settings


SCHEMA_VERSION = "2"


class CreationIdentityError(RuntimeError):
    """A identidade persistida da solicitação não permite esta operação."""


@dataclass(frozen=True)
class CreationRecord:
    calendar_id: str
    request_hash: str
    event_id: str
    fingerprint: str
    state: str


class Database:
    def __init__(self, path: Path):
        path = Path(path)
        self.path = (path if path.is_absolute() else PROJECT_ROOT / path).resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # A transação confirma ou desfaz as alterações; closing fecha a conexão.
        with closing(
            sqlite3.connect(self.path, timeout=5, autocommit=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.calendar_write_lock() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                ("schema_version",),
            ).fetchone()
            if row is not None and row["value"] not in {"1", SCHEMA_VERSION}:
                raise RuntimeError(
                    "Versão do banco incompatível. Revise a migração antes de iniciar."
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS creation_requests (
                    calendar_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'confirmed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (calendar_id, request_hash),
                    UNIQUE (calendar_id, event_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS creation_operations (
                    calendar_id TEXT NOT NULL,
                    operation_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    PRIMARY KEY (calendar_id, operation_hash),
                    FOREIGN KEY (calendar_id, request_hash)
                        REFERENCES creation_requests (calendar_id, request_hash)
                )
                """
            )
            if row is None or row["value"] != SCHEMA_VERSION:
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    ("schema_version", SCHEMA_VERSION, self._now()),
                )

    @contextmanager
    def calendar_write_lock(self) -> Iterator[sqlite3.Connection]:
        """Serializar checagem/gravação entre processos que usam este SQLite.

        Inicializar e atualizar metadados antes de entrar. O corpo não deve
        abrir outra conexão que escreva neste mesmo banco. A conexão fornecida
        permite confirmar a criação na mesma transação, depois da resposta externa.
        """
        with closing(sqlite3.connect(self.path, timeout=5, autocommit=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise CreationIdentityError("Identificador de criação inválido.")
        return value

    @staticmethod
    def _hash(value: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
            raise CreationIdentityError("Identificador de criação inválido.")
        return value.lower()

    @staticmethod
    def _creation_record(row: sqlite3.Row | None) -> CreationRecord | None:
        if row is None:
            return None
        return CreationRecord(
            calendar_id=row["calendar_id"],
            request_hash=row["request_hash"],
            event_id=row["event_id"],
            fingerprint=row["fingerprint"],
            state=row["state"],
        )

    def get_creation(
        self,
        calendar_id: str,
        request_hash: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> CreationRecord | None:
        calendar_id = self._identifier(calendar_id)
        request_hash = self._hash(request_hash)
        if connection is None:
            with self.connection() as owned_connection:
                return self.get_creation(
                    calendar_id, request_hash, connection=owned_connection
                )
        row = connection.execute(
            """
            SELECT calendar_id, request_hash, event_id, fingerprint, state
            FROM creation_requests WHERE calendar_id = ? AND request_hash = ?
            """,
            (calendar_id, request_hash),
        ).fetchone()
        return self._creation_record(row)

    def reserve_creation(
        self,
        calendar_id: str,
        operation_hash: str,
        request_hash: str,
        event_id: str,
        fingerprint: str,
    ) -> CreationRecord:
        """Persistir a identidade antes de qualquer tentativa de criação externa.

        Repetições com outra operação usam o ID e a impressão originais quando
        o conteúdo é o mesmo. Uma operação já vinculada não pode mudar de pedido.
        """
        calendar_id = self._identifier(calendar_id)
        operation_hash = self._hash(operation_hash)
        request_hash = self._hash(request_hash)
        event_id = self._identifier(event_id)
        fingerprint = self._hash(fingerprint)
        try:
            with self.calendar_write_lock() as connection:
                operation = connection.execute(
                    """
                    SELECT request_hash FROM creation_operations
                    WHERE calendar_id = ? AND operation_hash = ?
                    """,
                    (calendar_id, operation_hash),
                ).fetchone()
                if operation is not None and operation["request_hash"] != request_hash:
                    raise CreationIdentityError(
                        "Esta operação já está vinculada a outra solicitação. "
                        "Confira o pedido antes de repetir."
                    )
                record = self.get_creation(
                    calendar_id, request_hash, connection=connection
                )
                if operation is not None and record is None:
                    raise CreationIdentityError(
                        "A operação registrada está sem a identidade de criação. "
                        "Confira o evento antes de repetir."
                    )
                if record is None:
                    now = self._now()
                    connection.execute(
                        """
                        INSERT INTO creation_requests
                            (calendar_id, request_hash, event_id, fingerprint,
                             state, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (calendar_id, request_hash, event_id, fingerprint, now, now),
                    )
                    record = CreationRecord(
                        calendar_id, request_hash, event_id, fingerprint, "pending"
                    )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO creation_operations
                        (calendar_id, operation_hash, request_hash)
                    VALUES (?, ?, ?)
                    """,
                    (calendar_id, operation_hash, request_hash),
                )
            return record
        except sqlite3.IntegrityError:
            raise CreationIdentityError(
                "Não foi possível reservar uma identidade única para esta criação. "
                "Confira a solicitação antes de repetir."
            ) from None

    def confirm_creation(
        self,
        record: CreationRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if not isinstance(record, CreationRecord):
            raise CreationIdentityError("Registro de criação inválido.")
        calendar_id = self._identifier(record.calendar_id)
        request_hash = self._hash(record.request_hash)
        event_id = self._identifier(record.event_id)
        fingerprint = self._hash(record.fingerprint)
        if not isinstance(record.state, str) or record.state not in {"pending", "confirmed"}:
            raise CreationIdentityError("Estado de criação inválido.")
        if connection is None:
            with self.calendar_write_lock() as owned_connection:
                self.confirm_creation(record, connection=owned_connection)
            return
        cursor = connection.execute(
            """
            UPDATE creation_requests SET state = 'confirmed', updated_at = ?
            WHERE calendar_id = ? AND request_hash = ?
                AND event_id = ? AND fingerprint = ?
            """,
            (self._now(), calendar_id, request_hash, event_id, fingerprint),
        )
        if cursor.rowcount != 1:
            raise CreationIdentityError(
                "A identidade da criação não corresponde ao registro reservado. "
                "Confira o evento antes de repetir."
            )

    def set_metadata(self, key: str, value: str) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO app_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, self._now()),
            )

    def get_metadata(self, key: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?", (key,)
            ).fetchone()
            return None if row is None else row["value"]

    def delete_metadata(self, key: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM app_metadata WHERE key = ?", (key,))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


database = Database(settings.database_path)


def verify_persistence(db: Database) -> dict[str, str]:
    db.initialize()
    key = "check:" + uuid4().hex
    expected = "Registro de teste do SQLite"
    try:
        db.set_metadata(key, expected)
        # set_metadata já confirmou a gravação e fechou sua conexão.
        recovered = Database(db.path).get_metadata(key)
        if recovered != expected:
            raise RuntimeError("A informação gravada não foi recuperada corretamente.")
        return {
            "status": "ok",
            "database": str(db.path),
            "recovered_value": recovered,
            "test_record": "removed_after_check",
        }
    finally:
        # Apaga apenas o registro desta verificação, identificado por UUID.
        db.delete_metadata(key)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inicializar e verificar o SQLite.")
    parser.add_argument(
        "--check", action="store_true", help="Gravar e recuperar um registro de teste."
    )
    args = parser.parse_args()
    if args.check:
        print(json.dumps(verify_persistence(database)))
    else:
        database.initialize()
        print(json.dumps({"status": "ok", "database": str(database.path)}))


if __name__ == "__main__":
    main()
