"""Preparar a entrevista fictícia; escrita no Google exige --execute."""

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import sys

from googleapiclient.errors import HttpError
import httplib2

from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client
from app.services.interviews import (
    CalendarOperationError,
    InterviewDraft,
    InterviewService,
    validate_interview,
)


OPERATION_ID = "genius-primeira-entrevista-2026-10-22-v1"


def test_interview() -> InterviewDraft:
    return InterviewDraft(
        title="Entrevista de estágio — Empresa Exemplo (teste)",
        start=datetime.fromisoformat("2026-10-22T15:00:00-03:00"),
        end=datetime.fromisoformat("2026-10-22T15:30:00-03:00"),
        time_zone="America/Recife",
        description="Entrevista fictícia para testar o agendamento pelo Genius Agenda AI.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preparar ou criar a entrevista fictícia.")
    parser.add_argument(
        "--execute", action="store_true", help="Enviar a criação para o Google Calendar."
    )
    args = parser.parse_args(argv)
    try:
        draft = test_interview()
        validate_interview(draft, OPERATION_ID)
        if not args.execute:
            print(json.dumps({
                "status": "preview", "operation_id": OPERATION_ID,
                "title": draft.title, "start": draft.start.isoformat(),
                "end": draft.end.isoformat(), "time_zone": draft.time_zone,
                "description": draft.description, "attendees": [],
            }, ensure_ascii=True))
            return 0
        with calendar_client() as client:
            result = InterviewService(GoogleCalendarGateway(client)).create(
                draft, operation_id=OPERATION_ID
            )
        print(json.dumps({"status": "ok", **asdict(result)}, ensure_ascii=True))
        return 0
    except (CalendarSetupError, CalendarOperationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
    except HttpError as exc:
        print(f"Google recusou a consulta da agenda (HTTP {exc.resp.status}).", file=sys.stderr)
    except (OSError, httplib2.HttpLib2Error):
        print("Não foi possível acessar o Google. Confira a conexão e a configuração.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
