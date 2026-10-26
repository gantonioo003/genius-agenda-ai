# Conflitos de horário — etapa 08, parte 4

Em **22/10/2026**, implementamos a consulta de ocupação e o bloqueio de novos horários conflitantes na criação e no reagendamento. As regras ficam em `app/services/conflicts.py`, independentes da IA; o conector Google consulta os eventos e os comandos existentes aplicam a proteção.

## Consultar um intervalo

Na raiz do repositório, com a conta Google autorizada:

```powershell
.\.venv\Scripts\python.exe -m app.check_conflicts --start "2026-10-23T16:00:00-03:00" --end "2026-10-23T16:30:00-03:00" --timezone America/Recife
```

Para conferir um possível reagendamento, exclua somente o ID do próprio evento:

```powershell
.\.venv\Scripts\python.exe -m app.check_conflicts --start "2026-10-23T16:00:00-03:00" --end "2026-10-23T16:30:00-03:00" --exclude-event-id ID_DO_PROPRIO_EVENTO
```

O comando lê o Google Calendar; não cria nem altera eventos. A seleção da agenda pode atualizar metadados locais. API HTTP, Docker e n8n não precisam estar iniciados.

| Resultado | Significado |
|---|---|
| `available` | A consulta terminou sem encontrar eventos que ocupem o intervalo |
| `conflict` | A consulta encontrou sobreposição; `conflicts` identifica os eventos |

O relatório contém `status`, `calendar_name`, `start`, `end`, `time_zone` e `conflicts`. Ambos os estados retornam código zero: significam consulta concluída. Falhas retornam código 1 e mensagem em stderr, sem relatório de disponibilidade; erros de sintaxe dos argumentos retornam código 2. Falhas de validação do intervalo ou fuso retornam código 1.

**O relatório não reserva o horário.** A criação e o reagendamento consultam novamente a ocupação antes de gravar. Um pedido preparado como `ready` também não representa disponibilidade nem autorização para executar.

## Regras de sobreposição

- A comparação usa instantes em UTC: há conflito quando o outro evento começa antes do fim solicitado e termina depois do início solicitado. O fim é exclusivo; um evento que termina às 16h permite outro começar às 16h.
- Intervalos que atravessam a meia-noite são tratados normalmente. Eventos de dia inteiro usam o fuso da agenda e a data final exclusiva retornada pelo Google.
- Recorrências são expandidas em ocorrências. Excluir o próprio ID não exclui os demais eventos ou toda a série.
- Eventos `confirmed` e `tentative` com ocupação `opaque` bloqueiam o horário. Cancelados e `transparent` não bloqueiam; transparência omitida equivale a `opaque`.
- Todas as páginas são consultadas, inclusive quando uma página vazia aponta para a próxima. Paginação inválida, dados malformados, estados desconhecidos ou falhas de acesso/rede impedem concluir que o intervalo está livre.
- O Google ignora frações de segundo nos filtros. A janela enviada é ampliada em UTC para segundos inteiros: início arredondado para baixo e fim para cima quando necessário. Depois, a comparação local aplica os limites exatos solicitados.

## Proteção na gravação

A criação reutiliza primeiro uma operação já confirmada com o mesmo ID estável e os mesmos dados. Uma nova alocação verifica conflitos antes do INSERT; o ID derivado da própria operação é excluído dessa comparação.

O reagendamento ignora somente o próprio ID. Mesmos instantes retornam `unchanged`, sem nova alocação. Um evento que já é `transparent` mantém esse estado e pode ser movido sem disputar ocupação. A prévia de um novo horário `opaque` também verifica conflitos, mas não grava nem mantém reserva.

Para escritas, a resolução da agenda e os metadados ocorrem antes da trava SQLite. Dentro de `Database.calendar_write_lock()`, `BEGIN IMMEDIATE` serializa a leitura, a checagem e a gravação entre processos que compartilham esse banco. O reagendamento mantém o controle por ETag/`If-Match` e confirma horários e transparência após o PATCH. Escritas não têm retentativa automática.

Um conflito gera `CalendarConflictError`, com o relatório disponível em `report`, antes da gravação. Se houver falha depois de uma tentativa de escrita, inclusive ao liberar a trava, o resultado pode ter sido aplicado: consultar o evento pelo ID antes de repetir.

## Demonstração real em 22/10/2026

Consultamos `Genius Testes` antes de gravar: o dia 23/10 estava vazio, os intervalos estavam livres e os três novos IDs estavam ausentes. A entrevista original da etapa 07 continuou excluída. Criamos duas fixtures novas, sem convidados:

| Operação | Resultado confirmado pelo Google |
|---|---|
| Criar entrevista A, 23/10, 15h–15h30 | Criada e relida pelo ID |
| Tentar criar C, 15h15–15h45 | `CalendarConflictError` identificou A; consulta posterior confirmou C ausente |
| Criar entrevista B, 16h–16h30 | Criada e relida pelo ID |
| Tentar mover B para 15h15–15h45 | `CalendarConflictError` identificou A; nova leitura confirmou B ainda às 16h–16h30 |
| Consultar 15h30–16h | `available`: o intervalo entre A e B permanece livre |

Chamamos `InterviewService.create()` e `RescheduleService.reschedule(..., execute=True)` com o conector real, usando identificadores próprios para esta demonstração. Cada fase teve leitura de conferência; falhas genéricas de rede não foram aceitas como prova de conflito. A checagem da criação também consultou o ID da tentativa bloqueada, e o reagendamento comparou os dados anteriores e posteriores de B.

Os registros **031 a 034** preservam os resultados reais em JSON na pasta privada de evidências. A captura **035** mostra A e B nos horários preservados na interface do Google Agenda. A imagem comprova o estado visível; os JSONs comprovam os bloqueios das operações. Os dois eventos novos permanecem na agenda de teste. A fixture excluída na etapa 07 não foi recriada.

## Verificação e limites

A suíte local passou com **217 testes**, incluindo **29 novos testes de conflitos** e **7 da trava**. Os casos cobrem limites, fusos, frações de segundo, recorrências, ocupação, paginação, impedimento de escrita, reutilização, no-op e falhas. A trava foi exercitada com conexões SQLite independentes e bancos temporários. Os testes não acessam o Google. A suíte passou tanto na preparação isolada quanto no ambiente do repositório; a demonstração real foi executada separadamente.

A proteção considera apenas a agenda própria de teste selecionada (`Genius Testes`). A trava cobre processos que usam o mesmo arquivo SQLite; alterações feitas diretamente no Google ou por instâncias com outro banco podem ocorrer entre a consulta e a escrita. Não há transação atômica entre SQLite e Google Calendar.

HTTP, n8n, WhatsApp, IA, sugestões de outros horários e confirmação na conversa permanecem fora desta parte. A **parte 5**, [prevenção de duplicações](duplicacoes.md), acrescentou o registro durável de criação e o vínculo de identificadores diferentes ao mesmo conteúdo; novas alocações continuam passando pela checagem de conflitos.

Referências: [consulta de eventos e limites dos filtros](https://developers.google.com/workspace/calendar/api/v3/reference/events/list) e [campos de evento, transparência e recorrência](https://developers.google.com/workspace/calendar/api/v3/reference/events).


## Evolução — etapa 13, parte 4

A [disponibilidade avulsa](horarios-livres.md) calcula opções pela agenda autorizada, com a mesma política de opacidade/provisórios e limites semiabertos, mais fonte limitada completa, duração/faixa e margem explícitas. Não altera o verificador legado nem aceita exclusão arbitrária de ID. Ainda não é intenção nova no WhatsApp; sugestões vinculadas ao evento e sua revalidação seguem na parte 5. Provas da parte 4: 2.225 Python (+99), duas leituras reais e seis revalidações de conflito; registros 145/146.
