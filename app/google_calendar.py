"""Autorização local e descoberta segura da agenda de teste. Ainda sem CRUD."""

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import httplib2
from oauthlib.oauth2 import OAuth2Error

from app.database import database
from app.settings import PROJECT_ROOT, settings


SCOPES = (
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events.owned",
)


class CalendarSetupError(RuntimeError):
    """Erro que pode ser exibido sem revelar dados de autenticação."""


def local_path(path: Path) -> Path:
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def save_token(credentials: Credentials, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(credentials.to_json())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def get_credentials(*, interactive: bool = False) -> Credentials:
    token_file = local_path(settings.google_token_file)
    credentials = None
    if token_file.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(token_file))
        except (ValueError, KeyError):
            if not interactive:
                raise CalendarSetupError(
                    "Token local inválido. Execute novamente com --authorize."
                ) from None
        if credentials and not credentials.has_scopes(SCOPES):
            if not interactive:
                raise CalendarSetupError(
                    "A autorização não inclui as permissões necessárias. Use --authorize."
                )
            credentials = None
    if credentials and credentials.valid:
        return credentials
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            save_token(credentials, token_file)
            return credentials
        except RefreshError:
            if not interactive:
                raise CalendarSetupError(
                    "A autorização expirou ou foi revogada. Execute com --authorize."
                ) from None
    if not interactive:
        raise CalendarSetupError("Falta autorizar o Google. Execute com --authorize.")

    client_file = local_path(settings.google_client_file)
    if not client_file.is_file():
        raise CalendarSetupError("Arquivo do cliente OAuth não encontrado na pasta secrets.")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_file), list(SCOPES), autogenerate_code_verifier=True
    )
    # O callback contém um código temporário; não registrá-lo nos logs.
    logging.getLogger("google_auth_oauthlib.flow").setLevel(logging.WARNING)
    print("Uma janela do Google será aberta. Entre na conta de teste e autorize.", flush=True)
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=True,
        timeout_seconds=600,
        authorization_prompt_message="Aguardando autorização na janela do navegador...",
        success_message="Autorização recebida. Pode fechar esta aba e voltar ao projeto.",
        access_type="offline",
        prompt="consent",
    )
    granted_scopes = credentials.granted_scopes
    if not credentials.has_scopes(SCOPES) or (
        granted_scopes is not None and not set(SCOPES).issubset(granted_scopes)
    ):
        raise CalendarSetupError("Autorize as permissões solicitadas para concluir a conexão.")
    save_token(credentials, token_file)
    return credentials


def choose_calendar(
    items: list[dict[str, Any]], name: str, configured_id: str = ""
) -> dict[str, Any]:
    candidates = [
        item for item in items
        if (item.get("id") == configured_id if configured_id else item.get("summary") == name)
        and not item.get("deleted", False)
    ]
    if not candidates:
        raise CalendarSetupError(
            "A agenda de teste não foi encontrada. Confira conta, nome e ID configurado."
        )
    if len(candidates) != 1:
        raise CalendarSetupError(
            "Há mais de uma agenda com esse nome. Configure GOOGLE_CALENDAR_ID."
        )
    calendar = candidates[0]
    if calendar.get("accessRole") != "owner" or calendar.get("primary", False):
        raise CalendarSetupError(
            "Use uma agenda separada de teste criada por você, sem usar a agenda principal."
        )
    return calendar


def check_calendar(*, interactive: bool = False) -> dict[str, str]:
    credentials = get_credentials(interactive=interactive)
    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=20))
    service = build("calendar", "v3", http=http, cache_discovery=False)
    items: list[dict[str, Any]] = []
    page_token = None
    try:
        while True:
            page = service.calendarList().list(
                maxResults=250,
                pageToken=page_token,
                showHidden=True,
                showDeleted=False,
                minAccessRole="owner",
                fields="items(id,summary,timeZone,accessRole,primary,deleted),nextPageToken",
            ).execute(num_retries=2)
            items.extend(page.get("items", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
    finally:
        service.close()
    database.initialize()
    configured_id = settings.google_calendar_id or database.get_metadata("google_calendar_id") or ""
    calendar = choose_calendar(items, settings.google_calendar_name, configured_id)
    database.set_metadata("google_calendar_id", calendar["id"])
    database.set_metadata("google_calendar_name", calendar["summary"])
    return {
        "status": "ok",
        "calendar": calendar["summary"],
        "timezone": calendar.get("timeZone", ""),
        "access": calendar["accessRole"],
        "operation": "authorization_and_calendar_discovery_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Conectar o Google Calendar de teste.")
    parser.add_argument("--authorize", action="store_true", help="Abrir o consentimento no navegador.")
    args = parser.parse_args()
    try:
        result = check_calendar(interactive=args.authorize)
    except CalendarSetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OAuth2Error:
        print("A autorização não foi concluída. Confira a janela do Google e tente novamente.", file=sys.stderr)
        return 1
    except HttpError as exc:
        print(f"Google recusou a consulta (HTTP {exc.resp.status}). Confira API e permissões.", file=sys.stderr)
        return 1
    except (OSError, ValueError, httplib2.HttpLib2Error):
        print("Não foi possível concluir a conexão. Confira rede e configuração OAuth.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
