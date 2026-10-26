"""Rascunho estruturado mínimo para esclarecer um pedido sem guardar transcrições."""

from dataclasses import dataclass
import re
import unicodedata


_FIELD_NAMES = frozenset({
    "action", "time_zone", "title", "start", "end", "description", "location",
    "event_id", "search_title", "date", "period",
})
_AWAITING_NAMES = _FIELD_NAMES | {"event", "changes", "request"}
_ACTIONS = frozenset({"create", "list", "reschedule", "edit", "delete"})
_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_INVALID_MESSAGE = "Os dados do pedido pendente são inválidos."
_MAX_FIELDS_BYTES = 20 * 1024


@dataclass(frozen=True, repr=False)
class CandidateReference:
    event_id: str
    fingerprint: str

    def __repr__(self):
        return "CandidateReference(<private>)"


@dataclass(frozen=True, repr=False)
class PendingRequest:
    fields: dict[str, str]
    awaiting: tuple[str, ...] = ()
    candidates: tuple[CandidateReference, ...] = ()

    def __repr__(self):
        return "PendingRequest(<private>)"


def _valid_event_id(value):
    return (isinstance(value, str) and 1 <= len(value) <= 1024
            and not any(char.isspace() or unicodedata.category(char).startswith("C") for char in value))


def validate_pending_request(pending: PendingRequest) -> PendingRequest:
    """Copia apenas os campos permitidos; datas ainda inválidas podem ser corrigidas depois."""
    try:
        if type(pending) is not PendingRequest or type(pending.fields) is not dict:
            raise ValueError()
        fields = {}
        size = 0
        for name, value in pending.fields.items():
            if (not isinstance(name, str) or name not in _FIELD_NAMES
                    or not isinstance(value, str) or len(value) > 4000
                    or any(unicodedata.category(char).startswith("C") and char not in "\t\r\n"
                           for char in value)):
                raise ValueError()
            size += len(name.encode("utf-8")) + len(value.encode("utf-8"))
            if size > _MAX_FIELDS_BYTES:
                raise ValueError()
            fields[name] = value
        if "action" in fields and fields["action"] not in _ACTIONS:
            raise ValueError()
        if "event_id" in fields and not _valid_event_id(fields["event_id"]):
            raise ValueError()
        if (type(pending.awaiting) is not tuple or len(pending.awaiting) > 12
                or any(not isinstance(name, str) or name not in _AWAITING_NAMES for name in pending.awaiting)
                or len(set(pending.awaiting)) != len(pending.awaiting)):
            raise ValueError()
        if type(pending.candidates) is not tuple or len(pending.candidates) > 20:
            raise ValueError()
        candidates = []
        identifiers = set()
        for candidate in pending.candidates:
            if (type(candidate) is not CandidateReference or not _valid_event_id(candidate.event_id)
                    or not isinstance(candidate.fingerprint, str) or not _HASH.fullmatch(candidate.fingerprint)
                    or candidate.event_id in identifiers):
                raise ValueError()
            identifiers.add(candidate.event_id)
            candidates.append(CandidateReference(candidate.event_id, candidate.fingerprint))
        return PendingRequest(fields, tuple(pending.awaiting), tuple(candidates))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError(_INVALID_MESSAGE) from None
