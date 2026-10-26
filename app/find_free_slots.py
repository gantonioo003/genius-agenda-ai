"""Procurar horários livres, sem modelo, escrita de evento ou reserva."""

import argparse
import json
import re
import sys

from app.availability_calendar import default_availability_gateway
from app.services.availability import (
    AvailabilityService, AvailabilityInputError,
    AvailabilityLimitError, AvailabilitySetupError, validate_request,
)


def _integer(value, field):
    if type(value) is not str or re.fullmatch(r'[0-9]{1,4}', value, re.ASCII) is None:
        raise AvailabilityInputError((field,))
    return int(value)


def main(argv=None, *, gateway_factory=None, clock=None):
    parser = argparse.ArgumentParser(description='Calcular opções livres na agenda, sem criar reservas ou enviar mensagens.')
    for field in ['date-from', 'date-to', 'window-start', 'window-end', 'duration-minutes']:
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--buffer-minutes', default='0', help='Margem em minutos em relação aos outros compromissos; padrão zero.')
    parser.add_argument('--counts-only', action='store_true', help='Omitir a lista de opções e conservar metadados e contagens.')
    args = parser.parse_args(argv)
    try:
        request = {name: getattr(args, name) for name in ['date_from', 'date_to', 'window_start', 'window_end']}
        request['duration_minutes'] = _integer(args.duration_minutes, 'duration_minutes')
        request['buffer_minutes'] = _integer(args.buffer_minutes, 'buffer_minutes')
        validate_request(**request)
        options = {} if clock is None else {'clock': clock}
        result = AvailabilityService(gateway_factory or default_availability_gateway, **options).find(**request)
        if args.counts_only:
            result = {key: result[key] for key in ('schema_version', 'request', 'period', 'snapshot',
                'count', 'limit', 'more_available', 'scope', 'reservation_created', 'execution_allowed')}
        print(json.dumps({'status': 'ok', 'counts_only': args.counts_only, 'result': result}, ensure_ascii=True, indent=2))
        return 0
    except AvailabilityInputError:
        code = 'invalid_request'
    except AvailabilitySetupError:
        code = 'calendar_setup_unavailable'
    except AvailabilityLimitError:
        code = 'availability_limit_exceeded'
    except Exception:
        code = 'availability_collection_failed'
    print(json.dumps({'status': 'failed', 'error': {'code': code}, 'result': None}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
