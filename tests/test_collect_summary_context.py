"""CLI de coleta: projeção agregada e erros seguros com gateway fictício."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import unittest
from unittest.mock import patch

from app.collect_summary_context import main
from app.services.summary_context import SummaryLimitError, SummarySetupError
from test_summary_context_api import Gateway, PRIVATE


class CollectSummaryContextTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.clock = lambda: datetime(2030, 6, 19, 12, tzinfo=timezone.utc)

    def invoke(self, args):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(args, gateway_factory=self.gateway.factory, clock=self.clock)
        return code, output.getvalue(), error.getvalue()

    def test_explicit_week_returns_projected_context(self):
        code, output, error = self.invoke(['--period', 'week', '--date', '2030-06-19'])
        self.assertEqual((code, error), (0, ''))
        data = json.loads(output)
        self.assertEqual(data['context']['totals']['events'], 1)
        self.assertEqual(data['context']['events'][0]['title'], 'Entrevista fictícia')
        for private in ['private-calendar@example.invalid', 'PrivateEventMixedID', 'https://private.invalid/link']:
            self.assertNotIn(private, output)

    def test_counts_only_removes_all_event_details(self):
        code, output, error = self.invoke(['--period', 'month', '--date', '2030-06-19', '--counts-only'])
        data = json.loads(output)
        self.assertEqual((code, error), (0, ''))
        self.assertTrue(data['counts_only'])
        self.assertNotIn('events', data['context'])
        self.assertNotIn('Entrevista fict', output)
        self.assertEqual(data['context']['totals']['events'], 1)

    def test_missing_date_uses_calendar_zone_clock(self):
        code, output, _ = self.invoke(['--period', 'month', '--counts-only'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['context']['period']['reference_date'], '2030-06-19')

    def test_invalid_input_does_not_open_gateway(self):
        for args in [['--period', 'day'], ['--period', 'week', '--date', PRIVATE], ['--period', 'month', '--date', '2030-02-30']]:
            code, output, error = self.invoke(args)
            self.assertEqual((code, output), (1, ''))
            self.assertEqual(json.loads(error)['error']['code'], 'invalid_request')
            self.assertNotIn(PRIVATE, error)
        self.assertEqual(self.gateway.opened, 0)

    def test_provider_failure_is_sanitized(self):
        self.gateway.failure = RuntimeError(PRIVATE)
        code, output, error = self.invoke(['--period', 'week'])
        self.assertEqual((code, output), (1, ''))
        self.assertEqual(json.loads(error)['error']['code'], 'agenda_collection_failed')
        self.assertNotIn(PRIVATE, error)

    def test_setup_and_limit_have_distinct_safe_codes(self):
        for failure, expected in [(SummarySetupError(), 'calendar_setup_unavailable'),
                                  (SummaryLimitError(), 'agenda_context_limit_exceeded')]:
            self.gateway.failure = failure
            code, output, error = self.invoke(['--period', 'week'])
            self.assertEqual((code, output), (1, ''))
            self.assertEqual(json.loads(error)['error']['code'], expected)
            self.assertNotIn(PRIVATE, error)

    def test_default_factory_does_not_create_llm(self):
        output = StringIO()
        with patch('app.collect_summary_context.default_summary_calendar_gateway', self.gateway.factory), redirect_stdout(output):
            self.assertEqual(main(['--period', 'week', '--date', '2030-06-19'], clock=self.clock), 0)
        self.assertEqual(self.gateway.opened, 1)
