"""Prévia e aprovação vinculadas à proposta; nenhum executor é exposto aqui."""
import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_conversations import (conversation_error_response, conversation_owner_key,
                                    conversation_store, validate_conversation_id)
from app.api_security import security_envelope
from app.confirmation_state import ConfirmationError
from app.conversation_store import ConversationError, validate_memory
from app.services.confirmations import ConfirmationService, validate_confirmation_result
from app.services.agent_preview import validate_preview_message

def confirmation_service(request):
    factory = request.app.state.confirmation_service_factory
    return ConfirmationService(request.app.state.calendar_gateway_factory) if factory is None else factory()

def confirmation_error_response(request, code='confirmation_invalid', status=422):
    messages = {422: 'Informe uma solicitação válida para a confirmação.',
                404: 'A confirmação não está disponível nesta conversa.',
                409: 'A confirmação não pode ser usada. Confira a proposta antes de continuar.',
                502: 'Não foi possível conferir o evento para a confirmação.',
                503: 'O serviço de confirmação está temporariamente indisponível.'}
    return JSONResponse(status_code=status, content=security_envelope(
        request, status='failed', action=None, operation_id=None, mutation_state='not_applied',
        message=messages.get(status, messages[503]),
        error={'code': code, 'retryable': False, 'fields': []}))

def _unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError()
        value[name] = item
    return value

def _invalid_constant(_value):
    raise ValueError()

async def _execute(request, command):
    try:
        content_types = request.headers.getlist('content-type')
        if (request.scope.get('query_string') or len(content_types) != 1
                or content_types[0].split(';', 1)[0].strip().lower() != 'application/json'):
            raise ValueError()
        raw = bytearray()
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 2048:
                    raise ValueError()
                raw.extend(chunk)
        body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_invalid_constant)
        keys = {'conversation_id'} if command == 'preview' else {'conversation_id', 'proposal_id'}
        if command == 'approve':
            keys.add('message')
        if type(body) is not dict or set(body) != keys:
            raise ValueError()
        conversation_id = validate_conversation_id(body['conversation_id'])
        if command != 'preview':
            validate_conversation_id(body['proposal_id'])
        if command == 'approve':
            validate_preview_message(body['message'], max_input_chars=128)
    except Exception:
        return confirmation_error_response(request)

    def execute():
        try:
            store = conversation_store(request)
            owner = conversation_owner_key(request.app.state.settings)
            snapshot = store.load(owner, conversation_id)
        except ConversationError:
            raise
        except Exception:
            raise ConversationError('conversation_unavailable') from None
        service = confirmation_service(request)
        if command == 'preview':
            result, memory = service.preview(snapshot.memory)
        elif command == 'approve':
            result, memory = service.approve(snapshot.memory, body['proposal_id'], body['message'])
        else:
            result, memory = getattr(service, command)(snapshot.memory, body['proposal_id'])
        result = validate_confirmation_result(result)
        memory = validate_memory(memory)
        if command != 'status' and memory != snapshot.memory:
            store.save(owner, conversation_id, snapshot.revision, memory)
        return result
    try:
        result = await run_in_threadpool(execute)
    except ConversationError as error:
        return conversation_error_response(request, error)
    except ConfirmationError as error:
        return confirmation_error_response(request, error.code, error.http_status)
    except Exception:
        return confirmation_error_response(request, 'confirmation_unavailable', 503)
    return JSONResponse(status_code=result.http_status, content=security_envelope(
        request, status=result.status, action=result.action, operation_id=None, mutation_state='not_applied',
        message=result.message, data=result.data,
        error={'code': result.error_code, 'retryable': False, 'fields': []} if result.error_code else None))

def register_confirmation_routes(router):
    @router.post('/agent/confirmations/preview', name='api_v1_confirmation_preview', tags=['agent'])
    async def preview(request: Request):
        return await _execute(request, 'preview')

    @router.post('/agent/confirmations/approve', name='api_v1_confirmation_approve', tags=['agent'])
    async def approve(request: Request):
        return await _execute(request, 'approve')

    @router.post('/agent/confirmations/status', name='api_v1_confirmation_status', tags=['agent'])
    async def status(request: Request):
        return await _execute(request, 'status')

    @router.post('/agent/confirmations/revoke', name='api_v1_confirmation_revoke', tags=['agent'])
    async def revoke(request: Request):
        return await _execute(request, 'revoke')
