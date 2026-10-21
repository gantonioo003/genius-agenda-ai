# Identificação do evento — etapa 07, parte 3

**Concluída e validada em 21/10/2026.**

## O que foi implementado

Em 21/10/2026, criamos `app/services/event_selection.py`, independente da IA, e o comando `app/select_event.py`. O conector Google ganhou leitura exata pelo ID e passou a compartilhar a interpretação dos eventos entre listagem e leitura individual, preservando datas de dia inteiro, fuso e ocorrências recorrentes.

A seleção consulta somente a agenda de teste já configurada. Mantém as permissões existentes e solicita apenas ID, título, status, início/fim e link; não busca descrições ou contatos. Não modifica nem exclui eventos.

## Executar

Na raiz do repositório, com a conta já autorizada, para identificar pelo título em um dia:

```powershell
.\.venv\Scripts\python.exe -m app.select_event --title "ENTREVISTA DE ESTAGIO" --date 2026-10-22
```

Para ampliar a busca à semana ou ao mês da referência:

```powershell
.\.venv\Scripts\python.exe -m app.select_event --title "entrevista" --date 2026-10-22 --period week
.\.venv\Scripts\python.exe -m app.select_event --title "entrevista" --date 2026-10-22 --period month
```

Para selecionar uma opção já encontrada, substitua `ID_RETORNADO` pelo `event_id` do evento ou candidato:

```powershell
.\.venv\Scripts\python.exe -m app.select_event --event-id "ID_RETORNADO"
```

O ID é usado exatamente como retornado, preservando caixa e sufixo da ocorrência. Não usar título, número da posição ou `iCalUID` no lugar do ID. A seleção por ID não recebe `--date` ou `--period`. O fuso padrão é `America/Recife`; ambos os modos aceitam `--timezone`.

API, Docker e n8n não precisam estar iniciados. A descoberta da agenda pode atualizar seus metadados locais.

## Como a decisão funciona

| Situação | Resultado | Próximo passo |
|---|---|---|
| Um título corresponde a um evento no período | Relê o mesmo ID no Google; retorna `selected` se os dados seguem iguais | O evento identificado fica em `event` |
| Mais de um candidato corresponde ao título | `needs_clarification`, `event: null`, candidatos e pergunta | Escolher pelo ID de um candidato |
| Nenhum candidato corresponde | `not_found`, `event: null` e pergunta | Conferir título e data |
| ID ausente, removido ou cancelado | `not_found`, sem procurar outro evento como substituto | Consultar novamente e conferir o ID |
| Evento mudou entre a listagem e a confirmação | `needs_clarification`, sem seleção automática | Conferir os dados atuais e selecionar novamente pelo ID |
| Falha de acesso, comunicação ou resposta inválida | Erro operacional | Corrigir a falha e repetir a consulta |

A busca por título usa um trecho contínuo, ignorando caixa, acentos e espaços repetidos. Por exemplo, `ENTREVISTA DE ESTAGIO` corresponde a `Entrevista de estágio`. Se o trecho combina com vários títulos, nem a correspondência exata ganha preferência automática. A data é obrigatória nesse modo; os períodos seguem as [regras de dia/semana/mês](consulta-semana-mes.md).

A revalidação confere ID, título, início/fim, dia inteiro, fuso e status. Eventos provisórios (`tentative`) preservam esse estado e podem ser identificados; estados desconhecidos não são confirmados. Leituras podem usar retentativas; não há retentativas de escrita nesta funcionalidade.

O JSON contém `status`, `calendar_name`, `event`, `candidates` e `question`. Código de saída zero significa que a identificação terminou, inclusive quando é necessário esclarecer ou refazer a busca. Apenas `status: selected` contém um evento identificado. Entradas inválidas ou falhas operacionais retornam código 1 e mensagem em stderr; erros de sintaxe dos argumentos retornam código 2.

## Validação real e evidências

Em **21/10/2026**, realizamos três verificações somente de leitura:

1. Busca por `ENTREVISTA DE ESTAGIO`, em **22/10/2026**: retornou `selected` para a entrevista existente, **15h–15h30**, na agenda `Genius Testes`, com status `confirmed`.
2. Consulta pelo ID completo retornado na criação: identificou o mesmo compromisso e horários.
3. Busca com um título sem correspondência: retornou `not_found`, sem evento selecionado, e uma pergunta para conferir título e data.

As saídas reais dos comandos estão nos registros locais **012, 013 e 014**. Para demonstrar a ambiguidade, executamos uma **simulação local** com duas entrevistas fictícias: o serviço retornou `needs_clarification`, dois candidatos e nenhuma seleção. Esse registro é o **015**, explicitamente identificado como simulação; não representa eventos criados ou consultados no Google.

Esta parte tem saída de terminal. Seus registros são JSONs, sem apresentar imagens de etapas anteriores como prova da nova seleção.

## Testes e revisão

Criamos `tests/test_event_selection.py` com **18 testes novos**. A suíte completa passou com **72 testes**, na preparação isolada e no repositório, sem rede ou credenciais reais nos testes automatizados. A revisão independente do código não apontou outros impedimentos no escopo.

Os testes verificam normalização do título, múltiplos candidatos sem preferência automática, ausência de resultados, IDs opacos e de ocorrências, revalidação de dados alterados, seleção segura da agenda, campos mínimos, 404/410/cancelados, falhas 403 e de comunicação, respostas malformadas, dia inteiro, fuso e os contratos de sucesso/esclarecimento/erro da CLI. Chamadas de criação, atualização ou exclusão causam falha imediata nos testes de integração com o Google simulado.

## Limites e continuação

`selected` confirma a identidade naquele instante. Não autoriza uma alteração ou exclusão futura, nem impede mudanças posteriores no Google. As operações de alteração e exclusão implementadas nas partes seguintes releem o evento antes de agir; a exclusão exige confirmação explícita. A entrevista destes registros históricos foi removida ao final. Consulte o [fechamento da etapa 07](fechamento-etapa-07.md).

Ainda não há seleção persistida na conversa, escolha por número, rota HTTP, integração n8n/WhatsApp ou uso de IA. O resultado estruturado já separa evento selecionado, candidatos e pergunta para essa integração futura.

Referências: [Google Calendar — events.get](https://developers.google.com/workspace/calendar/api/v3/reference/events/get) e [registros de evolução](evidencias.md).
