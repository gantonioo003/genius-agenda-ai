"""Prévia e exclusão confirmada de um único evento pelo ID."""

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
from app.services.event_deletion import EventDeletionService, validate_deletion
from app.services.interviews import CalendarOperationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conferir o evento e confirmar sua exclusão pelo ID.")
    parser.add_argument("--event-id", required=True, help="ID completo do evento identificado")
    parser.add_argument("--timezone", dest="time_zone", default="America/Recife", help="Fuso para conferir os horários")
    parser.add_argument("--execute", action="store_true", help="Excluir; exige --confirm")
    parser.add_argument("--confirm", dest="confirmation_token", help="Código exato retornado na prévia")
    args = parser.parse_args(argv)
    try:
        validate_deletion(args.event_id, time_zone=args.time_zone, execute=args.execute,
                          confirmation_token=args.confirmation_token)
        with calendar_client() as client:
            result = EventDeletionService(GoogleCalendarGateway(client)).delete(
                args.event_id, time_zone=args.time_zone, execute=args.execute,
                confirmation_token=args.confirmation_token,
            )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou a exclusão (HTTP {exc.resp.status}).", file=sys.stderr)
    except AUTH_ERRORS:
        print(authentication_failure(writing=args.execute), file=sys.stderr)
    except LOCAL_STORE_ERRORS:
        print(local_store_failure(writing=args.execute), file=sys.stderr)
    except NETWORK_ERRORS:
        print("Não foi possível confirmar a operação. Consulte o evento pelo ID antes de repetir.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
