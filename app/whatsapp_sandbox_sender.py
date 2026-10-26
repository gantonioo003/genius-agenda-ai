"""Envio único ao sandbox 360dialog, sem expor conteúdo ou ID do provedor."""

import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter


SANDBOX_MESSAGES_URL = "https://waba-sandbox.360dialog.io/v1/messages"
MAX_RESPONSE_BYTES = 64 * 1024
_PHONE = re.compile(r"[1-9][0-9]{6,14}\Z", re.ASCII)
_PROVIDER_ID = re.compile(r"[\x21-\x7e]{1,1024}\Z", re.ASCII)
_KEY = re.compile(r"[A-Za-z0-9_-]{16,256}\Z", re.ASCII)
_ID_NAMESPACE = "genius-agenda:360dialog:outbound-message:v1"


class SandboxSendError(Exception):
    """Erro sem dados privados; uncertain nunca autoriza uma retentativa."""

    def __init__(self, *, uncertain: bool, http_status: int | None = None):
        super().__init__("Não foi possível confirmar o envio de teste.")
        self.uncertain = bool(uncertain)
        # Apenas o código numérico; nunca copiar o corpo/headers do provedor.
        self.http_status = http_status if (
            isinstance(http_status, int) and not isinstance(http_status, bool)
            and 100 <= http_status <= 599
        ) else None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError()


def fingerprint_message_id(provider_id: str) -> str:
    """Pseudônimo estável; não torna o identificador anônimo."""
    if not isinstance(provider_id, str) or _PROVIDER_ID.fullmatch(provider_id) is None:
        raise ValueError("Identificador de envio inválido.")
    canonical = json.dumps([_ID_NAMESPACE, provider_id], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _public_image_link(link) -> bool:
    """Validação sintática sem baixar/resolver URLs; o provedor busca a imagem."""
    if (not isinstance(link, str) or not 1 <= len(link) <= 2048
            or any(character.isspace() or ord(character) < 33 or ord(character) == 127
                   for character in link)
            or "\\" in link or "#" in link
            or re.search(r"%(?![0-9a-fA-F]{2})", link)):
        return False
    try:
        # Rejeita Unicode inválido sem alterar a URL recebida.
        link.encode("utf-8")
        parsed = urlsplit(link)
        hostname = parsed.hostname
        if (parsed.scheme != "https" or not parsed.netloc or not hostname
                or not hostname.isascii() or parsed.username is not None
                or parsed.password is not None or parsed.fragment
                or parsed.port == 0):
            return False
        # A leitura de port também recusa portas inválidas/fora do intervalo.
        parsed.port
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.split(".")
            if (not 2 <= len(labels) or len(hostname) > 253
                    or labels[-1].isdigit()
                    or labels[-1].lower() in {
                        "localhost", "local", "localdomain", "internal", "lan", "home",
                        "test", "invalid", "onion",
                    }
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,61}[A-Za-z0-9]", labels[-1])
                    or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                               for label in labels)):
                return False
            return True
        return (address.is_global and not address.is_multicast
                and not getattr(address, "is_site_local", False))
    except (ValueError, UnicodeError):
        return False


class SandboxSender:
    def __init__(self, api_key, session_factory=requests.Session):
        try:
            value = api_key.get_secret_value() if callable(getattr(api_key, "get_secret_value", None)) else api_key
            if not isinstance(value, str) or _KEY.fullmatch(value) is None:
                raise ValueError()
        except Exception:
            raise SandboxSendError(uncertain=False) from None
        self._api_key = value
        self._session_factory = session_factory

    def send(self, recipient: str, text: str) -> str:
        if (not isinstance(recipient, str) or _PHONE.fullmatch(recipient) is None
                or not isinstance(text, str) or not 1 <= len(text) <= 4096):
            raise SandboxSendError(uncertain=False)
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual",
                           "to": recipient, "type": "text", "text": {"body": text}})

    def send_image(self, recipient: str, link: str, caption: str = "") -> str:
        """Imagem por URL pública HTTPS; o suporte real do sandbox é verificado à parte."""
        try:
            valid_caption = isinstance(caption, str) and len(caption.encode("utf-8")) <= 1024
        except UnicodeError:
            valid_caption = False
        if (not isinstance(recipient, str) or _PHONE.fullmatch(recipient) is None
                or not _public_image_link(link) or not valid_caption):
            raise SandboxSendError(uncertain=False)
        image = {"link": link}
        if caption:
            image["caption"] = caption
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual",
                           "to": recipient, "type": "image", "image": image})

    def send_template(self, recipient: str, name: str = "disclaimer", language: str = "en") -> str:
        """Único template permitido; nenhuma composição/parâmetro arbitrário."""
        if (not isinstance(recipient, str) or _PHONE.fullmatch(recipient) is None
                or not isinstance(name, str) or name != "disclaimer"
                or not isinstance(language, str) or language != "en"):
            raise SandboxSendError(uncertain=False)
        return self._post({"messaging_product": "whatsapp", "recipient_type": "individual",
                           "to": recipient, "type": "template", "template": {
                               "name": "disclaimer", "language": {"code": "en"}, "components": [],
                           }})

    def _post(self, payload: dict) -> str:
        """Fronteira privada comum aos três métodos estreitos de envio."""
        initiated = False
        try:
            with self._session_factory() as session:
                session.trust_env = False
                session.mount("https://", HTTPAdapter(max_retries=0))
                initiated = True
                with session.post(
                    SANDBOX_MESSAGES_URL,
                    headers={"D360-API-KEY": self._api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=(3, 8), allow_redirects=False, stream=True, verify=True,
                ) as response:
                    if response.status_code not in (200, 201):
                        raise SandboxSendError(uncertain=True, http_status=response.status_code)
                    raw = bytearray()
                    for chunk in response.iter_content(chunk_size=8192):
                        if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise SandboxSendError(uncertain=True)
                        raw.extend(chunk)
                    reply = json.loads(
                        raw.decode("utf-8"), object_pairs_hook=_unique_object,
                        parse_constant=_invalid_constant,
                    )
                    messages = reply.get("messages") if isinstance(reply, dict) else None
                    if (not isinstance(messages, list) or len(messages) != 1
                            or not isinstance(messages[0], dict)):
                        raise SandboxSendError(uncertain=True)
                    return fingerprint_message_id(messages[0].get("id"))
        except SandboxSendError:
            raise
        except Exception:
            raise SandboxSendError(uncertain=initiated) from None
