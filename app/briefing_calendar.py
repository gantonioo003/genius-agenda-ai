"""Vínculo da agenda e leituras existentes, sem ferramenta de escrita."""

from contextlib import contextmanager

from app.google_calendar import CalendarSetupError, GoogleCalendarGateway, calendar_client, resolve_calendar
from app.services.interview_briefing import BriefingBinding, BriefingSetupError


class GoogleBriefingCalendarGateway:
    def __init__(self, service):
        self._service = service
        self._gateway = GoogleCalendarGateway(service)

    def binding(self):
        calendar = resolve_calendar(self._service)
        return BriefingBinding(calendar['id'], calendar['timeZone'])

    def get_event(self, event_id, *, time_zone):
        return self._gateway.get_event(event_id, time_zone=time_zone)

    def get_update_snapshot(self, event_id, *, time_zone):
        return self._gateway.get_update_snapshot(event_id, time_zone=time_zone)


@contextmanager
def default_briefing_gateway():
    try:
        with calendar_client(interactive=False) as client:
            yield GoogleBriefingCalendarGateway(client)
    except CalendarSetupError:
        raise BriefingSetupError() from None
