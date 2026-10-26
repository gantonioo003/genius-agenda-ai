"""Adaptador Gemini REST sem estado remoto, retries ou execução de ferramentas.

Os esquemas pertencem à aplicação. Uma resposta só se torna um plano após a
mesma validação local usada pelo outro fornecedor; a agenda continua independente.
"""

from copy import deepcopy
import json
import math

import requests
from requests.adapters import HTTPAdapter

from app.agent_query_contract import QUERY_TOOLS, QueryPlan
from app.dialogue_contract import DIALOGUE_TOOLS, DialoguePlan
from app.llm import (
    MAX_DIALOGUE_ARGUMENT_BYTES, MAX_QUERY_ARGUMENT_BYTES, MAX_REPLY_CHARACTERS,
    MAX_RESPONSE_BYTES, ModelError, ModelReply, _KEY, _MODEL,
    _dialogue_argument_is_valid, _invalid_constant, _query_argument_is_valid,
    _token_count, _unique_object, _valid_text,
)


GENERATE_CONTENT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/"
MIN_OUTPUT_TOKENS = 128
MAX_OUTPUT_TOKENS = 4096


def _response(raw: bytes, configured_model: str):
    """Exigir um candidato final; ignorar apenas metadados documentados."""
    payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                         parse_constant=_invalid_constant)
    if not isinstance(payload, dict) or payload.get("error") is not None:
        raise ValueError()
    feedback = payload.get("promptFeedback", {})
    if (not isinstance(feedback, dict)
            or feedback.get("blockReason") not in (None, "BLOCK_REASON_UNSPECIFIED")):
        raise ValueError()
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError()
    candidate = candidates[0]
    if (not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP"
            or candidate.get("continuationToken") is not None):
        raise ValueError()
    if "index" in candidate and (type(candidate["index"]) is not int or candidate["index"] != 0):
        raise ValueError()
    for owner in (feedback, candidate):
        ratings = owner.get("safetyRatings", [])
        if not isinstance(ratings, list):
            raise ValueError()
        for rating in ratings:
            if (not isinstance(rating, dict)
                    or ("blocked" in rating and type(rating["blocked"]) is not bool)
                    or rating.get("blocked", False)):
                raise ValueError()
    content = candidate.get("content")
    if not isinstance(content, dict) or content.get("role") != "model":
        raise ValueError()
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError()
    visible = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError()
        if "thoughtSignature" in part and not _valid_text(part["thoughtSignature"]):
            raise ValueError()
        if "thought" in part and type(part["thought"]) is not bool:
            raise ValueError()
        if "thoughtSignature" in part and not set(part) - {"thought", "thoughtSignature"}:
            # Uma assinatura pode vir sem resumo. Continua sendo somente
            # metadado opaco e não é devolvida ou armazenada pela aplicação.
            continue
        # Gemini pode incluir um resumo de raciocínio. Ele nunca vira resposta,
        # argumento de ferramenta, histórico ou instrução para o serviço.
        if part.get("thought") is True:
            if (set(part) - {"thought", "text", "thoughtSignature"}
                    or not _valid_text(part.get("text"))):
                raise ValueError()
            continue
        visible.append(part)
    if not visible:
        raise ValueError()
    model = payload.get("modelVersion", configured_model)
    if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
        raise ValueError()
    usage = payload.get("usageMetadata", {})
    if not isinstance(usage, dict):
        raise ValueError()
    tokens = {
        "input_tokens": _token_count(usage, "promptTokenCount"),
        "output_tokens": _token_count(usage, "candidatesTokenCount"),
    }
    # Mesmo metadados não utilizados não podem carregar números inválidos.
    for name in ("thoughtsTokenCount", "totalTokenCount", "cachedContentTokenCount"):
        _token_count(usage, name)
    return visible, model, tokens


def _parse_reply(raw: bytes, configured_model: str) -> ModelReply:
    try:
        parts, model, tokens = _response(raw, configured_model)
        texts = []
        for part in parts:
            if (set(part) - {"text", "thought", "thoughtSignature"}
                    or not _valid_text(part.get("text"))):
                raise ValueError()
            texts.append(part["text"])
        text = "".join(texts)
        if not 1 <= len(text) <= MAX_REPLY_CHARACTERS:
            raise ValueError()
        return ModelReply(text=text, model=model, **tokens)
    except ModelError:
        raise
    except Exception:
        raise ModelError("llm_invalid_response") from None


def _parse_plan(raw: bytes, configured_model: str, *, tools, max_argument_bytes,
                argument_validator, plan_type):
    try:
        parts, model, tokens = _response(raw, configured_model)
        if len(parts) != 1:
            raise ValueError()
        part = parts[0]
        if set(part) - {"functionCall", "thought", "thoughtSignature"}:
            raise ValueError()
        call = part.get("functionCall")
        if not isinstance(call, dict) or set(call) - {"name", "args", "id"}:
            raise ValueError()
        if "id" in call and not _valid_text(call["id"]):
            raise ValueError()
        schemas = {tool["name"]: tool["parameters"] for tool in tools}
        name = call.get("name")
        if not isinstance(name, str) or name not in schemas:
            raise ValueError()
        arguments = call.get("args")
        schema = schemas[name]
        if not isinstance(arguments, dict) or set(arguments) != set(schema["required"]):
            raise ValueError()
        argument_bytes = json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if not 1 <= len(argument_bytes) <= max_argument_bytes:
            raise ValueError()
        for key, value in arguments.items():
            if not argument_validator(key, value, schema["properties"][key]):
                raise ValueError()
        return plan_type(name=name, arguments=arguments, model=model, **tokens)
    except ModelError:
        raise
    except Exception:
        raise ModelError("llm_invalid_response") from None


class GeminiModel:
    """Uma chamada síncrona por turno, chave somente em cabeçalho HTTPS."""

    def __init__(self, api_key: str, model: str, *, timeout_seconds: float = 30,
                 max_output_tokens: int = 2048, session_factory=None):
        try:
            if (not isinstance(api_key, str) or _KEY.fullmatch(api_key) is None
                    or not isinstance(model, str) or _MODEL.fullmatch(model) is None
                    or not isinstance(timeout_seconds, (int, float))
                    or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
                    or timeout_seconds <= 0
                    or not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool)
                    or not MIN_OUTPUT_TOKENS <= max_output_tokens <= MAX_OUTPUT_TOKENS
                    or (session_factory is not None and not callable(session_factory))):
                raise ValueError()
            self._api_key = api_key
            self._model = model
            self._timeout_seconds = float(timeout_seconds)
            self._max_output_tokens = max_output_tokens
            self._session_factory = requests.Session if session_factory is None else session_factory
        except Exception:
            raise ModelError() from None

    def _payload(self, *, instructions, message, tools=None):
        if not _valid_text(instructions) or not _valid_text(message):
            raise ModelError()
        payload = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": message}]}],
            "generationConfig": {"candidateCount": 1, "maxOutputTokens": self._max_output_tokens},
        }
        if self._model == "gemini-3.8-flash":
            # Esse modelo não aceita MINIMAL. LOW é documentado e evita que
            # o orçamento curto da integração seja gasto em raciocínio médio.
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "LOW"}
        if tools is not None:
            # parametersJsonSchema aceita JSON Schema, incluindo null, enum e
            # additionalProperties. Campos próprios de Responses não são enviados.
            payload["tools"] = [{"functionDeclarations": [
                {"name": tool["name"], "description": tool["description"],
                 "parametersJsonSchema": deepcopy(tool["parameters"])} for tool in tools
            ]}]
            payload["toolConfig"] = {"functionCallingConfig": {
                "mode": "ANY", "allowedFunctionNames": [tool["name"] for tool in tools],
            }}
        return payload

    def generate(self, *, instructions: str, message: str) -> ModelReply:
        return _parse_reply(self._request(self._payload(instructions=instructions, message=message)),
                            self._model)

    def plan_query(self, *, instructions: str, message: str) -> QueryPlan:
        return _parse_plan(self._request(self._payload(
            instructions=instructions, message=message, tools=QUERY_TOOLS)), self._model,
            tools=QUERY_TOOLS, max_argument_bytes=MAX_QUERY_ARGUMENT_BYTES,
            argument_validator=_query_argument_is_valid, plan_type=QueryPlan)

    def plan_dialogue(self, *, instructions: str, message: str) -> DialoguePlan:
        return _parse_plan(self._request(self._payload(
            instructions=instructions, message=message, tools=DIALOGUE_TOOLS)), self._model,
            tools=DIALOGUE_TOOLS, max_argument_bytes=MAX_DIALOGUE_ARGUMENT_BYTES,
            argument_validator=_dialogue_argument_is_valid, plan_type=DialoguePlan)

    def _request(self, payload: dict) -> bytes:
        try:
            with self._session_factory() as session:
                session.trust_env = False
                session.mount("https://", HTTPAdapter(max_retries=0))
                with session.post(
                    GENERATE_CONTENT_BASE_URL + self._model + ":generateContent",
                    headers={"x-goog-api-key": self._api_key,
                             "Content-Type": "application/json", "Accept": "application/json"},
                    json=payload, timeout=(5, self._timeout_seconds),
                    allow_redirects=False, stream=True, verify=True,
                ) as response:
                    if response.status_code in (401, 403):
                        raise ModelError("llm_credentials_rejected")
                    if response.status_code == 429:
                        raise ModelError("llm_rate_limited")
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
            raise ModelError("llm_timeout") from None
        except Exception:
            raise ModelError() from None
