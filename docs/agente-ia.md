# Agente de IA — etapa 11

**Parte 1 definida em 24/10/2026.** Este documento fixa o escopo, as ferramentas e as regras. O adaptador OpenAI inicial foi preservado; na parte 10, o usuário escolheu a faixa gratuita [Gemini](gemini-api.md), validada com dois pedidos fictícios. O [fluxo WhatsApp atual](whatsapp-agente.md) já usa o agente. Os cenários em `examples/agent/conversation-contract.json` são critérios de aceite, não registro de conversas executadas.

**Etapa 11 concluída em 25/10/2026, no escopo do sandbox de teste.** As doze partes foram revisadas. A versão final tem 1.561 testes Python aprovados, 53 novos na parte 11, e 29 verificações HTTP finais. A consulta WhatsApp real da parte 10 foi recebida pelo usuário; as mutações conversacionais e a aprovação das sondagens Gemini usaram agenda e consentimento fictícios. Os 540 cenários JavaScript e 42 verificações n8n são históricos da parte 10. [Fechamento, provas e pendências](fechamento-etapa-11.md).

## Objetivo e pedidos atendidos

Atender em português pedidos sobre entrevistas na agenda configurada no Python. Nesta fase, a operação continua restrita à agenda de teste `Genius Testes` e ao remetente autorizado no sandbox.

| Pedido | Comportamento previsto |
|---|---|
| Consultar dia, semana ou mês | Resolver a data de referência e consultar o período civil no fuso configurado. |
| Consultar uma entrevista | Usar um ID já obtido do backend ou procurar por título e data/período; esclarecer quando não houver seleção segura. |
| Criar entrevista | Reunir título, início, término e fuso; descrição opcional. Validar os dados e os conflitos antes da execução. |
| Reagendar | Identificar o evento, reunir novo início/término/fuso e aplicar a validação existente. |
| Editar informações | Alterar apenas título, descrição ou local solicitados; preservar os demais campos. |
| Excluir | Apresentar a prévia do evento e aguardar confirmação explícita vinculada àquela versão. |
| Continuar ou cancelar um pedido | Completar os dados de uma única intenção pendente, corrigir a proposta ou cancelar sua execução antes do envio ao executor. |

Criação com participantes, convites, recorrências, seleção de outra agenda, edição de séries inteiras, áudio e interpretação de imagens ficam fora deste primeiro agente. A API atual de criação também não aceita `location`; o local pode ser atualizado pela edição existente. Reagendamento de evento de dia inteiro não é suportado. Lembretes, briefings, cards por IA e painel pertencem às próximas etapas.

## Responsabilidades

O LLM interpreta o pedido e propõe dados estruturados ou uma pergunta. A aplicação decide quais ferramentas podem ser usadas, valida os argumentos, controla contexto e consentimento e chama os serviços. As regras de calendário permanecem nos serviços Python existentes.

O percurso implementado é: WhatsApp → entrada validada e persistida → n8n → orquestrador Python → modelo/ferramentas autorizadas → resposta pelo provedor. A [parte 4](contexto-conversa.md) implementou a persistência das consultas HTTP; a [parte 10](whatsapp-agente.md) integrou texto, vínculo privado de conversa, fila, worker e resposta dinâmica ao WhatsApp. Somente metadados técnicos entram no n8n.

| Camada | Responsabilidade |
|---|---|
| Adaptador de modelo | Enviar contexto mínimo, receber resposta/chamada estruturada e permitir trocar o provedor sem alterar os serviços da agenda. |
| Orquestrador Python | Validar ferramentas, aplicar limites de execução, obter esclarecimentos, controlar autorização e interpretar resultados estruturados. |
| Estado da conversa | Resumo de consultas na parte 4 e rascunho/campos/seleção de candidatos na parte 5. Propostas e aprovações vinculadas implementadas na parte 6; a parte 7 executa criação por comando HTTP explícito e mantém seu resultado em ledger separado. A parte 8 acrescentou edição/reagendamento com ledger de alterações separado. A parte 9 acrescentou exclusão com aprovação obrigatória e ledger separado por proposta. Integração WhatsApp dinâmica implementada na parte 10, com vínculo privado e fila durável. |
| Serviços/rotas de agenda | Datas/fusos, seleção, conflitos, identidade de criação, ETag e confirmação do resultado no Google. |
| n8n e transporte WhatsApp | Encaminhamento autenticado e envio com controle de repetição, sem decidir consentimento pelo texto do modelo. |

O desenho usa chamadas estruturadas e adaptadores simples, sem LangChain. A parte 2 acrescentou o adaptador OpenAI configurável; a tentativa real identificou créditos esgotados, preservados no [histórico da conexão](llm-api.md). Na parte 10, a seleção explícita passou a [Gemini](gemini-api.md), validado com dados fictícios. Uma biblioteca adicional precisa resolver uma necessidade concreta antes de entrar nas dependências.

## Catálogo de ferramentas

Os nomes abaixo representam o contrato lógico do agente implementado. As rotas e serviços indicados já existem, salvo as pendências explícitas; as ferramentas de consulta foram conectadas na [parte 3](consultas-agente.md); a criação foi conectada na [parte 7](criacao-agente.md) por comando explícito sobre o rascunho, sem ferramenta de escrita livre no LLM. A [parte 8](alteracoes-agente.md) conectou edição/reagendamento por comandos HTTP explícitos; a [parte 9](exclusao-agente.md) conectou a exclusão ao consumo obrigatório da proposta aprovada. Os caminhos HTTP usam o prefixo `/api/v1` e preservam o [contrato v1](contrato-api-v1.md).

| Ferramenta | Base existente | Condições para uso |
|---|---|---|
| `prepare_request` | `RequestPreparationService.prepare`; rota `POST /requests/prepare` **implementada na parte 3** | Preparar `create`, `list`, `reschedule`, `edit` ou `delete`. Seleção por título usa internamente `EventSelectionService`; não há rota própria de seleção. `ready` não é autorização de escrita. |
| `list_events` | `GET /events` | `date`, `period: day\|week\|month`, `time_zone`. Retornar somente os campos mínimos da consulta. |
| `get_event` | `GET /events/{event_id}` | ID opaco completo proveniente do backend e fuso. Não inventar ID ou interpretar erro como ausência. |
| `create_event` | `InterviewService` e gateway existentes; comando `POST /agent/create` **implementado na parte 7** | Rascunho completo e comando explícito. Recebe somente `conversation_id`; parâmetros, agenda e `operation_id` são controlados pelo servidor. Proposta ativa precisa de aprovação exata e consumo; replay apenas reconcilia. |
| `reschedule_event` | `RescheduleService` existente; `POST /agent/reschedule` **implementado na parte 8** | Rascunho completo e comando explícito; ID, agenda e versão conferidos, aprovação quando ativa, conflitos e readback. Repetição somente consulta. |
| `edit_event` | `EventDetailsService` existente; `POST /agent/edit` **implementado na parte 8** | Rascunho completo e comando explícito; campos omitidos preservados, `""` limpa descrição/local. Agenda/versão, aprovação ativa e resultado conferidos. |
| `preview_deletion` | `ConfirmationService`; `POST /agent/confirmations/preview` | Emitir proposta sobre o rascunho `delete`, vinculada à agenda/ID/versão e aos dados apresentados. Apresentar e obter aprovação explícita; a rota direta anterior de prévia continua disponível. |
| `delete_event` | `EventDeletionService`; `POST /agent/delete` **implementado na parte 9** | Rascunho completo e proposta exata aprovada obrigatórios. Consumo único, token servidor, agenda/ETag/ID vinculados, ausência conferida e replay sem outro DELETE. |

Nenhuma ferramenta aceita credenciais, destino HTTP livre, comandos de terminal, SQL, `calendar_id`, destinatário WhatsApp ou identificação arbitrária de outro usuário. A aplicação valida nomes e campos por lista permitida; uma saída do modelo não cria ferramentas novas. Tokens, ETags, IDs de operação e associação de conversa permanecem sob controle do código.

## Dados, datas e seleção

- Usar o relógio e o fuso da aplicação para resolver “hoje”, “amanhã” e “próxima semana”. O contexto precisa informar esse instante de referência. Os serviços atuais validam valores absolutos; ainda não interpretam linguagem natural.
- Converter a proposta para data ISO e instantes com offset coerente com o fuso IANA. Apresentar data, início, término e fuso de forma legível nas mudanças.
- Perguntar por término ou duração quando não houver informação suficiente; não inventar duração, empresa, local, participantes ou horário. Expressões com mais de uma interpretação exigem esclarecimento.
- O fuso configurado pode ser usado como padrão informado ao usuário. Se ele pedir outro fuso, validar e mostrar a conversão antes de efetivar uma mudança quando a interpretação não estiver clara.
- Semana e mês seguem os períodos civis já implementados, com fim exclusivo. Não apresentar regras de “somente datas futuras” ou duração máxima como proteções existentes: o backend atual não oferece essas duas políticas gerais.
- Zero ou vários candidatos não autorizam alteração. Mostrar opções mínimas e aguardar identificação; revalidar o evento antes de executar. Um ID citado pelo usuário também precisa ser consultado no backend.

## Autorização e confirmação

Uma instrução explícita e inequívoca para criar, editar ou reagendar autoriza aquela mudança quando todos os dados estão completos e validados. Não exigir uma segunda confirmação genérica para cada pedido. Se faltarem informações, houver ambiguidade ou o usuário apenas perguntar por uma possibilidade, preparar/perguntar antes de executar.

Exclusão sempre exige prévia e confirmação específica posterior. O token da prévia comprova a versão, não o consentimento. O registro da aplicação deve vincular conversa autorizada, ação, ID do evento, versão/prévia, conteúdo exato aprovado e mensagem de confirmação. O LLM não recebe, fabrica ou escolhe esse token.

“Sim” só pode resolver uma única proposta pendente e válida naquela conversa. Se o pedido, o evento, os dados ou a versão mudarem, a aprovação anterior deixa de valer. Confirmar outra prévia quando necessário. A [parte 6](confirmacoes-agente.md) implementou validade, persistência e consumo interno único da confirmação no banco privado de conversas. O registro não executa a escrita. Na parte 7, o comando HTTP de criação consome a proposta aprovada correspondente antes de escrever; sem proposta, o comando explícito pode executar um pedido completo sem confirmação genérica adicional. Na parte 8, edição e reagendamento também conferem agenda, evento, versão e projeção da proposta ativa, consomem sua aprovação e aplicam a mudança condicional com `If-Match`. A [parte 9](exclusao-agente.md) exige essa aprovação para toda primeira tentativa de exclusão, consome a proposta uma vez, vincula o DELETE à versão e confere a ausência.

Cancelar antes do início da execução descarta a intenção pendente. Cancelar depois do envio ao Google não desfaz a operação: primeiro conferir o resultado e tratar qualquer reversão como outro pedido explícito. Aprovação anterior nunca autoriza outra entrevista ou outra ação.

## Contexto e privacidade

Manter somente os campos necessários da intenção ativa e o resumo mínimo da conversa. Candidatos e operações pertencem à conversa do remetente autorizado; não compartilhar memória entre usuários. Não usar o histórico de uma entrevista como instrução de sistema.

Os bancos de inbox/outbox guardam identidade e estado técnico, não o texto da conversa nem seu consentimento. A [parte 4](contexto-conversa.md) criou um banco privado separado para o último período e a seleção única de consulta, com expiração por inatividade, descarte e continuidade após reinício. Naquela parte não guardava rascunho ou candidatos. A [parte 5](esclarecimentos-agente.md) acrescentou campos estruturados, pendências e referências de candidatos, sem transcrição ou consentimento. A [parte 6](confirmacoes-agente.md) acrescentou a proposta, a aprovação e seu consumo único. A [parte 7](criacao-agente.md) acrescentou o executor HTTP de criação e um ledger separado, sem alterar o schema 3 do contexto.

Enviar ao modelo somente o texto e os campos da agenda necessários à decisão; omitir telefone, e-mail, credenciais, callback privado, tokens, resposta bruta de provedor e descrições sem necessidade para o pedido. Descrições/local só entram quando a edição exigir. Não gravar prompts, mensagens completas ou respostas privadas em logs, exports, prints ou exemplos públicos. A seleção atual e a configuração estão no [guia Gemini](gemini-api.md); o [guia OpenAI](llm-api.md) conserva o diagnóstico anterior de créditos. As sondagens reais não validam a qualidade geral da interpretação nem autorizam novas chamadas por si mesmas.

## Resultados e limites de execução

| Resultado estruturado | Ação do orquestrador |
|---|---|
| `ready` | Na rota de diálogo, pedido preparado sem escrita. No transporte WhatsApp, o orquestrador só liga o resultado ao executor após conferir pedido explícito, campos e autorização; exclusão sempre exige prévia aprovada. |
| `needs_clarification` / `needs_correction` | Fazer a pergunta necessária e aguardar; não chamar executor. |
| `needs_confirmation` | Apresentar a prévia e registrar a espera por consentimento. |
| `conflict` | Informar o intervalo conflitante com dados mínimos e pedir outro horário; não escolher uma alternativa sozinho. |
| `not_found` | Informar ausência somente quando o backend a confirmar; pedir outra referência se necessário. |
| `succeeded` com `mutation_state: confirmed` | Relatar o resultado conferido, incluindo reutilização ou ausência de mudança quando indicado. |
| Falha de leitura | Informar indisponibilidade; não transformar em lista vazia. |
| `mutation_state: unknown`, timeout ou resposta perdida | Marcar o resultado como incerto, bloquear reenvio cego e conferir o estado pelo backend antes de decidir recuperação. |

Interpretar os campos estruturados, sem analisar a mensagem textual ou exceção para inventar uma classificação. `confirmed` no envio WhatsApp significa aceite do provedor; não equivale a entrega ou leitura no aparelho.

Gerar e persistir a identidade técnica de uma criação antes da primeira tentativa; conservar a mesma intenção entre tentativas e nunca trocar o ID para contornar bloqueios. PATCHs diretos e a exclusão direta não têm uma garantia geral de idempotência persistente por `operation_id`. Os comandos do agente acrescentam ledgers próprios: alterações ligadas ao pedido e exclusões ligadas à proposta aprovada, sem reenvio de operações incertas. A deduplicação da mensagem de entrada não substitui o controle de cada ação e de cada resposta.

O adaptador implementado aplica limites de chamadas, tempo e tamanho de entrada/saída, descritos nos guias de [conexão](llm-api.md) e [diálogo](esclarecimentos-agente.md). O comando de criação usa o rascunho persistido e não chama o modelo novamente. Para o primeiro agente, processar uma intenção de alteração por vez; se houver vários pedidos de escrita na mesma mensagem, pedir que o usuário escolha por qual começar. Não ativar retries automáticos de gravações ou de envio WhatsApp após resultado incerto.

Conteúdo de usuário, título, descrição, local e retorno de ferramenta são dados não confiáveis. Instruções nesses campos não podem mudar regras, liberar outros remetentes, revelar segredos ou executar ferramentas fora da lista. Status de entrega/leitura não são mensagens do usuário e não chamam o LLM nem geram resposta automática.

## Pendências originalmente identificadas — resolvidas na parte 10

1. **Preparação HTTP — resolvida na parte 3:** rota autenticada implementada e conferida nos testes, reutilizando o serviço sem executar escritas. Veja [consultas e preparação](consultas-agente.md).
2. **Texto de entrada:** mensagens validadas são persistidas antes do ACK na fila privada; n8n continua recebendo somente os cinco campos de metadados.
3. **Resposta dinâmica:** nova ativação `/whatsapp/sandbox/agent`; worker reserva a resposta e envia somente ao destinatário privado autorizado.
4. **Estado da conversa:** vínculo privado entre remetente e conversa reutiliza consultas, rascunho, seleção e confirmações. O transporte chama os executores existentes conforme a autorização controlada pelo servidor.
5. **Tempo de processamento:** callback não espera IA ou envio; processamento durável por remetente, com bloqueio de incertezas. [Falhas, reinício e limites](whatsapp-agente.md).

## Sequência da etapa 11

- [x] 1. Definir pedidos, ferramentas e limites do agente.
- [x] 2. Conectar o LLM pela API — adaptador independente e dois testes fictícios reais Gemini HTTP 200 na parte 10; [configuração](gemini-api.md).
- [x] 3. Disponibilizar consultas/ferramentas e preparação HTTP — testes locais e quatro leituras Google reais com planejador simulado no marco original; Gemini real validado separadamente na parte 10.
- [x] 4. Implementar contexto mínimo privado por conversa — SQLite separado, TTL, isolamento, CAS e reinício conferidos; [escopo e testes](contexto-conversa.md).
- [x] 5. Tratar dados faltantes e ambiguidades na conversa — rascunho persistente, correções, escolha explícita, releitura e testes; [detalhes](esclarecimentos-agente.md).
- [x] 6. Registrar e validar confirmações vinculadas à proposta — conteúdo/evento/versão, prazo, revogação e consumo único conferidos; [detalhes](confirmacoes-agente.md).
- [x] 7. Criar eventos a partir de pedidos completos — comando explícito HTTP, validações existentes, proposta aprovada quando ativa, ledger durável e replay por consulta; ligação WhatsApp/n8n ainda fora deste marco. [Contrato, testes e limites](criacao-agente.md).
- [x] 8. Editar informações e reagendar o evento correto — comandos HTTP explícitos, snapshot/versão vinculados, `If-Match`, aprovação ativa, campos preservados e reconciliação sem outro PATCH; [testes e limites](alteracoes-agente.md).
- [x] 9. Excluir após confirmação específica da prévia — comando HTTP, aprovação obrigatória/exata, consumo único, versão/agenda vinculadas, ausência conferida e replay somente por leitura; [contrato, testes e limites](exclusao-agente.md).
- [x] 10. Conectar texto e resposta dinâmica ao fluxo WhatsApp/n8n — fila, worker, autorização e fluxo publicado; [provas e limites](whatsapp-agente.md).
- [x] 11. Testar conversas, conflitos, repetições e falhas — 1.561 testes, 53 novos, 29 verificações HTTP e provas Gemini com agenda fictícia; [resultados e limites](testes-conversas-agente.md).
- [x] 12. Revisar entregas, documentação, evidências e planner — [fechamento da etapa 11](fechamento-etapa-11.md), com resultados anteriores preservados e próxima etapa de lembretes.

## Histórico da definição — parte 1

Revisamos o código público da API, os serviços e o fluxo atual. Mapeamos oito ferramentas lógicas, explicitamos as cinco pendências acima e registramos cenários de aceite com dados fictícios. A validação documental confere os caminhos/endpoints, o formato dos exemplos e a preservação dos arquivos existentes; não é um teste de inferência ou conversa real.

Não houve conexão com LLM, chamada ao Google, envio WhatsApp, alteração de workflow ou instalação de dependências. Não foi criado outro print do mesmo canvas, pois este marco é uma definição de contrato sem mudança visual no produto. Registro local: `093_agente_ia_definicao.json`, no [índice de evidências](evidencias.md).

A [parte 5](esclarecimentos-agente.md) expõe o diálogo preparatório em `POST /api/v1/agent/dialogue`. Um `ready` não executa nem confirma uma alteração. A chave real foi configurada, mas a OpenAI retornou `429 credit_balance_exhausted`; as provas de diálogo usam modelo e Google controlados. Registros 099 a 101.

A parte 6 expõe os comandos explícitos de confirmação e intercepta respostas afirmativas/recusas inequívocas quando há proposta ativa, sem chamar o LLM. Qualquer novo turno de preparação bem-sucedido descarta a confirmação anterior, mesmo com conteúdo igual. Aprovar não executa; o controle não impõe confirmação genérica extra a todo pedido inequívoco de criação/edição/reagendamento. O [executor de exclusão da parte 9](exclusao-agente.md) exige consentimento específico para a proposta exata, sempre antes da primeira tentativa.

Na [parte 7](criacao-agente.md), concluída no escopo do executor HTTP em 25/10/2026, `POST /api/v1/agent/create` recebe somente a conversa e reutiliza os serviços de criação existentes. São 85 testes novos (24 de persistência, 27 de serviço, 16 de gateway e 18 de API), totalizando 1.160 aprovados, além de 27 casos HTTP TCP com dois processos e fixtures de modelo/agenda. O diálogo continua preparatório, a proposta ativa exige aprovação exata e consumo, e repetições `running`/`unknown`/`confirmed` somente reconciliam por GET. O ledger de criações usa schema 1 e não expira resultados incertos; o contexto permanece no schema 3. Registros 104/105 no [índice de evidências](evidencias.md), sem chamadas reais aos provedores ou envios WhatsApp neste marco. Naquele marco, a chave OpenAI estava configurada, mas faltavam créditos e não houve novo teste real. A seleção Gemini e as provas posteriores pertencem às partes 10 e 11.

## Edição e reagendamento — parte 8

Em 25/10/2026, os comandos `/agent/edit` e `/agent/reschedule` passaram a executar o rascunho completo com agenda, ID e ETag vinculados. O ledger privado `agent-updates.sqlite3` usa schema 1, sem TTL; contexto schema 3 e criação schema 1 não foram migrados. Repetição de estados em andamento/incertos/confirmados só lê o estado; uma divergência posterior não reaplica a alteração. O diálogo continua preparando, e WhatsApp/n8n dinâmico permanece na parte 10.

1.257 testes Python e 33 casos HTTP TCP locais aprovados; são 97 novos testes (26 de persistência, 33 de serviço, 20 de gateway e 18 de API), além do ensaio com dois processos e SQLite reais. Modelo, gateway e mensagens aprovativas são fixtures, sem consentimento humano real ou chamadas reais Google/LLM/WhatsApp. Registros 106/107 no [índice](evidencias.md). O [guia de alterações](alteracoes-agente.md) distingue confirmação dos valores atuais de prova de autoria de uma escrita.

## Exclusão aprovada — parte 9

Em 25/10/2026, `POST /api/v1/agent/delete` passou a executar somente a proposta de exclusão aprovada naquela conversa. Recebe só `conversation_id`; agenda, evento, versão, hash do conteúdo e identificador de operação vêm do servidor. O consumo único precede o token técnico e o DELETE com `If-Match`; a ausência é conferida por leitura. Sem aprovação específica, um pedido completo não executa a exclusão.

O ledger privado `agent-deletions.sqlite3` usa schema 1, separado e sem TTL. Uma proposta tem no máximo uma reserva; `running`/`unknown` bloqueiam nova proposta ou conteúdo naquela conversa. Replay apenas consulta o estado; ausência observada não prova autoria da remoção. Contexto schema 3, criação schema 1 e alterações schema 1 não foram migrados. WhatsApp/n8n dinâmico permanece na parte 10.

1.367 testes Python e 62 casos HTTP TCP locais aprovados; são 110 novos testes (27 de persistência, 39 de serviço, 20 de gateway e 24 de API), com dois processos e SQLite reais. Modelo, gateway e mensagens aprovativas são fixtures, sem consentimento humano real nem chamadas reais Google/LLM/WhatsApp. Registros 108/109 no [índice](evidencias.md). [Contrato e limites](exclusao-agente.md).


## Agente na conversa — parte 10

Em 25/10/2026, a ligação dinâmica foi implementada com fila, worker e outbox próprios, contexto persistente e execução pelos serviços existentes. O diálogo HTTP isolado continua preparatório. O transporte WhatsApp acrescenta a autorização do pedido e a ligação aos executores; uma aprovação explícita de exclusão é sempre obrigatória. A parte 11 verificou conversas completas, conflitos e falhas com provedores controlados e sondagens Gemini com agenda fictícia; a consulta real recebida pelo usuário pertence à parte 10. [Configuração, testes e recuperação](whatsapp-agente.md).


## Conversas e falhas — parte 11

1.561 testes Python aprovados (53 novos), 29 verificações HTTP finais e sondagens Gemini com agenda fictícia. Nove ajustes comprovados nos testes aplicados; nenhuma mutação real ou envio WhatsApp neste marco. A revisão final da parte 12 concluiu a etapa no escopo documentado. [Cenários, falhas preservadas, reprodução e limites](testes-conversas-agente.md).


## Fechamento — parte 12

As entregas, a correspondência das provas e as pendências foram consolidadas no [fechamento da etapa 11](fechamento-etapa-11.md). O marco é documental: não repete as suítes, não muda os executores e não realiza chamadas Gemini/Google ou envios WhatsApp. A próxima etapa do planner é a 12, de lembretes.


## Evolução — etapa 13, parte 1

O [contrato de resumos e preparo](resumos-preparo.md) define novas capacidades informativas, com fatos/disponibilidade calculados em Python e narrativa limitada às fontes. Elas ainda não foram implementadas ou registradas no agente, e as consultas atuais continuam sem enviar eventos ao LLM. Briefing contextual exigirá projeção própria e novas intenções; os nomes do contrato não são rotas nem ferramentas disponíveis. A futura entrada nesse modo deverá separar sugestões de autorização e revogar por CAS uma proposta CRUD não consumida, com aviso e preservação do rascunho, sem liberar operações incertas. Nada desse comportamento foi ativado na definição da parte 1. Registro 140; próxima parte: consulta e composição do período.


## Coleta disponível — etapa 13, parte 2

O [contexto factual de semana/mês](coleta-contexto-agenda.md) já é produzido por serviço, API autenticada avulsa e CLI. Fuso da agenda, fonte completa e contagens são conferidos sem LLM ou mudança na conversa. As ferramentas e intenções antigas continuam inalteradas, e os novos nomes de resumo/briefing ainda não estão registrados no agente. A integração do contexto ao modelo começa na parte 3; novas intenções WhatsApp e isolamento de confirmação continuam na parte 8. Suíte atual: 2.044 Python (+88); registros 141/142.


## Evolução — etapa 13, parte 3

A [organização de resumos com IA](resumos-com-ia.md) já reutiliza a coleta por API/CLI separados, com plano estrutural validado e alternativa factual. O modelo recebe somente agregados/período/catálogo; não recebe títulos/IDs nem redige fatos livres. As consultas e intenções antigas, os estados de confirmação, os adaptadores e os fluxos seguem preservados. Novas intenções WhatsApp continuam na parte 8. Testes atuais: 2.126 Python (+82); registros 143/144.


## Evolução — etapa 13, parte 4

A [disponibilidade avulsa](horarios-livres.md) calcula opções pela agenda autorizada, com a mesma política de opacidade/provisórios e limites semiabertos, mais fonte limitada completa, duração/faixa e margem explícitas. Não altera o verificador legado nem aceita exclusão arbitrária de ID. Ainda não é intenção nova no WhatsApp; sugestões vinculadas ao evento e sua revalidação seguem na parte 5. Provas da parte 4: 2.225 Python (+99), duas leituras reais e seis revalidações de conflito; registros 145/146.
