# Preparo compacto da entrevista

> **Atualização do bloco B:** preparo/checklist, resumo e disponibilidade já estão [conectados ao WhatsApp](preparo-whatsapp.md). O chat mostra título, data/horário legíveis, local, contexto curto e checklist a partir dos fatos estruturados validados; o contrato detalhado de API/CLI abaixo foi preservado. A seção de provas do bloco A é histórica.

**Bloco A da etapa 13 implementado em 26/10/2026:** briefing e checklist na mesma resposta, por serviço, API e CLI. Versão factual por template Python, sem chamada de IA, pesquisa, armazenamento novo, reserva ou alteração de evento. O checklist é uma sugestão genérica de preparo, não uma avaliação personalizada da vaga. O resumo semanal/mensal com IA continua sendo uma capacidade separada.

## Uso

Primeiro, consulte/identifique uma ocorrência usando as consultas existentes. O preparo recebe o ID exato; não escolhe por título parecido, primeira opção ou evento “mais importante”. No WhatsApp, a seleção existente pode ser usada em “me prepara para essa entrevista”; também há ID literal e notas após `Contexto:` nos [formatos aceitos](preparo-whatsapp.md).

```powershell
.\.venv\Scripts\python.exe -B -m app.prepare_interview --event-id ID_DA_OCORRENCIA --text-only
.\.venv\Scripts\python.exe -B -m app.prepare_interview --event-id ID_DA_OCORRENCIA --context "Quero revisar exemplos de experiências e testes."
```

`--text-only` emite UTF-8 quando a saída suporta reconfiguração; o padrão é JSON com caracteres escapados. Sem ID, o comando mostra a pergunta de seleção e não abre a agenda. Código de saída 0 significa que houve resultado, inclusive esclarecimento; falhas retornam 1 e erro sanitizado.

```text
POST /api/v1/preparation/briefing
Authorization: Bearer <chave privada configurada>
Content-Type: application/json
```

```json
{"event_id":"ID_DA_OCORRENCIA","user_context":"Quero revisar minhas experiências com APIs."}
```

`event_id` é opcional para permitir uma pergunta quando ausente; ID inválido ou acima de 1.024 caracteres é recusado. `user_context` é texto opcional de até 1.200 caracteres. Nenhum outro campo, agenda/fuso do cliente ou autorização de escrita é aceito. A API exige JSON estrito, sem chaves repetidas, query ou campos extras, até 8.192 bytes e dois segundos para receber o corpo. Autenticação vem antes do corpo/fonte; o trabalho síncrono roda fora do loop ASGI. As respostas usam correlação e `Cache-Control: no-store`.

## Conteúdo e limites

A resposta reúne dados da agenda, contexto fornecido, pontos a conferir e até seis sugestões de preparo. Datas, horários, fuso, estado e local vêm da leitura validada. Evento provisório é apresentado como provisório; dia inteiro usa datas e fim exclusivo, sem inventar horário. A agenda autorizada fornece o fuso.

A descrição cadastrada e as notas aparecem como contexto **não verificado externamente**. Empresa, cargo, etapa, entrevistador ou requisitos não são deduzidos do título nem pesquisados. Falta de contexto/local é indicada e não impede o checklist genérico. Instruções dentro desses textos não executam ações, mudam o checklist ou alteram confirmações.

O texto final tem até 3.500 caracteres. Título/local são limitados a 160/240; descrição/notas exibidas, a 600 cada, com aviso de abreviação. HTML da descrição vira texto, sem scripts, estilos ou download de links. IDs da agenda, ETag e diagnóstico bruto não entram na saída. O ID da ocorrência informado pelo solicitante permanece em `facts.event_id`.

O serviço reutiliza `EventSelectionService` para o ID exato e `get_update_snapshot`/`validate_update_snapshot` para reler os detalhes. Confere vínculo da agenda e identidade, horário, estado e título entre as leituras. Mudança detectada pede nova seleção, sem devolver preparo parcial. Falha de leitura/encerramento não vira agenda vazia ou briefing pronto. Não se promete snapshot transacional entre requests Google.

| Situação | Resposta |
|---|---|
| Ocorrência relida e válida | HTTP 200, `ready`, fatos e checklist |
| ID ausente, evento removido/cancelado ou mudança detectada | HTTP 200, `needs_clarification`, pergunta sem fatos/checklist |
| Entrada inválida | HTTP 422, `invalid_request` |
| Setup indisponível | HTTP 503, `calendar_setup_unavailable` |
| Fonte inválida/inacessível ou falha operacional | HTTP 502, `briefing_collection_failed`, sem dados |

O envelope usa `action: prepare_briefing`, `operation_id: null` e `mutation_state: not_applicable`. O resultado tem `mode: factual_template`, `execution_allowed: false` e `reservation_created: false`. A resposta não aprova operações anteriores nem escreve na agenda.

## Verificação

**2.269 testes Python passaram, 44 novos**, sem casos ignorados: 21 de serviço/adaptador/CLI, 18 da API e três percursos com API, leituras legadas, SDK/transporte reais sobre HTTP Google fictício. A revisão reproduziu e confirmou correções para ID vazio acima do limite e emoji em stdout Windows cp1252; não restaram achados abertos. As 981 verificações JavaScript são históricas e os cinco exports foram preservados, sem novo rerun JS.

Separadamente, serviço e CLI instalados releram uma ocorrência existente na agenda real de teste, produziram seis itens e conferiram igualdade dos fatos, fuso e limite de texto. As notas do ensaio eram fictícias. A evidência 147 registra somente metadados e verificações, sem dados ou IDs do evento. Não houve criação de evento para o teste, IA, mensagem ou escrita de agenda. OAuth/metadados locais podem ser renovados pelos conectores existentes.

No marco 147, o processo API ainda não havia sido reiniciado e a prova HTTP era ASGI. O bloco B recarregou a API, confirmou as três rotas por HTTP real e ligou as capacidades ao WhatsApp com proteção das confirmações anteriores. A apresentação final passou por leitura real dos serviços, sem novo envio; as confirmações humanas de resumo/horários ocorreram antes dessa simplificação e não comprovam recebimento de briefing. [Provas e limites atuais](preparo-whatsapp.md). O PDF e o fechamento geral continuam no [plano de entrega](plano-entrega-hoje.md).
