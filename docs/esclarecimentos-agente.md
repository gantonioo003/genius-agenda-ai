# Esclarecimentos do agente — etapa 11, parte 5

Atualização da parte 10, em 25/10/2026: o [orquestrador WhatsApp](whatsapp-agente.md) passou a reutilizar este contrato no fluxo n8n, com contexto privado, fila e saída reservada. As descrições de validação abaixo pertencem aos marcos originais; os testes de mutação da parte 10 também usam provedores fictícios. [Gemini real](gemini-api.md) tem prova separada.

O agente prepara um único pedido de agenda ao longo de vários turnos. Quando faltam dados, um valor está inválido ou existem vários eventos possíveis, devolve uma pergunta e mantém um rascunho estruturado na conversa. A resposta seguinte completa esse rascunho, sem exigir que a pessoa repita tudo.

Esta parte reutiliza o [serviço de preparação](../app/services/request_preparation.py) e suas validações. O diálogo não possui executor de escrita. Um resultado `ready` significa que os dados estão preparados; não representa execução ou autorização. O registro de confirmações foi acrescentado na [parte 6](confirmacoes-agente.md), e a [parte 7](criacao-agente.md) acrescentou o comando explícito HTTP de criação, separado do diálogo. A [parte 8](alteracoes-agente.md) acrescentou os comandos explícitos HTTP de edição/reagendamento; a [parte 9](exclusao-agente.md) implementou exclusão por comando HTTP com aprovação específica obrigatória. A ligação dinâmica permanece na parte 10 do [escopo](agente-ia.md).

## Contrato HTTP

`POST /api/v1/agent/dialogue` herda a autenticação Bearer, a correlação `X-Request-ID` e `Cache-Control: no-store` da API. Exige `Content-Type: application/json` e exatamente dois campos:

```json
{
  "message": "Quero marcar uma entrevista, mas ainda não defini o horário.",
  "conversation_id": "<UUID4 retornado pelo servidor>"
}
```

O exemplo é ilustrativo. Antes do primeiro turno, crie uma conversa por `POST /api/v1/agent/conversations`, com JSON `{}`, conforme o [guia de contexto](contexto-conversa.md). Não é possível criar uma conversa simplesmente inventando um UUID. Esta rota exige o identificador, enquanto `/api/v1/agent/query` mantém seu modo avulso opcional.

A entrada não aceita query, campos extras, chaves JSON repetidas, `null` no lugar dos campos, mensagem vazia ou Unicode inválido. O corpo bruto é limitado a 24 KiB e a mensagem segue `LLM_MAX_INPUT_CHARS`, até 4000 caracteres. A validação ocorre antes de abrir o contexto ou construir o modelo. Uma conversa inexistente, expirada, apagada ou pertencente a outra chave retorna 404 antes de chamar o LLM.

As respostas de diálogo usam o envelope comum da API, com `operation_id: null` e `mutation_state: not_applied`. Quando `data` existe, contém `execution_allowed: false`:

| `status` | Significado |
|---|---|
| `needs_clarification` | Faltam dados, há ambiguidade ou é necessário selecionar um evento. |
| `needs_correction` | Um dado informado precisa ser corrigido, por exemplo um intervalo inválido. |
| `ready` | Os parâmetros foram preparados e validados; nenhuma operação foi executada. |
| `cancelled` | O rascunho pendente foi descartado; nenhum compromisso foi removido. |
| `failed` | Não foi possível interpretar ou preparar o pedido; não há repetição automática. |

O objeto `data` contém somente `reason`, `question`, `missing_fields`, `invalid_fields`, `event`, `candidates`, `parameters` e `execution_allowed`. Os parâmetros finais aparecem apenas em `ready`; perguntas trazem os campos pendentes ou inválidos. Eventos e candidatos são projeções mínimas produzidas pelo servidor. A resposta não devolve a memória inteira ou o identificador da conversa.

Os erros preservam os códigos seguros já utilizados pelo contexto e pelo modelo: 422 para entrada inválida, 404 para conversa indisponível, 409 para conflito de revisão, 503 para configuração, capacidade ou serviço indisponível, 504 para timeout do modelo e 502 para resposta inválida ou falha operacional. Mensagens brutas do provedor, SQL e caminhos privados não são enviados ao cliente. Erros não são instruções para repetir uma gravação.

## Interpretação e continuidade

O [contrato de diálogo](../app/dialogue_contract.py) oferece quatro ferramentas ao modelo. Elas descrevem intenções; o Python decide quais dados são válidos e quais leituras são necessárias.

| Ferramenta | Comportamento |
|---|---|
| `prepare_request` | Inicia ou completa um rascunho, preservando valores conhecidos e indicando um campo ambíguo quando necessário. |
| `select_candidate` | Solicita a seleção explícita de uma posição entre 1 e 20. O servidor confere a escolha na mensagem atual. |
| `ask_dialogue` | Solicita uma pergunta fixa para informação ambígua, vários pedidos, resposta incompreensível ou pedido fora do escopo. |
| `cancel_request` | Descarta somente o pedido pendente. |

Em `prepare_request`, `mode: start` inicia um pedido novo e `mode: continue` aplica um complemento ao pedido pendente. Os campos anuláveis são obrigatórios no schema: `null` significa que aquele campo não faz parte do complemento, preservando o valor anterior. Não significa apagar título, descrição, local ou outro valor. Strings vazias permitidas no contrato ainda passam pelas validações da preparação.

`ambiguous_field` permite guardar os dados conhecidos e perguntar somente sobre a parte incerta. O servidor retira o valor ambíguo do rascunho e registra o campo que precisa de resposta. Assim, “marque uma entrevista em uma data que pode ter duas interpretações” pode preservar a ação e o título sem escolher uma data por conta própria. A pergunta vem de um conjunto fixo de mensagens, sem copiar instruções arbitrárias devolvidas pelo modelo.

Datas relativas usam o relógio atual do servidor. O agente não deve estimar início, término ou duração. Em pedidos de criação, uma `date` ISO válida pode servir de âncora enquanto os horários ainda não foram informados. Mudar essa data invalida os horários absolutos anteriores; horários explicitamente presentes no complemento atual são tratados como os novos valores. Uma data ambígua não permite conservar um intervalo baseado na interpretação anterior.

Se a pessoa explicitamente questionar o fuso, o servidor mantém essa dúvida pendente. Um turno seguinte sem resposta sobre o fuso não substitui silenciosamente a dúvida pelo fuso padrão. O padrão continua disponível para pedidos que não apresentaram essa ambiguidade.

Sem proposta de confirmação ativa, “sim”, “ok” ou “confirmo” não aprovam uma ação. Com uma proposta ativa, a parte 6 reconhece respostas explícitas para registrar o consentimento, sem executar a ação; “ok” continua ambíguo. Se não responderem à informação pendente, o agente pergunta novamente. Vários pedidos na mesma mensagem exigem escolher um pedido por vez. Cancelar o rascunho permite começar outro pedido sem tocar na agenda.

## Seleção de eventos

Um pedido pode indicar o ID completo e literal da mensagem atual, buscar por título e período ou usar a seleção validada do contexto de consultas. O modelo não recebe IDs anteriores para inventar uma seleção. O serviço resolve a referência localmente e relê o Google quando a preparação exige identificar um evento.

Quando uma busca não encontra eventos, o agente solicita outro identificador ou mais dados. Quando encontra vários candidatos, apresenta até 20 opções e espera a escolha; não seleciona a primeira automaticamente. Mais de 20 resultados exigem restringir o título ou o período. Uma busca nova invalida as referências da busca anterior.

A seleção por posição precisa estar explícita no texto atual, como “2” ou “a segunda”. Um plano `select_candidate` sozinho não basta: o servidor confere se a posição corresponde à mensagem e à lista pendente. “Segunda” isolada pode significar um dia da semana e não é aceita como escolha ordinal.

Cada referência guarda o ID e um fingerprint dos dados relevantes apresentados. Antes de usar uma posição escolhida, a preparação faz uma nova leitura do evento e compara o fingerprint. Se o compromisso mudou, devolve seus dados atuais e pede nova seleção. Se desapareceu ou a leitura falhou, não usa os dados antigos como confirmação do estado atual. Nenhuma dessas verificações grava no Google.

## Rascunho e privacidade

A [memória de conversa](../app/conversation_store.py) conserva os campos de consulta existentes e acrescenta `pending_request`, definido no [contrato de estado](../app/dialogue_state.py):

| Campo | Conteúdo local |
|---|---|
| `fields` | Valores necessários à preparação: ação, fuso, título, início, término, descrição, local, seletor e período. |
| `awaiting` | Nomes dos campos aguardando esclarecimento ou correção. |
| `candidates` | Até 20 referências com ID e fingerprint, sem copiar a lista completa de eventos para a memória. |

Os campos aceitos são `action`, `time_zone`, `title`, `start`, `end`, `description`, `location`, `event_id`, `search_title`, `date` e `period`. Cada valor tem até 4000 caracteres; nomes e valores somam até 20 KiB de UTF-8. Datas e horários ainda inválidos podem permanecer no rascunho para que a pessoa os corrija. Isso não os torna parâmetros válidos para execução.

Ao contrário da memória inicial restrita a consultas, o rascunho pode conter título, descrição, local e texto de busca essenciais ao pedido. **Esses dados e os IDs ficam legíveis no SQLite local; a aplicação não criptografa esse arquivo.** Não são guardados transcrições, prompts completos ou histórico de mensagens. Não existem campos próprios para telefone ou credenciais; campos livres podem conter dados pessoais que a pessoa informar. O arquivo e seus backups devem continuar privados e fora do Git. Não publique o banco como evidência.

Para interpretar um turno, o LLM recebe a mensagem atual e um resumo estruturado: ação pendente, nomes de campos conhecidos, nomes dos campos aguardados, datas/horários válidos, períodos, fusos, contagem de candidatos e indicadores de seleção. Títulos, descrições, locais, texto de busca e IDs dos turnos anteriores permanecem locais. Os detalhes dos candidatos também não voltam ao modelo. A mensagem atual, entretanto, é enviada ao provedor e pode conter esses dados; não há remoção automática de informações pessoais desse texto.

O transporte usa uma única chamada Responses, `store: false`, `tool_choice: required` e `parallel_tool_calls: false`, sem estado remoto de conversa, redirecionamento ou retry. Aceita uma ferramenta completa, argumentos de até 24 KiB e uma resposta externa de até 128 KiB. Tipos, campos obrigatórios, valores anuláveis, enums e JSON são revalidados no adaptador e no serviço. O schema estrito segue a [documentação oficial de function calling](https://developers.openai.com/api/docs/guides/function-calling). `store: false` não representa promessa de retenção zero pelo provedor; aplicam-se as políticas da conta, conforme o [guia do LLM](llm-api.md).

## Persistência, validade e concorrência

O schema do contexto passa da versão 1 para a versão 2. A migração é atômica: acrescenta `pending_request: null` aos registros antigos sem renovar validade ou alterar a revisão. Bancos inválidos ou incompatíveis são recusados, sem sobrescrever os dados como se fossem um banco novo.

Permanecem a identidade derivada da chave autenticada, o limite de conversas e a configuração do [contexto de conversa](contexto-conversa.md). A validade padrão é 30 minutos sem um salvamento válido. Perguntas e correções válidas podem salvar o rascunho e renovar essa validade; falhas do modelo, do Google ou da persistência não devem substituir o estado anterior. Expiração e exclusão representam remoção lógica do registro ativo, sem promessa de apagar cópias forenses ou backups.

Cada turno lê uma revisão, fecha a transação antes de acessar LLM/Google e salva com comparação de revisão (CAS). Duas respostas concorrentes não sobrescrevem silenciosamente o mesmo pedido: uma revisão desatualizada recebe 409. Não há conexão ou lock do SQLite mantido durante as chamadas externas, nem repetição automática do turno em conflito.

O endpoint `/agent/query` continua responsável pelas consultas de leitura; ele preserva o rascunho pendente. O endpoint `/agent/dialogue` prepara e esclarece pedidos; não substitui a listagem final. O WhatsApp continua com seu fluxo de teste até a etapa de ligação do diálogo ao remetente e à resposta dinâmica.

## Validação e limites deste marco

**Validado em 24/10/2026: 976 testes Python aprovados**, sem falhas, erros ou testes ignorados, incluindo 111 novos além dos 865 anteriores. O ensaio complementar aprovou **22 casos HTTP TCP** em loopback com dois processos Python e o mesmo SQLite temporário.

A validação local usa planos de modelo e respostas Google controlados, com SQLite real temporário. A suíte cobre contratos de transporte, persistência/migração, perguntas/correções, seleção atualizada de candidatos, isolamento, expiração, concorrência e falhas. O ensaio HTTP confirmou um pedido completado após reinício sem repetir título/data, conversa isolada, dados ambíguos, fuso pendente, troca de data, escolha explícita e descarte de opções de uma busca anterior. Registros 099/100. Essas fixtures verificam a lógica da aplicação; não comprovam interpretação linguística pelo LLM real ou entrega pelo WhatsApp.

Uma chave real do usuário foi configurada privadamente. As três requisições reais deste marco receberam HTTP 429; o diagnóstico identificou `credit_balance_exhausted`. Não houve inferência real bem-sucedida. O adaptador retorna o erro público seguro `llm_rate_limited`, sem expor detalhes brutos, e não tenta novamente automaticamente. A interpretação real permanece pendente de créditos disponíveis na conta. A distinção entre limite de requisições e falta de quota/créditos deve seguir a [documentação oficial de erros da API](https://developers.openai.com/api/docs/guides/error-codes).

Para reproduzir os testes locais, na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Os resultados efetivamente observados devem ser registrados no [índice de evidências](evidencias.md), separando suíte local, HTTP com fixtures e respostas do provedor. Esta funcionalidade de backend é mais bem demonstrada por relatórios JSON do que por uma captura artificial de tela. Nenhum resultado desta parte autoriza uma escrita real na agenda.

O diagnóstico da chave está no registro 101. As três tentativas de conexão foram manuais e limitadas; não houve retry automático nem inferência concluída. Após adicionar créditos, será necessário um novo teste explícito. Reinicie a API normal para carregar os novos endpoints e a configuração salva; os ensaios deste marco utilizaram somente processos temporários isolados.

A [parte 6](confirmacoes-agente.md) intercepta confirmação ou recusa inequívoca de proposta ativa antes do LLM. Um novo turno de preparação bem-sucedido elimina a aprovação anterior, inclusive se os campos finais forem iguais. Recusar a proposta não altera eventos. O schema atual é 3, migrando também a versão 2 deste marco; os relatórios 099/100 conservam o estado histórico da parte 5.

## Uso do rascunho na criação — parte 7

O comando autenticado `POST /api/v1/agent/create` recebe somente `conversation_id` e usa o rascunho completo persistido por este diálogo. Não chama o modelo novamente, não aceita parâmetros ou `operation_id` do cliente e não é disparado automaticamente por `ready`. Pedidos completos podem seguir um comando explícito sem confirmação genérica adicional; se houver proposta ativa, ela precisa estar aprovada para o conteúdo e a agenda exatos e ter seu consumo reservado antes da escrita.

O contexto permanece no schema **3**. A identidade e o resultado da execução ficam no ledger separado `data/agent-creations.sqlite3`, schema **1**, sem TTL; estados em andamento ou incertos bloqueiam nova escrita e são reconciliados por leitura. O fluxo WhatsApp/n8n ainda usa resposta fixa e não está ligado dinamicamente a esse comando. [Contrato, recuperação e limites](criacao-agente.md).

Os resultados e as três tentativas reais descritos acima pertencem ao marco da parte 5. A validação da parte 7 em **25/10/2026** aprovou **1.160 testes Python** e **27 casos HTTP TCP com dois processos**, com modelo/gateway como fixtures e SQLite real. Não houve chamadas reais Google/LLM nem envios WhatsApp nesse marco. A chave já está configurada, mas os créditos esgotados continuam bloqueando a inferência real, que não foi testada novamente. Registros 104/105 no [índice de evidências](evidencias.md).

## Uso do rascunho nas alterações — parte 8

O diálogo continua sem executar escrita. Os comandos autenticados `/agent/edit` e `/agent/reschedule` recebem somente `conversation_id`; usam a ação, o ID e os campos completos persistidos, sem aceitar parâmetros ou operação escolhidos pelo cliente/modelo. `ready` não dispara esses comandos automaticamente. Uma proposta ativa precisa estar aprovada para a versão e a alteração exatas; pedidos explícitos completos sem proposta podem seguir sem confirmação genérica extra.

O executor preserva os campos omitidos, valida o intervalo e os conflitos, aplica agenda/ETag vinculadas e confere o resultado. O ledger de alterações é separado, schema 1 e sem TTL; o contexto permanece no schema 3. Repetições incertas somente consultam, sem outro PATCH. O WhatsApp/n8n ainda não está ligado dinamicamente a esse comando. [Contrato, recuperação e limites](alteracoes-agente.md).

Validação desta parte: 1.257 testes Python e 33 casos HTTP TCP locais aprovados, com dois processos e SQLite reais, modelo/gateway como fixtures, zero chamadas reais Google/LLM ou envios WhatsApp. Resultados anteriores continuam históricos de seus marcos. Registros 106/107.

## Uso do rascunho na exclusão — parte 9

O diálogo continua preparatório e pode registrar aprovação explícita da proposta ativa, sem executar a ação. `POST /api/v1/agent/delete` usa o rascunho completo e a proposta de exclusão aprovada. Um pedido `ready`, uma escolha de candidato ou uma intenção explícita de excluir não substituem essa confirmação obrigatória. O cliente envia só a conversa; ID, agenda, versão, token técnico e operação ficam sob controle do servidor.

O comando reserva o uso único da proposta antes de escrever, aplica `If-Match` e confere a ausência. O ledger de exclusões é separado, schema 1 e sem TTL; contexto schema 3, criação schema 1 e alteração schema 1 permanecem. Repetições em andamento/incertas/confirmadas só consultam; não repetem DELETE nem transferem a aprovação para outro evento. O fluxo WhatsApp/n8n ainda não chama esses executores dinamicamente. [Contrato e limites](exclusao-agente.md).

Validação desta parte: 1.367 testes Python e 62 casos HTTP TCP locais aprovados, com dois processos e SQLite reais; modelo, gateway e respostas aprovativas como fixtures. Zero chamadas reais Google/LLM/WhatsApp ou aprovação humana para agenda real. Registros 108/109; contagens anteriores conservadas como histórico.
