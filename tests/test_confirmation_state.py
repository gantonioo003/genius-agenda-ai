"""Consentimento mínimo, migração e consumo atômico com SQLite temporário."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.confirmation_state import (ConfirmationError, ConfirmationProposal, canonical_parameters,
    validate_confirmation, proposal_request_hash, encode_confirmation, decode_confirmation)
from app.conversation_store import ConversationStore, ConversationError, QueryMemory
from app.dialogue_state import PendingRequest

NOW = datetime(2030, 6, 17, 12, tzinfo=timezone.utc)
OWNER = sha256(b'fixture-confirmation-owner').hexdigest()
OTHER = sha256(b'fixture-other-owner').hexdigest()
EVENT = {'event_id': 'FixtureExactEvent', 'title': 'Título atual fictício',
    'start': '2030-06-17T12:00:00+00:00', 'end': '2030-06-17T13:00:00+00:00',
    'all_day': False, 'time_zone': 'UTC', 'status': 'confirmed', 'html_link': None}
PARAMETERS = {'title': 'Título novo fictício', 'time_zone': 'UTC', 'event_id': EVENT['event_id']}

def proposal(action='edit', **changes):
    params = PARAMETERS if action == 'edit' else ({'time_zone': 'UTC', 'event_id': EVENT['event_id']} if action == 'delete' else
        {'time_zone': 'UTC', 'event_id': EVENT['event_id'], 'start': '2030-06-18T12:00:00+00:00', 'end': '2030-06-18T13:00:00+00:00'} if action == 'reschedule' else
        {'title': 'Entrevista fictícia', 'start': '2030-06-17T12:00:00+00:00', 'end': '2030-06-17T13:00:00+00:00', 'time_zone': 'UTC', 'description': ''})
    value = dict(proposal_id=str(uuid4()), action=action, parameters=dict(params), request_hash='', calendar_id='fixture-calendar',
        event_id=None if action == 'create' else EVENT['event_id'], event_version=None if action == 'create' else '"fixture-etag-v1"',
        event=None if action == 'create' else dict(EVENT), created_at=NOW.isoformat(), expires_at=(NOW + timedelta(seconds=300)).isoformat())
    value.update(changes)
    value['request_hash'] = proposal_request_hash(value['action'], value['parameters'], value['calendar_id'], value['event_id'], value['event_version'], value['event'])
    return ConfirmationProposal(**value)

def approved(value=None):
    return replace(proposal() if value is None else value, state='approved', approved_at=NOW.isoformat(),
                   approval_message_hash=sha256(b'confirmo').hexdigest())

def memory(value):
    return QueryMemory(pending_request=PendingRequest({'action': value.action, **value.parameters}), confirmation=value)

class ConfirmationStateTests(unittest.TestCase):
    def invalid(self, value):
        with self.assertRaises(ConfirmationError) as raised:
            validate_confirmation(value)
        self.assertEqual(raised.exception.code, 'confirmation_invalid')

    def test_all_actions_validate_and_round_trip_without_aliasing(self):
        for action in ('create', 'edit', 'reschedule', 'delete'):
            value = proposal(action)
            copied = validate_confirmation(value)
            self.assertEqual(copied, value)
            self.assertIsNot(copied.parameters, value.parameters)
            self.assertEqual(decode_confirmation(encode_confirmation(value)), value)

    def test_default_description_and_timestamp_title_canonicalization(self):
        params = {'title': '  Entrevista  ', 'start': '2030-06-17T12:00:00Z', 'end': '2030-06-17T13:00:00Z', 'time_zone': 'UTC'}
        canonical = canonical_parameters('create', params)
        self.assertEqual(canonical['title'], 'Entrevista')
        self.assertEqual(canonical['description'], '')
        self.assertEqual(canonical['start'], '2030-06-17T12:00:00+00:00')
        self.invalid(proposal('create', parameters=params))

    def test_exact_request_hash_covers_every_binding_component(self):
        original = proposal()
        for name, value in [('calendar_id', 'other-calendar'), ('event_version', '"v2"'),
                            ('parameters', {**PARAMETERS, 'description': ''}),
                            ('event', {**EVENT, 'title': 'outro título'})]:
            changed = replace(original, **{name: value})
            self.invalid(changed)
            self.assertNotEqual(original.request_hash, proposal_request_hash(changed.action, changed.parameters,
                changed.calendar_id, changed.event_id, changed.event_version, changed.event))

    def test_omitted_and_empty_changes_are_distinct(self):
        one = proposal()
        two = proposal(parameters={**PARAMETERS, 'description': ''})
        self.assertNotEqual(one.request_hash, two.request_hash)
        self.assertEqual(validate_confirmation(two), two)

    def test_invalid_shapes_and_private_fields_are_rejected(self):
        for params in ({}, {**PARAMETERS, 'execute': True}, {**PARAMETERS, 'confirmation_token': 'fixture'},
                       {**PARAMETERS, 'search_title': 'a'}, {**PARAMETERS, 'title': ''},
                       {**PARAMETERS, 'title': 'private\0'}, {**PARAMETERS, 'title': 'x' * 4001}):
            self.invalid(proposal(parameters=params))
        value = encode_confirmation(proposal())
        with self.assertRaises(ConfirmationError):
            decode_confirmation({**value, 'message': 'private fixture'})

    def test_identity_and_version_are_strict_and_never_repaired(self):
        for changes in ({'proposal_id': 'bad'}, {'proposal_id': str(uuid4()).upper()},
                        {'event_id': 'OtherID'}, {'event_version': ''}, {'event_version': 'v1\n'},
                        {'event': {**EVENT, 'event_id': 'OtherID'}}, {'calendar_id': ''}):
            self.invalid(proposal(**changes))
        self.invalid(proposal('create', event_id='unexpected'))

    def test_invalid_event_payload_cannot_be_sealed(self):
        for changes in ({'all_day': 1}, {'status': 'cancelled'}, {'end': EVENT['start']},
                        {'title': ''}, {'description': 'private'}, {'time_zone': 'not-zone'}):
            self.invalid(proposal(event={**EVENT, **changes}))

    def test_valid_all_day_uses_calendar_zone_and_exclusive_dates(self):
        event = {**EVENT, 'all_day': True, 'start': '2030-06-17', 'end': '2030-06-18', 'time_zone': 'America/Recife'}
        self.assertEqual(validate_confirmation(proposal('delete', event=event)).event, event)
        self.invalid(proposal('reschedule', event=event))

    def test_deadline_maximum_and_utc_ordering(self):
        for expiry in (NOW.isoformat(), (NOW - timedelta(seconds=1)).isoformat(), (NOW + timedelta(seconds=901)).isoformat(),
                       '2030-06-17T12:05:00', '2030-06-17T09:05:00-03:00'):
            self.invalid(proposal(expires_at=expiry))
        self.assertEqual(validate_confirmation(proposal(expires_at=(NOW + timedelta(seconds=900)).isoformat())).state, 'pending')

    def test_approval_requires_hash_and_server_time_pair(self):
        value = proposal()
        for changes in ({'state': 'approved'}, {'approved_at': NOW.isoformat()},
                        {'approval_message_hash': 'a' * 64}, {'state': 'approved', 'approved_at': value.expires_at, 'approval_message_hash': 'a' * 64},
                        {'state': 'pending', 'approved_at': NOW.isoformat(), 'approval_message_hash': 'a' * 64}):
            self.invalid(replace(value, **changes))
        self.assertEqual(validate_confirmation(approved(value)).state, 'approved')

    def test_consumption_requires_approval_and_deadline(self):
        value = approved()
        self.invalid(replace(value, state='consumed'))
        self.invalid(replace(value, state='consumed', consumed_at=value.expires_at))
        self.invalid(replace(value, consumed_at=NOW.isoformat()))
        self.assertEqual(validate_confirmation(replace(value, state='consumed', consumed_at=NOW.isoformat())).state, 'consumed')

    def test_frozen_private_repr_and_safe_errors(self):
        value = approved()
        with self.assertRaises(FrozenInstanceError):
            value.state = 'consumed'
        for private in (value.proposal_id, value.calendar_id, value.event_id, value.event_version, value.request_hash):
            self.assertNotIn(private, repr(value))
        self.assertNotIn('unsafe', str(ConfirmationError('unsafe provider detail')))
        self.assertEqual(ConfirmationError('confirmation_expired').http_status, 409)

class ConfirmationPersistenceTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'consent.sqlite3'
        self.now = NOW
        self.store = ConversationStore(self.path, clock=lambda: self.now)

    def save(self, value=None):
        value = approved() if value is None else value
        cid = self.store.create(OWNER)
        self.store.save(OWNER, cid, 0, memory(value))
        return cid, value

    def consume(self, cid, value, revision=1, **changes):
        args = {'expected_revision': revision, 'proposal_id': value.proposal_id, 'request_hash': value.request_hash,
                'event_version': value.event_version}
        args.update(changes)
        return self.store.consume_confirmation(OWNER, cid, **args)

    def test_new_store_reloads_and_consumes_exact_claim_only_once(self):
        cid, value = self.save()
        self.store = ConversationStore(self.path, clock=lambda: self.now)
        claim = self.consume(cid, value)
        self.assertEqual(claim.parameters, value.parameters)
        self.assertEqual(claim.event_version, value.event_version)
        self.assertNotIn(value.event_id, repr(claim))
        loaded = self.store.load(OWNER, cid)
        self.assertEqual(loaded.revision, 2)
        self.assertEqual(loaded.memory.confirmation.state, 'consumed')
        with self.assertRaises(ConfirmationError) as raised:
            self.consume(cid, value)
        self.assertEqual(raised.exception.code, 'confirmation_consumed')

    def test_wrong_owner_unknown_proposal_and_changed_binding_do_not_consume(self):
        cid, value = self.save()
        with self.assertRaises(ConversationError):
            self.store.consume_confirmation(OTHER, cid, 1, value.proposal_id, value.request_hash, value.event_version)
        for changes, code in (({'proposal_id': str(uuid4())}, 'confirmation_not_found'),
                              ({'request_hash': 'a' * 64}, 'confirmation_stale'),
                              ({'event_version': '"changed"'}, 'confirmation_stale')):
            with self.assertRaises(ConfirmationError) as raised:
                self.consume(cid, value, **changes)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.store.load(OWNER, cid).revision, 1)

    def test_deadline_and_unapproved_states_block_consumption(self):
        for state in ('pending', 'revoked', 'expired', 'invalidated'):
            cid, value = self.save(proposal(state=state))
            with self.assertRaises(ConfirmationError):
                self.consume(cid, value)
            self.assertEqual(self.store.load(OWNER, cid).revision, 1)
        cid, value = self.save()
        self.now += timedelta(seconds=300)
        with self.assertRaises(ConfirmationError) as raised:
            self.consume(cid, value)
        self.assertEqual(raised.exception.code, 'confirmation_expired')

    def test_stale_cas_cannot_consume_or_renew_revision(self):
        cid, value = self.save()
        with self.assertRaises(ConversationError) as raised:
            self.consume(cid, value, revision=0)
        self.assertEqual(raised.exception.code, 'conversation_conflict')
        self.assertEqual(self.store.load(OWNER, cid).revision, 1)

    def test_changed_draft_invalidates_carried_confirmation(self):
        cid, value = self.save()
        before = self.store.load(OWNER, cid).memory
        changed = replace(before, pending_request=PendingRequest({**before.pending_request.fields, 'title': 'Outro título'}))
        self.store.save(OWNER, cid, 1, changed)
        loaded = self.store.load(OWNER, cid)
        self.assertEqual(loaded.memory.confirmation.state, 'invalidated')
        with self.assertRaises(ConfirmationError):
            self.store.save(OWNER, cid, 2, before)
        self.assertEqual(self.store.load(OWNER, cid).revision, 2)

    def test_consumed_record_cannot_be_reactivated(self):
        cid, value = self.save()
        old = self.store.load(OWNER, cid).memory
        self.consume(cid, value)
        with self.assertRaises(ConfirmationError) as raised:
            self.store.save(OWNER, cid, 2, old)
        self.assertEqual(raised.exception.code, 'confirmation_consumed')

    def test_same_proposal_cannot_extend_expiry_or_replace_prior_approval(self):
        cid, value = self.save()
        original = self.store.load(OWNER, cid).memory
        for changed in (replace(value, expires_at=(NOW + timedelta(seconds=600)).isoformat()),
                        replace(value, approved_at=(NOW + timedelta(seconds=1)).isoformat()),
                        replace(value, approval_message_hash='b' * 64),
                        replace(value, state='pending', approved_at=None, approval_message_hash=None)):
            with self.assertRaises(ConfirmationError) as raised:
                self.store.save(OWNER, cid, 1, replace(original, confirmation=changed))
            self.assertEqual(raised.exception.code, 'confirmation_stale')
            self.assertEqual(self.store.load(OWNER, cid).memory, original)

    def test_consume_concurrency_has_one_winner(self):
        cid, value = self.save()
        barrier = Barrier(2)
        def consume(_):
            store = ConversationStore(self.path, clock=lambda: NOW)
            barrier.wait(timeout=5)
            try:
                store.consume_confirmation(OWNER, cid, 1, value.proposal_id, value.request_hash, value.event_version)
                return 'consumed'
            except (ConfirmationError, ConversationError) as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(consume, range(2))), ['consumed', 'confirmation_consumed'])
        self.assertEqual(self.store.load(OWNER, cid).revision, 2)

    def test_approval_survives_a_fresh_process(self):
        cid, value = self.save()
        script = ('from app.conversation_store import ConversationStore;from datetime import datetime;import sys;'
                  'm=ConversationStore(sys.argv[1],clock=lambda:datetime.fromisoformat(sys.argv[4])).load(sys.argv[2],sys.argv[3]);'
                  'assert m.memory.confirmation.state=="approved";print("approval-preserved")')
        result = subprocess.run([sys.executable, '-B', '-c', script, str(self.path), OWNER, cid, NOW.isoformat()],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'approval-preserved')

    def test_consume_is_atomic_between_processes(self):
        cid, value = self.save()
        script = ('import sys;from datetime import datetime;from app.conversation_store import ConversationStore;'
                  'from app.confirmation_state import ConfirmationError;'
                  's=ConversationStore(sys.argv[1],clock=lambda:datetime.fromisoformat(sys.argv[7]));'
                  'sys.stdin.readline();\n'
                  'try:\n s.consume_confirmation(sys.argv[2],sys.argv[3],1,sys.argv[4],sys.argv[5],sys.argv[6]);print("consumed")\n'
                  'except ConfirmationError as e:\n print(e.code)\n')
        children = [subprocess.Popen([sys.executable, '-B', '-c', script, str(self.path), OWNER, cid,
            value.proposal_id, value.request_hash, value.event_version, NOW.isoformat()],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) for _ in range(2)]
        try:
            for child in children:
                child.stdin.write('\n'); child.stdin.flush()
            results = [child.communicate(timeout=15) for child in children]
            self.assertCountEqual([stdout.strip() for stdout, _ in results], ['consumed', 'confirmation_consumed'])
            self.assertTrue(all(child.returncode == 0 for child in children))
            self.assertTrue(all(stderr == '' for _, stderr in results))
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate(); child.wait(timeout=5)
        self.assertEqual(self.store.load(OWNER, cid).revision, 2)

    def test_commit_failure_does_not_publish_or_consume_approval(self):
        cid, value = self.save()
        prior = self.store.load(OWNER, cid)
        real_connect = sqlite3.connect
        class BrokenCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError('private fixture commit failure')
        def connect(*args, **kwargs):
            return real_connect(*args, factory=BrokenCommit, **kwargs)
        with patch('app.conversation_store.sqlite3.connect', side_effect=connect):
            with self.assertRaises(ConversationError) as raised:
                self.consume(cid, value)
        self.assertEqual(raised.exception.code, 'conversation_unavailable')
        self.assertEqual(self.store.load(OWNER, cid), prior)

    def test_schema_one_and_two_migrations_preserve_bindings_revision_and_deadlines(self):
        for old_version in ('1', '2'):
            path = self.path.with_name('schema' + old_version + '.sqlite3')
            store = ConversationStore(path, clock=lambda: NOW)
            cid = store.create(OWNER)
            prior_memory = QueryMemory(last_list={'date': '2030-06-17', 'period': 'day', 'time_zone': 'UTC'})
            store.save(OWNER, cid, 0, prior_memory)
            with closing(sqlite3.connect(path)) as connection, connection:
                row = connection.execute('SELECT binding_hash,revision,memory_json,created_at,updated_at,expires_at FROM conversation_contexts').fetchone()
                legacy = json.loads(row[2]); del legacy['confirmation']
                if old_version == '1':
                    del legacy['pending_request']
                connection.execute('UPDATE conversation_contexts SET memory_json=?', (json.dumps(legacy, sort_keys=True, separators=(',', ':')),))
                connection.execute('UPDATE conversation_metadata SET value=?', (old_version,))
            new = ConversationStore(path, clock=lambda: NOW)
            self.assertEqual(new.load(OWNER, cid).memory, prior_memory)
            with closing(sqlite3.connect(path)) as connection, connection:
                migrated = connection.execute('SELECT binding_hash,revision,memory_json,created_at,updated_at,expires_at FROM conversation_contexts').fetchone()
                self.assertEqual(connection.execute('SELECT value FROM conversation_metadata').fetchone(), ('3',))
            self.assertEqual(row[:2] + row[3:], migrated[:2] + migrated[3:])

    def test_invalid_confirmation_does_not_create_database(self):
        with self.assertRaises(ConversationError):
            self.store.save(OWNER, str(uuid4()), 0, QueryMemory(confirmation={'unsafe': True}))
        self.assertFalse(self.path.exists())

if __name__ == '__main__':
    unittest.main()
