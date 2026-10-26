"""CLI de horários livres com fonte isolada, sem Google real ou modelo."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import unittest
from unittest.mock import patch

from app.find_free_slots import main
from app.services.availability import AvailabilitySetupError, AvailabilityLimitError
from test_availability_api import OccupancyGateway, PRIVATE


ARGS = ['--date-from', '2030-06-19', '--date-to', '2030-06-19',
        '--window-start', '09:00', '--window-end', '12:00', '--duration-minutes', '30']


class FindFreeSlotsCliTests(unittest.TestCase):
    def setUp(self):
        self.gateway = OccupancyGateway()
        self.clock = lambda: datetime(2030, 6, 19, 11, tzinfo=timezone.utc)

    def invoke(self, args):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(args, gateway_factory=self.gateway.factory, clock=self.clock)
        return code, output.getvalue(), error.getvalue()

    def test_returns_available_options_without_private_calendar_or_occurrences(self):
        code, output, error = self.invoke(ARGS)
        self.assertEqual((code, error), (0, ''))
        value = json.loads(output)['result']
        self.assertEqual(value['count'], 3)
        self.assertEqual(value['slots'][0]['start'], '2030-06-19T10:00:00-03:00')
        self.assertNotIn('PrivateMixedOccurrence', output)
        self.assertNotIn('private-calendar@example.invalid', output)

    def test_counts_only_omits_slots(self):
        code, output, error = self.invoke(ARGS + ['--counts-only'])
        self.assertEqual((code, error), (0, ''))
        value = json.loads(output)
        self.assertTrue(value['counts_only'])
        self.assertNotIn('slots', value['result'])
        self.assertEqual(value['result']['count'], 3)

    def test_duration_and_buffer_have_strict_cli_numeric_format(self):
        for value in ['True', '30.5', '-1', PRIVATE]:
            args = ARGS[:-1] + [value]
            code, output, error = self.invoke(args)
            self.assertEqual((code, output), (1, ''))
            self.assertEqual(json.loads(error)['error']['code'], 'invalid_request')
            self.assertNotIn(PRIVATE, error)
        self.assertEqual(self.gateway.opened, 0)

    def test_buffer_is_applied_without_altering_event(self):
        original = self.gateway.events
        code, output, error = self.invoke(ARGS + ['--buffer-minutes', '15'])
        self.assertEqual((code, error), (0, ''))
        self.assertEqual(json.loads(output)['result']['slots'][0]['start'], '2030-06-19T10:15:00-03:00')
        self.assertEqual(self.gateway.events, original)

    def test_source_error_is_sanitized_without_empty_success(self):
        self.gateway.failure = RuntimeError(PRIVATE)
        code, output, error = self.invoke(ARGS)
        self.assertEqual((code, output), (1, ''))
        self.assertEqual(json.loads(error)['error']['code'], 'availability_collection_failed')
        self.assertNotIn(PRIVATE, error)

    def test_setup_and_limit_have_distinct_codes(self):
        for error, code in [(AvailabilitySetupError(), 'calendar_setup_unavailable'),
                            (AvailabilityLimitError(), 'availability_limit_exceeded')]:
            self.gateway.failure = error
            status, output, stderr = self.invoke(ARGS)
            self.assertEqual((status, output), (1, ''))
            self.assertEqual(json.loads(stderr)['error']['code'], code)

    def test_no_future_option_is_complete_success(self):
        self.clock = lambda: datetime(2030, 6, 20, tzinfo=timezone.utc)
        status, output, error = self.invoke(ARGS)
        self.assertEqual((status, error), (0, ''))
        self.assertEqual(json.loads(output)['result']['slots'], [])

    def test_default_gateway_factory_is_replaceable(self):
        output = StringIO()
        with patch('app.find_free_slots.default_availability_gateway', self.gateway.factory), redirect_stdout(output):
            self.assertEqual(main(ARGS, clock=self.clock), 0)
        self.assertEqual(self.gateway.opened, 1)
