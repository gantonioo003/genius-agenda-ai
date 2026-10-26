"""Rascunhos mínimos: validação, persistência e migração atômica sem rede."""

from contextlib import closing
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.conversation_store import (
    ConversationError, ConversationSnapshot, ConversationStore, QueryMemory, validate_memory,
)
from app.dialogue_state import CandidateReference, PendingRequest, validate_pending_request


OWNER = sha256(b"fixture-dialogue-owner").hexdigest()
OTHER_OWNER = sha256(b"fixture-dialogue-other-owner").hexdigest()
CANDIDATE = CandidateReference("fixture-calendar-id", sha256(b"fixture-event-version").hexdigest())
DRAFT = PendingRequest(
    {"action": "create", "title": "Entrevista fictícia", "start": "amanhã sem horário", "time_zone": "UTC"},
    ("start", "end"), (CANDIDATE,),
)


class PendingRequestTests(unittest.TestCase):
    def invalid(self, pending):
        with self.assertRaises(ValueError) as raised:
            validate_pending_request(pending)
        self.assertEqual(str(raised.exception), "Os dados do pedido pendente são inválidos.")

    def test_empty_draft_and_three_positional_memory_fields_remain_supported(self):
        self.assertEqual(validate_pending_request(PendingRequest({})), PendingRequest({}, (), ()))
        self.assertIsNone(validate_memory(QueryMemory(None, None, None)).pending_request)

    def test_validation_copies_fields_candidates_and_memory(self):
        draft = PendingRequest(dict(DRAFT.fields), DRAFT.awaiting, DRAFT.candidates)
        copied = validate_pending_request(draft)
        self.assertEqual(copied, draft)
        self.assertIsNot(copied.fields, draft.fields)
        self.assertIsNot(copied.candidates[0], draft.candidates[0])
        memory = validate_memory(QueryMemory(pending_request=draft))
        draft.fields["title"] = "mutação externa"
        self.assertEqual(memory.pending_request.fields["title"], "Entrevista fictícia")
        self.assertEqual(copied.fields["title"], "Entrevista fictícia")

    def test_temporally_invalid_text_remains_available_for_correction(self):
        fields = {"action": "reschedule", "start": "2026-02-30T29:99", "end": "antes do início",
                  "date": "32/13", "period": "quinzena", "time_zone": "zona a esclarecer"}
        self.assertEqual(validate_pending_request(PendingRequest(fields)).fields, fields)

    def test_all_allowed_fields_are_supported_without_transcript_fields(self):
        fields = {"action": "edit", "time_zone": "UTC", "title": "a", "start": "b", "end": "c",
                  "description": "d", "location": "e", "event_id": "id", "search_title": "f",
                  "date": "g", "period": "h"}
        self.assertEqual(validate_pending_request(PendingRequest(fields)).fields, fields)
        for key in ("message", "history", "user_id", "phone", "token", "confirmation", "execution_allowed"):
            self.invalid(PendingRequest({key: "private fixture"}))

    def test_actions_are_exact_and_fields_require_text(self):
        for action in ("create", "list", "reschedule", "edit", "delete"):
            self.assertEqual(validate_pending_request(PendingRequest({"action": action})).fields["action"], action)
        for value in (None, {}, [], 1, True):
            self.invalid(PendingRequest({"title": value}))
        for action in ("query", "execute", "CREATE", "", "create\n"):
            self.invalid(PendingRequest({"action": action}))
        for pending in (None, {}, PendingRequest([]), PendingRequest({1: "value"})):
            self.invalid(pending)

    def test_valid_utf8_control_and_per_field_limits(self):
        text = "primeira\tcoluna\r\npróxima linha 🙂"
        self.assertEqual(validate_pending_request(PendingRequest({"description": text})).fields["description"], text)
        self.assertEqual(len(validate_pending_request(PendingRequest({"title": "x" * 4000})).fields["title"]), 4000)
        for value in ("x" * 4001, "private\0", "private\x1f", "private\x7f", "private\u200b", "private\ud800"):
            self.invalid(PendingRequest({"description": value}))

    def test_combined_utf8_budget_includes_multibyte_values(self):
        fields = {key: "x" * 4000 for key in ("title", "description", "location", "start", "end")}
        self.assertEqual(validate_pending_request(PendingRequest(fields)).fields, fields)
        self.invalid(PendingRequest({**fields, "search_title": "x" * 500}))
        self.invalid(PendingRequest({"title": "🙂" * 4000, "description": "🙂" * 2000}))

    def test_event_identifiers_are_opaque_strict_and_bounded(self):
        self.assertEqual(validate_pending_request(PendingRequest({"event_id": "MixedCase_id"})).fields["event_id"],
                         "MixedCase_id")
        self.assertEqual(len(validate_pending_request(PendingRequest({"event_id": "x" * 1024})).fields["event_id"]),
                         1024)
        for event_id in ("", "two words", "id\t", "id\n", "id\u200d", "x" * 1025):
            self.invalid(PendingRequest({"event_id": event_id}))

    def test_awaiting_names_are_known_unique_tuple_and_limited(self):
        names = ("action", "title", "start", "end", "time_zone", "event", "changes", "request")
        self.assertEqual(validate_pending_request(PendingRequest({}, names)).awaiting, names)
        for awaiting in ([], ("title", "title"), ("unknown",), (1,), "title",
                         ("action", "title", "start", "end", "time_zone", "date", "period", "event",
                          "event_id", "search_title", "changes", "description", "location")):
            self.invalid(PendingRequest({}, awaiting))

    def test_candidate_references_are_bounded_unique_and_only_ids_and_fingerprints(self):
        candidates = tuple(CandidateReference(f"fixture-{number}", CANDIDATE.fingerprint) for number in range(20))
        self.assertEqual(validate_pending_request(PendingRequest({}, (), candidates)).candidates, candidates)
        for values in ([], (CANDIDATE, CANDIDATE), ({"event_id": "id", "fingerprint": "a" * 64},),
                       (CandidateReference("two words", "a" * 64),),
                       (CandidateReference("id", "A" * 64),), (CandidateReference("id", "g" * 64),),
                       (CandidateReference("id", "a" * 63),),
                       candidates + (CandidateReference("extra", "a" * 64),)):
            self.invalid(PendingRequest({}, (), values))

    def test_private_repr_frozen_types_and_store_error_redaction(self):
        for value in (repr(CANDIDATE), repr(DRAFT), repr(QueryMemory(pending_request=DRAFT))):
            self.assertNotIn(CANDIDATE.event_id, value)
            self.assertNotIn(CANDIDATE.fingerprint, value)
            self.assertNotIn(DRAFT.fields["title"], value)
        with self.assertRaises(FrozenInstanceError):
            DRAFT.awaiting = ()
        with self.assertRaises(FrozenInstanceError):
            CANDIDATE.event_id = "other"
        with self.assertRaises(ConversationError) as raised:
            validate_memory(QueryMemory(pending_request={"title": "secret fixture"}))
        self.assertEqual(raised.exception.code, "invalid_conversation")
        self.assertNotIn("secret fixture", str(raised.exception))


class PendingConversationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "conversation.sqlite3"
        self.now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        self.store = ConversationStore(self.path, clock=lambda: self.now)

    def execute(self, sql, params=()):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            return connection.execute(sql, params).fetchall()

    def rows(self):
        return self.execute("SELECT binding_hash,revision,memory_json,created_at,updated_at,expires_at "
                            "FROM conversation_contexts ORDER BY binding_hash")

    def legacy(self, memories):
        self.store.initialize()
        identifiers = []
        for memory in memories:
            identifier = self.store.create(OWNER)
            self.store.save(OWNER, identifier, 0, memory)
            identifiers.append(identifier)
        rows = self.rows()
        for row in rows:
            value = json.loads(row[2])
            del value["pending_request"]
            del value["confirmation"]
            old_json = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            self.execute("UPDATE conversation_contexts SET memory_json=? WHERE binding_hash=?", (old_json, row[0]))
        self.execute("UPDATE conversation_metadata SET value='1'")
        return identifiers, self.rows()

    def unavailable(self, callback):
        with self.assertRaises(ConversationError) as raised:
            callback()
        self.assertEqual(raised.exception.code, "conversation_unavailable")

    def test_pending_draft_is_canonical_durable_and_independently_loaded(self):
        identifier = self.store.create(OWNER)
        memory = QueryMemory(pending_request=DRAFT)
        self.store.save(OWNER, identifier, 0, memory)
        restarted = ConversationStore(self.path, clock=lambda: self.now)
        loaded = restarted.load(OWNER, identifier)
        self.assertEqual(loaded, ConversationSnapshot(1, memory))
        loaded.memory.pending_request.fields["title"] = "outside mutation"
        self.assertEqual(restarted.load(OWNER, identifier).memory, memory)
        value = json.loads(self.rows()[0][2])["pending_request"]
        self.assertEqual(value, {"fields": DRAFT.fields, "awaiting": list(DRAFT.awaiting),
                                 "candidates": [{"event_id": CANDIDATE.event_id,
                                                 "fingerprint": CANDIDATE.fingerprint}]})
        self.assertEqual(self.execute("SELECT value FROM conversation_metadata"), [("3",)])

    def test_pending_draft_survives_a_new_process(self):
        identifier = self.store.create(OWNER)
        self.store.save(OWNER, identifier, 0, QueryMemory(pending_request=DRAFT))
        script = (
            "import sys;from datetime import datetime;from app.conversation_store import ConversationStore;"
            "store=ConversationStore(sys.argv[1],clock=lambda:datetime.fromisoformat(sys.argv[4]));"
            "m=store.load(sys.argv[2],sys.argv[3]).memory;"
            "assert m.pending_request.fields['action']=='create';"
            "assert m.pending_request.awaiting==('start','end');"
            "assert len(m.pending_request.candidates)==1;print('pending-preserved')"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(self.path), OWNER, identifier, self.now.isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "pending-preserved")
        self.assertEqual(result.stderr, "")

    def test_largest_allowed_multibyte_draft_remains_decodable_after_ascii_json_expansion(self):
        candidates = tuple(CandidateReference("🙂" * 1022 + f"{number:02}", "a" * 64) for number in range(20))
        pending = PendingRequest({"title": "🙂" * 4000, "description": "🙂" * 1000}, (), candidates)
        identifier = self.store.create(OWNER)
        self.store.save(OWNER, identifier, 0, QueryMemory(pending_request=pending))
        self.assertGreater(len(self.rows()[0][2]), 256 * 1024)
        self.assertEqual(ConversationStore(self.path, clock=lambda: self.now).load(OWNER, identifier).memory.pending_request,
                         pending)

    def test_pending_draft_respects_owner_ttl_and_delete(self):
        identifier = self.store.create(OWNER)
        other = self.store.create(OTHER_OWNER)
        self.store.save(OWNER, identifier, 0, QueryMemory(pending_request=DRAFT))
        for selected_owner, selected_id in ((OTHER_OWNER, identifier), (OWNER, other)):
            with self.assertRaises(ConversationError) as raised:
                self.store.load(selected_owner, selected_id)
            self.assertEqual(raised.exception.code, "conversation_not_found")
        self.store.delete(OWNER, identifier)
        self.assertEqual(self.store.load(OTHER_OWNER, other).memory, QueryMemory())
        self.now += timedelta(seconds=1800)
        with self.assertRaises(ConversationError) as raised:
            self.store.load(OTHER_OWNER, other)
        self.assertEqual(raised.exception.code, "conversation_not_found")

    def test_invalid_draft_cannot_create_database_or_change_saved_state(self):
        with patch("app.conversation_store.sqlite3.connect") as connect:
            with self.assertRaises(ConversationError) as raised:
                self.store.save(OWNER, str(uuid4()), 0, QueryMemory(pending_request=PendingRequest({"token": "secret"})))
            self.assertEqual(raised.exception.code, "invalid_conversation")
            connect.assert_not_called()
        self.assertFalse(self.path.exists())
        identifier = self.store.create(OWNER)
        before = self.rows()
        with self.assertRaises(ConversationError):
            self.store.save(OWNER, identifier, 0, QueryMemory(pending_request=PendingRequest({"history": "raw text"})))
        self.assertEqual(self.rows(), before)

    def test_schema_one_migration_preserves_all_bindings_revisions_and_timestamps(self):
        memory = QueryMemory({"date": "2026-10-24", "period": "day", "time_zone": "UTC"}, "fixture-id", "UTC")
        identifiers, before = self.legacy([QueryMemory(), memory])
        restarted = ConversationStore(self.path, clock=lambda: self.now)
        restarted.initialize()
        after = self.rows()
        self.assertEqual(self.execute("SELECT value FROM conversation_metadata"), [("3",)])
        for old, new in zip(before, after):
            self.assertEqual(old[:2] + old[3:], new[:2] + new[3:])
            self.assertEqual(json.loads(new[2]), {**json.loads(old[2]), "pending_request": None, "confirmation": None})
        self.assertEqual(restarted.load(OWNER, identifiers[1]), ConversationSnapshot(1, memory))
        with self.assertRaises(ConversationError) as raised:
            restarted.load(OTHER_OWNER, identifiers[1])
        self.assertEqual(raised.exception.code, "conversation_not_found")
        self.now += timedelta(seconds=1800)
        with self.assertRaises(ConversationError) as raised:
            restarted.load(OWNER, identifiers[1])
        self.assertEqual(raised.exception.code, "conversation_not_found")

    def test_migration_is_lazy_and_does_not_rewrite_current_schema(self):
        self.legacy([QueryMemory()])
        before = self.path.read_bytes()
        restarted = ConversationStore(self.path, clock=lambda: self.now)
        self.assertEqual(self.path.read_bytes(), before)
        restarted.initialize()
        migrated = self.path.read_bytes()
        ConversationStore(self.path, clock=lambda: self.now).initialize()
        self.assertEqual(self.path.read_bytes(), migrated)

    def test_bad_legacy_row_leaves_all_rows_and_version_unchanged(self):
        self.legacy([QueryMemory(), QueryMemory()])
        self.execute("UPDATE conversation_contexts SET memory_json=? WHERE binding_hash=?",
                     ('{"last_list":null,"selected_event_id":null,"selected_time_zone":null,"history":"raw"}',
                      self.rows()[-1][0]))
        before = self.path.read_bytes()
        self.unavailable(lambda: ConversationStore(self.path, clock=lambda: self.now).initialize())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.execute("SELECT value FROM conversation_metadata"), [("1",)])

    def test_noncanonical_legacy_json_and_future_schema_are_never_migrated(self):
        self.legacy([QueryMemory()])
        self.execute("UPDATE conversation_contexts SET memory_json=?",
                     ('{"last_list":null,"last_list":null,"selected_event_id":null,"selected_time_zone":null}',))
        before = self.path.read_bytes()
        self.unavailable(lambda: ConversationStore(self.path, clock=lambda: self.now).initialize())
        self.assertEqual(self.path.read_bytes(), before)
        self.execute("UPDATE conversation_metadata SET value='9000'")
        before = self.path.read_bytes()
        self.unavailable(lambda: ConversationStore(self.path, clock=lambda: self.now).initialize())
        self.assertEqual(self.path.read_bytes(), before)

    def test_migration_commit_failure_restores_original_schema_and_rows(self):
        self.legacy([QueryMemory(), QueryMemory()])
        before = self.path.read_bytes()
        real_connect = sqlite3.connect

        class FailingCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("private migration fixture")

        def connect(*args, **kwargs):
            return real_connect(*args, factory=FailingCommit, **kwargs)

        with patch("app.conversation_store.sqlite3.connect", side_effect=connect):
            self.unavailable(lambda: ConversationStore(self.path, clock=lambda: self.now).initialize())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.execute("SELECT value FROM conversation_metadata"), [("1",)])

    def test_corrupt_nested_pending_json_fails_without_repair_or_disclosure(self):
        identifier = self.store.create(OWNER)
        self.store.save(OWNER, identifier, 0, QueryMemory(pending_request=DRAFT))
        original = json.loads(self.rows()[0][2])
        for pending in ({"fields": {}, "awaiting": [], "candidates": [], "history": "secret"},
                        {"fields": {}, "awaiting": "title", "candidates": []},
                        {"fields": {}, "awaiting": [], "candidates": [{"event_id": "id", "fingerprint": "bad"}]},
                        {"fields": {"token": "secret"}, "awaiting": [], "candidates": []}):
            value = json.dumps({**original, "pending_request": pending}, ensure_ascii=True,
                               separators=(",", ":"), sort_keys=True)
            self.execute("UPDATE conversation_contexts SET memory_json=?", (value,))
            before = self.path.read_bytes()
            self.unavailable(lambda: self.store.load(OWNER, identifier))
            self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
