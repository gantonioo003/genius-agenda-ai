"""OAuth, seleção da agenda de teste e conector de criação e consulta."""

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
from hmac import compare_digest
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2
from oauthlib.oauth2 import OAuth2Error

from app.database import database
from app.settings import PROJECT_ROOT, settings
from app.services.agenda import (
    AgendaEvent,
    CalendarListing,
    CalendarPeriod,
    load_zone,
    validate_period,
)
from app.services.interviews import (
    CalendarOperationError,
    CreatedInterview,
    InterviewDraft,
    validate_interview,
)
from app.services.event_selection import EventLookup, validate_event_id
from app.services.rescheduling import RescheduleResult, ScheduleChange, validate_schedule_change
from app.services.event_details import DetailsChange, DetailsResult, EventDetails, validate_details_change
from app.services.event_deletion import (
    DeletionResult, deletion_confirmation, deletion_question, validate_deletion,
)


SCOPES = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.owned",
)


class CalendarSetupError(RuntimeError):
    """Erro que pode ser exibido sem revelar dados de autenticação."""


def local_path(path: Path) -> Path:
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def save_token(credentials: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(credentials.to_json())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def get_credentials(*, interactive: bool = False) -> Credentials:
    token_file = local_path(settings.google_token_file)
    credentials = None
    if token_file.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(token_file))
        except (ValueError, KeyError):
            if not interactive:
                raise CalendarSetupError(
                    "Token local inválido. Execute novamente com --authorize."
                ) from None
        if credentials and not credentials.has_scopes(SCOPES):
            if not interactive:
                raise CalendarSetupError(
                    "A autorização não inclui as permissões necessárias. Use --authorize."
                )
            credentials = None
    if credentials and credentials.valid:
        return credentials
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            save_token(credentials, token_file)
            return credentials
        except RefreshError:
            if not interactive:
                raise CalendarSetupError(
                    "A autorização expirou ou foi revogada. Execute com --authorize."
                ) from None
    if not interactive:
        raise CalendarSetupError("Falta autorizar o Google. Execute com --authorize.")

    client_file = local_path(settings.google_client_file)
    if not client_file.is_file():
        raise CalendarSetupError("Arquivo do cliente OAuth não encontrado na pasta secrets.")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_file), list(SCOPES), autogenerate_code_verifier=True
    )
    # O callback contém um código temporário; não registrá-lo nos logs.
    logging.getLogger("google_auth_oauthlib.flow").setLevel(logging.WARNING)
    print("Uma janela do Google será aberta. Entre na conta de teste e autorize.", flush=True)
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=True,
        timeout_seconds=600,
        authorization_prompt_message="Aguardando autorização na janela do navegador...",
        success_message="Autorização recebida. Pode fechar esta aba e voltar ao projeto.",
        access_type="offline",
        prompt="consent",
    )
    granted_scopes = credentials.granted_scopes
    if not credentials.has_scopes(SCOPES) or (
        granted_scopes is not None and not set(SCOPES).issubset(granted_scopes)
    ):
        raise CalendarSetupError("Autorize as permissões solicitadas para concluir a conexão.")
    save_token(credentials, token_file)
    return credentials


def choose_calendar(
    items: list[dict[str, Any]], name: str, configured_id: str = ""
) -> dict[str, Any]:
    candidates = [
        item for item in items
        if (item.get("id") == configured_id if configured_id else item.get("summary") == name)
        and not item.get("deleted", False)
    ]
    if not candidates:
        raise CalendarSetupError(
            "A agenda de teste não foi encontrada. Confira conta, nome e ID configurado."
        )
    if len(candidates) != 1:
        raise CalendarSetupError(
            "Há mais de uma agenda com esse nome. Configure GOOGLE_CALENDAR_ID."
        )
    calendar = candidates[0]
    if calendar.get("accessRole") != "owner" or calendar.get("primary", False):
        raise CalendarSetupError(
            "Use uma agenda separada de teste criada por você, sem usar a agenda principal."
        )
    return calendar


@contextmanager
def calendar_client(*, interactive: bool = False) -> Iterator[Any]:
    credentials = get_credentials(interactive=interactive)
    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=20))
    service = build("calendar", "v3", http=http, cache_discovery=False)
    try:
        yield service
    finally:
        service.close()


def resolve_calendar(service: Any) -> dict[str, Any]:
    """Selecionar e fixar uma agenda própria separada antes de qualquer escrita."""
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        page = service.calendarList().list(
            maxResults=250,
            pageToken=page_token,
            showHidden=True,
            showDeleted=False,
            minAccessRole="owner",
            fields="items(id,summary,timeZone,accessRole,primary,deleted),nextPageToken",
        ).execute(num_retries=2)
        items.extend(page.get("items", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    database.initialize()
    configured_id = settings.google_calendar_id or database.get_metadata("google_calendar_id") or ""
    calendar = choose_calendar(items, settings.google_calendar_name, configured_id)
    database.set_metadata("google_calendar_id", calendar["id"])
    database.set_metadata("google_calendar_name", calendar["summary"])
    return calendar


def check_calendar(*, interactive: bool = False) -> dict[str, str]:
    with calendar_client(interactive=interactive) as service:
        calendar = resolve_calendar(service)
    return {
        "status": "ok",
        "calendar": calendar["summary"],
        "timezone": calendar.get("timeZone", ""),
        "access": calendar["accessRole"],
        "operation": "authorization_and_calendar_discovery_only",
    }


class GoogleCalendarGateway:
    """Adaptador Google usado pelo serviço de entrevistas; não abre login."""

    EVENT_FIELDS = "id,htmlLink,status,summary,description,start,end,extendedProperties"

    def __init__(self, service: Any):
        self.service = service

    DELETION_FIELDS = "id,etag,summary,status,start,end,htmlLink,recurrence,recurringEventId,eventType"

    def _deletion_snapshot(
        self, events: Any, calendar: dict, event_id: str, time_zone: str,
    ) -> tuple[AgendaEvent, str] | None:
        try:
            item = events.get(
                calendarId=calendar["id"], eventId=event_id,
                timeZone=time_zone, fields=self.DELETION_FIELDS,
            ).execute(num_retries=2)
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                return None
            raise
        if not isinstance(item, dict) or item.get("id") != event_id:
            raise CalendarOperationError("A leitura não confirmou o ID do evento.")
        if item.get("status") == "cancelled":
            return None
        if item.get("status") not in ("confirmed", "tentative"):
            raise CalendarOperationError("Não foi possível confirmar o estado do evento.")
        if item.get("recurrence"):
            raise CalendarOperationError("Selecione uma ocorrência específica; esta operação não exclui a série inteira.")
        if item.get("eventType", "default") != "default":
            raise CalendarOperationError("Este comando exclui apenas compromissos comuns.")
        etag = item.get("etag")
        if not isinstance(etag, str) or not etag.strip() or "\r" in etag or "\n" in etag:
            raise CalendarOperationError("Não foi possível confirmar a versão atual do evento.")
        zone = load_zone(time_zone)
        calendar_zone = load_zone(calendar.get("timeZone") or time_zone)
        _, _, event = self._read_event(item, zone, calendar_zone)
        return event, etag

    def delete_event(
        self, event_id: str, *, time_zone: str = "America/Recife", execute: bool = False,
        confirmation_token: str | None = None,
    ) -> DeletionResult:
        validate_deletion(event_id, time_zone=time_zone, execute=execute,
                          confirmation_token=confirmation_token)
        attempted = False
        try:
            calendar = resolve_calendar(self.service)
            events = self.service.events()
            snapshot = self._deletion_snapshot(events, calendar, event_id, time_zone)
            if snapshot is None:
                return DeletionResult("already_absent", calendar["summary"], None)
            event, etag = snapshot
            expected = deletion_confirmation(calendar["id"], event_id, etag)
            if not execute:
                return DeletionResult("needs_confirmation", calendar["summary"], event,
                                      expected, deletion_question(event))
            if not compare_digest(confirmation_token, expected):
                raise CalendarOperationError("A confirmação não corresponde ao evento e à versão atuais. Faça uma nova prévia.")
            request = events.delete(
                calendarId=calendar["id"], eventId=event_id, sendUpdates="none",
            )
            request.headers["If-Match"] = etag
            attempted = True
            try:
                request.execute(num_retries=0)  # DELETE retorna corpo vazio.
            except HttpError as exc:
                if exc.resp.status not in (404, 410):
                    raise
                if self._deletion_snapshot(events, calendar, event_id, time_zone) is None:
                    return DeletionResult("already_absent", calendar["summary"], None)
                raise CalendarOperationError("O evento ainda está ativo; a exclusão não foi confirmada.") from None
            if self._deletion_snapshot(events, calendar, event_id, time_zone) is not None:
                raise CalendarOperationError("O evento ainda está ativo; a exclusão não foi confirmada.")
            return DeletionResult("deleted", calendar["summary"], event)
        except CalendarOperationError as exc:
            if attempted:
                raise CalendarOperationError(
                    f"{exc} A exclusão pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise
        except HttpError as exc:
            if exc.resp.status == 412:
                raise CalendarOperationError("O evento mudou durante a exclusão. Faça uma nova prévia antes de confirmar novamente.") from None
            if attempted:
                raise CalendarOperationError(
                    f"Exclusão não confirmada (HTTP {exc.resp.status}). "
                    "A exclusão pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise CalendarOperationError(
                f"Google recusou a consulta (HTTP {exc.resp.status}); nenhuma exclusão foi feita."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            if attempted:
                raise CalendarOperationError("Exclusão não confirmada. A exclusão pode ter sido aplicada; consulte o ID antes de repetir.") from None
            raise CalendarOperationError("Falha ao consultar o evento; nenhuma exclusão foi feita.") from None
        except (KeyError, TypeError, ValueError):
            suffix = "Consulte o ID antes de repetir; a exclusão pode ter sido aplicada." if attempted else "Nenhuma exclusão foi feita."
            raise CalendarOperationError(f"Dados inválidos na resposta do Google. {suffix}") from None

    DETAILS_FIELDS = "id,etag,summary,description,location,status,start,end,htmlLink,recurrence,recurringEventId,eventType"

    def _details_snapshot(
        self, events: Any, calendar: dict, event_id: str, time_zone: str,
    ) -> tuple[EventDetails, str]:
        item = events.get(
            calendarId=calendar["id"], eventId=event_id,
            timeZone=time_zone, fields=self.DETAILS_FIELDS,
        ).execute(num_retries=2)
        if not isinstance(item, dict) or item.get("id") != event_id:
            raise CalendarOperationError("A leitura não confirmou o ID do evento.")
        if item.get("status") not in ("confirmed", "tentative"):
            raise CalendarOperationError("O evento está indisponível ou seu estado não foi confirmado.")
        if item.get("recurrence"):
            raise CalendarOperationError("Selecione uma ocorrência específica; esta operação não edita a série inteira.")
        if item.get("eventType", "default") != "default":
            raise CalendarOperationError("Este comando edita apenas compromissos comuns.")
        etag = item.get("etag")
        if not isinstance(etag, str) or not etag.strip() or "\r" in etag or "\n" in etag:
            raise CalendarOperationError("Não foi possível confirmar a versão atual do evento.")
        content = {field: item.get(field, "") for field in ("summary", "description", "location")}
        if any(not isinstance(value, str) for value in content.values()):
            raise CalendarOperationError("A resposta contém informações inválidas do evento.")
        zone = load_zone(time_zone)
        calendar_zone = load_zone(calendar.get("timeZone") or time_zone)
        _, _, event = self._read_event(item, zone, calendar_zone)
        values = asdict(event)
        values["title"] = content["summary"]  # Comparar título real, sem rótulo de exibição.
        return EventDetails(**values, description=content["description"], location=content["location"]), etag

    def update_event_details(
        self, event_id: str, change: DetailsChange, *, time_zone: str = "America/Recife",
        execute: bool = False,
    ) -> DetailsResult:
        requested = validate_details_change(event_id, change, time_zone=time_zone)
        attempted = False
        try:
            calendar = resolve_calendar(self.service)
            events = self.service.events()
            before, etag = self._details_snapshot(events, calendar, event_id, time_zone)

            def result(status: str, after: EventDetails | None) -> DetailsResult:
                return DetailsResult(status, calendar["summary"], before, after, requested)

            if not execute:
                return result("preview", None)
            if all(getattr(before, field) == value for field, value in requested.items()):
                return result("unchanged", before)

            body = {("summary" if field == "title" else field): value
                    for field, value in requested.items()}
            request = events.patch(
                calendarId=calendar["id"], eventId=event_id, body=body,
                sendUpdates="none", fields="id",
            )
            request.headers["If-Match"] = etag
            attempted = True
            response = request.execute(num_retries=0)
            if not isinstance(response, dict) or response.get("id") != event_id:
                raise CalendarOperationError("A resposta da gravação não confirmou o ID solicitado.")
            after, _ = self._details_snapshot(events, calendar, event_id, time_zone)
            if any(getattr(after, field) != requested.get(field, getattr(before, field))
                   for field in ("title", "description", "location")):
                raise CalendarOperationError("A leitura final não confirmou as informações esperadas.")
            if any(getattr(after, field) != getattr(before, field)
                   for field in ("start", "end", "all_day", "time_zone")):
                raise CalendarOperationError("A data ou o horário mudou durante a edição; confira o evento.")
            return result("updated", after)
        except CalendarOperationError as exc:
            if attempted:
                raise CalendarOperationError(
                    f"{exc} A edição pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise
        except HttpError as exc:
            if exc.resp.status == 412:
                raise CalendarOperationError("O evento mudou durante a edição. Consulte os dados atuais antes de tentar novamente.") from None
            if not attempted and exc.resp.status in (404, 410):
                raise CalendarOperationError("O evento não está disponível na agenda de teste; nenhuma edição foi feita.") from None
            if attempted:
                raise CalendarOperationError(
                    f"Edição não confirmada (HTTP {exc.resp.status}). "
                    "A edição pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise CalendarOperationError(
                f"Google recusou a leitura do evento (HTTP {exc.resp.status}); nenhuma edição foi feita."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            if attempted:
                raise CalendarOperationError("Edição não confirmada. A edição pode ter sido aplicada; consulte o ID antes de repetir.") from None
            raise CalendarOperationError("Falha ao consultar o evento; nenhuma edição foi feita.") from None
        except (KeyError, TypeError, ValueError):
            suffix = "Consulte o ID antes de repetir; a edição pode ter sido aplicada." if attempted else "Nenhuma edição foi feita."
            raise CalendarOperationError(f"Dados inválidos na resposta do Google. {suffix}") from None

    SCHEDULE_FIELDS = "id,etag,summary,status,start,end,htmlLink,recurrence,recurringEventId,eventType"

    def _schedule_snapshot(
        self, events: Any, calendar: dict, event_id: str, time_zone: str,
    ) -> tuple[AgendaEvent, str]:
        item = events.get(
            calendarId=calendar["id"], eventId=event_id,
            timeZone=time_zone, fields=self.SCHEDULE_FIELDS,
        ).execute(num_retries=2)
        if not isinstance(item, dict) or item.get("id") != event_id:
            raise CalendarOperationError("A leitura não confirmou o ID do evento.")
        if item.get("status") not in ("confirmed", "tentative"):
            raise CalendarOperationError("O evento está indisponível ou seu estado não foi confirmado.")
        if item.get("recurrence"):
            raise CalendarOperationError("Selecione uma ocorrência específica; esta operação não altera a série inteira.")
        if item.get("eventType", "default") != "default":
            raise CalendarOperationError("Este comando reagenda apenas compromissos comuns.")
        etag = item.get("etag")
        if not isinstance(etag, str) or not etag.strip() or "\r" in etag or "\n" in etag:
            raise CalendarOperationError("Não foi possível confirmar a versão atual do evento.")
        zone = load_zone(time_zone)
        calendar_zone = load_zone(calendar.get("timeZone") or time_zone)
        _, _, event = self._read_event(item, zone, calendar_zone)
        if event.all_day:
            raise CalendarOperationError("Eventos de dia inteiro precisam de uma operação própria de reagendamento.")
        return event, etag

    @staticmethod
    def _has_requested_schedule(event: AgendaEvent, change: ScheduleChange) -> bool:
        return all(
            datetime.fromisoformat(actual).astimezone(timezone.utc) == expected.astimezone(timezone.utc)
            for actual, expected in ((event.start, change.start), (event.end, change.end))
        )

    def reschedule_event(
        self, event_id: str, change: ScheduleChange, *, execute: bool = False,
    ) -> RescheduleResult:
        validate_schedule_change(event_id, change)
        attempted = False
        try:
            calendar = resolve_calendar(self.service)
            events = self.service.events()
            before, etag = self._schedule_snapshot(events, calendar, event_id, change.time_zone)

            def result(status: str, after: AgendaEvent | None) -> RescheduleResult:
                return RescheduleResult(
                    status, calendar["summary"], before, after,
                    change.start.isoformat(), change.end.isoformat(), change.time_zone,
                )

            if not execute:
                return result("preview", None)
            if self._has_requested_schedule(before, change):
                return result("unchanged", before)

            request = events.patch(
                calendarId=calendar["id"], eventId=event_id,
                body={
                    "start": {"dateTime": change.start.isoformat(), "timeZone": change.time_zone},
                    "end": {"dateTime": change.end.isoformat(), "timeZone": change.time_zone},
                },
                sendUpdates="none", fields="id",
            )
            request.headers["If-Match"] = etag
            attempted = True
            response = request.execute(num_retries=0)
            if not isinstance(response, dict) or response.get("id") != event_id:
                raise CalendarOperationError("A resposta da gravação não confirmou o ID solicitado.")
            after, _ = self._schedule_snapshot(events, calendar, event_id, change.time_zone)
            if not self._has_requested_schedule(after, change):
                raise CalendarOperationError("A leitura final não confirmou os novos horários.")
            return result("rescheduled", after)
        except CalendarOperationError as exc:
            if attempted:
                raise CalendarOperationError(
                    f"{exc} A mudança pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise
        except HttpError as exc:
            if exc.resp.status == 412:
                raise CalendarOperationError(
                    "O evento mudou durante o reagendamento. Consulte os dados atuais antes de tentar novamente."
                ) from None
            if not attempted and exc.resp.status in (404, 410):
                raise CalendarOperationError("O evento não está disponível na agenda de teste; nenhum reagendamento foi feito.") from None
            if attempted:
                raise CalendarOperationError(
                    f"Reagendamento não confirmado (HTTP {exc.resp.status}). "
                    "A mudança pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise CalendarOperationError(
                f"Google recusou a leitura do evento (HTTP {exc.resp.status}); nenhum reagendamento foi feito."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            if attempted:
                raise CalendarOperationError(
                    "Reagendamento não confirmado. A mudança pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise CalendarOperationError("Falha ao consultar o evento; nenhum reagendamento foi feito.") from None
        except (KeyError, TypeError, ValueError):
            suffix = "Consulte o ID antes de repetir; a mudança pode ter sido aplicada." if attempted else "Nenhum reagendamento foi feito."
            raise CalendarOperationError(f"Dados inválidos na resposta do Google. {suffix}") from None

    @staticmethod
    def _read_event(
        item: dict, zone: Any, calendar_zone: Any,
    ) -> tuple[datetime, datetime, AgendaEvent]:
        """Mesmo formato de evento na listagem e na consulta pelo ID."""
        event_id = item["id"]
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Evento sem identificador.")
        start_data, end_data = item["start"], item["end"]
        all_day = "date" in start_data and "date" in end_data
        if all_day:
            start_date = date.fromisoformat(start_data["date"])
            end_date = date.fromisoformat(end_data["date"])
            start = datetime.combine(start_date, time.min, tzinfo=calendar_zone)
            end = datetime.combine(end_date, time.min, tzinfo=calendar_zone)
            display_start, display_end = start_date.isoformat(), end_date.isoformat()
        else:
            start = datetime.fromisoformat(start_data["dateTime"])
            end = datetime.fromisoformat(end_data["dateTime"])
            if start.utcoffset() is None or end.utcoffset() is None:
                raise ValueError("Evento temporizado sem deslocamento UTC.")
            display_start = start.astimezone(zone).isoformat()
            display_end = end.astimezone(zone).isoformat()
        start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        if end_utc <= start_utc:
            raise ValueError("Intervalo inválido no evento.")
        title = item.get("summary") or "(sem título)"
        if not isinstance(title, str):
            raise ValueError("Título inválido no evento.")
        entry = AgendaEvent(
            event_id=event_id, title=title,
            start=display_start, end=display_end, all_day=all_day,
            time_zone=calendar_zone.key if all_day else zone.key,
            status=item.get("status") or "unknown", html_link=item.get("htmlLink"),
        )
        return start_utc, end_utc, entry

    def get_event(self, event_id: str, *, time_zone: str = "America/Recife") -> EventLookup:
        validate_event_id(event_id)
        zone = load_zone(time_zone)
        try:
            calendar = resolve_calendar(self.service)
            calendar_zone = load_zone(calendar.get("timeZone") or time_zone)
            try:
                item = self.service.events().get(
                    calendarId=calendar["id"], eventId=event_id, timeZone=time_zone,
                    fields="id,summary,status,start,end,htmlLink",
                ).execute(num_retries=2)
            except HttpError as exc:
                if exc.resp.status in (404, 410):
                    return EventLookup(calendar["summary"], None)
                raise
            if not isinstance(item, dict):
                raise ValueError("Resposta de evento inválida.")
            if item.get("status") == "cancelled":
                return EventLookup(calendar["summary"], None)
            _, _, event = self._read_event(item, zone, calendar_zone)
            if event.event_id != event_id or event.status not in ("confirmed", "tentative"):
                raise ValueError("O retorno não confirma o evento solicitado.")
            return EventLookup(calendar["summary"], event)
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google recusou a identificação (HTTP {exc.resp.status}). Nenhum evento foi selecionado."
            ) from None
        except (OSError, httplib2.HttpLib2Error):
            raise CalendarOperationError(
                "Não foi possível confirmar o evento. Confira a conexão e tente novamente."
            ) from None
        except (KeyError, TypeError, ValueError):
            raise CalendarOperationError(
                "A resposta do Google não contém um evento válido; nenhum evento foi selecionado."
            ) from None

    def list_events(self, period: CalendarPeriod) -> CalendarListing:
        zone = validate_period(period)
        try:
            calendar = resolve_calendar(self.service)
            calendar_zone = load_zone(calendar.get("timeZone") or period.time_zone)
            events = self.service.events()
            collected: dict[str, tuple[datetime, AgendaEvent]] = {}
            page_token = None
            seen_tokens = {None}
            while True:
                page = events.list(
                    calendarId=calendar["id"],
                    timeMin=period.start.isoformat(),
                    timeMax=period.end.isoformat(),
                    timeZone=period.time_zone,
                    singleEvents=True, showDeleted=False, orderBy="startTime",
                    maxResults=250, pageToken=page_token,
                    fields="items(id,summary,status,start,end,htmlLink),nextPageToken",
                ).execute(num_retries=2)
                for item in page.get("items", []):
                    if item.get("status") == "cancelled":
                        continue
                    start_utc, end_utc, entry = self._read_event(item, zone, calendar_zone)
                    # Sobreposição: inclui madrugada e dias inteiros. O fim é exclusivo.
                    if (start_utc >= period.end.astimezone(timezone.utc)
                            or end_utc <= period.start.astimezone(timezone.utc)):
                        continue
                    collected[entry.event_id] = (start_utc, entry)
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
                if page_token in seen_tokens:
                    raise CalendarOperationError("O Google repetiu uma página; consulta não concluída.")
                seen_tokens.add(page_token)
            ordered = sorted(collected.values(), key=lambda value: (value[0], value[1].event_id))
            return CalendarListing(calendar["summary"], tuple(entry for _, entry in ordered))
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google recusou a consulta (HTTP {exc.resp.status}). Nenhum resultado foi confirmado."
            ) from None
        except (OSError, httplib2.HttpLib2Error):
            raise CalendarOperationError(
                "Não foi possível concluir a consulta. Confira a conexão e tente novamente."
            ) from None
        except (KeyError, TypeError, ValueError):
            raise CalendarOperationError(
                "A resposta do Google contém dados de evento ou fuso inválidos; consulta não concluída."
            ) from None

    def create_event(
        self, draft: InterviewDraft, *, operation_id: str
    ) -> CreatedInterview:
        validate_interview(draft, operation_id)
        calendar = resolve_calendar(self.service)
        # Hexadecimal atende ao alfabeto base32hex exigido pelo Google.
        event_id = "genius" + sha256(operation_id.encode("utf-8")).hexdigest()
        body = {
            "id": event_id,
            "summary": draft.title.strip(),
            "description": draft.description,
            "start": {"dateTime": draft.start.isoformat(), "timeZone": draft.time_zone},
            "end": {"dateTime": draft.end.isoformat(), "timeZone": draft.time_zone},
            "visibility": "private",
            "reminders": {"useDefault": False},
        }
        fingerprint = sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        body["extendedProperties"] = {"private": {"geniusRequest": fingerprint}}
        events = self.service.events()
        try:
            # Na repetição, consultar primeiro: uma tentativa anterior pode ter
            # sido gravada no Google mesmo se a resposta se perdeu na rede.
            existing = self._find_event(events, calendar["id"], event_id)
            if existing is not None:
                return self._result(existing, calendar, body, reused=True)
            try:
                event = events.insert(
                    calendarId=calendar["id"], body=body,
                    sendUpdates="none", fields=self.EVENT_FIELDS,
                ).execute(num_retries=0)
            except HttpError as exc:
                if exc.resp.status != 409:
                    raise
                # Outra execução pode ter criado o mesmo ID entre GET e POST.
                event = self._find_event(events, calendar["id"], event_id)
                if event is None:
                    raise CalendarOperationError(
                        "O ID já está em uso, mas o evento não pôde ser confirmado."
                    ) from None
                return self._result(event, calendar, body, reused=True)
            return self._result(event, calendar, body, reused=False)
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google não confirmou a operação (HTTP {exc.resp.status}). "
                "Confira o acesso e reutilize o mesmo identificador ao tentar novamente."
            ) from None
        except (OSError, httplib2.HttpLib2Error):
            raise CalendarOperationError(
                "Não foi possível confirmar a criação. O evento pode ter sido salvo; "
                "tente novamente com o mesmo identificador para consultar o resultado."
            ) from None

    def _find_event(self, events: Any, calendar_id: str, event_id: str) -> dict | None:
        try:
            return events.get(
                calendarId=calendar_id, eventId=event_id, fields=self.EVENT_FIELDS
            ).execute(num_retries=2)
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise

    @staticmethod
    def _result(event: dict, calendar: dict, body: dict, *, reused: bool) -> CreatedInterview:
        same_request = (
            event.get("extendedProperties", {}).get("private", {}).get("geniusRequest")
            == body["extendedProperties"]["private"]["geniusRequest"]
        )
        try:
            same_details = all(
                event.get(field, "") == body[field]
                for field in ("summary", "description")
            ) and all(
                datetime.fromisoformat(event[field]["dateTime"])
                == datetime.fromisoformat(body[field]["dateTime"])
                for field in ("start", "end")
            )
        except (KeyError, TypeError, ValueError):
            same_details = False
        if (event.get("id") != body["id"] or event.get("status") != "confirmed"
                or not same_request or not same_details or not event.get("htmlLink")):
            raise CalendarOperationError(
                "A resposta não confirma os dados solicitados. O ID pode estar em uso "
                "ou o evento pode ter sido alterado; confira a agenda antes de prosseguir."
            )
        return CreatedInterview(
            event_id=event["id"], html_link=event["htmlLink"],
            calendar_name=calendar["summary"], reused=reused,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Conectar o Google Calendar de teste.")
    parser.add_argument("--authorize", action="store_true", help="Abrir o consentimento no navegador.")
    args = parser.parse_args()
    try:
        result = check_calendar(interactive=args.authorize)
    except CalendarSetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OAuth2Error:
        print("A autorização não foi concluída. Confira a janela do Google e tente novamente.", file=sys.stderr)
        return 1
    except HttpError as exc:
        print(f"Google recusou a consulta (HTTP {exc.resp.status}). Confira API e permissões.", file=sys.stderr)
        return 1
    except (OSError, ValueError, httplib2.HttpLib2Error):
        print("Não foi possível concluir a conexão. Confira rede e configuração OAuth.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
