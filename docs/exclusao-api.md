# Exclusão pela API — etapa 09, parte 6

A exclusão usa duas chamadas separadas: consultar uma prévia e executar após a confirmação explícita do usuário. As rotas reutilizam `EventDeletionService` e o conector existentes, na agenda configurada no Python. A montagem do fluxo n8n permanece pendente.

## Executar e autorizar

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe -m app
```

Se a API já estiver aberta, encerrar com `Ctrl+C` e iniciar novamente. Abrir `http://127.0.0.1:8000/docs`, selecionar **Authorize** e informar somente a chave privada de `API_AUTH_TOKEN`; o Swagger acrescenta Bearer. Não habilitar persistência da autorização e usar **Logout** ao terminar. Consulte a [proteção da API](seguranca-api.md).

Usar `Content-Type: application/json`. A credencial pertence ao header `Authorization`, nunca à URL ou ao corpo. O ID deve ser exatamente o retornado na [consulta](consultas-api.md), preservando caixa e ocorrências; codificá-lo como componente da URL, inclusive barras como `%2F`.

## 1. Obter e apresentar a prévia

`POST /api/v1/events/{event_id}/deletion-preview`

O único campo aceito é obrigatório:

```json
{
  "time_zone": "America/Recife"
}
```

No Swagger, **Try it out → Execute** nesta rota consulta a versão atual e **não exclui o evento**. Em HTTP 200, o envelope usa `action: delete`, `operation_id: null` e `mutation_state: not_applied`.

| Situação | Resposta |
|---|---|
| Evento disponível | `status: needs_confirmation`; `data` contém `result: needs_confirmation`, `calendar_name`, `event`, `confirmation_token` e `question`. |
| Ausência confirmada | `status: succeeded`; `data.result: already_absent`, com `event`, `confirmation_token` e `question` nulos. Não executar a segunda chamada. |

O snapshot `event` expõe somente oito campos: `event_id`, `title`, `start`, `end`, `all_day`, `time_zone`, `status` e `html_link`. A API confere a identidade, os horários, o estado e a pergunta antes de publicá-los. Não inclui descrição, local, ETag ou informações privadas do conector.

**Apresentar título, data, horário/fuso e a pergunta ao usuário. Aguardar a confirmação explícita desse evento antes de prosseguir.** Guardar temporariamente o ID, o fuso e o token exatos dessa prévia para a segunda chamada.

## 2. Executar a exclusão confirmada

`POST /api/v1/events/{event_id}/delete`

Os dois campos são obrigatórios. O código abaixo é apenas ilustrativo: substituir os zeros pelo token completo retornado na prévia aprovada.

```json
{
  "confirmation_token": "0000000000000000000000000000000000000000000000000000000000000000",
  "time_zone": "America/Recife"
}
```

Enviar o token intacto, para o mesmo ID e fuso. **Execute nesta rota pode remover o evento da agenda.** O executor não gera uma prévia, não fabrica tokens e não trata `confirmed: true` como confirmação.

Sucesso retorna HTTP 200, `status: succeeded`, `action: delete`, `operation_id: null` e `mutation_state: confirmed`. `data` contém exatamente `result`, `calendar_name`, `event_id` e `recovered`; `result` é `deleted` ou `already_absent`. O token, a pergunta e o snapshot não aparecem nessa resposta. `recovered: true` indica que uma resposta incerta foi esclarecida por leitura e o resultado confirmado.

O conector consulta a versão, envia `If-Match` no DELETE e usa `sendUpdates=none`, sem enviar convites ou notificações aos convidados. Uma leitura posterior confirma a ausência. Isso não transforma o serviço em exclusão de séries inteiras: selecionar a ocorrência específica e respeitar as restrições já existentes para tipos de evento.

## Validação e vínculo da confirmação

Ambas as rotas exigem strings estritas, texto codificável em UTF-8, ID válido e fuso IANA válido. Recusam query e campos extras, inclusive `execute`, `confirmed`, `operation_id`, `calendar_id` e `user_id`. O executor exige `confirmation_token` com exatamente 64 caracteres hexadecimais minúsculos. Entrada inválida falha antes de abrir o conector.

O token corresponde à agenda, ao ID e ao ETag da versão consultada. **É um código de versão; não é credencial de autenticação nem prova de consentimento humano.** Não possui prazo de expiração por tempo nesta implementação. Sua validade depende da versão atual; se o evento mudar, obter outra prévia e pedir nova aprovação antes de executar. A IA ou o workflow não deve produzir ou renovar o token para contornar a confirmação.

Um token com formato válido, mas incorreto ou antigo, é rejeitado pelo conector antes de enviar DELETE. Como a exceção operacional atual não transporta um estado tipado para esse caso, a API publica conservadoramente 502/`unknown`. Não interpreta o texto da exceção para afirmar ausência, inventar `404` ou classificar `409 stale_version`.

## Falhas, privacidade e repetição

| HTTP | Código e situação | Estado da mutação |
|---|---|---|
| 422 | `invalid_request`: JSON, estrutura, texto, ID, fuso, formato do token ou query inválidos. | `not_applied` |
| 401 | `unauthorized`: chave da API ausente ou inválida. | `not_applied` |
| 503 | `api_auth_unavailable`: chave da própria API não configurada. | `not_applied` |
| 503 | `calendar_setup_unavailable`: `CalendarSetupError` na prévia. | `not_applicable` |
| 502 | `operation_failed`: falha operacional de leitura ou resultado inválido na prévia. | `not_applicable` |
| 503 | `calendar_setup_unavailable`: falha de configuração ao abrir o conector, antes de iniciar a execução. | `not_applied` |
| 502 | `operation_not_confirmed`: falha após iniciar o executor ou resultado de exclusão não confirmado. | `unknown` |

Cada requisição abre um cliente não interativo e o encerra antes da resposta. Falha genérica ao encerrar a prévia permanece 502 de leitura; `CalendarSetupError` tipado na prévia segue 503. No executor, qualquer falha depois de iniciar o serviço, inclusive configuração ou encerramento do cliente, permanece 502/`unknown`. A API não confirma sucesso antes do fechamento.

Erros retornam dados nulos e nomes de campos permitidos, sem reproduzir entradas, token, nomes de propriedades desconhecidas ou exceções brutas. Em falhas de autenticação, `action` permanece `null`. O framework pode identificar JSON malformado antes da autenticação; nenhum erro de parsing abre o conector.

**Desabilitar repetição automática das duas chamadas**, sobretudo da execução após timeout. Todas as falhas estruturadas têm `retryable: false`. Diante de resultado incerto, consultar o ID e conferir o estado antes de decidir outra ação; se o evento ainda existir, obter nova prévia e nova confirmação. A camada HTTP não acrescenta outro DELETE ou retentativas automáticas à recuperação já existente.

As respostas usam `Cache-Control: no-store`. O início padrão da API desabilita logs de acesso; manter corpos, respostas com dados de eventos e códigos de confirmação fora de logs, capturas públicas e Git. Esses códigos não substituem Bearer. OAuth continua não interativo e usa as permissões já configuradas; a leitura pode atualizar metadados locais sem modificar os eventos.

## Integração posterior com n8n

O workflow deverá guardar Bearer em credencial privada, apresentar a prévia e persistir qual usuário aprovou qual evento, versão e intenção antes de acionar o executor. Essa gestão persistente de consentimento ainda não está implementada pela API. Receber um token em formato correto não comprova que houve aprovação humana, e a rota não cria essa aprovação por conta própria.

Consulte também o [contrato v1](contrato-api-v1.md) e as [rotas de alteração](alteracoes-api.md).

## Verificação em 22/10/2026

As duas rodadas completas aprovaram **444 testes**, incluindo **20 novos de exclusão HTTP**. Os percursos usam ASGI, serviços, SDK/transporte e SQLite reais com o HTTP Google simulado. Cobrem a exclusão completa, prévia sem escrita, códigos vinculados a agenda/ID/versão, concorrência/412, resposta perdida, leitura final e repetição sem segundo DELETE. Nenhuma conexão da aplicação foi tentada. O registro **058** preserva os resultados locais.

No Google real, a prévia apresentou a entrevista fictícia **Entrevista técnica — API atualizada (teste)** de **23/10/2026, 18h–18h30, America/Recife**. Chamada sem chave retornou 401; código ausente/malformado e atalhos `execute`/`confirmed` retornaram 422 antes do conector. Um código bem formado incorreto retornou 502 seguro, sem DELETE. O transporte permitiu somente GET: foram **8 leituras**, com os três eventos idênticos antes/depois.

A verificação foi encerrada mantendo a entrevista. Não houve exclusão real por esses endpoints; a remoção foi exercitada somente com provedor simulado. O código temporário da prévia manual foi descartado. Para remover esse evento no futuro, obter outra prévia, apresentar os dados e receber autorização específica. O registro **059** preserva as respostas reais com o código omitido. `.env`, cliente OAuth e banco da aplicação permaneceram iguais. O token OAuth foi atualizado pela renovação normal da autorização. O servidor temporário foi encerrado; n8n ainda não foi conectado. Os JSONs comprovam o marco sem nova captura de interface.
