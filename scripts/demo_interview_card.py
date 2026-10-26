"""Renderizar um card fictício e, opcionalmente, enviar uma única demonstração."""
import argparse
from dataclasses import asdict
from datetime import date, datetime, time
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.interview_card import CardRenderError, interview_card_caption, render_interview_card


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--start', type=time.fromisoformat, default=time(17, 0))
    parser.add_argument('--end', type=time.fromisoformat, default=time(17, 30))
    parser.add_argument('--operation-id', required=True, help='UUID v4 estável; não trocar ao repetir')
    parser.add_argument('--image-link', help='URL HTTPS do PNG fictício já acessível')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    zone = ZoneInfo('America/Recife')
    start = datetime.combine(args.date, args.start, tzinfo=zone)
    end = datetime.combine(args.date, args.end, tzinfo=zone)
    title = 'Entrevista técnica — Genius Agenda AI'
    caption = interview_card_caption(title, start, end, 'America/Recife', demonstration=True)
    caption += '\nNenhum compromisso foi criado na agenda.'
    output = ROOT / 'data/cards/interview-demo-v1.png'
    rendered = False
    try:
        render_interview_card(title, start, end, 'America/Recife', output, demonstration=True)
        rendered = True
    except CardRenderError:
        pass
    if not args.execute:
        print(json.dumps({'state': 'preview', 'rendered': rendered,
                          'file': str(output) if rendered else None,
                          'text': caption, 'network_requests': 0}, ensure_ascii=True))
        return 0
    from app.interview_card_delivery import send_interview_card
    from app.sandbox_webhook import load_allowed_sender
    from app.settings import Settings
    from app.whatsapp_probe_ledger import ProbeLedger
    from app.whatsapp_sandbox_sender import SandboxSender
    config = Settings()
    recipient = load_allowed_sender((ROOT / 'secrets/whatsapp-sandbox-security.json').read_bytes())
    if config.d360_api_key is None:
        raise ValueError('Credencial de sandbox não configurada.')
    result = send_interview_card(args.operation_id, recipient=recipient,
        image_link=args.image_link if rendered else None, text=caption,
        sender=SandboxSender(config.d360_api_key),
        ledger=ProbeLedger(ROOT / 'data/whatsapp-media-probes.sqlite3'))
    print(json.dumps({'operation_id': args.operation_id, 'rendered': rendered,
                      **asdict(result), 'human_receipt_confirmed': False}))
    return 0 if result.state == 'accepted' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({'state': 'not_confirmed', 'retryable': False,
                          'message': 'Confira o registro local antes de repetir.'}))
        raise SystemExit(1)
