# Coleta de contexto da agenda — etapa 13, parte 2

**Implementada em 26/10/2026.** O serviço reúne eventos de semana/mês, conta o período inteiro e produz contexto factual mínimo para a parte 3. Há comando Python e rota autenticada avulsa. Não chama LLM, não cria resumo narrativo, não muda conversas/confirmações, eventos, configuração de lembretes ou workflows. A ligação das novas intenções ao WhatsApp continua na parte 8.

## Uso e entrada

```powershell
.\.venv\Scripts\python.exe -B -m app.collect_summary_context --period week --date 2030-06-19
.\.venv\Scripts\python.exe -B -m app.collect_summary_context --period month --counts-only
```

`--period` aceita somente `week` ou `month`. `--date` é opcional e exige `AAAA-MM-DD` completo/existente; omiti-lo usa o relógio UTC do servidor convertido para o fuso da agenda no início da coleta. Não há interpretação de mensagem livre, recorte “resto da semana” ou escolha pelo LLM nesta parte. Entrada inválida é recusada antes de abrir o conector ou ler o relógio. `--counts-only` remove a lista de eventos e seus títulos da saída, preservando somente metadados, contadores e dias.

```text
GET /api/v1/agenda/context?period=week&date=2030-06-19
GET /api/v1/agenda/context?period=month
```

Usar a chave Bearer privada já configurada e, opcionalmente, `X-Request-ID`. Não enviar chave na URL. O router v1 autentica antes de validar/abrir o gateway; a resposta tem `Cache-Control: no-store` e correlação. `period` é obrigatório; `date` opcional. Parâmetros extras, repetidos, data inválida e query acima de 1.024 bytes são recusados, inclusive `time_zone`/`calendar_id`: o fuso e a agenda vêm exclusivamente da seleção autorizada do servidor.

O envelope v1 usa `action: collect_agenda_context`, `operation_id: null` e `mutation_state: not_applicable`. `data.execution_allowed` é sempre `false`. A resposta não contém IDs reais, links ou nome da agenda. A API não implementa `counts-only`; a opção é da CLI para registros operacionais mínimos.

| Resultado | HTTP / código | Dados |
|---|---|---|
| Fonte completa, inclusive vazia | 200 / `succeeded` | Contexto factual completo |
| Entrada inválida | 422 / `invalid_request` | `null`; somente nomes de campos permitidos |
| Falha de seleção/configuração | 503 / `calendar_setup_unavailable` | `null`; erro de domínio sanitizado |
| Limite de páginas/itens/contexto | 503 / `agenda_context_limit_exceeded` | `null`; sugerir período menor, sem retry automático do chamador |
| Falha de leitura, resposta inválida ou limpeza do cliente | 502 / `agenda_collection_failed` | `null`; nunca prefixo parcial ou agenda vazia inventada |

Os casos de 401/503 da autenticação conservam o contrato de [segurança](seguranca-api.md). A nova rota foi verificada em ASGI com o código atual e provedores fictícios; o processo API persistente não foi reiniciado nesta parte. Para carregá-la num processo já aberto, reinicie a API pelo comando normal `python -m app`, preservando seus bancos e configuração. O teste real deste marco foi pela CLI e não prova disponibilidade dessa rota no processo antigo.

## Componentes e limites

`SummaryContextService` é independente de SDK/IA/persistência. Usa uma factory de leitura, relógio injetável e DTOs privados de vínculo/snapshot. `GoogleSummaryCalendarGateway` implementa a fronteira Google em módulo novo; os métodos e consultas legados de `GoogleCalendarGateway` não foram alterados. `SummarySetupError`, `SummaryLimitError` e `SummaryCollectionError` mantêm classificação sem expor título/agenda/erro bruto.

O binding efetivo é lido e validado, e relido antes de listar: ID e fuso precisam coincidir. Agenda principal, fuso ausente/inválido e drift são recusados. Não há fallback para UTC, fuso do host ou outro calendário. O helper existente de descoberta/OAuth pode renovar token/metadados locais; nenhuma escrita de eventos Google é executada.

| Limite | Política implementada |
|---|---|
| Período | Semana civil segunda→segunda exclusiva; mês civil primeiro→primeiro exclusivo |
| Paginação | Até 20 páginas lógicas; página final precisa terminar sem próximo token |
| Fonte bruta | Até 2.000 itens, incluindo cancelados, duplicados e fora da faixa; exatamente 2.000 só é sucesso numa página terminal |
| Detalhes | Até dez eventos, com `shown/total/omitted/limit/truncated` explícitos; totais usam a fonte completa |
| Título | UTF-8 válido, NFC, espaços/controles normalizados, até 160 caracteres e `title_truncated`; ausência vira rótulo `(sem título)` |
| Contexto | Até 16.000 bytes UTF-8 em JSON compacto; excesso falha, sem truncar silenciosamente |

O SDK mantém até duas retentativas controladas **de leitura** por chamada, conforme o conector existente. Os limites de páginas são lógicos, não contagem de tentativas HTTP. Não há retry de escrita ou envio porque esses recursos não existem neste percurso.

Páginas vazias com token válido são seguidas. Tokens repetidos/malformados, páginas/itens inválidos, séries sem expansão, intervalos/Unicode inválidos e cópias divergentes do mesmo ID abortam a leitura inteira. Recorrências vêm expandidas com ID próprio e metadados coerentes; não interpretamos RRULE. Ocorrências idênticas repetidas são contadas uma vez. Cancelados não são compromissos ativos, mas consomem o limite bruto. `confirmed` e `tentative` são distintos; transparência não é lida, pois este contexto **não calcula disponibilidade**.

`complete: true` significa paginação terminal e validação do resultado dentro desses limites; não é uma transação distribuída nem garantia de snapshot atômico do Google. Mudança externa entre páginas continua possível. Rejeitamos divergências observáveis e registramos o instante após a leitura; esse contexto não é autorização ou reserva para uma futura escrita.

## Datas, agrupamento e projeção mínima

Instantes são comparados em UTC; apresentação usa o IANA efetivo da agenda. A consulta usa sobreposição e fim exclusivo: inclui uma ocorrência começada antes do período que ainda o atravessa; exclui a que termina exatamente no início ou começa no fim. O offset explícito retornado pelo Google é preservado como instante, ainda que o IANA de origem de uma ocorrência seja outro. Offset com minuto/hora inválidos é recusado antes de `fromisoformat`, sem aceitar sua normalização.

Dia inteiro conserva datas e fim exclusivo, sem virar horário ou 24h de trabalho. Todos os dias do período entram em `days`, mesmo vazios; uma ocorrência multidia pode contar em mais de um dia, mas só uma vez em `totals.events`. Não somar contadores diários para obter o total do período. Provisórios são sinalizados, sem ranking de importância ou soma de horas como ocupação.

O relógio antes e depois da coleta precisa ser timezone-aware e não regressivo. `elapsed` inclui fim igual ao instante de coleta; início igual ao instante já é `ongoing`; início posterior é `upcoming`. Mudanças de horário de verão preservam duração real da semana, inclusive 167/169h. Dia civil inexistente, como 30/12/2011 em `Pacific/Apia`, interrompe a composição em vez de produzir um bucket vazio contado como ocupado.

O contexto possui `schema_version`, `period`, `snapshot`, `totals`, `days`, `events`, `details` e `execution_allowed`. Os detalhes são ordenados por início UTC e desempate pelo ID privado, e recebem referências locais `E1…E10`; elas não são IDs executáveis nem seleção persistente. Datas/horas/status vêm do backend e não do modelo. Veja [exemplos de entrada](../examples/preparation/context-requests.json).

Não entram na projeção IDs da agenda/eventos, ETags, links, descrição, local, participantes, tokens ou payload bruto, inclusive se um gateway retornar subclasse com campos adicionais. Títulos e texto livre ainda podem conter dados pessoais: normalização/projeção não é anonimização automática. Esta parte **não envia o contexto ao LLM**, não grava histórico novo e não altera schema/contexto de conversa. A parte 3 deverá respeitar seu orçamento e separar fatos de narrativa; a parte 8 cuidará do isolamento de novas intenções e confirmação pendente.

## Verificação e resultado real

Passaram **2.044 testes Python**, sendo **88 novos**: 27 de serviço, 32 de gateway/SDK, 18 da API ASGI, sete da CLI e quatro percursos compostos API/CLI→serviço→gateway→SDK/transporte. A suíte completa verificou também o CRUD, contexto, confirmações, worker e lembretes anteriores. As **981 verificações JavaScript são históricas**, sem nova execução: suas fontes e os cinco exports não mudaram. O registro 141 mantém hashes e log da suíte atual; nenhum caso foi ignorado.

As fixtures substituem OAuth/fronteira HTTP Google. O adaptador, SDK e transporte são reais; isso não é resposta de conta real ou teste de rede externo. Casos cobrem paginação vazia, erro tardio, limites exatos, cópia conflitante, recorrência, UTC/DST, dia inteiro, contagem após o décimo detalhe, fuso/data relativa, orçamento, campos privados e erros sanitizados. A revisão independente reproduziu o dia inexistente e os offsets inválidos; ambos foram corrigidos antes da execução consolidada final.

Separadamente, duas execuções da **CLI instalada**, em modo `--counts-only`, consultaram o Google real: **0 evento(s) na semana** e **3 no mês** da referência registrada. Ambas retornaram contexto completo no fuso efetivo da agenda, com sete dias e os dias civis do mês. Esses números são do momento da leitura, não resultado de IA nem evidência de mensagem WhatsApp. O registro 142 conserva somente agregados e períodos, sem títulos ou IDs. Não houve criação/edição/exclusão de eventos, chamada de IA, envio, restart da API ou mudança de workflow para esse teste.

Reprodução dos testes isolados, a partir da raiz do projeto, com dependências instaladas:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*summary*' -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

A suíte completa exige loopback/subprocessos para os testes anteriores de reinício. Não usar uma pasta com credenciais como cópia de fixtures. [Matriz anterior e relatório TCP](testes-conjunto-lembretes.md) detalham os testes de lembretes que seguem preservados. As 221 fontes testadas instaladas e os cinco exports foram conferidos por hash; o módulo legado de consultas permanece byte a byte igual. Na leitura real, os hashes privados conferidos foram somente `.env`, os JSONs `whatsapp-*.json` e `reminders-n8n-local.json`. Não foi feita comparação de hashes dos bancos ou do token OAuth, que podem ser atualizados pelos serviços existentes. O PDF fica para o fechamento da etapa 13; não foi produzido print porque JSON/testes comprovam melhor este marco sem nova interface.

**Parte 2/10 concluída. Próxima: gerar os resumos com IA e alternativa factual.**


## Evolução — etapa 13, parte 3

A [organização de resumos com IA](resumos-com-ia.md) já reutiliza a coleta por API/CLI separados, com plano estrutural validado e alternativa factual. O modelo recebe somente agregados/período/catálogo; não recebe títulos/IDs nem redige fatos livres. As consultas e intenções antigas, os estados de confirmação, os adaptadores e os fluxos seguem preservados. Novas intenções WhatsApp continuam na parte 8. Testes atuais: 2.126 Python (+82); registros 143/144.
