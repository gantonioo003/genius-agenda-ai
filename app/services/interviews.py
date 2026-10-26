"""Serviço de criação de entrevistas, sem dependência de Google, n8n ou IA."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.services.time_rules import validate_event_interval


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
    recovered: bool = False


class CalendarOperationError(RuntimeError):
    """Falha operacional que pode ser apresentada sem dados privados."""


class CalendarBindingError(CalendarOperationError):
    """A agenda atual diverge da agenda vinculada antes da tentativa de escrita."""


@dataclass(frozen=True, repr=False)
class ReconciledCreation:
    """Resultado de uma conferência somente de leitura, sem permissão para reenviar."""

    status: Literal["found", "absent", "unregistered"]
    created: CreatedInterview | None = None

    def __repr__(self):
        return "ReconciledCreation(<private>)"


class CalendarGateway(Protocol):
    def create_event(
        self, draft: InterviewDraft, *, operation_id: str
    ) -> CreatedInterview: ...


def validate_interview(draft: InterviewDraft, operation_id: str) -> None:
    if not isinstance(draft, InterviewDraft):
        raise ValueError("Informe os dados da entrevista no formato esperado.")
    if not isinstance(draft.title, str) or not draft.title.strip():
        raise ValueError("Informe um título para a entrevista.")
    if not isinstance(draft.description, str):
        raise ValueError("A descrição da entrevista deve ser um texto.")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ValueError("Informe um identificador estável para esta operação.")
    validate_event_interval(draft.start, draft.end, draft.time_zone)


class InterviewService:
    def __init__(self, gateway: CalendarGateway):
        self.gateway = gateway

    def create(
        self, draft: InterviewDraft, *, operation_id: str
    ) -> CreatedInterview:
        validate_interview(draft, operation_id)
        return self.gateway.create_event(draft, operation_id=operation_id)
