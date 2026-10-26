"""Consulta limitada e planejamento local dos avisos; não envia mensagens.

A fotografia inteira é validada antes de planejar o lote. Ausência na faixa,
mudança de início e cancelamento não reconciliam avisos antigos nesta etapa.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import unicodedata
from zoneinfo import ZoneInfo

from app.reminder_store import ReminderPlan


_ERROR = "Não foi possível gerar os lembretes com segurança."
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
MAX_EVENTS = 2000
PLANNING_WINDOW = timedelta(days=8)


class ReminderGenerationError(RuntimeError):
    """Falha sanitizada; não contém agenda, títulos, destinatário ou provedor."""

    def __init__(self):
        super().__init__(_ERROR)


class _Private:
    def __repr__(self):
        return type(self).__name__ + "(<private>)"


@dataclass(frozen=True, repr=False)
class ReminderCalendarEvent(_Private):
    event_id: str
    title: str
    start_utc: datetime
    end_utc: datetime
    all_day: bool
    status: str
    recurring_event_id: str | None = None
    expanded: bool = True


@dataclass(frozen=True, repr=False)
class ReminderCalendarSnapshot(_Private):
    calendar_id: str
    calendar_timezone: str
    events: tuple[ReminderCalendarEvent, ...]


@dataclass(frozen=True)
class ReminderGenerationSummary:
    status: str
    events_seen: int = 0
    eligible_events: int = 0
    skipped_count: int = 0
    reminders_considered: int = 0
    state_counts: tuple[tuple[str, int], ...] = ()


def _identifier(value, limit=1024):
    if (not isinstance(value, str) or not 1 <= len(value) <= limit
            or not value.strip()
            or any(unicodedata.category(char).startswith("C") for char in value)):
        raise ReminderGenerationError()
    value.encode("utf-8")
    return value


def _instant(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ReminderGenerationError()
    return value.astimezone(timezone.utc)


def _event(value):
    if type(value) is not ReminderCalendarEvent:
        raise ReminderGenerationError()
    _identifier(value.event_id)
    if not isinstance(value.title, str) or len(value.title) > 10000:
        raise ReminderGenerationError()
    value.title.encode("utf-8")
    if (type(value.all_day) is not bool or type(value.expanded) is not bool
            or not isinstance(value.status, str)
            or value.status not in {"confirmed", "tentative", "cancelled"}):
        raise ReminderGenerationError()
    if value.recurring_event_id is not None:
        _identifier(value.recurring_event_id)
        if value.expanded and value.recurring_event_id == value.event_id:
            raise ReminderGenerationError()
    start, end = _instant(value.start_utc), _instant(value.end_utc)
    if end <= start:
        raise ReminderGenerationError()
    return ReminderCalendarEvent(value.event_id, value.title, start, end,
        value.all_day, value.status, value.recurring_event_id, value.expanded)


class ReminderPlanningService:
    """Produz somente estado local usando política explícita e fotografia atual."""

    def __init__(self, store, gateway, *, expected_recipient_key, clock=None):
        if (not isinstance(expected_recipient_key, str)
                or _HASH.fullmatch(expected_recipient_key) is None
                or clock is not None and not callable(clock)):
            raise ReminderGenerationError()
        self._store = store
        self._gateway = gateway
        self._recipient = expected_recipient_key
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def generate(self) -> ReminderGenerationSummary:
        """Falhar fechado, sem aplicar um lote parcial nem alterar avisos ausentes."""
        try:
            return self._generate()
        except Exception:
            raise ReminderGenerationError() from None

    def _generate(self):
        config = self._store.config()
        if config is None:
            return ReminderGenerationSummary("not_configured")
        if not config.enabled:
            return ReminderGenerationSummary("disabled")
        if not config.permission_granted:
            return ReminderGenerationSummary("permission_required")
        if config.recipient_key != self._recipient:
            raise ReminderGenerationError()
        now = _instant(self._clock())
        end = now + PLANNING_WINDOW
        snapshot = self._gateway.snapshot(now, end, expected_calendar_id=config.calendar_id)
        if type(snapshot) is not ReminderCalendarSnapshot:
            raise ReminderGenerationError()
        if snapshot.calendar_id != config.calendar_id:
            raise ReminderGenerationError()
        _identifier(snapshot.calendar_timezone, 128)
        ZoneInfo(snapshot.calendar_timezone)
        if type(snapshot.events) is not tuple or len(snapshot.events) > MAX_EVENTS:
            raise ReminderGenerationError()
        unique = {}
        for raw in snapshot.events:
            event = _event(raw)
            previous = unique.get(event.event_id)
            if previous is not None and previous != event:
                raise ReminderGenerationError()
            unique[event.event_id] = event
        # A consulta pode ser lenta: a hora anterior não permite planejar evento
        # que iniciou durante a leitura. A política também pode ter mudado.
        current_now = _instant(self._clock())
        if current_now < now or self._store.config() != config:
            raise ReminderGenerationError()
        eligible = tuple(event for event in unique.values()
            if event.status == "confirmed" and not event.all_day and event.expanded
            and current_now < event.start_utc < end)
        plans = tuple(ReminderPlan(event.event_id, event.start_utc, slot,
            event.title, snapshot.calendar_timezone)
            for event in eligible for slot, minutes in config.offsets if minutes is not None)
        # O CAS da revisão ocorre na mesma transação de todos os planos; a
        # releitura acima sozinha não protegeria a corrida até a primeira escrita.
        records = self._store.plan_batch(plans, config_revision=config.revision)
        counts = tuple(sorted(Counter(record.state for record in records).items()))
        return ReminderGenerationSummary("planned", len(snapshot.events), len(eligible),
            len(snapshot.events) - len(eligible), len(records), counts)
