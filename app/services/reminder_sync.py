"""Reconciliação explícita por ID exato, sem descoberta ou transporte."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

from app.reminder_store import ReminderPlan, ReminderSyncCancellation
from app.services.reminders import ReminderCalendarEvent, _event, _identifier, _instant


_ERROR = "Não foi possível sincronizar os lembretes com segurança."
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
MAX_SYNC_EVENTS = 200


class ReminderSynchronizationError(RuntimeError):
    """Falha pública sanitizada, sem IDs, conteúdo ou dados do provedor."""

    def __init__(self):
        super().__init__(_ERROR)


class _Private:
    def __repr__(self):
        return type(self).__name__ + "(<private>)"


@dataclass(frozen=True, repr=False)
class ReminderEventLookup(_Private):
    event_id: str
    outcome: str
    event: ReminderCalendarEvent | None = None


@dataclass(frozen=True, repr=False)
class ReminderLookupSnapshot(_Private):
    calendar_id: str
    calendar_timezone: str
    lookups: tuple[ReminderEventLookup, ...]


@dataclass(frozen=True)
class ReminderSynchronizationSummary:
    status: str
    events_checked: int = 0
    tracked_reminders: int = 0
    reminders_considered: int = 0
    cancelled_count: int = 0
    replanned_count: int = 0
    unchanged_count: int = 0
    state_counts: tuple[tuple[str, int], ...] = ()


class ReminderSynchronizationService:
    """Valida todas as leituras antes de aplicar uma reconciliação atômica."""

    def __init__(self, store, gateway, *, expected_recipient_key, clock=None):
        if (not isinstance(expected_recipient_key, str)
                or _HASH.fullmatch(expected_recipient_key) is None
                or clock is not None and not callable(clock)):
            raise ReminderSynchronizationError()
        self._store = store
        self._gateway = gateway
        self._recipient = expected_recipient_key
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def sync(self) -> ReminderSynchronizationSummary:
        try:
            return self._sync()
        except Exception:
            raise ReminderSynchronizationError() from None

    def _sync(self):
        local = self._store.sync_snapshot()
        config = local.config
        if config is None:
            return ReminderSynchronizationSummary("not_configured")
        if not config.enabled:
            return ReminderSynchronizationSummary("disabled")
        if not config.permission_granted:
            return ReminderSynchronizationSummary("permission_required")
        if config.recipient_key != self._recipient:
            raise ReminderSynchronizationError()
        if not local.records:
            return ReminderSynchronizationSummary("synchronized")
        event_ids = tuple(sorted({record.event_id for record in local.records}))
        if len(event_ids) > MAX_SYNC_EVENTS:
            raise ReminderSynchronizationError()
        now = _instant(self._clock())
        if any(record.updated_at > now for record in local.records):
            raise ReminderSynchronizationError()
        remote = self._gateway.lookup_snapshot(event_ids, expected_calendar_id=config.calendar_id)
        if type(remote) is not ReminderLookupSnapshot or remote.calendar_id != config.calendar_id:
            raise ReminderSynchronizationError()
        _identifier(remote.calendar_timezone, 128)
        ZoneInfo(remote.calendar_timezone)
        if type(remote.lookups) is not tuple or len(remote.lookups) != len(event_ids):
            raise ReminderSynchronizationError()
        lookups = {}
        for lookup in remote.lookups:
            if type(lookup) is not ReminderEventLookup or lookup.event_id not in event_ids:
                raise ReminderSynchronizationError()
            _identifier(lookup.event_id)
            if lookup.event_id in lookups or lookup.outcome not in {"found", "cancelled", "absent"}:
                raise ReminderSynchronizationError()
            if lookup.outcome == "found":
                event = _event(lookup.event)
                if event.event_id != lookup.event_id or event.status == "cancelled":
                    raise ReminderSynchronizationError()
                lookups[lookup.event_id] = event
            else:
                if lookup.event is not None:
                    raise ReminderSynchronizationError()
                lookups[lookup.event_id] = None
        if set(lookups) != set(event_ids):
            raise ReminderSynchronizationError()
        current_now = _instant(self._clock())
        if current_now < now:
            raise ReminderSynchronizationError()
        plans, cancellations = [], []
        for event_id in event_ids:
            event = lookups[event_id]
            records = (record for record in local.records if record.event_id == event_id)
            if event is None:
                cancellations.extend(ReminderSyncCancellation(record.key, "event_cancelled")
                                     for record in records)
            elif (event.status != "confirmed" or event.all_day or not event.expanded
                    or event.start_utc <= current_now):
                cancellations.extend(ReminderSyncCancellation(record.key, "event_changed")
                                     for record in records)
            else:
                cancellations.extend(ReminderSyncCancellation(record.key, "event_rescheduled")
                    for record in records if record.start_at != event.start_utc)
                plans.extend(ReminderPlan(event.event_id, event.start_utc, slot,
                    event.title, remote.calendar_timezone)
                    for slot, minutes in config.offsets if minutes is not None)
        # apply_sync compara perfil, registros completos e histórico dentro da
        # mesma transação de todas as escritas, inclusive para um lote vazio.
        result = self._store.apply_sync(local, tuple(plans), tuple(cancellations))
        counts = tuple(sorted(Counter(record.state for record in result.records).items()))
        return ReminderSynchronizationSummary("synchronized", len(event_ids), len(local.records),
            len(result.records), result.cancelled_count, result.replanned_count,
            result.unchanged_count, counts)
