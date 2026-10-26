"""Regras comuns de intervalo e fuso, sem acesso a serviços externos."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def load_zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not name or name != name.strip():
        raise ValueError("Fuso horário inválido ou indisponível. Confira as dependências.")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Fuso horário inválido ou indisponível. Confira as dependências.") from None


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("Início e fim devem incluir data, horário e deslocamento UTC.")
    try:
        if value.utcoffset() is None:
            raise ValueError("Início e fim devem incluir data, horário e deslocamento UTC.")
        return value.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("A data está fora do intervalo de horários suportado.") from None
    except (TypeError, ValueError):
        raise ValueError("Início e fim devem incluir data, horário e deslocamento UTC válidos.") from None


def validate_interval(start: datetime, end: datetime) -> timedelta:
    """Comparar instantes em UTC; limites podem usar offsets diferentes."""
    duration = _as_utc(end) - _as_utc(start)
    if duration <= timedelta(0):
        raise ValueError("O fim deve ser posterior ao início do compromisso.")
    return duration


def validate_event_interval(start: datetime, end: datetime, time_zone: str) -> timedelta:
    """Validar também a hora civil declarada de um agendamento no seu fuso."""
    zone = load_zone(time_zone)
    duration = validate_interval(start, end)
    for value in (start, end):
        try:
            # O roundtrip detecta horas inexistentes; o offset distingue as repetidas.
            local = _as_utc(value).astimezone(zone)
        except OverflowError:
            raise ValueError("A data está fora do intervalo de horários suportado.") from None
        if (local.replace(tzinfo=None) != value.replace(tzinfo=None)
                or local.utcoffset() != value.utcoffset()):
            raise ValueError("Data, hora e deslocamento UTC devem corresponder ao fuso informado.")
    return duration
