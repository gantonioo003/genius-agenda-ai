"""Consulta o webhook local sem colocar sua chave no terminal ou nos argumentos."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="AAAA-MM-DD")
    parser.add_argument("--period", choices=("day", "week", "month"), default="day")
    parser.add_argument("--time-zone", default="America/Recife")
    parser.add_argument("--text-only", action="store_true", help="Mostrar somente a mensagem preparada para o usuario")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    config = json.loads((args.project_root / "secrets/n8n-local.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("webhook_token"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", config["webhook_token"]):
        raise ValueError("Configuracao privada invalida")
    request = Request("http://127.0.0.1:5678/webhook/genius-agenda/consultar", method="POST",
                      data=json.dumps({"date": args.date, "period": args.period, "time_zone": args.time_zone}).encode(),
                      headers={"Content-Type": "application/json", "X-Genius-Webhook-Key": config["webhook_token"]})
    try:
        response = build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=40)
    except HTTPError as error:
        response = error
    except URLError:
        print("Nao foi possivel acessar o n8n. Inicie o Docker e docker compose up -d.", file=sys.stderr)
        return 1
    with response:
        if 300 <= response.status < 400:
            print("Redirecionamento bloqueado; a chave nao foi encaminhada.", file=sys.stderr)
            return 1
        print(f"HTTP {response.status}")
        payload = json.load(response)
        print(payload["presentation"]["text"] if args.text_only else json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if response.status == 200 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError):
        print("Confira a configuracao privada do webhook e a resposta JSON do n8n.", file=sys.stderr)
        raise SystemExit(1)
