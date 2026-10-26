# Ambiguidades e esclarecimentos — etapa 08, parte 3

**Concluída e validada em 22/10/2026.**

`RequestPreparationService` prepara pedidos estruturados de criar, consultar, reagendar, editar e excluir. Devolve uma pergunta quando faltam dados ou quando eles precisam de correção. Operações sobre um evento usam a seleção existente para pedir esclarecimento diante de vários candidatos, resultado ausente ou evento alterado durante a busca.

O serviço não executa escrita. Um pedido `ready` tem dados preparados; o chamador ainda precisa solicitar a operação correspondente. Na exclusão, continuam obrigatórias a prévia e a confirmação vinculada à versão do evento.

## Executar os exemplos locais

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe -m app.prepare_request --request-file examples/requests/create_missing_time.json
.\.venv\Scripts\python.exe -m app.prepare_request --request-file examples/requests/reschedule_missing_event.json
.\.venv\Scripts\python.exe -m app.prepare_request --request-file examples/requests/create_ready.json
```

Os três arquivos contêm dados fictícios. Os dois primeiros devolvem perguntas; o último retorna `ready`, sem criar a entrevista. Eles funcionam sem abrir o cliente Google. Também não precisam iniciar API, n8n ou Docker.

Uma seleção completa por ID ou título/data pode consultar a agenda configurada, usando o acesso Google existente. O cliente só é aberto quando a seleção exige essa leitura; faltas ou erros detectados localmente impedem o acesso. Nenhum caminho do comando cria, altera ou exclui eventos.

## Contrato do pedido

O arquivo deve conter um objeto JSON. Campos duplicados são rejeitados. A ação é obrigatória; o serviço pergunta qual ação foi desejada se ela não foi informada.

| `action` | Dados exigidos |
|---|---|
| `create` | `title`, `start` e `end`; `description` opcional |
| `list` | `date`; `period` opcional: `day`, `week` ou `month` |
| `reschedule` | Seletor do evento, novo `start` e novo `end` |
| `edit` | Seletor e ao menos um campo entre `title`, `description` e `location` |
| `delete` | Seletor do evento; esta preparação não confirma a exclusão |

O seletor usa **`event_id` sozinho** ou **`search_title` com `date`**, admitindo `period` na busca. Misturar identificador e busca pede correção. `search_title` procura o título atual; `title` é o novo título na edição.

`start` e `end` recebem texto ISO com data, hora e offset UTC. `date` usa `AAAA-MM-DD`. Não são adivinhados data, horário de término ou duração. Horários incoerentes usam as [regras de datas e fusos](datas-fusos.md).

O fuso padrão da demonstração é `America/Recife`, configurado pelo construtor ou por `--timezone`. Um `time_zone` no pedido pode substituí-lo, desde que válido. Esse padrão explícito do projeto não é uma inferência sobre a localização de quem mandou a mensagem; a integração futura poderá fornecer o fuso do perfil.

Na edição, `null` significa omissão; `""` limpa descrição ou local. Título vazio pede correção. Campos desconhecidos ou incompatíveis com a ação impedem a preparação. `operation_id` e opções de execução não fazem parte deste pedido: são responsabilidades do chamador nas etapas próprias.

## Respostas

| `status` | Significado |
|---|---|
| `needs_clarification` | Faltam dados ou a seleção pede esclarecimento; há pergunta e, quando necessário, candidatos |
| `needs_correction` | Há um valor inválido ou informações contraditórias; há pergunta de correção |
| `ready` | Dados validados e, nas operações sobre evento, identidade relida; a ação ainda não foi executada |

A resposta inclui `action`, `reason`, `question`, `missing_fields`, `invalid_fields`, `event`, `candidates` e `parameters`. Somente `ready` contém `parameters`; nenhum resultado gera código de confirmação da exclusão. Uma dúvida deixa `event` nulo e nunca escolhe o primeiro candidato automaticamente.

O motivo diferencia `missing_information`, `invalid_information`, `event_not_found` e `event_selection` (vários candidatos ou mudança durante a seleção). As opções preservam o ID opaco e os dados mínimos do evento. O comando retorna código zero para uma preparação concluída, inclusive quando devolve pergunta. JSON malformado e falhas operacionais retornam erro, sem JSON de sucesso.

Falhas de comunicação ou autorização continuam erros operacionais. Elas não são transformadas em pedido para o usuário mudar a data ou o título. A seleção e as perguntas usam serviços independentes da IA; os comandos estritos já existentes mantêm seus contratos.

O executor continua conferindo permissões, versão e capacidades da operação. Por exemplo, a preparação pode identificar um evento de dia inteiro, mas o comando estrito de reagendamento ainda rejeita esse tipo; séries completas e eventos especiais também mantêm suas restrições. `ready` não comprova disponibilidade de horário, ausência de duplicação ou possibilidade final de executar todas as mudanças.

## Verificação e limites

Criamos **26 testes novos** em `tests/test_request_preparation.py`. A suíte completa passou com **181 testes**, na preparação isolada e no ambiente do repositório. A revisão independente não encontrou bloqueadores após corrigir o caso de título de busca que ficava vazio ao remover acentos.

Os testes cobrem campos ausentes/inválidos antes de I/O, horários e fusos, seletores contraditórios, campos desconhecidos, candidatos múltiplos, ausência, mudança durante a seleção, identificadores de ocorrências, omissão e limpeza de informações, preparação sem escrita e contratos da CLI. O cliente Google abre apenas quando é necessária uma seleção completa. Falhas operacionais continuam erros, sem resultado `ready`.

O registro **030** do acervo contém **nove exemplos locais**. Dois preservam a saída real da CLI com os arquivos fictícios distribuídos no repositório; os demais usam um conector somente de leitura com eventos fictícios. Demonstram perguntas, correção, vários candidatos, evento ausente/alterado e preparação por ID ou limpeza de campo. Nenhum exemplo consulta ou altera a conta Google. Os exemplos são separados da contagem da suíte.

A CLI recebe JSON estruturado. A interpretação de texto livre, o envio efetivo da pergunta pelo WhatsApp, a continuação da conversa, a associação ao usuário e a escolha persistida por número pertencem às etapas de integração e IA. O programa ainda não aplica automaticamente uma resposta recebida em mensagem posterior.

**Atualização após a parte 4:** os executores de criação e reagendamento agora conferem os conflitos antes de alocar um novo horário. A preparação `ready` continua sem reservar ou executar. Consulte [conflitos de horário](conflitos.md). As partes 5, [prevenção de duplicações](duplicacoes.md), 6, [falhas e recuperação](falhas-recuperacao.md), e 7, [testes do conjunto](testes-conjunto.md), também foram concluídas. **Etapa 08 concluída em 22/10/2026**, com 359 testes aprovados e código conferido por hashes. Consulte o [fechamento](fechamento-etapa-08.md). Próxima: **etapa 09 — operações na API e fluxos n8n**.
