# Alterar data e horário — etapa 07, parte 4

**Concluída e validada em 21/10/2026.**

## Implementação

Em 21/10/2026, criamos `app/services/rescheduling.py`, independente da IA e do Google, e o comando `app/reschedule_event.py`. O conector Google passou a reagendar pelo ID exato na agenda de teste configurada.

O serviço valida os novos horários antes de abrir a conexão. O conector lê novamente o evento e sua versão (`etag`), envia somente `start` e `end` por PATCH e confirma o resultado em outra leitura pelo mesmo ID. Não altera título, descrição, participantes ou demais informações.

## Executar

Na raiz do projeto, com a conta já autorizada, substitua `ID_RETORNADO_PELA_CONSULTA` pelo ID do evento identificado:

```powershell
$eventId = "ID_RETORNADO_PELA_CONSULTA"
.\.venv\Scripts\python.exe -m app.reschedule_event --event-id $eventId --start "2026-10-23T16:00:00-03:00" --end "2026-10-23T16:30:00-03:00" --timezone America/Recife
```

O padrão é **prévia somente de leitura**: mostra o evento atual e os horários solicitados com `status: preview`. Para aplicar a mudança explicitamente:

```powershell
.\.venv\Scripts\python.exe -m app.reschedule_event --event-id $eventId --start "2026-10-23T16:00:00-03:00" --end "2026-10-23T16:30:00-03:00" --timezone America/Recife --execute
```

API, Docker e n8n não precisam estar iniciados. A seleção segura da agenda pode atualizar metadados locais. O resultado inclui `calendar_name`, `before`, `after`, `requested_start`, `requested_end` e `time_zone`.

| Status | Significado |
|---|---|
| `preview` | Leitura concluída; nenhuma gravação solicitada; `after` fica nulo |
| `rescheduled` | PATCH aceito e nova leitura confirmou o mesmo ID e os horários desejados |
| `unchanged` | Na execução, o evento já tinha os mesmos instantes; nenhuma gravação foi feita |

Erros retornam código 1 e mensagem em stderr, sem JSON de sucesso. Erros de sintaxe dos argumentos retornam código 2.

## Validações e controle de falhas

- Início e fim devem incluir data, hora e deslocamento UTC. O fim deve ser posterior ao início em UTC.
- Data/hora local e offset precisam corresponder ao fuso IANA informado. Horas inexistentes são rejeitadas; horas ambíguas com offset válido explícito são aceitas.
- ID ausente, cancelamento, identidade incorreta, estado desconhecido ou versão indisponível impedem a gravação. Não há busca de outro evento como substituto.
- Esta operação atende eventos temporizados comuns e ocorrências individuais. Dia inteiro, série recorrente completa e tipos especiais exigem operações próprias e são rejeitados.
- O ETag da leitura vai no cabeçalho `If-Match`. Uma mudança concorrente produz erro 412 e interrompe a operação, sem reaplicação automática.
- O PATCH usa `num_retries=0`. Se a gravação ou a leitura posterior falhar, o programa informa que o resultado pode estar aplicado e orienta consultar o ID antes de repetir.
- Repetir uma execução com os mesmos instantes atuais retorna `unchanged`. Isso evita uma escrita redundante; não representa um registro persistente de idempotência da operação.

A prévia não trava o evento entre comandos. `--execute` relê o estado atual; a proteção de versão cobre o intervalo entre essa leitura e a gravação. O PATCH usa `sendUpdates="none"`; a demonstração é feita sem convidados. Mensagens de WhatsApp e lembretes serão implementados em outras etapas.

## Demonstração real

Reagendamento da entrevista fictícia de `Genius Testes`, realizado em **21/10/2026**:

| Dado | Antes | Depois |
|---|---|---|
| Data | 22/10/2026 | 23/10/2026 |
| Horário | 15h–15h30 | 16h–16h30 |
| Fuso | America/Recife | America/Recife |
| Duração | 30 minutos | 30 minutos |
| ID e título | Mesma entrevista fictícia | Preservados |

Os registros locais separam a prévia (016), a execução confirmada (017), a consulta posterior e conferência dos dois dias (018) e a captura real do Google Agenda (019). Os registros anteriores documentam o horário original; não devem ser apresentados como estado atual.

O comando de criação `app.create_test_interview` continua descrevendo a solicitação original de 22/10. Enquanto o evento reagendado existia, `--execute` rejeitava a divergência de dados. A entrevista foi posteriormente editada e excluída; esse comando não deve ser usado para consultar ou recriar o evento removido. Uma nova demonstração deve preparar outro evento de teste com ID próprio.

## Testes e revisão

Criamos `tests/test_rescheduling.py`, com **20 testes novos**. A suíte completa passou com **92 testes**, tanto na preparação isolada quanto no repositório. Os testes automatizados usam clientes falsos, sem rede ou credenciais reais; a demonstração no Google foi executada separadamente.

Os casos cobrem validação antes do acesso, fuso/offset e horário de verão, prévia, serviço injetado, PATCH mínimo com ETag, ordem GET/PATCH/GET, ausência de escrita redundante, erro 412 sem reaplicação, timeout e falhas HTTP com resultado incerto, confirmação posterior divergente, identidade e versão inválidas, seleção segura da agenda, ocorrências e contratos da CLI. Uma revisão independente do código não encontrou bloqueadores no escopo.

## Continuação

Alteração das demais informações e exclusão com confirmação foram concluídas nas partes 5 e 6. Consulte o [fechamento da etapa 07](fechamento-etapa-07.md) para o estado final e os 139 testes. Detecção de conflitos, lembretes persistentes e atualização desses lembretes após um reagendamento ainda estão pendentes. Não há integração HTTP, n8n ou WhatsApp nesta parte.

Referências: [semântica de events.patch](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch) e [controle de versão com ETag e If-Match](https://developers.google.com/workspace/calendar/api/guides/version-resources).
