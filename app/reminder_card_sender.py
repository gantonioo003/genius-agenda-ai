"""Preparar card antes da reserva do lembrete; transportar somente após o CAS."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.reminders import _event, _instant
from app.whatsapp_sandbox_sender import SandboxSendError, _public_image_link


@dataclass(frozen=True, repr=False)
class PreparedCard:
    link: str
    event_signature: str
    prepared_at: datetime
    expires_at: datetime
    offset_minutes: int = 60

    def __repr__(self):
        return 'PreparedCard(<private>)'


def _signature(event, zone, offset):
    event = _event(event)
    if type(zone) is not str or type(offset) is not int or offset != 60:
        raise ValueError()
    ZoneInfo(zone)
    raw = json.dumps(['reminder-card:v1', event.event_id, event.title,
                      event.start_utc.isoformat(), event.end_utc.isoformat(), zone, offset,
                      event.all_day, event.status, event.expanded],
                     ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


class ReminderCardSender:
    def __init__(self, transport, *, publisher, output_dir, renderer=None,
                 template_path=None, ttl_seconds=600):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 600:
            raise ValueError('Prazo do card inválido.')
        self._transport = transport
        self._publisher = publisher
        self._output = Path(output_dir)
        self._renderer = renderer
        self._template = template_path
        self._ttl = timedelta(seconds=ttl_seconds)

    def send(self, recipient, text):
        return self._transport.send(recipient, text)

    def prepare_reminder(self, event, *, calendar_timezone, offset_minutes, now):
        if type(offset_minutes) is not int or offset_minutes != 60:
            return None
        try:
            observed = _instant(now)
            event = _event(event)
            if (event.status != 'confirmed' or event.all_day or not event.expanded
                    or event.start_utc <= observed):
                return None
            signature = _signature(event, calendar_timezone, offset_minutes)
            renderer = self._renderer
            if renderer is None:
                from app.interview_card import render_interview_card
                renderer = render_interview_card
            output = self._output / (signature[:16] + '-' + uuid4().hex + '.png')
            options = {'now': observed}
            if self._template is not None:
                options['template_path'] = self._template
            rendered = renderer(event.title, event.start_utc, event.end_utc,
                                calendar_timezone, output, **options)
            link = self._publisher.publish(rendered, now=observed)
            if not _public_image_link(link):
                return None
            return PreparedCard(link, signature, observed, observed + self._ttl)
        except Exception:
            # Nenhuma tentativa de transporte ocorreu: texto continua disponível.
            return None

    def send_prepared(self, recipient, text, prepared, *, event,
                      calendar_timezone, offset_minutes, now):
        try:
            observed = _instant(now)
            signature = _signature(event, calendar_timezone, offset_minutes)
            usable = (type(prepared) is PreparedCard
                      and prepared.offset_minutes == 60 and offset_minutes == 60
                      and _instant(prepared.prepared_at) <= observed < _instant(prepared.expires_at)
                      and prepared.expires_at - prepared.prepared_at <= self._ttl
                      and hmac.compare_digest(prepared.event_signature, signature)
                      and _public_image_link(prepared.link))
        except Exception:
            usable = False
        if not usable:
            return self.send(recipient, text)
        try:
            return self._transport.send_image(recipient, prepared.link, caption=text)
        except SandboxSendError as error:
            if error.uncertain:
                raise
            return self.send(recipient, text)
        # Exceções arbitrárias após iniciar imagem propagam: nenhum fallback/retry.
