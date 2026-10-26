# Contexto de conversa — etapa 11, parte 4

Atualização da parte 10, em 25/10/2026: o [orquestrador WhatsApp](whatsapp-agente.md) passou a reutilizar este contrato no fluxo n8n, com contexto privado, fila e saída reservada. As descrições de validação abaixo pertencem aos marcos originais; os testes de mutação da parte 10 também usam provedores fictícios. [Gemini real](gemini-api.md) tem prova separada.

O agente mantém um resumo mínimo das consultas para continuar pedidos como “e na semana seguinte?” ou “consulte esse evento”. Cada conversa é criada explicitamente pela API e possui memória própria no SQLite local. Consultas sem `conversation_id` continuam avulsas, sem memória implícita nem criação desse banco.

O modelo interpreta a continuação; os serviços Python validam os argumentos e consultam a agenda. As rotas de consulta continuam somente de leitura. O resumo de consultas não autoriza mudanças. A memória compartilhada passou a guardar rascunho na [parte 5](esclarecimentos-agente.md) e proposta/aprovação na [parte 6](confirmacoes-agente.md); a [parte 7](criacao-agente.md) executa criação apenas por comando HTTP explícito sobre o pedido completo.

## Contrato HTTP

Todas as rotas herdam a autenticação Bearer, `X-Request-ID` e `Cache-Control: no-store` da API existente.

| Operação | Entrada | Resposta |
|---|---|---|
| `POST /api/v1/agent/conversations` | JSON `{}`, sem query; corpo bruto de até 1 KiB. | HTTP 201; `action: conversation_create`, `data: {conversation_id, expires_in_seconds, execution_allowed: false}`. |
| `POST /api/v1/agent/query` | JSON com `message` e, opcionalmente, `conversation_id`. | O mesmo envelope de consulta da [parte 3](consultas-agente.md), sem devolver a memória ou o ID da conversa. |
| `DELETE /api/v1/agent/conversations/{conversation_id}` | Sem query e sem corpo. | HTTP 204, com corpo vazio. Apaga somente o contexto; não altera o Google Calendar. |

Criar a conversa não chama o modelo ou o Google. O identificador é um UUID4 canônico emitido pelo servidor; inventar um UUID válido não cria uma conversa. O cliente guarda o ID retornado e o envia nas consultas seguintes. `null`, formatos alternativos de UUID, campos extras e JSON com chaves repetidas são recusados. A consulta mantém os limites anteriores: 24 KiB de corpo e até `LLM_MAX_INPUT_CHARS` caracteres na mensagem.

Exemplo de sequência, substituindo `<id retornado>` pelo valor recebido na criação:

```json
{"message":"Quais entrevistas tenho em 17 de junho de 2030?","conversation_id":"<id retornado>"}
```

```json
{"message":"E na semana seguinte?","conversation_id":"<id retornado>"}
```

O envelope usa `operation_id: null` e `mutation_state: not_applicable`. `execution_allowed: false` indica que nenhuma escrita na agenda foi autorizada. A interpretação linguística desses exemplos depende do LLM configurado; um plano controlado nos testes não comprova inferência real.

## Memória e isolamento

O resumo de consultas usa os três campos abaixo. A [parte 5](esclarecimentos-agente.md) acrescentou `pending_request` à memória compartilhada, com rascunho, nomes das pendências e referências de candidatos:

| Campo | Conteúdo |
|---|---|
| `last_list` | Última consulta de período: `date` ISO, `period: day\|week\|month` e fuso IANA. |
| `selected_event_id` | ID exato de um evento selecionado, ou `null`. |
| `selected_time_zone` | Fuso da seleção, preenchido junto com o ID. |

Uma listagem bem-sucedida atualiza o período. Se houver exatamente um resultado, ele vira a seleção; listas vazias ou com vários eventos removem a seleção. Consultar um ID com sucesso seleciona esse evento. Uma ausência confirmada remove a seleção e preserva o período. Perguntas preservam o resumo; falhas não sobrescrevem nem renovam o contexto.

O proprietário é derivado de SHA-256 com namespace e a chave autenticada da API. O banco guarda o vínculo proprietário/conversa como hash, sem a chave ou o UUID bruto. A API atual tem uma única identidade por chave: clientes usando a mesma chave pertencem ao mesmo escopo. Rotacionar a chave cria outro escopo; a chave nova não lê nem exclui conversas antigas. Isso ainda não é autenticação de múltiplos usuários.

## Uso pelo agente

O [orquestrador](../app/services/agent_queries.py) envia ao LLM somente o último período, os fusos e um indicador de existência de seleção. O ID selecionado permanece local. Para “esse evento”, o plano `get_context_event` pede uma releitura; o Python resolve o ID armazenado e consulta novamente o Google. Sem seleção, devolve esclarecimento. A memória não comprova que um evento continua existindo ou com os mesmos dados.

`get_event` continua exigindo o ID literal na mensagem atual. “Hoje” e “amanhã” usam a referência atual do servidor; não a data antiga do contexto. O texto atual do usuário continua sendo enviado ao LLM quando configurado e pode conter dados pessoais ou um ID explícito; não há remoção automática desse conteúdo. Os resultados e detalhes dos eventos não voltam ao modelo.

## Configuração, validade e concorrência

No `.env` privado:

```dotenv
CONVERSATION_DATABASE_PATH=data/agent-conversations.sqlite3
CONVERSATION_TTL_SECONDS=1800
```

O caminho relativo é resolvido a partir da raiz do projeto. O banco é separado da agenda, inbox e outbox. A validade padrão é **30 minutos**, configurável entre 60 e 86400 segundos. Um salvamento válido renova a validade, inclusive uma pergunta que preserve a memória; ler o banco, falhar no modelo ou falhar no Google não renova. Conversas expiradas são removidas durante as operações do store, sem tarefa periódica em segundo plano. O limite padrão é **1000 conversas ativas** por banco.

Cada chamada carrega uma revisão e fecha a transação antes de consultar o modelo ou o Google. Depois da validação do resultado e da nova memória, o salvamento usa comparação de revisão (CAS). Se outra chamada tiver atualizado a conversa, a segunda recebe HTTP 409, sem sobrescrever a atualização nem repetir automaticamente a consulta. O cliente deve apresentar o conflito e permitir um novo pedido.

| Situação | Resposta segura |
|---|---|
| Conversa inexistente, expirada, apagada ou de outra chave | 404 / `conversation_not_found`, antes de construir o serviço de modelo. |
| Atualizações concorrentes | 409 / `conversation_conflict`. |
| Banco indisponível ou incompatível | 503 / `conversation_unavailable`. |
| Capacidade atingida | 503 / `conversation_capacity`. |
| Entrada inválida | 422 / `invalid_request`, antes de abrir o store. |
| Conversa válida, mas chave do LLM ausente | 503 / `llm_not_configured`; memória, revisão e validade preservadas. |

As respostas não expõem caminhos, SQL, chaves, memória ou detalhes de exceções. Não há repetição automática em falhas.

## Privacidade e exclusão

O resumo de consultas guarda referência, revisão e datas de controle, sem transcrição ou resultados completos. Desde a parte 5, o rascunho pendente também pode guardar título, descrição, local e texto de busca necessários à preparação, conforme o [guia de esclarecimentos](esclarecimentos-agente.md). **O ID selecionado do Google fica em texto legível dentro da memória local**; esse arquivo não é criptografado pela aplicação. Deve permanecer no diretório privado `data/`, fora do Git, junto das cópias de segurança privadas.

Excluir uma conversa ou purgar sua expiração remove o registro ativo. Isso representa exclusão lógica do contexto e impede seu uso normal pela API. Não apaga os ledgers separados de criações, alterações e exclusões do agente nem reverte eventos no Google; identidades e resultados incertos precisam ser preservados para reconciliação. Não é promessa de eliminação forense de cópias, backups ou armazenamento subjacente. Não se deve publicar o SQLite para demonstrar a funcionalidade.

## Validação e limites deste marco

**Validado em 24/10/2026: 865 testes Python aprovados**, sem falhas, erros ou testes ignorados. São 88 testes novos sobre os 777 anteriores: 31 de persistência, 19 do orquestrador, 35 de API e três de configuração. Os 16 testes existentes do transporte também verificam o schema da quarta ferramenta.

Os testes de [store](../tests/test_conversation_store.py), [serviço](../tests/test_agent_query_context.py) e [API](../tests/test_conversation_api.py) cobrem isolamento, persistência, expiração, validade das memórias, falhas, ausência de chave e concorrência. A suíte usa modelo e Google substituídos; não exige secrets.

O ensaio complementar aprovou **19 casos por HTTP TCP em loopback**, com SQLite real e dois processos sucessivos da API. A seleção e o último período sobreviveram ao reinício; conversas ficaram isoladas, seleções ambíguas/ausentes foram descartadas e a exclusão foi conferida por uma nova consulta. Modelo e Google foram fixtures locais. Zero chamadas reais ao LLM/Google e zero envios WhatsApp; nenhum banco da aplicação normal foi usado. Registros locais 097/098 no índice de evidências.

Para reproduzir a suíte local:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

A conexão real com o LLM segue o [guia da parte 2](llm-api.md). O [diálogo de ambiguidades foi implementado na parte 5](esclarecimentos-agente.md); o registro de confirmações foi implementado na [parte 6](confirmacoes-agente.md), e o [executor HTTP de criação](criacao-agente.md) foi acrescentado na parte 7. A [parte 8](alteracoes-agente.md) acrescentou os comandos HTTP de edição/reagendamento; a [parte 9](exclusao-agente.md) acrescentou exclusão com aprovação específica obrigatória. A integração dinâmica segue na parte 10 do [escopo](agente-ia.md). O fluxo WhatsApp publicado permanece com a resposta fixa; vincular remetente e conversa e responder dinamicamente pertence à integração posterior. Evidências de testes, HTTP e confirmação do provedor devem continuar distinguidas no [índice](evidencias.md).

Na parte 5, o schema era 2: migra os registros válidos da versão 1 ao próximo uso, de forma atômica, sem mudar revisão ou prazo. As consultas preservam o rascunho pendente. O relatório de 865 testes acima é histórico da parte 4; os resultados históricos seguintes estão nos guias das partes 5 e 6, e a validação atual do executor HTTP está no [guia de criação](criacao-agente.md). A chave real está configurada, mas a inferência continua pendente de créditos e não foi testada novamente na parte 7.

Desde a [parte 6](confirmacoes-agente.md), o schema atual é **3** e a memória inclui `confirmation`: proposta, parâmetros, calendário/versão privados e metadados de aprovação/consumo. Migra registros válidos das versões 1/2 em uma transação, preservando vínculo, revisão, vencimento e rascunho. Uma mudança do pedido invalida a aprovação; consultas de leitura conservam o vínculo original. O prazo próprio da proposta é de cinco minutos e não é renovado por reuso ou status.

## Criação e retenção separada — parte 7

O schema do contexto continua **3**, sem migração adicional nesta parte. `POST /api/v1/agent/create` recebe apenas `conversation_id`, relê o rascunho completo e confere sua revisão. Quando existe proposta ativa, exige aprovação correspondente e usa `consume_confirmation` antes da escrita. O diálogo e as consultas continuam sem executar mutações.

A identidade da execução é registrada separadamente em **`data/agent-creations.sqlite3`**, schema **1**, configurável por `AGENT_CREATION_DATABASE_PATH`. Esse ledger não tem TTL: `running` e `unknown` bloqueiam nova escrita e só admitem reconciliação por consulta; `confirmed` também é reconferido sem POST. Expirar/apagar a conversa não elimina esses registros. A recuperação pública ainda exige conversa válida e rascunho completo; não existe endpoint administrativo de recuperação por operação neste marco. [Contrato e limites da criação](criacao-agente.md).

Os resultados de 865 testes e 19 casos HTTP acima são históricos da parte 4. Na parte 7, a suíte completa chegou a **1.160 testes aprovados**, e o ensaio complementar aprovou **27 casos HTTP TCP com dois processos**, SQLite real e modelo/gateway como fixtures. Zero chamadas reais Google/LLM ou envios WhatsApp; a inferência real não foi testada novamente. Registros 104/105 no [índice de evidências](evidencias.md).

## Alterações e retenção separada — parte 8

O contexto continua no schema **3** e o ledger de criação no schema **1**, sem migração adicional nesta parte. `POST /api/v1/agent/edit` e `/agent/reschedule` recebem só `conversation_id`, exigem o rascunho da ação completo e revalidam a revisão. Havendo proposta ativa, conferem ação, parâmetros, agenda, evento, versão e projeção, reservando seu consumo único antes de escrever.

A execução de alterações fica em **`data/agent-updates.sqlite3`**, schema **1**, configurável por `AGENT_UPDATE_DATABASE_PATH`. Guarda o snapshot completo anterior e a versão privadamente, além da ação, parâmetros, tentativa e resultado. Não tem TTL; expirar/apagar o contexto não elimina esse histórico nem desfaz eventos. `running`, `unknown` e `confirmed` somente admitem reconciliação por leitura nas repetições. Recuperação pública ainda exige conversa válida e rascunho completo; não há endpoint administrativo de liberação de incertezas. [Contrato e limites](alteracoes-agente.md).

Na parte 8, 1.257 testes Python e 33 casos HTTP TCP locais aprovados, com dois processos e SQLite reais e provedores fictícios. Registros 106/107; zero chamadas reais Google/LLM ou envios WhatsApp. O fluxo publicado continua com resposta fixa até a parte 10.

## Exclusões e retenção separada — parte 9

O contexto permanece no schema **3**, e os ledgers de criação e alteração permanecem no schema **1**, sem migração adicional. `POST /api/v1/agent/delete` recebe só a conversa e exige rascunho `delete` completo e proposta exata aprovada. Confere revisão, agenda, ID, versão e projeção; `consume_confirmation` reserva o uso único antes do token técnico e da chamada externa. `ready` e intenção de excluir não substituem a aprovação.

A execução fica em **`data/agent-deletions.sqlite3`**, schema **1**, configurável por `AGENT_DELETION_DATABASE_PATH`. A identidade liga proprietário/conversa, `proposal_id` e hash do conteúdo aprovado. Não há TTL de operações: expirar/apagar contexto não elimina o ledger. `running`/`unknown` impedem contornar incertezas com outra proposta; replay só lê o ID na agenda vinculada, sem outro DELETE. Recuperação pública ainda exige conversa e rascunho disponíveis; não há endpoint administrativo para recuperar por operação sem esse vínculo. [Contrato e limites](exclusao-agente.md).

Resultados históricos acima conservam seus números. A parte 9 aprovou 1.367 testes Python e 62 casos HTTP TCP locais aprovados, com dois processos e SQLite reais; modelo/gateway/consentimento como fixtures e zero chamadas reais Google/LLM/WhatsApp. Registros 108/109.
