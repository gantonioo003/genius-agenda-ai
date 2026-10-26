# Criação pela API — etapa 09, parte 4

`POST /api/v1/events` cadastra uma entrevista na agenda configurada no Python. A rota exige Bearer e reutiliza `InterviewService` e o conector existentes: as regras de horários, conflitos e deduplicação também se aplicam às chamadas HTTP.

## Executar e conferir

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe -m app
```

Se o servidor já estiver aberto, encerrá-lo com `Ctrl+C` e iniciar novamente para carregar as alterações. Abrir `http://127.0.0.1:8000/docs`, selecionar **Authorize** e informar somente a chave privada de `API_AUTH_TOKEN`; o Swagger acrescenta o esquema Bearer. Não habilitar persistência da autorização e usar **Logout** ao terminar. Consulte a [proteção da API](seguranca-api.md).

No Swagger, abrir **POST /api/v1/events**, selecionar **Try it out** e preencher o corpo. **Execute realiza a criação na agenda**. O exemplo abaixo é fictício; escolher um intervalo livre antes de executá-lo:

```json
{
  "operation_id": "interview-intent-20300617-001",
  "event": {
    "title": "Entrevista técnica — demonstração",
    "start": "2030-06-17T10:00:00-03:00",
    "end": "2030-06-17T10:30:00-03:00",
    "time_zone": "America/Recife",
    "description": "Entrevista fictícia para demonstrar a criação pela API."
  }
}
```

O cliente deve enviar `Content-Type: application/json`. A chave pertence ao header `Authorization`, nunca ao corpo ou à URL. A conferência posterior usa as [consultas GET](consultas-api.md).

## Identidade da operação e validação

Criar e persistir o `operation_id` **antes do primeiro envio**. Ele identifica a intenção de criação, enquanto `X-Request-ID` identifica uma tentativa HTTP. Conservar exatamente o mesmo `operation_id` entre tentativas da mesma intenção; não gerar outro a cada execução do fluxo. Uma intenção com dados alterados exige outra operação. Reutilizar o identificador não autoriza repetição automática após falha ou timeout.

O corpo aceita somente `operation_id` e `event`. Todos os valores de texto exigem strings válidas em UTF-8, sem conversão automática de números, booleanos ou `null`:

- `operation_id`: entre 1 e 128 caracteres, com conteúdo além de espaços. O valor é preservado exatamente, sem aparar ou normalizar.
- `event.title`: obrigatório, com conteúdo além de espaços.
- `event.start` e `event.end`: obrigatórios, em ISO 8601 com offset UTC. O fim deve ocorrer depois do início; os horários e offsets devem corresponder ao fuso informado, inclusive em transições de horário de verão.
- `event.time_zone`: obrigatório, com zona IANA válida, como `America/Recife`.
- `event.description`: string opcional, com padrão `""`. `null` é inválido.

Campos extras são recusados no corpo e em `event`; parâmetros na query também são recusados. Esta rota não interpreta linguagem natural e não aceita convidados, `calendar_id` ou `user_id`. A seleção continua limitada à agenda configurada no programa.

Autenticação e validação local antecedem a abertura do conector. JSON malformado pode ser identificado pelo framework antes da dependência de autenticação; nessa rota, a resposta também usa o envelope seguro de validação. Os erros publicam somente nomes de campos permitidos. Campos desconhecidos aparecem como `body`, `event` ou `query`, sem repetir nomes e valores recebidos.

## Confirmação e falhas

As respostas seguem o [contrato v1](contrato-api-v1.md), com `action: create` e o `operation_id` conhecido. A confirmação contém somente estes cinco campos em `data`:

```json
{
  "event_id": "EVENT_ID_FICTICIO",
  "html_link": "https://example.invalid/event",
  "calendar_name": "Genius Testes",
  "reused": false,
  "recovered": false
}
```

`reused` indica reutilização de uma criação correspondente. `recovered` indica que o conector confirmou o resultado por leitura após uma resposta incerta; não significa resultado desconhecido. Descrição e dados privados do conector não entram nesse DTO, mesmo quando o resultado interno contém mais campos.

| HTTP | Código ou resultado | Estado da mutação |
|---|---|---|
| 201 | Criação nova confirmada, inclusive com `recovered: true`. | `confirmed` |
| 200 | Criação correspondente reutilizada, com `reused: true`. | `confirmed` |
| 409 | `schedule_conflict`: `CalendarConflictError` tipado durante a criação, com relatório de conflito válido. | `not_applied` |
| 422 | `invalid_request`: estrutura, texto, datas, fuso ou parâmetros inválidos, antes de abrir o conector. | `not_applied` |
| 401 | `unauthorized`: chave da API ausente ou inválida. | `not_applied` |
| 503 | `api_auth_unavailable`: chave da própria API não configurada no servidor. | `not_applied` |
| 503 | `calendar_setup_unavailable`: falha de configuração na entrada do conector, antes de iniciar a criação. | `not_applied` |
| 502 | `operation_not_confirmed`: falha após iniciar a criação, resultado inválido, erro operacional ou falha ao encerrar o conector. | `unknown` |

Erros de autenticação mantêm `action: null`. Uma falha de configuração ocorrida após iniciar a criação também recebe 502: a API não afirma que nada foi escrito. Em 502, o `operation_id` conhecido permanece no envelope e `data.event_id` pode ser `null`.

O conflito 409 inclui `data.status: conflict`, nome da agenda, início, fim, fuso e `conflicts`. Cada evento conflitante expõe somente os oito campos públicos de `AgendaEvent`; não são incluídos descrição, local ou propriedades privadas. A API não analisa o texto de uma exceção para inventar um conflito.

Todos os erros estruturados têm `retryable: false`. **Desabilitar repetição automática de POST**, inclusive após timeout. O conector preserva as verificações de idempotência e recuperação já existentes; a camada HTTP não acrescenta outro envio de criação. Conferir o estado antes de decidir uma nova tentativa. Respostas v1 usam `Cache-Control: no-store` e não expõem exceções brutas, payloads ou credenciais.

## Persistência e integração

A deduplicação depende da mesma agenda e do SQLite persistente, normalmente `data/agenda.db`. A identidade canônica do conteúdo pode permitir reutilização mesmo com outro `operation_id`. Uma criação já confirmada não é recriada apenas porque seu evento foi posteriormente removido.

Preservar o banco entre execuções e manter backup consistente: parar os processos que escrevem nele antes da cópia, ou usar a API de backup do SQLite. Banco, backups e credenciais são dados privados e permanecem fora do Git. A inicialização e o acesso ao banco não devem ser substituídos por um arquivo temporário em cada execução de produção.

A chamada ao Google é não interativa, com um cliente por requisição. Problemas de autorização devem ser resolvidos pelo procedimento de configuração existente; o POST não abre uma janela de login. Não há nova dependência ou novo escopo de permissão nesta parte.

O fluxo n8n continua pendente. Quando implementado, deve persistir a identidade da intenção, guardar a chave em sua credencial privada e tratar o envelope e o estado da mutação antes de prosseguir. As [rotas HTTP de alteração](alteracoes-api.md) foram implementadas na parte 5. A [exclusão confirmada](exclusao-api.md) foi implementada na parte 6; preparação permanece para a próxima parte.

## Verificação em 22/10/2026

As rodadas no staging e no repositório aprovaram **402 testes**, incluindo **19 novos de criação HTTP**. A cobertura inclui autenticação, validação antes do conector, criação/reutilização/recuperação, conflito tipado, falhas com resultado incerto, encerramento do cliente e proteção dos dados expostos. Esses testes usam provedores controlados; o registro **054** preserva a validação local.

Também conferimos o servidor HTTP com o Google real: a primeira criação retornou 201; repetir a mesma operação retornou 200 com `reused: true`; outra operação com conteúdo idêntico também reutilizou o evento. As três chamadas produziram **um único POST de criação na Calendar API**.

A agenda passou de dois para três eventos, preservando os dois anteriores. O evento fictício **Entrevista técnica — API Python (teste)** ficou marcado para **23/10/2026, das 17h às 17h30, em `America/Recife`**, sem convidados, e foi mantido para conferência. Um pedido sobreposto às 15h10 retornou 409 sem POST ao Google; entradas inválidas retornaram 422 e a chamada sem chave retornou 401.

A validação usou o SQLite persistente e conferiu a integridade do backup. O pedido recusado por conflito deixou somente o registro local pendente, sem criar outro evento. O registro **055** reúne as respostas HTTP e a contagem de chamadas reais, separadamente dos testes simulados. Os resultados em JSON demonstram a operação; não foi produzida captura de interface para esta microetapa.

Na parte 5, a mesma fixture foi editada e reagendada para 23/10/2026, 18h–18h30. O histórico de criação foi preservado; o registro 055 continua demonstrando o estado anterior a essas alterações.
