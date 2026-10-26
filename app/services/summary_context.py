"""Coleta factual mínima de semana/mês, sem modelo, escrita ou persistência."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import re
import unicodedata

from app.services.agenda import AgendaEvent, CalendarPeriod, month_period, week_period
from app.services.event_selection import validate_event_id
from app.services.time_rules import load_zone, validate_interval


MAX_PAGES = 20
MAX_EVENTS = 2000
MAX_DETAILS = 10
MAX_TITLE_CHARS = 160
MAX_CONTEXT_BYTES = 16000
_BUILDERS = {"week": week_period, "month": month_period}
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})", re.ASCII)


class _Private:
    def __repr__(self):
        return type(self).__name__ + "(<private>)"


@dataclass(frozen=True, repr=False)
class SummaryCalendarBinding(_Private):
    calendar_id: str
    time_zone: str


@dataclass(frozen=True, repr=False)
class SummaryCalendarSnapshot(_Private):
    binding: SummaryCalendarBinding
    period: CalendarPeriod
    events: tuple[AgendaEvent, ...]
    complete: bool
    pages_read: int
    raw_events_seen: int


class SummaryInputError(ValueError):
    def __init__(self, fields):
        super().__init__("Informe somente semana/mês e uma data de referência válida.")
        self.fields = tuple(dict.fromkeys(fields))


class SummaryCollectionError(RuntimeError):
    def __init__(self):
        super().__init__("Não foi possível confirmar o contexto completo da agenda.")


class SummaryLimitError(SummaryCollectionError):
    pass


class SummarySetupError(SummaryCollectionError):
    pass


def validate_input(period: str, reference_date: str | None = None) -> tuple[str, date | None]:
    """Validar antes de abrir relógio/conector; não interpretar datas livres."""
    fields = []
    if type(period) is not str or period not in _BUILDERS:
        fields.append("period")
    reference = None
    if reference_date is not None:
        try:
            if type(reference_date) is not str or _DATE.fullmatch(reference_date) is None:
                raise ValueError()
            reference = date.fromisoformat(reference_date)
            # Detectar também períodos que ultrapassariam o calendário suportado.
            if not fields:
                _BUILDERS[period](reference, "UTC")
        except (ValueError, OverflowError):
            fields.append("reference_date")
    if fields:
        raise SummaryInputError(fields)
    return period, reference


def _utc(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc)


def _identifier(value):
    validate_event_id(value)
    value.encode("utf-8")
    return value


def _binding(value):
    if type(value) is not SummaryCalendarBinding:
        raise ValueError()
    if _identifier(value.calendar_id) == "primary":
        raise ValueError()
    return load_zone(value.time_zone)


def _timestamp(value):
    if type(value) is not str or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError()
    if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError()
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _title(value):
    if not isinstance(value, str):
        raise ValueError()
    value.encode("utf-8")
    normalized = unicodedata.normalize("NFC", value)
    normalized = "".join(" " if char.isspace() else char for char in normalized
                         if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf"})
    normalized = " ".join(normalized.split()) or "(sem título)"
    truncated = len(normalized) > MAX_TITLE_CHARS
    return (normalized[:MAX_TITLE_CHARS - 1] + "…" if truncated else normalized), truncated


def _event(value, zone):
    if not isinstance(value, AgendaEvent):
        raise ValueError()
    _identifier(value.event_id)
    if type(value.all_day) is not bool or value.status not in {"confirmed", "tentative"}:
        raise ValueError()
    load_zone(value.time_zone)
    if value.html_link is not None:
        if not isinstance(value.html_link, str):
            raise ValueError()
        value.html_link.encode("utf-8")
    title, truncated = _title(value.title)
    if value.all_day:
        if value.time_zone != zone.key:
            raise ValueError()
        if (type(value.start) is not str or type(value.end) is not str
                or _DATE.fullmatch(value.start) is None or _DATE.fullmatch(value.end) is None):
            raise ValueError()
        start_day, end_day = date.fromisoformat(value.start), date.fromisoformat(value.end)
        if end_day <= start_day:
            raise ValueError()
        start = datetime.combine(start_day, time.min, tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(end_day, time.min, tzinfo=zone).astimezone(timezone.utc)
        display_start, display_end = start_day.isoformat(), end_day.isoformat()
    else:
        start, end = _timestamp(value.start), _timestamp(value.end)
        display_start, display_end = start.astimezone(zone).isoformat(), end.astimezone(zone).isoformat()
    validate_interval(start, end)
    return {"identity": value.event_id, "title": title, "start": display_start, "end": display_end,
            "all_day": value.all_day, "status": value.status, "title_truncated": truncated,
            "start_utc": start, "end_utc": end}


def _snapshot(value, binding, selected):
    if (type(value) is not SummaryCalendarSnapshot or type(value.complete) is not bool
            or value.complete is not True or value.binding != binding
            or type(value.binding) is not SummaryCalendarBinding
            or type(value.period) is not CalendarPeriod or value.period.time_zone != selected.time_zone
            or value.period.start.isoformat() != selected.start.isoformat()
            or value.period.end.isoformat() != selected.end.isoformat()
            or type(value.events) is not tuple):
        raise ValueError()
    if type(value.pages_read) is not int or value.pages_read < 1 or type(value.raw_events_seen) is not int or value.raw_events_seen < 0:
        raise ValueError()
    if value.pages_read > MAX_PAGES or value.raw_events_seen > MAX_EVENTS or len(value.events) > MAX_EVENTS:
        raise SummaryLimitError()
    if value.raw_events_seen < len(value.events):
        raise ValueError()
    validate_interval(value.period.start, value.period.end)


def _compose(kind, reference, selected, events, now):
    zone = load_zone(selected.time_zone)
    start, end = _utc(selected.start), _utc(selected.end)
    unique = {}
    normalized = {}
    for raw in events:
        entry = _event(raw, zone)
        if raw.event_id in unique and raw != unique[raw.event_id]:
            raise ValueError()
        unique[raw.event_id] = raw
        normalized[raw.event_id] = entry
    ordered = sorted((entry for entry in normalized.values()
        if entry["start_utc"] < end and entry["end_utc"] > start),
        key=lambda item: (item["start_utc"], item["identity"]))
    totals = {"events": len(ordered), "confirmed": 0, "tentative": 0, "all_day": 0,
              "elapsed": 0, "ongoing": 0, "upcoming": 0}
    days = []
    day = selected.start.date()
    while day < selected.end.date():
        following = day + timedelta(days=1)
        day_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)
        day_end = datetime.combine(following, time.min, tzinfo=zone).astimezone(timezone.utc)
        # Um salto histórico de fuso pode eliminar um dia civil inteiro.
        # Não atribuir eventos a um bucket vazio e afirmar contagens completas.
        validate_interval(day_start, day_end)
        included = [entry for entry in ordered if entry["start_utc"] < day_end and entry["end_utc"] > day_start]
        days.append({"date": day.isoformat(), "event_count": len(included),
                     "confirmed": sum(entry["status"] == "confirmed" for entry in included),
                     "tentative": sum(entry["status"] == "tentative" for entry in included),
                     "all_day": sum(entry["all_day"] for entry in included)})
        day = following
    details = []
    for index, entry in enumerate(ordered):
        temporal = ("elapsed" if entry["end_utc"] <= now else
                    "upcoming" if entry["start_utc"] > now else "ongoing")
        totals[entry["status"]] += 1
        totals["all_day"] += entry["all_day"]
        totals[temporal] += 1
        if index < MAX_DETAILS:
            details.append({"ref": "E" + str(index + 1), "title": entry["title"],
                "start": entry["start"], "end": entry["end"], "all_day": entry["all_day"],
                "status": entry["status"], "temporal_state": temporal,
                "title_truncated": entry["title_truncated"]})
    result = {"schema_version": 1, "period": {"kind": kind, "reference_date": reference.isoformat(),
        "start": selected.start.isoformat(), "end": selected.end.isoformat(), "time_zone": selected.time_zone},
        "snapshot": {"collected_at_utc": now.isoformat(), "complete": True},
        "totals": totals, "days": days, "events": details,
        "details": {"shown": len(details), "total": len(ordered), "omitted": len(ordered) - len(details),
                    "limit": MAX_DETAILS, "truncated": len(ordered) > len(details)}, "execution_allowed": False}
    serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(serialized) > MAX_CONTEXT_BYTES:
        raise SummaryLimitError()
    return result


class SummaryContextService:
    def __init__(self, gateway_factory, *, clock=None):
        self._gateway_factory = gateway_factory
        self._clock = clock if clock is not None else lambda: datetime.now(timezone.utc)

    def build(self, *, period: str, reference_date: str | None = None) -> dict:
        kind, reference = validate_input(period, reference_date)
        try:
            before = _utc(self._clock())
            with self._gateway_factory() as gateway:
                binding = gateway.binding()
                zone = _binding(binding)
                reference = reference if reference is not None else before.astimezone(zone).date()
                selected = _BUILDERS[kind](reference, binding.time_zone)
                snapshot = gateway.list_events(selected, expected_binding=binding)
                _snapshot(snapshot, binding, selected)
                after = _utc(self._clock())
                if after < before:
                    raise ValueError()
                result = _compose(kind, reference, selected, snapshot.events, after)
            return result
        except SummaryLimitError:
            raise SummaryLimitError() from None
        except SummarySetupError:
            raise SummarySetupError() from None
        except Exception:
            # Nem resposta parcial, nem identificador/erro bruto do conector.
            raise SummaryCollectionError() from None
