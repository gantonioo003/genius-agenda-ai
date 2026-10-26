# Excluir com confirmação pelo agente — etapa 11, parte 9

Atualização da parte 10, em 25/10/2026: o [orquestrador WhatsApp](whatsapp-agente.md) passou a reutilizar este contrato no fluxo n8n, com contexto privado, fila e saída reservada. As descrições de validação abaixo pertencem aos marcos originais; os testes de mutação da parte 10 também usam provedores fictícios. [Gemini real](gemini-api.md) tem prova separada.

O backend remove somente o evento de uma proposta de exclusão explicitamente aprovada naquela conversa. Confere agenda, identificador, versão e dados apresentados, reserva o uso único da aprovação e verifica a ausência depois da operação. Repetir uma tentativa em andamento, incerta ou confirmada consulta o estado, sem enviar outro DELETE.

O [diálogo](esclarecimentos-agente.md) continua preparatório: `ready` informa que os dados estão completos, mas não executa nem aprova uma exclusão. A ligação desses comandos ao texto e à resposta dinâmica do WhatsApp/n8n permanece na parte 10 do [escopo do agente](agente-ia.md).

## Preparação, prévia e aprovação obrigatória

1. Obter uma conversa emitida pelo servidor e preparar o pedido de exclusão em `/api/v1/agent/dialogue`.
2. Resolver os dados faltantes e selecionar explicitamente o evento quando houver mais de um candidato.
3. Solicitar `/api/v1/agent/confirmations/preview` e apresentar o evento e a pergunta da proposta.
4. Registrar a resposta afirmativa explícita para essa proposta, pelo diálogo ou por `/api/v1/agent/confirmations/approve`.
5. Acionar `POST /api/v1/agent/delete` para executar a proposta aprovada.

**A aprovação é sempre obrigatória para iniciar uma exclusão.** Um pedido completo, um resultado `ready`, um texto de intenção de excluir ou um código técnico de versão não a substituem. O backend não usa a regra de criação/edição que permite executar certos pedidos completos sem uma segunda confirmação genérica.

A [proposta](confirmacoes-agente.md) deve pertencer à conversa, ter ação `delete`, corresponder aos parâmetros exatos, à agenda, ao ID, à ETag e à projeção mínima do evento. Seu prazo deve estar válido. Proposta pendente, revogada, expirada, consumida ou divergente não libera uma nova tentativa. “Ok” genérico permanece ambíguo; o reconhecimento de aprovação não é decidido pelo LLM.

Se o evento desaparecer depois da aprovação, o primeiro comando ainda exige essa aprovação correspondente e reserva seu consumo, mas pode apenas confirmar que ele já está ausente, sem DELETE. Não afirma que o sistema foi o autor da remoção.

## Contrato HTTP

`POST /api/v1/agent/delete` exige Bearer, `Content-Type: application/json`, UUID4 canônico de conversa emitida pelo servidor e corpo bruto de até **1 KiB**. Herda correlação `X-Request-ID` e resposta `Cache-Control: no-store`. Aceita exatamente um campo:

```json
{
  "conversation_id": "<UUID4 da conversa emitido pelo servidor>"
}
```

O marcador é ilustrativo. Query string, campos extras, chaves JSON repetidas e JSON fora do contrato são recusados antes da execução. O cliente não envia ID de evento, agenda, versão, `proposal_id`, `confirmed`, token técnico ou `operation_id` nesse comando. A conversa deve pertencer ao escopo autenticado e conter um pedido `delete` completo, sem esclarecimentos ou candidatos pendentes.

O servidor escolhe o evento e os parâmetros pelo rascunho e pela proposta aprovada. O `operation_id`, com prefixo `agent-delete-`, é derivado no servidor a partir do proprietário, da conversa, da proposta e do hash de seu conteúdo vinculado. O cliente e o modelo não escolhem essa identidade.

A resposta usa o [envelope v1](contrato-api-v1.md), com `action: delete`:

| HTTP / resultado | Estado de mutação | Significado |
|---|---|---|
| 200 / `succeeded` | `confirmed` | A ausência do evento aprovado foi conferida. |
| 409 / `failed` | Conforme o caso | Aprovação, versão, agenda, revisão ou operação anterior impede continuar. |
| 502 / `failed` | `unknown` ou `not_applied` | Leitura ou resultado não confirmado; o código distingue a fase. |
| 422, 404 ou 503 / `failed` | Conforme o caso | Entrada inválida, conversa indisponível ou dependência/configuração indisponível. |

Em sucesso, `data` contém somente `result`, `calendar_name`, `event_id` e `recovered`. `result` pode ser `deleted` ou `already_absent`. O token técnico, a ETag, o hash do vínculo e o snapshot aprovado não são incluídos no recibo público de execução. Falhas incertas podem informar somente o ID do evento; os erros usam códigos seguros e `retryable: false`, sem resposta bruta do provedor, SQL, credenciais ou caminhos privados.

`delete_previously_confirmed` conserva o histórico de ausência confirmada, mas informa que a consulta atual não a reconfirmou. Seu `mutation_state: confirmed` descreve esse histórico; não garante que o ID continua ausente nem provoca outra exclusão.

## Consumo, vínculo e DELETE condicional

O [executor](../app/services/agent_deletion.py) usa a [leitura de confirmação](../app/services/confirmation_snapshot.py) para reler o ID exato na agenda configurada. Se o evento está presente, agenda, versão e projeção mínima precisam corresponder à proposta. Uma mudança do compromisso exige nova prévia e aprovação, sem reaproveitar silenciosamente a anterior.

Antes da operação externa, o servidor registra a identidade no ledger e usa `ConversationStore.consume_confirmation` para conferir revisão, proposta, conteúdo e versão em uma transação curta. Somente um consumo recebe o claim. Não há transação do contexto ou do ledger mantida aberta durante a chamada ao Google.

Depois do consumo, o código técnico de exclusão é derivado no servidor de agenda, ID e versão aprovados. Ele é encaminhado internamente ao [EventDeletionService](../app/services/event_deletion.py), sem ser escolhido pelo cliente ou exposto ao LLM. Esse código comprova o vínculo de versão; o consentimento vem do registro separado da proposta aprovada.

O [gateway](../app/google_calendar.py) recebe também `expected_calendar_id` e `expected_version`. Compara a agenda antes de prosseguir e a ETag de uma nova leitura antes do DELETE. Envia `If-Match` com a versão conferida e `sendUpdates=none`. Se o evento mudar entre leitura e exclusão, o controle condicional do Google recusa a versão antiga. Não há retentativa automática de DELETE.

A resposta vazia do DELETE não basta para sucesso: o gateway relê o ID, e o executor faz sua própria conferência de ausência na agenda vinculada. Uma leitura confirmada como ausente — por exemplo, 404/410 ou cancelamento correspondente ao mesmo ID — permite relatar ausência. Erro de transporte, resposta inválida ou outro ID não equivale a ausência. Um evento ainda presente depois da tentativa mantém o resultado incerto. O HTTP 412 da própria tentativa DELETE comprova a recusa condicional; um 412 de uma leitura posterior ao DELETE não comprova que a escrita foi recusada e permanece resultado incerto.

As restrições existentes continuam: não excluir uma série inteira nem tipos de evento não suportados. Uma ocorrência específica ou um compromisso comum de dia inteiro pode ser tratado pelo ID e pela versão adequados. Títulos vazios ou só com espaços recebem o rótulo público `(sem título)` na prévia; isso não modifica o título no Google.

## Registro durável e uso único da proposta

O [ledger de exclusões](../app/agent_deletion_store.py) usa SQLite separado, por padrão **`data/agent-deletions.sqlite3`**, configurável por `AGENT_DELETION_DATABASE_PATH`. Caminhos relativos são resolvidos na raiz do projeto. O schema inicial é **1**.

O registro privado conserva identidade da proposta, hash do conteúdo vinculado, parâmetros, agenda, evento e versão aprovados, estado, tentativa e recibo confirmado. O vínculo do proprietário/conversa usa hash; isso não torna os demais dados anônimos. O banco não é criptografado pela aplicação e deve ficar fora do Git, assim como seus backups. Não publique bases reais como evidência.

| Estado interno | Tratamento |
|---|---|
| `running` | Uma tentativa assumiu a proposta; repetições somente consultam. |
| `unknown` | A exclusão pode ter sido aplicada; repetições somente consultam. |
| `confirmed` | A ausência foi conferida; repetir relê o estado, sem excluir novamente. |
| `not_applied` | A tentativa parou com evidência de que não aplicou a exclusão; a mesma proposta não é reivindicada outra vez. |

Cada proposta recebe no máximo uma reserva de execução. Uma tentativa conhecida como `not_applied` exige conferir o estado, preparar uma nova prévia e obter uma nova aprovação, em vez de reabrir a proposta anterior. A nova proposta tem sua própria identidade. Uma proposta consumida não é reemitida: um novo turno de preparação estabelece a nova intenção antes de solicitar outra prévia. Uma operação `running` ou `unknown` bloqueia outra proposta ou outro conteúdo na mesma conversa; não se pode criar outro consentimento para contornar essa incerteza.

**Não há TTL nem descarte automático das operações de exclusão.** Expirar ou apagar uma conversa não apaga o ledger nem desfaz uma operação externa. Capacidade esgotada, corrupção, schema inesperado ou banco indisponível fazem o serviço recusar novas execuções, sem remover registros incertos para abrir espaço. Contexto schema 3, criações schema 1 e alterações schema 1 continuam separados e não são migrados nesta parte.

## Reconciliação e limites de recuperação

Repetir uma operação `running`, `unknown` ou `confirmed` usa somente `get_confirmation_snapshot` para consultar o ID na agenda vinculada. Não chama o executor de exclusão nem gera outro token para reenviar DELETE. Esse caminho também não consome uma segunda aprovação.

Ausência confirmada permite registrar ou devolver `already_absent`. **Ela comprova a observação atual de ausência, sem provar qual cliente ou tentativa removeu o evento.** Outra ferramenta pode tê-lo removido. Um evento presente, uma falha de leitura ou mudança da agenda não libera outra exclusão; a operação incerta continua bloqueada. Uma operação historicamente confirmada que depois não confirma ausência informa essa divergência e não exclui novamente o ID.

A reconciliação de uma operação existente não é uma nova exclusão e pode ocorrer depois do prazo da proposta original, desde que a conversa e o rascunho necessários continuem disponíveis. Não há endpoint administrativo para recuperar por operação sem conversa válida, transferir a aprovação para outra conversa, descartar incertezas ou reabrir uma proposta consumida. A análise desses casos precisa conservar os registros e conferir o estado externo.

O registro da API autenticada não é uma assinatura humana nem prova de que uma pessoa viu determinada tela. As provas locais usam mensagens aprovativas fictícias; não representam consentimento humano para excluir um evento real. O controle não promete execução distribuída exatamente uma vez diante de restaurações externas ou perda dos bancos locais.

## Verificação e limites deste marco

**Validado em 25/10/2026: 1.367 testes Python aprovados**, sem falhas, erros ou testes ignorados. São **110 novos** sobre os 1.257 anteriores: 27 de persistência, 39 de serviço, 20 de gateway e 24 de API. O ensaio complementar aprovou **62 casos HTTP TCP em loopback**, com dois processos sucessivos da API e SQLite reais. Modelo, gateway e respostas aprovativas são fixtures; zero chamadas reais Google/LLM/WhatsApp e nenhum consentimento humano real para alterar agenda. Registros **108/109** no [índice de evidências](evidencias.md).

Os testes de [persistência](../tests/test_agent_deletion_store.py), [serviço](../tests/test_agent_deletion.py), [API](../tests/test_agent_deletion_api.py) e [gateway](../tests/test_agent_deletion_gateway.py) usam dados fictícios, bancos temporários e transporte controlado. Os ensaios do gateway atravessam o SDK Google real sem OAuth ou rede externa. Conferem aprovação obrigatória, vínculo exato, expiração, revisão, consumo único, concorrência, ausência, versão obsoleta, `If-Match`, falha de resposta, reinício e recuperação sem segundo DELETE.

Os registros **108/109** no [índice de evidências](evidencias.md) consolidam esta parte; o acervo deste marco é de **109 registros**. Nenhuma exclusão em agenda real, inferência real, envio WhatsApp ou aprovação humana real é comprovada por fixtures. A validação do LLM continua pendente dos créditos identificados no [guia de conexão](llm-api.md), sem novo teste de créditos nesta parte. O WhatsApp/n8n dinâmico permanece para a parte 10.

Para reproduzir a suíte local na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

O ledger é inicializado no primeiro uso. Os bancos e serviços normais não foram usados ou reiniciados nesta validação. Uma API já em execução precisa ser reiniciada para carregar o novo comando. Não houve mudança visual que justificasse captura artificial: relatórios JSON são a evidência adequada deste backend.


Atualização da parte 11: [conversas e falhas verificadas](testes-conversas-agente.md), com nove ajustes e 1.561 testes aprovados. As mutações conversacionais deste marco usaram agenda fictícia; a prova WhatsApp humana da parte 10 permanece histórica. O ledger de exclusões aguarda bloqueios SQLite por até 5 s, sem repetir escrita; prévia definitivamente recusada no transporte é descartada antes de liberar outro turno.
