# Consulta por dia — etapa 07, parte 1

**Concluída e validada em 21/10/2026.**

## Implementação

- `app/services/agenda.py`: serviço independente da IA, período de consulta e modelos de resultado. O dia é delimitado por duas meias-noites locais consecutivas, usando `zoneinfo`.
- `app/google_calendar.py`: conector de leitura com `events.list`, reutilizando a seleção segura da agenda de teste. Percorre todas as páginas e expande recorrências em ocorrências individuais.
- `app/list_events.py`: comando que recebe data e fuso, consulta o Google e exibe o resultado no terminal.
- `tzdata`: base oficial de fusos do Python, declarada nas dependências para funcionar também no Windows.

## Executar

Na raiz do repositório, com as dependências instaladas e a conta já autorizada:

```powershell
.\.venv\Scripts\python.exe -m app.list_events --date 2026-10-22 --timezone America/Recife
```

A consulta é somente de leitura no Google. Não precisa iniciar API, Docker ou n8n. A seleção pode atualizar os metadados locais da agenda, como ocorre na verificação de conexão.

## Comportamento

- A data deve usar o formato `AAAA-MM-DD`; o fuso padrão é `America/Recife` e pode ser informado explicitamente.
- A consulta inclui eventos que sobrepõem o dia: início anterior à meia-noite seguinte e fim posterior à meia-noite inicial. Eventos que atravessam a madrugada também aparecem.
- O período respeita o fuso informado, inclusive dias de 23 ou 25 horas. Data/fuso inválidos produzem erro antes de acessar o Google.
- Eventos temporizados são apresentados no fuso da consulta. Eventos de dia inteiro preservam as datas originais e o fim exclusivo; a sobreposição é calculada no fuso do calendário.
- Cancelados são excluídos; eventos provisórios (`tentative`) permanecem identificados pelo status.
- A resposta contém data, fuso, agenda, quantidade e eventos com ID, título, início/fim, status e link quando disponível. Descrições e contatos não são solicitados.
- Dia sem eventos retorna `status: ok`, `count: 0` e lista vazia. Falha de acesso, comunicação ou paginação retorna erro, sem apresentar uma lista parcial como completa.

## Validação

A execução real do comando retornou `status: ok`, data `2026-10-22`, agenda `Genius Testes` e `count: 1`. O evento retornado foi a entrevista criada na etapa 06, **22/10/2026, 15h–15h30**, com status `confirmed` e o mesmo identificador da criação.

A visão diária do Google Agenda foi aberta para comparar o resultado. O acervo local preserva a saída JSON do comando no registro 007 e a captura real do navegador no registro 008; são evidências distintas.

Foram aprovados **40 testes locais**: os 24 anteriores e 16 novos desta consulta. Os novos testes verificam limites exclusivos, eventos atravessando a madrugada, dias com 23/25 horas, dia civil inexistente, eventos de dia inteiro, paginação, seleção segura da agenda e erro sem sucesso parcial. Nenhuma operação de escrita no Google é chamada pelos testes de consulta. `pip check` também passou.

As [consultas por semana e mês](consulta-semana-mes.md) foram acrescentadas na parte 2, preservando este comando e seu formato de resposta. Identificação, alteração e exclusão também foram concluídas por comandos Python nas partes seguintes. A entrevista histórica foi posteriormente reagendada, editada e excluída. Consulte o [fechamento da etapa 07](fechamento-etapa-07.md) para o estado final e a suíte de 139 testes. Este comando ainda não está conectado a uma rota HTTP, ao n8n ou ao WhatsApp.

Referências: [events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [fusos do calendário](https://developers.google.com/workspace/calendar/api/concepts/events-calendars#calendar_time_zone) e [zoneinfo no Python](https://docs.python.org/3.14/library/zoneinfo.html).
