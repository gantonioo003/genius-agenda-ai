"""Falhas externas esperadas na CLI, sem expor dados das exceções."""

import sqlite3

from google.auth.exceptions import RefreshError, TransportError
import httplib2
from oauthlib.oauth2 import OAuth2Error


AUTH_ERRORS = (RefreshError, OAuth2Error)
NETWORK_ERRORS = (OSError, httplib2.HttpLib2Error, TransportError)
LOCAL_STORE_ERRORS = (sqlite3.Error,)


def authentication_failure(*, writing: bool = False) -> str:
    message = (
        "Não foi possível autorizar o acesso ao Google. "
        "Reautorize com python -m app.google_calendar --authorize."
    )
    if writing:
        message += " Consulte o evento pelo ID antes de repetir a operação."
    return message


def local_store_failure(*, writing: bool = False) -> str:
    if writing:
        return (
            "Não foi possível confirmar o controle local da operação. "
            "Consulte o evento pelo ID antes de repetir."
        )
    return (
        "Não foi possível consultar o controle local da agenda. "
        "Nenhum resultado foi confirmado. Tente consultar novamente."
    )
