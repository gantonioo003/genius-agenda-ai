"""Disponibilidade por ASGI com Bearer e fonte de ocupação fictícia."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
from threading import get_ident
import unittest
from unittest.mock import patch

from app.main import create_app
from app.services.availability import (
    AvailabilityBinding, AvailabilityEvent, AvailabilitySnapshot,
    AvailabilityCollectionError, AvailabilitySetupError, AvailabilityLimitError,
)
from app.settings import Settings
from test_api_security import InjectedDatabase, TOKEN, WRONG_TOKEN, invoke_asgi


URL = '/api/v1/agenda/availability'
HEADERS = ((b'authorization', f'Bearer {TOKEN}'.encode()), (b'content-type', b'application/json'),
           (b'x-request-id', b'availability-test-001'))
PRIVATE = 'PRIVATE_AVAILABILITY_DIAGNOSTIC'
REQUEST = {'date_from': '2030-06-19', 'date_to': '2030-06-19', 'window_start': '09:00',
           'window_end': '12:00', 'duration_minutes': 30}


class OccupancyGateway:
    def __init__(self):
        self.selected = AvailabilityBinding('private-calendar@example.invalid', 'America/Recife')
        self.events = (AvailabilityEvent('PrivateMixedOccurrence', datetime(2030, 6, 19, 12, tzinfo=timezone.utc),
            datetime(2030, 6, 19, 13, tzinfo=timezone.utc), 'confirmed', 'opaque', False),)
        self.opened = 0
        self.closed = 0
        self.periods = []
        self.failure = None
        self.incomplete = False
        self.thread = None

    @contextmanager
    def factory(self):
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1

    def binding(self):
        self.thread = get_ident()
        if self.failure:
            raise self.failure
        return self.selected

    def list_occupancy(self, period, *, expected_binding):
        assert expected_binding == self.selected
        self.periods.append(period)
        return AvailabilitySnapshot(self.selected, period, self.events, not self.incomplete, 1, len(self.events))


class AvailabilityApiTests(unittest.TestCase):
    def setUp(self):
        self.gateway = OccupancyGateway()
        self.app = create_app(config=Settings(_env_file=None, api_auth_token=TOKEN, app_env='test'),
            db=InjectedDatabase(), availability_gateway_factory=self.gateway.factory)

    def request(self, value=None, *, raw=None, headers=HEADERS, query=b''):
        body = json.dumps(REQUEST if value is None else value).encode() if raw is None else raw
        response = asyncio.run(invoke_asgi(self.app, URL, method='POST', headers=headers, body=body, query=query))
        text = json.dumps(response, ensure_ascii=False)
        for private in [TOKEN, WRONG_TOKEN, PRIVATE, 'private-calendar@example.invalid', 'PrivateMixedOccurrence']:
            self.assertNotIn(private, text)
        return response

    def test_success_is_no_store_read_only_and_only_returns_available_options(self):
        status, headers, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['x-request-id'], 'availability-test-001')
        self.assertEqual(value['action'], 'find_free_slots')
        self.assertEqual(value['mutation_state'], 'not_applicable')
        self.assertIsNone(value['operation_id'])
        data = value['data']
        self.assertEqual(data['count'], 3)
        self.assertEqual(data['slots'][0]['start'], '2030-06-19T10:00:00-03:00')
        self.assertTrue(data['more_available'])
        self.assertFalse(data['execution_allowed'])
        self.assertFalse(data['reservation_created'])
        self.assertEqual(data['scope'], 'authorized_calendar_only')
        self.assertEqual(self.gateway.opened, self.gateway.closed)

    def test_no_auth_even_with_bad_body_never_opens_gateway(self):
        self.assertEqual(self.request(raw=b'PRIVATE BAD JSON', headers=())[0], 401)
        self.assertEqual(self.gateway.opened, 0)

    def test_wrong_or_duplicate_bearer_is_rejected(self):
        for headers in [((b'authorization', f'Bearer {WRONG_TOKEN}'.encode()),), HEADERS + (HEADERS[0],)]:
            self.assertEqual(self.request(headers=headers)[0], 401)
        self.assertEqual(self.gateway.opened, 0)

    def test_invalid_correlation_is_rejected_before_source(self):
        self.assertEqual(self.request(headers=HEADERS[:2] + ((b'x-request-id', b'bad\nvalue'),))[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_query_and_wrong_or_duplicate_content_type_are_invalid(self):
        status, _, value = self.request(query=b'date_from=2030-06-19')
        self.assertEqual(status, 422)
        self.assertEqual(value['error']['fields'], ['query'])
        for headers in [(HEADERS[0],), (HEADERS[0], (b'content-type', b'text/plain')),
                        HEADERS + ((b'content-type', b'application/json'),)]:
            self.assertEqual(self.request(headers=headers)[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_duplicate_nonobject_invalid_utf8_and_large_body_are_rejected(self):
        for body in [b'[]', b'null', b'{}', b'{"duration_minutes":NaN}', b'\xff', b'x' * 4097,
                     b'{"date_from":"2030-06-19","date_from":"2030-06-20"}']:
            self.assertEqual(self.request(raw=body)[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_missing_fields_or_calendar_exclusion_or_limit_overrides_are_rejected(self):
        for key in ['calendar_id', 'time_zone', 'exclude_event_id', 'limit', 'step_minutes', 'conversation_id', PRIVATE]:
            self.assertEqual(self.request({**REQUEST, key: 'unsupported'})[0], 422)
        value = {key: item for key, item in REQUEST.items() if key != 'duration_minutes'}
        self.assertEqual(self.request(value)[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_invalid_dates_order_horizon_and_times_fail_before_gateway(self):
        for changes in [{'date_from': '2030-02-30'}, {'date_to': '2030-06-18'}, {'date_to': '2030-07-20'},
                        {'window_start': '9:00'}, {'window_start': '24:00'}, {'window_end': '24:01'},
                        {'window_start': '18:00', 'window_end': '09:00'}]:
            self.assertEqual(self.request({**REQUEST, **changes})[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_strict_integer_duration_and_buffer_validation(self):
        for changes in [{'duration_minutes': True}, {'duration_minutes': '30'}, {'duration_minutes': 30.0},
                        {'duration_minutes': 0}, {'duration_minutes': 1441}, {'buffer_minutes': True},
                        {'buffer_minutes': -1}, {'buffer_minutes': 241}]:
            self.assertEqual(self.request({**REQUEST, **changes})[0], 422)
        self.assertEqual(self.gateway.opened, 0)

    def test_end_24_is_supported_without_overnight_input(self):
        self.gateway.events = ()
        status, _, value = self.request({**REQUEST, 'window_start': '23:00', 'window_end': '24:00'})
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['count'], 3)
        self.assertFalse(value['data']['more_available'])

    def test_nonquarter_window_rounds_candidates_forward_to_local_quarter(self):
        self.gateway.events = ()
        status, _, value = self.request({**REQUEST, 'window_start': '09:07', 'window_end': '10:00', 'duration_minutes': 20})
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['slots'][0]['start'], '2030-06-19T09:15:00-03:00')
        self.assertEqual(value['data']['count'], 2)

    def test_calendar_time_zone_is_authoritative(self):
        self.gateway.events = ()
        self.gateway.selected = replace(self.gateway.selected, time_zone='Pacific/Kiritimati')
        status, _, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['period']['time_zone'], 'Pacific/Kiritimati')
        self.assertEqual(value['data']['slots'][0]['start'], '2030-06-19T09:00:00+14:00')

    def test_empty_future_options_is_success_not_global_unavailability(self):
        self.gateway.events = ()
        status, _, value = self.request({**REQUEST, 'date_from': '2000-06-19', 'date_to': '2000-06-19'})
        self.assertEqual(status, 200)
        self.assertEqual(value['data']['slots'], [])
        self.assertEqual(value['data']['count'], 0)
        self.assertTrue(value['data']['snapshot']['complete'])

    def test_source_setup_limit_and_unknown_failure_never_claim_free(self):
        for failure, expected, code in [(AvailabilitySetupError(), 503, 'calendar_setup_unavailable'),
            (AvailabilityLimitError(), 503, 'availability_limit_exceeded'),
            (AvailabilityCollectionError(), 502, 'availability_collection_failed'),
            (RuntimeError(PRIVATE), 502, 'availability_collection_failed')]:
            self.gateway.failure = failure
            status, _, value = self.request()
            self.assertEqual(status, expected)
            self.assertEqual(value['error']['code'], code)
            self.assertIsNone(value['data'])

    def test_incomplete_source_never_returns_three_fake_slots(self):
        self.gateway.incomplete = True
        self.gateway.events = ()
        status, _, value = self.request()
        self.assertEqual(status, 502)
        self.assertIsNone(value['data'])

    def test_ambiguous_window_is_a_safe_input_error_after_binding(self):
        self.gateway.events = ()
        self.gateway.selected = replace(self.gateway.selected, time_zone='America/New_York')
        status, _, value = self.request({**REQUEST, 'date_from': '2030-11-03', 'date_to': '2030-11-03',
            'window_start': '01:00', 'window_end': '03:00'})
        self.assertEqual(status, 422)
        self.assertIn('window_start', value['error']['fields'])
        self.assertEqual(self.gateway.periods, [])

    def test_default_factory_is_lazy_and_no_llm_is_used(self):
        self.app.state.availability_gateway_factory = None
        with patch('app.api_availability.default_availability_gateway', self.gateway.factory), \
                patch('app.language_model.configured_language_model', side_effect=AssertionError('Sem IA.')):
            self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.gateway.opened, 1)

    def test_work_runs_outside_event_loop_and_schema_has_only_supported_fields(self):
        current = get_ident()
        self.assertEqual(self.request()[0], 200)
        self.assertNotEqual(self.gateway.thread, current)
        route = self.app.openapi()['paths'][URL]['post']
        schema = route['requestBody']['content']['application/json']['schema']
        self.assertEqual(set(schema['properties']), set(REQUEST) | {'buffer_minutes'})
        self.assertFalse(schema['additionalProperties'])
        self.assertTrue(route['security'])
