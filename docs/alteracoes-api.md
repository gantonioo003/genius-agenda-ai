# Alterações pela API — etapa 09, parte 5

As rotas PATCH editam e reagendam eventos da agenda configurada no Python. Reutilizam os serviços existentes, com validação local, verificação da versão e leitura posterior do resultado. A integração com n8n permanece pendente.

## Executar e autorizar

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe -m app
```

Se a API já estiver aberta, encerrar com `Ctrl+C` e iniciar novamente. Abrir `http://127.0.0.1:8000/docs`, selecionar **Authorize** e informar somente a chave privada de `API_AUTH_TOKEN`; o Swagger acrescenta Bearer. Não habilitar persistência da autorização e usar **Logout** ao terminar. Consulte a [proteção da API](seguranca-api.md).

Selecionar a rota, usar **Try it out**, informar o ID e preencher o corpo JSON. **Execute realiza a alteração na agenda**. Enviar `Content-Type: application/json`; a credencial pertence ao header `Authorization`, nunca ao corpo ou à URL.

O ID é opaco: usar exatamente o valor da consulta, preservando caixa e identificadores de ocorrências. Codificá-lo como componente da URL, inclusive barras como `%2F`. O sufixo `/schedule` é reservado ao reagendamento e sua rota é registrada antes da edição genérica; um caminho terminado nesse sufixo será interpretado como subrecurso de horário.

## Reagendar

`PATCH /api/v1/events/{event_id}/schedule`

Todos os campos abaixo são obrigatórios. Exemplo fictício, para um evento já existente:

```json
{
  "start": "2030-06-17T11:00:00-03:00",
  "end": "2030-06-17T11:30:00-03:00",
  "time_zone": "America/Recife"
}
```

Início e fim exigem ISO 8601 com offset UTC, duração positiva e coerência entre data, horário, offset e fuso IANA. As regras também consideram transições de horário de verão. O conector verifica conflitos antes da escrita e exclui o próprio evento dessa comparação. Esta operação não converte eventos de dia inteiro em eventos com horário.

## Editar informações

`PATCH /api/v1/events/{event_id}`

`changes` e `time_zone` são obrigatórios. Exemplo fictício:

```json
{
  "changes": {
    "title": "Entrevista técnica — segunda etapa",
    "description": "Preparar exemplos de projetos anteriores.",
    "location": "Videoconferência"
  },
  "time_zone": "America/Recife"
}
```

| Campo em `changes` | Regra |
|---|---|
| Omitido ou `null` | Preserva o valor atual. |
| `title` | Quando informado, deve conter texto; espaços nas extremidades são removidos. Vazio ou somente espaços é inválido. |
| `description` e `location` | Aceitam texto; `""` limpa o campo. |

É necessário informar pelo menos um campo diferente de `null`; um valor já igual ao atual pode produzir `unchanged`. Para limpar somente o local, por exemplo, enviar `"changes": {"location": ""}` e manter `time_zone` no corpo.

As duas rotas exigem strings estritas e Unicode codificável em UTF-8. Recusam parâmetros de query e propriedades desconhecidas, inclusive dentro de `changes`. Não aceitam `operation_id`, opções de execução, convidados, `calendar_id` ou `user_id`. A seleção permanece limitada à agenda configurada no servidor.

## Confirmação e dados publicados

As respostas seguem o [contrato v1](contrato-api-v1.md). Em sucesso: HTTP 200, `status: succeeded`, `mutation_state: confirmed`, `error: null` e `operation_id: null`. A ação é `reschedule` ou `edit`.

| Ação | Conteúdo de `data` |
|---|---|
| Reagendamento | `result`, `calendar_name`, `before`, `after`, `requested_start`, `requested_end`, `time_zone`, `recovered`. `result` é `rescheduled` ou `unchanged`. |
| Edição | `result`, `calendar_name`, `before`, `after`, `requested`, `recovered`. `result` é `updated` ou `unchanged`. |

Os snapshots de reagendamento expõem somente os oito campos de `AgendaEvent`: `event_id`, `title`, `start`, `end`, `all_day`, `time_zone`, `status` e `html_link`. Os snapshots de edição acrescentam apenas `description` e `location`, totalizando dez campos. Na edição, o título conserva o valor real, que pode estar vazio ou conter somente espaços em um evento anterior; isso não permite enviar um novo título vazio.

A API confere a identidade, o estado do resultado e os valores solicitados antes de confirmar sucesso. Na edição, também confere os horários e a preservação de título, descrição e local quando não solicitados. `recovered: true` significa resultado confirmado por leitura após uma resposta incerta, não resultado desconhecido.

O conector mantém ETag e `If-Match` internamente para proteger a escrita contra mudanças concorrentes e faz leitura de confirmação depois de alterar. O cliente não envia esses cabeçalhos ao Google nem fabrica uma confirmação. A conferência manual posterior pode usar as [consultas GET](consultas-api.md).

## Falhas e repetição

| HTTP | Código e situação | Estado da mutação |
|---|---|---|
| 409 | `schedule_conflict`: somente `CalendarConflictError` tipado no reagendamento, com relatório válido de conflito antes da escrita. | `not_applied` |
| 422 | `invalid_request`: JSON, estrutura, texto, ID, horários, fuso ou query inválidos, antes de abrir o conector. | `not_applied` |
| 401 | `unauthorized`: chave da API ausente ou inválida. | `not_applied` |
| 503 | `api_auth_unavailable`: chave da própria API não configurada. | `not_applied` |
| 503 | `calendar_setup_unavailable`: `CalendarSetupError` na entrada do conector, antes de iniciar o serviço. | `not_applied` |
| 502 | `operation_not_confirmed`: falha operacional, DTO inconsistente ou falha após iniciar o serviço, inclusive ao encerrar o cliente. | `unknown` |

Em autenticação, `action` permanece `null`; `operation_id` é sempre `null` nestas operações. JSON malformado pode ser identificado pelo framework antes da autenticação, mas nenhum erro de parsing ou validação abre o conector. Erros publicam somente nomes de campos permitidos, sem reproduzir entradas, propriedades desconhecidas ou exceções brutas.

O relatório 409 inclui agenda, intervalo, fuso e eventos conflitantes com apenas os oito campos públicos. Falha ao validar esse relatório ou ao fechar o cliente continua sendo 502/`unknown`. Uma falha de configuração ocorrida depois de iniciar o serviço também recebe 502.

As exceções operacionais atuais não distinguem de forma estruturada evento ausente, versão alterada e outras falhas. PATCH não transforma o texto dessas exceções em 404 ou 409 e não afirma que uma escrita deixou de acontecer. Em 502, os dados de resultado ficam nulos.

**Desabilitar repetição automática de PATCH**, inclusive após timeout. Conferir o estado atual antes de decidir outra tentativa. Todas as falhas estruturadas usam `retryable: false`; estas rotas não acrescentam envios automáticos nem idempotência persistente geral para alterações.

## Persistência e integração

O ledger de [criação](criacao-api.md) permanece preservado: alterações não substituem sua identidade ou reescrevem a intenção original. Repetir POST com uma operação de criação confirmada só reutiliza o evento quando seus dados ainda correspondem ao pedido original. Se o evento foi editado ou reagendado, essa repetição é recusada: não sobrescreve os dados nem cria outro evento. Não usar POST para desfazer alterações.

Cada requisição abre um cliente não interativo e o encerra antes da resposta. Não há novo escopo OAuth ou mudança de permissões nesta parte. Problemas de autorização seguem a configuração existente, sem abrir login a partir do PATCH. Respostas v1 usam `Cache-Control: no-store`; credenciais e banco continuam privados.

A montagem dos fluxos n8n será feita nas próximas partes. O workflow deverá guardar a chave em credencial privada, apresentar os resultados confirmados e tratar falhas incertas sem reenviar alterações automaticamente.

## Verificação em 22/10/2026

As duas rodadas completas aprovaram **424 testes**, dos quais **22 novos de alterações HTTP**. Os testes percorrem ASGI, serviços, SDK, transporte e SQLite com o HTTP Google simulado; nenhuma conexão da aplicação foi tentada. Incluem concorrência/412, ausência, resposta perdida, resultado incerto, campos preservados e edição de eventos originalmente sem título. O registro **056** reúne essa validação local.

No Google real, somente a entrevista fictícia criada pela API foi alterada: reagendamento para **23/10/2026, 18h–18h30, America/Recife**, seguido da atualização de título, descrição e local. Ambos retornaram 200 com confirmação. Repetir os pedidos retornou `unchanged`, sem nova gravação. A tentativa de reagendar para um horário ocupado retornou 409 e preservou o evento.

Foram **dois PATCHs e 24 GETs reais** no Calendar, com `If-Match` e `sendUpdates=none` conferidos. A agenda manteve os mesmos três IDs e os outros dois eventos ficaram iguais. O registro de criação permaneceu intacto, com backup íntegro antes da conferência. `.env` e cliente OAuth permaneceram iguais. O token OAuth também permaneceu igual. Os 10 clientes foram fechados e o servidor temporário encerrado. O registro **057** preserva as respostas reais, sem injeção de falha; não foi necessária uma captura de interface.

A parte 6 acrescentou as [rotas de prévia e exclusão com confirmação](exclusao-api.md). A fixture usada nas alterações foi preservada no Google; somente a prévia e os bloqueios foram conferidos nessa conta.
