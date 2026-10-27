"""OAuth, seleção da agenda de teste e conector de criação e consulta."""

import argparse
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Iterator
import unicodedata

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2
from oauthlib.oauth2 import OAuth2Error

from app.database import CreationIdentityError, CreationRecord, database
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
    CalendarBindingError,
    ReconciledCreation,
    CreatedInterview,
    InterviewDraft,
    validate_interview,
)
from app.services.event_selection import EventLookup, normalized_title, validate_event_id
from app.services.rescheduling import RescheduleResult, ScheduleChange, validate_schedule_change
from app.services.event_details import DetailsChange, DetailsResult, EventDetails, validate_details_change
from app.services.event_deletion import (
    DeletionResult, DeletionBindingError, DeletionVersionError, deletion_confirmation,
    deletion_question, validate_deletion, validate_deletion_guards,
)
from app.services.conflicts import ConflictReport, build_conflict_report, require_available
from app.services.creation_identity import creation_content_hash
from app.services.update_snapshot import (
    UpdateSnapshot, UpdateBindingError, UpdateVersionError,
    validate_update_snapshot, validate_update_guards,
)
from app.google_transport import SingleAttemptHttp


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
        except (ValueError, KeyError, TypeError):
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
            request = Request()
            def bounded_request(*args, **kwargs):
                kwargs["timeout"] = 20
                return request(*args, **kwargs)
            credentials.refresh(bounded_request)
            save_token(credentials, token_file)
            return credentials
        except RefreshError:
            if not interactive:
                raise CalendarSetupError(
                    "A autorização expirou ou foi revogada. Execute com --authorize."
                ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarSetupError("Não foi possível renovar a autorização por falha de conexão. Tente consultar novamente.") from None
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
    try:
        credentials = get_credentials(interactive=interactive)
    except RefreshError:
        raise CalendarSetupError("A autorização precisa ser renovada. Execute novamente com --authorize.") from None
    except (OSError, httplib2.HttpLib2Error, TransportError):
        raise CalendarSetupError("Não foi possível obter a autorização local. Confira conexão e arquivos de configuração.") from None
    transport = SingleAttemptHttp(timeout=20)
    http = None
    service = None
    try:
        http = AuthorizedHttp(credentials, http=transport, max_refresh_attempts=0)
        service = build("calendar", "v3", http=http, cache_discovery=False)
        yield service
    finally:
        try:
            if service is not None:
                service.close()
            elif http is not None:
                http.close()
            else:
                transport.close()
        except (OSError, httplib2.HttpLib2Error, TransportError):
            logging.getLogger(__name__).warning("Falha ao fechar o cliente Google; o resultado da operação foi preservado.")


def resolve_calendar(service: Any) -> dict[str, Any]:
    """Selecionar e fixar uma agenda própria separada antes de qualquer escrita."""
    items: list[dict[str, Any]] = []
    page_token = None
    seen_tokens = {None}
    try:
        while True:
            page = service.calendarList().list(
                maxResults=250,
                pageToken=page_token,
                showHidden=True,
                showDeleted=False,
                minAccessRole="owner",
                fields="items(id,summary,timeZone,accessRole,primary,deleted),nextPageToken",
            ).execute(num_retries=2)
            if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                raise ValueError
            for item in page.get("items", []):
                if not isinstance(item, dict):
                    raise ValueError
                validate_event_id(item.get("id"))
                if not isinstance(item.get("summary"), str):
                    raise ValueError
                items.append(item)
            next_token = page.get("nextPageToken")
            if next_token is None or next_token == "":
                break
            if not isinstance(next_token, str) or not next_token.strip() or next_token in seen_tokens:
                raise ValueError
            seen_tokens.add(next_token)
            page_token = next_token
    except HttpError as exc:
        raise CalendarSetupError(f"Google recusou a descoberta da agenda (HTTP {exc.resp.status}). Nenhuma operação foi executada.") from None
    except (OSError, httplib2.HttpLib2Error, TransportError):
        raise CalendarSetupError("Falha ao consultar a lista de agendas. Nenhuma operação foi executada.") from None
    except (KeyError, TypeError, ValueError):
        raise CalendarSetupError("Resposta ou paginação inválida na lista de agendas. Nenhuma operação foi executada.") from None
    try:
        database.initialize()
        configured_id = settings.google_calendar_id or database.get_metadata("google_calendar_id") or ""
        calendar = choose_calendar(items, settings.google_calendar_name, configured_id)
        database.set_metadata("google_calendar_id", calendar["id"])
        database.set_metadata("google_calendar_name", calendar["summary"])
    except CalendarSetupError:
        raise
    except (sqlite3.Error, RuntimeError):
        raise CalendarSetupError("O banco local está ocupado ou indisponível. Tente novamente após a operação atual.") from None
    return calendar


@contextmanager
def calendar_write_lock() -> Iterator[sqlite3.Connection]:
    try:
        with database.calendar_write_lock() as connection:
            yield connection
    except sqlite3.Error:
        # Pode acontecer na liberação após o Google ter aceitado a escrita.
        raise CalendarOperationError(
            "Não foi possível concluir o controle local da operação. "
            "Consulte o evento pelo ID antes de repetir; uma gravação pode ter sido aplicada."
        ) from None


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


def _execute_write(request: Any, *, expected_id: str | None = None) -> tuple[Any, bool]:
    """Uma tentativa; resposta incerta exige leitura, nunca nova escrita aqui."""
    try:
        response = request.execute(num_retries=0)
    except HttpError as exc:
        if exc.resp.status in (408, 429) or 500 <= exc.resp.status <= 599:
            return None, True
        raise  # 401/403/412 não são convertidos em sucesso por recuperação.
    except (OSError, httplib2.HttpLib2Error, TransportError, ValueError, TypeError):
        return None, True
    malformed = expected_id is not None and (
        not isinstance(response, dict) or response.get("id") != expected_id
    )
    return response, malformed


class GoogleCalendarGateway:
    """Adaptador Google usado pelo serviço de entrevistas; não abre login."""

    EVENT_FIELDS = "id,htmlLink,status,summary,description,start,end,extendedProperties"

    def __init__(self, service: Any):
        self.service = service

    def _check_conflicts(
        self, events: Any, calendar: dict, period: CalendarPeriod, *,
        exclude_event_id: str | None = None,
    ) -> ConflictReport:
        zone = validate_period(period)
        if exclude_event_id is not None:
            validate_event_id(exclude_event_id)
        try:
            calendar_zone = load_zone(calendar.get("timeZone") or period.time_zone)
            # A API ignora frações de segundo nos filtros. Ampliar a janela e
            # filtrar pelos instantes exatos evita perder conflitos no fim.
            query_start = period.start.astimezone(timezone.utc).replace(microsecond=0)
            query_end = period.end.astimezone(timezone.utc)
            if query_end.microsecond:
                query_end = query_end.replace(microsecond=0) + timedelta(seconds=1)
            collected = {}
            page_token, seen_tokens = None, {None}
            while True:
                page = events.list(
                    calendarId=calendar["id"], timeMin=query_start.isoformat(), timeMax=query_end.isoformat(),
                    timeZone=period.time_zone, singleEvents=True, showDeleted=False, showHiddenInvitations=True,
                    orderBy="startTime", maxResults=250, pageToken=page_token,
                    fields="items(id,summary,status,start,end,htmlLink,transparency),nextPageToken",
                ).execute(num_retries=2)
                if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                    raise ValueError("Página inválida na consulta de conflitos.")
                for item in page.get("items", []):
                    if not isinstance(item, dict):
                        raise ValueError("Evento inválido na consulta de conflitos.")
                    validate_event_id(item.get("id"))
                    if item.get("status") == "cancelled":
                        continue
                    if item.get("status") not in ("confirmed", "tentative"):
                        raise ValueError("Estado desconhecido na consulta de conflitos.")
                    transparency = item.get("transparency", "opaque")
                    if transparency not in ("opaque", "transparent"):
                        raise ValueError("Disponibilidade desconhecida na consulta de conflitos.")
                    if transparency == "transparent":
                        continue
                    _, _, event = self._read_event(item, zone, calendar_zone)
                    previous = collected.get(event.event_id)
                    if previous is not None and previous != event:
                        raise ValueError("O evento mudou durante a consulta de conflitos.")
                    collected[event.event_id] = event
                next_token = page.get("nextPageToken")
                if next_token is None or next_token == "":
                    break
                if not isinstance(next_token, str) or not next_token.strip() or next_token in seen_tokens:
                    raise ValueError("Paginação inválida na consulta de conflitos.")
                seen_tokens.add(next_token)
                page_token = next_token
            return build_conflict_report(period, calendar["summary"], collected.values(), exclude_event_id=exclude_event_id)
        except HttpError as exc:
            raise CalendarOperationError(f"Não foi possível verificar os conflitos (HTTP {exc.resp.status}). Nenhum horário foi confirmado como livre.") from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarOperationError("Falha ao verificar conflitos. Nenhum horário foi confirmado como livre.") from None
        except (KeyError, TypeError, ValueError, OverflowError):
            raise CalendarOperationError("Dados inválidos na consulta de conflitos. Nenhum horário foi confirmado como livre.") from None

    def check_conflicts(self, period: CalendarPeriod, *, exclude_event_id: str | None = None) -> ConflictReport:
        validate_period(period)
        if exclude_event_id is not None:
            validate_event_id(exclude_event_id)
        calendar = resolve_calendar(self.service)
        return self._check_conflicts(self.service.events(), calendar, period, exclude_event_id=exclude_event_id)

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
        if not event.title.strip():
            event = replace(event, title="(sem título)")
        return event, etag

    def delete_event(
        self, event_id: str, *, time_zone: str = "America/Recife", execute: bool = False,
        confirmation_token: str | None = None,
        expected_calendar_id: str | None = None, expected_version: str | None = None,
    ) -> DeletionResult:
        validate_deletion(event_id, time_zone=time_zone, execute=execute,
                          confirmation_token=confirmation_token)
        validate_deletion_guards(expected_calendar_id, expected_version)
        attempted = False
        try:
            calendar = resolve_calendar(self.service)
            if expected_calendar_id is not None and calendar["id"] != expected_calendar_id:
                raise DeletionBindingError("A agenda mudou antes da exclusão; nenhuma exclusão foi feita.")
            events = self.service.events()
            snapshot = self._deletion_snapshot(events, calendar, event_id, time_zone)
            if snapshot is None:
                return DeletionResult("already_absent", calendar["summary"], None)
            event, etag = snapshot
            if expected_version is not None and etag != expected_version:
                raise DeletionVersionError("A versão do evento mudou antes da exclusão; nenhuma exclusão foi feita.")
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
                _, recovered = _execute_write(request)  # DELETE retorna corpo vazio.
            except HttpError as exc:
                if exc.resp.status == 412 and expected_version is not None:
                    # Somente o 412 da tentativa DELETE comprova a recusa da escrita.
                    raise DeletionVersionError("O evento mudou durante a exclusão. Faça uma nova prévia antes de confirmar novamente.") from None
                if exc.resp.status not in (404, 410):
                    raise
                if self._deletion_snapshot(events, calendar, event_id, time_zone) is None:
                    return DeletionResult("already_absent", calendar["summary"], None)
                raise CalendarOperationError("O evento ainda está ativo; a exclusão não foi confirmada.") from None
            if self._deletion_snapshot(events, calendar, event_id, time_zone) is not None:
                raise CalendarOperationError("O evento ainda está ativo; a exclusão não foi confirmada.")
            return DeletionResult("deleted", calendar["summary"], event, recovered=recovered)
        except (DeletionBindingError, DeletionVersionError):
            raise
        except CalendarOperationError as exc:
            if attempted:
                raise CalendarOperationError(
                    f"{exc} A exclusão pode ter sido aplicada; consulte o ID antes de repetir."
                ) from None
            raise
        except HttpError as exc:
            if exc.resp.status == 412:
                if expected_version is not None and not attempted:
                    raise DeletionVersionError("O evento mudou antes da exclusão. Faça uma nova prévia antes de confirmar novamente.") from None
                # Um 412 da leitura posterior não prova que o DELETE falhou.
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
        return self._parse_details_snapshot(item, calendar, event_id, time_zone)

    def _parse_details_snapshot(
        self, item: Any, calendar: dict, event_id: str, time_zone: str,
    ) -> tuple[EventDetails, str]:
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
        execute: bool = False, expected_calendar_id: str | None = None,
        expected_version: str | None = None,
    ) -> DetailsResult:
        requested = validate_details_change(event_id, change, time_zone=time_zone)
        validate_update_guards(expected_calendar_id, expected_version)
        attempted = False
        try:
            calendar = resolve_calendar(self.service)
            if expected_calendar_id is not None and calendar["id"] != expected_calendar_id:
                raise UpdateBindingError("A agenda mudou antes da edição; nenhuma edição foi feita.")
            events = self.service.events()
            before, etag = self._details_snapshot(events, calendar, event_id, time_zone)
            if expected_version is not None and etag != expected_version:
                raise UpdateVersionError("A versão do evento mudou antes da edição; nenhuma edição foi feita.")

            def result(status: str, after: EventDetails | None, *, recovered: bool = False) -> DetailsResult:
                return DetailsResult(status, calendar["summary"], before, after, requested, recovered)

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
            _, recovered = _execute_write(request, expected_id=event_id)
            after, _ = self._details_snapshot(events, calendar, event_id, time_zone)
            if any(getattr(after, field) != requested.get(field, getattr(before, field))
                   for field in ("title", "description", "location")):
                raise CalendarOperationError("A leitura final não confirmou as informações esperadas.")
            if any(getattr(after, field) != getattr(before, field)
                   for field in ("start", "end", "all_day", "time_zone")):
                raise CalendarOperationError("A data ou o horário mudou durante a edição; confira o evento.")
            return result("updated", after, recovered=recovered)
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

    SCHEDULE_FIELDS = "id,etag,summary,status,start,end,htmlLink,recurrence,recurringEventId,eventType,transparency"

    def _schedule_snapshot(
        self, events: Any, calendar: dict, event_id: str, time_zone: str,
    ) -> tuple[AgendaEvent, str, str]:
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
        transparency = item.get("transparency", "opaque")
        if transparency not in ("opaque", "transparent"):
            raise CalendarOperationError("Não foi possível confirmar se o evento ocupa o horário.")
        etag = item.get("etag")
        if not isinstance(etag, str) or not etag.strip() or "\r" in etag or "\n" in etag:
            raise CalendarOperationError("Não foi possível confirmar a versão atual do evento.")
        zone = load_zone(time_zone)
        calendar_zone = load_zone(calendar.get("timeZone") or time_zone)
        _, _, event = self._read_event(item, zone, calendar_zone)
        if event.all_day:
            raise CalendarOperationError("Eventos de dia inteiro precisam de uma operação própria de reagendamento.")
        if not event.title.strip():
            event = replace(event, title="(sem título)")
        return event, etag, transparency

    @staticmethod
    def _has_requested_schedule(event: AgendaEvent, change: ScheduleChange) -> bool:
        return all(
            datetime.fromisoformat(actual).astimezone(timezone.utc) == expected.astimezone(timezone.utc)
            for actual, expected in ((event.start, change.start), (event.end, change.end))
        )

    def reschedule_event(
        self, event_id: str, change: ScheduleChange, *, execute: bool = False,
        expected_calendar_id: str | None = None, expected_version: str | None = None,
    ) -> RescheduleResult:
        validate_schedule_change(event_id, change)
        validate_update_guards(expected_calendar_id, expected_version)
        attempted = False
        try:
            calendar = resolve_calendar(self.service)
            if expected_calendar_id is not None and calendar["id"] != expected_calendar_id:
                raise UpdateBindingError("A agenda mudou antes do reagendamento; nenhuma mudança foi feita.")
            events = self.service.events()
            with calendar_write_lock():
                before, etag, transparency = self._schedule_snapshot(events, calendar, event_id, change.time_zone)
                if expected_version is not None and etag != expected_version:
                    raise UpdateVersionError("A versão do evento mudou antes do reagendamento; nenhuma mudança foi feita.")

                def result(status: str, after: AgendaEvent | None, *, recovered: bool = False) -> RescheduleResult:
                    return RescheduleResult(
                        status, calendar["summary"], before, after,
                        change.start.isoformat(), change.end.isoformat(), change.time_zone, recovered,
                    )

                if self._has_requested_schedule(before, change):
                    return result("unchanged", before) if execute else result("preview", None)
                if transparency == "opaque":
                    require_available(self._check_conflicts(
                        events, calendar, CalendarPeriod(change.start, change.end, change.time_zone),
                        exclude_event_id=event_id,
                    ))
                if not execute:
                    return result("preview", None)

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
                _, recovered = _execute_write(request, expected_id=event_id)
                after, _, after_transparency = self._schedule_snapshot(events, calendar, event_id, change.time_zone)
                if not self._has_requested_schedule(after, change) or transparency != after_transparency:
                    raise CalendarOperationError("A leitura final não confirmou os novos horários e a ocupação preservada.")
                return result("rescheduled", after, recovered=recovered)
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
        if not isinstance(item, dict):
            raise ValueError("Resposta de evento inválida.")
        event_id = item["id"]
        validate_event_id(event_id)
        start_data, end_data = item["start"], item["end"]
        if not isinstance(start_data, dict) or not isinstance(end_data, dict):
            raise ValueError("Horários inválidos no evento.")
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

    def get_update_snapshot(self, event_id: str, *, time_zone: str = "America/Recife") -> UpdateSnapshot:
        """Ler uma versão completa para conferir campos solicitados e preservados."""
        validate_event_id(event_id)
        load_zone(time_zone)
        try:
            calendar = resolve_calendar(self.service)
            item = self.service.events().get(
                calendarId=calendar["id"], eventId=event_id, timeZone=time_zone,
                fields=self.DETAILS_FIELDS + ",transparency",
            ).execute(num_retries=2)
            event, version = self._parse_details_snapshot(item, calendar, event_id, time_zone)
            transparency = item.get("transparency", "opaque")
            return validate_update_snapshot(UpdateSnapshot(calendar["id"], calendar["summary"],
                event, version, transparency), event_id, time_zone)
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google recusou a leitura da alteração (HTTP {exc.resp.status})."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarOperationError("Não foi possível conferir o estado da alteração.") from None
        except (KeyError, TypeError, ValueError):
            raise CalendarOperationError("A resposta não confirmou um estado válido para alteração.") from None

    def get_confirmation_snapshot(self, event_id: str | None, *, time_zone: str = "America/Recife"):
        """Ler identidade da agenda e ETag; esta operação nunca altera eventos."""
        load_zone(time_zone)
        if event_id is not None:
            validate_event_id(event_id)
        from app.services.confirmation_snapshot import (
            ConfirmationSnapshot, validate_confirmation_snapshot,
        )
        try:
            calendar = resolve_calendar(self.service)
            if event_id is None:
                snapshot = ConfirmationSnapshot(calendar["id"], calendar["summary"], None, None)
            else:
                current = self._deletion_snapshot(self.service.events(), calendar, event_id, time_zone)
                event, version = (None, None) if current is None else current
                snapshot = ConfirmationSnapshot(calendar["id"], calendar["summary"], event, version)
            return validate_confirmation_snapshot(snapshot, event_id, time_zone)
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google recusou a leitura da confirmação (HTTP {exc.resp.status})."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarOperationError("Não foi possível conferir o estado da confirmação.") from None
        except (KeyError, TypeError, ValueError):
            raise CalendarOperationError("A resposta não confirmou um estado válido para aprovação.") from None

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
            if not isinstance(item, dict) or item.get("id") != event_id:
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
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarOperationError(
                "Não foi possível confirmar o evento. Confira a conexão e tente novamente."
            ) from None
        except (KeyError, TypeError, ValueError):
            raise CalendarOperationError(
                "A resposta do Google não contém um evento válido; nenhum evento foi selecionado."
            ) from None

    TITLE_SEARCH_MAX_PAGES = 100
    TITLE_SEARCH_AMBIGUITY_LIMIT = 21

    def search_events_by_title(
        self, title: str, *, time_zone: str = "America/Recife",
    ) -> CalendarListing:
        """Buscar em toda a agenda; uma leitura incompleta nunca prova unicidade."""
        zone = load_zone(time_zone)
        if not isinstance(title, str) or not normalized_title(title):
            raise ValueError("Informe um título para buscar o compromisso.")
        needle = normalized_title(title)
        try:
            calendar = resolve_calendar(self.service)
            calendar_zone = load_zone(calendar.get("timeZone") or time_zone)
            events = self.service.events()
            collected: dict[str, tuple[datetime, AgendaEvent]] = {}
            observed: dict[str, dict] = {}
            masters: dict[str, None] = {}
            page_count = 0
            fields = "items(id,summary,status,start,end,htmlLink,recurrence,recurringEventId,originalStartTime),nextPageToken"

            def pages(master_id: str | None = None) -> Iterator[list[dict]]:
                nonlocal page_count
                page_token, seen_tokens = None, {None}
                while True:
                    if page_count >= self.TITLE_SEARCH_MAX_PAGES:
                        raise CalendarOperationError(
                            "A busca por título excedeu o limite de páginas e não foi concluída. "
                            "Informe o ID ou uma data para refinar a busca."
                        )
                    page_count += 1
                    arguments = {
                        "calendarId": calendar["id"], "timeZone": time_zone,
                        "showDeleted": False, "maxResults": 250,
                        "pageToken": page_token, "fields": fields,
                    }
                    # q pesquisa outros campos e não tem a mesma semântica da
                    # comparação local. Nenhum filtro de data pode provar que
                    # um título é único em toda a agenda.
                    request = (
                        events.list(singleEvents=False, showHiddenInvitations=True, **arguments)
                        if master_id is None else
                        events.instances(eventId=master_id, **arguments)
                    )
                    page = request.execute(num_retries=2)
                    if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                        raise ValueError("Página inválida na busca por título.")
                    next_token = page.get("nextPageToken")
                    if next_token not in (None, ""):
                        if (not isinstance(next_token, str) or not next_token.strip()
                                or next_token in seen_tokens):
                            raise ValueError("Paginação inválida na busca por título.")
                        seen_tokens.add(next_token)
                    yield page.get("items", [])
                    if next_token is None or next_token == "":
                        return
                    page_token = next_token

            def collect(item: dict, master_id: str | None = None) -> None:
                if not isinstance(item, dict):
                    raise ValueError("Evento inválido na busca por título.")
                event_id = item.get("id")
                validate_event_id(event_id)
                previous = observed.get(event_id)
                if previous is not None and previous != item:
                    raise ValueError("O evento mudou durante a busca por título.")
                observed[event_id] = item
                if item.get("status") == "cancelled":
                    return
                if item.get("status") not in ("confirmed", "tentative"):
                    raise ValueError("Estado desconhecido na busca por título.")
                recurrence = item.get("recurrence", [])
                if (not isinstance(recurrence, list)
                        or any(not isinstance(rule, str) or not rule.strip() for rule in recurrence)):
                    raise ValueError("Recorrência inválida na busca por título.")
                recurring_id = item.get("recurringEventId")
                if recurring_id is not None:
                    validate_event_id(recurring_id)
                    if recurrence:
                        raise ValueError("Uma ocorrência não pode ser uma série recorrente.")
                if ("originalStartTime" in item
                        and not isinstance(item["originalStartTime"], dict)):
                    raise ValueError("Origem inválida de ocorrência recorrente.")
                if master_id is not None and (recurrence or recurring_id != master_id):
                    raise ValueError("A resposta não confirma a série da ocorrência.")
                start_utc, _, entry = self._read_event(item, zone, calendar_zone)
                if needle not in normalized_title(entry.title):
                    return
                if recurrence:
                    masters[event_id] = None
                else:
                    collected[event_id] = (start_utc, entry)

            def listing() -> CalendarListing:
                ordered = sorted(collected.values(), key=lambda value: (value[0], value[1].event_id))
                return CalendarListing(calendar["summary"], tuple(entry for _, entry in ordered))

            # singleEvents=False inclui exceções com título próprio, mesmo
            # quando o título da série não corresponde ao pedido.
            for items in pages():
                for item in items:
                    collect(item)
                    if len(collected) >= self.TITLE_SEARCH_AMBIGUITY_LIMIT:
                        return listing()
            for master_id in masters:
                for items in pages(master_id):
                    for item in items:
                        collect(item, master_id)
                        if len(collected) >= self.TITLE_SEARCH_AMBIGUITY_LIMIT:
                            return listing()
            return listing()
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google recusou a busca por título (HTTP {exc.resp.status}). Nenhum resultado foi confirmado."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarOperationError(
                "Não foi possível concluir a busca por título. Confira a conexão e tente novamente."
            ) from None
        except (KeyError, TypeError, ValueError, OverflowError):
            raise CalendarOperationError(
                "A resposta do Google contém dados ou paginação inválidos; busca por título não concluída."
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
                if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                    raise ValueError("Página inválida na consulta.")
                for item in page.get("items", []):
                    if not isinstance(item, dict):
                        raise ValueError("Evento inválido na consulta.")
                    validate_event_id(item.get("id"))
                    if item.get("status") == "cancelled":
                        continue
                    if item.get("status") not in ("confirmed", "tentative"):
                        raise ValueError("Estado desconhecido na consulta.")
                    start_utc, end_utc, entry = self._read_event(item, zone, calendar_zone)
                    # Sobreposição: inclui madrugada e dias inteiros. O fim é exclusivo.
                    if (start_utc >= period.end.astimezone(timezone.utc)
                            or end_utc <= period.start.astimezone(timezone.utc)):
                        continue
                    previous = collected.get(entry.event_id)
                    if previous is not None and previous[1] != entry:
                        raise ValueError("O evento mudou durante a consulta.")
                    collected[entry.event_id] = (start_utc, entry)
                next_token = page.get("nextPageToken")
                if next_token is None or next_token == "":
                    break
                if not isinstance(next_token, str) or not next_token.strip() or next_token in seen_tokens:
                    raise CalendarOperationError("O Google repetiu uma página; consulta não concluída.")
                seen_tokens.add(next_token)
                page_token = next_token
            ordered = sorted(collected.values(), key=lambda value: (value[0], value[1].event_id))
            return CalendarListing(calendar["summary"], tuple(entry for _, entry in ordered))
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google recusou a consulta (HTTP {exc.resp.status}). Nenhum resultado foi confirmado."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarOperationError(
                "Não foi possível concluir a consulta. Confira a conexão e tente novamente."
            ) from None
        except (KeyError, TypeError, ValueError):
            raise CalendarOperationError(
                "A resposta do Google contém dados de evento ou fuso inválidos; consulta não concluída."
            ) from None

    @staticmethod
    def _validate_creation_calendar_id(value: str) -> str:
        if (not isinstance(value, str) or not value or len(value) > 1024
                or any(char.isspace() or unicodedata.category(char).startswith("C") for char in value)):
            raise ValueError("Informe a identidade válida da agenda vinculada.")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Informe a identidade válida da agenda vinculada.") from None
        return value

    def create_event(
        self, draft: InterviewDraft, *, operation_id: str,
        expected_calendar_id: str | None = None,
    ) -> CreatedInterview:
        validate_interview(draft, operation_id)
        if expected_calendar_id is not None:
            self._validate_creation_calendar_id(expected_calendar_id)
        calendar = resolve_calendar(self.service)
        if expected_calendar_id is not None and calendar["id"] != expected_calendar_id:
            raise CalendarBindingError("A agenda configurada mudou. Prepare o pedido novamente antes de criar.")
        # Hexadecimal atende ao alfabeto base32hex exigido pelo Google.
        event_id = "genius" + sha256(operation_id.encode("utf-8")).hexdigest()
        body = {
            "id": event_id,
            "summary": unicodedata.normalize("NFC", draft.title.strip()),
            "description": unicodedata.normalize("NFC", draft.description),
            "start": {"dateTime": draft.start.isoformat(), "timeZone": draft.time_zone},
            "end": {"dateTime": draft.end.isoformat(), "timeZone": draft.time_zone},
            "visibility": "private",
            "reminders": {"useDefault": False},
        }
        fingerprint = sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        request_hash = creation_content_hash(draft.title, draft.description, draft.start, draft.end)
        # Esta transação curta precisa estar confirmada antes de qualquer POST:
        # a memória do ID não pode desaparecer em timeout/rollback/crash.
        try:
            reservation = database.reserve_creation(
                calendar["id"], sha256(operation_id.encode("utf-8")).hexdigest(),
                request_hash, event_id, fingerprint,
            )
        except CreationIdentityError as exc:
            raise CalendarOperationError(str(exc)) from None
        except sqlite3.Error:
            raise CalendarOperationError("Não foi possível registrar o pedido localmente; nenhuma criação foi enviada.") from None
        event_id = reservation.event_id
        body["id"] = event_id
        body["extendedProperties"] = {"private": {"geniusRequest": reservation.fingerprint}}
        events = self.service.events()
        try:
            # Na repetição, consultar primeiro: uma tentativa anterior pode ter
            # sido gravada no Google mesmo se a resposta se perdeu na rede.
            with calendar_write_lock() as connection:
                current = database.get_creation(calendar["id"], request_hash, connection=connection)
                if (not isinstance(current, CreationRecord)
                        or (current.calendar_id, current.request_hash, current.event_id, current.fingerprint)
                        != (reservation.calendar_id, request_hash, event_id, reservation.fingerprint)
                        or current.state not in ("pending", "confirmed")):
                    raise CalendarOperationError("O registro local do pedido não pôde ser confirmado; nenhuma criação foi enviada.")
                existing = self._find_event(events, calendar["id"], event_id)
                if existing is not None:
                    result = self._result(existing, calendar, body, reused=True)
                    database.confirm_creation(current, connection=connection)
                    return result
                if current.state == "confirmed":
                    raise CalendarOperationError(
                        "Este pedido já criou um evento, mas ele não está mais disponível. "
                        "Confira o ID; repetir o pedido não recria um evento removido."
                    )
                require_available(self._check_conflicts(
                    events, calendar, CalendarPeriod(draft.start, draft.end, draft.time_zone),
                    exclude_event_id=event_id,
                ))
                try:
                    request = events.insert(
                        calendarId=calendar["id"], body=body,
                        sendUpdates="none", fields=self.EVENT_FIELDS,
                    )
                    event, recovered = _execute_write(request, expected_id=event_id)
                except HttpError as exc:
                    if exc.resp.status != 409:
                        raise
                    # Outra execução externa pode ter criado o mesmo ID.
                    event = self._find_event(events, calendar["id"], event_id)
                    if event is None:
                        raise CalendarOperationError(
                            "O ID já está em uso, mas o evento não pôde ser confirmado."
                        ) from None
                    result = self._result(event, calendar, body, reused=True)
                    database.confirm_creation(current, connection=connection)
                    return result
                if not recovered:
                    try:
                        result = self._result(event, calendar, body, reused=False)
                    except CalendarOperationError:
                        recovered = True
                if recovered:
                    event = self._find_event(events, calendar["id"], event_id)
                    if event is None:
                        raise CalendarOperationError(
                            "A criação não foi confirmada na consulta pelo ID. "
                            "O evento pode ter sido salvo; consulte novamente antes de repetir."
                        )
                    result = replace(self._result(event, calendar, body, reused=False), recovered=True)
                database.confirm_creation(current, connection=connection)
                return result
        except CreationIdentityError as exc:
            raise CalendarOperationError(
                f"{exc} Consulte o ID antes de repetir; a criação pode ter sido aplicada."
            ) from None
        except HttpError as exc:
            raise CalendarOperationError(
                f"Google não confirmou a operação (HTTP {exc.resp.status}). "
                "Confira o acesso e reutilize o mesmo identificador ao tentar novamente."
            ) from None
        except (OSError, httplib2.HttpLib2Error, TransportError):
            raise CalendarOperationError(
                "Não foi possível confirmar a criação. O evento pode ter sido salvo; "
                "tente novamente com o mesmo identificador para consultar o resultado."
            ) from None

    def reconcile_creation(
        self, draft: InterviewDraft, *, operation_id: str, expected_calendar_id: str,
    ) -> ReconciledCreation:
        """Conferir uma identidade persistida sem reservar, inserir ou repetir POST.

        O registro global pode ter sido criado por outra operação de conteúdo
        idêntico. Seu ID e fingerprint originais são a referência da leitura.
        Ausência ou falta do registro nunca autorizam outra gravação por aqui.
        """
        validate_interview(draft, operation_id)
        self._validate_creation_calendar_id(expected_calendar_id)
        calendar = resolve_calendar(self.service)
        if calendar["id"] != expected_calendar_id:
            raise CalendarBindingError("A agenda configurada mudou. Confira o pedido antes de continuar.")
        request_hash = creation_content_hash(draft.title, draft.description, draft.start, draft.end)
        try:
            record = database.get_creation(calendar["id"], request_hash)
            if record is None:
                return ReconciledCreation("unregistered")
            if (not isinstance(record, CreationRecord) or record.calendar_id != calendar["id"]
                    or record.request_hash != request_hash or record.state not in {"pending", "confirmed"}
                    or not isinstance(record.fingerprint, str) or len(record.fingerprint) != 64
                    or any(char not in "0123456789abcdef" for char in record.fingerprint)):
                raise CalendarOperationError("O registro da criação não pôde ser conferido.")
            validate_event_id(record.event_id)
            body = {
                "id": record.event_id,
                "summary": unicodedata.normalize("NFC", draft.title.strip()),
                "description": unicodedata.normalize("NFC", draft.description),
                "start": {"dateTime": draft.start.isoformat(), "timeZone": draft.time_zone},
                "end": {"dateTime": draft.end.isoformat(), "timeZone": draft.time_zone},
                "extendedProperties": {"private": {"geniusRequest": record.fingerprint}},
            }
            found = self._find_event(self.service.events(), calendar["id"], record.event_id)
            if found is None:
                return ReconciledCreation("absent")
            result = replace(self._result(found, calendar, body, reused=True), recovered=True)
            return ReconciledCreation("found", result)
        except CalendarOperationError:
            raise
        except (CreationIdentityError, sqlite3.Error, HttpError, OSError,
                httplib2.HttpLib2Error, TransportError, KeyError, TypeError, ValueError):
            raise CalendarOperationError("Não foi possível conferir o resultado da criação; nenhuma nova criação foi enviada.") from None

    def _find_event(self, events: Any, calendar_id: str, event_id: str) -> dict | None:
        try:
            item = events.get(
                calendarId=calendar_id, eventId=event_id, fields=self.EVENT_FIELDS
            ).execute(num_retries=2)
            if not isinstance(item, dict) or item.get("id") != event_id:
                raise CalendarOperationError("A consulta não confirmou o ID e o estado do evento. Confira o ID antes de repetir.")
            return item
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise
        except (KeyError, TypeError, ValueError):
            raise CalendarOperationError("Dados inválidos na consulta do evento. Confira o ID antes de repetir.") from None

    @staticmethod
    def _result(event: dict, calendar: dict, body: dict, *, reused: bool) -> CreatedInterview:
        if not isinstance(event, dict):
            raise CalendarOperationError("A resposta não confirma o evento criado; consulte o ID antes de repetir.")
        try:
            same_request = (
                event.get("extendedProperties", {}).get("private", {}).get("geniusRequest")
                == body["extendedProperties"]["private"]["geniusRequest"]
            )
            same_details = creation_content_hash(
                event["summary"], event.get("description", ""),
                datetime.fromisoformat(event["start"]["dateTime"]),
                datetime.fromisoformat(event["end"]["dateTime"]),
            ) == creation_content_hash(
                body["summary"], body["description"],
                datetime.fromisoformat(body["start"]["dateTime"]),
                datetime.fromisoformat(body["end"]["dateTime"]),
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            same_request = same_details = False
        if (event.get("id") != body["id"] or event.get("status") != "confirmed"
                or not same_request or not same_details
                or not isinstance(event.get("htmlLink"), str) or not event["htmlLink"].strip()):
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
