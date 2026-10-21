"""Serviço de criação de entrevistas, sem dependência de Google, n8n ou IA."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class InterviewDraft:
    title: str
    start: datetime
    end: datetime
    time_zone: str
    description: str = ""


@dataclass(frozen=True)
class CreatedInterview:
    event_id: str
    html_link: str
    calendar_name: str
    reused: bool = False


class CalendarOperationError(RuntimeError):
    """Falha operacional que pode ser apresentada sem dados privados."""


class CalendarGateway(Protocol):
    def create_event(
        self, draft: InterviewDraft, *, operation_id: str
    ) -> CreatedInterview: ...


def validate_interview(draft: InterviewDraft, operation_id: str) -> None:
    if not draft.title.strip():
        raise ValueError("Informe um título para a entrevista.")
    if not draft.time_zone.strip():
        raise ValueError("Informe o fuso horário da entrevista.")
    for value in (draft.start, draft.end):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Início e fim devem incluir data, horário e deslocamento UTC.")
    if draft.end.astimezone(timezone.utc) <= draft.start.astimezone(timezone.utc):
        raise ValueError("O fim da entrevista deve ser posterior ao início.")
    if not operation_id.strip():
        raise ValueError("Informe um identificador estável para esta operação.")


class InterviewService:
    def __init__(self, gateway: CalendarGateway):
        self.gateway = gateway

    def create(
        self, draft: InterviewDraft, *, operation_id: str
    ) -> CreatedInterview:
        validate_interview(draft, operation_id)
        return self.gateway.create_event(draft, operation_id=operation_id)
