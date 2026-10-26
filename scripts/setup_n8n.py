"""Instala/publica apenas o fluxo de consulta local; credenciais nunca vao ao export."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen

CONTAINER = "genius-agenda-ai-n8n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Importar, publicar e reiniciar somente o n8n local")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workflow", type=Path)
    args = parser.parse_args()
    if not args.apply:
        parser.error("Use --apply para configurar e publicar o fluxo local.")
    root = args.project_root.resolve()
    sys.path.insert(0, str(root))
    from app.settings import Settings

    settings = Settings()
    if settings.api_auth_token is None:
        raise RuntimeError("Configure API_AUTH_TOKEN no .env antes de importar o fluxo.")
    docker = shutil.which("docker") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker/Docker/resources/bin/docker.exe")
    template = args.workflow or root / "n8n/workflows/consulta-eventos.json"
    workflow = json.loads(template.read_text(encoding="utf-8"))
    config_file = root / "secrets/n8n-local.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {
        "workflow_id": secrets.token_hex(8), "api_credential_id": secrets.token_hex(8),
        "webhook_credential_id": secrets.token_hex(8), "webhook_token": secrets.token_urlsafe(36),
    }
    if not isinstance(config, dict) or any(
        not isinstance(config.get(name), str) or not re.fullmatch(r"[0-9a-f]{16}", config[name])
        for name in ("workflow_id", "api_credential_id", "webhook_credential_id")
    ) or not isinstance(config.get("webhook_token"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", config["webhook_token"]):
        raise RuntimeError("Configuracao privada do n8n invalida; preserve o arquivo e confira seu backup.")
    config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    key = settings.api_auth_token.get_secret_value()
    credential_ids = {"httpBearerAuth": config["api_credential_id"], "httpHeaderAuth": config["webhook_credential_id"]}
    for node in workflow["nodes"]:
        for kind, ref in node.get("credentials", {}).items():
            ref["id"] = credential_ids[kind]
    workflow["id"] = config["workflow_id"]
    credentials = [
        {"id": config["api_credential_id"], "name": "Genius API - local", "type": "httpBearerAuth", "data": {"token": key}},
        {"id": config["webhook_credential_id"], "name": "Genius Webhook - teste local", "type": "httpHeaderAuth",
         "data": {"name": "X-Genius-Webhook-Key", "value": config["webhook_token"]}},
    ]

    def run(*command: str, payload: str | None = None) -> str:
        result = subprocess.run([docker, *command], input=payload, text=True, encoding="utf-8", capture_output=True, timeout=90)
        if result.returncode or "error occurred" in result.stdout.lower() or "failed" in result.stdout.lower():
            # Nao imprimir stderr/objetos com possiveis valores de credenciais.
            raise RuntimeError("O Docker/n8n recusou a operacao. Confira os servicos e a versao documentada.")
        return result.stdout

    def import_json(kind: str, value: object) -> None:
        path = f"/tmp/genius-import-{secrets.token_hex(8)}.json"
        try:
            run("exec", "-i", "-u", "node", CONTAINER, "node", "-e",
                "const fs=require('fs');fs.writeFileSync(process.argv[1],fs.readFileSync(0),{mode:0o600,flag:'wx'});",
                path, payload=json.dumps(value, ensure_ascii=False))
            output = run("exec", "-u", "node", CONTAINER, "n8n", f"import:{kind}", f"--input={path}")
            if "Successfully imported" not in output:
                raise RuntimeError("Importacao nao confirmada pelo n8n.")
        finally:
            run("exec", "-u", "node", CONTAINER, "node", "-e",
                "require('fs').rmSync(process.argv[1],{force:true});", path)

    run("exec", "-u", "node", CONTAINER, "node", "-e", "fetch('http://127.0.0.1:5678/healthz/readiness').then(r=>{if(!r.ok)process.exit(1)})")
    import_json("credentials", credentials)
    import_json("workflow", workflow)
    output = run("exec", "-u", "node", CONTAINER, "n8n", "publish:workflow", f"--id={config['workflow_id']}")
    if "Publishing workflow with ID:" not in output:
        raise RuntimeError("Publicacao nao confirmada; verifique o fluxo na interface.")
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
        raise RuntimeError("O n8n ainda nao esta pronto. Confira docker compose ps e tente acessar a interface.")
    print("Fluxo importado e publicado; n8n reiniciado preservando seu volume.")
    print("Webhook: http://localhost:5678/webhook/genius-agenda/consultar")
    print("Credenciais privadas: gerenciador do n8n; chave do webhook em secrets/n8n-local.json.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, RuntimeError) else "Configuracao interrompida; confira caminhos, Docker e arquivos locais.", file=sys.stderr)
        sys.exit(1)
