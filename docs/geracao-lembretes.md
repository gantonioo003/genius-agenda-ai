# Geração dos lembretes — etapa 12, parte 4

O serviço `app/services/reminders.py` consulta uma fotografia completa dos próximos eventos e prepara seus avisos no SQLite. O adaptador `app/reminder_calendar.py` lê o Google; `app/plan_reminders.py` oferece a execução manual. Esta entrega não inicia agendador, não chama LLM e não possui transporte de WhatsApp.

## Execução explícita

Na raiz do projeto, após a [persistência da parte 3](persistencia-lembretes.md):

```powershell
.\.venv\Scripts\python.exe -B -m app.plan_reminders
```

O comando usa o perfil existente. Ausência de configuração retorna `not_configured`; perfil desligado retorna `disabled`; falta de permissão retorna `permission_required`. Nesses casos, não abre cliente Google nem os arquivos privados do WhatsApp.

Para o operador configurar explicitamente a agenda de teste e o destinatário já autorizados:

```powershell
.\.venv\Scripts\python.exe -B -m app.plan_reminders --configure --enable --allow-reminders
```

`--configure` sem os dois últimos flags mantém o perfil desligado e sem permissão. `--enable` e `--allow-reminders` exigem `--configure`; não são interpretados a partir de conversas do modelo. A configuração preserva intervalos personalizados de um perfil existente e usa 60/15 por padrão quando o cria. O armazenamento continua aceitando o aviso opcional de 24h e os demais intervalos definidos na parte 1.

Essa operação registra a permissão operacional explícita para os avisos do perfil pessoal de teste, sem inferi-la do `START` do sandbox. Uma implantação para terceiros precisará obter sua própria permissão, conforme [limites do canal](canal-lembretes.md). Habilitar o perfil não inicia um timer ou envio; nesta entrega, a única execução é a geração solicitada pelo comando.

O vínculo do destinatário deriva da política privada fixa do sandbox e usa o mesmo HMAC/namespace do worker atual. Nenhum telefone, ID de evento, título ou token é aceito como destino na CLI. Mudança de política/token exige reconfiguração explícita; o comando normal recusa vínculo diferente antes de abrir o cliente Google. `--database` permite uma base privada alternativa, relativa à raiz do projeto ou absoluta; não altera `.env`.

## Seleção da agenda e intervalo

- A seleção reaproveita o conector existente, restrito à agenda própria de teste, separada da principal. O ID efetivamente resolvido precisa coincidir com o perfil antes de listar eventos.
- O fuso IANA da agenda é obrigatório, sem fallback para Windows, conversa ou timezone enviado ao agente. O fuso da agenda determina a apresentação dos avisos; início/fim e prazos permanecem instantes UTC.
- A janela local é de **oito dias**, a partir do relógio UTC no início da consulta. É uma decisão de implementação para cobrir os intervalos permitidos de até sete dias com folga para preparação.
- Ao terminar a leitura, o serviço atualiza o relógio e só aceita `agora_atual < início < limite_inicial_de_oito_dias`. Eventos iniciados durante a consulta ficam de fora; o limite final é exclusivo.

A API Google filtra por sobreposição: `timeMin` limita o fim do evento, enquanto `timeMax` limita seu início; por isso o filtro local de eventos ainda futuros é necessário. Como o Google ignora frações nos limites, o adaptador amplia a consulta aos segundos externos e aplica o intervalo exato localmente. A paginação é percorrida por inteiro, inclusive páginas vazias com token. [Referência oficial: events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).

O adaptador usa `singleEvents=True`, não interpreta regras de recorrência e não prepara um aviso para o recurso da série. Cada ocorrência usa seu ID próprio. `originalStartTime` pode diferir do início atual numa ocorrência reagendada; isso é válido. O offset explícito de `dateTime` determina o instante retornado, e um `timeZone` de evento informado é validado como IANA, sem exigir que sua representação seja a do fuso da agenda. [Referência oficial: events](https://developers.google.com/workspace/calendar/api/v3/reference/events).

## Elegibilidade e validação

Somente eventos `confirmed`, temporizados, futuros, dentro da janela e representados como evento único/ocorrência expandida geram planos. Dia inteiro, `tentative`, cancelados completos, iniciados e eventos fora da faixa são ignorados. Tombstones de cancelamento sem datas são ignorados pelo adaptador; não são tratados como instrução de cancelar lembrete nesta parte.

Dados malformados, status desconhecido, fusos inválidos, intervalo sem offset ou com fim anterior/igual ao início, série retornada indevidamente e ocorrência incoerente interrompem a consulta com erro genérico. IDs repetidos com conteúdo idêntico são deduplicados; cópias conflitantes abortam a fotografia. Uma resposta parcial não é usada para preparar avisos.

Os limites locais são **20 páginas e 2000 itens brutos**, incluindo tombstones e duplicatas; o serviço também limita seu snapshot a 2000 eventos. Limite atingido com paginação ainda pendente, token repetido ou falha de qualquer página causa erro, sem fingir que a consulta está completa. Os limites são escolhas locais de operação, não cotas declaradas do Google.

## Lote persistente e repetição

Depois da validação completa, o serviço confere novamente a revisão/configuração. Para cada evento elegível, cria um plano por slot ativo. `ReminderStore.plan_batch()` confirma a revisão **na mesma transação SQLite** dos planos: se a configuração mudou ou qualquer escrita falhar, o lote inteiro sofre rollback.

O novo método usa o schema 1 existente, sem migração. Uma fotografia com nenhum evento elegível também confere a revisão e não apaga avisos antigos. O instante atual do store é atualizado antes do cálculo do lote; o prazo de dois minutos e o limite do início continuam valendo. Slots já vencidos podem ser registrados como `expired`, enquanto os futuros permanecem `pending`; não há envio retroativo ou ajuste improvisado de horário.

Repetir a geração conserva a identidade de agenda/ocorrência/início/slot/vínculo. Título, fuso, offset e revisão não criam um segundo aviso. `reserved`, `sending`, `accepted` e `uncertain` permanecem protegidos. O método antigo `plan()` continua disponível e usa o mesmo caminho transacional do lote.

O JSON público do comando contém apenas status, contagens e `messages_sent: 0`. `reminders_considered` conta avisos considerados no lote, inclusive os já existentes ou vencidos; **não é uma contagem de novas inserções nem de mensagens enviadas**. `state_counts` descreve os estados desses avisos, sem expor seus dados.

## Efeitos locais e alcance

O Google recebe somente consultas. O helper legado `resolve_calendar` pode atualizar os metadados locais `google_calendar_id/name`; a renovação OAuth normal também pode salvar o token privado. A configuração explícita e o planejamento gravam no banco dos lembretes. Esta entrega não altera eventos no Google, workflow n8n, callback, serviço de IA ou transporte de mensagens, e não reinicia serviços.

A fotografia Google não é uma transação com o SQLite nem garante que o evento permaneça igual depois da consulta. A [parte 5](sincronizacao-lembretes.md) já sincroniza alterações e cancelamentos por comando separado; a parte 7 relerá o evento e aplicará o gate do canal antes de qualquer transporte. **A geração isolada não cancela a identidade do início anterior; executar a sincronização faz essa reconciliação.** Ausência numa faixa, cancelamento omitido ou erro de leitura não comprova exclusão.

## Verificação

Os testes de adaptador usam respostas Google fictícias; os testes de serviço/CLI usam SQLite real e dados fictícios. Cobrem paginação, limites, fusos, recorrências, campos inválidos, relógio atualizado, revisão concorrente, rollback tardio e repetição/reinício. A suíte completa inclui as operações de agenda e a persistência anteriores. Os resultados finais e a consulta real ficam no [índice de evidências](evidencias.md), separando fixtures de leitura efetiva do Google. Prints não são necessários para demonstrar esse resultado de backend; o PDF será consolidado no fechamento da etapa.


**Continuação — parte 5:** executar `python -m app.sync_reminders` após a geração confere os eventos acompanhados, inclusive fora do horizonte de oito dias. A [sincronização](sincronizacao-lembretes.md) usa seu próprio lote atômico; as duas CLIs não iniciam timer ou envio.
