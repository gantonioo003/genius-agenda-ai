"""Envio reservado de card, com fallback somente após falha comprovadamente segura."""

from dataclasses import dataclass
import hashlib
import json
import re

from app.whatsapp_probe_ledger import ProbeLedger
from app.whatsapp_sandbox_sender import SandboxSendError, SandboxSender


_CASE = 'interview_card.v1'
_PHONE = re.compile(r'[1-9][0-9]{6,14}\Z', re.ASCII)
_HASH = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)


@dataclass(frozen=True)
class CardDeliveryResult:
    state: str
    channel: str | None
    reused: bool


class CardDeliveryError(ValueError):
    def __init__(self):
        super().__init__('Definição de envio do card inválida.')


def _binding(recipient, image_link, text):
    if (not isinstance(recipient, str) or _PHONE.fullmatch(recipient) is None
            or not isinstance(text, str) or not text.strip() or len(text) > 4096
            or image_link is not None and (not isinstance(image_link, str) or not 1 <= len(image_link) <= 2048)):
        raise CardDeliveryError()
    try:
        # Somente o hash entra no ledger; a definição exata não pode ser trocada.
        raw = json.dumps(['genius-interview-card:v1', recipient, image_link, text],
                         ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()
    except (ValueError, UnicodeError, TypeError):
        raise CardDeliveryError() from None


def _uncertain(ledger, reservation):
    try:
        ledger.mark_uncertain(reservation)
    except Exception:
        # A reserva durável em sending também bloqueia uma segunda tentativa.
        pass


def send_interview_card(operation_id, *, recipient, image_link: str | None, text: str,
                        sender: SandboxSender, ledger: ProbeLedger) -> CardDeliveryResult:
    """Uma reserva por definição; resultado incerto nunca inicia outro envio.

    O ledger existente não persiste o canal efetivo de um fallback. Por isso
    channel=None em reuso: accepted comprova aceite, sem inventar o canal anterior.
    """
    fingerprint = _binding(recipient, image_link, text)
    reservation = ledger.reserve(operation_id, _CASE, fingerprint)
    if reservation.status == 'accepted':
        return CardDeliveryResult('accepted', None, True)
    if reservation.status == 'blocked':
        return CardDeliveryResult('uncertain', None, True)
    if reservation.status != 'new':
        raise CardDeliveryError()

    channel = 'image' if image_link is not None else 'text'
    try:
        if image_link is not None:
            try:
                accepted = sender.send_image(recipient, image_link, caption=text)
            except SandboxSendError as error:
                if error.uncertain:
                    _uncertain(ledger, reservation)
                    return CardDeliveryResult('uncertain', 'image', False)
                # Recusa comprovada antes/na tentativa: uma única saída em texto.
                channel = 'text'
                accepted = sender.send(recipient, text)
        else:
            # Render/hosting falhou antes da tentativa: não chama send_image.
            accepted = sender.send(recipient, text)
    except SandboxSendError as error:
        _uncertain(ledger, reservation)
        return CardDeliveryResult('uncertain' if error.uncertain else 'failed', channel, False)
    except Exception:
        # Exceção arbitrária não comprova recusa e jamais autoriza fallback.
        _uncertain(ledger, reservation)
        return CardDeliveryResult('uncertain', channel, False)

    if not isinstance(accepted, str) or _HASH.fullmatch(accepted) is None:
        _uncertain(ledger, reservation)
        return CardDeliveryResult('uncertain', channel, False)
    try:
        ledger.mark_accepted(reservation, accepted)
    except Exception:
        _uncertain(ledger, reservation)
        return CardDeliveryResult('uncertain', channel, False)
    return CardDeliveryResult('accepted', channel, False)
