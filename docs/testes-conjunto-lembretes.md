# Testes do conjunto de lembretes — etapa 12, parte 9

Esta parte verifica a sequência **recuperar → sincronizar/gerar → enviar**, incluindo passagem do tempo, mudanças externas, falhas e reinícios. Reúne as provas de domínio, persistência, adaptador de agenda, API e grafo n8n sem transformar fixtures em comprovação de envio real. As regras continuam nas [definições dos lembretes](regras-lembretes.md), no [canal](canal-lembretes.md), no [envio](envio-lembretes.md) e na [recuperação](recuperacao-lembretes.md).

**Parte 9 concluída em 26/10/2026: 1.956 testes Python e 981 verificações JavaScript aprovados**, incluindo 20 novos testes Python e 19 do grafo, sem skips na execução consolidada. Fontes, relatórios e runtime foram conferidos; registros 136/137. Nenhum defeito de produção foi identificado nos novos percursos, e as 109 fontes funcionais/configuração e cinco exports permaneceram iguais ao início desta parte.

## O que cada prova demonstra

| Nível | Componentes reais no ensaio | Componentes substituídos | Alcance da conclusão |
|---|---|---|---|
| Regras e persistência | Serviços Python, SQLite, transações, histórico e locks de SO | Relógio, eventos e transporte | Estados, limites de tempo, identidade, concorrência e rollback locais |
| Calendário integrado | Serviços, `GoogleReminderCalendarGateway`, construção de requisições pelo SDK e SQLite | Respostas/erros de calendário e sender | Compatibilidade entre representação do Google, reconciliação, planejamento e decisão de envio; não acesso à conta real |
| API por ASGI | Roteamento, autenticação, parsing, serviço, thread pool e SQLite | Entrada ASGI, relógio e provedores | Contrato HTTP da aplicação e cancelamento da espera; não conexão TCP nem restart de servidor |
| API por TCP e processos | Servidor local, cliente HTTP, processos distintos, SQLite e lock de SO | Agenda, remetente/destinatário de teste e sender | Persistência entre processos, timeout da espera, exclusão e retomada local; não tráfego com Google ou WhatsApp |
| Grafo n8n em harness | JSON distribuído, conexões e código dos nós de validação | Execução do motor n8n e respostas HTTP | Ordem e interrupção causal do grafo exportado, DTOs e propagação somente de agregados |
| Runtime instalado | API/receptor/n8n locais e grafo efetivamente publicado | Conforme o ensaio explicitamente registrado | Apenas fatos observados no preflight/log técnico; uma execução ociosa não demonstra transporte de lembrete |

Uma fixture que retorna um fingerprint permite testar a persistência de `accepted`. Isso representa aceite simulado, sem comprovar chamada ao provedor, entrega, leitura ou recebimento humano. Um log natural do n8n sem payload retido comprova conclusão dos nós/validadores, sem permitir atribuir contagens que não foram observadas.

## Matriz de regras e falhas

| Área | Cenário e resultado exigido | Provas locais existentes | Complemento desta parte |
|---|---|---|---|
| Configuração e horários | Padrões `preparation=60` e `imminent=15` minutos; `day_before` desligado. Intervalos personalizados válidos não mudam automaticamente | Store, geração e CLIs validam tipos, ordem, limites, tipos desligados e revisão | Ciclo completo com relógio avançando pelos dois horários e novo processo/instância sem duplicar o mesmo slot |
| Prazo do aviso | `due <= now < min(due + 120 s, start)`. Limite final exclusivo; atraso não cria aviso imediato ou rajada acumulada | Store/serviços conferem início, deadline, microssegundos e relógio atualizado depois das leituras | Integrar eventos criados/reagendados perto do prazo, passagem ao segundo slot e reinício após perder um aviso |
| UTC e fuso | Antecedência em minutos decorridos; apresentação no IANA efetivo da agenda. Mesmo instante UTC não cria outra identidade | Adaptador valida offsets, repetição de horário de verão, fuso, recorrência e intervalos | Percorrer adaptador e ciclo inteiro com representações/fusos relevantes, sem usar o relógio do Windows como regra |
| Mudança externa | Reagendar cancela o início antigo; título/fuso atuais entram no texto; retorno a início já aceito/incerto não reabre envio | Sincronização, dupla leitura do dispatch e CAS do store | Alterar a resposta do calendário entre ciclos e entre leituras; conferir ledger e número de chamadas do sender |
| Cancelamento e ausência | Cancelar apenas com prova exata. Ausência na listagem não demonstra exclusão; falha de consulta não vira ausência | GET exato, tombstone, 404/410 com confirmação de acesso à agenda e recusa de erros/parcialidade | Percorrer esse adaptador com os serviços, preservando avisos quando a consulta falha e impedindo transporte do cancelado |
| Canal | Sem observação válida, janela desconhecida. Texto só quando `message_at <= now < message_at + 24h - 60 s`; margem de 60 s é local | Ingresso/store validam origem declarada, timestamp original, vínculo, replay, mídia e status; dispatch aplica o gate final | Reabrir a mesma identidade bloqueada somente com nova entrada válida e antes do prazo; tempo perdido continua perdido |
| Identidade e concorrência | Uma identidade por agenda/ocorrência/início/slot/vínculo; request ID não é identidade de envio | SQLite, reserva atômica, CAS, dois threads/processos e lock compartilhado | Repetir a sequência HTTP completa e comprovar um único transporte, inclusive com chamadas concorrentes e espera perdida |
| Falha antes do transporte | Falha de leitura/factory/configuração não autoriza envio. Somente a reserva própria ainda anterior a `sending` pode ser liberada com segurança | Serviços de envio, store, falhas intermediárias e rollback | Interromper o processo no ponto controlado e confirmar que recover só libera o claim abandonado; o novo dispatch relê evento/canal |
| Envio potencial | Depois de `sending`, erro, resposta perdida ou persistência inconclusiva conserva `uncertain`/`sending`; não há retry cego | Sender fictício, ledger e API cobrem timeout, resposta inválida e falha ao gravar aceite | Timeout TCP/restart com contadores reais da fixture; `accepted`, `uncertain` e tentativas antigas não geram segunda chamada |
| Recuperação | Só posse positiva do lock permite reconciliar abandono; não há TTL. `reserved` seguro volta a `pending`; `sending` vira `uncertain` com a mesma tentativa | Snapshot limitado, configuração/conjunto/watermark, ABA, rollback, relógio e crash real com 44 registros protegidos | Integrar recovery às etapas seguintes e a processos do servidor, conferindo invalidação do claim antigo e proteção dos anteriores |
| Falha na fronteira do commit | Resultado desconhecido não pode fabricar contadores zero nem afirmar rollback. Resultado conhecido conserva suas contagens | API/serviço e percursos com commit seguido de exceção e cleanup posterior | Impedir que o grafo siga para tick/dispatch após HTTP 503, mesmo quando a resposta preserva contadores confirmados |
| Privacidade e publicação | Corpo `{}` e Bearer, sem override de destino/evento/força; saídas só com estados e agregados | APIs, normalizadores e instalador validam contrato, URLs, credenciais por referência e grafo | Executar o grafo distribuído e conferir preservação das fontes anteriores; nenhuma fixture pode alcançar a base/callback real |

Os testes anteriores continuam necessários: os novos percursos não substituem a cobertura de casos malformados, paginação, limites, configurações, corrupção do banco ou falhas de escrita. Os cenários integrados acrescentam uma relação causal entre camadas: uma mudança na entrada deve alterar a decisão, o histórico e as tentativas de transporte esperadas.

## Fontes de teste e reprodução

As suítes de persistência e domínio ficam em `tests/test_reminder_store.py`, `test_reminder_generation.py`, `test_reminder_sync.py`, `test_reminder_tick.py`, `test_reminder_delivery_store.py`, `test_reminder_delivery.py` e `test_reminder_recovery.py`. `test_reminder_calendar.py` e `test_reminder_calendar_sync.py` conferem o adaptador. As suítes `*_api.py`, `test_reminder_channel_ingress.py`, `test_reminder_delivery_paths.py` e `test_reminder_recovery_paths.py` verificam contratos e percursos já existentes. O instalador possui `test_setup_reminder_schedule.py`.

O novo conjunto de calendário integrado está em [`tests/test_reminders_system_calendar.py`](../tests/test_reminders_system_calendar.py), com o helper isolado [`reminder_system_fixture.py`](../tests/reminder_system_fixture.py). Os percursos TCP estão em [`tests/test_reminders_system_http.py`](../tests/test_reminders_system_http.py), usando o servidor temporário [`reminder_http_worker.py`](../tests/reminder_http_worker.py). O harness do export está em [`n8n/tests/lembretes-ciclo-completo.test.cjs`](../n8n/tests/lembretes-ciclo-completo.test.cjs). **Fontes finais conferidas:** 212 hashes correspondem à suíte aprovada, com oito logs preservados. Os 14 cenários do calendário atravessam gateway, SDK e transporte Google reais sobre HTTP em memória, com relógio controlado em todos os serviços. Os seis cenários TCP iniciaram 11 processos isolados e fizeram cinco reinícios; registraram 10 ciclos e 30 envelopes, com zero tentativas de acesso externo. Os 19 cenários do grafo passaram, incluindo a leitura desses envelopes reais de TCP, sem ignorar esse caso.

Na raiz do projeto, com o ambiente de testes preparado:

```powershell
$env:REMINDER_SYSTEM_REPORT_PATH = Join-Path (Get-Location) ("data/reminders-http-" + [guid]::NewGuid().ToString("N") + ".json")
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_reminders_system_calendar.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_reminders_system_http.py -v
node --test n8n/tests/lembretes-periodicos.test.cjs
node --test n8n/tests/lembretes-ciclo-completo.test.cjs
```

Os ensaios de TCP precisam permitir subprocessos e comunicação em loopback. Isso não exige rede externa, credenciais do projeto ou acesso aos serviços reais. Um teste impedido pelo ambiente não deve ser apresentado como aprovado nem substituído silenciosamente por uma chamada ASGI.

Defina `REMINDER_SYSTEM_REPORT_PATH` antes da suíte TCP e conserve a mesma variável ao chamar o harness. O caminho precisa apontar para um arquivo de relatório dentro da pasta de staging, pai do checkout de ensaio. Use um arquivo novo por execução: o writer acumula cenários quando o arquivo já existe, e um relatório antigo não comprova a execução atual. O documento `reminder-system-http-v1` reúne `scenarios[].cycles`, cada ciclo contendo os três envelopes efetivamente recebidos por TCP.

O harness usa esse relatório para conferir os envelopes por meio dos Code nodes e conexões do export. Sem ele, o caso correspondente fica explicitamente ignorado: os demais cenários continuam sendo respostas HTTP simuladas. O fechamento que afirmar integração TCP → grafo precisa registrar a execução com o relatório correto e o resultado desse caso, sem ocultar skips.

## Limites e evidências

O sandbox não oferece um template contextual de lembrete aprovado para envio fora da janela reconhecida. Os testes confirmam o bloqueio local, não uma solução para qualquer horário. Serviços parados ou computador suspenso impedem os pulsos; a retomada preserva histórico e classifica prazos, sem garantir entrega com o computador desligado.

Google e transporte não participam de uma transação distribuída com o SQLite. A última leitura e o gate final reduzem corridas, mas uma alteração externa imediatamente posterior continua possível. O teste deve demonstrar decisões com as respostas que efetivamente forneceu, sem alegar que eliminou essa corrida residual.

Não criar eventos fictícios na agenda real, mensagens sintéticas no callback real ou saídas reais só para fazer a matriz passar. As bases e configurações dos ensaios precisam ser isoladas; metadados e logs publicados não podem conter título, ID de evento, telefone, token, texto de mensagem ou payload bruto. A capacidade secreta do callback continua sem equivaler a assinatura criptográfica do provedor.

| Registro de fechamento | Situação desta parte |
|---|---|
| Total Python, novos testes e log/hash | **1.956 aprovados, 20 novos; sem skips; registro 136** |
| Total JavaScript, novos cenários e log/hash | **981 aprovados em sete suítes, 19 novos; sem skips; registro 136** |
| Fontes testadas e preservação dos exports anteriores | **212 hashes testados conferidos; 109 fontes de produção e cinco exports iguais ao baseline** |
| API/grafo instalado/publicado e ciclo natural | **API 200, receptor 404 na raiz, n8n 200; cinco grafos instalados e três publicados conferidos; ciclo natural às 03:32:20 (UTC−03), registro 137** |
| Consulta/mutação Google e envio/entrega reais de lembrete | **Não demonstrados pelos ensaios com fixtures** |

Os registros estão vinculados ao [índice de evidências](evidencias.md). Suíte local aprovada, resposta de API, disparo natural e confirmação humana são provas distintas e devem continuar identificadas separadamente.


A conferência do runtime foi somente de leitura: não houve importação/publicação/despublicação de fluxos, restart, mensagem sintética no callback real ou POST autenticado de lembretes. As três rotas recusaram requisições sem chave com 401. Um ciclo natural após o início desta parte concluiu os três HTTPs e normalizadores, em 26/10/2026 às 03:32:20 (UTC−03). Sem payloads salvos, o log confirma DTOs válidos `completed` ou `skipped`, sem fornecer contagens ou provar envio/entrega. O código de produção não precisou ser alterado para os novos cenários passarem.
