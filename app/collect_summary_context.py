"""Coletar contexto factual da agenda, sem IA ou escrita de compromissos."""

import argparse
import json
import sys

from app.services.summary_context import (
    SummaryContextService, SummaryInputError, SummaryLimitError, SummarySetupError, validate_input,
)
from app.summary_calendar import default_summary_calendar_gateway


def main(argv=None, *, gateway_factory=None, clock=None):
    parser = argparse.ArgumentParser(description='Coletar fatos de semana/mês; não chama IA nem envia mensagens.')
    parser.add_argument('--period', required=True, help='week ou month; período civil no fuso da agenda.')
    parser.add_argument('--date', dest='reference_date', help='Referência AAAA-MM-DD; ausente usa hoje no fuso da agenda.')
    parser.add_argument('--counts-only', action='store_true', help='Omitir detalhes e títulos dos eventos da saída.')
    args = parser.parse_args(argv)
    try:
        validate_input(args.period, args.reference_date)
        options = {} if clock is None else {'clock': clock}
        service = SummaryContextService(gateway_factory or default_summary_calendar_gateway, **options)
        context = service.build(period=args.period, reference_date=args.reference_date)
        if args.counts_only:
            context = {key: context[key] for key in (
                'schema_version', 'period', 'snapshot', 'totals', 'days', 'details', 'execution_allowed')}
        print(json.dumps({'status': 'ok', 'counts_only': args.counts_only, 'context': context}, ensure_ascii=True, indent=2))
        return 0
    except SummaryInputError:
        code = 'invalid_request'
    except SummarySetupError:
        code = 'calendar_setup_unavailable'
    except SummaryLimitError:
        code = 'agenda_context_limit_exceeded'
    except Exception:
        code = 'agenda_collection_failed'
    print(json.dumps({'status': 'failed', 'error': {'code': code}, 'context': None}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
