"""Trava SQLite real temporária e erros seguros do adaptador, sem rede."""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from app.database import Database
from app import google_calendar
from app.services.interviews import CalendarOperationError


class CalendarWriteLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "calendar.db")
        self.db.initialize()
        self.db.set_metadata("google_calendar_id", "test-calendar@example.invalid")
        self.db.set_metadata("existing_value", "preserve this value")

    def contender(self):
        # Outra conexão representa um processo independente usando o mesmo arquivo.
        return closing(sqlite3.connect(self.db.path, timeout=0, autocommit=True))

    def metadata(self):
        with self.db.connection() as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT key, value, updated_at FROM app_metadata ORDER BY key"
            ).fetchall()]

    def test_independent_begin_immediate_is_blocked_while_lock_is_open(self):
        with self.contender() as contender:
            with self.db.calendar_write_lock():
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    contender.execute("BEGIN IMMEDIATE")
                self.assertFalse(contender.in_transaction)

    def test_successful_context_releases_lock_for_another_writer(self):
        with self.contender() as contender:
            with self.db.calendar_write_lock():
                pass
            contender.execute("BEGIN IMMEDIATE")
            self.assertTrue(contender.in_transaction)
            contender.execute("COMMIT")
            self.assertFalse(contender.in_transaction)
        # A mesma instância também pode obter uma nova trava após sair.
        with self.db.calendar_write_lock():
            pass

    def test_body_exception_propagates_and_releases_lock(self):
        for failure in (RuntimeError("body failed"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)) as raised:
                    with self.db.calendar_write_lock():
                        raise failure
                self.assertIs(raised.exception, failure)
                with self.contender() as contender:
                    contender.execute("BEGIN IMMEDIATE")
                    self.assertTrue(contender.in_transaction)
                    contender.execute("ROLLBACK")

    def test_existing_metadata_survives_success_and_rollback(self):
        original = self.metadata()
        with self.db.calendar_write_lock():
            pass
        self.assertEqual(self.metadata(), original)
        with self.assertRaisesRegex(RuntimeError, "cancel operation"):
            with self.db.calendar_write_lock():
                raise RuntimeError("cancel operation")
        self.assertEqual(self.metadata(), original)
        self.assertEqual(self.db.get_metadata("existing_value"), "preserve this value")


class CalendarWriteLockAdapterTests(unittest.TestCase):
    def assert_uncertain_safe_error(self, error):
        message = str(error)
        self.assertIn("Consulte o evento pelo ID antes de repetir", message)
        self.assertIn("uma gravação pode ter sido aplicada", message)
        self.assertNotIn("private internal database detail", message)
        self.assertNotIn("nenhuma gravação", message.lower())
        self.assertNotIn("nada foi gravado", message.lower())
        self.assertTrue(error.__suppress_context__)

    def test_acquisition_sqlite_error_is_controlled_without_entering_body(self):
        @contextmanager
        def unavailable_lock():
            raise sqlite3.OperationalError("private internal database detail")
            yield

        database = Mock()
        database.calendar_write_lock.side_effect = unavailable_lock
        reached = []
        with patch.object(google_calendar, "database", database):
            with self.assertRaises(CalendarOperationError) as raised:
                with google_calendar.calendar_write_lock():
                    reached.append("body")
        self.assertEqual(reached, [])
        database.calendar_write_lock.assert_called_once_with()
        self.assert_uncertain_safe_error(raised.exception)

    def test_release_sqlite_error_after_body_does_not_claim_no_write(self):
        @contextmanager
        def failed_release():
            yield
            raise sqlite3.OperationalError("private internal database detail")

        database = Mock()
        database.calendar_write_lock.side_effect = failed_release
        reached = []
        with patch.object(google_calendar, "database", database):
            with self.assertRaises(CalendarOperationError) as raised:
                with google_calendar.calendar_write_lock():
                    reached.append("external operation could have completed")
        self.assertEqual(reached, ["external operation could have completed"])
        self.assert_uncertain_safe_error(raised.exception)

    def test_non_sqlite_domain_error_is_preserved(self):
        @contextmanager
        def valid_lock():
            yield

        database = Mock()
        database.calendar_write_lock.side_effect = valid_lock
        failure = CalendarOperationError("domain operation rejected")
        with patch.object(google_calendar, "database", database):
            with self.assertRaises(CalendarOperationError) as raised:
                with google_calendar.calendar_write_lock():
                    raise failure
        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
