"""Testes de persistência e transações, sempre em banco temporário."""

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from app.database import Database, verify_persistence


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "data" / "test.db")
        self.db.initialize()

    def test_value_survives_new_instance_and_initialization(self):
        self.db.set_metadata("saved_value", "persisted")
        reopened = Database(self.db.path)
        reopened.initialize()
        self.assertEqual(reopened.get_metadata("saved_value"), "persisted")

    def test_failed_transaction_rolls_back_previous_update(self):
        self.db.set_metadata("saved_value", "original")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connection() as connection:
                connection.execute(
                    "UPDATE app_metadata SET value = ? WHERE key = ?",
                    ("must_rollback", "saved_value"),
                )
                connection.execute(
                    "INSERT INTO app_metadata (key, value, updated_at) VALUES (?, ?, ?)",
                    (None, "invalid", "2026-01-01T00:00:00+00:00"),
                )
        self.assertEqual(self.db.get_metadata("saved_value"), "original")

    def test_check_cleans_only_its_record(self):
        self.db.set_metadata("existing_value", "keep")
        result = verify_persistence(self.db)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.db.get_metadata("existing_value"), "keep")
        with self.db.connection() as connection:
            keys = {
                row["key"]
                for row in connection.execute("SELECT key FROM app_metadata").fetchall()
            }
        self.assertEqual(keys, {"schema_version", "existing_value"})

    def test_unknown_schema_version_is_not_overwritten(self):
        self.db.set_metadata("schema_version", "99")
        with self.assertRaises(RuntimeError):
            self.db.initialize()
        self.assertEqual(self.db.get_metadata("schema_version"), "99")


if __name__ == "__main__":
    unittest.main()
