"""Loopback-only browser access and CSRF protection, separate from service API keys."""

import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import math
import re
from secrets import compare_digest

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.panel_access import PanelRateLimitError, PanelStoreError, configured_panel_store


COOKIE_NAME = "genius_panel_session"
COOKIE_PATH = "/painel"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z", re.ASCII)
_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff"}


class PanelAccessError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def panel_access_error_handler(_request, error):
    headers = dict(_PRIVATE_HEADERS)
    if error.status_code == 429:
        headers["Retry-After"] = "60"
    return JSONResponse(status_code=error.status_code, headers=headers,
                        content={"status": "error", "message": error.message,
                                 "error": {"code": error.code, "message": error.message}})


def panel_boundary(request: Request, write=False):
    config = request.app.state.settings
    if not getattr(config, "panel_enabled", True):
        raise PanelAccessError(404, "panel_unavailable", "O painel não está disponível.")
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    forwarded = any(name == "forwarded" or name.startswith("x-forwarded-") for name in request.headers.keys())
    host_values = request.headers.getlist("host")
    scheme = request.scope.get("scheme", "")
    port = config.api_port
    allowed = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
    if (port == 80 and scheme == "http") or (port == 443 and scheme == "https"):
        allowed |= {"localhost", "127.0.0.1", "[::1]"}
    host = host_values[0].lower() if len(host_values) == 1 else ""
    if not local or forwarded or scheme not in {"http", "https"} or host not in allowed:
        raise PanelAccessError(403, "panel_local_only", "Abra o painel pelo endereço local deste computador.")
    origin = scheme + "://" + host
    sites = request.headers.getlist("sec-fetch-site")
    if len(sites) > 1 or sites and sites[0] not in {"same-origin", "none"}:
        raise PanelAccessError(403, "panel_origin_denied", "Esta origem não pode acessar o painel.")
    supplied = request.headers.getlist("origin")
    if len(supplied) > 1 or supplied and supplied[0] != origin or write and supplied != [origin]:
        raise PanelAccessError(403, "panel_origin_denied", "Esta origem não pode acessar o painel.")
    return origin


def _cookie(request):
    values = request.headers.getlist("cookie")
    if not values:
        return None
    if len(values) != 1 or len(values[0]) > 8192:
        return None
    occurrences = sum(part.strip().partition("=")[0] == COOKIE_NAME for part in values[0].split(";"))
    if occurrences != 1:
        return None
    sid = request.cookies.get(COOKIE_NAME)
    return sid if isinstance(sid, str) and _TOKEN.fullmatch(sid) else None


def _store(request):
    try:
        factory = getattr(request.app.state, "panel_access_store_factory", None)
        return factory() if factory is not None else configured_panel_store(request.app.state.settings)
    except Exception:
        raise PanelAccessError(503, "panel_access_unavailable", "O acesso ao painel está indisponível.") from None


async def _get_session(request, *, touch=True):
    sid = _cookie(request)
    if sid is None:
        return None
    try:
        return await run_in_threadpool(_store(request).get, sid, touch=touch)
    except PanelStoreError:
        raise PanelAccessError(503, "panel_access_unavailable", "O acesso ao painel está indisponível.") from None


async def require_panel_session(request: Request):
    write = request.method not in {"GET", "HEAD", "OPTIONS"}
    panel_boundary(request, write=write)
    session = await _get_session(request)
    if session is None or session.state != "active":
        raise PanelAccessError(401, "panel_auth_required", "Autorize esta aba para acessar sua agenda.")
    if write:
        supplied = request.headers.getlist("x-panel-csrf")
        if len(supplied) != 1 or not _TOKEN.fullmatch(supplied[0]) or not compare_digest(supplied[0], session.csrf_token):
            raise PanelAccessError(403, "panel_csrf_invalid", "Atualize o painel antes de continuar.")
    request.state.panel_session = session
    return session


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _public(session):
    if session is None:
        return {"authenticated": False, "pending": False}
    if session.state == "pending":
        return {"authenticated": False, "pending": True,
                "pairing_code": session.pairing_code, "expires_at": _iso(session.expires_at)}
    return {"authenticated": True, "pending": False,
            "expires_at": _iso(session.expires_at), "csrf_token": session.csrf_token}


def _reply(request, session=None, *, sid=None, clear=False):
    response = JSONResponse(_public(session), headers=_PRIVATE_HEADERS)
    secure = request.scope.get("scheme") == "https"
    if clear:
        response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True, secure=secure, samesite="strict")
    elif sid is not None and session is not None:
        now = _store(request)._now()
        age = max(0, math.ceil(session.absolute_expires_at - now))
        response.set_cookie(COOKIE_NAME, sid, max_age=age, path=COOKIE_PATH,
                            httponly=True, secure=secure, samesite="strict")
    return response


async def _empty_body(request):
    if request.scope.get("query_string"):
        raise PanelAccessError(422, "invalid_request", "Envie somente um objeto JSON vazio.")
    content_types = request.headers.getlist("content-type")
    if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
        raise PanelAccessError(422, "invalid_request", "Envie somente um objeto JSON vazio.")
    raw = bytearray()
    try:
        async with asyncio.timeout(2):
            async for part in request.stream():
                if len(raw) + len(part) > 1024:
                    raise ValueError()
                raw.extend(part)
        body = json.loads(raw.decode("utf-8"))
        if type(body) is not dict or body:
            raise ValueError()
    except Exception:
        raise PanelAccessError(422, "invalid_request", "Envie somente um objeto JSON vazio.") from None


def register_panel_auth_routes(router):
    @router.post("/auth/start", include_in_schema=False)
    async def start(request: Request):
        panel_boundary(request, write=True)
        await _empty_body(request)
        try:
            sid, session = await run_in_threadpool(_store(request).begin, _cookie(request))
            return _reply(request, session, sid=sid)
        except PanelRateLimitError:
            raise PanelAccessError(429, "panel_rate_limited", "Há muitas solicitações. Aguarde um minuto.") from None
        except PanelStoreError:
            raise PanelAccessError(503, "panel_access_unavailable", "O acesso ao painel está indisponível.") from None

    @router.get("/session", include_in_schema=False)
    async def session_status(request: Request):
        panel_boundary(request)
        if request.scope.get("query_string"):
            raise PanelAccessError(422, "invalid_request", "Esta consulta não aceita parâmetros.")
        session = await _get_session(request)
        try:
            return _reply(request, session, sid=_cookie(request) if session and session.state == "active" else None,
                          clear=_cookie(request) is not None and session is None)
        except PanelStoreError:
            raise PanelAccessError(503, "panel_access_unavailable", "O acesso ao painel está indisponível.") from None

    @router.post("/auth/logout", include_in_schema=False)
    async def logout(request: Request):
        await require_panel_session(request)
        await _empty_body(request)
        try:
            await run_in_threadpool(_store(request).revoke, _cookie(request))
        except PanelStoreError:
            raise PanelAccessError(503, "panel_access_unavailable", "O acesso ao painel está indisponível.") from None
        return _reply(request, clear=True)
