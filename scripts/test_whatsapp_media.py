"""Prévia sem rede ou ensaio explícito de imagem/template ao WhatsApp privado."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sandbox_media_probe import ProbeRunError, preview, run_probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('image', 'template'), required=True)
    parser.add_argument('--operation-id', required=True, help='UUID v4 canônico estável para este ensaio')
    parser.add_argument('--execute', action='store_true', help='Enviar uma vez pela 360dialog ao WhatsApp já autorizado')
    args = parser.parse_args()
    planned = preview(args.kind, args.operation_id)
    if not args.execute:
        print(json.dumps(planned, ensure_ascii=True))
        return 0
    from app.sandbox_webhook import load_allowed_sender
    from app.settings import Settings
    from app.whatsapp_probe_ledger import ProbeLedger
    from app.whatsapp_sandbox_sender import SandboxSender
    try:
        config = Settings()
        if config.d360_api_key is None:
            raise ProbeRunError()
        recipient = load_allowed_sender((ROOT / 'secrets/whatsapp-sandbox-security.json').read_bytes())
        result = run_probe(args.kind, args.operation_id, recipient=recipient,
            sender=SandboxSender(config.d360_api_key), ledger=ProbeLedger(ROOT / 'data/whatsapp-media-probes.sqlite3'))
        print(json.dumps({'operation_id': args.operation_id, **asdict(result)}, ensure_ascii=True))
        return 0
    except Exception as error:
        print(json.dumps({'operation_id': args.operation_id, 'case_name': planned['case_name'],
            'state': 'uncertain' if getattr(error, 'uncertain', False) else 'not_started',
            'http_status': getattr(error, 'http_status', None), 'retryable': False,
            'message': 'Ensaio nao confirmado; preserve o registro e confira o resultado antes de repetir.'}))
        return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ProbeRunError:
        print('Dados de ensaio invalidos; nenhum envio iniciado.', file=sys.stderr)
        raise SystemExit(2)
