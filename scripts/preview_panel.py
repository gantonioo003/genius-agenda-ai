"""Serve only the fictional panel prototype on loopback; no API integrations."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit


DEMO_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/demo.js": "demo.js",
    "/demo.css": "demo.css",
    "/styles.css": "styles.css",
    "/assets/mark.svg": "assets/mark.svg",
    "/assets/interview-background.png": "assets/interview-background.png",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'none'; "
    "object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'"
)


class PreviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def send_head(self):
        """Only the demo shell and its assets are readable, even on loopback."""
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}",
                 f"[::1]:{self.server.server_port}"}
        host_values = self.headers.get_all("Host", [])
        host = host_values[0].lower() if len(host_values) == 1 else ""
        origins = self.headers.get_all("Origin", [])
        sites = self.headers.get_all("Sec-Fetch-Site", [])
        forwarded = any(name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")
                        for name in self.headers.keys())
        try:
            local = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            local = False
        if (not local or host not in hosts or forwarded
                or len(origins) > 1 or origins and origins[0] != "http://" + host
                or len(sites) > 1 or sites and sites[0] not in {"same-origin", "none"}):
            self.send_error(403, "Open this demo through its local address.")
            return None
        try:
            request_target = urlsplit(self.path)
        except ValueError:
            self.send_error(404, "Not found")
            return None
        if request_target.scheme or request_target.netloc or request_target.fragment:
            self.send_error(404, "Not found")
            return None
        path = request_target.path
        relative = DEMO_FILES.get(path)
        if relative is None:
            self.send_error(404, "Not found")
            return None
        root = Path(self.directory).resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self.send_error(404, "Not found")
            return None
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404, "Not found")
        return None


def main():
    parser = argparse.ArgumentParser(description="Abrir a previa visual local do painel Genius.")
    parser.add_argument("--port", type=int, default=8015)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Escolha uma porta entre 1024 e 65535.")
    directory = Path(__file__).resolve().parents[1] / "web"
    if not (directory / "index.html").is_file():
        parser.error("A pasta web com o prototipo nao foi encontrada.")
    handler = partial(PreviewHandler, directory=str(directory))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError:
        parser.exit(1, f"Nao foi possivel abrir a porta {args.port}. Escolha outra com --port.\n")
    print(f"Genius - previa visual: http://127.0.0.1:{args.port}\nDados ficticios. Ctrl+C para encerrar.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
