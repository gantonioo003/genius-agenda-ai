"""Publicação breve de PNG por capacidade aleatória, sem listagem de arquivos."""
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import secrets
import requests
from urllib.parse import urlsplit

from app.whatsapp_sandbox_sender import _public_image_link

MAX_BYTES = 5 * 1024 * 1024
_ROUTE = re.compile(r'/cards/private-([0-9a-f]{64})\.png\Z', re.ASCII)


def _instant(value):
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError('Horário de publicação inválido.')
    return value.astimezone(timezone.utc)


class CardMediaPublisher:
    def __init__(self, cache_root, origin, ttl_seconds=600, verify_public=False):
        if (type(origin) is not str or type(ttl_seconds) is not int
                or not 1 <= ttl_seconds <= 600 or not _public_image_link(origin + '/cards/check.png')):
            raise ValueError('Publicação de mídia não configurada.')
        parsed = urlsplit(origin)
        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError('Origem de mídia inválida.')
        self.cache_root = Path(cache_root)
        self.origin = origin
        self.ttl_seconds = ttl_seconds
        self.verify_public = bool(verify_public)

    def publish(self, path, *, now):
        now = _instant(now)
        with Path(path).open('rb') as handle:
            payload = handle.read(MAX_BYTES + 1)
        if not payload.startswith(b'\x89PNG\r\n\x1a\n') or len(payload) > MAX_BYTES:
            raise ValueError('Arquivo de card inválido.')
        self.cache_root.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(32)
        output = self.cache_root / (token + '.png')
        metadata = self.cache_root / (token + '.json')
        value = {'created_at': now.isoformat(),
                 'expires_at': (now + timedelta(seconds=self.ttl_seconds)).isoformat(),
                 'sha256': hashlib.sha256(payload).hexdigest()}
        with output.open('xb') as handle:
            handle.write(payload)
        try:
            with metadata.open('x', encoding='utf-8') as handle:
                json.dump(value, handle)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        link = self.origin + '/cards/private-' + token + '.png'
        if self.verify_public:
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    with session.get(link, timeout=(2, 4), allow_redirects=False, stream=True) as response:
                        if response.status_code != 200 or response.headers.get('Content-Type', '').split(';')[0] != 'image/png':
                            raise ValueError('Mídia indisponível.')
                        verified = bytearray()
                        for chunk in response.iter_content(8192):
                            if len(verified) + len(chunk) > MAX_BYTES:
                                raise ValueError('Mídia inválida.')
                            verified.extend(chunk)
                        if hashlib.sha256(verified).hexdigest() != value['sha256']:
                            raise ValueError('Mídia divergente.')
            except Exception:
                metadata.unlink(missing_ok=True)
                output.unlink(missing_ok=True)
                raise ValueError('Mídia indisponível antes do envio.') from None
        return link


def make_published_handler(cache_root, *, fallback_handler=None, clock=None):
    root = Path(cache_root).resolve()
    base = fallback_handler or BaseHTTPRequestHandler
    clock = clock or (lambda: datetime.now(timezone.utc))

    class PublishedCardHandler(base):
        def log_message(self, *_args):
            pass

        def _not_found(self):
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def _respond(self, body):
            match = _ROUTE.fullmatch(self.path)
            if match is None:
                if fallback_handler is not None:
                    return super()._respond(body)
                return self._not_found()
            try:
                token = match.group(1)
                png, metadata = root / (token + '.png'), root / (token + '.json')
                if png.resolve().parent != root or metadata.resolve().parent != root:
                    raise ValueError()
                with metadata.open('rb') as handle:
                    raw = handle.read(8193)
                if len(raw) > 8192:
                    raise ValueError()
                record = json.loads(raw)
                created = _instant(datetime.fromisoformat(record['created_at']))
                expires = _instant(datetime.fromisoformat(record['expires_at']))
                now = _instant(clock())
                if not created <= now < expires or not timedelta(0) < expires-created <= timedelta(seconds=600):
                    raise ValueError()
                with png.open('rb') as handle:
                    payload = handle.read(MAX_BYTES + 1)
                if (len(payload) > MAX_BYTES or not payload.startswith(b'\x89PNG\r\n\x1a\n')
                        or hashlib.sha256(payload).hexdigest() != record['sha256']):
                    raise ValueError()
            except Exception:
                return self._not_found()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if body:
                self.wfile.write(payload)

        def do_GET(self):
            self._respond(True)

        def do_HEAD(self):
            self._respond(False)

    return PublishedCardHandler
