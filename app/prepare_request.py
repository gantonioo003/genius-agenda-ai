"""Preparar um pedido JSON e exibir perguntas; nunca grava na agenda."""

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import json
from pathlib import Path
import sys

from googleapiclient.errors import HttpError

from app.cli_errors import (
    AUTH_ERRORS, LOCAL_STORE_ERRORS, NETWORK_ERRORS,
    authentication_failure, local_store_failure,
)
from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client
from app.services.interviews import CalendarOperationError
from app.services.request_preparation import RequestPreparationService


class _DeferredCalendar:
    def __init__(self, stack):
        self.stack = stack
        self.gateway = None

    def _get_gateway(self):
        if self.gateway is None:
            client = self.stack.enter_context(calendar_client())
            self.gateway = GoogleCalendarGateway(client)
        return self.gateway

    def list_events(self, period):
        return self._get_gateway().list_events(period)

    def get_event(self, event_id, *, time_zone):
        return self._get_gateway().get_event(event_id, time_zone=time_zone)

    def search_events_by_title(self, title, *, time_zone):
        return self._get_gateway().search_events_by_title(title, time_zone=time_zone)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("O JSON contém um campo repetido; informe cada campo uma única vez.")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preparar dados e perguntas a partir de JSON; sem escrita no Google.")
    parser.add_argument("--request-file", required=True, type=Path, help="Arquivo JSON com os dados estruturados do pedido")
    parser.add_argument("--timezone", default="America/Recife", help="Fuso padrão configurado para a demonstração")
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request_file.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
        with ExitStack() as stack:
            result = RequestPreparationService(_DeferredCalendar(stack), default_time_zone=args.timezone).prepare(request)
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou a consulta (HTTP {exc.resp.status}). Nenhuma ação foi executada.", file=sys.stderr)
    except AUTH_ERRORS:
        print(authentication_failure(), file=sys.stderr)
    except LOCAL_STORE_ERRORS:
        print(local_store_failure(), file=sys.stderr)
    except (*NETWORK_ERRORS, UnicodeError):
        print("Não foi possível preparar o pedido. Confira o arquivo, a conexão e a configuração.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
