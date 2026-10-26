"""Autenticação e correlação da API v1, sem acesso ao provedor da agenda."""

from secrets import compare_digest
from typing import Annotated
from uuid import uuid4
import re

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer = HTTPBearer(auto_error=False, scheme_name="ApiKeyBearer")
_REQUEST_ID = re.compile(rb"[A-Za-z0-9._-]{1,128}")


class ApiSecurityError(Exception):
    """Erro seguro cujos valores são definidos pelo servidor, sem entrada bruta."""

    def __init__(self, status_code: int, code: str, message: str, *, fields=()):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = tuple(fields)


class RequestCorrelationMiddleware:
    """Acrescenta um ID seguro sem consumir o corpo ou alterar o roteamento."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = [value for key, value in scope.get("headers", ())
                    if key.lower() == b"x-request-id"]
        valid = len(supplied) == 1 and _REQUEST_ID.fullmatch(supplied[0]) is not None
        request_id = supplied[0].decode("ascii") if valid else str(uuid4())
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["invalid_request_id"] = bool(supplied) and not valid
        path = scope.get("path", "")
        private_response = path == "/api/v1" or path.startswith("/api/v1/")

        async def correlated_send(message):
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", ())
                           if key.lower() != b"x-request-id"
                           and not (private_response and key.lower() == b"cache-control")]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                if private_response:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, correlated_send)


def security_envelope(request: Request, *, status: str, mutation_state: str,
                      message: str, data=None, error=None, action: str | None = None,
                      operation_id: str | None = None) -> dict:
    return {
        "api_version": "1", "request_id": request.state.request_id,
        "operation_id": operation_id, "status": status, "action": action,
        "mutation_state": mutation_state, "message": message,
        "data": data, "error": error,
    }


async def api_security_error_handler(request: Request, error: ApiSecurityError):
    headers = {"Cache-Control": "no-store"}
    if error.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=error.status_code,
        headers=headers,
        content=security_envelope(
            request, status="failed", mutation_state="not_applied",
            message=error.message,
            error={"code": error.code, "retryable": False, "fields": list(error.fields)},
        ),
    )


async def require_api_key(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
) -> None:
    expected = request.app.state.settings.api_auth_token
    if expected is None:
        raise ApiSecurityError(
            503, "api_auth_unavailable", "O acesso à API ainda não está configurado.",
        )
    authorization = request.headers.getlist("authorization")
    # HTTPBearer documenta o esquema e rejeita mecanismos alternativos, mas
    # algumas versões aparam a credencial. A comparação usa o valor original.
    scheme, separator, supplied = authorization[0].partition(" ") if len(authorization) == 1 else ("", "", "")
    if (credentials is None or not separator or scheme.lower() != "bearer"
            or not compare_digest(supplied.encode("utf-8"), expected.get_secret_value().encode("utf-8"))):
        raise ApiSecurityError(401, "unauthorized", "Autenticação necessária para acessar a API.")
    if getattr(request.state, "invalid_request_id", False):
        raise ApiSecurityError(
            422, "invalid_request", "Informe um identificador de correlação válido.",
            fields=("X-Request-ID",),
        )


def create_api_router() -> APIRouter:
    """Futuras operações devem entrar neste router para herdar a proteção."""
    return APIRouter(prefix="/api/v1", dependencies=[Security(require_api_key)])
