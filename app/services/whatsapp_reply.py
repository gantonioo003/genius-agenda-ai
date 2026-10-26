"""Resposta fixa de teste, independente de IA e do transporte do provedor."""

from dataclasses import dataclass
import re
from uuid import UUID


TEST_REPLY = (
    "Recebi sua mensagem! A integração de teste do Genius Agenda AI está funcionando "
    "com Python, n8n e WhatsApp. A conversa com a agenda será a próxima etapa."
)


class ReplyUnavailable(RuntimeError):
    """Falha segura, sem dados de configuração ou resposta bruta do provedor."""

    def __init__(self, code='reply_unavailable', *, http_status=503, uncertain=False):
        super().__init__('Não foi possível confirmar a resposta de teste.')
        self.code = code
        self.http_status = http_status
        self.uncertain = uncertain


def canonical_claim_id(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


@dataclass(frozen=True)
class ReplyResult:
    reply_state: str
    reused: bool


class ReplyService:
    """Uma resposta por claim de mensagens novas; status nunca dispara envio."""

    def __init__(self, *, inbox, outbox, sender, recipient):
        if not isinstance(recipient, str) or re.fullmatch(r'[1-9][0-9]{6,14}', recipient, flags=re.ASCII) is None:
            raise ReplyUnavailable()
        self.inbox = inbox
        self.outbox = outbox
        self.sender = sender
        self._recipient = recipient

    def reply(self, request_id):
        if not canonical_claim_id(request_id):
            raise ReplyUnavailable('invalid_claim', http_status=422)
        try:
            claim = self.inbox.lookup_processing_claim(request_id)
        except Exception:
            raise ReplyUnavailable() from None
        if claim is None:
            raise ReplyUnavailable('claim_unavailable', http_status=409)
        if not any(event.event_type.startswith('message_') for event in claim.events):
            return ReplyResult('skipped', False)
        try:
            reservation = self.outbox.reserve(request_id)
        except Exception:
            raise ReplyUnavailable() from None
        if reservation.status == 'accepted':
            return ReplyResult('accepted', True)
        if reservation.status != 'new':
            raise ReplyUnavailable('reply_pending', http_status=409, uncertain=True)
        try:
            fingerprint = self.sender.send(self._recipient, TEST_REPLY)
            if not isinstance(fingerprint, str) or re.fullmatch(r'[0-9a-f]{64}', fingerprint, flags=re.ASCII) is None:
                raise ValueError()
            self.outbox.mark_accepted(reservation, fingerprint)
        except Exception:
            try:
                self.outbox.mark_uncertain(reservation)
            except Exception:
                # sending continua bloqueando novas tentativas se a gravação falhar.
                pass
            raise ReplyUnavailable('reply_uncertain', http_status=502, uncertain=True) from None
        return ReplyResult('accepted', False)


def default_reply_service(config):
    """Carrega os segredos apenas na rota autenticada explicitamente acionada."""
    from app.sandbox_webhook import load_allowed_sender
    from app.settings import PROJECT_ROOT
    from app.whatsapp_inbox import InboxStore
    from app.whatsapp_outbox import OutboxStore
    from app.whatsapp_sandbox_sender import SandboxSender
    try:
        if config.d360_api_key is None:
            raise ValueError()
        recipient = load_allowed_sender((PROJECT_ROOT / 'secrets/whatsapp-sandbox-security.json').read_bytes())
        return ReplyService(
            inbox=InboxStore(PROJECT_ROOT / 'data/whatsapp-inbox.sqlite3'),
            outbox=OutboxStore(PROJECT_ROOT / 'data/whatsapp-outbox.sqlite3'),
            sender=SandboxSender(config.d360_api_key), recipient=recipient,
        )
    except Exception:
        raise ReplyUnavailable() from None
