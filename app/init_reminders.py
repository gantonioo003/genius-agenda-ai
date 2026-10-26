"""Inicializa somente a persistência de lembretes; não configura nem dispara avisos."""

import argparse
import json
from pathlib import Path

from app.reminder_store import (
    DEFAULT_REMINDERS_PATH, SCHEMA_VERSION, ReminderStore, ReminderStoreError,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Preparar o banco local dos lembretes, sem disparos.")
    parser.add_argument("--database", type=Path, default=DEFAULT_REMINDERS_PATH,
                        help="Arquivo SQLite privado; padrão: data/reminders.sqlite3")
    args = parser.parse_args(argv)
    try:
        store = ReminderStore(args.database)
        store.initialize()
        configured = store.config() is not None
    except (ReminderStoreError, ValueError):
        print(json.dumps({"status": "error", "message": "Persistência dos lembretes indisponível."},
                         ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ready", "schema_version": SCHEMA_VERSION,
                      "configuration_present": configured, "operation": "initialize_only"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
