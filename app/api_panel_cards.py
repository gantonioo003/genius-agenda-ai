"""Prévia privada reconstruída com dados atuais; nunca publica ou envia mídia."""

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import Depends, Request
from fastapi.responses import Response
from PIL import Image

from app.api_panel import (
    _HASH, _arguments, _binding, _gateway_factory, _invalid, _now,
    _reminder, _response, _store,
)
from app.google_calendar import CalendarSetupError
from app.interview_card import CardRenderError, render_interview_card
from app.panel_security import require_panel_session
from app.reminder_store import _title as _reminder_title
from app.services.agenda import AgendaEvent
from app.services.event_selection import EventLookup
from app.services.summary_context import SummarySetupError
from app.services.update_snapshot import validate_update_snapshot


MAX_PREVIEW_BYTES = 5 * 1024 * 1024
_PNG_HEADERS = {'Cache-Control': 'private, no-store', 'Vary': 'Cookie',
                'X-Content-Type-Options': 'nosniff'}


def _changed():
    return _response(status=409, code='card_event_changed',
        message='O compromisso mudou desde este aviso. Atualize a lista para conferir os dados atuais.')


def _render(event, time_zone, now):
    # Nenhum arquivo entra no armazenamento público de cards. A resposta possui
    # os bytes antes da remoção incondicional do diretório temporário.
    with TemporaryDirectory(prefix='genius-panel-card-') as directory:
        output = Path(directory) / 'preview.png'
        render_interview_card(event.title, datetime.fromisoformat(event.start),
            datetime.fromisoformat(event.end), time_zone, output, now=now, preview=True)
        with output.open('rb') as source:
            data = source.read(MAX_PREVIEW_BYTES + 1)
        if not data or len(data) > MAX_PREVIEW_BYTES:
            raise CardRenderError()
        with Image.open(BytesIO(data)) as image:
            if (image.format, image.mode, image.size) != ('PNG', 'RGB', (1254, 1254)):
                raise CardRenderError()
            image.verify()
        return data


def register_panel_card_routes(router):
    @router.get('/reminders/{reminder_id}/card-preview',
                dependencies=[Depends(require_panel_session)], name='panel_reminder_card_preview')
    def card_preview(request: Request, reminder_id: str):
        try:
            _arguments(request, set())
            if _HASH.fullmatch(reminder_id) is None:
                raise ValueError()
        except ValueError:
            return _invalid()

        try:
            store = _store(request)
            record = store.get(reminder_id)
            if record is None:
                return _response(status=404, code='reminder_not_found', message='O aviso não foi encontrado.')
            planned = _reminder(record,
                cards_enabled=getattr(request.app.state.settings, 'reminder_cards_enabled', False) is True)
            if record.key != reminder_id:
                raise ValueError()
            if planned['format'] != 'card':
                return _response(status=422, code='card_not_planned',
                    message='Este aviso está previsto como mensagem de texto.')
        except Exception:
            return _response(status=503, code='reminders_unavailable',
                message='Não foi possível conferir o registro deste aviso.')

        try:
            with _gateway_factory(request)() as gateway:
                binding = _binding(gateway.binding())
                if record.calendar_id != binding.calendar_id or record.calendar_timezone != binding.time_zone:
                    return _changed()
                lookup = gateway.get_event(record.event_id, time_zone=binding.time_zone)
                if type(lookup) is not EventLookup or lookup.calendar_name != binding.calendar_name:
                    raise ValueError()
                if lookup.event is None or isinstance(lookup.event, AgendaEvent) and lookup.event.status == 'cancelled':
                    return _response(status=404, code='event_not_found',
                        message='O compromisso deste aviso foi removido ou não está disponível.')
                if not isinstance(lookup.event, AgendaEvent):
                    raise ValueError()
                if lookup.event.all_day or lookup.event.event_id != record.event_id:
                    return _changed()
                snapshot = validate_update_snapshot(gateway.get_update_snapshot(record.event_id,
                    time_zone=binding.time_zone), record.event_id, binding.time_zone)
                if (snapshot.calendar_id != binding.calendar_id or snapshot.calendar_name != binding.calendar_name
                        or any(getattr(lookup.event, field) != getattr(snapshot.event, field)
                               for field in ('event_id', 'title', 'start', 'end', 'all_day', 'time_zone', 'status'))):
                    return _changed()
                event = snapshot.event
                if (event.event_id != record.event_id or event.status == 'cancelled' or event.all_day
                        or datetime.fromisoformat(event.start).astimezone(timezone.utc) != record.start_at
                        or _reminder_title(event.title) != record.title or store.get(reminder_id) != record):
                    return _changed()
                now = _now(request)
        except (CalendarSetupError, SummarySetupError):
            return _response(status=503, code='calendar_setup_unavailable',
                message='O acesso à agenda está indisponível. A prévia não foi gerada.')
        except Exception:
            return _response(status=502, code='card_event_collection_failed',
                message='Não foi possível confirmar os dados atuais do compromisso.')

        try:
            data = _render(event, binding.time_zone, now)
        except Exception:
            return _response(status=503, code='card_preview_unavailable',
                message='A prévia não está disponível. Consulte os dados do aviso em texto.')
        return Response(data, media_type='image/png', headers=_PNG_HEADERS)
