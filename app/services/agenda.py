"""Consultas de agenda independentes de Google, n8n e modelo de IA."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class CalendarPeriod:
    start: datetime
    end: datetime
    time_zone: str


@dataclass(frozen=True)
class AgendaEvent:
    event_id: str
    title: str
    start: str
    end: str
    all_day: bool
    time_zone: str
    status: str
    html_link: str | None = None


@dataclass(frozen=True)
class CalendarListing:
    calendar_name: str
    events: tuple[AgendaEvent, ...]


@dataclass(frozen=True)
class DayAgenda:
    date: str
    time_zone: str
    calendar_name: str
    events: tuple[AgendaEvent, ...]


@dataclass(frozen=True)
class PeriodAgenda:
    period: str
    reference_date: str
    start_date: str
    end_date_exclusive: str
    time_zone: str
    calendar_name: str
    events: tuple[AgendaEvent, ...]


class AgendaGateway(Protocol):
    def list_events(self, period: CalendarPeriod) -> CalendarListing: ...


def load_zone(name: str) -> ZoneInfo:
    try:
        if not name or name != name.strip():
            raise ValueError
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Fuso horário inválido ou indisponível. Confira as dependências.") from None


def validate_period(period: CalendarPeriod) -> ZoneInfo:
    zone = load_zone(period.time_zone)
    if any(not isinstance(value, datetime) or value.utcoffset() is None
           for value in (period.start, period.end)):
        raise ValueError("Os limites da consulta devem incluir deslocamento UTC.")
    if period.end.astimezone(timezone.utc) <= period.start.astimezone(timezone.utc):
        raise ValueError("O período da consulta precisa ter início anterior ao fim.")
    return zone


def _validate_date(day: date) -> None:
    if type(day) is not date:
        raise ValueError("Informe uma data para a consulta.")


def _date_period(start: date, end: date, time_zone: str) -> CalendarPeriod:
    zone = load_zone(time_zone)
    period = CalendarPeriod(
        start=datetime.combine(start, time.min, tzinfo=zone),
        end=datetime.combine(end, time.min, tzinfo=zone),
        time_zone=time_zone,
    )
    # Dias civis podem ter 23/25 horas ou até não existir em um fuso histórico.
    validate_period(period)
    return period


def day_period(day: date, time_zone: str) -> CalendarPeriod:
    _validate_date(day)
    try:
        next_day = day + timedelta(days=1)
    except OverflowError:
        raise ValueError("A data está fora do intervalo de consulta suportado.") from None
    return _date_period(day, next_day, time_zone)


def week_period(day: date, time_zone: str) -> CalendarPeriod:
    """Semana civil: segunda-feira até a segunda seguinte, exclusiva."""
    _validate_date(day)
    try:
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=7)
    except OverflowError:
        raise ValueError("A data está fora do intervalo de consulta suportado.") from None
    return _date_period(start, end, time_zone)


def month_period(day: date, time_zone: str) -> CalendarPeriod:
    """Mês civil da referência; o primeiro dia do mês seguinte fica de fora."""
    _validate_date(day)
    try:
        start = day.replace(day=1)
        end = date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
    except ValueError:
        raise ValueError("A data está fora do intervalo de consulta suportado.") from None
    return _date_period(start, end, time_zone)


class AgendaService:
    def __init__(self, gateway: AgendaGateway):
        self.gateway = gateway

    def list_day(self, day: date, *, time_zone: str = "America/Recife") -> DayAgenda:
        period = day_period(day, time_zone)
        listing = self.gateway.list_events(period)
        return DayAgenda(
            date=day.isoformat(), time_zone=time_zone,
            calendar_name=listing.calendar_name, events=listing.events,
        )

    def list_week(self, day: date, *, time_zone: str = "America/Recife") -> PeriodAgenda:
        return self._list_period("week", day, week_period(day, time_zone))

    def list_month(self, day: date, *, time_zone: str = "America/Recife") -> PeriodAgenda:
        return self._list_period("month", day, month_period(day, time_zone))

    def _list_period(self, kind: str, day: date, period: CalendarPeriod) -> PeriodAgenda:
        listing = self.gateway.list_events(period)
        return PeriodAgenda(
            period=kind, reference_date=day.isoformat(),
            start_date=period.start.date().isoformat(),
            end_date_exclusive=period.end.date().isoformat(), time_zone=period.time_zone,
            calendar_name=listing.calendar_name, events=listing.events,
        )
