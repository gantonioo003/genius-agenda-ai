"""Comando interno autenticado para responder ao claim corrente do sandbox."""

import asyncio
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api_security import security_envelope
from app.services.whatsapp_reply import ReplyResult, ReplyUnavailable, canonical_claim_id, default_reply_service


def _response(request, *, http_status=200, state=None, reused=False, code=None, uncertain=False):
    return JSONResponse(status_code=http_status, content=security_envelope(
        request, status='succeeded' if state else 'failed', action='whatsapp_test_reply',
        mutation_state=('confirmed' if state == 'accepted' else 'not_applicable') if state else ('unknown' if uncertain else 'not_applied'),
        message=('Resposta de teste aceita pelo provedor.' if state == 'accepted' else 'Evento de status recebido; nenhuma resposta enviada.') if state else 'Não foi possível confirmar a resposta de teste.',
        data={'authenticated': True, 'reply_state': state, 'reused': reused} if state else None,
        error={'code': code or 'reply_unavailable', 'retryable': False, 'fields': []} if not state else None,
    ))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def register_reply_route(router):
    @router.post('/whatsapp/sandbox/reply', name='api_v1_whatsapp_sandbox_reply', tags=['whatsapp'])
    async def reply_to_claim(request: Request):
        # A dependência Bearer do router roda antes de consumir dados/configuração.
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
            body = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
            if (not isinstance(body, dict) or set(body) != {'request_id'}
                    or not canonical_claim_id(body['request_id'])
                    or body['request_id'] != request.state.request_id):
                raise ValueError()
        except Exception:
            return _response(request, http_status=422, code='invalid_request')

        def execute():
            factory = request.app.state.sandbox_reply_service_factory
            service = default_reply_service(request.app.state.settings) if factory is None else factory()
            result = service.reply(body['request_id'])
            if (not isinstance(result, ReplyResult) or result.reply_state not in ('accepted', 'skipped')
                    or not isinstance(result.reused, bool)):
                raise ReplyUnavailable()
            return result

        try:
            result = await run_in_threadpool(execute)
            return _response(request, state=result.reply_state, reused=result.reused)
        except ReplyUnavailable as error:
            return _response(request, http_status=error.http_status, code=error.code, uncertain=error.uncertain)
        except Exception:
            return _response(request, http_status=503, code='reply_unavailable')
