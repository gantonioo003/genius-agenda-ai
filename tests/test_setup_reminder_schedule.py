"""Setup totalmente simulado: sem Docker, rede, .env ou segredos reais."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
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
SPEC = importlib.util.spec_from_file_location("setup_reminder_schedule", ROOT / "scripts/setup_reminder_schedule.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)
API_ID = "bbbbbbbbbbbbbbbb"


class ReminderScheduleInstallerTests(unittest.TestCase):
    def config(self):
        return {"schema_version": 1, "workflow_id": "1111111111111111", "api_credential_id": API_ID}

    def workflow(self):
        return json.loads((ROOT / "n8n/workflows/lembretes-periodicos.json").read_text(encoding="utf-8"))

    def fixture(self, folder):
        root = Path(folder)
        (root / "secrets").mkdir()
        (root / "n8n/workflows").mkdir(parents=True)
        shutil.copyfile(ROOT / "n8n/workflows/lembretes-periodicos.json", root / "n8n/workflows/lembretes-periodicos.json")
        original = {"workflow_id": "aaaaaaaaaaaaaaaa", "api_credential_id": API_ID,
                    "webhook_credential_id": "cccccccccccccccc", "webhook_token": "ORIGINAL_PRIVATE"}
        old = root / "secrets/n8n-local.json"
        old.write_text(json.dumps(original), encoding="utf-8")
        sandbox = root / "secrets/whatsapp-sandbox-local.json"
        sandbox.write_text(json.dumps({"workflow_id": "dddddddddddddddd", "webhook_credential_id": "eeeeeeeeeeeeeeee",
                                      "callback_token": "CALLBACK_PRIVATE"}), encoding="utf-8")
        return root, {old: old.read_bytes(), sandbox: sandbox.read_bytes()}

    def test_explicit_apply_is_required_before_reading_files_or_running_commands(self):
        with patch.object(setup.sys, "argv", ["setup"]), patch.object(setup, "load_json") as load, \
                patch.object(setup.subprocess, "run") as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                setup.main()
            load.assert_not_called()
            run.assert_not_called()

    def test_versioned_config_rejects_extra_fields_invalid_ids_and_collisions(self):
        cases = [None, {}, {**self.config(), "token": "PRIVATE"}, {**self.config(), "schema_version": True},
                 {**self.config(), "schema_version": 2}, {**self.config(), "workflow_id": "PRIVATE"},
                 {**self.config(), "api_credential_id": "aaaaaaaaaaaaaaaa"},
                 {**self.config(), "workflow_id": API_ID}]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(RuntimeError) as raised:
                    setup.validate_config(case, API_ID, set())
                self.assertNotIn("PRIVATE", str(raised.exception))
        with self.assertRaises(RuntimeError):
            setup.validate_config(self.config(), API_ID, {self.config()["workflow_id"]})

    def test_own_config_reuses_identity_and_keeps_only_version_and_references(self):
        with TemporaryDirectory(dir=ROOT, prefix=".reminder-setup-test-") as folder:
            target = Path(folder) / "secrets/reminders-n8n-local.json"
            first = setup.own_config(target, API_ID, {"aaaaaaaaaaaaaaaa"})
            before = target.read_bytes()
            second = setup.own_config(target, API_ID, {"aaaaaaaaaaaaaaaa"})
            self.assertEqual(first, second)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(set(first), {"schema_version", "workflow_id", "api_credential_id"})

    def test_prepare_adds_only_new_workflow_id_and_existing_credential_reference(self):
        public = self.workflow()
        before = deepcopy(public)
        prepared = setup.prepare_workflow(public, self.config())
        self.assertEqual(public, before)
        self.assertEqual(prepared["id"], self.config()["workflow_id"])
        references = [node["credentials"] for node in prepared["nodes"] if "credentials" in node]
        self.assertEqual(references, [{"httpBearerAuth": {"name": setup.API_CREDENTIAL, "id": API_ID}}]*3)
        self.assertFalse(prepared["active"])
        self.assertNotIn("token", json.dumps(prepared))

    def test_prepare_rejects_other_exports_saved_execution_data_and_credentials(self):
        mutations = [lambda value: value.update(id="PRIVATE"), lambda value: value.update(active=True),
                     lambda value: value.update(staticData={}), lambda value: value.update(pinData={"private": "PRIVATE"}),
                     lambda value: value.update(name="Other workflow"),
                     lambda value: value["settings"].update(saveDataErrorExecution="all"),
                     lambda value: value["nodes"][0]["parameters"].update(rule={"interval": []}),
                     lambda value: value["nodes"][2]["credentials"]["httpBearerAuth"].update(id="PRIVATE"),
                     lambda value: value["nodes"][2]["parameters"].update(url="https://external.invalid"),
                     lambda value: value["nodes"][2]["parameters"].update(jsonBody='{"send":true}'),
                     lambda value: value["nodes"][2].update(retryOnFail=True),
                     lambda value: value["nodes"][2]["parameters"]["options"].update(timeout=1000)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                public = self.workflow()
                mutate(public)
                with self.assertRaises(RuntimeError) as raised:
                    setup.prepare_workflow(public, self.config())
                self.assertNotIn("PRIVATE", str(raised.exception))

    def test_dispatch_reference_body_destination_and_retry_are_also_validated(self):
        for change in ('destination','body','retry','credential','duplicate_tick'):
            public=self.workflow()
            http=next(node for node in public['nodes'] if node.get('parameters',{}).get('url','').endswith('/dispatch'))
            if change=='destination': http['parameters']['url']='https://other.invalid/dispatch'
            elif change=='body': http['parameters']['jsonBody']='{"recipient":"PRIVATE"}'
            elif change=='retry': http['retryOnFail']=True
            elif change=='credential': http['credentials']['httpBearerAuth']['id']='PRIVATE'
            else: http['parameters']['url']='http://host.docker.internal:8000/api/v1/reminders/tick'
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                setup.prepare_workflow(public,self.config())

    def test_recovery_cannot_receive_a_force_override_or_use_another_endpoint(self):
        for change in ('force','key','url','duplicate_tick','retry','credential'):
            public=self.workflow()
            node=next(node for node in public['nodes'] if node.get('parameters',{}).get('url','').endswith('/recover'))
            if change=='force': node['parameters']['jsonBody']='{"force":true}'
            elif change=='key': node['parameters']['jsonBody']='{"key":"PRIVATE"}'
            elif change=='url': node['parameters']['url']='https://external.invalid/recover'
            elif change=='duplicate_tick': node['parameters']['url']='http://host.docker.internal:8000/api/v1/reminders/tick'
            elif change=='retry': node['retryOnFail']=True
            else: node['credentials']['httpBearerAuth']['id']='PRIVATE'
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                setup.prepare_workflow(public,self.config())

    def test_prepare_rejects_bypass_or_duplicate_execution_connections(self):
        for change in ('skip_recovery','skip_recovery_validator','extra_dispatch','error_output','cycle','swap_roles'):
            value=self.workflow()
            if change=='skip_recovery':
                value['connections']['Preparar tick']['main'][0][0]['node']='Solicitar verificacao protegida'
            elif change=='skip_recovery_validator':
                value['connections']['Solicitar recuperacao local']['main'][0][0]['node']='Solicitar verificacao protegida'
            elif change=='extra_dispatch':
                value['connections']['Preparar tick']['main'][0].append({'node':'Solicitar disparo protegido','type':'main','index':0})
            elif change=='error_output':
                value['connections']['Solicitar recuperacao local']['main'][1][0]['node']='Solicitar disparo protegido'
            elif change=='cycle':
                value['connections']['Validar resumo da recuperacao']['main'][0][0]['node']='Solicitar recuperacao local'
            else:
                recovery=next(node for node in value['nodes'] if node['name']=='Solicitar recuperacao local')
                dispatch=next(node for node in value['nodes'] if node['name']=='Solicitar disparo protegido')
                recovery['parameters']['url'],dispatch['parameters']['url']=dispatch['parameters']['url'],recovery['parameters']['url']
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                setup.prepare_workflow(value,self.config())

    def test_prepare_rejects_changed_code_or_extra_code_parameters(self):
        for name in setup.APPROVED_CODE_SHA256:
            for change in ('code','parameters','continue_error','disabled','continue_on_fail','version'):
                value=self.workflow();node=next(node for node in value['nodes'] if node['name']==name)
                if change=='code':node['parameters']['jsCode']+='\nthrow new Error("unapproved");'
                elif change=='parameters':node['parameters']['mode']='unexpected'
                elif change=='continue_error':node['onError']='continueRegularOutput'
                elif change=='disabled':node['disabled']=True
                elif change=='continue_on_fail':node['continueOnFail']=True
                else:node['typeVersion']=1
                with self.subTest(name=name,change=change),self.assertRaises(RuntimeError):
                    setup.prepare_workflow(value,self.config())

    def run_mocked(self, root, *, metadata=None, version="2.44.4", error_import=False):
        calls = []
        self.last_calls = calls
        metadata = metadata if metadata is not None else {"workflow": None,
            "credential": {"name": setup.API_CREDENTIAL, "type": "httpBearerAuth"}}

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            output = ""
            if "--version" in command:
                output = version
            elif any("SELECT name FROM workflow_entity" in str(item) for item in command):
                output = json.dumps(metadata)
            elif "import:workflow" in command:
                output = "PRIVATE FAILED" if error_import else "Successfully imported 1 workflow."
            elif "publish:workflow" in command:
                output = "Publishing workflow with ID: fake"
            return SimpleNamespace(returncode=0, stdout=output)

        ready = MagicMock()
        ready.__enter__.return_value.status = 200
        output = io.StringIO()
        with patch.object(setup.sys, "argv", ["setup", "--apply", "--project-root", str(root)]), \
                patch.object(setup.shutil, "which", return_value="docker-fake"), \
                patch.object(setup.subprocess, "run", side_effect=fake_run), \
                patch.object(setup, "urlopen", return_value=ready), redirect_stdout(output):
            setup.main()
        return calls, output.getvalue()

    def test_apply_reuses_reference_without_importing_or_exporting_any_credential(self):
        with TemporaryDirectory(dir=ROOT, prefix=".reminder-setup-test-") as folder:
            root, before = self.fixture(folder)
            first_calls, output = self.run_mocked(root)
            config_file = root / "secrets/reminders-n8n-local.json"
            config_bytes = config_file.read_bytes()
            config = json.loads(config_bytes)
            second_calls, second_output = self.run_mocked(root, metadata={"workflow": {"name": setup.WORKFLOW_NAME},
                "credential": {"name": setup.API_CREDENTIAL, "type": "httpBearerAuth"}})
            self.assertEqual(config_file.read_bytes(), config_bytes)
            for old, content in before.items():
                self.assertEqual(old.read_bytes(), content)
            for calls in (first_calls, second_calls):
                self.assertEqual(sum("import:workflow" in command for command, _ in calls), 1)
                self.assertEqual(sum("publish:workflow" in command for command, _ in calls), 1)
                self.assertEqual(sum("restart" in command for command, _ in calls), 1)
                self.assertFalse(any("import:credentials" in command or "export:credentials" in command for command, _ in calls))
                payloads = [json.loads(kwargs["input"]) for _, kwargs in calls if kwargs.get("input")]
                self.assertEqual(len(payloads), 1)
                self.assertEqual(payloads[0]["id"], config["workflow_id"])
                self.assertNotIn("ORIGINAL_PRIVATE", json.dumps(payloads))
                self.assertNotIn("CALLBACK_PRIVATE", json.dumps(payloads))
                self.assertTrue(any("flag:'wx'" in str(command) and "mode:0o600" in str(command) for command, _ in calls))
                self.assertTrue(any("rmSync" in str(command) for command, _ in calls))
                for command, _ in calls:
                    self.assertNotIn("ORIGINAL_PRIVATE", str(command))
                    self.assertNotIn("CALLBACK_PRIVATE", str(command))
            self.assertNotIn(config["workflow_id"], output + second_output)
            self.assertNotIn(API_ID, output + second_output)

    def test_preflight_rejects_wrong_runtime_workflow_credential_or_version(self):
        cases = [({"workflow": {"name": "Other workflow"}, "credential": {"name": setup.API_CREDENTIAL, "type": "httpBearerAuth"}}, "2.44.4"),
                 ({"workflow": None, "credential": None}, "2.44.4"),
                 ({"workflow": None, "credential": {"name": "Other credential", "type": "httpBearerAuth"}}, "2.44.4"),
                 (None, "2.45.0")]
        for metadata, version in cases:
            with self.subTest(metadata=metadata, version=version), TemporaryDirectory(dir=ROOT, prefix=".reminder-setup-test-") as folder:
                root, before = self.fixture(folder)
                with self.assertRaises(RuntimeError):
                    self.run_mocked(root, metadata=metadata, version=version)
                self.assertFalse(any("import:workflow" in command for command, _ in self.last_calls))
                self.assertFalse(any("restart" in command for command, _ in self.last_calls))
                for old, content in before.items():
                    self.assertEqual(old.read_bytes(), content)

    def test_import_error_hides_native_output_and_still_removes_temporary_file(self):
        with TemporaryDirectory(dir=ROOT, prefix=".reminder-setup-test-") as folder:
            root, before = self.fixture(folder)
            with self.assertRaises(RuntimeError) as raised:
                self.run_mocked(root, error_import=True)
            self.assertNotIn("PRIVATE", str(raised.exception))
            self.assertTrue(any("rmSync" in str(command) for command, _ in self.last_calls))
            self.assertFalse(any("publish:workflow" in command for command, _ in self.last_calls))
            for old, content in before.items():
                self.assertEqual(old.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
