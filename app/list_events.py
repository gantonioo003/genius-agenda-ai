"""Consultar eventos por dia, semana ou mês, somente leitura no Google Calendar."""

import argparse
from dataclasses import asdict
from datetime import date
import json
import sys

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
import httplib2

from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client
from app.services.agenda import AgendaService, day_period, month_period, week_period
from app.services.interviews import CalendarOperationError


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError("Use uma data válida no formato AAAA-MM-DD.") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consultar eventos por dia, semana ou mês.")
    parser.add_argument("--date", dest="day", type=parse_date, required=True,
                        help="Data de referência no formato AAAA-MM-DD")
    parser.add_argument("--period", choices=("day", "week", "month"), default="day",
                        help="day: dia; week: segunda a domingo; month: mês da referência")
    parser.add_argument("--timezone", dest="time_zone", default="America/Recife", help="Fuso IANA")
    args = parser.parse_args(argv)
    try:
        builders = {"day": day_period, "week": week_period, "month": month_period}
        builders[args.period](args.day, args.time_zone)  # Validar antes de abrir o conector Google.
        with calendar_client() as client:
            service = AgendaService(GoogleCalendarGateway(client))
            queries = {"day": service.list_day, "week": service.list_week,
                       "month": service.list_month}
            result = queries[args.period](args.day, time_zone=args.time_zone)
        print(json.dumps({
            "status": "ok", **asdict(result), "count": len(result.events),
        }, ensure_ascii=True, indent=2))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou a consulta (HTTP {exc.resp.status}).", file=sys.stderr)
    except (OSError, httplib2.HttpLib2Error, TransportError):
        print("Não foi possível acessar o Google. Confira a conexão e a configuração.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
