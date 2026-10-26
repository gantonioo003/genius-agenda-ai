# Consultas do agente — etapa 11, parte 3

**Implementadas e verificadas em 24/10/2026.** O agente ganhou ferramentas de leitura por dia, semana, mês e ID. A preparação de pedidos também foi exposta por HTTP, reutilizando o serviço existente. A chave do LLM continua ausente: os testes do modelo usam respostas controladas, e a conferência no Google real usa um planejador local fixo. Não houve teste de interpretação pelo provedor real.

## Percurso da consulta

`POST /api/v1/agent/query` recebe `{"message":"..."}`, sob a autenticação Bearer existente. A [parte 4](contexto-conversa.md) acrescentou `conversation_id` opcional; omiti-lo preserva a consulta avulsa. O orquestrador pede ao modelo **uma única escolha** entre:

| Ferramenta | Argumentos | Execução |
|---|---|---|
| `list_events` | `date` ISO, `period: day\|week\|month`, `time_zone` IANA | Consulta o período civil com os serviços/conector existentes. |
| `get_event` | `event_id` completo, `time_zone` | Confere o evento pelo ID literal informado na mensagem atual. |
| `get_context_event` | `time_zone` IANA | Acrescentada na parte 4: resolve a seleção única localmente e relê o evento; sem seleção, pede esclarecimento. |
| `ask_query` | `reason: missing_information\|unsupported_request` | Devolve pergunta fixa sem abrir o Google. É uma decisão de controle, não um novo serviço de calendário. |

As schemas usam `strict: true`, todos os campos obrigatórios e `additionalProperties: false`. O pedido Responses envia `tool_choice: required`, `parallel_tool_calls: false` e `store: false`. O parser só aceita uma chamada das funções autorizadas, com JSON/UTF-8 válido, chaves únicas e argumento de até 8 KiB. Ferramentas de escrita, respostas incompletas, chamadas múltiplas e nomes desconhecidos são recusados.

O Python valida novamente os argumentos antes de abrir o conector: data completa e existente, período permitido, fuso válido e ID opaco sem alterações. A seleção `get_event` exige que o ID também apareça literalmente na mensagem atual; o modelo não recebe autorização para inventar um ID. A ferramenta `get_context_event` usa um caminho separado: o backend resolve a seleção persistida sem enviar seu ID ao modelo.

O relógio UTC do servidor é convertido para `America/Recife`, fuso padrão da agenda, e incluído nas instruções para datas relativas. Não se usa a data local do host nem um suposto relógio escrito na mensagem como referência de sistema. A resolução linguística pelo modelo ainda precisa de validação real com a chave; os testes usam relógio e planos controlados.

Após a ferramenta, **não há segunda chamada ao LLM**. A API devolve o resultado estruturado conferido pelo backend. Títulos, horários e IDs encontrados no Google não são enviados ao modelo; descrições, locais e participantes não entram na projeção. O texto do pedido atual é enviado ao provedor quando a conexão estiver configurada; não foi implementada uma remoção automática de dados pessoais desse texto.

## Contrato da resposta

O envelope v1 conserva `operation_id: null` e `mutation_state: not_applicable`. `action` é `list`, `get` ou `null` para uma pergunta. Dados de sucesso sempre têm `execution_allowed: false`, relativo à execução de escritas; a consulta de leitura já ocorreu.

- Listagem: `calendar_name`, `period: {start,end,time_zone}` e `events`.
- ID: `calendar_name` e `event`, ou ausência confirmada com HTTP 404/`event_not_found`.
- Pergunta: `reason` e `question`, com `needs_clarification`/HTTP 200 e sem Google.

Cada evento contém somente `event_id`, `title`, `start`, `end`, `all_day`, `time_zone`, `status` e `html_link`. Semana civil começa na segunda; o fim dos períodos é exclusivo. O ID preserva caixa e ocorrências; a rota não escolhe outra agenda.

Entrada inválida retorna 422 antes da factory; falta de Bearer retorna 401. Ausência da chave do modelo retorna 503/`llm_not_configured`, sem abrir o Google. Erros seguros de modelo seguem a [parte 2](llm-api.md). Falha de configuração da agenda retorna 503/`calendar_setup_unavailable`; falha operacional ou dados incoerentes retornam 502/`operation_failed`. Nenhuma dessas falhas vira lista vazia ou ausência inventada. Não há retry automático.

O corpo bruto tem limite de 24 KiB e a mensagem segue `LLM_MAX_INPUT_CHARS`, com padrão/máximo de 4000 caracteres. Query, campos extras, chaves JSON duplicadas e Unicode inválido são recusados. A rota valida também os resultados de serviços substituídos, sem permitir campos privados extras. Modelo e consulta síncronos rodam em worker.

## Preparação HTTP

`POST /api/v1/requests/prepare` recebe `{"request":{...}}` e usa `RequestPreparationService`. Não chama o modelo. Os [exemplos fictícios](../examples/agent/query-requests.json) incluem consulta e preparação.

O corpo externo aceita somente `request`; seus campos permitidos são `action`, `time_zone`, `title`, `start`, `end`, `description`, `location`, `event_id`, `search_title`, `date` e `period`. Valores são texto ou `null`, com até 4000 caracteres; aplicam-se ainda as validações específicas do serviço. Campos técnicos como `operation_id`, `execute`, `confirmed`, token, usuário e agenda não são aceitos. O limite bruto também é 24 KiB, sem query e sem JSON com chaves repetidas.

Uma passagem local produz as perguntas/correções ou os parâmetros prontos sem abrir o conector. Só abre o Google quando a seleção de um evento exigir consulta. Na busca por título, reaproveita os esclarecimentos e a releitura por ID do serviço existente. Descoberta/autorização do conector pode atualizar metadados locais; isso não executa alteração de evento.

A resposta tem `ready`, `needs_clarification` ou `needs_correction`, `mutation_state: not_applied`, `operation_id: null` e `data` com `reason`, `question`, `missing_fields`, `invalid_fields`, `event`, `candidates` e `parameters`. Somente `ready` fornece parâmetros. Perguntas não escolhem um candidato nem produzem parâmetros executáveis.

É possível preparar `create`, `list`, `reschedule`, `edit` e `delete`, mas **nenhuma preparação executa essas escritas**. Uma preparação de exclusão não gera o token de prévia nem comprova consentimento. A prévia e a confirmação específica continuam pertencendo às rotas existentes e às próximas partes conversacionais.

## Reprodução e resultados

Exemplos de mensagem para `/api/v1/agent/query`:

```json
{"message":"Quais entrevistas tenho em 17 de junho de 2030?"}
```

```json
{"message":"Consulte minhas entrevistas desta semana."}
```

Exemplo para `/api/v1/requests/prepare`, que funciona sem chave de LLM:

```json
{"request":{"action":"list","date":"2030-06-17","period":"month","time_zone":"America/Recife"}}
```

Inicie/reinicie a API com `python -m app` para carregar as novas rotas. A autenticação usa a chave privada já configurada, sem colocá-la em exemplos públicos. Para a interpretação natural, configure também `OPENAI_API_KEY` conforme o [guia de conexão](llm-api.md). Não houve reinício dos serviços existentes neste marco.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_llm_query_plans.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agent_queries.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_request_preparation_api.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Passaram **777 testes Python**, sem falhas, erros ou casos ignorados: 718 anteriores e 59 novos (16 de planos/transporte, 28 de consultas e 15 de preparação HTTP). A suíte completa usou cópia isolada, sem `.env` ou bancos reais, com provedores substituídos. As bibliotecas e quatro exports n8n foram preservados; as 502 verificações JavaScript anteriores permanecem históricas, sem nova rodada.

Separadamente, quatro requisições **ASGI em memória**, usando o código novo e a API autenticada, passaram pelo orquestrador e pelo conector/OAuth reais do Google. O planejador foi substituído por planos fixos locais: não é um teste de inferência pelo LLM. Foram conferidos:

| Caso real | Resultado |
|---|---|
| Dia 23/10/2026 | Três eventos em `Genius Testes`; 23/10 00h até 24/10 00h exclusivo. |
| Semana de referência 23/10/2026 | Três eventos; 19/10 até 26/10 exclusivo. |
| Outubro/2026 | Três eventos; 01/10 até 01/11 exclusivo. |
| ID de um evento retornado | HTTP 200 e identidade exata confirmada. |

Esses são quatro usos lógicos do gateway, não uma contagem de requisições HTTP externas do SDK. Não houve mutação Google, envio WhatsApp ou envio dos eventos ao LLM. As contagens do registro de criação no SQLite foram preservadas. O relatório guarda períodos, contagens e indicadores, sem texto privado dos eventos ou IDs brutos.

Registros locais: `095_agente_consultas_testes_locais.json` e `096_agente_consultas_google_real.json`, no [índice de evidências](evidencias.md). O JSON comprova melhor o resultado que repetir a captura de um canvas que não mudou.

**Parte 3 implementada e validada nas ferramentas/backend.** Inferência real continua dependente da chave; contexto persistente, diálogo de ambiguidades, consentimento conversacional, escritas pelo agente e integração WhatsApp são partes seguintes. O fluxo WhatsApp publicado segue com a resposta fixa.

Fonte de formato de ferramentas conferida em 24/10/2026: [Function calling — documentação oficial OpenAI](https://developers.openai.com/api/docs/guides/function-calling).

A memória opcional de consultas, seus endpoints, retenção e erros estão no [guia de contexto](contexto-conversa.md). O resultado de 777 testes e as quatro leituras Google acima pertencem à parte 3; os 865 testes e o ensaio de reinício pertencem à parte 4, com provedores simulados.

Para completar informações ou resolver ambiguidades ao longo de turnos, use o [diálogo preparatório da parte 5](esclarecimentos-agente.md): `POST /api/v1/agent/dialogue`. A consulta e a preparação estruturada anteriores permanecem disponíveis; consultar com contexto preserva um rascunho pendente. A chave foi configurada, mas o teste real retornou 429 por créditos esgotados; a inferência real continua pendente.
