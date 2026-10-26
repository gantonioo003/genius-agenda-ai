# Contrato HTTP v1 — etapa 09, parte 1

**Contrato definido em 22/10/2026, na parte 1.** Na parte 2, implementamos a autenticação Bearer e `GET /api/v1/status`. `/health`, `/docs` e o esquema OpenAPI continuam públicos. Na parte 3, implementamos consultas; na parte 4, criação; na parte 5, alterações; na parte 6, prévia e exclusão confirmada. A parte 7 conectou o primeiro workflow de consultas por webhook. A parte 8 acrescentou apresentação de respostas ao fluxo de consultas, preservando este contrato da API. A parte 9 validou CRUD por uma fixture n8n com HTTP Google simulado e um disparo natural de status. A etapa está [encerrada](fechamento-etapa-09.md); a preparação HTTP foi acrescentada na [etapa 11, parte 3](consultas-agente.md); a [etapa 11, parte 7](criacao-agente.md) implementou o comando explícito de criação do rascunho. Fluxos n8n de escrita sobre Google real e o WhatsApp conversacional permanecem planejados.

Os [exemplos de comunicação](../examples/api/v1/communication-examples.json) são dados fictícios para conferir o contrato, sem enviar requisições. As datas usam **17/06/2030**, `America/Recife` e offset `-03:00`.

## Transporte e identificação

- Prefixo das novas rotas: `/api/v1`. Corpos e respostas usam JSON; enviar `Content-Type: application/json` quando houver corpo e aceitar `application/json`.
- Autenticação implementada: `Authorization: Bearer <CREDENCIAL_PRIVADA>`, com a chave do `.env` local. No primeiro fluxo n8n, a credencial fica em sua configuração privada, nunca no corpo, na query, em exports ou no Git. Consulte [configuração e testes da proteção](seguranca-api.md).
- `X-Request-ID` é opcional: 1 a 128 caracteres ASCII no conjunto `[A-Za-z0-9._-]`. Identifica uma tentativa HTTP, não uma intenção de criação. Uma nova tentativa recebe outro identificador.
- A API gera um UUID quando o header está ausente; se o valor recebido for inválido ou duplicado, gera um identificador seguro e, depois de autenticar a rota protegida, retorna 422. Respostas do envelope repetem o identificador no header `X-Request-ID` e no campo `request_id`.
- Na rota direta de criação, `operation_id` tem o contrato existente abaixo. Nos comandos de edição/reagendamento do agente, a parte 8 acrescentou identidade durável gerada pelo servidor, sem campo de operação escolhido pelo cliente. Na criação direta: texto com conteúdo, até 128 caracteres na fronteira HTTP, preservado exatamente, sem aparar ou normalizar o identificador. Na rota direta `POST /events`, o código/workflow deve produzi-lo e persistir antes do primeiro envio. No comando `/agent/create`, o servidor o deriva do escopo, da conversa e dos parâmetros canônicos; o cliente e o modelo não o fornecem.

O `operation_id` permanece igual entre tentativas da mesma intenção. Não usar a saída da IA nem o ID de cada execução do n8n para produzir uma nova operação a cada tentativa. Alterar o conteúdo de um pedido válido exige uma nova intenção/operação; repetir uma criação confirmada não recria um evento removido.

A deduplicação de criação usa o mesmo SQLite, a mesma agenda e a identidade canônica do conteúdo. Outro identificador com conteúdo idêntico pode reutilizar o ID original. A deduplicação das mensagens WhatsApp e o controle de cada operação são registros separados. Os PATCHs diretos e a exclusão não oferecem idempotência persistente geral; os comandos de edição/reagendamento do agente acrescentam seu próprio ledger e reconciliação na [parte 8](alteracoes-agente.md), e a [parte 9](exclusao-agente.md) acrescenta a reserva de exclusão de uso único por proposta aprovada.

## Rotas escolhidas

Os caminhos da tabela são relativos ao prefixo `/api/v1`: por exemplo, `/events` corresponde a `/api/v1/events`. O ID de evento é opaco: codificar como um componente da URL e preservar o valor completo, inclusive caixa e identificadores de ocorrências.

A API opera somente sobre a agenda configurada no Python. O workflow não pode escolher `calendar_id` ou `user_id` arbitrários. As rotas CRUD rejeitam campos desconhecidos no corpo e nos objetos `event`/`changes`; as futuras rotas também devem aplicar essa restrição.

| Método e caminho | Entrada | Comportamento |
|---|---|---|
| `POST /agent/conversations` — implementada | JSON `{}` | Emitir UUID4 de conversa com memória privada e validade limitada. |
| `POST /agent/confirmations/preview` — implementada | `conversation_id` | Emitir prévia vinculada ao rascunho e à versão atual; sem executor. |
| `POST /agent/confirmations/approve` — implementada | `conversation_id`, `proposal_id`, `message` | Registrar consentimento explícito para a proposta exata. |
| `POST /agent/confirmations/status` — implementada | `conversation_id`, `proposal_id` | Consultar somente o estado local, sem renovar prazo. |
| `POST /agent/confirmations/revoke` — implementada | `conversation_id`, `proposal_id` | Revogar somente a proposta. |
| `POST /agent/dialogue` — implementada | `message` e `conversation_id` obrigatório | Esclarecer e preparar um pedido com rascunho persistente; sem executor. |
| `POST /agent/delete` — implementada | Somente `conversation_id` | Excluir apenas a proposta exata aprovada, com consumo único, versão e ausência conferidas. |
| `POST /agent/edit` — implementada | Somente `conversation_id` | Editar o evento do rascunho completo, com versão vinculada e conferência do resultado. |
| `POST /agent/reschedule` — implementada | Somente `conversation_id` | Reagendar o evento do rascunho, com versão, conflitos e readback. |
| `POST /agent/create` — implementada na etapa 11, parte 7 | Somente `conversation_id` | Executar criação preparada por comando explícito; proposta ativa exige aprovação exata e consumo. Replay em andamento/incerto/confirmado apenas reconcilia por GET. |
| `POST /agent/query` — implementada | `message` e `conversation_id` opcional | Consultar com contexto explícito ou como pedido avulso; sem executar escrita. |
| `DELETE /agent/conversations/{conversation_id}` — implementada | Sem corpo | Apagar somente o contexto local; HTTP 204 sem corpo. |
| `GET /status` — implementada | Sem corpo | Conferir a autenticação da API; não consulta Google ou eventos. |
| `POST /requests/prepare` — implementada | `{ "request": { ... } }` | Preparar os dados; consultar a agenda apenas quando a seleção exigir. Não alterar eventos. |
| `GET /agenda/context` — implementada na etapa 13, parte 2 | Query `period` (week ou month), `date` opcional | Coletar contexto factual mínimo, fuso da agenda e contagens completas; sem LLM ou conversa. |
| `POST /agenda/summary` — implementada na etapa 13, parte 3 | JSON `period` (week ou month), `date` opcional | Organizar fatos com IA ou fallback factual; sem contexto de conversa, escrita ou envio. |
| `POST /agenda/availability` — implementada na etapa 13, parte 4 | JSON datas inicial/final, faixa diária, duração e margem opcional | Calcular até três opções, sem IA, reserva, exclusão de ID ou alteração. |
| `GET /events` — implementada | Query `date`, `period`, `time_zone` | Consultar eventos por dia, semana ou mês. |
| `GET /events/{event_id}` — implementada | Query `time_zone` | Consultar um evento pelo ID exato. |
| `POST /events` — implementada | `{ "operation_id": "...", "event": { ... } }` | Executar criação ou reutilizar uma criação correspondente. |
| `PATCH /events/{event_id}/schedule` — implementada | `{ "start": "...", "end": "...", "time_zone": "..." }` | Executar reagendamento. |
| `PATCH /events/{event_id}` — implementada | `{ "changes": { ... }, "time_zone": "..." }` | Executar edição parcial das informações. |
| `POST /events/{event_id}/deletion-preview` — implementada | `{ "time_zone": "..." }` | Consultar o evento e obter a prévia de exclusão com token da versão atual. |
| `POST /events/{event_id}/delete` — implementada | `{ "confirmation_token": "...", "time_zone": "..." }` | Executar exclusão após a confirmação explícita da prévia. |

Em `GET /events`, `date` é obrigatória no formato `AAAA-MM-DD`; `period` aceita `day`, `week` ou `month`, com padrão `day`. O fuso de consulta tem padrão `America/Recife`, inclusive no GET por ID. Os limites civis e o fim exclusivo seguem as regras já implementadas.

As consultas rejeitam valores vazios, datas abreviadas, parâmetros desconhecidos e parâmetros repetidos, com HTTP 422 antes de abrir o conector. Padrões são aplicados apenas na ausência do parâmetro. Campos desconhecidos são indicados como `query`, sem reproduzir seu nome ou valor. A rota por ID preserva caixa, ocorrências e barras codificadas. Respostas v1 usam `Cache-Control: no-store`; o comando `python -m app` desativa o log de acesso que conteria a URL completa. Consulte o [guia de consultas implementadas](consultas-api.md).

No POST de criação, `event` exige `title`, `start`, `end` e `time_zone`; `description` é opcional. Início/fim usam ISO 8601 com offset UTC, coerentes com o fuso informado e com duração positiva. A chamada executa a ação; para conferir apenas os dados, usar o serviço/CLI de preparação. A rota HTTP de preparação está disponível conforme o [guia da etapa 11, parte 3](consultas-agente.md).

Na criação implementada, os campos são textos estritos, com Unicode codificável em UTF-8. `operation_id` aceita 1 a 128 caracteres, deve conter texto e é preservado exatamente. Não enviar parâmetros de query. JSON inválido e campos desconhecidos geram 422 seguro; o nome de uma propriedade desconhecida não é refletido. A autenticação não promete anteceder o parsing JSON do framework; nenhum erro de parsing/validação abre o conector. Consulte o [guia de criação](criacao-api.md).

Na edição, `changes` aceita `title`, `description` e `location`. Campo omitido ou `null` preserva o valor; `""` limpa descrição ou local. Título vazio é inválido. É necessária ao menos uma alteração. `time_zone` faz parte do corpo das operações de reagendamento, edição, prévia e exclusão.

Na edição e no reagendamento implementados, `time_zone` é obrigatório no corpo e `operation_id` permanece `null` nas respostas. Não enviar query nem escolher outra agenda. As rotas preservam o ID opaco; `/schedule` é um subrecurso reservado e sua rota é registrada antes do PATCH genérico. Dados inválidos e JSON malformado produzem 422 seguro, sem abrir o conector. O título original de um snapshot de edição pode estar vazio; isso não permite enviar um novo título vazio. Consulte o [guia de alterações](alteracoes-api.md).

Exemplo de intenção de criação:

```json
{
  "operation_id": "interview-intent-20300617-001",
  "event": {
    "title": "Entrevista técnica",
    "start": "2030-06-17T10:00:00-03:00",
    "end": "2030-06-17T10:30:00-03:00",
    "time_zone": "America/Recife"
  }
}
```

## Preparação e confirmação

O objeto `request` conserva o [contrato de preparação existente](esclarecimentos.md): `action` e os campos próprios de criar, consultar, reagendar, editar ou excluir. Identificadores técnicos do HTTP ficam fora dele. Não aceitar `operation_id`, opções de execução ou confirmação dentro de `request`.

Uma preparação `ready` fornece parâmetros para uma ação posterior. O n8n não deve chamar um executor automaticamente apenas porque recebeu `ready`: a ação precisa ter sido solicitada. Perguntas e correções não escolhem um evento nem produzem parâmetros executáveis.

Preparação, consulta e prévia não alteram eventos. A descoberta da agenda pode atualizar metadados no SQLite local; essas operações não prometem ausência de qualquer escrita local.

Para excluir, o fluxo deve obter a prévia, apresentar o evento ao usuário e receber confirmação explícita antes de devolver seu `confirmation_token` à rota de exclusão. O token vem do Python e corresponde à agenda, ao ID e à versão; não deve ser fabricado, substituído por `confirmed: true` ou gerado pela IA. Uma preparação de exclusão não fornece esse token.

As rotas de exclusão estão implementadas na parte 6. Ambas exigem `time_zone` no corpo; a execução exige `confirmation_token` de 64 caracteres hexadecimais minúsculos. Não aceitar `execute`, `confirmed`, query ou propriedades extras. O executor encaminha o código recebido intacto e não gera outra prévia. Código com formato válido, mas incorreto ou obsoleto, é recusado antes do DELETE pelo conector; a exceção genérica permanece 502/`unknown`, sem classificação inventada. Consulte o [guia de exclusão](exclusao-api.md).

ETag e `If-Match` são internos ao Python. O token de prévia não autentica a chamada nem comprova sozinho o consentimento humano. A persistência da conversa e das propostas já está implementada no backend do agente. Criação/edição/reagendamento consomem a aprovação quando aplicável; o executor HTTP de exclusão sempre exige e consome a proposta aprovada antes da primeira tentativa. Vincular essas operações ao remetente e ao texto WhatsApp permanece na integração futura. Se a versão mudar, deve ser obtida e confirmada outra prévia.

## Envelope comum de resposta

Todos os campos abaixo são obrigatórios, inclusive os que admitem `null`:

```json
{
  "api_version": "1",
  "request_id": "request-20300617-001",
  "operation_id": "interview-intent-20300617-001",
  "status": "succeeded",
  "action": "create",
  "mutation_state": "confirmed",
  "message": "Entrevista confirmada na agenda.",
  "data": {
    "event_id": "EVENT_ID_FICTICIO",
    "html_link": "https://example.invalid/event",
    "calendar_name": "Genius Testes",
    "reused": false,
    "recovered": false
  },
  "error": null
}
```

| Campo | Valores e significado |
|---|---|
| `api_version` | Texto fixo `"1"`. |
| `request_id` | Correlação da tentativa HTTP, igual ao header da resposta. |
| `operation_id` | Identificador conhecido da criação; `null` nas demais ações. |
| `status` | `succeeded`, `ready`, `needs_clarification`, `needs_correction`, `needs_confirmation`, `conflict`, `not_found` ou `failed`. |
| `action` | `create`, `list`, `get`, `reschedule`, `edit`, `delete` ou `null` em respostas de infraestrutura/autenticação ou quando não for possível determinar a ação. |
| `mutation_state` | Estado da operação descrito abaixo. |
| `message` | Texto seguro para apresentação; não é um código para decisões automáticas. |
| `data` | Objeto específico da ação ou `null`. |
| `error` | `null` em sucesso, pergunta, correção de preparação e prévia; objeto estruturado nas falhas/bloqueios. |

Estados de mutação:

- `not_applicable`: consulta sem ação de escrita.
- `not_applied`: preparação, prévia ou bloqueio comprovado antes da escrita.
- `confirmed`: estado esperado conferido, inclusive reutilização, ausência confirmada ou operação sem mudança.
- `unknown`: mutação cujo resultado não pôde ser suficientemente confirmado.

`error` usa `{ "code": "...", "retryable": false, "fields": [] }`. `fields` contém somente nomes de campos. Não incluir credenciais, tokens, stack trace, URL interna, cabeçalhos OAuth, cópia do payload ou `str(exception)`. O token de confirmação aparece apenas no dado específico da prévia bem-sucedida.

## Dados por ação

| Resposta | Conteúdo de `data` |
|---|---|
| Status autenticado — implementado | `authenticated: true`, `service`; `status: succeeded`, `action: null` e `mutation_state: not_applicable`. |
| Preparação | `reason`, `question`, `missing_fields`, `invalid_fields`, `event`, `candidates`, `parameters`; não repetir `status` ou `action`. Somente `ready` pode ter `parameters`; perguntas mantêm `parameters` e evento escolhido nulos. |
| Criação | `event_id`, `html_link`, `calendar_name`, `reused`, `recovered`, conforme o resultado atual do serviço. |
| Listagem | `calendar_name`, `period: {start, end, time_zone}`, `events`. Os limites usam instantes com offset; o fim é exclusivo. |
| Consulta por ID | `calendar_name`, `event`. |
| Reagendamento | `calendar_name`, `before`, `after`, `requested_start`, `requested_end`, `time_zone`, `recovered`; `result` é `rescheduled` ou `unchanged`. |
| Edição | `calendar_name`, `before`, `after`, `requested`, `recovered`; `result` é `updated` ou `unchanged`. |
| Prévia de exclusão | `calendar_name`, `event`, `confirmation_token`, `question`; `result` é `needs_confirmation` ou `already_absent`. Se já estiver ausente: HTTP 200, `succeeded`, `not_applied`, evento/token/pergunta nulos, sem executar exclusão. |
| Exclusão executada | `calendar_name`, `event_id`, `recovered`; `result` é `deleted` ou `already_absent`. |
| Conflito de horário | `ConflictReport` completo: `status: "conflict"`, `calendar_name`, `start`, `end`, `time_zone`, `conflicts`; não usar a mensagem como fonte dos eventos conflitantes. |

Os eventos mínimos seguem `AgendaEvent`: `event_id`, `title`, `start`, `end`, `all_day`, `time_zone`, `status` e `html_link`. Eventos de dia inteiro conservam datas e fim exclusivo. As informações de descrição/local aparecem somente nos snapshots de edição que precisam desses dados; não acrescentar campos privados às consultas gerais ou à criação.

O DTO HTTP preserva os snapshots e renomeia o `status` próprio de reagendamento, edição e exclusão para `data.result`, sem substituir o `status` comum do envelope. O relatório de conflito conserva seu `data.status`. `recovered: true` significa sucesso conferido por leitura após resposta incerta, nunca resultado desconhecido.

## HTTP e classificação de falhas

| HTTP | Situação | Envelope |
|---|---|---|
| `200` | Consulta, criação reutilizada, atualização, exclusão ou operação sem mudança | `succeeded`; leitura `not_applicable`, mutação com resultado conferido `confirmed`. |
| `201` | Criação nova confirmada, inclusive recuperada após resposta perdida | `succeeded`, `create`, `confirmed`. |
| `200` | Preparação completa, pergunta, correção ou prévia | `ready`, `needs_clarification`, `needs_correction` ou `needs_confirmation`, com `not_applied`. |
| `404` | GET por ID não encontrou o evento | `not_found`, `get`, `not_applicable`, `event_not_found`. Na preparação, ausência pede esclarecimento com HTTP 200. |
| `409` | `CalendarConflictError` tipado antes da escrita | `conflict`, `not_applied`, `schedule_conflict`, com relatório em `data`. |
| `422` | JSON/estrutura/campos inválidos na fronteira ou entrada inválida antes de executar | `failed`, `invalid_request`; não houve mutação. Dados semanticamente incompletos/incorretos da preparação usam HTTP 200 e pergunta/correção. |
| `401` | Credencial da própria API ausente ou inválida | `failed`, `unauthorized`, desafio `WWW-Authenticate: Bearer`. Implementado na parte 2; não confundir com OAuth do Google. |
| `503` | Chave do próprio servidor não configurada | `failed`, `api_auth_unavailable`, sem executar a rota. Implementado na parte 2. |
| `503` | `CalendarSetupError` antes da operação | `failed`, `calendar_setup_unavailable`; consulta `not_applicable`, mutação `not_applied`. |
| `502` | `CalendarOperationError` genérico em mutação | `failed`, `operation_not_confirmed`, `unknown`, sem repetição automática. |
| `502` | Falha operacional em leitura | `failed`, `operation_failed`; nunca converter em evento ausente ou lista vazia. |

Nas rotas CRUD diretas (`/events`), `CalendarSetupError` ao abrir a factory antes da chamada do serviço produz 503/`not_applied`. Após iniciar o serviço, qualquer falha não comprovada, inclusive no fechamento do cliente, permanece 502/`unknown`; não deduzir o estado pelo texto da exceção. Somente `CalendarConflictError` válido lançado pela criação ou pelo reagendamento gera 409. Nas alterações, ausência e versão concorrente continuam erros operacionais genéricos: não fabricar 404 ou 409 pelo texto da exceção. JSON/campos inválidos podem manter no envelope um `operation_id` válido conhecido; entradas inválidas desse identificador e falhas de parsing não o reproduzem.

Na prévia de exclusão, `CalendarSetupError` produz 503/`not_applicable`; falha operacional, DTO incoerente ou cleanup genérico produz 502/`operation_failed`/`not_applicable`. No executor, falha após iniciar o serviço ou fechar o cliente permanece 502/`unknown`. O token aparece somente na prévia bem-sucedida de um evento disponível; o executor e os erros não o retornam. O `data.result` da execução é `deleted` ou `already_absent`, com `event_id`, `calendar_name` e `recovered`.

Nas rotas diretas antigas, a exceção operacional pode reunir conflito de versão, identidade divergente e resultado incerto. Não analisar seu texto para fabricar `409 stale_version`, presumir ausência ou afirmar que uma escrita não ocorreu. Os comandos do agente das partes 8/9 acrescentaram guardas tipadas de agenda/versão, contexto e testes para especializar apenas as falhas comprovadas, mantendo o fallback `unknown` após possível escrita.

Na criação incerta, a exceção atual pode não expor o ID reservado: `data.event_id` pode ser `null`, enquanto o `operation_id` conhecido permanece no envelope. Não calcular outro ID nem inventar uma rota de busca por operação; esse suporte exige contrato e implementação próprios.

## Fluxo n8n e limites desta parte

Desabilitar repetição automática de POST, PATCH e exclusão, inclusive após timeout. Se uma resposta se perder, conferir o estado antes de decidir outra tentativa. A reutilização de um `operation_id` preserva identidade; não autoriza reenvio cego. Todas as falhas estruturadas têm `retryable: false` neste contrato.

A parte 2 implementou autenticação, correlação e status. A parte 3 disponibilizou as consultas GET, com validação segura e conferência HTTP/Google real. A parte 4 implementou a criação com body estrito e erros seguros, conferida por HTTP/Google real. A parte 5 implementou edição e reagendamento com validação segura e conferência real. A parte 6 implementou a exclusão em duas chamadas, com validação local completa e prévia/bloqueios reais. A parte 7 conectou as consultas por webhook ao n8n, conforme o [guia do primeiro fluxo](n8n-primeiro-fluxo.md). A preparação HTTP e seu corpo foram implementados na etapa 11, parte 3; demais fluxos n8n permanecem pendentes. A dependência protege rotas registradas no router v1; caminhos desconhecidos e métodos não suportados ainda seguem o roteador, sem promessa de autenticação anterior ao parsing de JSON.

O [catálogo da parte 1](../examples/api/v1/communication-examples.json) preserva exemplos ilustrativos, não respostas capturadas das rotas. Docker não é necessário para esta verificação da API. As rotas CRUD estão implementadas. A parte 7 conectou o primeiro fluxo de consultas; demais operações no n8n permanecem pendentes.

Dentro do contêiner n8n, `localhost` aponta para o próprio contêiner. Na parte 7, o Docker Desktop alcançou `host.docker.internal:8000` com a API ainda em `127.0.0.1`, sem ampliar sua exposição. Essa conectividade precisa ser validada em outra máquina; não alterar automaticamente para `0.0.0.0`.

## Apresentação no n8n — parte 8

O envelope **da API Python** permanece com os nove campos acima. O webhook **do n8n** acrescenta `presentation`, fora de `data`, com `kind`, `text`, `next_step`, `requires_user_input`, `auto_retry: false` e `execution_allowed: false`. A resposta técnica é preservada quando coerente com a ação/fase esperadas; respostas incompatíveis produzem 502 seguro. A extensão não modifica o contrato das rotas Python.

Essa camada não autoriza execução nem retentativa: perguntas aguardam dados/consentimento e escritas `unknown` exigem conferência. O fluxo atual continua somente GET; estados das demais ações foram exercitados com dados fictícios. Na futura integração de confirmação, apresentar também o evento e obter o consentimento, sem usar o código como prova de consentimento. Consulte o [guia de respostas e falhas](n8n-respostas-falhas.md).

## Ensaio integrado — parte 9

O [runner de integração](n8n-testes-integracao.md) conferiu as rotas CRUD e seus estados atravessando n8n e HTTP reais, com os serviços/SDK/SQLite da aplicação e somente o HTTP Google em memória. A fixture usa origem fixa `8001`, credenciais exclusivas e IDs de fluxo separados; não é um executor publicado sobre a agenda real. O Schedule Trigger consultou `GET /api/v1/status` da API normal e foi despublicado após comprovar o disparo natural. O contrato das rotas da aplicação não mudou.

## Resposta fixa do sandbox — etapa 10, parte 8

`POST /api/v1/whatsapp/sandbox/reply` é um comando interno autenticado usado pelo n8n. Recebe somente `{"request_id":"<UUID_CANONICO_V4_DO_CLAIM>"}`, idêntico ao `X-Request-ID`. Destinatário e texto vêm da configuração privada e do serviço; não são parâmetros do comando. O corpo é limitado a 1 KiB, sem query ou propriedades extras. Consulte o [contrato e os testes do envio](whatsapp-respostas.md).

A rota exige um claim existente e íntegro em `processing` na inbox. UUID inexistente/concluído/incerto retorna 409 e não autoriza envio. Um claim somente de status retorna 200 com `skipped`, sem reservar outbox ou chamar o provedor. Um claim com mensagens novas reserva uma resposta por lote antes do POST externo; resultado pendente/incerto bloqueia nova tentativa. O comando não executa operações de agenda.

O envelope mantém os nove campos v1, com a ação adicional `whatsapp_test_reply` e `operation_id: null`. Sucesso contém `data: {authenticated: true, reply_state: "accepted"|"skipped", reused: boolean}`. Aceite validado/persistido usa `confirmed`, significando confirmação do aceite do pedido de envio; não comprova entrega/leitura no aparelho. Somente status usa `not_applicable`. Falha antes de iniciar usa `not_applied`; resultado de envio incerto usa `unknown`, com `retryable: false`. Os nomes de erro são limitados pelo servidor, sem resposta bruta ou dados privados.

`reused: true` recupera o aceite persistido enquanto o claim permanece ativo, sem novo envio. Depois que a inbox conclui, repetições do callback são respondidas pelo receptor sem chegar ao n8n/API; uma chamada direta para o claim concluído retorna 409. Confirmações de entrega/leitura não geram outra mensagem.

## Agente de IA — etapa 11, parte 1

O [escopo do agente](agente-ia.md) mapeia ferramentas lógicas para estas rotas e os serviços existentes. Trata-se de uma definição: nenhum adaptador de LLM ou endpoint novo foi implementado nesta parte. Naquele marco, a preparação HTTP ainda estava pendente; foi implementada posteriormente na parte 3. Naquele marco, contexto, consentimento persistente e transporte mínimo de texto WhatsApp ainda precisavam ser implementados. Contexto e consentimento foram adicionados nas partes 4 a 6; a parte 7 conectou a criação pelo comando HTTP explícito. O texto/resposta dinâmica WhatsApp continua pendente. O contrato v1 e o comando atual de resposta fixa permanecem inalterados.

## Prévia LLM — etapa 11, parte 2

`POST /api/v1/agent/preview` foi implementada no router autenticado. Recebe somente `message` e retorna uma prévia sem ferramentas, com `action: agent_preview`, `operation_id: null`, `mutation_state: not_applied` e `data.execution_allowed: false`. A configuração é opcional para a aplicação: ausência de chave retorna 503/`llm_not_configured` nas rotas que dependem do modelo. Consulte [corpo, limites, erros e reprodução](llm-api.md). Naquela parte, as rotas de agenda e o comando de resposta fixa WhatsApp permaneceram inalterados; a preparação HTTP foi acrescentada depois, na parte 3.

## Consultas do agente e preparação — etapa 11, parte 3

`POST /api/v1/agent/query` recebe `message` e, desde a parte 4, `conversation_id` opcional; escolhe uma ferramenta de leitura, valida os argumentos e devolve dados conferidos, sem segunda chamada ao modelo. Mantém `operation_id: null`, `mutation_state: not_applicable` e `data.execution_allowed: false`. Falta de chave do modelo retorna 503/`llm_not_configured` antes de abrir o Google.

`POST /api/v1/requests/prepare` recebe somente `{request:{...}}`, sob Bearer, e preserva `ready`/`needs_clarification`/`needs_correction`, `mutation_state: not_applied` e os dados de preparação deste contrato. Só `ready` fornece `parameters`; nenhuma preparação executa mutações ou gera token de exclusão. Schema estrito, limites, campos permitidos, erros e reprodução estão no [guia de consultas do agente](consultas-agente.md). A criação do agente foi acrescentada na [parte 7](criacao-agente.md) por comando HTTP explícito. Edição/reagendamento foram implementados na [parte 8](alteracoes-agente.md), e exclusão aprovada na [parte 9](exclusao-agente.md); WhatsApp conversacional continua para a parte 10.

## Contexto de consultas — etapa 11, parte 4

`POST /agent/conversations` recebe somente `{}` e retorna 201 com UUID4 canônico emitido pelo servidor, `expires_in_seconds` e `execution_allowed: false`. `DELETE /agent/conversations/{conversation_id}` exige corpo vazio e retorna 204 sem conteúdo. Ambos herdam Bearer, correlação e `Cache-Control: no-store`; seu efeito é local, com `operation_id: null` e `mutation_state: not_applicable` nos envelopes.

`POST /agent/query` aceita `conversation_id` opcional. O servidor verifica escopo/validade antes de construir o modelo; sem esse campo, permanece avulso. Contexto inexistente/expirado/de outra chave retorna 404, revisão concorrente 409 e armazenamento indisponível/capacidade 503, sem retry. Campos extras e UUID inválido retornam 422. A memória não é devolvida nem autoriza escrita. [Configuração, schemas e retenção](contexto-conversa.md).

## Esclarecimentos — etapa 11, parte 5

`POST /agent/dialogue` aceita somente `message` e `conversation_id` canônico previamente emitido. Corpo de até 24 KiB, Bearer e validação antes da memória/modelo. Retorna `ready`, `needs_clarification`, `needs_correction`, `cancelled` ou `failed`, com `operation_id: null`, `mutation_state: not_applied` e `execution_allowed: false` quando há dados. `data` admite somente `reason`, `question`, `missing_fields`, `invalid_fields`, `event`, `candidates`, `parameters`, `execution_allowed`; parâmetros apenas em `ready`.

Uma pergunta preserva os campos conhecidos; correção ou troca da busca invalida as referências que deixaram de corresponder. Seleção por posição exige escolha explícita, releitura e conferência do fingerprint. “Sim” e `ready` não são consentimento para executar. Falhas não avançam revisão/TTL; concorrência é recusada por CAS. A preparação estruturada `/requests/prepare` permanece avulsa. [Schemas, rascunho, migração e exemplos](esclarecimentos-agente.md).

## Confirmações vinculadas — etapa 11, parte 6

Os quatro comandos `/agent/confirmations/preview`, `/approve`, `/status` e `/revoke` exigem Bearer e corpo JSON estrito de até 2 KiB. A prévia recebe somente a conversa emitida; os demais comandos exigem também a proposta, e a aprovação recebe mensagem de até 128 caracteres. Parâmetros, calendário e versão não são aceitos do cliente.

A resposta usa `operation_id: null`, `mutation_state: not_applied` e `data.execution_allowed: false`, mesmo em `approved`. Os dados públicos são somente `proposal_id`, `state`, `action`, `event`, `parameters`, `expires_at`, `approved_at`, `question`, `execution_allowed`. Estados conhecidos: `needs_confirmation` (`pending` nos dados), `approved`, `revoked`, `expired`, `invalidated`, `consumed`; falhas usam `failed`. Prazo padrão de cinco minutos, versão lida novamente antes de aprovar, CAS ao salvar e consumo interno único sem endpoint público. O LLM não decide a aprovação. [Schemas, exemplos, privacidade e limites](confirmacoes-agente.md).

## Criação do rascunho — etapa 11, parte 7

`POST /api/v1/agent/create` recebe exatamente `{conversation_id}`, sob Bearer, sem query e com `Content-Type: application/json`. O corpo bruto tem até **1 KiB**, sem campos extras, chaves repetidas ou UUID fora do formato canônico emitido pelo servidor. A conversa deve pertencer ao escopo autenticado e conter um rascunho `create` completo. O diálogo continua sem escrita; `ready` não aciona o executor.

Parâmetros e `operation_id` vêm do servidor. O comando explícito pode executar um pedido completo sem confirmação genérica adicional; se houver proposta ativa, exige aprovação exata, agenda correspondente, revisão válida e consumo único. Não recebe `proposal_id`, `event`, `calendar_id`, opções de execução ou identidade de operação para aceitar valores escolhidos pelo cliente/modelo.

Usa `action: create`, com 201 para nova criação confirmada, 200 para evento reutilizado/reconciliado e `data: {event_id, html_link, calendar_name, reused, recovered}`. Conflito confirmado usa 409/`conflict`/`not_applied` e relatório mínimo. Falha incerta usa 502/`operation_not_confirmed`/`unknown`, sem reenvio automático; erro mantém `retryable: false`. Há também recusas 409 para agenda/revisão/proposta divergente ou outra criação não resolvida. Erros de entrada/preparação, conversa e indisponibilidade preservam as respostas seguras dos respectivos serviços.

O ledger separado **`data/agent-creations.sqlite3`**, schema **1**, é configurável por `AGENT_CREATION_DATABASE_PATH`; o contexto conserva o schema **3**. Não há TTL para resultados de criação. Replay de `running`, `unknown` ou `confirmed` somente consulta a agenda e a identidade global persistida, sem POST/PATCH/DELETE. `absent` ou `unregistered` não autorizam uma nova criação. `creation_previously_confirmed` com `mutation_state: confirmed` descreve o histórico confirmado quando o evento deixou de ser reconfirmado; não garante sua presença atual nem o recria.

A validação de **25/10/2026** aprovou **1.160 testes Python** e **27 casos HTTP TCP com dois processos**, SQLite real e modelo/gateway como fixtures. Zero chamadas reais Google/LLM ou envios WhatsApp. Credencial do LLM configurada, créditos esgotados e inferência real não testada novamente. A [parte 8](alteracoes-agente.md) implementou edição/reagendamento por comando HTTP; a [parte 9](exclusao-agente.md) implementou a exclusão aprovada por comando HTTP; WhatsApp/n8n dinâmico continua pendente. [Contrato completo, testes, privacidade e recuperação](criacao-agente.md).

## Edição e reagendamento do rascunho — etapa 11, parte 8

`POST /api/v1/agent/edit` e `POST /api/v1/agent/reschedule` recebem exatamente `{conversation_id}`, sob Bearer, sem query, com `Content-Type: application/json`, corpo de até **1 KiB**, JSON estrito e UUID4 emitido pelo servidor. Herda correlação e `Cache-Control: no-store`. A conversa deve conter a mesma ação completa e pertencer ao escopo autenticado. O diálogo continua preparatório; ID do evento, parâmetros, agenda, versão e `operation_id` vêm do servidor.

O comando explícito pode executar um pedido inequívoco sem confirmação genérica extra. Se houver proposta ativa, exige aprovação exata vinculada à ação/conteúdo/agenda/evento/versão/projeção e reserva seu consumo antes de escrever. O gateway relê a versão e aplica `If-Match`; o executor confere campos solicitados e preservados com nova leitura completa.

Sucesso usa 200/`succeeded`/`confirmed`. Edição retorna `data: {result,calendar_name,before,after,requested,recovered}`; reagendamento retorna `{result,calendar_name,before,after,requested_start,requested_end,time_zone,recovered}`. O `result` distingue `updated`, `rescheduled` e `unchanged`. O envelope conserva a operação estável `agent-update-...`. Conflito do reagendamento usa 409/`conflict`/`not_applied`, com projeção mínima dos compromissos. Resultado incerto usa 502/`operation_not_confirmed`/`unknown`, sem retry automático. Outros códigos seguros distinguem preparação, versão/agenda, contexto e serviço indisponíveis.

O ledger **`data/agent-updates.sqlite3`**, schema **1**, configurável por `AGENT_UPDATE_DATABASE_PATH`, guarda a identidade, o snapshot completo anterior e a versão privadamente. Contexto schema 3 e criação schema 1 continuam separados. Replay de `running`, `unknown` ou `confirmed` somente lê, sem PATCH. `update_previously_confirmed` com `mutation_state: confirmed` descreve o histórico quando o estado esperado não foi reconfirmado; não garante valores atuais nem os reaplica. Vínculo alterado após `not_applied`/`conflict` exige nova intenção conforme os limites do [guia](alteracoes-agente.md); não pode contornar incertezas.

Em 25/10/2026, 1.257 testes Python e 33 casos HTTP TCP locais aprovados, com dois processos e SQLite reais e modelo/gateway/consentimento como fixtures. Zero chamadas reais Google/LLM ou envios WhatsApp; inferência real não foi testada novamente. A exclusão aprovada foi implementada na [parte 9](exclusao-agente.md); integração dinâmica continua na parte 10. Registros 106/107.

## Exclusão da proposta aprovada — etapa 11, parte 9

`POST /api/v1/agent/delete` recebe exatamente `{conversation_id}`, sob Bearer, sem query, com `Content-Type: application/json`, corpo bruto de até **1 KiB**, JSON estrito e UUID4 emitido pelo servidor. Herda correlação e `Cache-Control: no-store`. Exige conversa própria, rascunho `delete` completo e proposta exata explicitamente aprovada, com prazo válido. Não aceita ID de evento, parâmetros, versão, `proposal_id`, token, `confirmed` ou `operation_id` do cliente.

A aprovação é obrigatória para iniciar a exclusão, mesmo com pedido inequívoco e completo. O servidor confere agenda/ID/ETag/projeção e consome a proposta uma vez antes de derivar internamente o token técnico. O gateway revalida agenda/versão, envia `If-Match` e confere ausência; o executor também relê o estado. A rota direta anterior `/events/{event_id}/delete` e seu contrato de token recebido continuam disponíveis.

Sucesso usa HTTP 200/`succeeded`/`confirmed`, com `data: {result,calendar_name,event_id,recovered}`; `result` é `deleted` ou `already_absent`. O envelope conserva a operação estável `agent-delete-...`. Token técnico, ETag, hashes e snapshot aprovado permanecem privados. Falha após possível escrita usa `502/operation_not_confirmed/unknown`, sem retry automático; ausência não é inferida de erro genérico.

O ledger **`data/agent-deletions.sqlite3`**, schema **1**, configurável por `AGENT_DELETION_DATABASE_PATH`, vincula operação a proposta e hash do conteúdo aprovado. Uma proposta tem no máximo uma reserva; `not_applied` exige uma nova proposta/aprovação para nova tentativa. `running`/`unknown` bloqueiam outra proposta ou conteúdo na conversa. Replay somente consulta, sem outro DELETE. Ausência observada confirma estado, não autoria. `delete_previously_confirmed` com `mutation_state: confirmed` descreve o histórico quando a ausência não foi reconfirmada; não exclui novamente. Não há TTL de operações ou recuperação administrativa sem conversa válida. [Contrato e limites completos](exclusao-agente.md).

Em 25/10/2026, 1.367 testes Python e 62 casos HTTP TCP locais aprovados, com dois processos e SQLite reais e modelo/gateway/consentimento como fixtures. Nenhuma chamada real Google/LLM ou envio WhatsApp. Contexto schema 3 e ledgers anteriores schema 1 não foram migrados. Inferência real não foi testada novamente; WhatsApp dinâmico segue na parte 10. Registros 108/109.


## Ativação do agente WhatsApp — etapa 11, parte 10

`POST /api/v1/whatsapp/sandbox/agent` exige Bearer, JSON estrito até 1 KiB, sem query e exatamente `{request_id}`. O UUID4 canônico deve ser igual ao `X-Request-ID`. Não aceita texto, telefone ou parâmetros de agenda. Exige modo agente ativo e trabalho privado já persistido; claim ativo somente de status pode retornar `skipped`.

Envelope v1 de sucesso: `action: whatsapp_agent_turn`, `status: succeeded`, `operation_id: null`, `mutation_state: not_applicable`, `data: {authenticated: true, queue_state: queued|skipped, reused: boolean}`. Ativação reutilizada não repete a reserva; estado incerto ou claim inconsistente é recusado com erro seguro. O modelo/executor/envio não roda no pedido HTTP do n8n. `queued` não comprova sucesso da agenda nem entrega da mensagem.

Receptor e worker usam `WHATSAPP_AGENT_DATABASE_PATH`; envio usa outbox própria. Mensagens do mesmo remetente seguem a ordem e não ultrapassam incerteza. A versão antiga `/reply` permanece disponível como contrato legado. [Fluxo, configuração, autorização, estados e provas](whatsapp-agente.md).


## Coleta factual — etapa 13, parte 2

`GET /api/v1/agenda/context` usa Bearer e aceita somente `period` (week/month, obrigatório) e `date` ISO opcional. Data ausente usa hoje no fuso IANA da agenda autorizada; não aceita escolha de agenda ou fuso pelo cliente. Produz snapshot completo ou falha, com totais/dias de todo o período e até dez detalhes, omissões explícitas e sem IDs reais. A ação é `collect_agenda_context`, com `mutation_state: not_applicable` e `execution_allowed: false`. Não chama IA nem altera contexto/confirmações. [Contrato, limites e erros](coleta-contexto-agenda.md). API verificada em ASGI; o processo persistente anterior precisa do restart normal para carregar a nova rota.


## Resumo estrutural com IA — etapa 13, parte 3

`POST /api/v1/agenda/summary` recebe somente período/data em JSON, com Bearer, sem query e sem contexto/prompt/ID do cliente. Coleta fatos atuais antes do modelo. Retorna `summary.mode: ai_organized` quando um plano estrutural foi aceito; `factual` e motivo controlado quando vazio/limite de contexto/falha de IA. Fonte não confirmada retorna erro sem resumo. `mutation_state: not_applicable` e `execution_allowed: false`. [Contrato, exemplos e provas](resumos-com-ia.md). A API foi verificada em ASGI; o processo persistente anterior não foi reiniciado neste marco.


## Horários livres — etapa 13, parte 4

`POST /api/v1/agenda/availability` exige Bearer e JSON estrito com `date_from`, `date_to` inclusivas (até31dias), `window_start`, `window_end`, `duration_minutes` inteiro e `buffer_minutes` opcional. Agenda/fuso são do servidor; não há query, seleção de outra agenda ou exclusão de ID pelo cliente. `action: find_free_slots`, `mutation_state: not_applicable`, `execution_allowed: false`, `reservation_created: false`. As até três opções são alternativas, não reservas; `more_available` sinaliza uma quarta. Falha de ocupação retorna erro sem opções. [Contrato e provas](horarios-livres.md). Rota testada em ASGI; processo persistente anterior não foi reiniciado neste marco.

## Preparo compacto da entrevista

`POST /api/v1/preparation/briefing` recebe JSON `{event_id?,user_context?}` sob Bearer, sem query/campos extras ou chaves repetidas. ID até 1.024 caracteres; contexto texto até 1.200; corpo até 8.192 bytes e dois segundos. Faz seleção exata/releitura, sem modelo ou escrita. `ready` e `needs_clarification` usam HTTP 200; entrada inválida 422, setup 503 e falha de fonte 502 sem preparo parcial. Ação `prepare_briefing`, operação nula, mutação `not_applicable`, `mode: factual_template`, `execution_allowed: false`. [Formato, fatos, checklist e provas](preparo-entrevista.md). Rota testada em ASGI; processo persistente não reiniciado neste bloco.
