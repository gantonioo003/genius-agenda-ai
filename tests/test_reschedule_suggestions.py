"""Sugestão conservadora: releitura, duração derivada e disponibilidade intacta."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from app.services.availability import (
    AvailabilityBinding, AvailabilityEvent, AvailabilityInputError,
    AvailabilityService, AvailabilitySnapshot,
)
from app.services.interview_briefing import BriefingInputError, BriefingSetupError
from app.services.reschedule_suggestions import RescheduleSuggestionsService, SuggestionCollectionError


IDENTITY = 'MixedOccurrence_20300619'
ZONE = 'America/Recife'
NOW = datetime(2030, 6, 18, tzinfo=timezone.utc)
REQUEST = {'event_id': IDENTITY, 'date_from': '2030-06-20', 'date_to': '2030-06-20',
           'window_start': '09:00', 'window_end': '12:00', 'buffer_minutes': 0}
PRIVATE = 'PRIVATE_PROVIDER_DIAGNOSTIC'


class BriefingFake:
    def __init__(self):
        self.calls, self.failure, self.transform = [], None, None
        self.status = 'ready'
        self.facts = {'event_id': IDENTITY, 'title': 'Entrevista técnica',
            'start': '2030-06-19T09:00:00-03:00', 'end': '2030-06-19T09:45:00-03:00',
            'time_zone': ZONE, 'all_day': False, 'status': 'confirmed', 'location': 'not projected'}

    def prepare(self, **arguments):
        self.calls.append(arguments)
        if self.failure:
            raise self.failure
        result = {'schema_version': 1, 'status': self.status, 'question': 'Not reused ' + PRIVATE,
                  'facts': deepcopy(self.facts) if self.status == 'ready' else None,
                  'execution_allowed': False, 'reservation_created': False}
        return self.transform(result) if self.transform else result


class OccupancySource:
    def __init__(self):
        self.zone, self.events, self.queries = ZONE, (), []

    @contextmanager
    def factory(self):
        yield self

    def binding(self):
        return AvailabilityBinding('fixture-calendar', self.zone)

    def list_occupancy(self, period, *, expected_binding):
        self.queries.append((period, expected_binding))
        return AvailabilitySnapshot(expected_binding, period, self.events, True, 1, len(self.events))


class AvailabilityFake:
    """Captura a delegação e usa o cálculo real sobre uma fonte local controlada."""
    def __init__(self):
        self.calls, self.failure, self.transform = [], None, None
        self.source, self.last_result = OccupancySource(), None

    def find(self, **arguments):
        self.calls.append(arguments)
        if self.failure:
            raise self.failure
        result = AvailabilityService(self.source.factory, clock=lambda: NOW).find(**arguments)
        self.last_result = self.transform(result) if self.transform else result
        return self.last_result


class RescheduleSuggestionsTests(unittest.TestCase):
    def setUp(self):
        self.briefing, self.availability = BriefingFake(), AvailabilityFake()
        self.briefing_factory, self.availability_factory = Mock(return_value=self.briefing), Mock(return_value=self.availability)
        self.service = RescheduleSuggestionsService(briefing_factory=self.briefing_factory,
                                                   availability_factory=self.availability_factory)

    def suggest(self, **changes):
        result = self.service.suggest(**(REQUEST | changes))
        self.assertIs(result['execution_allowed'], False)
        self.assertIs(result['reservation_created'], False)
        return result

    def assert_clarification(self, result):
        self.assertEqual(result['status'], 'needs_clarification')
        self.assertTrue(result['question'])
        self.assertNotIn(PRIVATE, result['question'])
        self.assertIsNone(result['event'])
        self.assertIsNone(result['availability'])
        self.assertIsNone(result['duration_minutes'])

    def assert_collection_failure(self):
        with self.assertRaises(SuggestionCollectionError) as caught:
            self.suggest()
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_ready_derives_duration_and_preserves_explicit_target_and_original_availability(self):
        result = self.suggest(buffer_minutes=15)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['duration_minutes'], 45)
        self.assertEqual(self.briefing.calls, [{'event_id': IDENTITY}])
        self.assertEqual(self.availability.calls, [{key: value for key, value in (REQUEST | {
            'buffer_minutes': 15, 'duration_minutes': 45}).items() if key != 'event_id'}])
        self.assertEqual(set(result['event']), {'event_id', 'title', 'start', 'end', 'time_zone', 'all_day', 'status'})
        self.assertEqual(result['event']['start'], self.briefing.facts['start'])
        self.assertIs(result['availability'], self.availability.last_result)
        self.briefing_factory.assert_called_once_with()
        self.availability_factory.assert_called_once_with()

    def test_conservative_source_keeps_original_event_busy_without_exclusion_or_filter(self):
        occupied = AvailabilityEvent(IDENTITY, datetime(2030, 6, 19, 12, tzinfo=timezone.utc),
            datetime(2030, 6, 19, 12, 45, tzinfo=timezone.utc), 'confirmed', 'opaque', False)
        self.availability.source.events = (occupied,)
        result = self.suggest(date_from='2030-06-19', date_to='2030-06-19')
        self.assertEqual(result['availability']['slots'][0]['start'], '2030-06-19T09:45:00-03:00')
        self.assertEqual(self.availability.source.events, (occupied,))
        self.assertNotIn('exclude_event_id', self.availability.calls[0])
        self.assertEqual(len(self.availability.source.queries), 1)

    def test_dates_window_buffer_and_id_validate_before_any_factory(self):
        cases = [({'date_from': '2030-02-30'}, AvailabilityInputError),
                 ({'date_to': '2030-07-21'}, AvailabilityInputError),
                 ({'window_start': '18:00', 'window_end': '09:00'}, AvailabilityInputError),
                 ({'buffer_minutes': True}, AvailabilityInputError), ({'buffer_minutes': 241}, AvailabilityInputError),
                 ({'event_id': 'bad id'}, BriefingInputError), ({'event_id': 'x' * 1025}, BriefingInputError)]
        for changes, exception in cases:
            with self.subTest(changes=changes), self.assertRaises(exception):
                self.suggest(**changes)
        self.briefing_factory.assert_not_called()
        self.availability_factory.assert_not_called()
        with self.assertRaises(TypeError):
            self.service.suggest(**REQUEST, duration_minutes=60)

    def test_missing_identity_asks_without_selecting_or_opening_source(self):
        for identity in (None, '', '   '):
            self.assert_clarification(self.suggest(event_id=identity))
        self.briefing_factory.assert_not_called()
        self.availability_factory.assert_not_called()

    def test_absent_cancelled_or_changed_interview_never_reaches_availability(self):
        self.briefing.status = 'needs_clarification'
        self.assert_clarification(self.suggest())
        self.briefing.status = 'ready'
        self.briefing.facts['status'] = 'cancelled'
        self.assert_clarification(self.suggest())
        self.availability_factory.assert_not_called()

    def test_all_day_interview_asks_for_timed_occurrence_without_inventing_duration(self):
        self.briefing.facts.update(all_day=True, start='2030-06-19', end='2030-06-21')
        self.assert_clarification(self.suggest())
        self.availability_factory.assert_not_called()

    def test_fractional_too_short_or_overlong_duration_requires_clarification(self):
        for end in ['2030-06-19T09:00:30-03:00', '2030-06-19T09:45:00.001-03:00', '2030-06-20T09:01:00-03:00']:
            with self.subTest(end=end):
                self.briefing.facts['end'] = end
                self.assert_clarification(self.suggest())
        self.availability_factory.assert_not_called()

    def test_duration_uses_real_utc_minutes_across_dst_and_accepts_exact_maximum(self):
        self.briefing.facts.update(start='2030-11-03T01:45:00-04:00', end='2030-11-03T01:15:00-05:00',
                                  time_zone='America/New_York')
        self.availability.source.zone = 'America/New_York'
        result = self.suggest(date_from='2030-11-04', date_to='2030-11-04')
        self.assertEqual(result['duration_minutes'], 30)
        self.assertEqual(self.availability.calls[-1]['duration_minutes'], 30)
        self.briefing.facts.update(start='2030-06-19T09:00:00-03:00', end='2030-06-20T09:00:00-03:00', time_zone=ZONE)
        self.availability.source.zone = ZONE
        result = self.suggest(window_start='00:00', window_end='24:00')
        self.assertEqual(result['duration_minutes'], 1440)
        self.assertEqual(result['availability']['count'], 1)

    def test_invalid_briefing_identity_flags_time_or_source_never_becomes_ready(self):
        baseline = deepcopy(self.briefing.facts)
        for change in [{'event_id': 'other'}, {'title': '\ud800'}, {'time_zone': 'Invalid/PRIVATE'},
                       {'start': '2030-06-19T09:00:00'}, {'end': baseline['start']}, {'status': 'unknown'}]:
            self.briefing.facts = baseline | change
            self.assert_collection_failure()
        self.briefing.facts = baseline
        self.briefing.transform = lambda result: result | {'execution_allowed': True}
        self.assert_collection_failure()
        self.availability_factory.assert_not_called()

    def test_availability_binding_flags_and_incomplete_or_wrong_results_fail_closed(self):
        transforms = [lambda r: r | {'execution_allowed': True}, lambda r: r | {'reservation_created': True},
                      lambda r: r | {'period': r['period'] | {'time_zone': 'UTC'}},
                      lambda r: r | {'snapshot': {'complete': False}}, lambda r: r | {'count': True},
                      lambda r: r | {'request': r['request'] | {'duration_minutes': 60}},
                      lambda r: r | {'slots': [r['slots'][0] | {'end': r['slots'][0]['start']}] + r['slots'][1:]}]
        for transform in transforms:
            self.availability.transform = transform
            self.assert_collection_failure()

    def test_factory_or_service_errors_are_sanitized_without_retry(self):
        self.briefing.failure = BriefingSetupError()
        self.assert_collection_failure()
        self.briefing.failure = RuntimeError(PRIVATE)
        self.assert_collection_failure()
        self.briefing.failure = None
        self.availability.failure = RuntimeError(PRIVATE)
        self.assert_collection_failure()
        self.assertEqual(len(self.briefing.calls), 3)
        self.assertEqual(len(self.availability.calls), 1)
        self.briefing_factory.side_effect = RuntimeError(PRIVATE)
        self.assert_collection_failure()
        self.assertEqual(len(self.briefing.calls), 3)

    def test_complete_empty_availability_is_ready_without_reservation_or_fabricated_options(self):
        self.availability.source.events = (AvailabilityEvent('other-busy',
            datetime(2030, 6, 20, 12, tzinfo=timezone.utc), datetime(2030, 6, 20, 15, tzinfo=timezone.utc),
            'confirmed', 'opaque', False),)
        result = self.suggest()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['availability']['slots'], [])
        self.assertEqual(result['availability']['count'], 0)
        self.assertFalse(result['availability']['more_available'])


if __name__ == '__main__':
    unittest.main()
