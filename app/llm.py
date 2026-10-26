"""Fronteira de linguagem independente dos serviços e adaptador REST Responses.

O adaptador realiza uma única tentativa e não executa ferramentas nem mantém
estado remoto de conversa. Mensagens e credenciais ficam fora dos erros públicos.
"""

from dataclasses import dataclass, field
import json
import math
import re
import unicodedata
from typing import Protocol

import requests
from requests.adapters import HTTPAdapter

from app.agent_query_contract import QUERY_TOOLS, QueryPlan
from app.dialogue_contract import DIALOGUE_TOOLS, DialoguePlan


RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 128 * 1024
MAX_REPLY_CHARACTERS = 8000
MAX_QUERY_ARGUMENT_BYTES = 8 * 1024
MAX_DIALOGUE_ARGUMENT_BYTES = 24 * 1024
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z", re.ASCII)
_KEY = re.compile(r"[\x21-\x7e]{1,512}\Z", re.ASCII)
_ERRORS = {
    "llm_not_configured": (503, "O modelo de linguagem ainda não foi configurado."),
    "llm_unavailable": (502, "Não foi possível consultar o modelo de linguagem."),
    "llm_invalid_response": (502, "O modelo devolveu uma resposta inválida."),
    "llm_credentials_rejected": (503, "A credencial do modelo não foi aceita."),
    "llm_rate_limited": (503, "O limite de uso do modelo foi atingido."),
    "llm_timeout": (504, "O modelo não respondeu dentro do tempo configurado."),
}


@dataclass(frozen=True)
class ModelReply:
    """Texto privado e somente metadados necessários à aplicação."""

    text: str = field(repr=False)
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LanguageModel(Protocol):
    """Contrato usado pela aplicação, sem referência ao fornecedor de IA."""

    def generate(self, *, instructions: str, message: str) -> ModelReply:
        ...


class ModelError(Exception):
    """Código, mensagem e status fechados; nunca receber texto do fornecedor."""

    def __init__(self, code: str = "llm_unavailable", http_status: int = 502):
        # O status é determinado pelo código conhecido. Um chamador não pode
        # introduzir mensagens/estados arbitrários por meio desta exceção.
        safe_code = code if isinstance(code, str) and code in _ERRORS else "llm_unavailable"
        status, message = _ERRORS[safe_code]
        super().__init__(message)
        self.code = safe_code
        self.http_status = status


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError()


def _valid_text(value) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return not any(ord(character) < 32 and character not in "\t\n\r" or ord(character) == 127
                   for character in value)


def _token_count(usage: dict, name: str) -> int | None:
    value = usage.get(name)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ModelError("llm_invalid_response")
    return value


def _parse_reply(raw: bytes) -> ModelReply:
    """Aceitar texto final completo; chamadas de ferramentas ficam para outra etapa."""
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if (not isinstance(payload, dict) or payload.get("status") != "completed"
                or payload.get("error") is not None or payload.get("incomplete_details") is not None):
            raise ValueError()
        model = payload.get("model")
        output = payload.get("output")
        if (not isinstance(model, str) or _MODEL.fullmatch(model) is None
                or not isinstance(output, list) or not output):
            raise ValueError()

        texts = []
        message_count = 0
        for item in output:
            if not isinstance(item, dict):
                raise ValueError()
            if item.get("type") == "reasoning":
                continue
            if (item.get("type") != "message" or item.get("role") != "assistant"
                    or item.get("status") != "completed"):
                raise ValueError()
            content = item.get("content")
            if not isinstance(content, list) or not content:
                raise ValueError()
            message_count += 1
            for part in content:
                if (not isinstance(part, dict) or part.get("type") != "output_text"
                        or not _valid_text(part.get("text"))):
                    raise ValueError()
                texts.append(part["text"])

        text = "\n".join(texts)
        if not message_count or not 1 <= len(text) <= MAX_REPLY_CHARACTERS:
            raise ValueError()
        usage = payload.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise ValueError()
        return ModelReply(
            text=text, model=model,
            input_tokens=_token_count(usage, "input_tokens"),
            output_tokens=_token_count(usage, "output_tokens"),
        )
    except ModelError:
        raise
    except Exception:
        raise ModelError("llm_invalid_response") from None


def _query_argument_is_valid(key, value, specification) -> bool:
    return _valid_text(value) and ("enum" not in specification or value in specification["enum"])


def _dialogue_argument_is_valid(key, value, specification) -> bool:
    """Validar tipos JSON estritos; regras da agenda pertencem ao serviço."""
    allowed_types = specification["type"]
    if not isinstance(allowed_types, list):
        allowed_types = [allowed_types]
    if value is None:
        valid = "null" in allowed_types
    elif isinstance(value, bool):
        valid = "boolean" in allowed_types
    elif isinstance(value, int):
        valid = ("integer" in allowed_types
                 and value >= specification.get("minimum", value)
                 and value <= specification.get("maximum", value))
    elif isinstance(value, str):
        valid = ("string" in allowed_types
                 and (bool(value.strip()) or value == "" and key in {"title", "description", "location"}))
        if valid:
            value.encode("utf-8")
            multiline = key in {"title", "description", "location"}
            valid = not any(
                unicodedata.category(character) in {"Cc", "Cs"}
                and not (multiline and character in "\t\r\n")
                for character in value
            )
    else:
        valid = False
    return valid and ("enum" not in specification or value in specification["enum"])


def _parse_function_plan(raw, *, tools, max_argument_bytes, argument_validator, plan_type):
    """Aceitar uma única intenção completa e validar argumentos sem executá-los."""
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if (not isinstance(payload, dict) or payload.get("status") != "completed"
                or payload.get("error") is not None or payload.get("incomplete_details") is not None):
            raise ValueError()
        model = payload.get("model")
        output = payload.get("output")
        if (not isinstance(model, str) or _MODEL.fullmatch(model) is None
                or not isinstance(output, list) or not output):
            raise ValueError()

        call = None
        for item in output:
            if not isinstance(item, dict):
                raise ValueError()
            if item.get("type") == "reasoning":
                continue
            if (item.get("type") != "function_call" or call is not None
                    or ("status" in item and item["status"] != "completed")
                    or not _valid_text(item.get("call_id"))):
                raise ValueError()
            call = item
        if call is None:
            raise ValueError()
        name = call.get("name")
        schemas = {tool["name"]: tool["parameters"] for tool in tools}
        if not isinstance(name, str) or name not in schemas:
            raise ValueError()
        raw_arguments = call.get("arguments")
        if not isinstance(raw_arguments, str) or not 1 <= len(raw_arguments.encode("utf-8")) <= max_argument_bytes:
            raise ValueError()
        arguments = json.loads(
            raw_arguments, object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        schema = schemas[name]
        if not isinstance(arguments, dict) or set(arguments) != set(schema["required"]):
            raise ValueError()
        for key, value in arguments.items():
            specification = schema["properties"][key]
            if not argument_validator(key, value, specification):
                raise ValueError()
        usage = payload.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise ValueError()
        return plan_type(
            name=name, arguments=arguments, model=model,
            input_tokens=_token_count(usage, "input_tokens"),
            output_tokens=_token_count(usage, "output_tokens"),
        )
    except ModelError:
        raise
    except Exception:
        raise ModelError("llm_invalid_response") from None


def _parse_query_plan(raw: bytes) -> QueryPlan:
    return _parse_function_plan(
        raw, tools=QUERY_TOOLS, max_argument_bytes=MAX_QUERY_ARGUMENT_BYTES,
        argument_validator=_query_argument_is_valid, plan_type=QueryPlan,
    )


def _parse_dialogue_plan(raw: bytes) -> DialoguePlan:
    return _parse_function_plan(
        raw, tools=DIALOGUE_TOOLS, max_argument_bytes=MAX_DIALOGUE_ARGUMENT_BYTES,
        argument_validator=_dialogue_argument_is_valid, plan_type=DialoguePlan,
    )


class OpenAIResponsesModel:
    """REST síncrono e sem efeitos no construtor; sem retries automáticos."""

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float = 30,
                 max_output_tokens: int = 600, session_factory=None):
        try:
            if (not isinstance(api_key, str) or _KEY.fullmatch(api_key) is None
                    or not isinstance(model, str) or _MODEL.fullmatch(model) is None
                    or not isinstance(timeout_seconds, (int, float))
                    or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
                    or timeout_seconds <= 0
                    or not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool)
                    or max_output_tokens <= 0
                    or (session_factory is not None and not callable(session_factory))):
                raise ValueError()
            self._api_key = api_key
            self._model = model
            self._timeout_seconds = float(timeout_seconds)
            self._max_output_tokens = max_output_tokens
            self._session_factory = requests.Session if session_factory is None else session_factory
        except Exception:
            raise ModelError() from None

    def generate(self, *, instructions: str, message: str) -> ModelReply:
        if not _valid_text(instructions) or not _valid_text(message):
            raise ModelError()
        return _parse_reply(self._request_response({
            "model": self._model, "instructions": instructions,
            "input": [{"role": "user", "content": message}],
            "max_output_tokens": self._max_output_tokens, "store": False,
        }))

    def plan_query(self, *, instructions: str, message: str) -> QueryPlan:
        if not _valid_text(instructions) or not _valid_text(message):
            raise ModelError()
        return _parse_query_plan(self._request_response({
            "model": self._model, "instructions": instructions,
            "input": [{"role": "user", "content": message}],
            "max_output_tokens": self._max_output_tokens, "store": False,
            "tools": QUERY_TOOLS, "tool_choice": "required", "parallel_tool_calls": False,
        }))

    def plan_dialogue(self, *, instructions: str, message: str) -> DialoguePlan:
        if not _valid_text(instructions) or not _valid_text(message):
            raise ModelError()
        return _parse_dialogue_plan(self._request_response({
            "model": self._model, "instructions": instructions,
            "input": [{"role": "user", "content": message}],
            "max_output_tokens": self._max_output_tokens, "store": False,
            "tools": DIALOGUE_TOOLS, "tool_choice": "required", "parallel_tool_calls": False,
        }))

    def _request_response(self, payload: dict) -> bytes:
        """Transporte único para texto e planos; sem reenvio ou retorno de erros brutos."""
        try:
            with self._session_factory() as session:
                session.trust_env = False
                session.mount("https://", HTTPAdapter(max_retries=0))
                with session.post(
                    RESPONSES_URL,
                    headers={"Authorization": "Bearer " + self._api_key,
                             "Content-Type": "application/json", "Accept": "application/json"},
                    json=payload,
                    timeout=(5, self._timeout_seconds), allow_redirects=False,
                    stream=True, verify=True,
                ) as response:
                    if response.status_code in (401, 403):
                        raise ModelError("llm_credentials_rejected", http_status=503)
                    if response.status_code == 429:
                        raise ModelError("llm_rate_limited", http_status=503)
                    if response.status_code != 200:
                        raise ModelError()
                    raw = bytearray()
                    for chunk in response.iter_content(chunk_size=8192):
                        if not isinstance(chunk, bytes) or len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ModelError("llm_invalid_response")
                        raw.extend(chunk)
                    return bytes(raw)
        except ModelError:
            raise
        except requests.Timeout:
            raise ModelError("llm_timeout", http_status=504) from None
        except Exception:
            raise ModelError() from None
