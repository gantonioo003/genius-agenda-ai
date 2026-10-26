"""Plano editorial fechado: o modelo escolhe organização, Python fornece fatos."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import json
import re
import unicodedata

from app.llm import ModelError, ModelReply
from app.services.agenda import month_period, week_period
from app.services.summary_context import SummaryCollectionError
from app.services.time_rules import load_zone, validate_interval


MAX_RENDER_CHARS = 3500
MAX_PLAN_BYTES = 2048
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z", re.ASCII)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z", re.ASCII)
_TOTALS = {"events", "confirmed", "tentative", "all_day", "elapsed", "ongoing", "upcoming"}
_LAYOUTS = {"chronological", "upcoming_first"}
SUMMARY_INSTRUCTIONS = (
    'Organize apenas os fatos fornecidos. Responda um único objeto JSON, sem texto adicional, '
    'com exatamente {"layout":"chronological" ou "upcoming_first","highlights":[...]}. '
    'highlights contém de zero a três referências distintas do catálogo H1..H8 fornecido. '
    'Não crie referências, texto factual, datas, horários, importância, disponibilidade ou ações. '
    'O backend renderiza todos os fatos e compromissos; você escolhe somente layout e destaques.'
)


@dataclass(frozen=True, repr=False)
class PreparedSummary:
    model_message: str | None
    fallback_reason: str | None
    _context_json: str = field(repr=False)
    _candidates: tuple[tuple[str, str], ...] = field(repr=False)

    @property
    def context(self):
        return json.loads(self._context_json)

    def __repr__(self):
        return "PreparedSummary(<private>)"


@dataclass(frozen=True)
class SummaryPlan:
    layout: str
    highlights: tuple[str, ...]


def _exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError()


def _count(value):
    if type(value) is not int or not 0 <= value <= 2000:
        raise ValueError()
    return value


def _day(value):
    if type(value) is not str or _DATE.fullmatch(value) is None:
        raise ValueError()
    return date.fromisoformat(value)


def _stamp(value):
    if type(value) is not str or _STAMP.fullmatch(value) is None:
        raise ValueError()
    if not value.endswith("Z") and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError()
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError()
    return result


def _midnight(day, zone):
    # Mesma fronteira civil usada pela coleta: um salto na meia-noite pode
    # produzir um dia de 23h válido. O intervalo de cada bucket ainda precisa
    # ser positivo, recusando um dia histórico eliminado por inteiro.
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)


def _validate_context(context):
    _exact(context, {"schema_version", "period", "snapshot", "totals", "days", "events", "details", "execution_allowed"})
    if type(context["schema_version"]) is not int or context["schema_version"] != 1 or context["execution_allowed"] is not False:
        raise ValueError()
    period = context["period"]
    _exact(period, {"kind", "reference_date", "start", "end", "time_zone"})
    if type(period["kind"]) is not str or period["kind"] not in {"week", "month"}:
        raise ValueError()
    zone = load_zone(period["time_zone"])
    if len(period["time_zone"]) > 128:
        raise ValueError()
    reference = _day(period["reference_date"])
    selected = (week_period if period["kind"] == "week" else month_period)(reference, zone.key)
    if period["start"] != selected.start.isoformat() or period["end"] != selected.end.isoformat():
        raise ValueError()
    start, end = selected.start.astimezone(timezone.utc), selected.end.astimezone(timezone.utc)
    _exact(context["snapshot"], {"collected_at_utc", "complete"})
    now = _stamp(context["snapshot"]["collected_at_utc"])
    if now.utcoffset() != timedelta(0) or context["snapshot"]["complete"] is not True:
        raise ValueError()
    totals = context["totals"]
    _exact(totals, _TOTALS)
    for value in totals.values():
        _count(value)
    total = totals["events"]
    if (totals["confirmed"] + totals["tentative"] != total
            or totals["elapsed"] + totals["ongoing"] + totals["upcoming"] != total
            or totals["all_day"] > total):
        raise ValueError()
    details = context["details"]
    _exact(details, {"shown", "total", "omitted", "limit", "truncated"})
    for name in ("shown", "total", "omitted", "limit"):
        _count(details[name])
    if (details["limit"] != 10 or details["total"] != total or details["shown"] != min(10, total)
            or details["omitted"] != total - details["shown"]
            or type(details["truncated"]) is not bool or details["truncated"] != (total > 10)
            or type(context["events"]) is not list or len(context["events"]) != details["shown"]
            or type(context["days"]) is not list):
        raise ValueError()
    dates = []
    current = selected.start.date()
    while current < selected.end.date():
        following = current + timedelta(days=1)
        left, right = _midnight(current, zone), _midnight(following, zone)
        validate_interval(left, right)
        dates.append((current, left, right))
        current = following
    if len(context["days"]) != len(dates):
        raise ValueError()
    for row, (day, _, _) in zip(context["days"], dates):
        _exact(row, {"date", "event_count", "confirmed", "tentative", "all_day"})
        if row["date"] != day.isoformat():
            raise ValueError()
        for name in ("event_count", "confirmed", "tentative", "all_day"):
            _count(row[name])
        if (row["confirmed"] + row["tentative"] != row["event_count"] or row["all_day"] > row["event_count"]
                or row["event_count"] > total or any(row[name] > totals[name] for name in ("confirmed", "tentative", "all_day"))):
            raise ValueError()
    for daily_name, total_name in (("event_count", "events"), ("confirmed", "confirmed"), ("tentative", "tentative"), ("all_day", "all_day")):
        summed = sum(row[daily_name] for row in context["days"])
        if not totals[total_name] <= summed <= totals[total_name] * len(dates):
            raise ValueError()
    parsed = []
    previous = None
    for index, event in enumerate(context["events"]):
        _exact(event, {"ref", "title", "start", "end", "all_day", "status", "temporal_state", "title_truncated"})
        title = event["title"]
        if (event["ref"] != f"E{index + 1}" or type(title) is not str or not 1 <= len(title) <= 160
                or unicodedata.normalize("NFC", title) != title or " ".join(title.split()) != title
                or any(unicodedata.category(char) in {"Cc", "Cf"} for char in title)
                or type(event["all_day"]) is not bool or type(event["title_truncated"]) is not bool
                or event["status"] not in {"confirmed", "tentative"}
                or event["title_truncated"] and (len(title) != 160 or not title.endswith("…"))):
            raise ValueError()
        if event["all_day"]:
            left, right = _midnight(_day(event["start"]), zone), _midnight(_day(event["end"]), zone)
        else:
            left, right = _stamp(event["start"]), _stamp(event["end"])
            if left.astimezone(zone).isoformat() != event["start"] or right.astimezone(zone).isoformat() != event["end"]:
                raise ValueError()
            left, right = left.astimezone(timezone.utc), right.astimezone(timezone.utc)
        validate_interval(left, right)
        temporal = "elapsed" if right <= now else "upcoming" if left > now else "ongoing"
        if (event["temporal_state"] != temporal or left >= end or right <= start
                or previous is not None and left < previous):
            raise ValueError()
        previous = left
        parsed.append((event, left, right))
    known = {name: 0 for name in _TOTALS}
    known["events"] = len(parsed)
    for event, _, _ in parsed:
        known[event["status"]] += 1
        known[event["temporal_state"]] += 1
        known["all_day"] += event["all_day"]
    if any(known[name] > totals[name] or total <= 10 and known[name] != totals[name] for name in _TOTALS):
        raise ValueError()
    for row, (_, left, right) in zip(context["days"], dates):
        included = [event for event, first, last in parsed if first < right and last > left]
        computed = {"event_count": len(included), "confirmed": sum(event["status"] == "confirmed" for event in included),
                    "tentative": sum(event["status"] == "tentative" for event in included), "all_day": sum(event["all_day"] for event in included)}
        if any(computed[name] > row[name] or total <= 10 and computed[name] != row[name] for name in computed):
            raise ValueError()


def _catalog(context):
    totals = context["totals"]
    facts = [f'{totals["events"]} compromissos no período.',
        f'{totals["confirmed"]} confirmados e {totals["tentative"]} provisórios.',
        f'{totals["elapsed"]} encerrados, {totals["ongoing"]} em andamento e {totals["upcoming"]} futuros na consulta.']
    if totals["all_day"]:
        facts.append(f'{totals["all_day"]} compromissos de dia inteiro.')
    occupied = [row for row in context["days"] if row["event_count"]]
    facts.append(f'{len(occupied)} dias com compromissos no período.')
    for row in occupied[:2]:
        facts.append(f'{_day(row["date"]).strftime("%d/%m/%Y")}: {row["event_count"]} compromissos nesse dia.')
    if context["details"]["omitted"]:
        facts.append(f'{context["details"]["omitted"]} compromissos sem detalhes individuais nesta apresentação.')
    return tuple((f"H{index + 1}", fact) for index, fact in enumerate(facts[:8]))


def prepare_summary(context, *, max_input_chars=4000):
    try:
        if type(max_input_chars) is not int or not 1 <= max_input_chars <= 4000:
            raise ValueError()
        _validate_context(context)
        # Roundtrip JSON cria uma fotografia imutável, sem objetos fornecidos
        # pelo chamador/modelo ou referências mutáveis compartilhadas.
        raw = json.dumps(context, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > 16000:
            raise ValueError()
        copied = json.loads(raw)
        _validate_context(copied)
        candidates = _catalog(copied)
        payload = {"period": copied["period"], "totals": copied["totals"],
                   "catalog": [{"ref": ref, "fact": fact} for ref, fact in candidates],
                   "allowed_layouts": ["chronological", "upcoming_first"], "maximum_highlights": 3}
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        reason = "empty_period" if copied["totals"]["events"] == 0 else "context_budget_exceeded" if len(message) > max_input_chars else None
        return PreparedSummary(None if reason else message, reason, raw, candidates)
    except Exception:
        raise SummaryCollectionError() from None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _constant(_):
    raise ValueError()


def _plan(plan, prepared):
    if (type(prepared) is not PreparedSummary or type(plan) is not SummaryPlan
            or type(plan.layout) is not str or plan.layout not in _LAYOUTS
            or type(plan.highlights) is not tuple or len(plan.highlights) > 3
            or any(type(ref) is not str or ref not in dict(prepared._candidates) for ref in plan.highlights)
            or len(set(plan.highlights)) != len(plan.highlights)):
        raise ValueError()


def parse_summary_plan(reply, prepared):
    try:
        if (type(prepared) is not PreparedSummary or prepared.model_message is None
                or type(reply) is not ModelReply or type(reply.text) is not str
                or not 1 <= len(reply.text) <= MAX_PLAN_BYTES or len(reply.text.encode("utf-8")) > MAX_PLAN_BYTES
                or type(reply.model) is not str or _MODEL.fullmatch(reply.model) is None
                or any(value is not None and (type(value) is not int or value < 0)
                       for value in (reply.input_tokens, reply.output_tokens))):
            raise ValueError()
        text = reply.text.strip()
        if text.startswith("```json\n") and text.endswith("\n```"):
            text = text[8:-4]
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
        _exact(value, {"layout", "highlights"})
        if type(value["highlights"]) is not list:
            raise ValueError()
        plan = SummaryPlan(value["layout"], tuple(value["highlights"]))
        _plan(plan, prepared)
        return plan
    except Exception:
        raise ModelError("llm_invalid_response") from None


def _when(event):
    if event["all_day"]:
        return f'{_day(event["start"]).strftime("%d/%m/%Y")} até {_day(event["end"]).strftime("%d/%m/%Y")} (fim exclusivo), dia inteiro'
    def display(value):
        parsed = _stamp(value)
        return parsed.strftime("%d/%m/%Y ") + value[11:]
    return display(event["start"]) + " → " + display(event["end"])


def render_summary(prepared, plan=None):
    try:
        if type(prepared) is not PreparedSummary:
            raise ValueError()
        if plan is not None:
            _plan(plan, prepared)
        context = prepared.context
        _validate_context(context)
        if prepared._candidates != _catalog(context):
            raise ValueError()
        period, totals, details = context["period"], context["totals"], context["details"]
        label = "semana" if period["kind"] == "week" else "mês"
        first = _stamp(period["start"]).date().strftime("%d/%m/%Y")
        last = (_stamp(period["end"]).date() - timedelta(days=1)).strftime("%d/%m/%Y")
        header = [f"Resumo da {label}" if label == "semana" else "Resumo do mês", f"{first} a {last} | Fuso: {period['time_zone']}",
            f"Consulta UTC: {context['snapshot']['collected_at_utc']}",
            f"Total: {totals['events']} | Confirmados: {totals['confirmed']} | Provisórios: {totals['tentative']}",
            f"Na consulta: {totals['elapsed']} encerrados, {totals['ongoing']} em andamento, {totals['upcoming']} futuros.",
            f"Dia inteiro: {totals['all_day']}."]
        if totals["events"] == 0:
            return "\n".join(header + ["Nenhum compromisso encontrado no período consultado."])
        if plan is not None and plan.highlights:
            catalog = dict(prepared._candidates)
            header += ["Destaques factuais:"] + ["- " + catalog[ref] for ref in plan.highlights]
        events = list(context["events"])
        if plan is not None and plan.layout == "upcoming_first":
            order = {"ongoing": 0, "upcoming": 1, "elapsed": 2}
            events.sort(key=lambda event: order[event["temporal_state"]])
        temporal = {"elapsed": "encerrado", "ongoing": "em andamento", "upcoming": "futuro"}
        footer = f"Mostrando {len(events)} de {totals['events']} compromissos; {details['omitted']} sem detalhes individuais."
        # Conservar os dez detalhes e todos os horários; reduzir apenas títulos
        # de maneira explícita se a composição ultrapassar o limite do canal.
        for title_limit in (160, 140, 120, 100, 80, 60, 40, 20, 1):
            rows, shortened = [], False
            for event in events:
                title = event["title"]
                if len(title) > title_limit:
                    title = title[:title_limit - 1] + "…"
                    shortened = True
                status = "provisório" if event["status"] == "tentative" else "confirmado"
                rows.append(f"- {_when(event)} | {title} | {status}; {temporal[event['temporal_state']]}")
            text = "\n".join(header + ["Compromissos:"] + rows + [footer] + (["Títulos abreviados para caber na mensagem."] if shortened else []))
            if len(text) <= MAX_RENDER_CHARS:
                text.encode("utf-8")
                return text
        raise ValueError()
    except ModelError:
        raise
    except Exception:
        raise SummaryCollectionError() from None


def render_summary_whatsapp(prepared, plan=None):
    """Usar a mesma fotografia validada com apresentação curta para a conversa."""
    from app.chat_formatting import event_interval, visible_zone_label as zone_label
    try:
        if type(prepared) is not PreparedSummary:
            raise ValueError()
        if plan is not None:
            _plan(plan, prepared)
        context = prepared.context
        _validate_context(context)
        if prepared._candidates != _catalog(context):
            raise ValueError()
        period, totals, details = context['period'], context['totals'], context['details']
        first = _stamp(period['start']).date()
        last = _stamp(period['end']).date() - timedelta(days=1)
        months = ('janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro')
        if period['kind'] == 'month':
            header = ['📅 *Resumo de ' + months[first.month - 1] + '/' + str(first.year) + '*']
        else:
            header = ['📅 *Resumo da semana*', first.strftime('%d/%m/%Y') + ' a ' + last.strftime('%d/%m/%Y')]
        count = totals['events']
        if not count:
            return '\n'.join(header + ['', 'Você não tem compromissos nesse período.', '', zone_label(period['time_zone'])])
        header += ['', f'*{count} compromisso' + ('s' if count != 1 else '') + ' no período.*']
        if totals['elapsed'] == count:
            header.append('Todos já aconteceram.' if count != 1 else 'Esse compromisso já aconteceu.')
        elif totals['upcoming'] == count:
            header.append('Todos ainda vão acontecer.' if count != 1 else 'Esse compromisso ainda vai acontecer.')
        else:
            parts = []
            for key, label in [('elapsed', 'já aconteceu' if totals['elapsed'] == 1 else 'já aconteceram'), ('ongoing', 'em andamento'), ('upcoming', 'por vir')]:
                if totals[key]:
                    parts.append(str(totals[key]) + ' ' + label)
            header.append(' · '.join(parts))
        events = list(context['events'])
        if plan is not None and plan.layout == 'upcoming_first':
            order = {'ongoing': 0, 'upcoming': 1, 'elapsed': 2}
            events.sort(key=lambda entry: order[entry['temporal_state']])
        grouped = {}
        for event in events:
            grouped.setdefault(event['start'][:10], []).append(event)
        footer = ['', zone_label(period['time_zone'])]
        if details['omitted']:
            footer.insert(0, f"Mostrando {len(events)} de {count}. Consulte um período menor para ver os demais.")
        for title_limit in (100, 80, 60, 40, 20, 1):
            rows, shortened = [], False
            for day, entries in grouped.items():
                rows += ['', '*' + _day(day).strftime('%d/%m') + '*']
                local_offsets = {}
                for entry in entries:
                    if not entry['all_day']:
                        a, b = _stamp(entry['start']), _stamp(entry['end'])
                        key = (a.replace(tzinfo=None), b.replace(tzinfo=None))
                        local_offsets.setdefault(key, set()).add((a.utcoffset(), b.utcoffset()))
                for event in entries:
                    title = event['title']
                    if len(title) > title_limit:
                        title = title[:title_limit - 1] + '…'
                        shortened = True
                    show_offset = False
                    if not event['all_day']:
                        a, b = _stamp(event['start']), _stamp(event['end'])
                        key = (a.replace(tzinfo=None), b.replace(tzinfo=None))
                        show_offset = len(local_offsets[key]) > 1
                    when = event_interval(event['start'], event['end'], event['all_day'], include_date=False, show_offset=show_offset)
                    state = ' (a confirmar)' if event['status'] == 'tentative' else ''
                    rows.append('• *' + when + '* — ' + title + state)
            text = '\n'.join(header + rows + (['Títulos abreviados.'] if shortened else []) + footer)
            if len(text) <= MAX_RENDER_CHARS:
                text.encode('utf-8')
                return text
        raise ValueError()
    except Exception:
        raise SummaryCollectionError() from None
