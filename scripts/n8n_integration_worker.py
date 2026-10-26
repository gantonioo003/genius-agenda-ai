"""Servidor isolado para conferir n8n -> API -> SDK/SQLite sem Google real.

Recebe um objeto de configuração JSON pela entrada padrão. Nunca lê tokens
Google, usa uma chave de API exclusiva do ensaio e mantém o banco em
``data/n8n-integration-runs/``. As rotas ``/__test`` existem somente neste
processo de teste, nunca na aplicação normal.
"""

from __future__ import annotations

from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from unittest.mock import patch


CONFIG_FIELDS = {"project_root", "port", "api_token", "database_path", "report_path"}
FAULT_FIELDS = {"reset", "lose_response_once", "fail_reads_after_write", "fail_next_read"}
MUTATIONS = {"POST", "PATCH", "DELETE"}


def read_config():
    value = json.load(sys.stdin)
    if not isinstance(value, dict) or set(value) != CONFIG_FIELDS:
        raise ValueError("Configuração do ensaio incompleta ou desconhecida.")
    if type(value["port"]) is not int or value["port"] != 8001:
        raise ValueError("O ensaio deve usar somente a porta local 8001.")
    key = value["api_token"]
    if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key, re.ASCII) is None:
        raise ValueError("A chave exclusiva do ensaio é inválida.")
    if any(not isinstance(value[field], str) or not value[field] for field in
           ("project_root", "database_path", "report_path")):
        raise ValueError("Informe os caminhos locais do ensaio.")
    root = Path(value["project_root"]).resolve(strict=True)
    if not (root / "app" / "main.py").is_file() or not (root / "tests" / "calendar_http_fake.py").is_file():
        raise ValueError("O diretório informado não contém a aplicação e o helper de teste.")
    permitted = (root / "data" / "n8n-integration-runs").resolve()
    database_path = Path(value["database_path"]).resolve()
    report_path = Path(value["report_path"]).resolve()
    if any(not candidate.is_relative_to(permitted) or candidate == permitted
           for candidate in (database_path, report_path)):
        raise ValueError("Banco e relatório devem permanecer na pasta isolada do ensaio.")
    if database_path == report_path or database_path == (root / "data" / "agenda.db").resolve():
        raise ValueError("Os arquivos do ensaio precisam de destinos separados e exclusivos.")
    # Um novo processo não pode reutilizar acidentalmente o estado de outro ensaio.
    if database_path.exists() or report_path.exists():
        raise ValueError("Banco e relatório do ensaio já existem; escolha uma nova pasta.")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    return root, key, database_path, report_path


class FixtureState:
    def __init__(self, backend, fake_response, report_path):
        self.backend = backend
        self.fake_response = fake_response
        self.report_path = report_path
        self.lock = threading.RLock()
        self.read_failures = 0
        self.scheduled_status_calls = []
        self.original_request = backend.request
        backend.request = self.request

    def request(self, method, url, data=None, headers=None, **kwargs):
        method = method.upper()
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        event_root = f"/calendar/v3/calendars/{self.backend.calendar['id']}/events"
        if self.read_failures and method == "GET" and (path == event_root or path.startswith(event_root + "/")):
            self.read_failures -= 1
            self.backend.calls.append({
                "method": method, "path": path, "status": 503,
                "event_id": path[len(event_root) + 1:] if path.startswith(event_root + "/") else None,
                "headers": {},
            })
            return self.fake_response(503, {"error": {"code": 503, "message": "Service Unavailable"}})
        return self.original_request(method, url, data=data, headers=headers, **kwargs)

    def configure_faults(self, body):
        if not isinstance(body, dict) or not body or not set(body).issubset(FAULT_FIELDS):
            raise ValueError("Plano de falha inválido.")
        if "reset" in body and type(body["reset"]) is not bool:
            raise ValueError("Plano de falha inválido.")
        if "fail_next_read" in body and type(body["fail_next_read"]) is not bool:
            raise ValueError("Plano de falha inválido.")
        lost = body.get("lose_response_once")
        if lost is not None and (not isinstance(lost, list) or any(
            not isinstance(method, str) or method not in MUTATIONS for method in lost
        ) or len(set(lost)) != len(lost)):
            raise ValueError("Plano de falha inválido.")
        fail_reads = body.get("fail_reads_after_write")
        if fail_reads is not None and (type(fail_reads) is not int or not 0 <= fail_reads <= 12):
            raise ValueError("Plano de falha inválido.")
        # Null não equivale a ausência em campos de um plano estrito.
        if any(body.get(field) is None for field in body):
            raise ValueError("Plano de falha inválido.")
        with self.lock:
            if body.get("reset"):
                self.backend.lose_response_once.clear()
                self.backend.fail_reads_after_write = 0
                self.read_failures = 0
            if "lose_response_once" in body:
                self.backend.lose_response_once = set(lost)
            if "fail_reads_after_write" in body:
                self.backend.fail_reads_after_write = fail_reads
            if "fail_next_read" in body:
                # O SDK pode tentar GET mais duas vezes. Falhamos uma consulta
                # lógica inteira, e a próxima consulta já pode se recuperar.
                self.read_failures = 3 if body["fail_next_read"] else 0
            self.persist()
        return {"ok": True, "fixture_only": True}

    def snapshot(self):
        with self.lock:
            calls = []
            for entry in self.backend.calls:
                normalized = {str(name).lower(): value for name, value in entry.get("headers", {}).items()}
                calls.append({
                    "method": entry["method"], "event_id": entry.get("event_id"),
                    "status": entry.get("status"), "if_match": normalized.get("if-match"),
                })
            events = []
            for event_id, event in sorted(self.backend.saved.items()):
                events.append({
                    "event_id": event_id, "title": event.get("summary"),
                    "start": event.get("start"), "end": event.get("end"),
                    "description": event.get("description"), "location": event.get("location"),
                    "status": event.get("status"), "etag": event.get("etag"),
                })
            writes = [entry for entry in calls if entry["method"] in MUTATIONS]
            return {
                "fixture_only": True, "google_http": "in_memory", "oauth_used": False,
                "google_calendar": "suite-calendar@example.invalid",
                "calls": calls, "saved_events": events,
                "counts": {"total": len(calls), "writes": len(writes),
                           "by_method": dict(Counter(entry["method"] for entry in calls)),
                           "by_status": dict(Counter(str(entry["status"]) for entry in calls))},
                "faults_remaining": {
                    "lose_response_once": sorted(self.backend.lose_response_once),
                    "fail_reads_after_write": self.backend.fail_reads_after_write,
                    "read_attempt_failures": self.read_failures,
                },
                "status_checks": list(self.scheduled_status_calls),
            }

    def persist(self):
        content = json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n"
        temporary = self.report_path.with_suffix(self.report_path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(self.report_path)


def main():
    root, key, db_path, report_path = read_config()
    sys.path[:0] = [str(root), str(root / "tests")]
    # Garante que os singletons importados também apontem para o ensaio.
    # Nenhum arquivo OAuth dessa pasta precisa existir ou é aberto.
    os.environ.update({
        "API_AUTH_TOKEN": key, "APP_ENV": "test", "DATABASE_PATH": str(db_path),
        "GOOGLE_CLIENT_FILE": str(db_path.parent / "unused-google-client.json"),
        "GOOGLE_TOKEN_FILE": str(db_path.parent / "unused-google-token.json"),
        "GOOGLE_CALENDAR_ID": "suite-calendar@example.invalid",
        "GOOGLE_CALENDAR_NAME": "Genius Testes",
    })
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from google.auth.credentials import AnonymousCredentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    import uvicorn

    from app import google_calendar
    from app.database import Database
    from app.google_transport import SingleAttemptHttp
    from app.main import create_app
    from app.settings import Settings
    from calendar_http_fake import CalendarHttpFake, FakeJsonResponse

    database = Database(db_path)
    backend = CalendarHttpFake()
    state = FixtureState(backend, FakeJsonResponse, report_path)

    @contextmanager
    def gateway_factory():
        with state.lock:
            transport = SingleAttemptHttp(timeout=5)
            authorized = AuthorizedHttp(AnonymousCredentials(), http=transport, max_refresh_attempts=0)
            service = build("calendar", "v3", http=authorized,
                            cache_discovery=False, static_discovery=True)
            try:
                yield google_calendar.GoogleCalendarGateway(service)
            finally:
                service.close()
                state.persist()

    application = create_app(
        config=Settings(_env_file=None, app_env="test", api_auth_token=key,
                        database_path=db_path),
        db=database, calendar_gateway_factory=gateway_factory,
    )
    # Request é importado localmente para não carregar dependências antes de
    # fixar a configuração. FastAPI resolve a anotação pelo módulo global.
    globals()["Request"] = Request

    def authorized(request):
        values = request.headers.getlist("authorization")
        return len(values) == 1 and hmac.compare_digest(
            values[0].encode("utf-8"), ("Bearer " + key).encode("ascii")
        )

    def test_reply(content, status_code=200):
        return JSONResponse(content, status_code=status_code,
                            headers={"Cache-Control": "no-store"})

    @application.post("/__test/control", include_in_schema=False)
    async def control(request: Request):
        if not authorized(request):
            return test_reply({"error": "unauthorized"}, 401)
        raw = await request.body()
        if len(raw) > 4096:
            return test_reply({"error": "invalid_fault_plan"}, 422)
        try:
            body = json.loads(raw)
            result = await run_in_threadpool(state.configure_faults, body)
        except (ValueError, UnicodeError, TypeError):
            return test_reply({"error": "invalid_fault_plan"}, 422)
        return test_reply(result)

    @application.get("/__test/snapshot", include_in_schema=False)
    async def snapshot(request: Request):
        if not authorized(request):
            return test_reply({"error": "unauthorized"}, 401)
        return test_reply(await run_in_threadpool(state.snapshot))

    @application.middleware("http")
    async def record_status_check(request: Request, call_next):
        response = await call_next(request)
        if request.method == "GET" and request.url.path == "/api/v1/status" and response.status_code == 200:
            def record():
                with state.lock:
                    state.scheduled_status_calls.append({
                        "at_utc": datetime.now(timezone.utc).isoformat(),
                        "request_id": response.headers.get("x-request-id"),
                        "status": 200,
                    })
                    state.persist()
            await run_in_threadpool(record)
        return response

    with ExitStack() as stack:
        stack.enter_context(patch("app.google_transport.requests.Session", side_effect=backend.session))
        stack.enter_context(patch.object(google_calendar, "database", database))
        stack.enter_context(patch.object(google_calendar, "settings", SimpleNamespace(
            google_calendar_name=backend.calendar["summary"],
            google_calendar_id=backend.calendar["id"],
        )))
        stack.enter_context(patch.object(google_calendar, "get_credentials", side_effect=AssertionError(
            "O ensaio isolado nunca pode consultar credenciais Google."
        )))
        stack.enter_context(patch("googleapiclient.http.time.sleep", return_value=None))
        state.persist()
        try:
            uvicorn.run(application, host="127.0.0.1", port=8001, workers=1,
                        access_log=False, log_level="warning")
        finally:
            state.persist()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, NotADirectoryError):
        print("Não foi possível preparar a configuração isolada do ensaio.", file=sys.stderr)
        raise SystemExit(2) from None
