"""Publicar recuperacao segura, planejamento e envio autorizado pela API local."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen


CONTAINER = "genius-agenda-ai-n8n"
WORKFLOW_NAME = "Genius Agenda AI - Verificação periódica de lembretes"
API_CREDENTIAL = "Genius API - local"
CONFIG_KEYS = {"schema_version", "workflow_id", "api_credential_id"}
ID = re.compile(r"[0-9a-f]{16}", re.ASCII)
WORKFLOW_ORDER = (
    'Verificar a cada minuto', 'Preparar tick', 'Solicitar recuperacao local',
    'Validar resumo da recuperacao', 'Solicitar verificacao protegida',
    'Validar resumo seguro', 'Solicitar disparo protegido', 'Validar resumo do disparo',
)
# Código público validado com as suítes; atualizar estes hashes ao regenerá-lo.
# Não são hashes de credenciais ou de dados de execução.
APPROVED_CODE_SHA256 = {
    'Preparar tick': '82b534bbe50ef956d96705cbfa98362a4a0e3eaf95a1bd65689a316944e0c9ed',
    'Validar resumo seguro': '9f162b8d4b470143efd8b881a8420be01b077dbf975dddd3f32cb1563340f698',
    'Validar resumo do disparo': 'd13623573bd496d977c347cefcef9e2b8406112c81f49b4797d140b9d0839450',
    'Validar resumo da recuperacao': '5d8e4d8a8a6f0257657c28a6d020d35381ccb0311924a12d99623097ab357dad',
}
HTTP_ROLES = {
    'Solicitar recuperacao local': 'http://host.docker.internal:8000/api/v1/reminders/recover',
    'Solicitar verificacao protegida': 'http://host.docker.internal:8000/api/v1/reminders/tick',
    'Solicitar disparo protegido': 'http://host.docker.internal:8000/api/v1/reminders/dispatch',
}


def load_json(path: Path, message: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError(message) from None
    if not isinstance(value, dict):
        raise RuntimeError(message)
    return value


def validate_config(value: object, api_id: str, reserved_ids: set[str]) -> dict:
    if (not isinstance(value, dict) or set(value) != CONFIG_KEYS
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or any(not isinstance(value.get(key), str) or not ID.fullmatch(value[key])
                   for key in ("workflow_id", "api_credential_id"))
            or value["api_credential_id"] != api_id or value["workflow_id"] in reserved_ids
            or value["workflow_id"] == api_id):
        raise RuntimeError("Configuracao privada do agendador invalida; preserve o arquivo e confira seu backup.")
    return value


def own_config(path: Path, api_id: str, reserved_ids: set[str]) -> dict:
    if path.exists():
        return validate_config(load_json(path, "Nao foi possivel ler a configuracao privada do agendador."), api_id, reserved_ids)
    value = {"schema_version": 1, "workflow_id": secrets.token_hex(8), "api_credential_id": api_id}
    validate_config(value, api_id, reserved_ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Reserva somente o novo ID antes da importacao; reaplicar reutiliza esse ID.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
    except FileExistsError:
        return validate_config(load_json(path, "Nao foi possivel ler a configuracao privada do agendador."), api_id, reserved_ids)
    return value


def prepare_workflow(value: dict, config: dict) -> dict:
    workflow = deepcopy(value)
    if (set(workflow) != {"name", "active", "nodes", "connections", "settings", "pinData", "tags"}
            or workflow.get("name") != WORKFLOW_NAME or workflow.get("active") is not False
            or workflow.get("pinData") != {} or workflow.get("tags") != []
            or not isinstance(workflow.get("nodes"), list) or len(workflow["nodes"]) != 8):
        raise RuntimeError("Use o export publico inativo de lembretes, sem IDs locais ou dados de execucao.")
    settings = workflow.get("settings", {})
    required_settings = {"executionOrder": "v1", "timezone": "America/Fortaleza",
                         "saveDataSuccessExecution": "none", "saveDataErrorExecution": "none",
                         "saveManualExecutions": False, "saveExecutionProgress": False,
                         "executionTimeout": 420, "callerPolicy": "none"}
    if settings != required_settings:
        raise RuntimeError("As configuracoes de privacidade/timeout do agendador sao inesperadas.")
    if (any(not isinstance(node, dict) or not isinstance(node.get('name'), str)
            or not isinstance(node.get('id'), str) or not node['id'] for node in workflow['nodes'])
            or {node['name'] for node in workflow['nodes']} != set(WORKFLOW_ORDER)
            or len({node['id'] for node in workflow['nodes']}) != 8):
        raise RuntimeError('Use o grafo publico completo de recuperacao, verificacao e disparo.')
    by_name = {node['name']: node for node in workflow['nodes']}
    base_fields = {'id', 'name', 'type', 'typeVersion', 'position', 'parameters'}
    for name, node in by_name.items():
        if name in HTTP_ROLES:
            fields = base_fields | {'credentials', 'retryOnFail', 'onError'}
            expected_type, version = 'n8n-nodes-base.httpRequest', 4.5
            if node.get('parameters', {}).get('url') != HTTP_ROLES[name]:
                raise RuntimeError('Cada chamada deve manter seu papel e destino local originais.')
        elif name in APPROVED_CODE_SHA256:
            fields = base_fields
            expected_type, version = 'n8n-nodes-base.code', 2
        else:
            fields = base_fields
            expected_type, version = 'n8n-nodes-base.scheduleTrigger', 1.4
        if (set(node) != fields or node.get('type') != expected_type
                or type(node.get('typeVersion')) not in (int, float)
                or node.get('typeVersion') != version):
            raise RuntimeError('Tipos, versoes e flags dos blocos devem preservar o grafo aprovado.')
    expected_connections = {}
    for index, name in enumerate(WORKFLOW_ORDER[:-1]):
        edge = [{'node': WORKFLOW_ORDER[index + 1], 'type': 'main', 'index': 0}]
        outputs = 2 if by_name[name].get('type') == 'n8n-nodes-base.httpRequest' else 1
        expected_connections[name] = {'main': [deepcopy(edge) for _ in range(outputs)]}
    if workflow.get('connections') != expected_connections:
        raise RuntimeError('As conexoes precisam preservar recuperacao, verificacao e disparo, sem desvios.')
    for name, expected in APPROVED_CODE_SHA256.items():
        node = by_name[name]
        parameters = node.get('parameters')
        if (node.get('type') != 'n8n-nodes-base.code' or type(parameters) is not dict
                or set(parameters) != {'jsCode'} or type(parameters['jsCode']) is not str
                or sha256(parameters['jsCode'].encode('utf-8')).hexdigest() != expected):
            raise RuntimeError('O codigo embutido nao corresponde aos validadores publicos aprovados.')
    found = []
    schedule = []
    for node in workflow["nodes"]:
        if not isinstance(node, dict):
            raise RuntimeError("O grafo publico do agendador e invalido.")
        if node.get("type") == "n8n-nodes-base.scheduleTrigger":
            schedule.append(node)
        elif node.get("type") not in {"n8n-nodes-base.code", "n8n-nodes-base.httpRequest"}:
            raise RuntimeError("O agendador tem um tipo de bloco inesperado.")
        for kind, reference in node.get("credentials", {}).items():
            if kind != "httpBearerAuth" or reference != {"name": API_CREDENTIAL}:
                raise RuntimeError("O export do agendador tem uma referencia de credencial inesperada.")
            reference["id"] = config["api_credential_id"]
            found.append(node)
    if (len(schedule) != 1 or schedule[0].get("parameters") != {
            "rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}}
            or len(found) != 3 or any(node.get('type') != 'n8n-nodes-base.httpRequest' for node in found)
            or {node.get('parameters', {}).get('url') for node in found} != {
                'http://host.docker.internal:8000/api/v1/reminders/recover',
                'http://host.docker.internal:8000/api/v1/reminders/tick',
                'http://host.docker.internal:8000/api/v1/reminders/dispatch'}):
        raise RuntimeError("O agendador exige um Schedule Trigger e tres chamadas locais autenticadas.")
    for http in found:
        parameters = http.get("parameters", {})
        if (parameters.get("url") not in {
                'http://host.docker.internal:8000/api/v1/reminders/recover',
                'http://host.docker.internal:8000/api/v1/reminders/tick',
                'http://host.docker.internal:8000/api/v1/reminders/dispatch'}
            or parameters.get("method") != "POST" or parameters.get("jsonBody") != "{}"
            or parameters.get("authentication") != "genericCredentialType"
            or parameters.get("genericAuthType") != "httpBearerAuth"
            or parameters.get("contentType") != "json" or parameters.get("specifyBody") != "json"
            or parameters.get("sendBody") is not True or parameters.get("sendHeaders") is not True
            or parameters.get("headerParameters") != {"parameters": [
                {"name": "Content-Type", "value": "application/json"},
                {"name": "X-Request-ID", "value": "={{ $json.request_id }}"}]}
            or parameters.get("options") != {"redirect": {"redirect": {"followRedirects": False}},
                "timeout": 120000, "response": {"response": {
                    "fullResponse": True, "neverError": True, "responseFormat": "json"}}}
            or http.get("retryOnFail") is not False or http.get("onError") != "continueErrorOutput"):
            raise RuntimeError("A chamada protegida do agendador tem uma configuracao inesperada.")
    workflow["id"] = config["workflow_id"]
    return workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Importar/publicar o agendador e reiniciar somente o n8n local")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if not args.apply:
        parser.error("Use --apply para instalar e publicar a verificacao periodica local.")
    root = args.project_root.resolve()
    original = load_json(root / "secrets/n8n-local.json", "Configure primeiro a integracao local existente do n8n.")
    api_id = original.get("api_credential_id")
    if not isinstance(api_id, str) or not ID.fullmatch(api_id):
        raise RuntimeError("A referencia privada da credencial da API nao e valida.")
    reserved = {value for key, value in original.items() if key.endswith("_id") and isinstance(value, str)}
    # Apenas IDs de workflows/credenciais sao usados; os arquivos anteriores nunca sao gravados.
    for relative in ("secrets/whatsapp-sandbox-local.json", "data/n8n-integration-fixtures.json"):
        previous = root / relative
        if previous.exists():
            value = load_json(previous, "Nao foi possivel conferir as referencias privadas anteriores.")
            reserved.update(item for key, item in value.items() if key.endswith("_id") and isinstance(item, str))
    docker = shutil.which("docker") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker/Docker/resources/bin/docker.exe")

    def run(*command: str, payload: str | None = None) -> str:
        result = subprocess.run([docker, *command], input=payload, text=True, encoding="utf-8",
                                capture_output=True, timeout=90)
        if result.returncode or "error occurred" in result.stdout.lower() or "failed" in result.stdout.lower():
            raise RuntimeError("O Docker/n8n recusou a operacao; confira os servicos locais.")
        return result.stdout

    run("exec", "-u", "node", CONTAINER, "node", "-e",
        "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>{if(!r.ok)process.exit(1)})")
    version = run("exec", "-u", "node", CONTAINER, "n8n", "--version")
    if not re.search(r"(?m)^2\.44\.4\s*$", version):
        raise RuntimeError("Este setup foi preparado para n8n 2.44.4; confira a versao local.")
    config = own_config(root / "secrets/reminders-n8n-local.json", api_id, reserved)
    workflow = prepare_workflow(load_json(root / "n8n/workflows/lembretes-periodicos.json",
        "Nao foi possivel ler o export publico do agendador."), config)
    metadata_script = """const {DatabaseSync}=require('node:sqlite');
const db=new DatabaseSync('/home/node/.n8n/database.sqlite',{readOnly:true});
const ids=JSON.parse(process.argv[1]);
const workflow=db.prepare('SELECT name FROM workflow_entity WHERE id=?').get(ids.workflow_id)||null;
const credential=db.prepare('SELECT name,type FROM credentials_entity WHERE id=?').get(ids.api_credential_id)||null;
db.close();console.log(JSON.stringify({workflow,credential}));"""
    try:
        metadata = json.loads(run("exec", "-u", "node", CONTAINER, "node", "-e", metadata_script,
                                  json.dumps({"workflow_id": config["workflow_id"], "api_credential_id": api_id})))
    except (ValueError, TypeError):
        raise RuntimeError("Nao foi possivel conferir as referencias existentes do n8n.") from None
    if (not isinstance(metadata, dict) or metadata.get("credential") != {"name": API_CREDENTIAL, "type": "httpBearerAuth"}
            or metadata.get("workflow") not in (None, {"name": WORKFLOW_NAME})):
        raise RuntimeError("O ID reservado ou a credencial existente nao corresponde ao agendador esperado.")

    path = f"/tmp/genius-reminders-import-{secrets.token_hex(8)}.json"
    try:
        run("exec", "-i", "-u", "node", CONTAINER, "node", "-e",
            "const fs=require('fs');fs.writeFileSync(process.argv[1],fs.readFileSync(0),{mode:0o600,flag:'wx'});",
            path, payload=json.dumps(workflow, ensure_ascii=False))
        output = run("exec", "-u", "node", CONTAINER, "n8n", "import:workflow", f"--input={path}")
        if "Successfully imported" not in output:
            raise RuntimeError("Importacao do agendador nao confirmada.")
    finally:
        run("exec", "-u", "node", CONTAINER, "node", "-e", "require('fs').rmSync(process.argv[1],{force:true});", path)
    output = run("exec", "-u", "node", CONTAINER, "n8n", "publish:workflow", f"--id={config['workflow_id']}")
    if "Publishing workflow with ID:" not in output:
        raise RuntimeError("Publicacao do agendador nao confirmada; confira a interface.")
    run("restart", CONTAINER)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:5678/healthz/readiness", timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(.5)
    else:
        raise RuntimeError("O n8n ainda nao esta pronto; confira os servicos locais.")
    print("Verificacao periodica importada e publicada; n8n reiniciado preservando o volume.")
    print("Configuracao privada: secrets/reminders-n8n-local.json; credencial existente reutilizada por referencia.")
    print("A cada 60 segundos, a API recupera somente estados seguros, planeja e processa os envios autorizados.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, RuntimeError) else "Configuracao interrompida; confira arquivos e Docker.", file=sys.stderr)
        sys.exit(1)
