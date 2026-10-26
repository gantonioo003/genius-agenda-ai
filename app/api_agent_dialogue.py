"""Turnos autenticados de esclarecimento; preparar dados nunca executa escrita."""
import asyncio
import json
from dataclasses import replace

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_conversations import (
    conversation_error_response, conversation_owner_key, conversation_store,
    validate_conversation_id,
)
from app.api_confirmations import confirmation_service, confirmation_error_response
from app.confirmation_state import ConfirmationError
from app.services.confirmations import is_confirmation_message, is_rejection_message, validate_confirmation_result
from app.api_security import security_envelope
from app.conversation_store import ConversationError, validate_memory
from app.llm import ModelError
from app.services.agent_preview import validate_preview_message
from app.services.agent_dialogue import default_agent_dialogue_service, validate_dialogue_result

_MODEL_ERRORS = {
    'llm_not_configured': (503, 'O modelo de IA ainda não está configurado.'),
    'llm_credentials_rejected': (503, 'Não foi possível autenticar o modelo de IA.'),
    'llm_rate_limited': (503, 'O modelo de IA está temporariamente indisponível.'),
    'llm_timeout': (504, 'O modelo de IA não respondeu no prazo.'),
    'llm_invalid_response': (502, 'O modelo de IA retornou uma resposta inválida.'),
    'llm_unavailable': (502, 'Não foi possível interpretar o pedido de IA.'),
}

def _response(request, *, status='failed', action=None, message='Não foi possível esclarecer o pedido.',
              data=None, code=None, http_status=200):
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status=status, action=action, operation_id=None, mutation_state='not_applied',
        message=message, data={**data, 'execution_allowed': False} if data is not None else None,
        error={'code': code, 'retryable': False, 'fields': []} if code else None,
    ))

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result

def _invalid_constant(_value):
    raise ValueError()

def register_agent_dialogue_routes(router):
    @router.post('/agent/dialogue', name='api_v1_agent_dialogue', tags=['agent'])
    async def dialogue_agent(request: Request):
        try:
            content_types = request.headers.getlist('content-type')
            if (request.scope.get('query_string') or len(content_types) != 1
                    or content_types[0].split(';', 1)[0].strip().lower() != 'application/json'):
                raise ValueError()
            raw = bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 24 * 1024:
                        raise ValueError()
                    raw.extend(chunk)
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_invalid_constant)
            if type(body) is not dict or set(body) != {'message', 'conversation_id'}:
                raise ValueError()
            message = validate_preview_message(body['message'], max_input_chars=request.app.state.settings.llm_max_input_chars)
            conversation_id = validate_conversation_id(body['conversation_id'])
        except Exception:
            return _response(request, code='invalid_request', http_status=422,
                             message='Informe uma mensagem e uma conversa válida para continuar.')

        def execute():
            try:
                store = conversation_store(request)
                owner = conversation_owner_key(request.app.state.settings)
                snapshot = store.load(owner, conversation_id)
            except ConversationError:
                raise
            except Exception:
                raise ConversationError('conversation_unavailable') from None
            if snapshot.memory.confirmation is not None and (is_confirmation_message(message) or is_rejection_message(message)):
                selected_service = confirmation_service(request)
                if is_rejection_message(message):
                    result, updated = selected_service.revoke(snapshot.memory, snapshot.memory.confirmation.proposal_id)
                else:
                    result, updated = selected_service.approve(snapshot.memory, snapshot.memory.confirmation.proposal_id, message)
                result = validate_confirmation_result(result)
                updated = validate_memory(updated)
                if updated != snapshot.memory:
                    store.save(owner, conversation_id, snapshot.revision, updated)
                return result
            factory = request.app.state.agent_dialogue_service_factory
            service = (default_agent_dialogue_service(request.app.state.settings,
                       request.app.state.calendar_gateway_factory) if factory is None else factory())
            result, memory = service.respond(message, snapshot.memory)
            try:
                result = validate_dialogue_result(result)
                memory = validate_memory(memory)
            except Exception:
                raise ModelError('llm_invalid_response') from None
            if result.status != 'failed':
                memory = replace(memory, confirmation=None)
                try:
                    store.save(owner, conversation_id, snapshot.revision, memory)
                except ConversationError:
                    raise
                except Exception:
                    raise ConversationError('conversation_unavailable') from None
            return result

        try:
            result = await run_in_threadpool(execute)
        except ConversationError as error:
            return conversation_error_response(request, error)
        except ConfirmationError as error:
            return confirmation_error_response(request, error.code, error.http_status)
        except ModelError as error:
            code = error.code if isinstance(error.code, str) and error.code in _MODEL_ERRORS else 'llm_unavailable'
            status, safe_message = _MODEL_ERRORS[code]
            return _response(request, code=code, http_status=status, message=safe_message)
        except Exception:
            return _response(request, code='operation_failed', http_status=502)
        return _response(request, status=result.status, action=result.action, message=result.message,
                         data=result.data, code=result.error_code, http_status=result.http_status)
