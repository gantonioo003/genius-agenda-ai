"""Private pairing store: real temporary SQLite, no network or provider setup."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from app.panel_access import (
    ABSOLUTE_SECONDS, IDLE_SECONDS, MAX_STARTS, PanelAccessStore,
    PanelRateLimitError, PanelStoreError, normalize_pairing_code,
)


class PanelAccessStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "panel.sqlite3"
        self.now = 1000.0
        self.store = PanelAccessStore(self.path, clock=lambda: self.now)

    def activate(self):
        sid, pending = self.store.begin()
        self.assertTrue(self.store.authorize(pending.pairing_code))
        return sid, self.store.get(sid)

    def test_pending_request_is_not_authenticated_and_expires_in_five_minutes(self):
        sid, record = self.store.begin()
        self.assertEqual(record.state, "pending")
        self.assertEqual(record.expires_at, 1300)
        self.assertIsNone(record.csrf_token)
        self.assertRegex(record.pairing_code, r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
        self.assertRegex(sid, r"^[A-Za-z0-9_-]{43}$")
        self.now = 1300
        self.assertIsNone(self.store.get(sid))
        self.assertFalse(self.store.authorize(record.pairing_code))

    def test_persisted_session_id_is_only_a_sha256_digest(self):
        sid, record = self.store.begin()
        self.assertNotEqual(record.id_hash, sid)
        self.assertEqual(record.id_hash, hashlib.sha256(sid.encode("ascii")).hexdigest())
        self.assertNotIn(sid.encode("ascii"), self.path.read_bytes())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            fields = [row[1] for row in connection.execute("PRAGMA table_info(panel_sessions)")]
            self.assertNotIn("sid", fields)
            self.assertEqual(connection.execute("SELECT id_hash FROM panel_sessions").fetchone()[0], record.id_hash)

    def test_authorization_is_exact_and_the_pairing_code_is_consumed(self):
        sid, pending = self.store.begin()
        self.assertFalse(self.store.authorize("AAAA-AAAA"))
        self.assertTrue(self.store.authorize(pending.pairing_code))
        self.assertFalse(self.store.authorize(pending.pairing_code))
        active = self.store.get(sid)
        self.assertEqual(active.state, "active")
        self.assertIsNone(active.pairing_code)
        self.assertRegex(active.csrf_token, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(active.csrf_token, sid)
        self.assertNotIn(active.csrf_token, repr(active))

    def test_code_normalization_allows_cli_case_and_optional_hyphen(self):
        self.assertEqual(normalize_pairing_code(" abcd-efgh "), "ABCD-EFGH")
        self.assertEqual(normalize_pairing_code("abcdefgh"), "ABCD-EFGH")
        for invalid in [None, "", "ABCD--EFGH", "ABCD-EFGH/", "IIII-OOOO", "中文"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_pairing_code(invalid)

    def test_active_session_expires_after_thirty_minutes_idle(self):
        sid, active = self.activate()
        self.assertEqual(active.expires_at, self.now + IDLE_SECONDS)
        self.now += IDLE_SECONDS
        self.assertIsNone(self.store.get(sid))

    def test_activity_extends_idle_timeout_but_not_absolute_expiry(self):
        sid, first = self.activate()
        self.now += 1000
        second = self.store.get(sid)
        self.assertEqual(second.expires_at, self.now + IDLE_SECONDS)
        self.assertEqual(second.absolute_expires_at, first.absolute_expires_at)
        self.now += 1700
        self.assertIsNotNone(self.store.get(sid))

    def test_absolute_eight_hour_limit_survives_regular_activity(self):
        sid, active = self.activate()
        for delta in range(1000, ABSOLUTE_SECONDS, 1000):
            self.now = 1000 + delta
            self.assertIsNotNone(self.store.get(sid))
        self.now = active.absolute_expires_at
        self.assertIsNone(self.store.get(sid))

    def test_clock_moving_backwards_revokes_a_session(self):
        sid, _active = self.activate()
        self.now = 999
        self.assertIsNone(self.store.get(sid))
        self.now = 1001
        self.assertIsNone(self.store.get(sid))

    def test_touch_false_does_not_extend_the_session(self):
        sid, active = self.activate()
        self.now += 1000
        self.assertEqual(self.store.get(sid, touch=False).last_seen_at, active.last_seen_at)
        self.now += 800
        self.assertIsNone(self.store.get(sid, touch=False))

    def test_revocation_invalidates_both_pending_and_active_sessions(self):
        pending_sid, pending = self.store.begin()
        self.assertTrue(self.store.revoke(pending_sid))
        self.assertIsNone(self.store.get(pending_sid))
        self.assertFalse(self.store.authorize(pending.pairing_code))
        sid, _active = self.activate()
        self.assertTrue(self.store.revoke(sid))
        self.assertFalse(self.store.revoke(sid))
        self.assertIsNone(self.store.get(sid))

    def test_repeated_start_reuses_pending_without_extending_expiry(self):
        sid, record = self.store.begin()
        self.now += 100
        repeated_sid, repeated = self.store.begin(sid)
        self.assertEqual(repeated_sid, sid)
        self.assertEqual(repeated.pairing_code, record.pairing_code)
        self.assertEqual(repeated.expires_at, record.expires_at)

    def test_repeated_start_reuses_active_and_preserves_csrf(self):
        sid, active = self.activate()
        repeated_sid, repeated = self.store.begin(sid)
        self.assertEqual(repeated_sid, sid)
        self.assertEqual(repeated.csrf_token, active.csrf_token)
        self.assertEqual(repeated.state, "active")

    def test_start_rate_limit_uses_a_rolling_window(self):
        for _ in range(MAX_STARTS):
            self.store.begin()
        with self.assertRaises(PanelRateLimitError):
            self.store.begin()
        self.now += 61
        sid, record = self.store.begin()
        self.assertEqual(self.store.get(sid).state, "pending")

    def test_pending_cap_and_expired_request_cleanup(self):
        for _ in range(10):
            self.store.begin()
        self.now += 61
        for _ in range(10):
            self.store.begin()
        self.now += 61
        with self.assertRaises(PanelRateLimitError):
            self.store.begin()
        self.now += 301
        self.assertEqual(self.store.begin()[1].state, "pending")

    def test_invalid_session_input_never_opens_a_database(self):
        for invalid in [None, "", "A" * 42, "A" * 44, "../secret", "中文"]:
            self.assertIsNone(self.store.get(invalid))
            self.assertFalse(self.store.revoke(invalid))
        self.assertFalse(self.path.exists())

    def test_private_record_corruption_fails_closed(self):
        sid, _active = self.activate()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE panel_sessions SET csrf_token='invalid'")
        with self.assertRaises(PanelStoreError):
            self.store.get(sid)

    def test_pending_deadline_corruption_fails_closed(self):
        sid, _pending = self.store.begin()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE panel_sessions SET absolute_expires_at=9000")
        with self.assertRaises(PanelStoreError):
            self.store.get(sid)

    def test_invalid_clock_fails_closed(self):
        for value in [float("nan"), float("inf"), -1, True, "1000"]:
            with self.subTest(value=value), self.assertRaises(PanelStoreError):
                PanelAccessStore(self.path, clock=lambda: value).begin()

    def test_filesystem_failure_is_sanitized(self):
        not_a_directory = Path(self.directory.name) / "file"
        not_a_directory.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(PanelStoreError, "indisponível"):
            PanelAccessStore(not_a_directory / "blocked.sqlite3").begin()

    def test_concurrent_authorization_only_approves_once(self):
        sid, record = self.store.begin()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _value: self.store.authorize(record.pairing_code), [1, 2]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.store.get(sid).state, "active")

    def test_cli_authorizes_without_printing_session_or_csrf(self):
        store = PanelAccessStore(self.path)
        sid, record = store.begin()
        command = [sys.executable, "-B", "-m", "app.panel_access", "--database", str(self.path), "authorize", record.pairing_code]
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        active = store.get(sid)
        self.assertEqual(active.state, "active")
        output = result.stdout + result.stderr
        self.assertNotIn(sid.encode(), output)
        self.assertNotIn(active.csrf_token.encode(), output)
        self.assertNotIn(record.pairing_code.encode(), output)
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
