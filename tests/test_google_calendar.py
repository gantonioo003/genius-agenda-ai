"""Proteção contra escolher a agenda errada. Sem chamadas reais ao Google."""

import unittest

from app.google_calendar import CalendarSetupError, choose_calendar


class CalendarSelectionTests(unittest.TestCase):
    def setUp(self):
        self.calendar = {
            "id": "test@example.invalid", "summary": "Genius Testes",
            "accessRole": "owner", "primary": False,
        }

    def test_selects_unique_owned_test_calendar(self):
        self.assertEqual(choose_calendar([self.calendar], "Genius Testes"), self.calendar)

    def test_ambiguous_names_require_id(self):
        other = dict(self.calendar, id="another@example.invalid")
        with self.assertRaises(CalendarSetupError):
            choose_calendar([self.calendar, other], "Genius Testes")

    def test_missing_pinned_id_never_falls_back_to_name(self):
        with self.assertRaises(CalendarSetupError):
            choose_calendar([self.calendar], "Genius Testes", "missing@example.invalid")

    def test_rejects_primary_even_when_name_matches(self):
        with self.assertRaises(CalendarSetupError):
            choose_calendar([dict(self.calendar, primary=True)], "Genius Testes")

    def test_rejects_calendar_not_owned_by_user(self):
        with self.assertRaises(CalendarSetupError):
            choose_calendar([dict(self.calendar, accessRole="writer")], "Genius Testes")


if __name__ == "__main__":
    unittest.main()
