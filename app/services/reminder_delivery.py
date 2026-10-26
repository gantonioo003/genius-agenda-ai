"""Entrega de lembretes por identidade persistente, sem retry ou timer.

Aceite não comprova entrega. Toda falha após a transição sending conserva
incerteza; uma reserva de execução anterior nunca é retomada automaticamente.
"""

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
from inspect import getattr_static
from math import ceil
import re
import unicodedata
from zoneinfo import ZoneInfo

from app.reminder_store import ChannelObservation
from app.chat_formatting import clock_time, visible_zone_label
from app.services.reminder_sync import ReminderEventLookup, ReminderLookupSnapshot
from app.services.reminder_tick import reminder_tick_lock
from app.services.reminders import _event, _identifier, _instant


_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PHONE = re.compile(r"[1-9][0-9]{6,14}\Z", re.ASCII)
_COUNTERS = ("reminders_considered", "expired_count", "cancelled_count", "blocked_count",
             "accepted_count", "uncertain_count", "failed_count", "transport_attempts")
MAX_DELIVERY_CANDIDATES = 200
CHANNEL_WINDOW = timedelta(hours=24)
CHANNEL_MARGIN = timedelta(seconds=60)


class ReminderDeliveryError(RuntimeError):
    def __init__(self):
        super().__init__("Não foi possível concluir o envio dos lembretes com segurança.")


@dataclass(frozen=True, repr=False)
class ReminderRecipient:
    recipient: str
    binding: str

    def __repr__(self):
        return "ReminderRecipient(<private>)"


@dataclass(frozen=True)
class ReminderDeliverySummary:
    dispatch_status: str
    skip_reason: str | None = None
    reminders_considered: int = 0
    expired_count: int = 0
    cancelled_count: int = 0
    blocked_count: int = 0
    accepted_count: int = 0
    uncertain_count: int = 0
    failed_count: int = 0
    transport_attempts: int = 0

    def public_data(self):
        if (self.dispatch_status not in {"completed", "skipped", "failed", "partial"}
                or any(type(getattr(self, name)) is not int or getattr(self, name) < 0
                       for name in _COUNTERS)
                or self.transport_attempts != self.accepted_count + self.uncertain_count):
            raise ReminderDeliveryError()
        if self.dispatch_status == "skipped":
            if (self.skip_reason not in {"not_configured", "disabled", "permission_required", "busy"}
                    or any(getattr(self, name) for name in _COUNTERS)):
                raise ReminderDeliveryError()
        elif self.skip_reason is not None:
            raise ReminderDeliveryError()
        if (self.dispatch_status == "completed" and (self.failed_count or self.uncertain_count)
                or self.dispatch_status in {"failed", "partial"}
                and not (self.failed_count or self.uncertain_count)):
            raise ReminderDeliveryError()
        return {"dispatch_status": self.dispatch_status, "skip_reason": self.skip_reason,
                **{name: getattr(self, name) for name in _COUNTERS}}


def reminder_text(title, start, calendar_timezone):
    """Texto curto determinístico; nunca inclui descrição ou identidade privada."""
    if not isinstance(title, str):
        raise ReminderDeliveryError()
    title.encode("utf-8")
    title = unicodedata.normalize("NFC", title)
    title = "".join(" " if char.isspace() else char
                    for char in title if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf"})
    title = " ".join(title.split()) or "Compromisso"
    if len(title) > 160:
        title = title[:159] + "…"
    _identifier(calendar_timezone, 128)
    local = _instant(start).astimezone(ZoneInfo(calendar_timezone))
    text = ("Lembrete da agenda\n" + title + "\nInício: "
            + local.strftime("%d/%m/%Y às %H:%M") + " (" + calendar_timezone + ")")
    if len(text) > 1000:
        raise ReminderDeliveryError()
    return text


def reminder_notice_text(title, start, calendar_timezone, *, offset_minutes, now=None):
    """Aviso legível com tempo restante real; mantém o formatter legado intacto."""
    if type(offset_minutes) is not int or not 1 <= offset_minutes <= 1440:
        raise ReminderDeliveryError()
    # A função anterior mantém a mesma validação/normalização de título e fuso.
    normalized = reminder_text(title, start, calendar_timezone).split('\n')[1]
    instant = _instant(start)
    minutes = offset_minutes
    if now is not None:
        remaining = (instant - _instant(now)).total_seconds()
        # A guarda persistente ainda precisa registrar o vencimento quando
        # a consulta termina exatamente no início. Formatar não decide envio.
        minutes = max(0, ceil(remaining / 60))
    amount = (f'{minutes // 60} hora' + ('' if minutes == 60 else 's') if minutes % 60 == 0
              else f'{minutes} minuto' + ('' if minutes == 1 else 's'))
    zone = ZoneInfo(calendar_timezone)
    local = instant.astimezone(zone)
    clock = clock_time(local)
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        offset = local.strftime('%z')
        clock += ' (UTC' + offset[:3] + ':' + offset[3:] + ')'
    headline = 'Lembrete da entrevista.' if minutes == 0 else f'Sua entrevista começa em {amount}.'
    text = (headline + '\n' + normalized + '\n'
            + local.strftime('%d/%m/%Y') + ' às ' + clock)
    label = visible_zone_label(calendar_timezone)
    if label:
        text += '\n' + label
    if len(text) > 1000:
        raise ReminderDeliveryError()
    return text


class ReminderDeliveryService:
    def __init__(self, store, gateway_factory, recipient_loader, sender_factory, clock=None):
        self._store = store
        self._gateway_factory = gateway_factory
        self._recipient_loader = recipient_loader
        self._sender_factory = sender_factory
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
        self._last_now = None

    def _now(self):
        now = _instant(self._clock())
        if self._last_now is not None and now < self._last_now:
            raise ReminderDeliveryError()
        self._last_now = now
        return now

    def _recipient(self, config):
        value = self._recipient_loader()
        if (type(value) is not ReminderRecipient or not isinstance(value.recipient, str)
                or _PHONE.fullmatch(value.recipient) is None or not isinstance(value.binding, str)
                or _HASH.fullmatch(value.binding) is None
                or not hmac.compare_digest(config.recipient_key, value.binding)):
            raise ReminderDeliveryError()
        return value

    def _channel_reason(self, binding, now):
        observation = self._store.last_inbound(binding)
        if (type(observation) is not ChannelObservation or not observation.valid
                or observation.reason != "valid" or observation.provenance != "sandbox_callback_capacity"
                or observation.recipient_key != binding or observation.message_at is None
                or observation.message_at > observation.received_at or observation.received_at > now):
            return "channel_window_unknown"
        if now >= observation.message_at + CHANNEL_WINDOW:
            return "channel_window_closed"
        if now >= observation.message_at + CHANNEL_WINDOW - CHANNEL_MARGIN:
            return "channel_window_margin"
        return None

    @staticmethod
    def _lookup(gateway, record, now):
        snapshot = gateway.lookup_snapshot((record.event_id,), expected_calendar_id=record.calendar_id)
        if (type(snapshot) is not ReminderLookupSnapshot or snapshot.calendar_id != record.calendar_id
                or type(snapshot.lookups) is not tuple or len(snapshot.lookups) != 1):
            raise ReminderDeliveryError()
        _identifier(snapshot.calendar_timezone, 128)
        ZoneInfo(snapshot.calendar_timezone)
        lookup = snapshot.lookups[0]
        if type(lookup) is not ReminderEventLookup or lookup.event_id != record.event_id:
            raise ReminderDeliveryError()
        if lookup.outcome in {"absent", "cancelled"}:
            if lookup.event is not None:
                raise ReminderDeliveryError()
            return None, snapshot.calendar_timezone, "event_cancelled"
        if lookup.outcome != "found":
            raise ReminderDeliveryError()
        event = _event(lookup.event)
        if event.event_id != record.event_id or event.status == "cancelled":
            raise ReminderDeliveryError()
        if (event.status != "confirmed" or event.all_day or not event.expanded or event.start_utc <= now):
            return None, snapshot.calendar_timezone, "event_changed"
        if event.start_utc != record.start_at:
            return None, snapshot.calendar_timezone, "event_rescheduled"
        return event, snapshot.calendar_timezone, None

    @staticmethod
    def _record_count(record, counters):
        field = {"expired": "expired_count", "cancelled": "cancelled_count",
                 "blocked": "blocked_count"}.get(record.state)
        if field is None:
            raise ReminderDeliveryError()
        counters[field] += 1

    def dispatch(self):
        counters = {name: 0 for name in _COUNTERS}
        try:
            with reminder_tick_lock(self._store.path) as acquired:
                if not acquired:
                    return ReminderDeliverySummary("skipped", "busy")
                self._last_now = None
                config = self._store.config()
                if config is None or not config.enabled or not config.permission_granted:
                    reason = ("not_configured" if config is None else "disabled" if not config.enabled
                              else "permission_required")
                    return ReminderDeliverySummary("skipped", reason)
                self._now()
                snapshot = self._store.delivery_snapshot(limit=MAX_DELIVERY_CANDIDATES)
                counters["expired_count"] = snapshot.expired_count
                counters["reminders_considered"] = len(snapshot.records) + snapshot.expired_count
                if snapshot.config != config:
                    raise ReminderDeliveryError()
                if snapshot.records:
                    recipient = self._recipient(config)
                    with ExitStack() as stack:
                        gateway, sender = None, None
                        for record in snapshot.records:
                            claim = None
                            attempted = False
                            try:
                                now = self._now()
                                if now < record.updated_at:
                                    raise ReminderDeliveryError()
                                reason = ("channel_window_unknown" if now >= record.deadline_at else
                                          self._channel_reason(recipient.binding, now))
                                if reason is not None:
                                    self._record_count(self._store.finish_delivery_candidate(record, reason), counters)
                                    continue
                                if gateway is None:
                                    gateway = stack.enter_context(self._gateway_factory())
                                event, zone, cancellation = self._lookup(gateway, record, now)
                                now = self._now()
                                if cancellation is not None:
                                    self._record_count(self._store.finish_delivery_candidate(record, cancellation), counters)
                                    continue
                                try:
                                    reminder_notice_text(event.title, event.start_utc, zone,
                                        offset_minutes=record.offset_minutes, now=now)
                                except Exception:
                                    self._record_count(self._store.finish_delivery_candidate(record, "invalid_format"), counters)
                                    continue
                                reason = self._channel_reason(recipient.binding, now)
                                if reason is not None:
                                    self._record_count(self._store.finish_delivery_candidate(record, reason), counters)
                                    continue
                                if sender is None:
                                    sender = self._sender_factory()
                                    if not callable(getattr(sender, "send", None)):
                                        raise ReminderDeliveryError()
                                claim = self._store.reserve(record.key, config_revision=config.revision)
                                if claim.status != "new":
                                    if claim.status == "expired":
                                        counters["expired_count"] += 1
                                    claim = None
                                    continue
                                prepared = None
                                use_prepared = (claim.reminder.offset_minutes == 60
                                    and callable(getattr_static(sender, 'prepare_reminder', None))
                                    and callable(getattr_static(sender, 'send_prepared', None)))
                                if use_prepared:
                                    try:
                                        prepared = sender.prepare_reminder(event, calendar_timezone=zone,
                                            offset_minutes=claim.reminder.offset_minutes, now=self._now())
                                    except Exception:
                                        # Preparação não é tentativa de transporte. A mesma
                                        # reserva segue para texto após todas as guardas.
                                        prepared = None
                                event, zone, cancellation = self._lookup(gateway, claim.reminder, self._now())
                                now = self._now()
                                if cancellation is not None:
                                    self._record_count(self._store.cancel_reserved(claim, cancellation), counters)
                                    claim = None
                                    continue
                                text = reminder_notice_text(event.title, event.start_utc, zone,
                                    offset_minutes=claim.reminder.offset_minutes, now=now)
                                if self._recipient(config) != recipient:
                                    raise ReminderDeliveryError()
                                now = self._now()
                                # O store repete perfil, relógio, canal e CAS do claim
                                # na transação que imediatamente precede o transporte.
                                current = self._store.begin_delivery(claim, config_revision=config.revision,
                                    title=event.title, calendar_timezone=zone, not_before=now)
                                if current.state != "sending":
                                    self._record_count(current, counters)
                                    claim = None
                                    continue
                                attempted = True
                                counters["transport_attempts"] += 1
                                try:
                                    if use_prepared and prepared is not None:
                                        fingerprint = sender.send_prepared(recipient.recipient, text, prepared,
                                            event=event, calendar_timezone=zone,
                                            offset_minutes=claim.reminder.offset_minutes, now=now)
                                    else:
                                        fingerprint = sender.send(recipient.recipient, text)
                                    if not isinstance(fingerprint, str) or _HASH.fullmatch(fingerprint) is None:
                                        raise ReminderDeliveryError()
                                    self._store.mark_accepted(claim, fingerprint)
                                except Exception:
                                    counters["uncertain_count"] += 1
                                    try:
                                        self._store.mark_uncertain(claim)
                                    except Exception:
                                        counters["failed_count"] += 1
                                    break
                                counters["accepted_count"] += 1
                                claim = None
                            except Exception:
                                counters["failed_count"] += 1
                                if claim is not None and not attempted:
                                    try:
                                        self._store.release_reserved(claim)
                                    except Exception:
                                        pass
                                break
        except Exception:
            counters["failed_count"] += 1
        failed = counters["failed_count"] > 0 or counters["uncertain_count"] > 0
        applied = any(counters[name] for name in ("expired_count", "cancelled_count", "blocked_count",
                                                "accepted_count", "uncertain_count"))
        status = "partial" if failed and applied else "failed" if failed else "completed"
        return ReminderDeliverySummary(status, **counters)
