# Sincronização dos lembretes — etapa 12, parte 5

`app/services/reminder_sync.py` confere pelo ID os eventos que já possuem avisos ativos e aplica cancelamentos/replanejamentos em uma única transação local. `app/sync_reminders.py` executa essa conferência manualmente. A entrega não inicia verificação periódica, não altera eventos no Google e não envia mensagens.

## Executar

Na raiz do projeto, com o perfil configurado na [geração da parte 4](geracao-lembretes.md):

```powershell
.\.venv\Scripts\python.exe -B -m app.sync_reminders
```

O comando aceita `--database` para outra base privada. Não oferece flags de habilitação, destino ou envio; a configuração operacional continua na CLI de geração. Perfil ausente, desligado ou sem permissão retorna respectivamente `not_configured`, `disabled` ou `permission_required`, antes de ler a política do WhatsApp ou abrir o Google. Num perfil ativo, o vínculo precisa coincidir com a política privada atual, mesmo quando não há avisos para conferir.

Sem avisos `pending`, `blocked` ou `reserved`, retorna `synchronized` com contagens zero e não abre cliente Google. Isso é uma execução ociosa bem-sucedida, não prova de consulta, cancelamento ou reagendamento real. Para descobrir novos eventos e também atualizar os já acompanhados, os dois comandos manuais podem ser executados em sequência:

```powershell
.\.venv\Scripts\python.exe -B -m app.plan_reminders
.\.venv\Scripts\python.exe -B -m app.sync_reminders
```

Cada comando tem seu próprio lote transacional; a dupla não é uma transação distribuída. O n8n acionará essa rotina numa etapa posterior. O transporte ainda precisará revalidar evento, relógio e canal antes de enviar.

## Consulta exata e comprovação de ausência

O sincronizador captura todo o conjunto ativo do perfil, agrupa os IDs e consulta cada recurso por `events.get(calendarId, eventId)`. A leitura usa a agenda própria de teste e seu fuso IANA efetivo. **Não usa ausência na listagem de oito dias como exclusão.** Um evento acompanhado que foi movido para além desse horizonte continua sendo conferido por ID e recebe o novo planejamento.

| Resultado da consulta | Decisão |
|---|---|
| Evento exato confirmado, temporizado e futuro | Atualizar informações e planejar os slots ativos no início atual |
| Início diferente | Cancelar avisos ativos do início antigo e preparar os do novo início na mesma transação |
| Tombstone com ID exato e `status=cancelled` | Cancelar os avisos ativos; datas não são necessárias |
| GET exato retorna 404, ou 410 com motivo estruturado `deleted` | Só aceitar ausência depois de reconfirmar que a agenda permanece acessível e autorizada |
| Evento virou tentative/dia inteiro ou já iniciou | Cancelar os avisos ativos com motivo `event_changed`, sem criar novos planos |
| Falha de rede, 403, 429, 5xx, resposta inválida, ID diferente ou perda da agenda | Recusar o lote inteiro sem cancelar ou replanejar avisos |

O adapter valida todos os IDs e o vínculo antes dos GETs. Para 404/410 de um **GET de evento**, faz uma confirmação posterior por `calendarList.get`: ID correto, proprietário, agenda separada da principal, não removida e mesmo fuso válido da fotografia. Erro nessa confirmação não significa que o evento foi excluído. Falha na descoberta da agenda também nunca vira ausência de evento. Os GETs usam os escopos já existentes, incluindo `calendar.calendarlist.readonly`.

O HTTP 410 pode representar outros problemas, como `fullSyncRequired` ou `updatedMinTooLongAgo`. Esses motivos, corpo malformado ou razões misturadas são recusados; não há limpeza de banco nem tentativa de “full sync” destrutiva. [Referências oficiais: events.get](https://developers.google.com/workspace/calendar/api/v3/reference/events/get), [calendarList.get](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/get) e [erros](https://developers.google.com/workspace/calendar/api/guides/errors).

Recursos da série com `recurrence` são recusados, inclusive quando cancelados. Ocorrências usam seu próprio ID; o horário original da recorrência pode diferir do atual depois de um reagendamento. O offset retornado determina o instante UTC, e o fuso da agenda é usado na apresentação.

## Estados, informação atual e histórico

Somente `pending`, `blocked` e `reserved` compõem o escopo ativo da sincronização. `sending`, `accepted` e `uncertain` não são cancelados, liberados por timeout nem transformados em autorização de nova tentativa. Seu histórico permanece mesmo se o evento for removido. Se outro slot ativo do mesmo evento for conferido, os estados protegidos também continuam intactos.

- Título ou fuso mudou sem alterar o início: atualizar os avisos ainda não enviados, sem criar outra identidade.
- Reserva anterior ao transporte com conteúdo/fuso alterado: cancelar a reserva e replanejar a mesma identidade. O token antigo não pode avançar para envio. Título equivalente depois da normalização não revoga reserva desnecessariamente.
- Início mudou: cancelar os antigos e planejar o início atual, inclusive fora dos oito dias. A configuração continua sendo a do perfil.
- Evento retornou a um início já usado: reutilizar a identidade existente. Se ela já está aceita ou incerta, não criar outro aviso para o mesmo slot/início/vínculo.
- Evento iniciou ou deixou de ser elegível: cancelar com `event_changed`. Isso difere de `expired`, usado para vencimento de prazo pela geração/expiração; ambos impedem o envio indevido.

A sincronização não descobre eventos novos nem revisita histórico somente cancelado/expirado/protegido. A geração manual pode reconsiderar identidades canceladas/expiradas sem envio potencial quando o evento voltar a ser elegível. Não existe restauração de alerta já enviado ou incerto.

## Concorrência e integridade

`sync_snapshot()` captura configuração, todos os registros ativos e o último número global do histórico na mesma transação. O limite é **6000 registros ativos e 200 IDs distintos**; uma linha sentinela detecta excesso, sem aceitar truncamento silencioso. Esses são limites locais, não cotas do Google. O adapter também limita a consulta a 200 IDs únicos.

Após validar a resposta inteira e atualizar o relógio, `apply_sync()` abre um único `BEGIN IMMEDIATE` e compara novamente configuração, conjunto ativo completo e sequência do histórico. Essa comparação detecta revisão alterada, nova identidade inserida, reserva/envio iniciado em paralelo e mudança que voltou aos mesmos valores no mesmo instante. Qualquer divergência recusa o lote para uma futura nova conferência, sem retry interno automático.

Todos os planos/cancelamentos são validados antes da primeira escrita; somente eventos pertencentes ao escopo capturado podem ser replanejados. Cancelamentos e novos planos compartilham o commit/rollback. Uma falha depois de um cancelamento ou de uma primeira inserção não deixa metade da sincronização aplicada. O schema 1 permanece, com novos métodos e DTOs privados, sem migração destrutiva.

O JSON do comando mostra apenas contagens e `messages_sent: 0`: `tracked_reminders` é o escopo ativo inicial; `reminders_considered` é a união por identidade dos avisos antigos e dos planos atuais. `cancelled_count` conta transições, inclusive reserva cancelada e replanejada na mesma identidade; `replanned_count` conta gravações novas/alteradas, não apenas inserções. `unchanged_count` conta identidades preservadas; `state_counts` descreve o estado final dessa união. Não imprimir IDs, títulos, telefone, token ou conteúdo HTTP.

## Limites e verificação

As leituras Google e a transação SQLite não são atômicas entre si. A confirmação de agenda após 404/410 reduz a ambiguidade, mas não impede uma alteração externa imediatamente depois. Por isso a revalidação final antes do transporte continua necessária na parte 7. O helper legado pode gravar metadados locais da agenda ou token OAuth renovado; esta funcionalidade não escreve eventos no Google.

Os testes usam SQLite real e respostas Google fictícias. Conferem reagendamentos, cancelamentos, falhas intermediárias, mudança de estado/configuração, inserção concorrente, alterações de ida e volta, rollback tardio e bloqueio do token anterior. O comando também foi conferido no banco do projeto: sem avisos ativos, retornou execução ociosa sem abrir cliente Google ou modificar a base. Os cenários de mudanças foram verificados nas fixtures, sem inventar sincronização externa. Resultados e limites ficam no [índice de evidências](evidencias.md). Backend demonstrado em JSON; PDF permanece para o fechamento da etapa.


**Atualização da parte 6:** o [tick autenticado](lembretes-n8n.md) agora coordena a sincronização **antes** da geração e é acionado pelo n8n a cada minuto. Os comandos acima continuam disponíveis individualmente; seus lotes não formam uma transação única. O tick mantém essa separação e informa resultado parcial se uma fase posterior falhar.
