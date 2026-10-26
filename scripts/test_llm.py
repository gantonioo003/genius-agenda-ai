"""Teste explícito da conexão com modelo; prévia padrão sem rede ou dados reais."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_MESSAGE = "Quero marcar uma entrevista fictícia, mas ainda não defini a data e o horário. O que preciso informar?"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Fazer uma chamada real à API do modelo usando apenas o pedido fictício fixo.')
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'mode': 'preview', 'message': TEST_MESSAGE, 'network_requests': 0, 'calendar_operations': 0, 'whatsapp_sends': 0, 'execute_command': '.\\.venv\\Scripts\\python.exe scripts/test_llm.py --execute'}, ensure_ascii=False))
        return 0
    try:
        from app.llm import ModelError
        from app.services.agent_preview import default_agent_preview_service
        from app.settings import Settings
        config = Settings()
        result = default_agent_preview_service(config).preview(TEST_MESSAGE)
    except ModelError as error:
        # Códigos fechados do módulo, sem mostrar a exceção/transporte/credenciais.
        print(json.dumps({'status': 'failed', 'code': error.code, 'execution_allowed': False}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({'status': 'failed', 'code': 'llm_unavailable', 'execution_allowed': False}, ensure_ascii=False))
        return 1
    print(json.dumps({'status': 'succeeded', 'model': result.model, 'reply': result.text, 'input_tokens': result.input_tokens, 'output_tokens': result.output_tokens, 'execution_allowed': False, 'calendar_operations': 0, 'whatsapp_sends': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
