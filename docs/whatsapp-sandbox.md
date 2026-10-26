# Recebimento real pelo WhatsApp Sandbox

**Recebimento inicial validado em 23/10/2026:** uma mensagem enviada pelo usuário ao sandbox da 360dialog percorreu HTTPS → receptor local → n8n → API Python autenticada. A API respondeu ao `GET /api/v1/status`, e a entrada confirmou HTTP 200. Naquele marco, o fluxo recebia e registrava metadados, sem resposta automática ou ação de agenda. A parte 8 acrescentou posteriormente a resposta fixa descrita abaixo.

**Etapa 10 concluída em 24/10/2026 no escopo da integração de teste pela 360dialog Sandbox.** A revisão da parte 10 encerra este percurso sem novos envios; checklist, reprodução e resultados finais estão no [fechamento da etapa 10](fechamento-etapa-10.md). O erro original de emissão Meta continua pendente e está preservado no [histórico](whatsapp-teste.md). Conversa com IA, operações de agenda pelo WhatsApp e demais funcionalidades do produto seguem em etapas futuras.

**Parte 6 — implementação da segurança da entrada concluída:** o receptor exige um único identificador de telefone autorizado por igualdade exata. A comparação de uma mensagem real confirmou a representação usada neste ambiente; somente então a política privada foi vinculada explicitamente a ela. Passaram 26 verificações HTTP sintéticas, incluindo cinco após o vínculo final. A suíte de 499 testes Python e as 470 verificações JavaScript são resultados anteriores preservados; não foram repetidas na retomada, que alterou somente configuração e processos.

**Parte 6 validada com mensagem real após o vínculo final:** o novo envio do usuário atravessou o filtro exato, o n8n e o status autenticado da API às 20:09:30.176124 UTC, com HTTP 200. O recebimento inicial anterior ao filtro e os bloqueios reais intermediários continuam separados no histórico. Esse resultado permanece como prova histórica da proteção de entrada. A parte 7 de deduplicação foi concluída posteriormente, conforme o marco abaixo.

**Parte 7 — deduplicação concluída:** a inbox SQLite separada registra as identidades antes do n8n. Eventos já concluídos recebem 200 sem novo encaminhamento ou recibo JSONL; eventos pendentes/incertos recebem 503 e ficam bloqueados para repetição automática. Passaram 551 testes Python e 22 cenários HTTP loopback em 23 requisições, com n8n/API reais e reinício real do receptor. Essa nova prova usa dados/IDs fictícios e não representa retransmissão do provedor ou teste pelo HTTPS público. Esse marco antecedeu a implementação da parte 8 abaixo. Veja [estados, recuperação e evidências da deduplicação](whatsapp-deduplicacao.md).

**Parte 8 implementada em 24/10/2026:** o n8n passou do GET de status para um POST autenticado que solicita uma resposta fixa. A API consulta o claim real em `processing`, reserva a saída e envia somente ao WhatsApp autorizado; lotes somente de status não disparam mensagem. Passaram **613 testes Python e 502 verificações JavaScript**, com as cinco suítes JS executadas nesta entrega. O resultado do ensaio real está no [guia de respostas](whatsapp-respostas.md#resultado-do-ensaio-real), separado dos testes simulados e do recebimento real histórico. `accepted` significa aceite do provedor, não entrega ou leitura.

**Parte 9 concluída em 24/10/2026 — imagem e template de teste:** um ensaio CLI separado obteve dois aceites reais da 360dialog: imagem por link público e template `disclaimer/en`. Cada caso fez uma chamada ao provedor; repetir sua operação retornou `reused: true` sem outro envio. Passaram **664 testes Python**. As **502 verificações JavaScript** são o resultado histórico da parte 8, sem nova execução aqui; arquivos n8n e manifestos permaneceram idênticos. Aceite não comprova entrega/leitura no aparelho; o usuário confirmou o recebimento dos dois itens, separadamente do aceite técnico. O [guia de mídias e templates](whatsapp-midias-templates.md) reúne limites, comandos e resultados. A parte 10 de revisão foi concluída posteriormente no escopo de teste.

## Percurso implementado

```text
WhatsApp do usuário → 360dialog Sandbox → HTTPS temporário Cloudflare
  → receptor 127.0.0.1:8787 → webhook autenticado do n8n em 127.0.0.1:5678
  → POST autenticado da API Python em 8000 → claim da inbox
  → outbox → envio fixo pela 360dialog, ou skip de status → confirmação HTTP
```

| Componente | Responsabilidade |
|---|---|
| [`app/sandbox_webhook.py`](../app/sandbox_webhook.py) | Receber somente `POST` no caminho secreto, validar o envelope e descartar conteúdo/identificadores pessoais antes de encaminhar |
| [`app/whatsapp_inbox.py`](../app/whatsapp_inbox.py) | Reservar e persistir identidades de eventos em SQLite; impedir novo encaminhamento de repetições concluídas ou resultados incertos |
| [`whatsapp-sandbox.json`](../n8n/workflows/whatsapp-sandbox.json) | Validar os metadados recebidos, consultar a API e produzir uma confirmação mínima |
| `POST /api/v1/whatsapp/sandbox/reply` | Conferir o claim real e solicitar uma resposta fixa ou skip de status; não chama o Google Calendar |
| [`app/whatsapp_outbox.py`](../app/whatsapp_outbox.py) | Reservar um único envio por claim e conservar aceite/incerteza sem retentativa automática |
| [`register_sandbox_webhook.py`](../scripts/register_sandbox_webhook.py) | Registrar o callback HTTPS no provedor, sem imprimir a URL completa ou a chave |

O receptor exige que `messages[].from` e `statuses[].recipient_id` correspondam exatamente ao telefone autorizado. Identificadores adicionais em `from_user_id` ou `recipient_user_id` podem estar presentes com formato limitado, mas não substituem o telefone: somente BSUID recebe 403, e um BSUID presente não libera um telefone diferente. Todos os eventos do lote são validados e autorizados antes de derivar hashes ou reservar registros na inbox; um remetente não autorizado rejeita o lote inteiro. IDs originais não são armazenados ou repassados ao n8n. A inbox persiste hashes para reconhecer repetições; esses hashes são pseudônimos e permanecem privados.

## Configuração privada

| Arquivo | Conteúdo e uso |
|---|---|
| `.env` | `D360_API_KEY`, recebida pelo usuário; `Settings` a trata como segredo. A chave é usada no cadastro do callback e no envio fixo pela API Python autenticada |
| `secrets/n8n-local.json` | Configuração anterior, preservada; fornece apenas a referência da credencial existente `Genius API - local` ao instalador |
| `secrets/whatsapp-sandbox-local.json` | Quatro campos próprios: `workflow_id`, `webhook_credential_id`, `webhook_token` e `callback_token`; preservados nesta atualização |
| `secrets/whatsapp-sandbox-security.json` | Política obrigatória e separada, com uma única chave `allowed_phone`; guarda exatamente o identificador de telefone do WhatsApp autorizado |
| `secrets/whatsapp-sandbox-security.before-identity-binding.json` | Backup privado da política anterior ao vínculo explícito realizado neste ambiente; não é carregado pelo receptor |
| `secrets/whatsapp-sandbox-registration.json` | Origem, callback completo e confirmação do registro no provedor; privado porque contém o caminho secreto |
| `secrets/whatsapp-sandbox-runtime.json` | Acompanhamento dos processos locais iniciados em segundo plano nesta sessão |
| `data/whatsapp-inbox.sqlite3` | Inbox própria de deduplicação, schema 1, com hashes, estados, UUID e horários UTC; separada do banco da agenda, que permanece no schema 2 |
| `data/whatsapp-outbox.sqlite3` | Outbox própria, schema 1: reserva/aceite/incerteza por UUID e fingerprint do ID de saída; não guarda conteúdo ou destinatário |
| `data/whatsapp-media-probes.sqlite3` | Ledger separado dos ensaios de imagem/template, schema 1; vincula UUID, caso e hash da definição/destinatário, sem guardar telefone ou payload original |
| `data/tools/cloudflared.exe` | Binário local do túnel; origem e digest documentados em `data/tools/cloudflared-release.json` |

`.env`, `secrets/` e `data/` são ignorados pelo Git. A chave da 360dialog não entra no export nem nas credenciais desse fluxo n8n. O callback completo também não deve aparecer em terminal compartilhado, prints ou documentação pública.

## Instalar e executar

Use a pasta raiz do projeto, o ambiente Python já preparado e o Docker Desktop em execução. A integração local anterior deve estar configurada, com a credencial `Genius API - local` disponível. Para a instalação inicial do projeto, consulte o [README](../README.md) e a [operação local do n8n](n8n-local.md).

1. Salve a chave recebida somente no campo `D360_API_KEY` do `.env` privado. Não a coloque nos argumentos dos comandos.
2. Suba o n8n e instale sua ponte de recebimento:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe scripts/setup_whatsapp_sandbox.py --apply
```

O instalador reserva IDs separados, importa a nova credencial de entrada e publica somente seu workflow. Ao repetir o comando, reutiliza a configuração própria. Não modifica `secrets/n8n-local.json`, não substitui o fluxo de consultas e não importa novamente a credencial Bearer da API. O n8n reinicia brevemente, preservando o volume. Não use `setup_n8n.py --workflow` para instalar este fluxo.

3. Configure o único WhatsApp autorizado em um terminal com entrada oculta:

```powershell
.\.venv\Scripts\python.exe scripts/configure_sandbox_sender.py --apply
```

Informe a representação **exata do identificador de telefone que o provedor usa para esse mesmo WhatsApp**, com país e DDD; ela pode diferir da forma usada para discar. O comando recebe o valor por `getpass`, sem argumentos de telefone ou eco, e remove somente a apresentação (`+`, espaços, parênteses e hífens). O resultado precisa ter de 7 a 15 dígitos ASCII, começando de 1 a 9. O comando não acrescenta/remove o nono dígito nem cadastra aliases.

Se o terminal não permitir entrada oculta, o comando encerra antes de gravar a política. Repetir o mesmo identificador preserva o arquivo; informar outro ou encontrar política inválida interrompe a operação, sem substituição automática. Não existe aprendizado do remetente a partir de callbacks. Se a identidade precisa ser ajustada, primeiro confirme a relação com o usuário autorizado, preserve a configuração anterior e faça uma alteração privada explícita; não libere um número desconhecido apenas porque enviou um evento.

O formato abaixo ilustra somente a chave; o valor deve ser criado localmente pelo comando, sem copiar este placeholder:

```json
{"allowed_phone":"<TELEFONE_CANONICO_CONFIGURADO_LOCALMENTE>"}
```

A ausência ou a invalidade dessa política impede a inicialização do receptor. Sua criação não modifica o arquivo de quatro campos do workflow, os outros segredos ou o cadastro do callback.

4. Inicie a API e o receptor em terminais separados. Na inicialização, o receptor cria/verifica `data/whatsapp-inbox.sqlite3` antes de escutar; corrupção, esquema incompatível ou base de outro sistema impedem a abertura. Preserve esse arquivo e seus backups ao atualizar ou reiniciar. Não existe carga retroativa dos recibos históricos. Se a API já estiver em execução, use a instância existente. Para aplicar a política ao receptor já iniciado, encerre somente sua instância verificada e inicie-a novamente:

```powershell
# Terminal da API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal do receptor isolado
.\.venv\Scripts\python.exe -m app.sandbox_webhook
```

5. Prepare o `cloudflared` conforme a [instalação oficial indicada no guia de Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/). O binário local em `data/tools/` não acompanha o Git; em outra máquina, ajuste o caminho abaixo para sua instalação. Em outro terminal, abra o túnel exclusivamente para o receptor:

```powershell
.\data\tools\cloudflared.exe tunnel --no-autoupdate --url http://127.0.0.1:8787 --protocol http2
```

O `cloudflared` mostra uma origem temporária `https://....trycloudflare.com`. O túnel aponta para a porta 8787; o editor n8n e a API de agenda continuam fora desse encaminhamento público.

6. Copie somente essa origem para registrar o callback. O script monta o restante usando a configuração privada:

```powershell
.\.venv\Scripts\python.exe scripts/register_sandbox_webhook.py --origin https://SEU-HOST.trycloudflare.com --apply
```

O registro só é confirmado se o provedor responder HTTP 200 e devolver exatamente o callback solicitado. Nenhuma mensagem de saída é enviada por esse comando.

7. Com a versão da parte 8 aplicada, uma mensagem nova do WhatsApp autorizado pode disparar a resposta fixa descrita no [guia de respostas](whatsapp-respostas.md). O teste consome um envio real do sandbox; repetir uma entrada já concluída não envia novamente. Lotes somente de status são confirmados sem responder. O aceite HTTP do provedor não comprova entrega ou leitura no aplicativo.

O provedor exige HTTPS e encaminha mensagens ao callback registrado. A ativação, o número atual e as regras de destinatário ficam na [documentação oficial da 360dialog](https://docs.360dialog.com/docs/get-started/sandbox).

## Contrato entre receptor e n8n

O receptor reserva os eventos novos na inbox e usa o UUID desse claim. Encaminha somente este objeto, sem campos adicionais, com contagens e tipos referentes apenas aos eventos únicos novos:

```json
{
  "source": "360dialog_sandbox",
  "request_id": "25f61604-9c02-4366-80be-928aeb4b440e",
  "message_count": 1,
  "status_count": 0,
  "event_types": ["message_text"]
}
```

As contagens são inteiros de 0 a 100, com soma positiva. `event_types` contém de 1 a 20 nomes únicos e limitados, como `message_text` ou `status_read`. A entrada n8n usa a credencial `Genius WhatsApp Sandbox - entrada`, com `X-Genius-Webhook-Key`. O validador aceita somente os tipos suportados, compatíveis com as contagens, e calcula `should_reply` para conferir o retorno. O HTTP faz `POST http://host.docker.internal:8000/api/v1/whatsapp/sandbox/reply`, usando a credencial Bearer existente e o mesmo UUID em `X-Request-ID`; o corpo contém somente `request_id`. A API decide pela inbox persistida, sem confiar nas contagens para autorizar o envio.

O n8n só devolve sucesso quando o envelope v1 de nove campos confirma `succeeded`, ação `whatsapp_test_reply`, `data.authenticated: true`, `reused` booleano e o mesmo UUID. Para mensagens, exige `reply_state: accepted` com `mutation_state: confirmed`; para somente status, exige `skipped` com `not_applicable`. O retorno mínimo passou a ter quatro campos:

```json
{"status":"received","api_reachable":true,"request_id":"25f61604-9c02-4366-80be-928aeb4b440e","reply_state":"accepted"}
```

Entrada normalizada inválida recebe 422; falha da API ou resposta incompatível recebe 503 seguro. Para um claim novo, o receptor confere os quatro campos e o estado esperado, marca o claim como `processed` no SQLite e confirma `{"status":"received"}`. O JSONL é auxiliar: falhar ao gravá-lo depois da conclusão mantém HTTP 200 e não libera novo encaminhamento. Eventos já concluídos também recebem 200, sem chamar n8n ou acrescentar recibo; qualquer evento pendente/incerto bloqueia o lote com 503. Não há retentativa automática nem expiração dos claims. O [guia de deduplicação](whatsapp-deduplicacao.md) detalha os estados e a recuperação.

## Proteções e limites atuais

- O acesso externo depende do caminho secreto, comparado em tempo constante. Depois da validação do esquema, cada telefone é comparado ao permitido, também em tempo constante. Telefones diferentes e eventos identificados somente por BSUID recebem 403, sem chegar ao n8n ou gerar recibo de sucesso. Eventos malformados continuam recebendo 400.
- **O caminho secreto e o filtro de telefone não são assinatura HMAC nem prova criptográfica de autoria.** A página específica do [sandbox](https://docs.360dialog.com/docs/get-started/sandbox) documenta HTTPS e cadastro de `url`, mas não documenta assinatura ou headers customizados nos callbacks. Headers/Basic Auth aparecem na [documentação geral](https://docs.360dialog.com/docs/messaging/webhook); a [referência correspondente](https://docs.360dialog.com/docs/messaging-api/api-reference/webhooks) identifica o servidor de produção. Esse suporte não foi presumido para o sandbox. `D360-API-KEY` autentica nossa chamada de configuração ao provedor, não o evento que chega aqui.
- O receptor limita o corpo a 64 KiB e sua leitura a dois segundos, exige JSON e rejeita chaves JSON repetidas, queries e envelopes incompatíveis. Documentação interativa e access log ficam desativados.
- O corpo bruto, o texto, o telefone e os IDs originais dos eventos recebidos não entram no banco da inbox, nos recibos ou nos diagnósticos. A inbox armazena hashes com namespace, tipos e metadados técnicos; são pseudônimos, não dados anônimos. O identificador permitido fica na política privada, com a versão anterior preservada no backup desta intervenção; nenhum telefone foi reproduzido nas evidências públicas. `data/whatsapp-sandbox-receipts.jsonl` guarda apenas o resumo normalizado. O diagnóstico `data/whatsapp-sandbox-ingress.jsonl` registra horários, resultados e indicadores de formato sem valores pessoais; mantém uma cópia anterior ao atingir seu limite de tamanho.
- As opções de salvamento de execuções bem-sucedidas, de erro, manuais e em progresso estão desativadas. O n8n ainda pode manter registros técnicos e dados transitórios até a limpeza; seu volume permanece privado. Os dados recebidos pelo provedor e pelo túnel também são processados por esses serviços externos.
- A deduplicação usa ID do provedor para mensagem e ID + fase para status; o timestamp não altera a identidade, e o tipo não altera a identidade de uma mensagem. Repetições concluídas não geram novo encaminhamento ou recibo. `processing`/`uncertain` bloqueiam o lote sem inserir seus eventos novos. Uma queda pode deixar bloqueado um evento ainda não encaminhado; não há promessa de execução exatamente uma vez ou liberação automática. A parte 8 adiciona uma resposta fixa por claim com mensagens novas e outbox separada com `sending`/`accepted`/`uncertain`; nenhuma retentativa automática foi adicionada. Interpretação, ações de agenda e estado de lembretes continuam pendentes. Veja [envio, tempos e recuperação](whatsapp-respostas.md).

O sandbox se limita a um destinatário por chave e a 200 requisições à API de mensagens, inclusive as rejeitadas. Oferece templates predefinidos e não permite upload/consulta de mídia por ID. A parte 9 confirmou o aceite de uma imagem por URL e do template `disclaimer/en`, sem acrescentar mídia ao fluxo ativo. Janela de atendimento, limite de imagem e restrições de templates estão no [guia específico](whatsapp-midias-templates.md), com fontes e distinção entre sandbox e produção. [Regras do sandbox](https://docs.360dialog.com/docs/get-started/sandbox).

## Recuperação do túnel e vínculo da identidade

O primeiro teste real depois do filtro recebeu 403: o telefone do evento não correspondia à política, mesmo com os dois campos de identificação presentes. O bloqueio foi mantido.

Depois, a mensagem “segurança 2” inicialmente não produziu callback novo. A entrada local respondia, mas a origem pública falhava na conexão e o provedor ainda apontava para ela. Reiniciamos somente os processos verificados do túnel, validamos a nova origem HTTPS e registramos/conferimos o callback por POST e GET com HTTP 200 e endereço coincidente. Essa ausência de entrega era uma falha de transporte, diferente da rejeição do remetente.

O reenvio real de “segurança 2” chegou em **23/10/2026 às 20:06:43.179954 UTC** e recebeu 403. O diagnóstico guardou somente relações booleanas: `all_events_phone_matches_policy: false` e `all_events_phone_matches_br_legacy: true`, com telefone e identificador de escopo presentes. Isso confirmou, para este caso, a relação testada com a representação brasileira sem o dígito adicional. Nenhum número recebido ou conteúdo foi persistido para fazer essa comparação.

Somente após essa confirmação, fixamos explicitamente **um único identificador** em `allowed_phone`, com backup privado em `secrets/whatsapp-sandbox-security.before-identity-binding.json`, e reiniciamos somente o receptor. A regra pública continuou igual: comparação exata, sem normalização genérica, aliases ou autoaprendizado. O booleano de formato legado continua sendo diagnóstico e nunca autoriza um evento.

Cinco verificações sintéticas após o vínculo passaram: identificador fixado recebeu 200; representação original com dígito adicional, outro telefone, BSUID isolado e lote misto receberam 403. Depois desses testes, uma nova mensagem do usuário confirmou o recebimento real com a política final, descrito na validação abaixo.

## Reiniciar e encerrar

Para execução manual, mantenha os três terminais abertos e encerre receptor, API e túnel com `Ctrl+C` quando terminar. O n8n pode ser parado com `docker compose stop`, preservando seu volume.

Na validação desta sessão, receptor e túnel foram iniciados em segundo plano e acompanhados no arquivo runtime privado. Para encerrá-los, confira os PIDs e a identidade dos processos registrados; evite iniciar cópias ou interromper processos por nome de forma geral.

Após reiniciar o computador, ligue o Docker, a API, o receptor e um novo túnel. Preserve a política privada do telefone, a inbox e a outbox SQLite; não é necessário executar o configurador novamente para retomar o mesmo remetente. O reinício mantém repetições concluídas e claims pendentes/incertos; não apague a inbox para contornar um bloqueio. **Cada Quick Tunnel gera outro hostname:** repita o registro com `--origin` para o endereço novo. Não é necessário reinstalar o workflow nem recriar seus segredos. A origem anterior para de funcionar quando o processo do túnel termina. Quick Tunnels são destinados a desenvolvimento, sem garantia de disponibilidade; o uso contínuo pede implantação e endereço estável. [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## Ensaios separados de imagem e template — parte 9

O comando `scripts/test_whatsapp_media.py --kind image|template --operation-id <UUID>` mostra uma prévia sem rede por padrão; `--execute` solicita explicitamente um envio ao WhatsApp já autorizado. Cada operação é vinculada a UUID, caso e hash canônico do payload/destinatário no ledger separado `data/whatsapp-media-probes.sqlite3`, schema 1. O telefone participa do cálculo somente em memória; persiste apenas o hash, que permanece pseudônimo e privado. Preserve também esse ledger nos backups, sem reutilizar uma operação para outra definição.

A imagem de teste foi o [JPEG público do MDN](https://interactive-examples.mdn.mozilla.net/media/cc0-images/elephant-660-480.jpg), com 39.662 bytes, 660×480 e 8 bits. Ela valida transporte e não representa um card real ou imagem de IA. O segundo caso foi somente `disclaimer`, idioma `en`, com `components: []`.

Ambos foram aceitos pelo provedor, com uma requisição por caso; repetir cada UUID retornou `reused: true` sem novo envio. O teste comprova aceite da imagem no sandbox e do template predefinido, com confirmação humana do recebimento dos dois itens, sem atribuir status de entrega/leitura do provedor. Passaram **664 testes Python**: 613 anteriores +20 do sender +18 do ledger +13 de serviço/CLI. As 502 verificações JavaScript pertencem à parte 8 e não foram repetidas. Arquivos n8n/manifestos, código da API/receptor/inbox/outbox e esquemas anteriores foram preservados. Não há nova rota de API/n8n ou disparo de mídia no produto ativo. O [guia de mídias e templates](whatsapp-midias-templates.md) detalha o comando e os limites; a parte 10 de revisão foi concluída posteriormente no escopo de teste.

## Revisão e fechamento — parte 10

O fechamento preserva os 664 testes Python aprovados e as 502 verificações JavaScript históricas da parte 8. A revisão operacional não repete os ensaios de envio; seus resultados e a conferência dos quatro exports/manifestos ficam no registro 091 e no [relatório final](fechamento-etapa-10.md). Resposta fixa e mídia/template tiveram confirmações humanas separadas dos aceites técnicos. Mídia continua em CLI; o bot ativo segue sem IA ou ação de calendário pela conversa.

## Validação e evidências

O marco inicial do recebimento aprovou **478 testes Python** e **470 verificações JavaScript**: as 427 anteriores mais 43 do novo fluxo. Na implementação da proteção do remetente, a suíte completa passou com **499 testes Python**, incluindo **43 do receptor** e **7 do configurador de remetente**. Na retomada do túnel e no vínculo final da política, o código público não mudou: os resultados Python e JavaScript foram preservados, sem executar novamente as suítes.

Foram aprovadas **26 verificações HTTP com payloads sintéticos** no total: 20 iniciais, um probe bloqueado na recuperação e cinco casos após o vínculo final. Os casos permitidos alcançaram n8n e status da API; os eventos proibidos foram rejeitados antes do encaminhamento. Esses testes não enviaram mensagens de saída pelo WhatsApp nem acessaram o Google Calendar.

A mensagem real final informada pelo usuário foi confirmada às **20:09:30.176124 UTC**, com diagnóstico `succeeded`, HTTP 200 e recibo UUID `2487cab1-9409-4678-a991-b639604c7810`: uma mensagem, nenhum status e tipo `message_text`. O telefone coincidiu exatamente com a política (`all_events_phone_matches_policy: true`); nesse recibo, `all_events_phone_matches_br_legacy` foi `false`. A autorização continuou baseada somente na igualdade exata. Os campos de telefone e escopo estavam presentes e com formato aceito.

O relatório separa os sete recibos gerados pelos probes sintéticos do recebimento real inicial e deste novo recebimento autorizado. O sucesso final comprova entrada → filtro de remetente → n8n → status autenticado da API. Não comprova resposta no WhatsApp, deduplicação ou operação de agenda. Essa prova histórica concluiu a parte 6; a prova posterior da parte 7 está separada abaixo.

O teste real inicial, **anterior ao filtro de telefone**, foi confirmado às **18:25:24 UTC**: diagnóstico `succeeded`, HTTP 200 e recibo com UUID `25f61604-9c02-4366-80be-928aeb4b440e`, uma mensagem, nenhum status e tipo `message_text`. Foi necessário aceitar os identificadores de remetente presentes no envelope real. Essa evidência demonstra chegada ao receptor, passagem pelo n8n e acesso autenticado ao status Python; não demonstra resposta no WhatsApp, interpretação de texto ou acesso à agenda.

Na parte 7, a suíte completa passou com **551 testes Python**: 499 anteriores, mais 18 casos do receptor e 34 do novo store. São **61 testes do receptor**, **34 do store** e **7 do configurador**. As **470 verificações JavaScript** permanecem um resultado histórico, sem nova execução nesta entrega.

A prova nova executou **22 cenários/23 requisições HTTP loopback**, com destino fixo `http://127.0.0.1:8787`, IDs/dados fictícios e n8n/API locais reais. Nove identidades novas produziram nove encaminhamentos/consultas de status e nove recibos. Repetições não aumentaram os totais, inclusive depois de reiniciar realmente o receptor; um ID novo após o reinício foi aceito. Foram conferidos banco, journals, recibos e diagnósticos sem ID original, texto ou telefone. O telefone autorizado permaneceu somente em memória no ensaio local.

O primeiro ensaio proposto pelo HTTPS público com telefone privado foi rejeitado pela revisão automática e não executado; a alternativa ficou restrita ao loopback. Esta prova **não é uma retransmissão real do WhatsApp/provedor nem uma validação pública HTTPS da deduplicação**. Naquele ensaio da parte 7, não houve resposta no WhatsApp ou operação Google. A parte 7 foi concluída nesse escopo; a parte 8 foi implementada depois. O canvas da parte 7 permaneceu igual e não justificou outro print.

Na **parte 8**, passaram **613 testes Python** e **502 verificações JavaScript** nas cinco suítes, executadas nesta entrega. A API ganhou consulta autoritativa do claim, serviço independente, transporte para destino fixo e outbox separada. O fluxo mudou para POST de resposta e o retorno ganhou `reply_state`. O [guia de respostas](whatsapp-respostas.md#resultado-do-ensaio-real) contém o resultado efetivo do ensaio de saída; não se deve atribuir esse envio aos testes históricos 080–085. Passaram **nove verificações HTTP** nesta parte: entrada sintética loopback percorreu n8n/API e produziu **um aceite real da 360dialog**, com outbox `accepted` e inbox `processed`; repetir o mesmo ID, inclusive com texto alterado, não gerou outro envio. Somente status foi confirmado sem reserva de saída; autenticação, claim desconhecido/concluído e campo extra foram bloqueados. Não foi uma nova entrada real do WhatsApp/HTTPS e o aceite não comprova entrega/leitura no aparelho. O callback HTTPS foi recuperado e conferido separadamente, sem mudar essa proveniência.

| Registro local | O que demonstra |
|---|---|
| `080_whatsapp_sandbox_integracao_real.json` | Testes locais, verificações HTTP e confirmação do percurso real, sem conteúdo ou telefone |
| `081_n8n_sandbox_recebimento_publicado.png` | Canvas real do workflow publicado no marco inicial; complementa a prova HTTP |
| `082_whatsapp_sandbox_seguranca_entrada.json` | Marco histórico da política obrigatória: 499 testes Python e 20 verificações HTTP sintéticas, antes da confirmação do formato |
| `083_whatsapp_sandbox_remetente_confirmado.json` | Recuperação do túnel, reenvio real rejeitado com comparação booleana, vínculo explícito da política, verificações sintéticas finais e novo recebimento real autorizado HTTP 200, com recibos separados |
| `084_whatsapp_deduplicacao_testes.json` | Estados da inbox e suíte completa de 551 testes Python |
| `085_whatsapp_deduplicacao_http_local.json` | 22 cenários HTTP loopback/23 requisições, nove identidades novas, concorrência, reinício real e privacidade da persistência |
| `086_whatsapp_respostas_testes.json` | Implementação e validação da parte 8: 613 testes Python e 502 verificações JavaScript |
| `087_whatsapp_resposta_envio_real.json` | Resultado e proveniência do ensaio de saída real, com origem de entrada sintética local |
| `088_whatsapp_midias_templates_testes.json` | Parte 9: 664 testes Python e preservação do fluxo ativo; JavaScript histórico sem nova execução |
| `089_whatsapp_imagem_publica_teste.jpg` | Imagem pública do MDN usada apenas no teste de transporte; não é card de entrevista ou conteúdo gerado por IA |
| `090_whatsapp_midias_templates_envios_reais.json` | Dois aceites reais: imagem por link e `disclaimer/en`, com replay reutilizado e sem novo envio |
| `091_fechamento_etapa_10.json` | Revisão final da alternativa de teste e verificações operacionais, sem novos envios |

Os originais ficam no acervo ignorado `data/evidencias/`, com o [índice de evidências](evidencias.md). O [manifesto separado do sandbox](../n8n/sandbox-export-manifest.json) registra a comparação do export público com a instância e a preservação dos três exports anteriores. Os quatro arquivos públicos continuam inativos; publicação local e distribuição do export são estados diferentes.

Para reproduzir os testes específicos:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_whatsapp*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_sandbox_webhook.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_setup_whatsapp_sandbox.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_configure_sandbox_sender.py -v
node --test n8n/tests/whatsapp-sandbox.test.cjs
```
