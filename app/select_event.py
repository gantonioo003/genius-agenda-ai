"""Selecionar um evento ou pedir esclarecimento; não altera nem exclui eventos."""

import argparse
from dataclasses import asdict
import json
import sys

from googleapiclient.errors import HttpError

from app.cli_errors import (
    AUTH_ERRORS, LOCAL_STORE_ERRORS, NETWORK_ERRORS,
    authentication_failure, local_store_failure,
)
from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client
from app.list_events import parse_date
from app.services.agenda import day_period, month_period, week_period
from app.services.event_selection import EventSelectionService, validate_selection
from app.services.interviews import CalendarOperationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Identificar um evento sem modificar a agenda.")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--event-id", help="ID completo retornado pelo Google; preservar caixa")
    selector.add_argument("--title", help="Trecho do título; requer --date")
    parser.add_argument("--date", dest="day", type=parse_date, help="Data de referência AAAA-MM-DD")
    parser.add_argument("--period", choices=("day", "week", "month"),
                        help="Período da busca por título; padrão: day")
    parser.add_argument("--timezone", dest="time_zone", default="America/Recife", help="Fuso IANA")
    args = parser.parse_args(argv)
    try:
        period = None
        if args.event_id is not None:
            if args.day is not None or args.period is not None:
                raise ValueError("Use --event-id sem --date ou --period; --timezone é opcional.")
        else:
            if args.day is None:
                raise ValueError("Informe --date para buscar pelo título.")
            builders = {"day": day_period, "week": week_period, "month": month_period}
            period = builders[args.period or "day"](args.day, args.time_zone)
        validate_selection(event_id=args.event_id, title=args.title,
                           period=period, time_zone=args.time_zone)
        with calendar_client() as client:
            result = EventSelectionService(GoogleCalendarGateway(client)).select(
                event_id=args.event_id, title=args.title, period=period, time_zone=args.time_zone,
            )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou a identificação (HTTP {exc.resp.status}).", file=sys.stderr)
    except AUTH_ERRORS:
        print(authentication_failure(), file=sys.stderr)
    except LOCAL_STORE_ERRORS:
        print(local_store_failure(), file=sys.stderr)
    except NETWORK_ERRORS:
        print("Não foi possível consultar o Google. Confira a conexão e a configuração.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
