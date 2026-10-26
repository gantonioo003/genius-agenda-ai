"""Servidor temporário de PNGs, sem diretório público; capacidades expiram."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading


def make_handler(image_path):
    path = Path(image_path)
    payload = path.read_bytes()
    if not payload.startswith(b'\x89PNG\r\n\x1a\n') or len(payload) > 5 * 1024 * 1024:
        raise ValueError('PNG indisponível ou fora do limite de 5 MB.')
    digest = hashlib.sha256(payload).hexdigest()
    route = '/cards/demo-' + digest + '.png'

    class CardHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _respond(self, body):
            if self.path != route:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
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

    return CardHandler, route


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--ttl', type=int, default=14400)
    parser.add_argument('--cache-root', type=Path, help='Cache privado de publicações com expiração')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.ttl <= 14400:
        parser.error('Porta ou duração inválida.')
    handler, route = make_handler(args.image)
    if args.cache_root is not None:
        from app.card_media_store import make_published_handler
        handler = make_published_handler(args.cache_root, fallback_handler=handler)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    timer = threading.Timer(args.ttl, server.shutdown)
    timer.daemon = True
    timer.start()
    print(route, flush=True)
    try:
        server.serve_forever()
    finally:
        timer.cancel()
        server.server_close()


if __name__ == '__main__':
    main()
