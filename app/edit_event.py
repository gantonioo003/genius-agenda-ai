"""Prévia e edição explícita de título, descrição e local pelo ID do evento."""

import argparse
from dataclasses import asdict
import json
import sys

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
import httplib2

from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client
from app.services.event_details import DetailsChange, EventDetailsService, validate_details_change
from app.services.interviews import CalendarOperationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Editar informações pelo ID; padrão: prévia sem gravação.")
    parser.add_argument("--event-id", required=True, help="ID completo do evento identificado")
    parser.add_argument("--title", help="Novo título; não pode ficar vazio")
    description = parser.add_mutually_exclusive_group()
    description.add_argument("--description", help="Nova descrição")
    description.add_argument("--clear-description", dest="description", action="store_const", const="",
                             help="Limpar explicitamente a descrição")
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--location", help="Novo local, como texto")
    location.add_argument("--clear-location", dest="location", action="store_const", const="",
                          help="Limpar explicitamente o local")
    parser.add_argument("--timezone", dest="time_zone", default="America/Recife", help="Fuso para exibir horários")
    parser.add_argument("--execute", action="store_true", help="Aplicar a edição no Google Calendar")
    args = parser.parse_args(argv)
    try:
        change = DetailsChange(args.title, args.description, args.location)
        validate_details_change(args.event_id, change, time_zone=args.time_zone)
        with calendar_client() as client:
            result = EventDetailsService(GoogleCalendarGateway(client)).update(
                args.event_id, change, time_zone=args.time_zone, execute=args.execute,
            )
        print(json.dumps(asdict(result), ensure_ascii=True, indent=2))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou a edição (HTTP {exc.resp.status}).", file=sys.stderr)
    except (OSError, httplib2.HttpLib2Error, TransportError):
        print("Não foi possível confirmar a operação. Consulte o evento pelo ID antes de repetir.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
