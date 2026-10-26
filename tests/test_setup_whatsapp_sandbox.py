"""Instalacao simulada: nenhum Docker, rede, .env ou segredo real e lido."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("setup_whatsapp_sandbox", ROOT / "scripts/setup_whatsapp_sandbox.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class InstallerTests(unittest.TestCase):
    def config(self):
        return {"workflow_id": "1111111111111111", "webhook_credential_id": "2222222222222222",
                "webhook_token": "a" * 64, "callback_token": "b" * 64}

    def test_invalid_or_colliding_config_fails_without_echoing_private_values(self):
        cases = [None, {}, {**self.config(), "private": "PRIVATE"},
                 {**self.config(), "workflow_id": "PRIVATE"},
                 {**self.config(), "webhook_credential_id": "1111111111111111"},
                 {**self.config(), "callback_token": "a" * 64},
                 {**self.config(), "webhook_token": "short"},
                 {**self.config(), "callback_token": "+" * 64}]
        for case in cases:
            with self.subTest(case_type=type(case).__name__):
                with self.assertRaises(RuntimeError) as raised:
                    setup.validate_config(case, set())
                self.assertNotIn("PRIVATE", str(raised.exception))
        with self.assertRaises(RuntimeError):
            setup.validate_config(self.config(), {"1111111111111111"})

    def test_own_config_reuses_reserved_identity_and_preserves_original(self):
        with TemporaryDirectory(dir=ROOT, prefix=".sandbox-test-") as folder:
            root = Path(folder)
            old = root / "n8n-local.json"
            old.write_text('{"workflow_id":"aaaaaaaaaaaaaaaa"}\n', encoding="utf-8")
            before = old.read_bytes()
            target = root / "whatsapp-sandbox-local.json"
            first = setup.own_config(target, {"aaaaaaaaaaaaaaaa"})
            second = setup.own_config(target, {"aaaaaaaaaaaaaaaa"})
            self.assertEqual(first, second)
            self.assertEqual(set(first), setup.CONFIG_KEYS)
            self.assertEqual(old.read_bytes(), before)
            self.assertGreaterEqual(len(first["callback_token"]), 48)

    def test_prepare_workflow_references_existing_api_without_importing_its_secret(self):
        workflow = json.loads((ROOT / "n8n/workflows/whatsapp-sandbox.json").read_text(encoding="utf-8"))
        prepared = setup.prepare_workflow(workflow, self.config(), "aaaaaaaaaaaaaaaa")
        self.assertEqual(prepared["id"], self.config()["workflow_id"])
        references = [node["credentials"] for node in prepared["nodes"] if "credentials" in node]
        self.assertEqual(references, [
            {"httpHeaderAuth": {"name": setup.HEADER_CREDENTIAL, "id": "2222222222222222"}},
            {"httpBearerAuth": {"name": setup.API_CREDENTIAL, "id": "aaaaaaaaaaaaaaaa"}}])
        self.assertNotIn("webhook_token", json.dumps(prepared))
        self.assertNotIn("callback_token", json.dumps(prepared))
        self.assertNotIn("a" * 64, json.dumps(prepared))

    def test_apply_is_separate_idempotent_and_only_imports_new_header_credential(self):
        with TemporaryDirectory(dir=ROOT, prefix=".sandbox-test-") as folder:
            root = Path(folder)
            (root / "secrets").mkdir()
            (root / "n8n/workflows").mkdir(parents=True)
            shutil.copyfile(ROOT / "n8n/workflows/whatsapp-sandbox.json", root / "n8n/workflows/whatsapp-sandbox.json")
            original = {"workflow_id": "aaaaaaaaaaaaaaaa", "api_credential_id": "bbbbbbbbbbbbbbbb",
                        "webhook_credential_id": "cccccccccccccccc", "webhook_token": "ORIGINAL_PRIVATE"}
            original_file = root / "secrets/n8n-local.json"
            original_file.write_text(json.dumps(original), encoding="utf-8")
            before = original_file.read_bytes()
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                stdout = "Successfully imported" if any(str(value).startswith("import:") for value in command) else ""
                if "publish:workflow" in command:
                    stdout = "Publishing workflow with ID: fake"
                return SimpleNamespace(returncode=0, stdout=stdout)

            readiness = MagicMock()
            readiness.__enter__.return_value.status = 200
            output = io.StringIO()
            with patch.object(setup.sys, "argv", ["setup", "--apply", "--project-root", str(root)]), \
                    patch.object(setup.shutil, "which", return_value="docker-fake"), \
                    patch.object(setup.subprocess, "run", side_effect=fake_run), \
                    patch.object(setup, "urlopen", return_value=readiness), redirect_stdout(output):
                setup.main()
                first = json.loads((root / "secrets/whatsapp-sandbox-local.json").read_text(encoding="utf-8"))
                setup.main()
                second = json.loads((root / "secrets/whatsapp-sandbox-local.json").read_text(encoding="utf-8"))
            self.assertEqual(first, second)
            self.assertEqual(original_file.read_bytes(), before)
            self.assertNotIn(first["workflow_id"], set(original.values()))
            self.assertNotIn(first["webhook_credential_id"], set(original.values()))
            payloads = [json.loads(options["input"]) for _, options in calls if options.get("input")]
            credentials = [payload for payload in payloads if isinstance(payload, list)]
            self.assertEqual(len(credentials), 2)
            for credential in credentials:
                self.assertEqual(len(credential), 1)
                self.assertEqual(credential[0]["type"], "httpHeaderAuth")
                self.assertEqual(credential[0]["id"], first["webhook_credential_id"])
                self.assertEqual(credential[0]["data"], {"name": setup.HEADER_NAME, "value": first["webhook_token"]})
            for command, _ in calls:
                if "publish:workflow" in command:
                    self.assertIn("--id=" + first["workflow_id"], command)
                    self.assertNotIn("--id=" + original["workflow_id"], command)
            created_paths = [command[-1] for command, _ in calls if any("mode:0o600" in str(arg) for arg in command)]
            removed_paths = [command[-1] for command, _ in calls if any("rmSync" in str(arg) for arg in command)]
            self.assertEqual(created_paths, removed_paths)
            self.assertEqual(len(created_paths), 4)
            for private in (original["webhook_token"], first["webhook_token"], first["callback_token"]):
                self.assertNotIn(private, output.getvalue())
                self.assertNotIn(private, json.dumps([command for command, _ in calls]))

    def test_import_error_still_removes_private_container_file(self):
        # Exercise the full installer with a failed credentials import, never real Docker.
        with TemporaryDirectory(dir=ROOT, prefix=".sandbox-test-") as folder:
            root = Path(folder)
            (root / "secrets").mkdir()
            (root / "n8n/workflows").mkdir(parents=True)
            shutil.copyfile(ROOT / "n8n/workflows/whatsapp-sandbox.json", root / "n8n/workflows/whatsapp-sandbox.json")
            (root / "secrets/n8n-local.json").write_text(json.dumps({"api_credential_id": "aaaaaaaaaaaaaaaa"}), encoding="utf-8")
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                return SimpleNamespace(returncode=1 if "import:credentials" in command else 0,
                                       stdout="", stderr="PRIVATE")

            with patch.object(setup.sys, "argv", ["setup", "--apply", "--project-root", str(root)]), \
                    patch.object(setup.shutil, "which", return_value="docker-fake"), \
                    patch.object(setup.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(RuntimeError) as raised:
                    setup.main()
            self.assertNotIn("PRIVATE", str(raised.exception))
            self.assertTrue(any("rmSync" in str(arg) for arg in calls[-1]))
            self.assertFalse(any("publish:workflow" in command for command in calls))


if __name__ == "__main__":
    unittest.main()
