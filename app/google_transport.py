"""Transporte compatível com o SDK, sem repetição implícita de requisições."""

import httplib2
import requests
from requests.adapters import HTTPAdapter


TRANSPORT_ERROR = "Não foi possível obter uma resposta completa do Google."


class SingleAttemptHttp:
    """Uma chamada HTTP por tentativa explícita do SDK.

    A política de repetição pertence ao chamador: leituras podem ser repetidas,
    enquanto uma gravação sem resposta precisa ser conferida antes de novo envio.
    Redirecionamentos também não reenviam o corpo nem a autorização.
    """

    def __init__(self, timeout: float = 20):
        self.timeout = timeout
        self.follow_redirects = False
        self.redirect_codes = httplib2.REDIRECT_CODES
        self.connections = {}
        self._session = requests.Session()
        self._session.mount("https://", HTTPAdapter(max_retries=0))
        self._session.mount("http://", HTTPAdapter(max_retries=0))

    def request(
        self,
        uri,
        method="GET",
        body=None,
        headers=None,
        redirections=httplib2.DEFAULT_MAX_REDIRECTS,
        connection_type=None,
        **kwargs,
    ):
        # AuthorizedHttp transmite esses argumentos de httplib2. O transporte
        # não segue redirects nem aceita classes de conexão alternativas.
        if connection_type is not None or kwargs:
            raise TypeError("Opções de transporte não suportadas.")
        response = None
        try:
            try:
                response = self._session.request(
                    method=method,
                    url=uri,
                    data=body,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=False,
                    verify=True,
                )
                content = response.content
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                # requests entrega bytes já descomprimidos; informar a
                # codificação/tamanho originais causaria uma segunda decodificação.
                response_headers.pop("content-encoding", None)
                response_headers["content-length"] = str(len(content))
                response_headers["status"] = str(response.status_code)
                result = httplib2.Response(response_headers)
                result.reason = response.reason or ""
                return result, content
            finally:
                if response is not None:
                    response.close()
        except requests.RequestException:
            # O erro original pode incluir URL, token, headers ou corpo. Não
            # propagá-lo, inclusive pela cadeia de exceções exibida ao usuário.
            raise ConnectionError(TRANSPORT_ERROR) from None

    def close(self):
        self._session.close()
