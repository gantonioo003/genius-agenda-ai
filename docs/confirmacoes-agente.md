# Confirmações do agente — etapa 11, parte 6

Atualização da parte 10, em 25/10/2026: o [orquestrador WhatsApp](whatsapp-agente.md) passou a reutilizar este contrato no fluxo n8n, com contexto privado, fila e saída reservada. As descrições de validação abaixo pertencem aos marcos originais; os testes de mutação da parte 10 também usam provedores fictícios. [Gemini real](gemini-api.md) tem prova separada.

O backend registra uma aprovação para a proposta apresentada naquela conversa: ação, parâmetros, agenda, identificador e versão do evento. Alterar o pedido, trocar o evento ou detectar uma nova versão impede reutilizar a aprovação anterior. A parte 6 implementou esse controle de consentimento sem executar mudanças. Desde a [parte 7](criacao-agente.md), o comando HTTP de criação consome a aprovação correspondente quando há proposta ativa; o diálogo continua apenas preparando pedidos ou registrando a resposta à proposta.

O fluxo explícito é preparar o pedido, solicitar a prévia, apresentar seus dados e receber a confirmação. Um resultado `ready` da [preparação conversacional](esclarecimentos-agente.md) apenas informa que os parâmetros estão completos. “Ok” genérico também não aprova uma proposta.

Este mecanismo não impõe uma segunda confirmação genérica a todo pedido de criação, edição ou reagendamento. Pedidos inequívocos e completos podem autorizar essas ações conforme o [escopo do agente](agente-ia.md). A [exclusão pelo agente](exclusao-agente.md), implementada na parte 9, exige sempre prévia e aprovação específica para a primeira tentativa. O servidor consome essa proposta uma vez antes do DELETE; os comandos HTTP de criação, edição e reagendamento preservam suas regras próprias de autorização.

## Contrato HTTP

Os quatro comandos são `POST`, com prefixo `/api/v1`, autenticação Bearer, `Content-Type: application/json`, correlação `X-Request-ID` e `Cache-Control: no-store`. O corpo bruto é limitado a **2 KiB**; não aceita query, campos extras, chaves JSON repetidas ou identificadores fora do formato UUID4 canônico. A mensagem da rota de aprovação tem até 128 caracteres.

| Rota | Corpo | Efeito |
|---|---|---|
| `/agent/confirmations/preview` | `{conversation_id}` | Confere o rascunho completo e lê o estado atual necessário para emitir a proposta. |
| `/agent/confirmations/approve` | `{conversation_id, proposal_id, message}` | Confere a proposta e registra uma aprovação explícita. |
| `/agent/confirmations/status` | `{conversation_id, proposal_id}` | Consulta o estado registrado, sem chamar Google ou LLM e sem renovar a validade. |
| `/agent/confirmations/revoke` | `{conversation_id, proposal_id}` | Revoga a proposta atual sem alterar compromissos. |

A conversa deve ter sido emitida pelo servidor, conforme o [guia de contexto](contexto-conversa.md). O servidor verifica seu proprietário e sua validade antes de construir o serviço. A prévia recebe somente a conversa; o cliente não fornece parâmetros de alteração, outra agenda ou uma versão do evento para o servidor aceitar.

Exemplo ilustrativo da aprovação, substituindo os identificadores pelos valores emitidos pelo servidor:

```json
{
  "conversation_id": "<UUID4 da conversa>",
  "proposal_id": "<UUID4 da prévia apresentada>",
  "message": "Sim, confirmo."
}
```

As respostas mantêm `operation_id: null` e `mutation_state: not_applied`. Quando há `data`, seus campos são somente `proposal_id`, `state`, `action`, `event`, `parameters`, `expires_at`, `approved_at`, `question` e `execution_allowed`. **`execution_allowed` permanece `false`, inclusive em `approved`**, pois o comando registra consentimento e não executa a ação.

| Estado | Significado |
|---|---|
| `needs_confirmation` / `state: pending` | A proposta foi emitida e aguarda uma resposta explícita. |
| `approved` | A aprovação está registrada para o conteúdo e a versão daquela proposta. |
| `revoked` | A proposta foi revogada. |
| `expired` | O prazo da proposta terminou. |
| `invalidated` | O pedido ou o estado ao qual a proposta se vinculava deixou de corresponder. |
| `consumed` | O uso interno da aprovação já foi reservado e não pode ser repetido. |
| `failed` | Não foi possível concluir a operação; o código seguro informa a causa. |

Na consulta de status, um estado conhecido é apresentado com HTTP 200, incluindo uma expiração calculada, sem salvar a memória ou renovar seu prazo. Comandos que tentam usar uma aprovação expirada, inválida ou consumida são recusados com HTTP 409. Identificador desconhecido retorna 404; entrada inválida ou resposta ambígua, 422; indisponibilidade e falhas de leitura, 503 ou 502. Não há repetição automática.

## Conteúdo e versão vinculados

O [serviço de confirmação](../app/services/confirmations.py) exige um rascunho completo, sem campos aguardando resposta ou opções por selecionar. Aceita `create`, `edit`, `reschedule` e `delete`. Um título de busca pendente não substitui a identificação exata do evento.

Os parâmetros são normalizados e validados pelos serviços existentes. A identidade da proposta inclui a ação e todos os parâmetros exatos, preservando a diferença entre omitir um campo de edição e enviar `""` para limpá-lo. Título, horários, fuso, descrição e local entram quando fazem parte da ação. O reagendamento de evento de dia inteiro permanece fora do suporte atual.

A [leitura de confirmação](../app/services/confirmation_snapshot.py) obtém a identidade da agenda configurada e, para eventos existentes, o ID, a projeção mínima e a versão ETag devolvida pelo Google. Antes de registrar uma aprovação, o serviço faz uma nova leitura e confere o vínculo. Ausência confirmada ou mudança de evento, calendário, versão ou dados apresentados invalida a proposta. Uma falha de leitura preserva o registro anterior e não aprova nada.

Na criação ainda não existe um evento para consultar. A proposta vincula a agenda configurada ao conteúdo exato do novo compromisso. O [executor da parte 7](criacao-agente.md) aplica as validações de conflitos e a identidade persistente da criação, além de reler a agenda vinculada antes de escrever.

O cliente recebe `event` e `parameters` para apresentar o alvo e a alteração proposta. O calendário interno, ETag, hash do pedido e hash da resposta aprovativa permanecem privados. Esses valores não são argumentos do LLM nem campos do envelope público. O modelo não possui ferramenta capaz de fabricar consentimento.

## Aprovação explícita, alterações e revogação

As respostas afirmativas aceitas são “sim”, “confirmo” e “sim, confirmo”, com normalização limitada de caixa, espaços e pontuação final. Perguntas, negativas e mensagens que acrescentam outra instrução não são tratadas como aprovação. Na rota `/approve`, “não” continua sendo uma resposta inválida para aprovar.

Quando existe uma proposta na conversa, o endpoint `/agent/dialogue` pode reconhecer essas respostas afirmativas antes de chamar o modelo e encaminhá-las ao mesmo serviço de confirmação. O serviço ainda confere existência, prazo, estado e vínculo da proposta. As recusas explícitas “não”, “não confirmo”, “cancela” e “cancelar” revogam a proposta sem depender do LLM. Mensagens mais longas, como “não, reagende para amanhã”, seguem a interpretação normal do diálogo.

Um novo turno processado com sucesso pelo diálogo invalida a confirmação anterior, inclusive quando o resultado mantém os mesmos valores. Assim, uma aprovação não atravessa silenciosamente outra interação ou uma nova intenção. Uma mudança do rascunho também é conferida pela persistência. Consultas pelo endpoint de leitura preservam o rascunho e a proposta; a aprovação permanece ligada ao seu próprio evento e conteúdo, sem acompanhar uma nova seleção de consulta.

Repetir a mesma prévia válida conserva seu identificador e seu prazo. Repetir a aprovação da mesma proposta não troca a data de aprovação, o hash da resposta ou o vencimento. Uma nova prévia após mudança ou expiração emite outra proposta pendente e exige sua própria aprovação. Uma proposta consumida não é reemitida automaticamente. Revogar não remove eventos e não chama o Google.

## Prazos, persistência e concorrência

O prazo padrão da proposta é de **cinco minutos**. O serviço admite um limite interno de até 900 segundos; esse valor não é configurável pelo corpo HTTP. A validade da conversa continua sendo controlada pelo [contexto persistente](contexto-conversa.md), com padrão de 30 minutos. Uma conversa indisponível ou expirada também impede acessar sua proposta.

O prazo da proposta não é renovado por status, repetição da prévia ou repetição da aprovação. Mesmo se a leitura do Google demorar, o relógio do servidor é conferido novamente antes de registrar a aprovação. Horário escrito pelo cliente não substitui essa referência.

O [contrato de estado](../app/confirmation_state.py) registra a proposta, seu hash de vínculo, estado, horários de criação/vencimento e, após aprovação, horário e hash da resposta normalizada. Não guarda uma transcrição da mensagem aprovativa. O schema do [banco de conversas](../app/conversation_store.py) passa para a versão 3 e migra versões 1 e 2 em uma transação, preservando vínculo, revisão, prazo e rascunho existentes.

O banco e suas cópias permanecem privados e fora do Git. A aplicação não criptografa esse arquivo; proposta, parâmetros, referência do evento e versão podem ficar legíveis localmente. Expiração e exclusão representam descarte lógico do registro ativo, sem promessa de eliminar cópias forenses ou backups.

As operações da API carregam uma revisão e fecham a conexão antes de consultar serviços externos. O salvamento usa comparação de revisão (CAS), recusando atualizações concorrentes com 409. Um resultado de confirmação não pode sobrescrever uma alteração do pedido ocorrida durante a leitura do Google. Não há lock do SQLite mantido durante essa leitura.

## Consumo pelos executores e exclusão aprovada

`ConversationStore.consume_confirmation` é uma operação interna, sem endpoint público. Ela confere conversa, revisão, proposta, hash do pedido, versão, aprovação e prazo em uma única transação. O primeiro consumo válido marca o registro como `consumed` e devolve um claim privado com os dados vinculados. Uma repetição ou uma tentativa concorrente não recebe outro claim.

Esse consumo reserva o uso do consentimento e não executa o Google Calendar por si só. O comando HTTP de criação da parte 7 consome a proposta aprovada correspondente antes de chamar `InterviewService` e o gateway, preservando agenda vinculada, conflitos e identidade/idempotência. Os executores de edição/reagendamento da [parte 8](alteracoes-agente.md) conferem a versão antes de consumir a aprovação e aplicam `If-Match` na escrita. O [executor de exclusão da parte 9](exclusao-agente.md) exige aprovação obrigatória vinculada a agenda, evento, ETag e projeção, consome-a uma vez e confere a ausência. Se houver falha ou resultado incerto após reservar o uso, será necessário reconciliar a operação antes de decidir uma recuperação. Não se deve criar outra aprovação ou repetir uma gravação automaticamente para contornar esse estado.

O registro corresponde à resposta recebida por uma API autenticada. A aplicação atual identifica um escopo por chave Bearer; não constitui uma assinatura humana nem comprova que uma pessoa viu uma tela específica. Os testes usam mensagens fictícias e não representam consentimento humano para executar mudanças reais.

## Verificação e limites deste marco

**Validado em 24/10/2026: 1.075 testes Python aprovados**, sem falhas, erros ou testes ignorados. São 99 novos: 25 de estado/persistência, 16 da leitura versionada, 35 do serviço e 23 de API. As regressões anteriores foram preservadas.

Os testes de [estado/persistência](../tests/test_confirmation_state.py), [leitura de versão](../tests/test_confirmation_snapshot.py), [serviço](../tests/test_confirmations.py) e [API](../tests/test_confirmation_api.py) verificam conteúdo exato, isolamento, prazo, alterações, revogação, repetição e consumo concorrente. O ensaio aprovou **26 casos HTTP TCP em loopback**, com dois processos Python e SQLite reais. Uma aprovação persistiu após reinício; proposta de outra conversa, “ok” ambíguo, versão alterada, novo pedido de conteúdo igual e vencimento foram tratados. Um consumo interno foi confirmado; o segundo uso foi recusado. Modelo e respostas Google controlados nesses ensaios não comprovam acesso aos provedores reais.

Os registros 102/103 no [índice de evidências](evidencias.md) consolidam a suíte e o ensaio HTTP com mensagens de aprovação fictícias, sem registrar consentimento humano real. Este controle de backend é demonstrado por relatórios JSON, sem necessidade de uma captura artificial. Não houve escrita real na agenda, envio WhatsApp ou chamada ao LLM real nesta parte. A inferência real continua aguardando os créditos identificados na [parte de conexão](llm-api.md); não foi testada novamente neste marco.

Para reproduzir a suíte local, na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

O [executor HTTP de criação](criacao-agente.md) foi implementado na parte 7. Os [comandos HTTP de edição/reagendamento](alteracoes-agente.md) foram acrescentados na parte 8. Os próximos passos são **9. excluir após confirmação específica** e **10. ligar texto e resposta dinâmica ao WhatsApp/n8n**, conforme a sequência da [etapa 11](agente-ia.md).

Os serviços normais não foram reiniciados. Para carregar os endpoints e os hooks novos, reinicie a API local. A migração do contexto ocorre no próximo uso do store; a instalação dos fontes não abriu nem migrou os bancos normais.

## Integração atual — parte 7

`POST /api/v1/agent/create` recebe somente a conversa; os parâmetros e o identificador da operação vêm do servidor. Um pedido completo sem proposta pode seguir o comando explícito, sem confirmação genérica extra. Com proposta ativa, o executor exige ação/conteúdo/agenda correspondentes, estado aprovado e prazo válido, e reserva seu consumo uma única vez. Propostas pendentes, revogadas, expiradas, consumidas ou divergentes não liberam uma nova criação.

Após uma tentativa registrada, repetir `running`, `unknown` ou `confirmed` aciona reconciliação somente de leitura, sem consumir novamente a aprovação nem enviar outro POST. Uma falha depois do consumo não autoriza gerar outra aprovação para contornar um resultado incerto. O estado da execução fica no ledger separado de criações, schema 1 e sem TTL; o banco de conversas conserva o schema 3.

A validação histórica da parte 6 acima permanece em 1.075 testes e 26 casos HTTP. Em **25/10/2026**, a parte 7 foi validada com **1.160 testes Python** e **27 casos HTTP TCP com dois processos**, usando SQLite real e provedores controlados. As mensagens aprovativas são fictícias, sem consentimento humano para alterar agenda real. Zero chamadas reais Google/LLM ou envios WhatsApp neste marco. Chave real configurada, créditos esgotados e inferência não testada novamente. [Criação, recuperação e evidências](criacao-agente.md).

## Integração de edição e reagendamento — parte 8

Quando houver proposta ativa, `/agent/edit` e `/agent/reschedule` exigem aprovação correspondente aos parâmetros, agenda, ID, ETag e projeção do evento, dentro do prazo. O consumo usa revisão e versão em uma transação curta antes da escrita. O gateway confere agenda/versão novamente e envia `If-Match`; mudar o evento impede usar a aprovação antiga. Um comando explícito para pedido completo sem proposta não exige confirmação genérica adicional.

A tentativa fica no ledger privado de alterações, separado do contexto. Repetir `running`, `unknown` ou `confirmed` apenas consulta o estado, sem consumir novamente nem repetir PATCH. Uma observação dos valores esperados confirma o estado atual, sem provar quem o escreveu. Se o histórico confirmado divergir depois, não há reaplicação automática. [Recuperação e limites](alteracoes-agente.md).

Validação da parte 8: 1.257 testes Python e 33 casos HTTP TCP locais aprovados, com dois processos, SQLite reais e provedores controlados. Mensagens aprovativas são fictícias; zero consentimento humano real, chamadas reais Google/LLM ou envios WhatsApp. Registros 106/107.

## Exclusão e proposta de uso único — parte 9

`POST /api/v1/agent/delete` recebe somente `conversation_id` e **sempre exige uma proposta de exclusão explicitamente aprovada** para iniciar uma tentativa. A regra de dispensa de confirmação genérica de certos pedidos completos de criação/edição/reagendamento não se aplica à exclusão. O comando confere os parâmetros e a versão apresentados; proposta pendente, revogada, expirada, consumida ou divergente não libera novo DELETE.

A identidade da execução é persistida no ledger privado de exclusões, vinculada a `proposal_id` e hash do conteúdo aprovado. O consumo atômico da aprovação precede a geração do token técnico no servidor. Esse código de versão não é entregue ao LLM nem escolhido pelo cliente; o consentimento vem da proposta, não do código.

Uma proposta recebe no máximo uma reserva de execução. Após resultado comprovadamente `not_applied`, é preciso conferir o estado, obter nova prévia e nova aprovação, sem reivindicar a proposta antiga. `running`/`unknown` bloqueiam outra proposta naquela conversa; uma aprovação nova não contorna incertezas. Replay somente consulta o ID na agenda vinculada, sem consumir novamente nem repetir DELETE. Se o evento desaparecer depois da aprovação, o comando pode consumir o vínculo e confirmar ausência sem excluir.

1.367 testes Python e 62 casos HTTP TCP locais aprovados, com dois processos e SQLite reais e provedores/mensagens aprovativas fictícios. Nenhum consentimento humano real ou chamada real Google/LLM/WhatsApp registrado nesta parte. As validações anteriores permanecem históricas. [Exclusão, recuperação e evidências 108/109](exclusao-agente.md).
