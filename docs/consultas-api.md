# Consultas pela API — etapa 09, parte 3

As consultas usam o router v1 protegido por Bearer e foram concluídas na parte 3. A [criação HTTP](criacao-api.md) foi acrescentada na parte 4, as [alterações](alteracoes-api.md) na parte 5 e a [exclusão confirmada](exclusao-api.md) na parte 6. A conexão com n8n permanece para as próximas partes.

## Executar e conferir

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe -m app
```

Se a API já estiver aberta, encerrá-la com `Ctrl+C` e iniciar novamente para carregar as rotas e configurações. O endereço local padrão continua `http://127.0.0.1:8000`.

Abrir `/docs`, usar **Authorize** e informar somente a chave privada de `API_AUTH_TOKEN`; o Swagger acrescenta o esquema Bearer. Manter a autorização somente na sessão da aba, sem habilitar persistência, e usar **Logout** ao terminar. A chave não deve entrar nas URLs ou nos exemplos. Sua configuração está descrita em [proteção da API](seguranca-api.md).

Exemplos de caminhos, com data e identificadores fictícios:

```text
/api/v1/events?date=2030-06-17
/api/v1/events?date=2030-06-17&period=week&time_zone=America%2FRecife
/api/v1/events?date=2030-06-17&period=month&time_zone=UTC
/api/v1/events/geniusdemo001?time_zone=America%2FRecife
/api/v1/events/geniusdemoCase%2Foccurrence_20300617T130000Z
```

Essas chamadas exigem o header Bearer; abrir a URL sem ele não autentica. Os caminhos acima ilustram o formato e não afirmam a existência desses eventos.

## Entrada e contrato de saída

`GET /api/v1/events` exige `date` exatamente em `AAAA-MM-DD`, com data civil válida. `period` aceita `day`, `week` ou `month`; seu padrão é `day`. `time_zone` exige zona IANA válida e tem padrão `America/Recife`. Os padrões valem somente quando o parâmetro está ausente: valor vazio é inválido.

A semana começa na segunda-feira; o mês começa no primeiro dia. Os limites usam a meia-noite do fuso escolhido e **fim exclusivo**. Um evento que atravessa a madrugada pode aparecer nos dois dias correspondentes. A rota reutiliza os mesmos builders das consultas Python.

`GET /api/v1/events/{event_id}` aceita somente `time_zone` na query. Preservar o ID completo, caixa e identificador da ocorrência; codificá-lo como componente da URL, inclusive `/` como `%2F`. A captura de caminho preserva barras no identificador. Não procurar pelo título nessa rota.

Parâmetros desconhecidos e repetidos são recusados. Erros não repetem seus nomes ou valores: nomes desconhecidos aparecem apenas como `query`; os campos conhecidos usam uma lista permitida. A API continua selecionando a agenda configurada no Python, sem aceitar `calendar_id` ou `user_id` escolhidos pelo cliente.

As respostas seguem o [envelope v1](contrato-api-v1.md), com `action: list` ou `get`, `operation_id: null` e `mutation_state: not_applicable`:

- Listagem: `data` contém `calendar_name`, `period` com início/fim ISO 8601 e fuso, e `events`.
- Consulta por ID: `data` contém `calendar_name` e `event`.
- Cada evento expõe somente `event_id`, `title`, `start`, `end`, `all_day`, `time_zone`, `status` e `html_link`. Descrição, local e propriedades privadas não são publicados, mesmo se o conector entregar uma estrutura mais ampla.

## Códigos e falhas

| HTTP | Significado |
|---|---|
| 200 | Consulta concluída; `events: []` é uma listagem vazia válida. |
| 404 | No GET por ID, ausência validada pelo conector; não representa falha de comunicação. |
| 401 | Credencial da própria API ausente ou inválida, antes de abrir o conector da agenda. |
| 422 | Data, período, fuso, ID ou parâmetros inválidos; sem abrir o conector. Correlação inválida também pode produzir 422 após autenticação. |
| 502 | Consulta não concluída, falha do provedor ou resposta incoerente. Não é convertida em lista vazia ou evento ausente. |
| 503 | Configuração ou acesso à agenda indisponível; se a chave da própria API estiver ausente no servidor, o acesso protegido também fica fechado com 503. |

Os erros retornam códigos e mensagens fixos, sem corpo recebido, URL interna, credenciais ou exceções brutas. Erros de autenticação preservam `action: null` e `mutation_state: not_applied`.

## Cliente Google, encerramento e registros

As rotas são síncronas para não bloquear o event loop com o SDK. Cada requisição abre seu cliente somente depois da autenticação e da validação local; o contextmanager encerra o cliente antes de concluir a resposta. O acesso ao Google é **não interativo**: a rota não abre uma janela de login; problemas de autorização exigem configuração pelo procedimento já documentado.

Consultar não altera eventos. A seleção da agenda pode atualizar metadados no SQLite, e o fluxo normal de credenciais pode renovar o token OAuth. Não há novo escopo de permissão nem mudança do servidor para outro endereço.

O comando `python -m app` desativa o access log do Uvicorn para evitar registrar IDs e parâmetros das URLs. Ao iniciar Uvicorn por outro comando, manter `--no-access-log`; essa configuração não controla logs de clientes ou proxies externos.

## Verificação em 22/10/2026

As rodadas na preparação isolada e no repositório aprovaram **383 testes**, incluindo **11 novos de consultas HTTP**. Os testes atravessam ASGI, autenticação, validação, respostas e encerramento do cliente com provedores controlados; um percurso também usa SDK, transporte e SQLite reais com HTTP Google simulado. Não houve nova dependência. O registro **052** reúne essa validação local.

Também conferimos o servidor HTTP com a agenda Google real: saúde pública 200, chamada sem chave 401, parâmetros inválidos/desconhecidos 422, consultas por dia/semana/mês 200, ID existente 200 e ID anteriormente excluído 404. A leitura final do dia **23/10/2026** preservou os mesmos dois eventos existentes.

Essa verificação realizou **12 GETs reais na Calendar API e nenhuma mutação**. O registro **053** preserva as respostas HTTP e a contagem de chamadas ao Google, separadamente dos testes simulados. Não foi necessária nova captura de interface para demonstrar os códigos e o conteúdo retornado. A integração com n8n permanece pendente.


## Contexto factual para resumos — etapa 13, parte 2

As consultas acima mantêm seu contrato e projeção. A [coleta nova](coleta-contexto-agenda.md), `GET /api/v1/agenda/context`, é uma leitura avulsa separada para semana/mês: fuso efetivo da agenda, paginação limitada, contagem integral e contexto mínimo sem IDs ou LLM. Não reutiliza uma conversa nem seleciona um evento para executar ações. A API foi validada em ASGI e a leitura Google real foi pela CLI instalada; o processo API persistente não foi reiniciado neste marco.
