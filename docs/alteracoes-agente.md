# Editar e reagendar pelo agente — etapa 11, parte 8

Atualização da parte 10, em 25/10/2026: o [orquestrador WhatsApp](whatsapp-agente.md) passou a reutilizar este contrato no fluxo n8n, com contexto privado, fila e saída reservada. As descrições de validação abaixo pertencem aos marcos originais; os testes de mutação da parte 10 também usam provedores fictícios. [Gemini real](gemini-api.md) tem prova separada.

O backend executa uma edição ou um reagendamento a partir do pedido completo já preparado na conversa. Ele relê o evento correto, vincula a agenda e a versão, aplica os serviços existentes e confere o resultado. A execução fica registrada antes de chamar o Google; repetir uma operação em andamento, incerta ou confirmada consulta seu estado sem enviar outro PATCH.

O [diálogo](esclarecimentos-agente.md) continua preparatório. `ready` informa que os dados estão completos e mantém `execution_allowed: false`; não dispara uma alteração automaticamente. A ligação desse comando ao texto e à resposta dinâmica do WhatsApp/n8n permanece na parte 10 do [escopo do agente](agente-ia.md).

## Preparação e comando explícito

1. Obter uma conversa emitida pelo servidor e preparar o pedido em `/api/v1/agent/dialogue`.
2. Esclarecer os campos faltantes e selecionar explicitamente um evento quando houver mais de um candidato.
3. Conferir o identificador completo e os parâmetros do pedido `ready`.
4. Acionar o comando autenticado correspondente à ação solicitada.

Um pedido inequívoco e completo pode autorizar esse comando, sem uma segunda confirmação genérica. A camada que chama a API deve vincular a execução à intenção explícita do usuário. Um modelo não recebe ferramenta livre de escrita nem escolhe os identificadores técnicos da operação.

Se houver uma proposta de confirmação ativa, ela não pode ser ignorada. Deve estar aprovada, válida e vinculada à ação, aos parâmetros exatos, à agenda, ao evento e à sua versão atual. A projeção mínima do evento também é conferida. O servidor reserva o consumo dessa aprovação uma única vez antes da alteração. Proposta pendente, revogada, expirada, consumida ou divergente não libera uma nova execução. [Regras de confirmação](confirmacoes-agente.md).

A repetição de uma operação já registrada segue para reconciliação, sem consumir novamente a aprovação. Uma falha depois do consumo não autoriza emitir outra aprovação para contornar um resultado incerto.

## Contrato HTTP

Os caminhos abaixo usam o prefixo `/api/v1`:

| Comando | Entrada | Efeito |
|---|---|---|
| `POST /agent/edit` | Somente `conversation_id` | Editar título, descrição e/ou local do evento no rascunho. |
| `POST /agent/reschedule` | Somente `conversation_id` | Alterar início e término do evento no rascunho. |

Ambos exigem Bearer, `Content-Type: application/json`, corpo bruto de até **1 KiB**, UUID4 canônico de conversa emitida pelo servidor, correlação `X-Request-ID` e resposta `Cache-Control: no-store`. Query string, campos extras, chaves JSON repetidas e JSON fora do contrato são recusados antes da execução.

```json
{
  "conversation_id": "<UUID4 da conversa emitido pelo servidor>"
}
```

O marcador é ilustrativo. O cliente não envia título, horários, `changes`, ID de evento, agenda, versão, aprovação ou `operation_id` nesse corpo. Esses dados vêm do contexto privado validado. A conversa deve pertencer ao escopo da chave, estar disponível e conter a mesma ação do endpoint, sem campos aguardando esclarecimento ou candidatos pendentes.

A API devolve o [envelope v1](contrato-api-v1.md) com `action: edit` ou `reschedule`. O `operation_id` é gerado no servidor com prefixo `agent-update-`, vinculado ao proprietário, à conversa, à ação e aos parâmetros canônicos. A versão e a agenda são guardadas na reserva; mudar esses valores não gera silenciosamente outra identidade.

| HTTP / resultado | Estado de mutação | Significado |
|---|---|---|
| 200 / `succeeded` | `confirmed` | O estado solicitado foi conferido; pode ter sido atualizado, reagendado, já estar igual ou ter sido reconciliado. |
| 409 / `conflict` | `not_applied` | O reagendamento encontrou compromisso sobreposto, sem aplicar o novo horário. |
| 409 / `failed` | Conforme o caso | Pedido, aprovação, revisão, agenda, versão ou operação anterior impedem continuar. |
| 502 / `failed` | `unknown` ou `not_applied` | Uma leitura ou o resultado da operação não pôde ser confirmado; o código diferencia a fase. |
| 422, 404 ou 503 / `failed` | Conforme o caso | Entrada inválida, conversa indisponível ou dependência local/configuração indisponível. |

O sucesso de edição contém `result`, `calendar_name`, `before`, `after`, `requested` e `recovered`. Os snapshots incluem descrição e local porque sua preservação faz parte da edição. O sucesso de reagendamento contém `result`, `calendar_name`, projeções mínimas `before`/`after`, `requested_start`, `requested_end`, `time_zone` e `recovered`; descrição e local são conferidos internamente, sem ampliar essa resposta. `result` diferencia `updated`, `rescheduled` e `unchanged`.

Falhas conhecidas usam códigos seguros e `retryable: false`, sem refletir SQL, caminhos privados, credenciais ou a resposta bruta do provedor. Resultados incertos podem informar somente o ID do evento. `update_previously_confirmed` conserva o histórico confirmado, mas informa que a consulta atual não reconfirmou aquele estado; não significa que o compromisso continua com os mesmos valores nem provoca reaplicação.

## Agenda, evento e versão vinculados

O [snapshot privado](../app/services/update_snapshot.py) lê o evento por ID exato e reúne agenda, ETag, título, descrição, local, início, término, fuso, estado, link e transparência. Uma leitura saudável faz um GET do evento. Ausência, evento cancelado, série inteira, tipo não suportado ou resposta inválida não viram um snapshot executável. Uma falha de leitura também não é convertida em ausência confirmada.

Depois da reserva, o [executor](../app/services/agent_updates.py) relê a revisão e a memória da conversa. Com proposta, usa o consumo atômico da aprovação correspondente. Não mantém uma transação do contexto ou do ledger aberta durante a chamada ao Google.

O [gateway](../app/google_calendar.py) recebe internamente `expected_calendar_id` e `expected_version`. Ele compara a agenda resolvida e a versão de uma nova leitura antes de montar o PATCH. Se houver mudança, interrompe a escrita. Quando prossegue, envia `If-Match` com a ETag conferida. Uma alteração externa entre essa leitura e o PATCH é recusada pelo controle condicional do Google, sem sobrescrever a versão nova. Não há retentativa automática de gravação.

## Regras preservadas

A edição reutiliza [EventDetailsService](../app/services/event_details.py):

- Altera somente `title`, `description` ou `location` solicitados.
- Campo omitido preserva seu valor. Descrição ou local `""` limpam o campo; título vazio é inválido.
- Um título original vazio pode ser lido e preservado quando o pedido altera outro campo.
- Horários, dia inteiro, fuso e todos os campos não solicitados devem permanecer correspondentes ao snapshot anterior.

O reagendamento reutiliza [RescheduleService](../app/services/rescheduling.py):

- Exige início e término com offset coerente com o fuso IANA e intervalo positivo.
- Verifica conflitos para eventos que ocupam horário, excluindo o próprio ID da busca. Um intervalo novo que se sobrepõe ao horário anterior do próprio evento pode ser válido.
- Preserva título, descrição, local, estado, link e transparência; não escolhe outro horário automaticamente.
- Não reagenda eventos de dia inteiro nem uma série inteira. Uma ocorrência identificada pode ser tratada pelas restrições existentes.

Os comandos continuam sem participantes, convites, alteração de recorrência ou escolha de outra agenda. O PATCH usa `sendUpdates=none`. As regras existentes não passam a impor uma duração máxima geral ou proibição geral de datas passadas.

Depois do retorno dos serviços, o executor faz uma leitura completa e compara os campos pedidos e preservados. Um recibo parcial, outro ID, intervalo divergente ou campo preservado alterado não é apresentado como sucesso. Se a escrita já pode ter ocorrido, o resultado fica incerto.

## Registro durável e repetição

O [ledger de alterações](../app/agent_update_store.py) é um SQLite separado, por padrão **`data/agent-updates.sqlite3`**, configurável por `AGENT_UPDATE_DATABASE_PATH`. Caminhos relativos são resolvidos na raiz do projeto. O schema inicial é **1**; os bancos de contexto e de criações continuam separados.

O registro privado conserva a ação, os parâmetros exatos, a agenda, a versão, o snapshot completo anterior, a transparência, a identidade da tentativa, o estado e o recibo confirmado. Contém informações legíveis localmente e não é criptografado pela aplicação. Deve permanecer fora do Git, assim como seus backups; não publique bases reais como evidência.

| Estado interno | Tratamento |
|---|---|
| `running` | Uma tentativa assumiu o pedido; repetições somente consultam. |
| `unknown` | A mudança pode ter sido aplicada; repetições somente consultam. |
| `confirmed` | O resultado já foi conferido; repetir relê o estado, sem reaplicar. |
| `not_applied` | A execução parou com evidência anterior à escrita; uma tentativa posterior precisa passar novamente pelas verificações e manter o vínculo original. |
| `conflict` | O horário não estava disponível; uma nova tentativa precisa preservar o vínculo e conferir os conflitos atuais. |

A reserva é persistida antes do PATCH. Restrições de unicidade e comparação da identidade da tentativa impedem que duas requisições assumam a mesma alteração ou sobrescrevam livremente seu resultado. Uma operação `running` ou `unknown` bloqueia outro conteúdo ou outra ação de alteração na mesma conversa.

**Não há TTL nem descarte automático das operações de alteração.** Expirar ou apagar uma conversa não apaga o ledger nem desfaz uma mudança externa. Capacidade esgotada, banco indisponível, corrupção ou schema inesperado recusam novas execuções; não removem registros incertos para abrir espaço.

Na implementação atual, uma operação conhecida como `not_applied` ou `conflict` não pode ser vinculada silenciosamente a outra agenda, versão ou snapshot. Se o evento mudou, a mesma identidade é recusada com `update_snapshot_changed`; se o destino mudou, o vínculo também é recusado. É necessário conferir o estado e preparar uma intenção com novo vínculo, usando uma nova conversa quando os parâmetros da intenção permanecerem idênticos. Esse caminho não deve ser usado para contornar uma operação ainda incerta.

## Reconciliação e limites de recuperação

Repetir uma operação `running`, `unknown` ou `confirmed` lê o ID na agenda vinculada. A reconciliação compara o estado solicitado e todos os campos que deveriam ter sido preservados, incluindo descrição, local e transparência. Não chama o método de edição/reagendamento nem envia PATCH.

Se a consulta confirma o estado esperado, o servidor pode registrar ou devolver o resultado correspondente. **Essa observação confirma os valores atuais; não prova qual cliente ou tentativa os escreveu.** Outra ferramenta pode ter produzido o mesmo estado. O campo `recovered` descreve a recuperação técnica do resultado e não uma prova de autoria da escrita.

Se o estado não corresponder, uma operação incerta continua bloqueada. Mesmo observar os valores antigos não libera outro PATCH, porque a primeira tentativa ainda pode estar em andamento ou a leitura pode não resolver sua história. Para uma operação historicamente confirmada que depois sofreu alterações, o servidor informa a divergência e não restaura automaticamente os valores anteriores.

Não há endpoint administrativo para recuperar uma operação por ID sem conversa válida, descartar incertezas, reabrir uma aprovação consumida ou transferir uma intenção para outra conversa. A análise desses casos deve conservar os registros e conferir o estado externo. O controle não promete execução distribuída exatamente uma vez diante de mudanças externas ou perda dos bancos locais.

## Verificação e limites deste marco

**Validado em 25/10/2026: 1.257 testes Python aprovados**, sem falhas, erros ou testes ignorados. São **97 novos** sobre os 1.160 anteriores: 26 de persistência, 33 de serviço, 20 de gateway e 18 de API. A prova complementar aprovou **33 casos HTTP TCP em loopback**, com dois processos sucessivos da API e SQLite reais. Modelo, gateway e mensagens aprovativas são fixtures; zero chamadas reais Google/LLM ou envios WhatsApp. Registros **106/107** no [índice de evidências](evidencias.md).

Os testes de [persistência](../tests/test_agent_update_store.py), [serviço](../tests/test_agent_updates.py), [API](../tests/test_agent_update_api.py) e [gateway](../tests/test_agent_update_gateway.py) usam eventos e provedores fictícios, bancos temporários e transporte controlado. O ensaio do gateway atravessa o SDK Google real sem OAuth ou rede externa. Cobre agenda/versão alteradas, `If-Match`, corrida com HTTP 412, conflito, preservação de campos, repetição, reinício e perda de resposta sem segundo PATCH.

Essas provas não equivalem a uma alteração em agenda real, inferência real ou conversa real no WhatsApp. A validação do LLM continua pendente dos créditos identificados no [guia de conexão](llm-api.md); não houve novo teste de créditos neste marco. A [parte 9](exclusao-agente.md) implementou exclusão com proposta aprovada obrigatória e ledger próprio; texto/resposta dinâmica do WhatsApp/n8n permanece na parte 10.

Para reproduzir a suíte local na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

O ledger é inicializado no primeiro uso. Uma API já em execução precisa ser reiniciada para carregar os novos comandos; instalar os fontes não atualiza automaticamente o processo anterior. Não houve mudança visual que justificasse capturas artificiais: relatórios JSON são a evidência adequada deste backend.


## Continuidade — parte 9

A [exclusão aprovada](exclusao-agente.md) usa `POST /agent/delete` e ledger separado, schema 1, com reserva de uso único por proposta. Aprovação específica é sempre obrigatória para excluir; a dispensa de confirmação genérica de certos pedidos completos de edição/reagendamento não se aplica a esse comando. A ausência é conferida, e replay não repete DELETE. O contrato de alterações e seu ledger permanecem com o escopo deste guia. As contagens 1.257/33 são históricas da parte 8; a validação atual está nos registros 108/109 e no guia de exclusão. WhatsApp/n8n dinâmico permanece na parte 10.
