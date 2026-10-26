"""Leitura completa da agenda para preparar lembretes, sem escrita no Google.

``resolve_calendar`` preserva a seleção autorizada do conector existente e
pode inicializar/gravar os metadados locais ``google_calendar_id/name`` do
banco legado. Este adaptador não altera eventos, configurações ou ledger.
Ausência na listagem nunca é prova de exclusão/cancelamento de um evento.
"""

from dataclasses import dataclass
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json
import re
from typing import Any

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
import httplib2

from app.google_calendar import CalendarSetupError, resolve_calendar
from app.services.event_selection import validate_event_id
from app.services.reminders import ReminderCalendarEvent, ReminderCalendarSnapshot
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
from app.services.time_rules import load_zone, validate_interval


class ReminderCalendarError(RuntimeError):
    """Consulta não confirmou um snapshot completo e utilizável."""


@dataclass(frozen=True, repr=False)
class ReminderCalendarBinding:
    calendar_id: str
    calendar_timezone: str


_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
    flags=re.ASCII,
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", flags=re.ASCII)


def _identity(value: Any) -> str:
    validate_event_id(value)
    value.encode("utf-8")
    return value


def _instant(part: Any, calendar_zone: Any) -> tuple[datetime, bool]:
    if not isinstance(part, dict):
        raise ValueError
    has_date, has_time = "date" in part, "dateTime" in part
    if has_date == has_time:
        raise ValueError
    declared_zone = part.get("timeZone")
    if "timeZone" in part:
        zone = load_zone(declared_zone)
    else:
        zone = None
    if has_date:
        value = part["date"]
        if not isinstance(value, str) or _DATE.fullmatch(value) is None:
            raise ValueError
        # Datas de dia inteiro pertencem ao fuso da agenda, não ao host/chat.
        if zone is not None and zone.key != calendar_zone.key:
            raise ValueError
        instant = datetime.combine(date.fromisoformat(value), time.min, tzinfo=calendar_zone)
        local = instant.astimezone(timezone.utc).astimezone(calendar_zone)
        if local.replace(tzinfo=None) != instant.replace(tzinfo=None):
            raise ValueError
        return instant.astimezone(timezone.utc), True
    value = part["dateTime"]
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() is None:
        raise ValueError
    # ``events.list(timeZone=...)`` pode projetar dateTime no fuso da
    # resposta e preservar timeZone original/de expansão da ocorrência.
    # O offset explícito define o instante; o IANA declarado foi validado
    # acima, mas não redefine essa representação retornada pelo Google.
    return instant.astimezone(timezone.utc), False


class GoogleReminderCalendarGateway:
    """Consulta limitada e atômica do ponto de vista do chamador.

    Falha, limite, página repetida ou conflito entre cópias interrompe toda a
    leitura. Nenhuma tupla parcial escapa. ``singleEvents`` pede ao Google
    as ocorrências; regras de recorrência nunca são interpretadas aqui.
    """

    MAX_PAGES = 20
    MAX_EVENTS = 2000
    MAX_LOOKUP_EVENTS = 200
    EVENT_FIELDS = (
        "items(id,summary,status,start,end,recurrence,recurringEventId,originalStartTime),"
        "nextPageToken"
    )
    LOOKUP_FIELDS = (
        "id,summary,status,start,end,recurrence,recurringEventId,originalStartTime"
    )
    CALENDAR_CHECK_FIELDS = "id,timeZone,accessRole,primary,deleted"

    def __init__(self, service: Any):
        self.service = service

    def binding(self) -> ReminderCalendarBinding:
        """Ler o ID efetivo e o fuso para configuração explícita do perfil."""
        try:
            return self._binding()
        except (CalendarSetupError, KeyError, TypeError, ValueError, OverflowError,
                UnicodeError, OSError, httplib2.HttpLib2Error, TransportError, HttpError):
            raise ReminderCalendarError("Não foi possível confirmar a agenda e seu fuso.") from None

    def _binding(self) -> ReminderCalendarBinding:
        calendar = resolve_calendar(self.service)
        if not isinstance(calendar, dict):
            raise ValueError
        calendar_id = _identity(calendar["id"])
        if calendar_id == "primary":
            raise ValueError
        calendar_timezone = calendar["timeZone"]
        load_zone(calendar_timezone)
        return ReminderCalendarBinding(calendar_id, calendar_timezone)

    @staticmethod
    def _event(item: dict, calendar_zone: Any) -> ReminderCalendarEvent:
        event_id = _identity(item["id"])
        if item["status"] not in ("confirmed", "tentative"):
            raise ValueError
        # O recurso da série não possui identidade própria de ocorrência.
        if "recurrence" in item:
            raise ValueError
        start_utc, all_day = _instant(item["start"], calendar_zone)
        end_utc, end_all_day = _instant(item["end"], calendar_zone)
        if all_day != end_all_day or end_utc <= start_utc:
            raise ValueError
        recurring_event_id = item.get("recurringEventId")
        if "recurringEventId" in item or "originalStartTime" in item:
            recurring_event_id = _identity(recurring_event_id)
            if recurring_event_id == event_id:
                raise ValueError
            _, original_all_day = _instant(item["originalStartTime"], calendar_zone)
            if original_all_day != all_day:
                raise ValueError
        title = item.get("summary", "")
        if not isinstance(title, str):
            raise ValueError
        title.encode("utf-8")
        return ReminderCalendarEvent(
            event_id=event_id, title=title, start_utc=start_utc, end_utc=end_utc,
            all_day=all_day, status=item["status"],
            recurring_event_id=recurring_event_id, expanded=True,
        )

    def _verify_calendar_access(self, binding: ReminderCalendarBinding) -> None:
        """404/410 de evento só prova ausência com agenda ainda acessível.

        CalendarList usa o escopo de leitura já autorizado. Repetir a
        descoberta por nome ou consultar calendars.get ampliaria o contrato.
        """
        calendar = self.service.calendarList().get(
            calendarId=binding.calendar_id, fields=self.CALENDAR_CHECK_FIELDS,
        ).execute(num_retries=2)
        if (not isinstance(calendar, dict)
                or _identity(calendar.get("id")) != binding.calendar_id
                or calendar.get("accessRole") != "owner"
                or calendar.get("primary", False) is not False
                or calendar.get("deleted", False) is not False):
            raise ValueError
        calendar_timezone = calendar["timeZone"]
        load_zone(calendar_timezone)
        if calendar_timezone != binding.calendar_timezone:
            raise ValueError

    def lookup_snapshot(
        self, event_ids: tuple[str, ...], expected_calendar_id: str,
    ) -> ReminderLookupSnapshot:
        """Reler IDs exatos, sem inferir exclusão pela listagem de uma faixa.

        A tupla inteira é validada antes da primeira consulta. Uma falha em
        qualquer recurso descarta o lote. Apenas 404 ou 410 ``deleted`` no
        GET do evento, seguido da confirmação da agenda, prova ``absent``.
        Não existe transação externa: eventos ou permissões ainda podem
        mudar depois das leituras, inclusive entre os GETs deste lote.
        """
        try:
            if type(event_ids) is not tuple or len(event_ids) > self.MAX_LOOKUP_EVENTS:
                raise ValueError
            _identity(expected_calendar_id)
            if expected_calendar_id == "primary":
                raise ValueError
            seen = set()
            for event_id in event_ids:
                _identity(event_id)
                if event_id in seen:
                    raise ValueError
                seen.add(event_id)
            binding = self._binding()
            if binding.calendar_id != expected_calendar_id:
                raise ValueError
            zone = load_zone(binding.calendar_timezone)
            lookups = []
            missing = False
            events = self.service.events() if event_ids else None
            for event_id in event_ids:
                request = events.get(
                    calendarId=binding.calendar_id, eventId=event_id,
                    timeZone=binding.calendar_timezone, fields=self.LOOKUP_FIELDS,
                )
                try:
                    item = request.execute(num_retries=2)
                except HttpError as exc:
                    if exc.resp.status == 410:
                        # Outros 410 sinalizam problemas de sincronização,
                        # não exclusão do recurso consultado por ID exato.
                        content = json.loads(exc.content)
                        detail = content.get("error") if isinstance(content, dict) else None
                        reasons = detail.get("errors") if isinstance(detail, dict) else None
                        if (not isinstance(reasons, list) or not reasons
                                or any(not isinstance(reason, dict)
                                       or reason.get("reason") != "deleted" for reason in reasons)):
                            raise ValueError
                    elif exc.resp.status != 404:
                        raise
                    missing = True
                    lookups.append(ReminderEventLookup(event_id, "absent", None))
                    continue
                if (not isinstance(item, dict)
                        or _identity(item.get("id")) != event_id
                        or "recurrence" in item):
                    raise ValueError
                if item.get("status") == "cancelled":
                    # Tombstones legítimos podem conter somente ID e status.
                    lookups.append(ReminderEventLookup(event_id, "cancelled", None))
                else:
                    lookups.append(ReminderEventLookup(event_id, "found", self._event(item, zone)))
            if missing:
                self._verify_calendar_access(binding)
            return ReminderLookupSnapshot(
                calendar_id=binding.calendar_id,
                calendar_timezone=binding.calendar_timezone, lookups=tuple(lookups),
            )
        except Exception:
            # Também sanitizar exceções inesperadas do cliente e payloads
            # malformados; nunca devolver dados de provider ou tupla parcial.
            raise ReminderCalendarError("Não foi possível confirmar a consulta exata dos eventos.") from None

    def snapshot(
        self, start: datetime, end: datetime, expected_calendar_id: str | None = None,
    ) -> ReminderCalendarSnapshot:
        """Confirmar todos os eventos na janela UTC, sem cancelar por ausência."""
        try:
            validate_interval(start, end)
            start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
            # O Google ignora frações nos filtros: ampliar para segundos
            # completos, depois aplicar a janela exata localmente.
            query_start = start_utc.replace(microsecond=0)
            query_end = end_utc.replace(microsecond=0)
            if end_utc.microsecond:
                query_end += timedelta(seconds=1)
            if expected_calendar_id is not None:
                _identity(expected_calendar_id)
            binding = self._binding()
            if (expected_calendar_id is not None
                    and binding.calendar_id != expected_calendar_id):
                raise ReminderCalendarError("A agenda vinculada não corresponde à configuração.")
            zone = load_zone(binding.calendar_timezone)
            events = self.service.events()
            collected: dict[str, ReminderCalendarEvent] = {}
            # Também conferir cópias ignoradas; cancelled não traz start/end.
            observed: dict[str, dict] = {}
            page_token = None
            seen_tokens: set[str] = set()
            seen_items = 0
            for page_index in range(self.MAX_PAGES):
                page = events.list(
                    calendarId=binding.calendar_id,
                    timeMin=query_start.isoformat(), timeMax=query_end.isoformat(),
                    timeZone=binding.calendar_timezone,
                    singleEvents=True, showDeleted=False, orderBy="startTime",
                    maxResults=250, pageToken=page_token, fields=self.EVENT_FIELDS,
                ).execute(num_retries=2)
                if not isinstance(page, dict) or not isinstance(page.get("items", []), list):
                    raise ValueError
                for item in page.get("items", []):
                    seen_items += 1
                    if seen_items > self.MAX_EVENTS or not isinstance(item, dict):
                        raise ValueError
                    event_id = _identity(item.get("id"))
                    previous = observed.get(event_id)
                    if previous is not None and previous != item:
                        raise ValueError
                    observed[event_id] = deepcopy(item)
                    status = item.get("status")
                    if status == "cancelled":
                        continue
                    entry = self._event(item, zone)
                    if entry.start_utc >= end_utc or entry.end_utc <= start_utc:
                        continue
                    collected[event_id] = entry
                next_token = page.get("nextPageToken")
                if next_token is None or next_token == "":
                    ordered = tuple(sorted(
                        collected.values(), key=lambda entry: (entry.start_utc, entry.event_id),
                    ))
                    return ReminderCalendarSnapshot(
                        calendar_id=binding.calendar_id,
                        calendar_timezone=binding.calendar_timezone, events=ordered,
                    )
                if (not isinstance(next_token, str) or not next_token
                        or next_token != next_token.strip() or len(next_token) > 4096
                        or any(char.isspace() or ord(char) < 32 or ord(char) == 127
                               for char in next_token)
                        or next_token in seen_tokens or page_index + 1 == self.MAX_PAGES):
                    raise ValueError
                seen_tokens.add(next_token)
                page_token = next_token
            raise ValueError
        except ReminderCalendarError:
            raise
        except (CalendarSetupError, KeyError, TypeError, ValueError, OverflowError,
                UnicodeError, OSError, httplib2.HttpLib2Error, TransportError, HttpError):
            raise ReminderCalendarError("Não foi possível confirmar a consulta completa da agenda.") from None
