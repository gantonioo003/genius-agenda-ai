"""Ensaios explícitos e limitados de capacidades; não alteram o bot ativo."""
from dataclasses import dataclass
import hashlib
import json
import re
from uuid import UUID


IMAGE_URL = 'https://interactive-examples.mdn.mozilla.net/media/cc0-images/elephant-660-480.jpg'
IMAGE_CAPTION = 'Mídia de teste — Genius Agenda AI. Imagem pública para validar o envio; não é um card de entrevista.'
CASE_NAMES = {'image': 'image_link_v1', 'template': 'template_disclaimer_v1'}


class ProbeRunError(RuntimeError):
    def __init__(self, *, uncertain=False, http_status=None):
        super().__init__('Ensaio não confirmado; nenhum reenvio automático foi iniciado.')
        self.uncertain = bool(uncertain)
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None


@dataclass(frozen=True)
class ProbeResult:
    case_name: str
    state: str
    reused: bool


def _valid_operation_id(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def definition(kind):
    if kind == 'image':
        return {'case_name': CASE_NAMES[kind], 'type': 'image',
                'image': {'link': IMAGE_URL, 'caption': IMAGE_CAPTION}}
    if kind == 'template':
        return {'case_name': CASE_NAMES[kind], 'type': 'template',
                'template': {'name': 'disclaimer', 'language': {'code': 'en'}, 'components': []}}
    raise ProbeRunError()


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def preview(kind, operation_id):
    if not _valid_operation_id(operation_id):
        raise ProbeRunError()
    content = definition(kind)
    return {'operation_id': operation_id, 'case_name': content['case_name'],
            'definition': content, 'definition_fingerprint': fingerprint(content),
            'recipient': 'somente a politica privada ja autorizada',
            'network_requests': 0, 'execute': False}


def run_probe(kind, operation_id, *, recipient, sender, ledger):
    # Identidade canônica inclui destinatário em memória; só o SHA-256 é salvo.
    if (not _valid_operation_id(operation_id) or not isinstance(recipient, str)
            or re.fullmatch(r'[1-9][0-9]{6,14}', recipient, re.ASCII) is None):
        raise ProbeRunError()
    content = definition(kind)
    binding = fingerprint({'definition': content, 'recipient': recipient})
    try:
        reservation = ledger.reserve(operation_id, content['case_name'], binding)
    except Exception:
        raise ProbeRunError() from None
    if reservation.status == 'accepted':
        return ProbeResult(content['case_name'], 'accepted', True)
    if reservation.status != 'new':
        raise ProbeRunError(uncertain=True)
    try:
        if kind == 'image':
            message_id = sender.send_image(recipient, IMAGE_URL, IMAGE_CAPTION)
        else:
            message_id = sender.send_template(recipient, 'disclaimer', 'en')
        if not isinstance(message_id, str) or re.fullmatch(r'[0-9a-f]{64}', message_id, re.ASCII) is None:
            raise ValueError()
        ledger.mark_accepted(reservation, message_id)
    except Exception as error:
        try:
            ledger.mark_uncertain(reservation)
        except Exception:
            pass
        raise ProbeRunError(uncertain=True, http_status=getattr(error, 'http_status', None)) from None
    return ProbeResult(content['case_name'], 'accepted', False)
