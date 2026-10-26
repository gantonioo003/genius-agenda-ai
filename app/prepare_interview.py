"""Exibir briefing e checklist factuais sem alterar eventos ou chamar IA."""

import argparse
import json
import sys

from app.briefing_calendar import default_briefing_gateway
from app.services.interview_briefing import (
    BriefingCollectionError, BriefingInputError, BriefingSetupError, InterviewBriefingService,
)


def main(argv=None, *, gateway_factory=None):
    parser = argparse.ArgumentParser(description='Preparar briefing e checklist de uma entrevista pelo ID exato.')
    parser.add_argument('--event-id')
    parser.add_argument('--context', default='', help='Contexto opcional fornecido pelo usuário (até 1200 caracteres).')
    parser.add_argument('--text-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = InterviewBriefingService(gateway_factory or default_briefing_gateway).prepare(
            event_id=args.event_id, user_context=args.context)
        if args.text_only and callable(getattr(sys.stdout, 'reconfigure', None)):
            sys.stdout.reconfigure(encoding='utf-8')
        print(result['text'] if args.text_only else json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except BriefingInputError:
        code = 'invalid_request'
    except BriefingSetupError:
        code = 'calendar_setup_unavailable'
    except BriefingCollectionError:
        code = 'briefing_collection_failed'
    print(json.dumps({'error': code, 'briefing_created': False}), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
