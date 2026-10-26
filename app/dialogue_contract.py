"""Planos de esclarecimento sem execução ou estado remoto no fornecedor."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class DialoguePlan:
    """Uma interpretação; seus argumentos podem conter dados privados."""

    name: str
    arguments: dict = field(repr=False)
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class DialogueLanguageModel(Protocol):
    """Interpretar uma mensagem sem executar alterações na agenda."""

    def plan_dialogue(self, *, instructions: str, message: str) -> DialoguePlan:
        ...


_DRAFT_FIELDS = (
    "time_zone", "title", "start", "end", "description", "location",
    "event_id", "search_title", "date", "period",
)

DIALOGUE_TOOLS = [
    {
        "type": "function", "name": "prepare_request", "strict": True,
        "description": (
            "Preparar ou corrigir um único pedido, sem executá-lo. Use start para um "
            "novo pedido e continue para completar o pedido pendente. Informe apenas "
            "dados da mensagem atual; null mantém um campo anterior e não apaga valores. "
            "Preserve dados conhecidos e use ambiguous_field para indicar o campo "
            "que precisa de esclarecimento, sem escolher um valor por conta própria. "
            "use_selected_event solicita seleção no servidor, sem inventar identificador."
        ),
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["start", "continue"]},
                "action": {"type": ["string", "null"],
                           "enum": ["create", "list", "reschedule", "edit", "delete", None]},
                **{name: {"type": ["string", "null"]} for name in _DRAFT_FIELDS},
                "ambiguous_field": {"type": ["string", "null"], "enum": [
                    None, "action", "title", "start", "end", "time_zone", "date", "period", "event", "changes", "request",
                ]},
                "use_selected_event": {"type": "boolean"},
            },
            "required": ["mode", "action", *_DRAFT_FIELDS, "ambiguous_field", "use_selected_event"],
        },
    },
    {
        "type": "function", "name": "select_candidate", "strict": True,
        "description": "Selecionar pela posição um dos eventos apresentados no pedido pendente.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"choice": {"type": "integer", "minimum": 1, "maximum": 20}},
            "required": ["choice"],
        },
    },
    {
        "type": "function", "name": "ask_dialogue", "strict": True,
        "description": "Pedir esclarecimento de informação ambígua ou de um pedido não interpretável.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "reason": {"type": "string", "enum": [
                    "ambiguous_information", "multiple_requests", "unclear_answer", "unsupported_request",
                ]},
                "field": {"type": "string", "enum": [
                    "action", "title", "start", "end", "time_zone", "date", "period", "event", "changes", "request",
                ]},
            },
            "required": ["reason", "field"],
        },
    },
    {
        "type": "function", "name": "cancel_request", "strict": True,
        "description": "Descartar somente o pedido pendente, sem remover eventos da agenda.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {}, "required": [],
        },
    },
]
