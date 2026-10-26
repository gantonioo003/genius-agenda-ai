"""Segurança HTTP atravessando a aplicação ASGI, sem cliente de rede."""

import asyncio
from contextlib import ExitStack
from io import StringIO
import json
import logging
import unittest
from unittest.mock import patch
from uuid import UUID

from pydantic import SecretStr, ValidationError

from app.api_security import create_api_router
from app.main import create_app
from app.settings import Settings


TOKEN = "ApiTestToken_OnlyForTests_0123456789AbCdEf"
WRONG_TOKEN = "WrongApiToken_OnlyForTests_0123456789AbCd"


class InjectedDatabase:
    """Contador local; qualquer acesso real ao banco seria um erro de teste."""

    def __init__(self):
        self.initializations = 0

    def initialize(self):
        self.initializations += 1


async def invoke_asgi(app, path, *, method="GET", headers=(), body=b"", query=b""):
    """Executar HTTP ASGI em memória e preservar headers duplicados na entrada."""
    messages = []
    request_delivered = False
    response_finished = asyncio.Event()
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "scheme": "http", "method": method,
        "root_path": "", "path": path, "raw_path": path.encode("ascii"),
        "query_string": query, "headers": list(headers),
        "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
    }

    async def receive():
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await response_finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_finished.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    starts = [item for item in messages if item["type"] == "http.response.start"]
    if len(starts) != 1:
        raise AssertionError("A resposta ASGI precisa ter um único início.")
    start = starts[0]
    content = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    response_headers = {name.decode("latin-1").lower(): value.decode("latin-1")
                        for name, value in start.get("headers", [])}
    return start["status"], response_headers, json.loads(content)


class ApiSecurityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.config = self.settings(TOKEN)
        self.db = InjectedDatabase()
        self.app = create_app(config=self.config, db=self.db)
        self.logs = StringIO()
        self.log_handler = logging.StreamHandler(self.logs)
        logger = logging.getLogger()
        previous_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(self.log_handler)
        self.stack.callback(logger.setLevel, previous_level)
        self.stack.callback(logger.removeHandler, self.log_handler)
        self.addCleanup(self.check_logs)

    def settings(self, token):
        return Settings(
            _env_file=None, api_auth_token=token,
            app_name="Agenda API de teste", app_env="test",
        )

    def check_logs(self):
        output = self.logs.getvalue()
        self.assertNotIn(TOKEN, output)
        self.assertNotIn(WRONG_TOKEN, output)

    @staticmethod
    def authorization(value=TOKEN, scheme="Bearer"):
        return (b"authorization", f"{scheme} {value}".encode("latin-1"))

    def request(self, path="/api/v1/status", *, app=None, **options):
        response = asyncio.run(invoke_asgi(app or self.app, path, **options))
        encoded = json.dumps(response, ensure_ascii=False)
        self.assertNotIn(TOKEN, encoded)
        self.assertNotIn(WRONG_TOKEN, encoded)
        return response

    def assert_error(self, response, expected_http, code, *, fields=()):
        http_status, headers, payload = response
        self.assertEqual(http_status, expected_http)
        self.assertEqual(set(payload), {
            "api_version", "request_id", "operation_id", "status", "action",
            "mutation_state", "message", "data", "error",
        })
        self.assertEqual(payload["api_version"], "1")
        self.assertEqual(payload["request_id"], headers["x-request-id"])
        self.assertIsNone(payload["operation_id"])
        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["action"])
        self.assertEqual(payload["mutation_state"], "not_applied")
        self.assertIsNone(payload["data"])
        self.assertIsInstance(payload["message"], str)
        self.assertTrue(payload["message"])
        self.assertEqual(payload["error"], {"code": code, "retryable": False, "fields": list(fields)})
        self.assertEqual(headers["cache-control"], "no-store")
        if expected_http == 401:
            self.assertEqual(headers["www-authenticate"], "Bearer")
        else:
            self.assertNotIn("www-authenticate", headers)

    def test_health_remains_public_with_and_without_api_configuration(self):
        for token in (TOKEN, None):
            with self.subTest(configured=token is not None):
                app = create_app(config=self.settings(token), db=InjectedDatabase())
                response = self.request("/health", app=app)
                self.assertEqual(response[0], 200)
                self.assertEqual(response[2], {
                    "status": "ok", "service": "Agenda API de teste", "environment": "test",
                })
        self.assertEqual(self.db.initializations, 0)

    def test_missing_configuration_fails_closed_even_with_a_present_credential(self):
        for absent in (None, ""):
            with self.subTest(configuration=absent):
                app = create_app(config=self.settings(absent), db=InjectedDatabase())
                for headers in ((), (self.authorization(),)):
                    self.assert_error(self.request(app=app, headers=headers), 503, "api_auth_unavailable")

    def test_missing_incorrect_empty_basic_unicode_or_modified_bearer_is_rejected(self):
        invalid = (
            (), ((b"authorization", b""),), ((b"authorization", b"Bearer"),),
            ((b"authorization", b"Bearer "),), (self.authorization(WRONG_TOKEN),),
            (self.authorization(scheme="Basic"),),
            ((b"authorization", b"Bearer \xff" + b"A" * 40),),
            ((b"authorization", b"Bearer\t" + TOKEN.encode("ascii")),),
            (self.authorization(TOKEN + " "),), (self.authorization(" " + TOKEN),),
        )
        for headers in invalid:
            with self.subTest(header=headers[0][1][:12] if headers else "missing"):
                self.assert_error(self.request(headers=headers), 401, "unauthorized")

    def test_exact_credential_succeeds_with_case_insensitive_bearer_scheme(self):
        for scheme in ("Bearer", "bearer", "bEaReR"):
            with self.subTest(scheme=scheme):
                status, headers, payload = self.request(headers=(self.authorization(scheme=scheme),))
                self.assertEqual(status, 200)
                self.assertEqual(payload, {
                    "api_version": "1", "request_id": headers["x-request-id"],
                    "operation_id": None, "status": "succeeded", "action": None,
                    "mutation_state": "not_applicable", "message": "Acesso autorizado.",
                    "data": {"authenticated": True, "service": "Agenda API de teste"},
                    "error": None,
                })
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertNotIn("www-authenticate", headers)
        self.assertEqual(self.db.initializations, 0)

    def test_duplicate_authorization_is_rejected_instead_of_selecting_one_value(self):
        for duplicated in (self.authorization(), self.authorization(WRONG_TOKEN)):
            with self.subTest(same_value=duplicated == self.authorization()):
                response = self.request(headers=(self.authorization(), duplicated))
                self.assert_error(response, 401, "unauthorized")

    def test_credentials_in_query_or_body_do_not_authenticate(self):
        query = f"api_auth_token={TOKEN}&api_key={TOKEN}".encode("ascii")
        body = json.dumps({"token": TOKEN, "Authorization": f"Bearer {TOKEN}"}).encode("utf-8")
        for options in ({"query": query}, {"body": body, "headers": ((b"content-type", b"application/json"),)}):
            with self.subTest(source="query" if "query" in options else "body"):
                self.assert_error(self.request(**options), 401, "unauthorized")

    def test_valid_request_id_is_echoed_and_missing_header_generates_a_uuid(self):
        for request_id in ("workflow-001.try_2", "A", "B" * 128):
            with self.subTest(request_id_length=len(request_id)):
                response = self.request(headers=(self.authorization(), (b"x-request-id", request_id.encode("ascii"))))
                self.assertEqual(response[0], 200)
                self.assertEqual(response[1]["x-request-id"], request_id)
                self.assertEqual(response[2]["request_id"], request_id)
        missing = self.request(headers=(self.authorization(),))
        generated = missing[1]["x-request-id"]
        self.assertEqual(str(UUID(generated)), generated)
        self.assertEqual(missing[2]["request_id"], generated)

    def test_invalid_or_duplicate_request_id_uses_safe_uuid_and_is_rejected_after_authentication(self):
        malformed = (
            ((b"x-request-id", b""),), ((b"x-request-id", b"INVALID PRIVATE VALUE"),),
            ((b"x-request-id", b"X" * 129),), ((b"x-request-id", b"\xff"),),
            ((b"x-request-id", b"private\r\nInjected: value"),),
            ((b"x-request-id", b"same-id"), (b"x-request-id", b"same-id")),
        )
        for supplied in malformed:
            with self.subTest(header=supplied[0][1][:12]):
                response = self.request(headers=(self.authorization(), *supplied))
                self.assert_error(response, 422, "invalid_request", fields=("X-Request-ID",))
                safe_id = response[1]["x-request-id"]
                self.assertEqual(str(UUID(safe_id)), safe_id)
                for _, raw_value in supplied:
                    self.assertNotEqual(safe_id, raw_value.decode("latin-1"))
                self.assertNotIn("INVALID PRIVATE VALUE", json.dumps(response))
        invalid_with_bad_auth = self.request(headers=((b"x-request-id", b"INVALID PRIVATE VALUE"),))
        self.assert_error(invalid_with_bad_auth, 401, "unauthorized")
        public_health = self.request("/health", headers=((b"x-request-id", b"INVALID PRIVATE VALUE"),))
        self.assertEqual(public_health[0], 200)
        self.assertEqual(str(UUID(public_health[1]["x-request-id"])), public_health[1]["x-request-id"])

    def test_openapi_declares_bearer_for_status_without_exposing_the_key(self):
        status, _, schema = self.request("/openapi.json")
        self.assertEqual(status, 200)
        security = schema["components"]["securitySchemes"]["ApiKeyBearer"]
        self.assertEqual(security["type"], "http")
        self.assertEqual(security["scheme"], "bearer")
        self.assertIn({"ApiKeyBearer": []}, schema["paths"]["/api/v1/status"]["get"]["security"])
        self.assertFalse(schema["paths"]["/health"]["get"].get("security"))
        self.assertNotIn("api_auth_token", json.dumps(schema))

    def test_future_router_route_inherits_the_same_dependency(self):
        router = create_api_router()

        @router.get("/proof")
        async def proof():
            return {"guarded": True}

        self.app.include_router(router)
        self.assert_error(self.request("/api/v1/proof"), 401, "unauthorized")
        accepted = self.request("/api/v1/proof", headers=(self.authorization(),))
        self.assertEqual(accepted[0], 200)
        self.assertEqual(accepted[2], {"guarded": True})
        schema = self.app.openapi()
        self.assertIn({"ApiKeyBearer": []}, schema["paths"]["/api/v1/proof"]["get"]["security"])

    def test_settings_hide_valid_and_invalid_secret_values(self):
        self.assertIsInstance(self.config.api_auth_token, SecretStr)
        self.assertEqual(self.config.api_auth_token.get_secret_value(), TOKEN)
        for rendered in (str(self.config), repr(self.config), repr(self.config.model_dump()),
                         json.dumps(self.config.model_dump(mode="json")), self.config.model_dump_json()):
            self.assertNotIn(TOKEN, rendered)
        self.assertNotIn("api_auth_token", self.config.model_dump())
        self.assertIsNone(self.settings("").api_auth_token)
        self.assertIsNone(self.settings(None).api_auth_token)
        for valid in ("A" * 32, "B" * 128, SecretStr(TOKEN)):
            with self.subTest(valid_type=type(valid).__name__):
                self.assertIsInstance(self.settings(valid).api_auth_token, SecretStr)
        invalid_tokens = (
            "PRIVATE_SHORT_TOKEN", "PRIVATE_OVERSIZED_" + "A" * 129,
            " " + TOKEN, TOKEN + " ", "PRIVATE_INVALID_" + "ñ" * 32,
            "PRIVATE_INVALID_" + ":" * 32,
        )
        for invalid in invalid_tokens:
            with self.subTest(invalid_kind=invalid[:16]):
                with self.assertRaises(ValidationError) as failure:
                    self.settings(invalid)
                self.assertNotIn(invalid, str(failure.exception))
                self.assertNotIn(invalid, repr(failure.exception))
                self.assertNotIn(invalid, failure.exception.json(include_input=False))

    def test_lifespan_initializes_only_the_injected_database(self):
        async def run_lifespan():
            async with self.app.router.lifespan_context(self.app):
                self.assertEqual(self.db.initializations, 1)
                response = await invoke_asgi(self.app, "/health")
                self.assertEqual(response[0], 200)
            self.assertEqual(self.db.initializations, 1)

        with patch("app.main.database.initialize", side_effect=AssertionError("Acesso ao banco real proibido.")):
            asyncio.run(run_lifespan())

    def test_unrecognized_paths_and_methods_keep_routing_statuses(self):
        self.assertEqual(self.request("/api/v1/not-a-route")[0], 404)
        self.assertEqual(self.request(method="POST")[0], 405)


if __name__ == "__main__":
    unittest.main()
