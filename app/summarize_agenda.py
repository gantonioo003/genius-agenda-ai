"""Gerar resumo de semana/mês com organização por IA e alternativa factual."""

import argparse
import json
import sys

from app.language_model import configured_language_model
from app.services.agenda_summary import AgendaSummaryService
from app.services.summary_context import (
    SummaryCollectionError, SummaryContextService, SummaryInputError, SummaryLimitError,
    SummarySetupError, validate_input,
)
from app.summary_calendar import default_summary_calendar_gateway


def main(argv=None, *, gateway_factory=None, model_factory=None, config=None, clock=None):
    parser = argparse.ArgumentParser(description='Resumir a agenda sem alterar eventos ou enviar mensagens.')
    parser.add_argument('--period', required=True, help='week ou month, no fuso da agenda autorizada.')
    parser.add_argument('--date', dest='reference_date', help='Referência AAAA-MM-DD; ausente usa hoje na agenda.')
    parser.add_argument('--metadata-only', action='store_true', help='Omitir o texto do resumo da saída; conservar agregados/modo.')
    args = parser.parse_args(argv)
    try:
        validate_input(args.period, args.reference_date)
        if config is None:
            from app.settings import settings
            config = settings
        clock_options = {} if clock is None else {'clock': clock}
        context = SummaryContextService(gateway_factory or default_summary_calendar_gateway, **clock_options)
        factory = model_factory if model_factory is not None else lambda: configured_language_model(config)
        result = AgendaSummaryService(context, factory, max_input_chars=config.llm_max_input_chars).summarize(
            period=args.period, reference_date=args.reference_date)
        if args.metadata_only:
            result = {**{key: result[key] for key in (
                'schema_version', 'period', 'snapshot', 'totals', 'details', 'generation', 'execution_allowed')},
                'summary': {key: result['summary'][key] for key in ('mode', 'reason')}}
        print(json.dumps({'status': 'ok', 'metadata_only': args.metadata_only, 'result': result}, ensure_ascii=True, indent=2))
        return 0
    except SummaryInputError:
        code = 'invalid_request'
    except SummarySetupError:
        code = 'calendar_setup_unavailable'
    except SummaryLimitError:
        code = 'agenda_context_limit_exceeded'
    except SummaryCollectionError:
        code = 'agenda_collection_failed'
    except Exception:
        code = 'agenda_summary_failed'
    print(json.dumps({'status': 'failed', 'error': {'code': code}, 'result': None}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
