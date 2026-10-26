"""Ativar trabalho durável autorizado; nunca recebe texto nem destinatário do n8n."""
import asyncio
import json
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from app.api_security import security_envelope
from app.services.whatsapp_reply import canonical_claim_id
from app.settings import PROJECT_ROOT
from app.whatsapp_agent_store import AgentStore


def default_whatsapp_agent_store(config):
    path=config.whatsapp_agent_database_path
    return AgentStore(path if path.is_absolute() else PROJECT_ROOT/path)


def _response(request,*,state=None,reused=False,code=None,http_status=200,uncertain=False):
    return JSONResponse(status_code=http_status,content=security_envelope(request,
        status='succeeded' if state else 'failed',action='whatsapp_agent_turn',operation_id=None,
        mutation_state='not_applicable' if state else 'unknown' if uncertain else 'not_applied',
        message='Recebimento registrado para o agente.' if state=='queued' else 'Status recebido sem resposta.' if state else 'Não foi possível registrar o turno do agente.',
        data={'authenticated':True,'queue_state':state,'reused':reused} if state else None,
        error={'code':code or 'agent_queue_unavailable','retryable':False,'fields':[]} if not state else None))


def _unique(pairs):
    result={}
    for name,value in pairs:
        if name in result: raise ValueError()
        result[name]=value
    return result


def _invalid_constant(_value): raise ValueError()


def register_whatsapp_agent_route(router):
    @router.post('/whatsapp/sandbox/agent',name='api_v1_whatsapp_agent_turn',tags=['whatsapp'])
    async def activate_agent_turn(request:Request):
        try:
            types=request.headers.getlist('content-type')
            if request.scope.get('query_string') or len(types)!=1 or types[0].split(';',1)[0].strip().lower()!='application/json':
                raise ValueError()
            raw=bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(raw)+len(chunk)>1024: raise ValueError()
                    raw.extend(chunk)
            body=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,parse_constant=_invalid_constant)
            if (type(body) is not dict or set(body)!={'request_id'} or not canonical_claim_id(body['request_id'])
                    or body['request_id']!=request.state.request_id): raise ValueError()
        except Exception:
            return _response(request,code='invalid_request',http_status=422)
        if not request.app.state.settings.whatsapp_agent_enabled:
            return _response(request,code='whatsapp_agent_disabled',http_status=503)
        def activate():
            factory=request.app.state.whatsapp_agent_store_factory
            store=default_whatsapp_agent_store(request.app.state.settings) if factory is None else factory()
            claim=store.get_claim(body['request_id'])
            if claim.known:
                if 'uncertain' in claim.states:
                    return None,False,'agent_turn_uncertain'
                activated=store.activate(body['request_id'])
                if not activated.known or activated.count!=claim.count: raise ValueError()
                return 'queued',any(state!='staged' for state in claim.states),None
            from app.whatsapp_inbox import InboxStore
            inbox=InboxStore(PROJECT_ROOT/'data/whatsapp-inbox.sqlite3')
            injected=getattr(request.app.state,'whatsapp_agent_inbox_factory',None)
            if injected is not None: inbox=injected()
            status_claim=inbox.lookup_processing_claim(body['request_id'])
            if status_claim is None: return None,False,'agent_claim_unavailable'
            if any(event.event_type.startswith('message_') for event in status_claim.events):
                return None,False,'agent_claim_unavailable'
            return 'skipped',False,None
        try:
            state,reused,code=await run_in_threadpool(activate)
            if code:
                return _response(request,code=code,http_status=409,uncertain=code=='agent_turn_uncertain')
            return _response(request,state=state,reused=reused)
        except Exception:
            return _response(request,code='agent_queue_unavailable',http_status=503)
