"""HTTP local para exercitar o SDK real sem rede, OAuth ou agendas pessoais.

Este helper representa apenas eventos simples. Não expande recorrências nem
implementa projeção de ``fields`` ou o protocolo completo do Google. ETags
servem somente para simular concorrência e ``If-Match``. Os testes de domínio
e os testes específicos do adaptador continuam cobrindo os demais contratos.
"""

from copy import deepcopy
from datetime import datetime, time, timezone
from http import HTTPStatus
import json
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

import requests


class FakeJsonResponse:
    """Parte da resposta requests consumida por SingleAttemptHttp."""

    def __init__(self, status, body=None):
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.reason = HTTPStatus(status).phrase
        self.content = b"" if status == 204 else json.dumps(
            body, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        self.closed = False

    def close(self):
        self.closed = True


class FakeRequestsSession:
    """Sessão sem sockets: todas as chamadas terminam no backend local."""

    def __init__(self, backend):
        self.backend = backend
        self.closed = False
        self.mounted = {}

    def mount(self, prefix, adapter):
        self.mounted[prefix] = adapter

    def request(self, method, url, data=None, headers=None, **kwargs):
        if self.closed:
            raise AssertionError("A sessão HTTP falsa já foi fechada.")
        return self.backend.request(method, url, data=data, headers=headers, **kwargs)

    def close(self):
        self.closed = True


class CalendarHttpFake:
    """Pequeno estado remoto para cenários que atravessam SDK e transporte.

    ``calls`` registra também leituras e gravações que falham. Quando uma
    resposta se perde, a gravação permanece no estado remoto e seu status
    2xx permanece no histórico, mas a sessão recebe ``requests.Timeout``.
    ``fail_reads_after_write`` conta GETs de eventos que devolverão 503 depois
    da primeira gravação aplicada. Leituras da lista de agendas não consomem
    esse contador. ``before_write`` recebe método, ID e corpo antes do teste
    de If-Match; pode simular uma alteração externa entre leitura e gravação.
    """

    def __init__(self):
        self.calendar = {
            "id": "suite-calendar@example.invalid",
            "summary": "Genius Testes",
            "timeZone": "America/Recife",
            "accessRole": "owner",
            "primary": False,
        }
        self.saved = {}
        self.calls = []
        self.before_write = None
        self.lose_response_once = set()
        self.fail_reads_after_write = 0
        self.read_override = None
        self.page_size = None
        self.empty_first_page = False
        self._versions = {}
        self._has_written = False

    def session(self):
        return FakeRequestsSession(self)

    @staticmethod
    def _body(data):
        if data is None or data == b"" or data == "":
            return None
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise AssertionError("O helper espera um objeto JSON no corpo.")
        return deepcopy(data)

    @staticmethod
    def _error(status):
        return {"error": {"code": status, "message": HTTPStatus(status).phrase}}

    def _reply(self, call, status, body=None, *, applied=False):
        call["status"] = status
        if applied:
            self._has_written = True
            if call["method"] in self.lose_response_once:
                self.lose_response_once.remove(call["method"])
                raise requests.exceptions.Timeout("simulated lost response")
        return FakeJsonResponse(status, deepcopy(body))

    def _next_etag(self, event_id):
        version = self._versions.get(event_id, 0)
        current = self.saved.get(event_id, {}).get("etag", "")
        if isinstance(current, str) and current.startswith('"v') and current.endswith('"'):
            digits = current[2:-1]
            if digits.isdecimal():
                version = max(version, int(digits))
        version += 1
        self._versions[event_id] = version
        return f'"v{version}"'

    def _bounds(self, event):
        def instant(part):
            if "dateTime" in part:
                value = datetime.fromisoformat(part["dateTime"].replace("Z", "+00:00"))
                if value.tzinfo is None:
                    raise AssertionError("Fixtures temporais precisam de offset.")
            else:
                day = datetime.fromisoformat(part["date"]).date()
                zone = ZoneInfo(part.get("timeZone") or self.calendar["timeZone"])
                value = datetime.combine(day, time.min, tzinfo=zone)
            return value.astimezone(timezone.utc)
        return instant(event["start"]), instant(event["end"])

    def _list_events(self, query):
        minimum = query.get("timeMin")
        maximum = query.get("timeMax")
        minimum = datetime.fromisoformat(minimum.replace("Z", "+00:00")) if minimum else None
        maximum = datetime.fromisoformat(maximum.replace("Z", "+00:00")) if maximum else None
        selected = []
        for item in self.saved.values():
            if item.get("status") == "cancelled" and query.get("showDeleted", "false").lower() != "true":
                continue
            start, end = self._bounds(item)
            if minimum is not None and end <= minimum:
                continue
            if maximum is not None and start >= maximum:
                continue
            selected.append(item)
        selected.sort(key=lambda item: (self._bounds(item)[0], item["id"]))
        token = query.get("pageToken")
        if self.empty_first_page and not token:
            return {"items": [], "nextPageToken": "page-0"}
        if token:
            if not token.startswith("page-") or not token[5:].isdecimal():
                raise AssertionError("Token de página inválido no helper.")
            offset = int(token[5:])
        else:
            offset = 0
        if self.page_size is None:
            return {"items": deepcopy(selected[offset:])}
        if not isinstance(self.page_size, int) or isinstance(self.page_size, bool) or self.page_size < 1:
            raise AssertionError("page_size precisa ser um inteiro positivo.")
        page = {"items": deepcopy(selected[offset:offset + self.page_size])}
        next_offset = offset + self.page_size
        if next_offset < len(selected):
            page["nextPageToken"] = f"page-{next_offset}"
        return page

    def request(self, method, url, data=None, headers=None, **kwargs):
        method = method.upper()
        parsed = urlsplit(url)
        query = {
            key: values[0] if len(values) == 1 else values
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        path = unquote(parsed.path)
        body = self._body(data)
        call = {
            "method": method, "path": path, "query": query,
            "headers": dict(headers or {}), "body": deepcopy(body), "status": None,
            "event_id": None,
        }
        self.calls.append(call)
        prefix = "/calendar/v3/"
        if not path.startswith(prefix):
            raise AssertionError(f"Rota não implementada no helper: {method} {path}")
        route = path[len(prefix):]
        if route == "users/me/calendarList" and method == "GET":
            return self._reply(call, 200, {"items": [self.calendar]})
        event_root = f"calendars/{self.calendar['id']}/events"
        if route == event_root:
            if method == "GET":
                return self._reply(call, 200, self._list_events(query))
            if method == "POST":
                if body is None or not isinstance(body.get("id"), str):
                    raise AssertionError("A criação de teste exige ID explícito.")
                event_id = body["id"]
                call["event_id"] = event_id
                if self.before_write is not None:
                    self.before_write(method, event_id, deepcopy(body))
                if event_id in self.saved:
                    return self._reply(call, 409, self._error(409))
                item = {
                    "status": "confirmed", "eventType": "default",
                    "transparency": "opaque", "description": "", "location": "",
                    **deepcopy(body),
                    "htmlLink": f"https://example.invalid/event/{event_id}",
                    "etag": self._next_etag(event_id),
                }
                self.saved[event_id] = item
                return self._reply(call, 200, item, applied=True)
        elif route.startswith(event_root + "/"):
            event_id = route[len(event_root) + 1:]
            call["event_id"] = event_id
            if not event_id or "/" in event_id:
                raise AssertionError("ID de evento inválido na rota do helper.")
            if method == "GET":
                if self._has_written and self.fail_reads_after_write > 0:
                    self.fail_reads_after_write -= 1
                    return self._reply(call, 503, self._error(503))
                if self.read_override is not None:
                    overridden = self.read_override(event_id)
                    if overridden is not None:
                        return self._reply(call, 200, overridden)
                if event_id not in self.saved:
                    return self._reply(call, 404, self._error(404))
                return self._reply(call, 200, self.saved[event_id])
            if method in ("PATCH", "DELETE"):
                if self.before_write is not None:
                    self.before_write(method, event_id, deepcopy(body))
                if event_id not in self.saved:
                    return self._reply(call, 404, self._error(404))
                normalized_headers = {key.lower(): value for key, value in (headers or {}).items()}
                if normalized_headers.get("if-match") != self.saved[event_id]["etag"]:
                    return self._reply(call, 412, self._error(412))
                if method == "DELETE":
                    del self.saved[event_id]
                    return self._reply(call, 204, applied=True)
                if body is None:
                    raise AssertionError("O helper exige corpo JSON no PATCH.")
                etag = self._next_etag(event_id)
                self.saved[event_id].update(deepcopy(body))
                self.saved[event_id]["etag"] = etag
                return self._reply(call, 200, self.saved[event_id], applied=True)
        raise AssertionError(f"Rota não implementada no helper: {method} {path}")
