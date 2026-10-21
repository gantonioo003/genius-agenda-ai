"""Banco SQLite inicial e verificação manual de persistência."""

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

from app.settings import PROJECT_ROOT, settings


SCHEMA_VERSION = "1"


class Database:
    def __init__(self, path: Path):
        path = Path(path)
        self.path = (path if path.is_absolute() else PROJECT_ROOT / path).resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        # A transação confirma ou desfaz as alterações; closing fecha a conexão.
        with closing(
            sqlite3.connect(self.path, timeout=5, autocommit=False)
        ) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO app_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                ("schema_version", SCHEMA_VERSION, self._now()),
            )
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                ("schema_version",),
            ).fetchone()
            if row["value"] != SCHEMA_VERSION:
                raise RuntimeError(
                    "Versão do banco incompatível. Revise a migração antes de iniciar."
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
