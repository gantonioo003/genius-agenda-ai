"""Consultar ocupação de um intervalo, sem criar reserva ou escrever na agenda."""

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
from app.reschedule_event import parse_timestamp
from app.services.agenda import CalendarPeriod
from app.services.event_selection import validate_event_id
from app.services.interviews import CalendarOperationError
from app.services.time_rules import validate_event_interval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conferir conflitos; consulta sem escrita no Google.")
    parser.add_argument("--start", required=True, type=parse_timestamp)
    parser.add_argument("--end", required=True, type=parse_timestamp)
    parser.add_argument("--timezone", default="America/Recife")
    parser.add_argument("--exclude-event-id", help="ID exato do próprio evento em uma consulta de reagendamento")
    args = parser.parse_args(argv)
    try:
        validate_event_interval(args.start, args.end, args.timezone)
        if args.exclude_event_id is not None:
            validate_event_id(args.exclude_event_id)
        with calendar_client() as client:
            result = GoogleCalendarGateway(client).check_conflicts(
                CalendarPeriod(args.start, args.end, args.timezone), exclude_event_id=args.exclude_event_id,
            )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou a consulta de conflitos (HTTP {exc.resp.status}).", file=sys.stderr)
    except AUTH_ERRORS:
        print(authentication_failure(), file=sys.stderr)
    except LOCAL_STORE_ERRORS:
        print(local_store_failure(), file=sys.stderr)
    except NETWORK_ERRORS:
        print("Não foi possível conferir a disponibilidade. Tente consultar novamente.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
