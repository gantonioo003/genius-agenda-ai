"""Instalar apenas a ponte de recebimento do sandbox; sem enviar mensagens."""
from __future__ import annotations

import argparse
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
CONFIG_KEYS = {"workflow_id", "webhook_credential_id", "webhook_token", "callback_token"}
ID = re.compile(r"[0-9a-f]{16}", re.ASCII)
TOKEN = re.compile(r"[A-Za-z0-9_-]{48,128}", re.ASCII)
HEADER_NAME = "X-Genius-Webhook-Key"
HEADER_CREDENTIAL = "Genius WhatsApp Sandbox - entrada"
API_CREDENTIAL = "Genius API - local"


def load_json(path: Path, message: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError(message) from None
    if not isinstance(value, dict):
        raise RuntimeError(message)
    return value


def validate_config(value: object, reserved_ids: set[str]) -> dict:
    if (not isinstance(value, dict) or set(value) - {'agent_enabled'} != CONFIG_KEYS
            or 'agent_enabled' in value and type(value['agent_enabled']) is not bool
            or any(not isinstance(value.get(key), str) or not ID.fullmatch(value[key])
                   for key in ("workflow_id", "webhook_credential_id"))
            or any(not isinstance(value.get(key), str) or not TOKEN.fullmatch(value[key])
                   for key in ("webhook_token", "callback_token"))
            or value["workflow_id"] == value["webhook_credential_id"]
            or value["webhook_token"] == value["callback_token"]
            or {value["workflow_id"], value["webhook_credential_id"]} & reserved_ids):
        raise RuntimeError("Configuracao privada do sandbox invalida; preserve o arquivo e confira seu backup.")
    return value


def own_config(path: Path, reserved_ids: set[str]) -> dict:
    if path.exists():
        return validate_config(load_json(path, "Nao foi possivel ler a configuracao privada do sandbox."), reserved_ids)
    value = {
        "workflow_id": secrets.token_hex(8), "webhook_credential_id": secrets.token_hex(8),
        "webhook_token": secrets.token_urlsafe(48), "callback_token": secrets.token_urlsafe(48),
    }
    validate_config(value, reserved_ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Reserva antes da importacao; repetir --apply reutiliza somente estes IDs.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
    except FileExistsError:
        return validate_config(load_json(path, "Nao foi possivel ler a configuracao privada do sandbox."), reserved_ids)
    return value


def prepare_workflow(workflow: dict, config: dict, api_id: str) -> dict:
    if "id" in workflow or workflow.get("active") is not False:
        raise RuntimeError("Use o export publico inativo do sandbox, sem ID local.")
    references = {"httpHeaderAuth": (config["webhook_credential_id"], HEADER_CREDENTIAL),
                  "httpBearerAuth": (api_id, API_CREDENTIAL)}
    found = []
    for node in workflow.get("nodes", []):
        for kind, reference in node.get("credentials", {}).items():
            if kind not in references or set(reference) != {"name"} or reference["name"] != references[kind][1]:
                raise RuntimeError("O export do sandbox tem uma referencia de credencial inesperada.")
            reference["id"] = references[kind][0]
            found.append(kind)
    if sorted(found) != sorted(references):
        raise RuntimeError("O export do sandbox precisa das duas credenciais esperadas.")
    workflow["id"] = config["workflow_id"]
    return workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Importar/publicar a ponte e reiniciar somente o n8n local")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if not args.apply:
        parser.error("Use --apply para instalar a ponte local do sandbox.")
    root = args.project_root.resolve()
    original = load_json(root / "secrets/n8n-local.json", "Configure primeiro a integracao local existente do n8n.")
    api_id = original.get("api_credential_id")
    if not isinstance(api_id, str) or not ID.fullmatch(api_id):
        raise RuntimeError("A referencia privada da credencial da API nao e valida.")
    reserved = {value for key, value in original.items()
                if key.endswith("_id") and isinstance(value, str)}
    docker = shutil.which("docker") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker/Docker/resources/bin/docker.exe")

    def run(*command: str, payload: str | None = None) -> str:
        result = subprocess.run([docker, *command], input=payload, text=True, encoding="utf-8",
                                capture_output=True, timeout=90)
        if result.returncode or "error occurred" in result.stdout.lower() or "failed" in result.stdout.lower():
            # Nao revelar stderr, stdout nem payloads que possam conter credenciais.
            raise RuntimeError("O Docker/n8n recusou a operacao; confira os servicos locais.")
        return result.stdout

    run("exec", "-u", "node", CONTAINER, "node", "-e",
        "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>{if(!r.ok)process.exit(1)})")
    config = own_config(root / "secrets/whatsapp-sandbox-local.json", reserved)
    workflow = prepare_workflow(load_json(root / "n8n/workflows/whatsapp-sandbox.json",
                                          "Nao foi possivel ler o export publico do sandbox."), config, api_id)
    credential = [{"id": config["webhook_credential_id"], "name": HEADER_CREDENTIAL,
                   "type": "httpHeaderAuth", "data": {"name": HEADER_NAME, "value": config["webhook_token"]}}]

    def import_json(kind: str, value: object) -> None:
        path = f"/tmp/genius-sandbox-import-{secrets.token_hex(8)}.json"
        try:
            run("exec", "-i", "-u", "node", CONTAINER, "node", "-e",
                "const fs=require('fs');fs.writeFileSync(process.argv[1],fs.readFileSync(0),{mode:0o600,flag:'wx'});",
                path, payload=json.dumps(value, ensure_ascii=False))
            output = run("exec", "-u", "node", CONTAINER, "n8n", f"import:{kind}", f"--input={path}")
            if "Successfully imported" not in output:
                raise RuntimeError("Importacao do sandbox nao confirmada.")
        finally:
            run("exec", "-u", "node", CONTAINER, "node", "-e",
                "require('fs').rmSync(process.argv[1],{force:true});", path)

    import_json("credentials", credential)
    import_json("workflow", workflow)
    output = run("exec", "-u", "node", CONTAINER, "n8n", "publish:workflow", f"--id={config['workflow_id']}")
    if "Publishing workflow with ID:" not in output:
        raise RuntimeError("Publicacao do sandbox nao confirmada; confira a interface.")
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
    print("Ponte do sandbox importada e publicada; n8n reiniciado preservando o volume.")
    print("Destino privado: POST http://127.0.0.1:5678/webhook/genius-agenda/whatsapp-sandbox")
    print("Configuracao privada preservada em secrets/whatsapp-sandbox-local.json; nenhum segredo foi exibido.")
    print("Este passo nao configura provedor/tunel, nao envia mensagens e nao altera a agenda.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, RuntimeError) else "Configuracao interrompida; confira arquivos e Docker.", file=sys.stderr)
        sys.exit(1)
