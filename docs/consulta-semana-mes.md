# Consulta por semana e mês — etapa 07, parte 2

**Concluída e validada em 21/10/2026.**

## O que foi implementado

Em 21/10/2026, ampliamos `app/services/agenda.py` com períodos semanais e mensais e métodos de consulta independentes da IA. O conector Google existente foi reaproveitado, incluindo paginação, expansão de recorrências, seleção segura da agenda e tratamento de falhas. Nenhuma dependência ou permissão foi acrescentada.

O comando `app/list_events.py` recebeu `--period day|week|month`. O padrão continua sendo `day`; os comandos e o formato JSON da consulta diária permanecem compatíveis.

## Executar

Na raiz do repositório, com as dependências instaladas e a conta já autorizada:

```powershell
.\.venv\Scripts\python.exe -m app.list_events --period week --date 2026-10-22 --timezone America/Recife
.\.venv\Scripts\python.exe -m app.list_events --period month --date 2026-10-22 --timezone America/Recife
```

Os comandos consultam o Google sem alterar eventos. API, Docker e n8n não precisam estar iniciados. A descoberta da agenda pode atualizar seus metadados locais.

## Regras dos períodos

| Opção | Intervalo para a referência 22/10/2026 |
|---|---|
| `day` | 22/10, 00h até 23/10, 00h |
| `week` | Segunda, 19/10, 00h até segunda, 26/10, 00h |
| `month` | 01/10, 00h até 01/11, 00h |

O início está incluído e o fim é exclusivo. Assim, o domingo 25/10 pertence à semana consultada; um evento que começa exatamente em 26/10, 00h, fica para a próxima semana. Eventos que atravessam os limites também aparecem quando sobrepõem o intervalo.

Cada limite é calculado no fuso informado, sem presumir uma quantidade fixa de horas. Isso respeita mudanças de horário de verão, meses de tamanhos diferentes, anos bissextos e viradas de ano. Data, fuso ou intervalo não representável produzem erro antes de abrir o cliente Google.

Semanas e meses retornam `period`, `reference_date`, `start_date`, `end_date_exclusive`, `time_zone`, `calendar_name`, `count` e `events`. Os eventos mantêm o mesmo formato diário: ID, título, início/fim, dia inteiro, fuso, status e link quando disponível. Descrições e contatos não são solicitados. Uma falha produz erro; lista vazia só representa uma consulta concluída sem eventos.

## Validação real

Execuções de leitura realizadas em **21/10/2026**, com referência **22/10/2026** e fuso `America/Recife`:

| Consulta | Início | Fim exclusivo | Resultado |
|---|---|---|---|
| Semana | 19/10/2026 | 26/10/2026 | `status: ok`, `count: 1` |
| Mês | 01/10/2026 | 01/11/2026 | `status: ok`, `count: 1` |

Ambas retornaram a entrevista **Entrevista de estágio — Empresa Exemplo (teste)**, em `Genius Testes`, dia **22/10/2026, 15h–15h30**, com o mesmo ID da criação e status `confirmed`.

As saídas reais foram preservadas nos registros locais 009 e 010. A captura 011 mostra o evento na visão mensal do Google Agenda; ela comprova a presença visual, enquanto os JSONs registram os limites exatos das consultas. A interface pode usar outra organização das colunas da semana, sem alterar a regra segunda–domingo adotada pelo programa.

## Testes locais

Criamos `tests/test_period_queries.py` com **14 testes novos**. A suíte completa passou com **54 testes**, tanto na preparação isolada quanto no repositório, sem rede ou credenciais reais nos testes automatizados.

Os novos casos cobrem segunda-feira e domingo, virada de ano, fevereiro comum e bissexto, último dia do mês, dezembro/janeiro, semanas com 167/169 horas, meses com mudança de fuso, intervalos inválidos antes de qualquer acesso, serviço com conector injetado, compatibilidade diária, parâmetros enviados ao Google simulado e falhas sem falso sucesso. As consultas são verificadas sem chamadas de criação, alteração ou exclusão.

## Limites desta parte

A funcionalidade lista os compromissos. Identificação, edição e exclusão por comandos Python foram concluídas nas partes seguintes; a entrevista destes registros históricos foi removida ao final. Consulte o [fechamento da etapa 07](fechamento-etapa-07.md). Resumos personalizados com IA, escolha do evento na conversa e conexão HTTP/n8n/WhatsApp seguem pendentes.

Referências: [consulta diária e regras compartilhadas](consulta-por-dia.md), [events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list) e [zoneinfo](https://docs.python.org/3.14/library/zoneinfo.html).
