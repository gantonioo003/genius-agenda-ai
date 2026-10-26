"""Entrada autorizada: n8n recebe só metadados; texto do agente fica na fila privada.

O token na URL é uma capacidade secreta, não uma assinatura do provedor.
No modo agente, persistir as mensagens precede o ack; status nunca gera resposta.
O modo legado permanece disponível sem ativar a fila.
"""

from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone
import hmac
import json
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import requests
from requests.adapters import HTTPAdapter
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from app.whatsapp_inbox import InboxClaim, InboxEvent, InboxStore, make_event_key
from app.whatsapp_agent_store import AgentInput, AgentStore
from app.reminder_store import ReminderStore


MAX_BODY_BYTES = 64 * 1024
N8N_URL = "http://127.0.0.1:5678/webhook/genius-agenda/whatsapp-sandbox"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "secrets" / "whatsapp-sandbox-local.json"
SECURITY_PATH = PROJECT_ROOT / "secrets" / "whatsapp-sandbox-security.json"
RECEIPTS_PATH = PROJECT_ROOT / "data" / "whatsapp-sandbox-receipts.jsonl"
DIAGNOSTICS_PATH = PROJECT_ROOT / "data" / "whatsapp-sandbox-ingress.jsonl"
MAX_DIAGNOSTIC_BYTES = 128 * 1024
MESSAGE_TYPES = frozenset({
    "text", "image", "audio", "video", "document", "sticker", "location",
    "contacts", "interactive", "button", "reaction", "order", "system",
    "unknown", "unsupported",
})
STATUS_TYPES = frozenset({"sent", "delivered", "read", "failed", "played"})
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,}\Z", re.ASCII)
PHONE_PATTERN = re.compile(r"[1-9][0-9]{6,14}\Z", re.ASCII)


class InvalidPayload(ValueError):
    """Corpo rejeitado, sem incorporar o conteúdo recebido na exceção."""


class ForbiddenSender(ValueError):
    """Identidade válida, mas não autorizada pela política local explícita."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPayload()
        result[key] = value
    return result


def _invalid_constant(_value):
    raise InvalidPayload()


def _decode_json(raw: bytes):
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidPayload() from None


def _config_values(config: Mapping) -> tuple[str, str]:
    if not isinstance(config, Mapping):
        raise ValueError("Configuração local inválida.")
    for key in ("callback_token", "webhook_token"):
        token = config.get(key)
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Configuração local inválida.")
    for key in ("workflow_id", "webhook_credential_id"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError("Configuração local inválida.")
    return config["callback_token"], config["webhook_token"]


def _validate_allowed_sender(value: str) -> str:
    if not isinstance(value, str) or PHONE_PATTERN.fullmatch(value) is None:
        raise ValueError("Política local de remetente inválida.")
    return value


def load_allowed_sender(raw: bytes) -> str:
    """Política privada independente, com JSON estrito e somente uma chave."""
    try:
        policy = _decode_json(raw)
        if not isinstance(policy, dict) or set(policy) != {"allowed_phone"}:
            raise ValueError()
        return _validate_allowed_sender(policy["allowed_phone"])
    except (ValueError, TypeError):
        raise ValueError("Política local de remetente inválida.") from None


def _bounded_string(value, maximum=1024) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum


def _sender(event: dict, normal: str, scoped: str):
    # Identificador apenas validado em memória; nunca encaminhado ou registrado.
    supplied = [event[key] for key in (normal, scoped) if event.get(key) is not None]
    if not supplied or any(not _bounded_string(value) for value in supplied):
        raise InvalidPayload()


def _common_event(event):
    if not isinstance(event, dict) or not _bounded_string(event.get("id")):
        raise InvalidPayload()
    timestamp = event.get("timestamp")
    if not isinstance(timestamp, str) or not re.fullmatch(r"[0-9]{1,20}", timestamp):
        raise InvalidPayload()


def summarize_payload(payload) -> dict:
    """Valida os envelopes suportados e descarta integralmente dados pessoais."""
    if not isinstance(payload, dict):
        raise InvalidPayload()
    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries:
        raise InvalidPayload()
    message_count = status_count = 0
    event_types = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise InvalidPayload()
        changes = entry.get("changes")
        if not isinstance(changes, list) or not changes:
            raise InvalidPayload()
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                raise InvalidPayload()
            value = change.get("value")
            if not isinstance(value, dict) or not ({"messages", "statuses"} & value.keys()):
                raise InvalidPayload()
            for group in ("messages", "statuses"):
                if group not in value:
                    continue
                events = value[group]
                if not isinstance(events, list) or not events:
                    raise InvalidPayload()
                for event in events:
                    _common_event(event)
                    if group == "messages":
                        _sender(event, "from", "from_user_id")
                        event_type = event.get("type")
                        if not isinstance(event_type, str) or event_type not in MESSAGE_TYPES:
                            raise InvalidPayload()
                        if event_type == "text":
                            text = event.get("text")
                            if not isinstance(text, dict) or not _bounded_string(text.get("body"), 4096):
                                raise InvalidPayload()
                        elif event_type not in {"unknown", "unsupported"}:
                            expected_type = list if event_type == "contacts" else dict
                            if not isinstance(event.get(event_type), expected_type):
                                raise InvalidPayload()
                        message_count += 1
                        event_types.add("message_" + event_type)
                    else:
                        _sender(event, "recipient_id", "recipient_user_id")
                        event_type = event.get("status")
                        if not isinstance(event_type, str) or event_type not in STATUS_TYPES:
                            raise InvalidPayload()
                        status_count += 1
                        event_types.add("status_" + event_type)
                    if message_count > 100 or status_count > 100 or len(event_types) > 20:
                        raise InvalidPayload()
    if not message_count + status_count:
        raise InvalidPayload()
    return {
        "source": "360dialog_sandbox",
        "request_id": str(uuid4()),
        "message_count": message_count,
        "status_count": status_count,
        "event_types": sorted(event_types),
    }


def _authorize_senders(payload: dict, allowed_sender: str):
    """Verifica todos os eventos após validar o esquema; rejeita o lote inteiro."""
    expected = allowed_sender.encode("ascii")
    for entry in payload["entry"]:
        for change in entry["changes"]:
            value = change["value"]
            for group, phone_key in (("messages", "from"), ("statuses", "recipient_id")):
                for event in value.get(group, ()):
                    phone = event.get(phone_key)
                    if phone is None or not hmac.compare_digest(phone.encode("utf-8"), expected):
                        raise ForbiddenSender()


def _inbox_events(payload: dict) -> tuple[InboxEvent, ...]:
    """Somente após validar e autorizar o lote inteiro, derivar suas identidades.

    IDs do provedor ficam apenas em memória; a inbox recebe hashes separados
    por mensagem e por fase de status. O texto nunca participa da identidade.
    """
    events = []
    for entry in payload["entry"]:
        for change in entry["changes"]:
            value = change["value"]
            for group, kind, type_key in (("messages", "message", "type"),
                                           ("statuses", "status", "status")):
                for event in value.get(group, ()):
                    event_type = kind + "_" + event[type_key]
                    events.append(InboxEvent(
                        make_event_key(kind, event["id"], event_type), event_type,
                    ))
    return tuple(events)


def _claim_summary(claim: InboxClaim) -> dict:
    """Mantém os cinco campos do contrato, agora só com os eventos novos."""
    return {
        "source": "360dialog_sandbox", "request_id": claim.request_id,
        "message_count": sum(event.event_type.startswith("message_") for event in claim.events),
        "status_count": sum(event.event_type.startswith("status_") for event in claim.events),
        "event_types": sorted({event.event_type for event in claim.events}),
    }


def _policy_relation_flags(payload: dict, allowed_sender: str) -> dict[str, bool]:
    """Somente relações booleanas; a hipótese legada nunca concede autorização."""
    legacy_candidate = None
    if (len(allowed_sender) == 13 and allowed_sender.isascii() and allowed_sender.isdigit()
            and allowed_sender.startswith("55")
            and allowed_sender[4] == "9"):
        legacy_candidate = allowed_sender[:4] + allowed_sender[5:]
    phones = []
    for entry in payload["entry"]:
        for change in entry["changes"]:
            value = change["value"]
            for group, key in (("messages", "from"), ("statuses", "recipient_id")):
                phones.extend(event.get(key) for event in value.get(group, ()))
    return {
        "all_events_phone_matches_policy": bool(phones) and all(
            phone == allowed_sender for phone in phones
        ),
        "all_events_phone_matches_br_legacy": bool(phones) and legacy_candidate is not None and all(
            phone == legacy_candidate for phone in phones
        ),
    }


def make_forwarder(webhook_token: str) -> Callable[[dict], dict]:
    """Chama um único endereço fixo; ignora proxies do ambiente e redirecionamentos."""
    def forward(summary: dict) -> dict:
        with requests.Session() as session:
            session.trust_env = False
            session.mount("http://", HTTPAdapter(max_retries=0))
            response = session.post(
                N8N_URL, json=summary,
                headers={"X-Genius-Webhook-Key": webhook_token},
                timeout=(2, 20), allow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError("Fluxo indisponível.")
            return _decode_json(response.content)
    return forward


def _agent_inputs(payload: dict, claim: InboxClaim) -> tuple[AgentInput, ...]:
    """Texto mínimo privado, somente das mensagens novas do lote autorizado."""
    fresh = {event.event_key: event.event_type for event in claim.events
             if event.event_type.startswith("message_")}
    selected = {}
    for entry in payload["entry"]:
        for change in entry["changes"]:
            for event in change["value"].get("messages", ()):
                event_type = "message_" + event["type"]
                key = make_event_key("message", event["id"], event_type)
                if key in fresh and key not in selected:
                    if fresh[key] != event_type:
                        raise InvalidPayload()
                    selected[key] = AgentInput(key, event_type,
                        event["text"]["body"] if event_type == "message_text" else None)
    if set(selected) != set(fresh):
        raise InvalidPayload()
    return tuple(selected.values())


def _observe_reminder_messages(payload, claim, store, sender_key, received_at):
    """Somente IDs novos autorizados; timestamp original, sem texto ou telefone.

    Capacidade secreta do callback não equivale a assinatura do provedor. Uma
    observação inválida fica imutável para impedir reparo por replay do mesmo ID.
    Status de saída, horário de processamento e retry nunca abrem a janela.
    """
    fresh = {event.event_key: event.event_type for event in claim.events
             if event.event_type.startswith('message_')}
    seen = set()
    for entry in payload['entry']:
        for change in entry['changes']:
            for event in change['value'].get('messages', ()):
                event_type = 'message_' + event['type']
                key = make_event_key('message', event['id'], event_type)
                if key not in fresh or key in seen:
                    continue
                if fresh[key] != event_type:
                    raise InvalidPayload()
                store.observe_inbound(key, sender_key, event['timestamp'], received_at,
                    message_type=event['type'], provenance='sandbox_callback_capacity')
                seen.add(key)
    if seen != set(fresh):
        raise InvalidPayload()


def _valid_reply(reply, request_id: str, message_count: int, *, agent_enabled=False) -> bool:
    return (
        isinstance(reply, dict)
        and set(reply) == {"status", "api_reachable", "request_id", "reply_state"}
        and reply["status"] == "received"
        and reply["api_reachable"] is True
        and reply["request_id"] == request_id
        and reply["reply_state"] == (("queued" if agent_enabled else "accepted") if message_count else "skipped")
    )


def _shape_flags(payload=None) -> dict[str, bool | str]:
    """Somente presença de campos conhecidos, sem copiar valores da requisição."""
    flags = dict.fromkeys(("has_entry", "has_messages", "has_statuses",
                           "change_has_field", "message_has_timestamp",
                           "firstsender_phone_valid", "firstsender_scoped_valid",
                           "all_events_phone_matches_policy", "all_events_phone_matches_br_legacy"), False)
    flags.update(timestamp_kind="missing", firstmessage_type="other", sender_key="missing")
    if not isinstance(payload, dict):
        return flags
    flags["has_entry"] = "entry" in payload
    values = [payload]
    entries = payload.get("entry")
    for entry in entries if isinstance(entries, list) else ():
        changes = entry.get("changes") if isinstance(entry, dict) else None
        for change in changes if isinstance(changes, list) else ():
            if isinstance(change, dict):
                flags["change_has_field"] |= "field" in change
                if isinstance(change.get("value"), dict):
                    values.append(change["value"])
    first_message_seen = False
    for value in values:
        flags["has_messages"] |= "messages" in value
        flags["has_statuses"] |= "statuses" in value
        messages = value.get("messages")
        if isinstance(messages, list):
            flags["message_has_timestamp"] |= any(
                isinstance(message, dict) and "timestamp" in message for message in messages
            )
            if messages and not first_message_seen:
                first_message_seen = True
                message = messages[0]
                if isinstance(message, dict):
                    flags["firstsender_phone_valid"] = _bounded_string(message.get("from"))
                    flags["firstsender_scoped_valid"] = _bounded_string(message.get("from_user_id"))
                    event_type = message.get("type")
                    if isinstance(event_type, str) and event_type in MESSAGE_TYPES:
                        flags["firstmessage_type"] = event_type
                    if "from" in message and "from_user_id" in message:
                        flags["sender_key"] = "both"
                    elif "from" in message:
                        flags["sender_key"] = "from"
                    elif "from_user_id" in message:
                        flags["sender_key"] = "from_user_id"
                    if "timestamp" in message:
                        timestamp = message["timestamp"]
                        if isinstance(timestamp, str):
                            flags["timestamp_kind"] = (
                                "string_digits" if re.fullmatch(r"[0-9]+", timestamp) else "string_other"
                            )
                        elif isinstance(timestamp, int) and not isinstance(timestamp, bool):
                            flags["timestamp_kind"] = "int"
                        else:
                            flags["timestamp_kind"] = "other"
    return flags


def create_app(config: Mapping, *, allowed_sender: str,
               forward: Callable[[dict], dict] | None = None,
               receipts_path: Path | str = RECEIPTS_PATH,
               diagnostics_path: Path | str = DIAGNOSTICS_PATH,
               inbox_path: Path | str | None = None,
               inbox_store: InboxStore | None = None,
               agent_enabled: bool = False, agent_store: AgentStore | None = None,
               reminder_store: ReminderStore | None = None, clock=None) -> FastAPI:
    """Cria a aplicação sem ler segredos ou inicializar outros módulos do projeto."""
    if type(agent_enabled) is not bool:
        raise ValueError("Modo local do agente inválido.")
    if clock is not None and not callable(clock):
        raise ValueError('Relógio local inválido.')
    selected_clock = clock if clock is not None else lambda: datetime.now(timezone.utc)
    callback_token, webhook_token = _config_values(config)
    selected_sender = _validate_allowed_sender(allowed_sender)
    selected_forward = make_forwarder(webhook_token) if forward is None else forward
    target = Path(receipts_path)
    diagnostic_target = Path(diagnostics_path)
    selected_inbox = inbox_store if inbox_store is not None else InboxStore(
        Path(inbox_path) if inbox_path is not None else target.parent / "whatsapp-inbox.sqlite3"
    )
    selected_agent = (agent_store if agent_store is not None else AgentStore(target.parent / "whatsapp-agent.sqlite3")) if agent_enabled else None
    sender_key = hmac.new(callback_token.encode("ascii"), b"genius-whatsapp-agent-sender:v1\0" + selected_sender.encode("ascii"), "sha256").hexdigest()
    receipt_lock = Lock()
    diagnostic_lock = Lock()

    @asynccontextmanager
    async def lifespan(_application):
        try:
            await run_in_threadpool(selected_inbox.initialize)
            if selected_agent is not None:
                await run_in_threadpool(selected_agent.initialize)
            if reminder_store is not None:
                await run_in_threadpool(reminder_store.initialize)
        except Exception:
            raise RuntimeError("Não foi possível inicializar a inbox local do sandbox.") from None
        yield

    application = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None,
        redirect_slashes=False, lifespan=lifespan,
    )
    application.state.inbox = selected_inbox
    application.state.agent_store = selected_agent
    application.state.agent_enabled = agent_enabled
    application.state.reminder_store = reminder_store

    @application.exception_handler(HTTPException)
    async def safe_http_error(_request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"status": "rejected"})

    def append_receipt(summary: dict):
        # Somente o resumo já sanitizado, nunca o corpo/headers recebidos.
        line = json.dumps(summary, ensure_ascii=True, separators=(",", ":")) + "\n"
        with receipt_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)

    def append_diagnostic(stage: str, status: int | None, shape: dict[str, bool | str | int]):
        record = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "stage": stage, "http_status": status, **shape,
        }
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n"
        with diagnostic_lock:
            diagnostic_target.parent.mkdir(parents=True, exist_ok=True)
            # Dois arquivos locais limitados; nenhum corpo, header ou identificador.
            if diagnostic_target.exists() and diagnostic_target.stat().st_size + len(line) > MAX_DIAGNOSTIC_BYTES:
                diagnostic_target.replace(diagnostic_target.with_suffix(".previous.jsonl"))
            with diagnostic_target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)

    async def record_diagnostic(stage: str, status: int | None, shape=None):
        try:
            await run_in_threadpool(append_diagnostic, stage, status, shape or _shape_flags())
        except Exception:
            # Observabilidade auxiliar não modifica o contrato de recebimento.
            pass

    @application.post("/webhooks/360dialog/{token}")
    async def receive(request: Request, token: str):
        if not hmac.compare_digest(token.encode("utf-8"), callback_token.encode("ascii")):
            return JSONResponse(status_code=404, content={"status": "rejected"})
        # Instante do ingresso, antes de parsing/forward. Não substituir pelo
        # momento do processamento, que pode ser muito posterior ao provedor.
        try:
            received_at = selected_clock()
            if (not isinstance(received_at, datetime) or received_at.tzinfo is None
                    or received_at.utcoffset() is None):
                raise ValueError()
            received_at = received_at.astimezone(timezone.utc)
        except Exception:
            return JSONResponse(status_code=503, content={'status': 'unavailable'})
        await record_diagnostic("received", None)
        if request.scope.get("query_string"):
            await record_diagnostic("rejected", 400)
            return JSONResponse(status_code=400, content={"status": "rejected"})
        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
            await record_diagnostic("rejected", 415)
            return JSONResponse(status_code=415, content={"status": "rejected"})
        shape = _shape_flags()
        try:
            raw = bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > MAX_BODY_BYTES:
                        await record_diagnostic("rejected", 413)
                        return JSONResponse(status_code=413, content={"status": "rejected"})
                    raw.extend(chunk)
            decoded = _decode_json(bytes(raw))
            shape = _shape_flags(decoded)
            summarize_payload(decoded)
            shape.update(_policy_relation_flags(decoded, selected_sender))
            _authorize_senders(decoded, selected_sender)
        except TimeoutError:
            await record_diagnostic("rejected", 408)
            return JSONResponse(status_code=408, content={"status": "rejected"})
        except ForbiddenSender:
            await record_diagnostic("forbidden", 403, shape)
            return JSONResponse(status_code=403, content={"status": "rejected"})
        except Exception:
            # Não retornar mensagens de erro que possam conter corpo ou segredos.
            await record_diagnostic("rejected", 400, shape)
            return JSONResponse(status_code=400, content={"status": "rejected"})

        # Nenhuma identidade pode ocupar a inbox antes da autenticação, da
        # validação integral do esquema e da autorização de todos os remetentes.
        try:
            events = _inbox_events(decoded)
            claim = await run_in_threadpool(selected_inbox.claim, events)
        except Exception:
            await record_diagnostic("inboxfail", 503, shape)
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        outcome = {**shape, "duplicate_count": claim.duplicate_count,
                   "new_count": len(claim.events)}
        if claim.status == "duplicate":
            await record_diagnostic("duplicate", 200, outcome)
            return {"status": "received"}
        if claim.status == "blocked":
            await record_diagnostic("pending", 503, outcome)
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        if claim.status != "new":
            await record_diagnostic("inboxfail", 503, shape)
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        summary = _claim_summary(claim)

        async def uncertain():
            # Se até o registro auxiliar falhar, processing continua bloqueando
            # repetição em outro processo; nunca liberar nova execução aqui.
            try:
                await run_in_threadpool(selected_inbox.mark_uncertain, claim)
            except Exception:
                pass

        try:
            if reminder_store is not None and summary['message_count']:
                await run_in_threadpool(_observe_reminder_messages, decoded, claim,
                                        reminder_store, sender_key, received_at)
            if selected_agent is not None and summary["message_count"]:
                inputs = _agent_inputs(decoded, claim)
                await run_in_threadpool(selected_agent.stage, claim, inputs, sender_key)
            reply = await run_in_threadpool(selected_forward, summary.copy())
            if not _valid_reply(reply, summary["request_id"], summary["message_count"], agent_enabled=agent_enabled):
                raise ValueError("Fluxo indisponível.")
        except Exception:
            await uncertain()
            await record_diagnostic("upstreamfail", 503, outcome)
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        try:
            # SQLite é a prova autoritativa; o JSONL é somente evidência auxiliar.
            await run_in_threadpool(selected_inbox.complete, claim)
        except Exception:
            await uncertain()
            await record_diagnostic("completionfail", 503, outcome)
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        try:
            await run_in_threadpool(append_receipt, summary)
        except Exception:
            await record_diagnostic("receiptfail", 200, outcome)
            return {"status": "received"}
        await record_diagnostic("succeeded", 200, outcome)
        return {"status": "received"}

    return application


def main():
    # Leitura exclusivamente no comando explícito de inicialização.
    import uvicorn
    try:
        config = _decode_json(CONFIG_PATH.read_bytes())
        allowed_sender = load_allowed_sender(SECURITY_PATH.read_bytes())
        agent_enabled = config.get("agent_enabled", False)
        if type(agent_enabled) is not bool:
            raise ValueError("Modo local do agente inválido.")
        selected_agent = None
        from app.settings import Settings
        settings = Settings()
        reminder_path = settings.reminder_database_path
        selected_reminders = ReminderStore(reminder_path if reminder_path.is_absolute()
                                            else PROJECT_ROOT / reminder_path)
        if agent_enabled:
            # Leitura da configuração da API apenas no startup explícito. Os dois
            # processos precisam apontar para a mesma fila, inclusive com path custom.
            configured_path = settings.whatsapp_agent_database_path
            selected_agent = AgentStore(configured_path if configured_path.is_absolute()
                                        else PROJECT_ROOT / configured_path)
        application = create_app(config, allowed_sender=allowed_sender,
                                 agent_enabled=agent_enabled, agent_store=selected_agent,
                                 reminder_store=selected_reminders)
    except Exception:
        raise SystemExit("Não foi possível carregar a configuração local do sandbox.") from None
    uvicorn.run(
        application, host="127.0.0.1", port=8787, access_log=False,
        log_level="warning", proxy_headers=False, server_header=False,
        date_header=False, limit_concurrency=16, backlog=32,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
