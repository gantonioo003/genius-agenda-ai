"""Prévia e execução explícita do reagendamento de um evento pelo ID."""

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import sys

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
import httplib2

from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client
from app.services.interviews import CalendarOperationError
from app.services.rescheduling import RescheduleService, ScheduleChange, validate_schedule_change


def parse_timestamp(value: str) -> datetime:
    try:
        if "T" not in value:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Use data e hora ISO com deslocamento, como 2026-10-23T16:00:00-03:00."
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reagendar pelo ID; padrão: prévia somente de leitura.")
    parser.add_argument("--event-id", required=True, help="ID completo do evento identificado")
    parser.add_argument("--start", required=True, type=parse_timestamp, help="Novo início ISO com offset")
    parser.add_argument("--end", required=True, type=parse_timestamp, help="Novo fim ISO com offset")
    parser.add_argument("--timezone", dest="time_zone", default="America/Recife", help="Fuso IANA")
    parser.add_argument("--execute", action="store_true", help="Aplicar a alteração no Google Calendar")
    args = parser.parse_args(argv)
    try:
        change = ScheduleChange(args.start, args.end, args.time_zone)
        validate_schedule_change(args.event_id, change)
        with calendar_client() as client:
            result = RescheduleService(GoogleCalendarGateway(client)).reschedule(
                args.event_id, change, execute=args.execute,
            )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou o reagendamento (HTTP {exc.resp.status}).", file=sys.stderr)
    except (OSError, httplib2.HttpLib2Error, TransportError):
        print("Não foi possível concluir a operação. Consulte o evento pelo ID antes de repetir.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
