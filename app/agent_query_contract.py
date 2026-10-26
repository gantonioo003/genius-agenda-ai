"""Planos de consulta independentes do fornecedor e dos serviços da agenda."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class QueryPlan:
    """Uma intenção validada; os argumentos podem conter dados privados."""

    name: str
    arguments: dict = field(repr=False)
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class QueryLanguageModel(Protocol):
    """O modelo interpreta a consulta sem executar uma ferramenta."""

    def plan_query(self, *, instructions: str, message: str) -> QueryPlan:
        ...


QUERY_TOOLS = [
    {
        "type": "function", "name": "list_events", "strict": True,
        "description": "Consultar os eventos de um dia, semana ou mês em um fuso IANA.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "date": {"type": "string", "description": "Data ISO YYYY-MM-DD."},
                "period": {"type": "string", "enum": ["day", "week", "month"]},
                "time_zone": {"type": "string", "description": "Nome IANA do fuso horário."},
            },
            "required": ["date", "period", "time_zone"],
        },
    },
    {
        "type": "function", "name": "get_event", "strict": True,
        "description": "Consultar um evento pelo identificador exato e literal informado na mensagem atual.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "event_id": {"type": "string"},
                "time_zone": {"type": "string", "description": "Nome IANA do fuso horário."},
            },
            "required": ["event_id", "time_zone"],
        },
    },
    {
        "type": "function", "name": "ask_query", "strict": True,
        "description": "Pedir dados que faltam ou indicar um pedido fora das consultas disponíveis.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "reason": {"type": "string", "enum": ["missing_information", "unsupported_request"]},
            },
            "required": ["reason"],
        },
    },
    {
        "type": "function", "name": "get_context_event", "strict": True,
        "description": "Reler o único evento selecionado no contexto validado, sem fornecer um identificador.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "time_zone": {"type": "string", "description": "Nome IANA do fuso horário."},
            },
            "required": ["time_zone"],
        },
    },
]
