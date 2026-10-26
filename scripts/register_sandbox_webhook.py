"""Registrar o callback restrito do sandbox, sem exibir chave ou URL secreta."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import requests


def callback_url(origin: str, config: dict) -> str:
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com', parsed.netloc)
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Informe somente a origem HTTPS do tunel de teste.')
    token = config.get('callback_token')
    if not isinstance(token, str) or re.fullmatch(r'[A-Za-z0-9_-]{48,128}', token) is None:
        raise ValueError('Configuracao privada do callback invalida.')
    return f'https://{parsed.netloc}/webhooks/360dialog/{token}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if not args.apply:
        parser.error('Use --apply para registrar o webhook de teste.')
    root = args.project_root.resolve()
    config = json.loads((root / 'secrets/whatsapp-sandbox-local.json').read_text(encoding='utf-8'))
    target = callback_url(args.origin, config)
    sys.path.insert(0, str(root))
    from app.settings import Settings
    key = Settings().d360_api_key
    if key is None:
        raise ValueError('Configure D360_API_KEY no .env privado.')
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            'https://waba-sandbox.360dialog.io/v1/configs/webhook',
            headers={'D360-API-KEY': key.get_secret_value()},
            json={'url': target}, timeout=(5, 15), allow_redirects=False,
        )
    if response.status_code != 200:
        raise RuntimeError(f'O sandbox nao confirmou o cadastro: HTTP {response.status_code}.')
    reply = response.json()
    if not isinstance(reply, dict) or reply.get('url') != target:
        raise RuntimeError('Resposta inesperada; confira o callback antes de repetir o cadastro.')
    registration = {'callback_url': target, 'origin': args.origin.rstrip('/'),
                    'registered_at_utc': datetime.now(timezone.utc).isoformat(),
                    'provider_http_status': 200}
    (root / 'secrets/whatsapp-sandbox-registration.json').write_text(
        json.dumps(registration, indent=2) + '\n', encoding='utf-8')
    print('Webhook HTTPS registrado no sandbox: HTTP 200 e endereco confirmado. Nenhuma mensagem enviada.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
        print(str(error) if isinstance(error, RuntimeError) else 'Cadastro interrompido; confira a configuracao e a conexao sem expor credenciais.', file=sys.stderr)
        raise SystemExit(1)
