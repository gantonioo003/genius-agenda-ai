# Fluxos n8n

Cinco exports públicos para **n8n 2.44.4**. Na parte 8, o workflow de lembretes passa a executar **recover → tick → dispatch**: recuperação local, sincronização/geração e processamento dos avisos devidos. A validação anterior, da parte 7, comparou o grafo de seis nós instalado/publicado e preservou os quatro fluxos anteriores; está no [manifesto dos lembretes](reminders-schedule-manifest.json). Em 26/10/2026, a parte 8 conferiu o novo grafo de oito nós instalado/publicado e um disparo natural. Os demais fluxos foram preservados, conforme o manifesto atualizado. O [manifesto da etapa 09](exports-manifest.json), o [do sandbox](sandbox-export-manifest.json) e o [fechamento do WhatsApp](whatsapp-closure-manifest.json) conservam seus escopos históricos. Todos os exports são distribuídos inativos, sem dados fixados ou segredos.

**Etapa 12 encerrada no escopo de teste (26/10/2026):** cinco exports e os resultados de 1.956 Python/981 JavaScript preservados na auditoria final. O ciclo de lembretes tem oito nós e segue recover → tick → dispatch; o recebimento de um lembrete real ainda não foi validado. [Fechamento e reprodução](../docs/fechamento-etapa-12.md).

**Validação do conjunto — parte 9:** 1.956 testes Python e 981 verificações JavaScript aprovados; cinco exports preservados e runtime conferido por leitura. [Matriz de testes](../docs/testes-conjunto-lembretes.md) · [Manifesto dos testes](reminders-system-tests-manifest.json).

| Arquivo | Uso | Conexão |
|---|---|---|
| [Consulta de eventos](workflows/consulta-eventos.json) | Fluxo normal de consulta por webhook autenticado | GET na API Python `8000`, que acessa o Google real |
| [Integração isolada](workflows/integracao-isolada-teste.json) | Fixture de ensaio CRUD e falhas | API isolada `8001`, com HTTP Google simulado |
| [Disparo agendado](workflows/disparo-agendado-teste.json) | Prova de Schedule Trigger; até duas tentativas | GET de status autenticado na API normal `8000` |
| [WhatsApp Sandbox](workflows/whatsapp-sandbox.json) | Receber metadados e ativar o agente pela fila privada | POST autenticado `/api/v1/whatsapp/sandbox/agent` na API `8000`; worker Python controla contexto, serviços e resposta |
| [Lembretes periódicos](workflows/lembretes-periodicos.json) | Recuperar estado local, sincronizar/gerar e processar avisos devidos a cada minuto | POSTs autenticados `/api/v1/reminders/recover`, `/tick` e `/dispatch` na API `8000`, todos sob `/api/v1/reminders`, com `{}` |

## Configuração

O setup [scripts/setup_n8n.py](../scripts/setup_n8n.py) configura **somente a consulta normal**, com credenciais locais. O runner [scripts/test_n8n_integration.py](../scripts/test_n8n_integration.py) configura as duas fixtures com IDs separados, publica temporariamente, verifica o ensaio e despublica ao terminar. Ambos reiniciam brevemente o n8n. Não passe fixtures, sandbox ou lembretes a `setup_n8n.py --workflow`, pois esse setup utiliza o ID do fluxo normal.

```powershell
.\.venv\Scripts\python.exe scripts/setup_n8n.py --apply
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23
```

O [setup específico do sandbox](../scripts/setup_whatsapp_sandbox.py) preserva a configuração anterior, reutiliza a referência da credencial `Genius API - local` e cria somente sua nova credencial de entrada e seu workflow. Seus IDs e tokens ficam em `secrets/whatsapp-sandbox-local.json`, separado de `secrets/n8n-local.json`.

```powershell
.\.venv\Scripts\python.exe scripts/setup_whatsapp_sandbox.py --apply
```

Esse comando publica a ponte local e reinicia brevemente o n8n. Receptor, túnel HTTPS e registro no provedor são passos separados no [guia do WhatsApp Sandbox](../docs/whatsapp-sandbox.md).

O [setup específico dos lembretes](../scripts/setup_reminder_schedule.py) importa/publica somente esse workflow e reinicia brevemente o n8n. Reutiliza seu ID e a credencial `Genius API - local`, sem importar ou substituir credenciais. Guarda apenas versão, ID do workflow e referência da credencial em `secrets/reminders-n8n-local.json`; preserva os arquivos privados anteriores. A parte 8 acrescenta a [recuperação local](../docs/recuperacao-lembretes.md) antes das rotinas existentes. Consulte também [operação e limites do envio](../docs/envio-lembretes.md). O [guia da parte 6](../docs/lembretes-n8n.md) conserva a implementação anterior, restrita à preparação local.

```powershell
.\.venv\Scripts\python.exe -B scripts/setup_reminder_schedule.py --apply
```

Nas validações das partes 6 e 7, consulta, sandbox e lembretes estavam publicados na instância; as duas fixtures foram preservadas despublicadas. Na parte 8, os mesmos três fluxos continuam publicados, com as duas fixtures despublicadas. Os exports distribuídos continuam com `active: false`.

Na importação manual, associe as credenciais privadas pelos nomes referenciados. O JSON público sozinho não fornece acesso à API ou ao webhook. Para pré-requisitos e reprodução, consulte [operação local](../docs/n8n-local.md), [primeiro fluxo](../docs/n8n-primeiro-fluxo.md) e [ensaio integrado](../docs/n8n-testes-integracao.md).

## Exports e privacidade

O fechamento da etapa 09 exportou os três workflows instalados pelo CLI, removeu IDs locais de workflow/credenciais e campos de conta/execução, definiu `active: false` e preservou somente nome, blocos, conexões, configurações e listas vazias de dados fixados/tags. As credenciais contêm somente nomes. A estrutura corresponde aos arquivos testados; seus bytes foram preservados. O novo export do sandbox também foi comparado ao instalado, removendo apenas IDs privados para a conferência. Os manifestos não incluem valores ou hashes de segredos.

Os exports não contêm `staticData`, resultados, histórico de execução ou eventos. IDs internos dos blocos e do webhook pertencem ao grafo, não concedem acesso. Um export de workflow não é backup completo do volume privado do n8n; a configuração privada e as chaves devem ser preservadas separadamente. Não publique exports de credenciais ou dumps do volume.

O sandbox recebe somente origem, UUID próprio, contagens e tipos limitados; texto, telefone e resposta ficam fora do workflow. A versão atual chama `POST /api/v1/whatsapp/sandbox/agent` para ativar a fila privada. Somente status recebe `skipped`; mensagens novas recebem `queued`, sem afirmar entrega ou execução. A resposta fixa da etapa 10 permanece documentada abaixo como histórico. [Fluxo atual e configuração alinhada de API/receptor](../docs/whatsapp-agente.md).

Depois de alterar a biblioteca de respostas, use `node n8n/build-workflow.cjs` e os testes do fluxo para regenerar/conferir `consulta-eventos.json` antes de reaplicar. Ao modificar o sandbox ou os lembretes, mantenha o código embutido no respectivo export consistente com `n8n/lib/whatsapp-sandbox.cjs` ou `n8n/lib/reminder-tick.cjs`; os testes verificam essa correspondência. Cada manifesto precisa ser atualizado quando seu export mudar.

## Verificações registradas

```powershell
node n8n/tests/present-api-response.test.cjs
node n8n/tests/consulta-eventos.test.cjs
node n8n/tests/integracao-isolada.test.cjs
node n8n/tests/disparo-agendado.test.cjs
node --test n8n/tests/whatsapp-sandbox.test.cjs
node --test n8n/tests/lembretes-periodicos.test.cjs
node --test n8n/tests/lembretes-ciclo-completo.test.cjs
```

A verificação histórica da etapa 12, parte 6, aprovou **719 cenários JavaScript** nas seis primeiras suítes acima, incluindo **179 novos** dos lembretes, e **1.778 testes Python**, dos quais **35 novos**. A parte 7 executou novamente as seis suítes: **853 cenários JavaScript (+134) e 1.874 testes Python (+96)** aprovados. Na parte 8 passaram **1.936 testes Python (+62) e 962 verificações JavaScript (+109)**, com os arquivos finais congelados após o reforço do instalador. Os números abaixo registram marcos anteriores e mantêm suas datas e limites.

O fechamento da etapa 09 registrou **427 verificações JavaScript** nas quatro suítes anteriores: 215, 82, 63 e 67. O ensaio HTTP e o disparo natural têm prova separada no [fechamento da etapa 09](../docs/fechamento-etapa-09.md); as suítes não foram repetidas naquele fechamento documental.

Na conexão do sandbox, passaram **470 verificações JavaScript** — as 427 anteriores mais 43 novas — e **478 testes Python**. O recebimento real da mensagem do usuário e a consulta autenticada ao status da API têm prova própria no registro `080_whatsapp_sandbox_integracao_real.json`; o canvas publicado está no registro `081_n8n_sandbox_recebimento_publicado.png`. A evidência real não comprova resposta de WhatsApp ou operações de agenda. Consulte [resultado, limites e reprodução](../docs/whatsapp-sandbox.md).

A segurança da entrada exige a política privada de um único identificador de remetente antes de iniciar o receptor. A representação exata enviada pelo provedor foi conferida e uma nova mensagem real passou com HTTP 200 após o ajuste. Os registros 082/083 distinguem 20 verificações HTTP iniciais, uma de recuperação e cinco após fixar a identidade. A suíte de 499 testes Python e os quatro exports foram preservados nesta retomada; as 470 verificações JavaScript permanecem como resultado do marco anterior. Consulte o [guia atualizado](../docs/whatsapp-sandbox.md). Esse marco histórico concluiu a parte 6; a parte 7 está registrada a seguir.

A parte 7 acrescentou a [inbox SQLite no receptor](../docs/whatsapp-deduplicacao.md), com reserva antes do n8n. Eventos concluídos repetidos não chegam ao fluxo; resultados pendentes/incertos bloqueiam reenvio automático. Passaram **551 testes Python** e **22 cenários HTTP loopback/23 requisições**, com dados fictícios, serviços locais reais, concorrência e reinício real. Nove identidades novas produziram nove encaminhamentos/consultas de status e nove recibos; repetições não aumentaram esses totais. Registros 084/085. Os quatro exports, manifestos e JavaScript foram preservados; as 470 verificações JavaScript continuam históricas, sem nova execução. A prova nova não inclui retransmissão do provedor ou teste positivo pelo HTTPS público. Esse foi o marco da parte 7 em 23/10/2026; a implementação posterior da parte 8 está abaixo.

## Parte 8 — resposta fixa (24/10/2026)

O HTTP chama somente `POST http://host.docker.internal:8000/api/v1/whatsapp/sandbox/reply`, com a credencial Bearer existente, `X-Request-ID` e corpo contendo apenas o mesmo UUID. A API autoriza a execução pela inbox persistida em `processing`; `should_reply` calculado no fluxo serve apenas para conferir o retorno esperado. Um claim com mensagens novas pode gerar uma resposta fixa; somente status recebe `skipped`.

O normalizador exige o envelope v1 de nove campos, ação `whatsapp_test_reply`, correlação exata e `data` limitada a `authenticated`, `reply_state` e `reused` booleano. `accepted` exige `mutation_state: confirmed`; `skipped`, `not_applicable`. O sucesso devolve quatro campos: `status`, `api_reachable`, `request_id`, `reply_state`. Aceite não comprova entrega ou leitura. Falha ou retorno incompatível gera 503 seguro; nenhuma resposta bruta do provedor é propagada.

A outbox mantém `sending`/`accepted`/`uncertain` em SQLite separado. Reserva antes do envio, reutiliza aceite enquanto o claim está ativo e bloqueia resultado incerto sem retry/expiração automática. O HTTP do n8n tem timeout de 15.000 ms, sem retentativas ou redirecionamentos; o workflow tem limite de 30 s. O salvamento de execuções de sucesso, erro, manuais e progresso permanece desativado. Esses limites não estabelecem prazo global de entrega.

Passaram **613 testes Python** e **502 verificações JavaScript** nas cinco suítes executadas: 215 +82 +63 +67 +75 do sandbox. Os três exports anteriores foram preservados; o sandbox é a atualização desta parte e seu manifesto acompanha a comparação após a aplicação. O export público continua inativo, sem IDs de credenciais ou segredos. Registros 086/087: testes locais e resultado do ensaio de saída real com entrada sintética local, documentados separadamente no [guia de respostas](../docs/whatsapp-respostas.md). Não foi exigido novo callback real de entrada para esse ensaio. Naquele marco, a etapa 10 permanecia em andamento; o fechamento atual está registrado abaixo.

O ensaio aprovou **nove verificações HTTP**: um POST sintético loopback alcançou n8n/API e resultou em **um aceite real do provedor**, com outbox `accepted` e inbox `processed`. Repetir o mesmo ID ou alterar somente o texto não gerou outro envio; status isolado não reservou saída. Não foi uma nova entrada real de WhatsApp/HTTPS nem comprovação de entrega/leitura no aparelho. Os demais casos conferiram autenticação, claim desconhecido/concluído e rejeição de campo extra.

## Parte 9 — mídia/template em ensaio separado (24/10/2026)

O [ensaio CLI de mídias/templates](../docs/whatsapp-midias-templates.md) obteve dois aceites reais: uma imagem por URL pública e o template `disclaimer/en`. Cada caso fez uma chamada ao provedor; repetir a mesma operação retornou `reused: true` sem novo envio. O ledger próprio `data/whatsapp-media-probes.sqlite3`, schema 1, vincula UUID, caso e hash da definição/destinatário, mantendo o telefone somente em memória no cálculo.

Nenhum bloco, biblioteca, teste JavaScript, export ou manifesto n8n foi alterado nesta parte. A API, o receptor e os códigos de inbox/outbox também foram preservados. O workflow ativo continua solicitando somente a resposta fixa; não há nova rota ou disparo de imagem/template na conversa. A CLI faz prévia sem rede por padrão e exige `--execute` para enviar ao WhatsApp já autorizado.

Passaram **664 testes Python**: 613 anteriores +20 do sender +18 do ledger +13 de serviço/CLI. As **502 verificações JavaScript** são históricas da parte 8 e **não foram executadas novamente**. Os registros 088/089/090 separam testes, imagem pública e dois aceites reais; aceite não comprova entrega/leitura e o usuário confirmou o recebimento dos dois itens, separadamente do aceite técnico. Limites e fontes estão no guia. Esse foi o marco da parte 9; o fechamento da etapa 10 está registrado abaixo.

## Fechamento da etapa 10 — 24/10/2026

Os quatro fluxos instalados foram exportados pelo CLI e comparados aos arquivos públicos; as versões publicadas de consulta e sandbox também foram conferidas com `--published`. Os dois fluxos de ensaio continuam despublicados. Os exports públicos permanecem inativos e seus bytes não mudaram. O [manifesto final](whatsapp-closure-manifest.json) registra a verificação atual; os dois manifestos anteriores preservam suas datas e escopos históricos. Nenhuma credencial foi exportada.

Quinze verificações HTTP atuais conferiram saúde, autenticação, rejeições e replays de eventos concluídos, sem novo envio WhatsApp ou operação Google. A suíte de 664 testes Python e as 502 verificações JavaScript anteriores continuam vinculadas ao código preservado; não foram repetidas neste fechamento documental. O [registro da etapa 10](../docs/fechamento-etapa-10.md) consolida reprodução, falhas e evidências 091/092. A integração de teste pela 360dialog está concluída; IA, escritas de agenda pelo chat e lembretes permanecem futuros.


## Etapa 11, parte 10 — agente e fila privada (25/10/2026)

O export `whatsapp-sandbox.json` passou da rota `/reply` para `/agent`, usando as mesmas credenciais privadas nomeadas e somente `{request_id}`. O normalizador exige `whatsapp_agent_turn` e `queue_state: queued|skipped`, com correlação exata. O callback espera somente a ativação durável, sem esperar LLM, agenda ou saída. Erros viram 503 seguro; nenhum corpo bruto é devolvido. Não há retries de escrita/envio.

Passaram 540 cenários JavaScript (113 sandbox +82 consulta +67 agendado +63 integração +215 apresentação), 38 novos, e 1.508 testes Python. A prova com n8n publicado aprovou 42 verificações e 14 chamadas ao webhook; modelo, Google e saída WhatsApp foram fixtures. O ensaio HTTP separado aprovou 29 verificações com uma ponte n8n fixture. Esses testes não comprovam mutações na agenda real.

Os quatro exports foram novamente conferidos pelo CLI, com as duas versões publicadas também verificadas; os manifestos acompanham os hashes atuais. Os três fluxos anteriores permaneceram iguais. Evidências 110 a 114; [contrato, privacidade, reinício e limites](../docs/whatsapp-agente.md).

A validação real posterior recebeu uma consulta diária pelo sandbox, persistiu o período no contexto e obteve um aceite de saída. O usuário confirmou o recebimento da resposta da agenda; nenhum compromisso foi alterado. Essa prova separada está no registro 114.

## Etapa 12, parte 6 — verificação periódica de lembretes (25/10/2026)

Registro histórico da preparação local, antes da conexão do transporte na parte 7 descrita a seguir.

O novo fluxo tem somente Schedule Trigger de um minuto, preparação da correlação, HTTP e normalizador estrito. Chama `POST http://host.docker.internal:8000/api/v1/reminders/tick` com `{}`, Bearer existente, `Content-Type: application/json` e `X-Request-ID` derivado da execução. A API sincroniza os avisos acompanhados antes de gerar novos planos; um lock de SO compartilhado entre ticks da mesma base impede sobreposição. Não há TTL que libere trabalho ainda em andamento, retry automático, webhook de entrada ou timer Python.

Cada fase tem seu próprio commit local. HTTP 503 com `partial` preserva a sincronização concluída; se o encerramento dos recursos falhar após a geração, também preserva `generation: planned`. `local_mutation_state: applied` confirma fases concluídas, inclusive com zero linhas novas. O workflow valida o envelope v1 e devolve somente agregados seguros; falha HTTP, transporte ou DTO incompatível encerra a execução com erro fixo. Timeout HTTP de 120 s, limite do workflow de 150 s e salvamento de sucesso, erro, execução manual e progresso desativado. [Contrato, concorrência e operação](../docs/lembretes-n8n.md).

O preflight autenticado da API retornou `completed` com contagens zero, sem envio ou mutação externa. Separadamente, o log técnico do n8n comprovou disparo natural em **25/10/2026 às 20:44:20.699 -03:00**, modo `trigger`, com HTTP e normalizador concluídos. Os valores da saída natural não foram retidos: essa prova confirma um DTO válido `completed` ou `skipped`, sem afirmar novas linhas, contagens ou entrega de lembretes.

Passaram **1.778 testes Python e 719 JavaScript**. As quatro versões instaladas anteriores e as duas versões publicadas anteriores foram preservadas; o novo grafo instalado/publicado corresponde ao export público inativo, conforme o [manifesto](reminders-schedule-manifest.json). Esta parte não chama LLM, não escreve eventos Google e não envia WhatsApp. O transporte com revalidação do evento, relógio e canal permanece para a parte 7.

## Etapa 12, parte 7 — envio dos lembretes (26/10/2026)

Registro histórico do grafo de seis nós, anterior à recuperação local acrescentada na parte 8.

O workflow passa de quatro para **seis nós**: Schedule Trigger, preparação da correlação, HTTP tick, normalização do tick, HTTP dispatch e normalização do dispatch. Mantém o intervalo de um minuto e o mesmo `X-Request-ID` nas duas chamadas. Somente HTTP 200 `completed` ou `skipped` validado no tick permite chamar `POST /api/v1/reminders/dispatch`; falha ou resultado parcial do tick interrompe o fluxo. Ambos os POSTs usam `{}`, Bearer existente e timeout de 120 s, sem retries ou redirects. O limite total do workflow passa de 150 para **300 s**; o salvamento de execuções permanece desativado.

A API compartilha o lock de SO com o tick, reserva a identidade, relê o evento exato e confere atomicamente perfil, relógio e janela do canal antes de marcar `sending`. Há uma única chamada ao sender por tentativa iniciada. `accepted` exige aceite persistido do provedor; falha após o início do transporte conserva `uncertain` ou `sending`, sem liberação por TTL, retomada de reserva antiga ou retry cego. Bloqueio de janela desconhecida, fechada ou na margem de 60 s impede transporte; uma nova observação válida só permite reconsiderar avisos ainda no prazo. [Arquitetura, operação e recuperação](../docs/envio-lembretes.md).

As respostas do dispatch contêm somente estados e contadores agregados; a normalização distingue aceite de resultado incerto e não apresenta entrega/leitura como confirmadas. Título, telefone, IDs e texto do lembrete ficam fora do n8n. A formatação é determinística, sem LLM; o envio não escreve eventos Google.

**Verificação concluída:** 1.874 testes Python e 853 JavaScript aprovados; novo grafo instalado/publicado corresponde ao export inativo. As versões dos outros quatro fluxos e as duas versões anteriormente publicadas foram preservadas. Preflight real concluiu tick/dispatch sem candidatos; o disparo natural de 26/10/2026, 02:39:20 (UTC−03), terminou os dois HTTPs e normalizadores. Como os payloads não são retidos, o log comprova somente respostas válidas `completed` ou `skipped`, sem contagens ou entrega atribuídas ao disparo. Aceite e incerteza de lembretes foram exercitados com transporte fictício; nenhum lembrete real ou confirmação humana nova integra esta prova. Registros 132/133.

## Etapa 12, parte 8 — recuperação após interrupção (26/10/2026)

O workflow passa a **oito nós**, com HTTP/normalização de `POST /api/v1/reminders/recover` antes de tick e dispatch. Mantém Schedule Trigger de um minuto, corpo `{}`, Bearer existente e correlação; cada HTTP tem timeout de 120 s e o workflow passa a **420 s**. Não há retries HTTP, redirects, webhook adicional ou timer Python. Falha ou retorno incompatível interrompe a sequência; salvamento de sucesso, erro, execução manual e progresso permanece desativado.

A recuperação só altera o ledger depois de adquirir o lock de SO compartilhado, comprovando ausência de proprietário participante ainda ativo. Reserva anterior ao transporte e sem histórico potencial volta a `pending`, ou vence se seu prazo terminou; `sending` órfão passa a `uncertain` com a mesma tentativa. `accepted` e `uncertain` permanecem protegidos. Schema 1, histórico e CAS são preservados, sem TTL, `force`, reset ou reenvio de resultado incerto. Recovery não consulta Google ou provedor e não chama LLM; uma reserva liberada ainda exige todas as validações do dispatch antes de enviar. [Contrato, operação e limites](../docs/recuperacao-lembretes.md).

**Verificação concluída:** 1.936 testes Python e 962 JavaScript aprovados. O grafo instalado/publicado corresponde ao export público inativo e mantém os demais quatro fluxos. A API real concluiu recovery/tick/dispatch com contagens zero, rejeitando falta de chave e override `force`. O ciclo natural de 26/10/2026, às 03:13:21 (UTC−03), concluiu os três HTTPs e normalizadores; não há payload salvo para atribuir contagens ou transições ao disparo. A base real não tinha avisos a recuperar. O instalador também recusa troca de URLs entre papéis, mudanças de tipos/versões/código/flags e desvios que pulem normalizadores. Registros 134/135; a recuperação de casos interrompidos foi exercitada em SQLite isolado e com processo real, sem mensagem ou evento Google real.


## Etapa 12, parte 9 — testes do conjunto (26/10/2026)

Passaram **1.956 testes Python (+20)** e **981 verificações JavaScript (+19)**. As sete suítes JavaScript são 215 apresentação, 82 consulta, 63 integração, 67 disparo de teste, 113 sandbox, 422 lembretes e 19 de ciclo completo. A suíte nova segue as conexões do export e executa seus Code nodes numa VM, interrompendo a sequência em erros de recovery/tick/dispatch sem repetir HTTP. Esse harness simula a fronteira HTTP e não substitui o motor n8n.

O relatório atual do ensaio TCP forneceu **30 envelopes reais de dez ciclos**, produzidos por seis cenários com processos/SQLite e provedores fictícios. Esses envelopes também passaram pelos validadores/conexões exportados; o caso foi executado sem skip, com `REMINDER_SYSTEM_REPORT_PATH` apontando para o relatório gerado na mesma execução. Consulte [reprodução e limites](../docs/testes-conjunto-lembretes.md).

Os cinco grafos instalados e as três versões publicadas foram conferidos sem alterar suas configurações. O ciclo natural de 03:32:20 (UTC−03), em 26/10/2026, terminou os três HTTPs e normalizadores; os payloads não foram retidos, portanto não se atribuem contagens ou entrega a esse log. Não houve importação, republicação, restart ou envio real de teste nesta parte. Código funcional, bibliotecas e exports permanecem iguais. [Manifesto de validação](reminders-system-tests-manifest.json), registros 136/137.


## Fechamento da etapa 12

A parte 10 revisou documentação, inventário de exports e evidências sem alterar código, grafo, credenciais ou serviços. Os manifestos de [agendamento](reminders-schedule-manifest.json) e [testes do conjunto](reminders-system-tests-manifest.json) permanecem registros das respectivas execuções, preservados por hash. Não houve nova importação, publicação, chamada de dispatch ou envio real neste fechamento. A auditoria 138 e o planner 139 consolidam o marco; [pendências e critérios de validação real](../docs/fechamento-etapa-12.md). Próxima entrega: resumos e preparo (13).
