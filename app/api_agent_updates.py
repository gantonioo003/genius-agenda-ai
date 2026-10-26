"""Comando explícito para alterar o evento do rascunho completo, sem argumentos do modelo."""
import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_conversations import conversation_error_response, conversation_owner_key, conversation_store, validate_conversation_id
from app.api_security import security_envelope
from app.agent_update_store import AgentUpdateError, AgentUpdateStore
from app.confirmation_state import ConfirmationError
from app.conversation_store import ConversationError
from app.services.agent_updates import AgentUpdateService, validate_update_result
from app.settings import PROJECT_ROOT

def default_agent_update_store(config):
    path = config.agent_update_database_path
    return AgentUpdateStore(path if path.is_absolute() else PROJECT_ROOT / path)

def _error(request, *, action, code, http_status, unknown=False):
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status='failed', action=action, operation_id=None,
        mutation_state='unknown' if unknown else 'not_applied',
        message=('Não foi possível confirmar a alteração. Consulte o resultado antes de tentar novamente.'
                 if unknown else 'Não foi possível iniciar a alteração. Confira o pedido e seu estado.'),
        data={'event_id': None} if unknown else None,
        error={'code': code, 'retryable': False, 'fields': []}))

def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError()
        result[name] = value
    return result

def _invalid_constant(_value):
    raise ValueError()

def register_agent_update_routes(router):
    def register(action):
        @router.post('/agent/' + action, name='api_v1_agent_' + action, tags=['agent'])
        async def update_from_conversation(request: Request):
            try:
                content_types = request.headers.getlist('content-type')
                if (request.scope.get('query_string') or len(content_types) != 1
                        or content_types[0].split(';', 1)[0].strip().lower() != 'application/json'):
                    raise ValueError()
                raw = bytearray()
                async with asyncio.timeout(2):
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > 1024:
                            raise ValueError()
                        raw.extend(chunk)
                body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_invalid_constant)
                if type(body) is not dict or set(body) != {'conversation_id'}:
                    raise ValueError()
                conversation_id = validate_conversation_id(body['conversation_id'])
            except Exception:
                return _error(request, action=action, code='invalid_request', http_status=422)

            def execute():
                try:
                    context = conversation_store(request)
                    owner = conversation_owner_key(request.app.state.settings)
                    context.load(owner, conversation_id)
                except ConversationError:
                    raise
                except Exception:
                    raise ConversationError('conversation_unavailable') from None
                factory = request.app.state.agent_update_service_factory
                if factory is None:
                    store_factory = request.app.state.agent_update_store_factory
                    ledger = default_agent_update_store(request.app.state.settings) if store_factory is None else store_factory()
                    service = AgentUpdateService(context, ledger, request.app.state.calendar_gateway_factory)
                else:
                    service = factory()
                result = service.execute(owner, conversation_id, action)
                try:
                    if result.action != action:
                        raise ValueError()
                    return validate_update_result(result)
                except Exception:
                    # O serviço já pode ter escrito antes de devolver dados inválidos.
                    # Não apresentar essa falha como prova de que nada foi aplicado.
                    raise RuntimeError('Resultado da alteração não confirmado.') from None
            try:
                result = await run_in_threadpool(execute)
            except ConversationError as error:
                return conversation_error_response(request, error)
            except (AgentUpdateError, ConfirmationError) as error:
                return _error(request, action=action, code=error.code, http_status=error.http_status)
            except Exception:
                return _error(request, action=action, code='operation_not_confirmed', http_status=502, unknown=True)
            return JSONResponse(status_code=result.http_status, content=security_envelope(
                request, status=result.status, action=action, operation_id=result.operation_id,
                mutation_state=result.mutation_state, message=result.message, data=result.data,
                error={'code': result.error_code, 'retryable': False, 'fields': []} if result.error_code else None))
    register('edit')
    register('reschedule')
