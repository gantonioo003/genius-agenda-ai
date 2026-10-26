"""Worker local: uma passagem pelo agente e um envio reservado por mensagem."""
import re
import hashlib
import hmac
from threading import Event,Thread

from app.services.whatsapp_reply import ReplyUnavailable
from app.whatsapp_sandbox_sender import SandboxSendError

_PHONE=re.compile(r'[1-9][0-9]{6,14}\Z',re.ASCII)
_HASH=re.compile(r'[0-9a-f]{64}\Z',re.ASCII)

class AgentWorker:
    def __init__(self,*,store,outbox,service_factory,sender,recipient,expected_sender_key,poll_seconds=0.25):
        if (not callable(service_factory) or not isinstance(recipient,str) or not _PHONE.fullmatch(recipient)
                or not isinstance(expected_sender_key,str) or not _HASH.fullmatch(expected_sender_key)
                or not isinstance(poll_seconds,(int,float)) or isinstance(poll_seconds,bool) or not 0.01<=poll_seconds<=5):
            raise ReplyUnavailable('agent_worker_unavailable')
        self.store,self.outbox,self.service_factory,self.sender=store,outbox,service_factory,sender
        self._recipient,self._poll=recipient,poll_seconds
        self._expected_sender_key=expected_sender_key
        self._stop=Event()
        self._thread=None
    def __repr__(self): return 'AgentWorker(<private>)'
    def run_once(self):
        from app.services.whatsapp_agent import validate_agent_turn
        job=self.store.claim_next()
        if job is None: return 'idle'
        try:
            if not hmac.compare_digest(job.sender_key,self._expected_sender_key):
                self.store.uncertain(job)
                return 'uncertain'
            if job.state=='processing':
                session=self.store.get_session(job.sender_key)
                turn=validate_agent_turn(self.service_factory().process(job.text,
                    conversation_id=session.conversation_id if session else None,
                    authorization_action=session.authorization_action if session else None,
                    event_type=job.event_type))
                self.store.set_session(job.sender_key,turn.conversation_id,turn.authorization_action)
                self.store.prepare(job,turn.reply)
                return 'prepared'
            if job.state!='delivering' or not isinstance(job.reply,str) or not job.reply.strip(): raise ValueError()
            # A resposta já está persistida e é imutável antes de reservar o envio.
            reservation=self.outbox.reserve(job.job_id)
            if reservation.status=='accepted':
                self.store.complete(job,reservation.message_id_fingerprint)
                return 'sent'
            if reservation.status!='new':
                self.store.uncertain(job)
                return 'uncertain'
            try:
                fingerprint=self.sender.send(self._recipient,job.reply)
                if not isinstance(fingerprint,str) or _HASH.fullmatch(fingerprint) is None: raise ValueError()
                self.outbox.mark_accepted(reservation,fingerprint)
            except SandboxSendError as error:
                try: self.outbox.mark_uncertain(reservation)
                except Exception: pass
                if not error.uncertain:
                    # Uma prévia que o provedor recusou não pode permanecer
                    # aprovável por um "sim" posterior. A revogação precisa
                    # persistir antes de liberar o próximo turno do remetente.
                    session=self.store.get_session(job.sender_key)
                    if session is None: raise ValueError()
                    self.service_factory().discard_unseen_confirmation(session.conversation_id)
                    self.store.set_session(job.sender_key,session.conversation_id,None)
                    self.store.reject(job)
                    return 'rejected'
                raise
            except Exception:
                try: self.outbox.mark_uncertain(reservation)
                except Exception: pass
                raise
            self.store.complete(job,fingerprint)
            return 'sent'
        except Exception:
            try: self.store.uncertain(job)
            except Exception: pass
            return 'uncertain'
    def _run(self):
        while not self._stop.is_set():
            try: result=self.run_once()
            except Exception: result='idle'
            if result=='idle': self._stop.wait(self._poll)
    def start(self):
        if self._thread is not None: raise RuntimeError('O worker já foi iniciado.')
        self.store.initialize()
        self.outbox.initialize()
        self._thread=Thread(target=self._run,name='genius-whatsapp-agent',daemon=True)
        self._thread.start()
    def stop(self):
        self._stop.set()
        if self._thread is not None:
            # Não abandona uma chamada em execução para iniciá-la em outro thread.
            self._thread.join(timeout=45)


def default_whatsapp_agent_service(application):
    from app.preparation_chat_factory import default_preparation_chat_service
    from app.api_conversations import default_conversation_store,conversation_owner_key
    from app.api_agent_creation import default_agent_creation_store
    from app.api_agent_updates import default_agent_update_store
    from app.api_agent_deletion import default_agent_deletion_store
    from app.services.agent_creation import AgentCreationService
    from app.services.agent_updates import AgentUpdateService
    from app.services.agent_deletion import AgentDeletionService
    from app.services.agent_dialogue import default_agent_dialogue_service
    from app.services.agent_queries import default_agent_query_service
    from app.services.confirmations import ConfirmationService
    from app.services.whatsapp_agent import WhatsAppAgentService
    state=application.state
    config=state.settings
    context=default_conversation_store(config) if state.conversation_store_factory is None else state.conversation_store_factory()
    gateway=state.calendar_gateway_factory
    return WhatsAppAgentService(context,conversation_owner_key(config),
        dialogue_factory=state.agent_dialogue_service_factory or (lambda:default_agent_dialogue_service(config,gateway)),
        confirmation_factory=state.confirmation_service_factory or (lambda:ConfirmationService(gateway)),
        creation_factory=state.agent_creation_service_factory or (lambda:AgentCreationService(context,
            default_agent_creation_store(config) if state.agent_creation_store_factory is None else state.agent_creation_store_factory(),gateway)),
        update_factory=state.agent_update_service_factory or (lambda:AgentUpdateService(context,
            default_agent_update_store(config) if state.agent_update_store_factory is None else state.agent_update_store_factory(),gateway)),
        deletion_factory=state.agent_deletion_service_factory or (lambda:AgentDeletionService(context,
            default_agent_deletion_store(config) if state.agent_deletion_store_factory is None else state.agent_deletion_store_factory(),gateway)),
        query_factory=state.agent_query_service_factory or (lambda:default_agent_query_service(config,gateway)),gateway_factory=gateway,
        preparation_factory=lambda: default_preparation_chat_service(application))


def default_whatsapp_agent_worker(application):
    from app.api_whatsapp_agent import default_whatsapp_agent_store
    from app.sandbox_webhook import load_allowed_sender, _config_values, _decode_json
    from app.settings import PROJECT_ROOT
    from app.whatsapp_outbox import OutboxStore
    from app.whatsapp_sandbox_sender import SandboxSender
    config=application.state.settings
    if config.d360_api_key is None: raise ReplyUnavailable('agent_worker_unavailable')
    recipient=load_allowed_sender((PROJECT_ROOT/'secrets/whatsapp-sandbox-security.json').read_bytes())
    callback_token,_=_config_values(_decode_json((PROJECT_ROOT/'secrets/whatsapp-sandbox-local.json').read_bytes()))
    expected_sender_key=hmac.new(callback_token.encode('ascii'),b'genius-whatsapp-agent-sender:v1\0'+recipient.encode('ascii'),hashlib.sha256).hexdigest()
    factory=application.state.whatsapp_agent_store_factory
    store=default_whatsapp_agent_store(config) if factory is None else factory()
    path=config.whatsapp_agent_outbox_path
    outbox=OutboxStore(path if path.is_absolute() else PROJECT_ROOT/path)
    return AgentWorker(store=store,outbox=outbox,
        service_factory=application.state.whatsapp_agent_service_factory or (lambda:default_whatsapp_agent_service(application)),
        sender=SandboxSender(config.d360_api_key),recipient=recipient,expected_sender_key=expected_sender_key)
