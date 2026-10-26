# Recuperação dos lembretes — etapa 12, parte 8

A recuperação reconcilia o estado local deixado por uma execução encerrada. Uma reserva comprovadamente anterior ao transporte pode voltar à fila; um envio potencial permanece bloqueado como incerto. A rotina usa o ledger existente, **schema 1**, sem consultar Google, WhatsApp ou LLM e sem apagar histórico. O [envio da parte 7](envio-lembretes.md) continua responsável por todas as validações antes de qualquer nova tentativa.

## O que autoriza recuperar

O serviço precisa **adquirir efetivamente o mesmo lock de SO** usado por tick e dispatch para a base canônica. Enquanto o proprietário anterior continua com o lock, a recuperação retorna `busy`, sem alterar registros. A exclusão vale entre processos que seguem esse protocolo; não depende de adivinhar se um PID existe ou de quanto tempo passou.

O lock permanece retido até o trabalho real terminar. Timeout do n8n, perda da conexão HTTP ou um worker que apenas deixou de responder não liberam a reserva. Se o processo encerra, o SO fecha o descritor e libera o lock; somente depois de outra execução adquirir essa exclusão há prova de que nenhum proprietário participante ainda pode continuar. O arquivo do lock não é removido para forçar essa condição.

| Estado encontrado | Decisão local |
|---|---|
| `reserved`, antes do prazo e sem histórico de envio potencial | Voltar a `pending`; invalidar o claim antigo, preservando sua passagem no histórico |
| `reserved`, prazo encerrado | Mover a `expired`, sem tentativa retroativa |
| `sending`, depois de encerrado o proprietário | Mover a `uncertain`, com a **mesma tentativa**, nunca para `pending` |
| `accepted` ou `uncertain` | Preservar estado, tentativa e eventual fingerprint; não autorizar outro envio |
| Estado/histórico incoerente ou mudança concorrente | Recusar a aplicação, sem liberar a proteção contra repetição |

Tempo decorrido não comprova ausência de transporte. Não há TTL de reserva/envio, flag `force`, reset de tentativa, descarte do ledger ou passagem de `sending` para uma fila reenviável. A recuperação não confirma se o provedor recebeu uma solicitação: `uncertain` mantém justamente essa dúvida. Um aceite persistido continua sendo aceite, sem se converter em confirmação de entrega/leitura.

## Snapshot, relógio e aplicação

Sob o lock de SO, o serviço captura o conjunto completo de `reserved`/`sending`, limitado a **200 registros**, com uma linha sentinela para detectar excesso. A fotografia conserva perfil, registros completos, contagem dos protegidos, sequência global do histórico e instante capturado. A aplicação usa uma transação SQLite e compara novamente esses valores; mudança de perfil, inserção ou transição de ida e volta recusa o lote. O claim e a tentativa esperados não podem ser trocados entre a captura e a escrita. Relógio inválido, regressivo ou anterior aos registros observados também interrompe a recuperação.

As mudanças do lote compartilham commit/rollback. Uma falha intermediária não deve deixar metade das reservas liberada e metade reconciliada. Ao preservar a tentativa de `sending → uncertain`, uma resposta tardia ou reinício não cria uma nova identidade capaz de reenviar o mesmo aviso. Reservas liberadas conservam histórico, e seu token antigo deixa de autorizar transições.

`protected_count` conta os `accepted` e `uncertain` já existentes; não afirma uma nova confirmação. `uncertain_count` conta apenas os `sending` convertidos a `uncertain` nesta recuperação. `reservations_recovered` conta as reservas que voltaram a `pending`, e `expired_count` as que venceram. O resultado não inclui IDs ou tokens das tentativas.

## Fluxo periódico e reprodução

O n8n executa **recover → tick → dispatch**, cada POST seguido de seu normalizador estrito. São oito nós: Schedule Trigger, preparação da correlação, HTTP/normalização de recovery, HTTP/normalização do tick e HTTP/normalização do dispatch. O intervalo continua em um minuto; cada HTTP tem timeout de 120 s e o workflow passa a **420 s**, sem retries ou redirecionamentos. Falha HTTP, de transporte ou de DTO interrompe a sequência, sem fabricar sucesso ou repetir a chamada.

| Chamada | Função |
|---|---|
| `POST /api/v1/reminders/recover` | Reconciliar somente o estado local depois de adquirir o lock |
| `POST /api/v1/reminders/tick` | Sincronizar eventos acompanhados e gerar planos |
| `POST /api/v1/reminders/dispatch` | Reler evento/perfil/canal e processar avisos devidos |

As três rotas exigem corpo exatamente `{}`, sem query, `Content-Type: application/json`, o Bearer existente e o mesmo `X-Request-ID: reminder-tick-<execution_id>`. Não recebem ID de evento, token de tentativa, destinatário, novo horário ou instrução para forçar recuperação. HTTP 200 `completed` ou `skipped`, com DTO e correlação validados, permite seguir ao tick; HTTP 503 ou retorno incompatível interrompe a sequência.

O recover usa o envelope v1 com ação `reminder_recovery`, `operation_id: null` e `mutation_state: not_applicable`, pois não há mutação externa. `data` tem oito campos exatos: `recovery_status`, `skip_reason`, os quatro contadores acima, `messages_sent: 0` e `external_mutations: 0`. Perfil ausente, desligado, sem permissão ou lock ocupado produz `skipped` com motivo `not_configured`, `disabled`, `permission_required` ou `busy`, respectivamente, e contagens zero. `completed` também pode ter zero transições.

Falha retorna HTTP 503 e erro fixo `reminder_recovery_failed`, sem retry. Se o commit teve resultado conhecido e apenas o fechamento do lock falhou, a resposta `failed` preserva os contadores confirmados. Se uma exceção impedir confirmar o resultado da aplicação, a API retorna `data: null`; não inventa contadores zero nem afirma rollback. Esse erro também impede tick/dispatch na mesma execução do workflow.

Depois de instalar o código desta parte na API, reaplique o workflow pelo setup específico:

```powershell
.\.venv\Scripts\python.exe -B scripts/setup_reminder_schedule.py --apply
```

O setup reutiliza o ID e a referência da credencial `Genius API - local`, publica somente o workflow de lembretes e reinicia brevemente o n8n. Configurações e credenciais anteriores permanecem privadas; o export público continua inativo, com dados fixados/tags vazios e sem dados de execução. PC, Docker e API precisam permanecer ativos. O [inventário n8n](../n8n/README.md) distingue o estado publicado das versões distribuídas.

## Recuperar não autoriza enviar

Uma reserva devolvida a `pending` só fica disponível para **nova conferência completa**. Tick e dispatch ainda precisam validar perfil/permissão vigentes, vínculo privado, ocorrência exata no Google, início atual, prazo do aviso e janela do canal. A recuperação não abre janela de WhatsApp, não renova uma entrada do usuário e não recupera prazo vencido. Mudança de agenda, destinatário ou evento continua impedindo o transporte inadequado.

O dispatch reserva novamente com um claim válido, relê o evento após reservar e aplica sua conferência atômica imediatamente antes de `sending`. Não existe retry automático de transporte: `accepted`, `uncertain` e qualquer `sending` ainda não reconciliado permanecem protegidos. Uma nova execução periódica não deve ser confundida com repetição cega de uma solicitação potencialmente enviada.

Para investigar um resultado incerto, preserve banco, histórico e configurações. Não apague o arquivo de lock ou a base para liberar trabalho; não altere manualmente estado/tentativa e não use restauração de backup como autorização de reenvio. A recuperação local desta parte não oferece reconciliação externa de entrega, consulta de recibo ao provedor ou autorização para repetir um envio incerto.

## Privacidade e verificação

O retorno expõe somente estados e contadores agregados, sem IDs, títulos, telefone, token, fingerprint ou payload bruto. O n8n mantém desativado o salvamento de sucesso, erro, execução manual e progresso e não usa `staticData`. A execução de recovery tem zero chamadas de agenda/provedor; isso não elimina as leituras e o eventual transporte das fases posteriores do workflow.

**1.936 testes Python aprovados, 62 novos, e 962 verificações JavaScript, 109 novas.** São 32 testes de serviço/store, 22 da API, cinco percursos de recuperação→planejamento→envio e três novos do instalador, além das suítes anteriores. A interrupção de um processo real liberou o lock do SO e permitiu recuperar apenas a reserva anterior ao envio; outro caso converteu `sending` em `uncertain`, mantendo tentativa e 44 registros protegidos antigos. Proprietário vivo permaneceu `busy`, claim antigo foi invalidado e a nova reserva recebeu outro UUID. Também passaram CAS de configuração/conjunto/histórico, inserção/ABA, prazo exclusivo, regressão de relógio, rollback intermediário, commit de resultado desconhecido e cleanup com contadores conhecidos. Registros 134/135.

**Runtime conferido em 26/10/2026:** API/receptor atualizados, rota de recovery respondeu 401 sem chave, 422 para override `force` e 200 `completed` com contagens zero. Tick/dispatch também concluíram sem candidatos. O grafo instalado/publicado corresponde ao export público inativo, e os quatro fluxos anteriores e suas duas versões publicadas foram preservados. O log técnico comprovou ciclo natural às 03:13:21 (UTC−03), com os três HTTPs e normalizadores concluídos. Como os payloads não são retidos, a prova natural confirma respostas válidas `completed` ou `skipped`, sem atribuir contagens ou recuperação de reservas a esse ciclo. A base real estava sem avisos; não foram criados casos fictícios nela nem enviados lembretes para este ensaio. Os ensaios usam bases isoladas e não podem remover a barreira contra repetição da base real. Resultados e limites estão consolidados no [índice de evidências](evidencias.md).


O instalador confere a correspondência exata de papel/tipo/versão/URL de cada nó, as conexões e os hashes do código público embutido. Recusa desvios que pulem recovery ou um normalizador, mudança de URL entre papéis e flags que desativem validadores ou continuem após erro. Os hashes são de código público, não de segredos; devem ser atualizados ao regenerar os validadores aprovados.
