# Criação pelo agente — etapa 11, parte 7

Atualização da parte 10, em 25/10/2026: o [orquestrador WhatsApp](whatsapp-agente.md) passou a reutilizar este contrato no fluxo n8n, com contexto privado, fila e saída reservada. As descrições de validação abaixo pertencem aos marcos originais; os testes de mutação da parte 10 também usam provedores fictícios. [Gemini real](gemini-api.md) tem prova separada.

O backend executa um pedido de criação completo que já está preparado na conversa. O comando usa os parâmetros persistidos pelo servidor, aplica as validações existentes e só apresenta sucesso quando o resultado corresponde ao evento solicitado. Repetir um comando em andamento, incerto ou confirmado aciona uma consulta de reconciliação, sem enviar outra criação ao Google.

O [diálogo](esclarecimentos-agente.md) continua responsável por interpretar a mensagem e perguntar sobre dados ausentes ou ambíguos. `ready` significa que o rascunho está completo; o diálogo permanece sem escrita, com `execution_allowed: false`. A execução ocorre pelo comando explícito descrito neste guia e não chama o LLM novamente.

## Preparação e comando explícito

O fluxo de criação é:

1. Criar uma conversa pelo servidor, conforme o [guia de contexto](contexto-conversa.md).
2. Enviar o pedido para `/api/v1/agent/dialogue` e responder aos esclarecimentos necessários.
3. Apresentar os parâmetros completos do resultado `ready`.
4. Acionar `POST /api/v1/agent/create` para executar aquele pedido preparado.

Um pedido inequívoco e completo pode autorizar a criação por esse comando, sem uma segunda confirmação genérica. A camada que chama a API deve vincular o comando à intenção explícita do usuário; o servidor não transforma sozinho um resultado `ready` em execução.

Se a conversa já contém uma proposta de confirmação, ela não pode ser ignorada para iniciar a escrita. A proposta deve ser de criação, corresponder aos parâmetros exatos e à agenda atual, estar aprovada e permanecer válida. Uma proposta pendente, revogada, expirada, consumida ou divergente impede uma nova execução. O consumo da aprovação ocorre uma única vez, antes da chamada de criação, por comparação de revisão e vínculo, conforme o [guia de confirmações](confirmacoes-agente.md).

Uma repetição de operação já registrada consulta seu resultado antes de tentar consumir qualquer aprovação novamente. Esse caminho não representa uma segunda utilização do consentimento: ele apenas confere a operação original. Uma falha após o consumo não autoriza reemitir uma proposta para contornar um resultado incerto.

## Contrato HTTP

`POST /api/v1/agent/create` herda autenticação Bearer, correlação `X-Request-ID` e `Cache-Control: no-store`. Exige `Content-Type: application/json` e aceita exatamente um campo:

```json
{
  "conversation_id": "<UUID4 da conversa emitido pelo servidor>"
}
```

O exemplo é ilustrativo; substitua o marcador pelo identificador real daquela conversa. O corpo bruto é limitado a **1 KiB**. Query string, campos extras, chaves JSON repetidas, identificador inválido e JSON fora do contrato são recusados antes da execução.

Não são aceitos título, horários, agenda, ID de evento, parâmetros de ferramenta ou `operation_id` no corpo. A conversa deve pertencer ao escopo da chave autenticada, estar disponível e conter um pedido `create` completo, sem esclarecimentos ou candidatos pendentes. O servidor relê o contexto e revalida os parâmetros antes de escrever.

Os parâmetros de criação são `title`, `start`, `end`, `time_zone` e `description`, com descrição vazia como padrão. O executor não acrescenta suporte a local, participantes ou recorrência. O identificador da operação é derivado no servidor a partir do proprietário, da conversa e dos parâmetros canônicos, com prefixo `agent-create-`. O modelo e o cliente não escolhem esse valor.

As respostas usam o envelope comum da API com `action: create`:

| HTTP / `status` | `mutation_state` | Resultado |
|---|---|---|
| 201 / `succeeded` | `confirmed` | Uma criação nova foi confirmada. |
| 200 / `succeeded` | `confirmed` | Um evento correspondente foi reutilizado ou confirmado por reconciliação. |
| 409 / `conflict` | `not_applied` | O intervalo possui conflito; os dados mínimos permitem escolher outro horário. |
| 409 / `failed` | Conforme o caso | O pedido, a aprovação, a agenda ou uma operação anterior impedem prosseguir. |
| 502 / `failed` | `unknown` | O resultado não pôde ser confirmado; a operação não será reenviada automaticamente. |
| 422, 404 ou 503 / `failed` | Conforme o caso | Entrada ou preparação inválida, conversa indisponível ou serviço local indisponível. |

Em sucesso, `data` contém somente `event_id`, `html_link`, `calendar_name`, `reused` e `recovered`. Conflitos devolvem o intervalo e a projeção mínima dos compromissos envolvidos. Falhas incertas podem devolver apenas `event_id`, inclusive `null` quando ainda não foi confirmado. Erros usam códigos seguros e `retryable: false`; não expõem SQL, credenciais, caminhos privados ou respostas brutas dos provedores.

`creation_previously_confirmed` merece uma distinção: o histórico conserva a criação confirmada, mas a nova consulta não reconfirmou o evento. Nesse caso, `mutation_state: confirmed` descreve o histórico da operação e não afirma que o compromisso continua disponível. O servidor não o recria.

## Registro durável da execução

O [registro de criações do agente](../app/agent_creation_store.py) usa um SQLite separado, por padrão **`data/agent-creations.sqlite3`**, configurável por `AGENT_CREATION_DATABASE_PATH`. Caminhos relativos são resolvidos a partir da raiz do projeto. O schema inicial é **versão 1**.

Esse registro complementa os bancos existentes:

| Registro | Responsabilidade |
|---|---|
| Contexto da conversa | Rascunho, revisão, prazo da conversa e eventual proposta de confirmação. |
| Criações do agente | Identidade da execução, parâmetros exatos, agenda vinculada, estado, tentativa e recibo confirmado. |
| Identidade global de criação da agenda | Conteúdo, ID estável do evento e fingerprint que permitem detectar repetição mesmo entre operações distintas. |

O ledger persiste uma reserva `running` antes da tentativa de escrita. Transações curtas, restrições de unicidade e comparação da identidade da tentativa impedem que duas execuções assumam livremente o mesmo trabalho ou sobrescrevam um resultado concorrente. Não é mantida uma transação desse ledger durante a chamada ao Google.

| Estado interno | Significado e tratamento |
|---|---|
| `running` | Uma tentativa assumiu o pedido e pode ainda estar em execução. Repetições somente reconciliam. |
| `unknown` | O resultado pode ter sido aplicado. Repetições somente reconciliam. |
| `confirmed` | Há um recibo validado. Repetições relêem o Google e conferem a identidade. |
| `not_applied` | A execução foi interrompida com evidência anterior à escrita. Pode admitir uma nova tentativa, após todas as verificações. |
| `conflict` | A checagem de disponibilidade impediu a criação. Uma nova tentativa volta a conferir as condições atuais. |

**Não há TTL nem descarte automático dos registros de criação, inclusive dos resultados incertos.** O vencimento da conversa não apaga a identidade de uma possível escrita externa. Uma operação `running` ou `unknown` bloqueia outro conteúdo na mesma conversa até que seu resultado seja esclarecido. Capacidade esgotada, schema inesperado, corrupção ou indisponibilidade do banco fazem o serviço recusar a execução, sem apagar registros para abrir espaço.

O ledger contém parâmetros e referências de eventos legíveis localmente; ele não é criptografado pela aplicação. Deve permanecer privado, assim como suas cópias e backups. Hashes de vínculo não tornam os demais campos anônimos. Não publique bancos reais como evidência.

## Validações e proteção da agenda

O [executor](../app/services/agent_creation.py) reutiliza `InterviewService` e o [gateway Google](../app/google_calendar.py), preservando as regras já implementadas para datas, duração, fuso, conflitos e idempotência.

Antes de reservar a execução, o servidor obtém a identidade da agenda configurada. Se houver proposta, confere seu vínculo com esse destino. A criação recebe internamente `expected_calendar_id`, e o gateway compara essa identidade novamente após resolver a agenda e **antes de reservar a criação global ou enviar um POST**. Uma troca de agenda nesse intervalo interrompe a escrita.

Sem proposta, o executor também verifica se a revisão e o rascunho da conversa permaneceram iguais desde sua leitura. Com proposta, o consumo atômico exige revisão e conteúdo correspondentes. Uma atualização concorrente não pode ser silenciosamente tratada como o pedido aprovado.

O gateway consulta o ID persistido antes de criar, verifica disponibilidade e conserva a identidade global por conteúdo. Pedidos de conteúdo idêntico podem reutilizar o evento de uma operação anterior, inclusive quando ela possui outro identificador. A confirmação exige correspondência de ID, título, descrição, instantes de início e término, estado confirmado, link e fingerprint privado `geniusRequest`.

A escrita mantém uma única tentativa de POST e a conferência do resultado prevista pelo gateway. Não há retry automático de gravação. A criação continua sem convidados e sem disparo de atualização por e-mail. A checagem de disponibilidade e o POST não formam uma transação distribuída com o Google; alterações externas podem ocorrer entre essas etapas.

## Reconciliação e recuperação

Repetir `/api/v1/agent/create` para os mesmos parâmetros registrados em `running`, `unknown` ou `confirmed` chama `reconcile_creation`. Esse caminho consulta a agenda e a identidade global por conteúdo; usa o ID e o fingerprint persistidos originalmente, em vez de derivar outro evento a partir do comando repetido.

O gateway não reserva nem confirma registros globais de criação nesse caminho e não envia POST, PATCH ou DELETE. A leitura da agenda conserva a atualização de metadados locais já existente em `resolve_calendar`. Quando um evento é encontrado e validado, o ledger do agente pode registrar seu recibo como `confirmed`.

| Resultado da consulta | Consequência |
|---|---|
| `found` | Evento correspondente conferido; responde com `reused: true` e `recovered: true`. |
| `absent` | Existe uma identidade global registrada, mas o evento não foi encontrado. Isso não autoriza outro POST. |
| `unregistered` | Não há identidade global para aquele conteúdo. Isso não comprova que uma tentativa externa não ocorreu. |
| Falha, divergência ou mudança de agenda | Resultado permanece sem reconfirmação; nenhuma nova criação é enviada. |

Por exemplo, se o Google salvar o evento e a resposta se perder, a próxima chamada do comando pode recuperá-lo por consulta. Se a consulta também falhar, o estado permanece incerto. Uma requisição concorrente que ainda não encontra o evento não rebaixa a tentativa original em andamento; ela pode concluir normalmente depois.

Para recuperar uma falha, preserve os arquivos de contexto, de criações do agente e de identidade global; restaure o acesso à agenda vinculada e consulte novamente o mesmo pedido. Não apague o ledger, altere seu estado manualmente ou troque a identidade para forçar outra escrita. Um evento removido ou alterado depois da confirmação exige investigação do estado atual, sem recriação automática.

Há limites operacionais: o comando público exige uma conversa válida e um rascunho completo. O ledger não expira com ela, mas este marco não fornece endpoint administrativo de recuperação por `operation_id`, liberação de operações incertas ou restauração de conversa vencida. Nesses casos, a análise e a recuperação precisam preservar os registros existentes. Esta implementação não promete execução distribuída exatamente uma vez diante de alterações externas ou perda dos bancos locais.

## Verificação e limites deste marco

**Validado em 25/10/2026: 1.160 testes Python aprovados**, sem falhas, erros ou testes ignorados. São **85 novos** sobre os 1.075 anteriores: 24 de store, 27 de serviço, 16 de gateway e 18 de API. O ensaio complementar aprovou **27 casos HTTP TCP em loopback**, com dois processos sucessivos da API, SQLite real e modelo/gateway como fixtures. Preparação sem escrita, comando explícito, conflitos, repetição, aprovação exata, consumo único e recuperação após reinício foram conferidos. Os registros 104/105 no [índice de evidências](evidencias.md) consolidam esses resultados, com zero chamadas reais ao Google/LLM e zero envios WhatsApp neste marco.

Os testes de [persistência](../tests/test_agent_creation_store.py), [serviço](../tests/test_agent_creation.py), [API](../tests/test_agent_creation_api.py) e [gateway](../tests/test_agent_creation_gateway.py) usam dados fictícios e bancos isolados. A verificação cobre preparação obrigatória, aprovação exata e consumo, propriedade da conversa, mudança de revisão e de agenda, concorrência, conflitos, repetição, falha após gravação, recuperação por consulta e ausência de uma segunda escrita nos estados incertos.

Os testes do gateway atravessam o SDK Google com transporte HTTP controlado; os testes da API injetam dependências controladas. Esses ensaios não comprovam acesso aos provedores reais e não representam consentimento humano para modificar uma agenda real. Esta parte não exige capturas artificiais de interface: os contratos e os resultados dos testes constituem a evidência do backend.

A chave real do LLM já está configurada privadamente, mas os créditos foram esgotados. A chamada ao **LLM real continua pendente de créditos**, conforme o [guia de conexão](llm-api.md), e não foi testada novamente neste marco. A execução de criação usa o rascunho persistido e pode ser verificada sem uma nova inferência. Não houve validação de criação em provedor real por esses testes.

A ligação dinâmica do diálogo e desse comando ao **n8n e WhatsApp** permanece para uma etapa futura. Os fluxos de teste existentes não passam a criar eventos conversacionais automaticamente com a adição deste endpoint. Edição e reagendamento pelo agente foram implementados nos comandos HTTP da [parte 8](alteracoes-agente.md), com ledger e versões próprios. A [parte 9](exclusao-agente.md) implementou a exclusão apenas da proposta explicitamente aprovada, com uso único e ausência conferida. A integração dinâmica permanece na parte 10.

Para reproduzir a suíte local na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

O ledger é inicializado no primeiro uso do serviço. Para carregar o novo endpoint em uma API já em execução, reinicie esse processo; a presença do código no projeto não atualiza um serviço que ainda usa a versão anterior.

## Continuidade — parte 8

O executor de criação e seu ledger schema 1 permanecem com o contrato desta parte. A [edição e o reagendamento](alteracoes-agente.md) usam comandos e ledger separados (`agent-updates.sqlite3`, schema 1), com snapshot anterior, versão vinculada, `If-Match` e conferência dos campos preservados. O contexto continua no schema 3. As contagens 1.160/27 acima são históricas da parte 7; a verificação atual das alterações e os registros 106/107 estão no novo guia. Nenhum desses comandos é disparado automaticamente pelo diálogo ou pelo fluxo WhatsApp existente.

## Continuidade — parte 9

A [exclusão pelo agente](exclusao-agente.md) foi implementada em comando e ledger separados (`agent-deletions.sqlite3`, schema 1). Ela sempre exige proposta exata aprovada e reserva seu uso uma vez; não herda a dispensa de confirmação genérica permitida a certos pedidos de criação. A ausência é conferida e repetições somente consultam. O contrato e o ledger de criação permanecem com o escopo deste guia; suas contagens 1.160/27 são históricas da parte 7. A ligação automática ao WhatsApp/n8n continua na parte 10.
