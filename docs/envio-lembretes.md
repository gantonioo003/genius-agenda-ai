# Envio dos lembretes — etapa 12, parte 7

**Marco da parte 7:** a [parte 8](recuperacao-lembretes.md) acrescentou recuperação local antes de tick/dispatch. O setup agora publica oito nós e limite de 420 s; reservas antigas só retornam à validação após prova de abandono pela trava exclusiva e sem envio potencial. O dispatch abaixo conserva todas as validações antes do transporte.

A API processa avisos devidos do ledger e pode enviar texto ao destinatário privado autorizado do sandbox. Antes de cada tentativa, relê o evento, confere perfil/permissão, horário do aviso e janela do canal. O texto é determinístico; não há chamada ao LLM ou escrita de eventos Google. [Planejamento](geracao-lembretes.md), [sincronização](sincronizacao-lembretes.md) e [persistência](persistencia-lembretes.md) continuam sendo as bases da rotina.

## Operação pelo n8n

O workflow `lembretes-periodicos.json` mantém o Schedule Trigger de um minuto. Seu grafo tem seis nós: preparar correlação e chamar/validar o tick; depois chamar/validar o dispatch. O tick sincroniza antes de gerar planos. Somente seu HTTP 200 com DTO válido `completed` ou `skipped` permite a segunda chamada; HTTP 503, transporte inválido ou DTO incompatível interrompe o fluxo.

| Chamada | Contrato |
|---|---|
| `POST /api/v1/reminders/tick` | Sincronização e geração, conforme [contrato da parte 6](lembretes-n8n.md) |
| `POST /api/v1/reminders/dispatch` | Processamento dos avisos devidos; não recebe evento, destinatário ou texto |
| Ambas | Corpo exatamente `{}`, sem query; `Content-Type: application/json`, Bearer existente e `X-Request-ID: reminder-tick-<execution_id>` |
| Limites | HTTP 120 s por chamada; workflow 300 s; sem retry ou redirecionamento |

O request ID é correlação, não a identidade de envio. O ledger continua impedindo repetição por agenda/ocorrência/início/slot/vínculo. O tick e o dispatch usam o mesmo lock de SO por base, inclusive entre processos; uma execução concorrente recebe `busy`. O lock permanece até terminar o trabalho real, mesmo se o n8n deixar de esperar, sem TTL que permita sobreposição.

Com API, Docker e perfil autorizado configurados, reaplique pelo setup específico:

```powershell
.\.venv\Scripts\python.exe -B scripts/setup_reminder_schedule.py --apply
```

Esse comando atualiza/publica somente o workflow de lembretes e reinicia brevemente o n8n. A API e o receptor precisam estar executando o código desta parte. A credencial `Genius API - local` e os IDs privados existentes são reutilizados; a chave do provedor e o destinatário ficam na API. O PC precisa permanecer ligado com API/Docker ativos. O export público continua inativo, sem dados fixados ou credenciais privadas; publicar na instância permite processar avisos reais que passem por todas as validações.

## Reserva e ponto final de envio

O serviço captura até **200 candidatos devidos**, com detecção de excesso para não aceitar truncamento silencioso. Avisos fora do prazo vencem sem envio retroativo. Perfil ausente, desligado, sem permissão ou lock ocupado retorna `skipped`, sem transporte. A seleção não retoma reservas de execuções anteriores nem estados `sending`, `accepted` ou `uncertain`.

Para cada candidato, a sequência é:

1. Conferir destinatário/vínculo privado e janela do canal; consultar o evento exato e validar texto, início e fuso. O sender só é aberto quando necessário.
2. Reservar a identidade com revisão do perfil. Somente uma reserva nova autoriza continuar.
3. Fazer **uma segunda consulta exata ao evento após reservar**, obter os dados atuais e reler o par privado telefone/vínculo. Mudança de evento, horário ou destinatário impede o envio.
4. Executar `begin_delivery()` com CAS da reserva e conferência de perfil vigente, relógio e última observação válida do canal **na mesma transação que marca `sending`**. O título e fuso atuais acompanham essa transição.
5. Chamar o sender uma vez e persistir o resultado. Aceite válido gravado produz `accepted`; falha ou resultado não confirmado depois de `sending` conserva incerteza.

Evento removido, cancelado, reagendado, iniciado ou inelegível invalida o aviso antigo. A sincronização de um pulso posterior prepara os planos pertinentes; o dispatch não improvisa outro horário ou destino. A leitura Google e a transação local não formam uma transação distribuída: uma alteração externa posterior à última leitura permanece possível. O provedor continua autoritativo no aceite.

## Janela do canal e ingresso

Mensagem do usuário abre/renova a janela de atendimento de 24 h; fora dela, texto livre requer uma alternativa por template. A implementação reconhece apenas novas mensagens de texto elegíveis do remetente autorizado, com timestamp original válido. Chamadas, mensagens do bot e status de entrega/leitura não abrem a janela local. [Mensageria 360dialog, conferida em 26/10/2026](https://docs.360dialog.com/docs/messaging/overview).

A condição local é `last_user_message <= now < last_user_message + 24h - 60s`. Os **60 s são margem operacional do projeto**, não regra publicada ou garantia contra latência. Janela desconhecida (`channel_window_unknown`), encerrada (`channel_window_closed`) ou na margem (`channel_window_margin`) produz `blocked`, sem tentativa de transporte. Uma nova entrada válida pode permitir reconsiderar a mesma identidade antes do prazo do aviso; após o prazo, ela vence. Não há template contextual de lembrete configurado no sandbox e não se usa `disclaimer` como substituto. O sandbox oferece somente seus três templates predefinidos e vincula o envio à identidade da conta de teste. [Sandbox 360dialog, conferido em 26/10/2026](https://docs.360dialog.com/docs/get-started/sandbox).

O receptor registra hash do ID de uma nova mensagem, vínculo, instante original UTC, recebimento e proveniência. Timestamp ausente, inválido ou futuro em relação ao recebimento não abre janela; duplicata conserva a primeira observação. Não se substitui o timestamp pelo processamento local nem se faz backfill a partir de conversas ou horários técnicos antigos.

O callback atual é protegido por URL com capacidade secreta e filtro de remetente; **isso não equivale a assinatura criptográfica de origem**. A proveniência `sandbox_callback_capacity` mantém essa limitação explícita. Entradas sintéticas são usadas somente em ambiente isolado de testes: declarar essa proveniência em uma fixture não comprova origem e nunca deve abrir a janela da base real.

## Aceite, incerteza e recuperação

| Estado/resultado | Efeito operacional |
|---|---|
| `pending`/`blocked` | Podem ser novamente conferidos enquanto houver prazo; o bloqueio não consome tentativa de transporte |
| `reserved` | Pertence a uma tentativa; reserva antiga não é retomada/liberada automaticamente |
| `sending` | Transporte pode ter iniciado; não liberar por TTL ou reinício |
| `accepted` | Aceite do provedor foi persistido; não comprova entrega ou leitura e impede novo envio da identidade |
| `uncertain` | Pode ter havido envio; bloqueia retry automático |
| `cancelled`/`expired` | Aviso invalidado ou fora do prazo; não enviar em rajada posterior |

Qualquer falha do sender após `sending`, resposta inválida ou falha ao persistir o aceite é tratada como incerta. Se nem `mark_uncertain()` puder ser gravado, o registro pode permanecer `sending`; esse estado continua bloqueado. A implementação não troca o tipo da mensagem nem repete a chamada para tentar obter certeza. Antes do transporte, somente a reserva adquirida pela própria execução pode ser liberada quando seguro; isso não recupera uma reserva abandonada de outra execução.

O dispatch usa o envelope v1 com ação `reminder_dispatch`, `operation_id: null` e dez campos em `data`: `dispatch_status`, `skip_reason`, `reminders_considered`, `expired_count`, `cancelled_count`, `blocked_count`, `accepted_count`, `uncertain_count`, `failed_count` e `transport_attempts`. `transport_attempts = accepted_count + uncertain_count`; os aceites contam novas confirmações persistidas, e a incerteza também cobre transporte não conciliado que permaneceu `sending`.

HTTP 200 representa `completed` ou `skipped`; 503 representa `failed` ou `partial`. `completed` pode conter bloqueios e zero envios. `mutation_state` é `confirmed` quando houve somente aceites persistidos, `unknown` quando há tentativa incerta e `not_applied` quando não houve tentativa externa, mesmo que estados locais tenham mudado. Uma falha inesperada na fronteira depois de entrar no serviço pode retornar 503 com `data: null` e `mutation_state: unknown`; não se inventam contadores zero ou ausência de envio. O normalizador encerra o workflow com erro fixo em falhas, sem repetir o transporte.

## Texto e privacidade

O corpo tem cabeçalho `Lembrete da agenda`, título e início absoluto como `dd/mm/aaaa às HH:MM (America/Fortaleza)`, usando o fuso IANA efetivo da agenda. O título é normalizado para NFC, whitespace vira espaço, controles Unicode `Cc/Cf` são removidos e espaços são colapsados. Título vazio vira `Compromisso`; o limite é **160 pontos de código**, incluindo `…` se houver corte. O corpo inteiro tem até **1000 pontos de código** por `len()` Python, orçamento local; não se corta data/hora/fuso para caber.

O n8n recebe apenas correlação, estados e contadores, sem título, IDs de evento, telefone, texto ou resposta bruta do provedor. Execuções de sucesso, erro, manuais e progresso continuam sem dados salvos; não há `staticData`. O ledger privado preserva histórico, reserva e fingerprint, não um recibo de entrega/leitura. Conservar banco e configurações nos backups; apagar a base perde a proteção contra repetição. O transporte não chama LLM nem escreve eventos Google.

## Verificação e alcance

**1.874 testes Python aprovados, 96 novos, e 853 verificações JavaScript, 134 novas.** Cinco percursos integrados callback→janela→tick→dispatch usaram SQLite real e agenda/transporte fictícios. Confirmaram aceite único após repetição/reinício, bloqueio com janela desconhecida, liberação após nova entrada válida, replay incapaz de renovar janela, cancelamento antes do envio e incerteza sem outra chamada. Os grafos instalado e publicado correspondem ao export público; quatro workflows anteriores foram preservados. Registro 132. Testes sintéticos devem permanecer separados da base/callback real e comprovar prazo, janela, mudança do evento/perfil, concorrência, falhas antes/depois de `sending`, aceite, incerteza e repetição bloqueada.

**Runtime real conferido em 26/10/2026:** API/receptor atualizados, 401 sem chave e 422 com campo extra; preflight posterior concluiu tick e dispatch com contagens zero. As primeiras chamadas retornaram `busy`, sem trabalho duplicado; não foram apresentadas como conclusão. O n8n publicou o grafo de seis nós e concluiu disparo natural às 02:39:20 (UTC−03), com ambos os HTTPs e normalizadores terminados. Os payloads naturais não são salvos: isso confirma DTOs válidos `completed` ou `skipped`, sem atribuir contagens ao disparo. Não havia avisos devidos e não foi enviado lembrete real, criada entrevista para o ensaio ou injetada mensagem sintética no callback real. Nova observação real e confirmação humana de um lembrete ainda não foram verificadas nesta parte. Registro 133. A prova histórica da parte 6 demonstrou preparação e disparo natural, sem transporte; ela não substitui a validação desta parte. Resultados e limites estão consolidados no [índice de evidências](evidencias.md).
