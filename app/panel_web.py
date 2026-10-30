"""Same-origin panel shell and a closed allowlist of local visual assets."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.panel_security import PanelAccessError, panel_boundary
from app.settings import PROJECT_ROOT


WEB_ROOT = PROJECT_ROOT / "web"
LIVE_FILES = {
    "live.js": ("live.js", "text/javascript"),
    "live.css": ("live.css", "text/css"),
    "styles.css": ("styles.css", "text/css"),
    "assets/mark.svg": ("assets/mark.svg", "image/svg+xml"),
    "assets/interview-background.png": ("assets/interview-background.png", "image/png"),
}
DEMO_FILES = {
    "app.js": ("app.js", "text/javascript"),
    "demo.js": ("demo.js", "text/javascript"),
    "demo.css": ("demo.css", "text/css"),
    "styles.css": ("styles.css", "text/css"),
    "assets/mark.svg": ("assets/mark.svg", "image/svg+xml"),
    "assets/interview-background.png": ("assets/interview-background.png", "image/png"),
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'"
)
DEMO_CSP = CSP.replace("connect-src 'self'", "connect-src 'none'")


class PanelHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (scope.get("path") == "/painel" or scope.get("path", "").startswith("/painel/")):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        content_policy = DEMO_CSP if path == "/painel/demo" or path.startswith("/painel/demo/") else CSP

        async def safe_send(message):
            if message["type"] == "http.response.start":
                names = {b"cache-control", b"content-security-policy", b"x-content-type-options", b"referrer-policy", b"x-frame-options", b"vary"}
                original = list(message.get("headers", []))
                headers = [(key, value) for key, value in original if key.lower() not in names]
                vary = {part.strip() for key, value in original if key.lower() == b"vary" for part in value.decode("latin-1").split(",") if part.strip()}
                vary.add("Cookie")
                headers.extend([
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", content_policy.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"vary", ", ".join(sorted(vary)).encode("latin-1")),
                ])
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, safe_send)


def _file(relative: str, media_type: str):
    root = WEB_ROOT.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise PanelAccessError(404, "panel_file_not_found", "Esta página não está disponível.")
    return FileResponse(target, media_type=media_type)


def register_panel_web_routes(application: FastAPI):
    @application.get("/painel", include_in_schema=False)
    async def canonical_panel(request: Request):
        panel_boundary(request)
        return RedirectResponse("/painel/", status_code=303)

    @application.get("/painel/", include_in_schema=False)
    async def panel_shell(request: Request):
        panel_boundary(request)
        return _file("live.html", "text/html")

    @application.get("/painel/demo", include_in_schema=False)
    async def canonical_demo(request: Request):
        panel_boundary(request)
        return RedirectResponse("/painel/demo/", status_code=303)

    @application.get("/painel/demo/", include_in_schema=False)
    async def demo_shell(request: Request):
        panel_boundary(request)
        return _file("index.html", "text/html")

    @application.get("/painel/demo/{asset:path}", include_in_schema=False)
    async def demo_asset(request: Request, asset: str):
        panel_boundary(request)
        if asset not in DEMO_FILES:
            raise PanelAccessError(404, "panel_file_not_found", "Esta página não está disponível.")
        return _file(*DEMO_FILES[asset])

    @application.get("/painel/{asset:path}", include_in_schema=False)
    async def live_asset(request: Request, asset: str):
        panel_boundary(request)
        if asset not in LIVE_FILES:
            raise PanelAccessError(404, "panel_file_not_found", "Esta página não está disponível.")
        return _file(*LIVE_FILES[asset])
