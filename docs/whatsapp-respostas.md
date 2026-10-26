# Resposta fixa pelo WhatsApp Sandbox

**Parte 8 concluída em 24/10/2026:** o fluxo pode enviar uma resposta fixa ao WhatsApp explicitamente autorizado, com decisão pela inbox e reserva persistente na outbox. Passaram **613 testes Python** e **502 verificações JavaScript**. O resultado do ensaio real está registrado separadamente abaixo.

Esta entrega confirma a integração de transporte. Interpretação da conversa, IA, ações de agenda, lembretes, mídia e templates personalizados continuam fora deste fluxo.

**Atualização da parte 9, em 24/10/2026:** o [ensaio separado de mídias/templates](whatsapp-midias-templates.md) obteve dois aceites reais — imagem por link e `disclaimer/en` — com uma chamada por caso e replay reutilizado. O produto ativo descrito aqui continua enviando somente a resposta fixa: não foi criado disparo de mídia por callback, API ou n8n. O total atual é **664 testes Python**; as **502 verificações JavaScript** são o resultado da parte 8, sem nova execução nesta parte. O usuário confirmou “Chegaram os dois”; essa confirmação humana permanece separada do aceite e dos status técnicos de entrega/leitura. A revisão da parte 10 concluiu posteriormente esse escopo de teste, conforme o fechamento abaixo.

**Fechamento da etapa 10 em 24/10/2026:** a parte 10 concluiu a revisão desta integração de teste na alternativa 360dialog. A resposta fixa confirmada pelo usuário e os ensaios de mídia permanecem separados; não houve novo envio para fechar a etapa. O [relatório de fechamento](fechamento-etapa-10.md) reúne checklist, reprodução e resultados finais do registro 091. Provisionamento Meta original e funcionalidades futuras do produto continuam fora do que foi concluído.

## Percurso e responsabilidade

```text
Callback → receptor Python → claim processing na inbox
  → n8n recebe somente metadados → POST autenticado na API Python
  → API consulta o claim real → reserva de saída → 360dialog → WhatsApp autorizado
  → aceite validado → outbox accepted → retorno n8n → inbox processed
```

O n8n recebe os mesmos cinco campos: `source`, `request_id`, `message_count`, `status_count` e `event_types`. Não recebe telefone, texto, ID original do provedor ou chave da 360dialog. O validador retorna somente UUID, `valid` e `should_reply`; a última informação serve para conferir o retorno esperado, não para autorizar um envio na API.

| Componente | Responsabilidade |
|---|---|
| [`app/services/whatsapp_reply.py`](../app/services/whatsapp_reply.py) | Conferir o claim na inbox, decidir envio/skip e coordenar reserva e transporte, sem dependência de IA |
| [`app/api_whatsapp.py`](../app/api_whatsapp.py) | Receber o comando interno autenticado e produzir o envelope seguro |
| [`app/whatsapp_outbox.py`](../app/whatsapp_outbox.py) | Reservar uma resposta por UUID e impedir retentativa automática de resultados incertos |
| [`app/whatsapp_sandbox_sender.py`](../app/whatsapp_sandbox_sender.py) | Enviar ao endereço fixo do sandbox e validar o aceite |
| [`whatsapp-sandbox.json`](../n8n/workflows/whatsapp-sandbox.json) | Fazer a chamada interna, conferir correlação/estado e devolver confirmação mínima |

Uma execução responde **uma vez por claim com mensagens novas**, mesmo que o lote contenha várias mensagens. Um lote somente de status recebe `skipped`, sem reservar saída ou chamar o provedor. Lotes mistos são respondidos quando o claim contém mensagens novas; os eventos já concluídos foram retirados pelo receptor antes disso.

O texto é uma constante do serviço:

> Recebi sua mensagem! A integração de teste do Genius Agenda AI está funcionando com Python, n8n e WhatsApp. A conversa com a agenda será a próxima etapa.

O destinatário vem exclusivamente da política privada de remetente já configurada. Não é selecionado pelo corpo do comando nem pelo n8n. A chave fica em `D360_API_KEY`, no `.env` privado; o serviço de envio só é instanciado após a autenticação da rota.

## Comando interno e confirmações

O n8n substitui o antigo GET de status por:

```text
POST /api/v1/whatsapp/sandbox/reply
Authorization: Bearer <CHAVE_PRIVADA_DA_API>
X-Request-ID: <UUID_DO_CLAIM>
Content-Type: application/json
```

```json
{"request_id":"<MESMO_UUID_CANONICO_V4_DO_HEADER>"}
```

O corpo só aceita essa chave, tem limite de 1 KiB e leitura de até dois segundos; queries, chaves repetidas ou UUID diferente do header recebem 422. A autenticação Bearer ocorre antes de consumir configuração de envio. O serviço consulta o claim persistido: só um conjunto íntegro em `processing` permite decidir envio ou skip. UUID inexistente, concluído ou incerto não libera envio e recebe 409; um UUID bem formado sozinho não cria um claim.

O envelope mantém os nove campos da API v1 e usa a extensão de ação `whatsapp_test_reply`. Em sucesso, `data` contém exatamente `authenticated: true`, `reply_state` e `reused` booleano. Os estados são:

| Resultado | `reply_state` | `mutation_state` da API | Significado |
|---|---|---|---|
| Aceite válido do provedor, persistido | `accepted` | `confirmed` | O provedor aceitou o pedido de envio |
| Claim somente de status | `skipped` | `not_applicable` | Nenhuma mensagem foi enviada |
| Falha com resultado incerto | Não há sucesso | `unknown` | Não há confirmação segura para repetir |

**`accepted` não significa entregue ou lido.** Esta parte não correlaciona recibos de entrega/leitura com a mensagem de saída. `reused: true` informa que um aceite já persistido foi recuperado sem novo envio, enquanto o mesmo claim ainda está em `processing`.

O n8n exige HTTP 200, envelope compatível, UUID coincidente e o estado esperado: `accepted` para metadados com mensagens ou `skipped` para somente status. Seu retorno de sucesso tem quatro campos:

```json
{"status":"received","api_reachable":true,"request_id":"<UUID_DO_CLAIM>","reply_state":"accepted"}
```

O receptor também confere os quatro campos antes de concluir a inbox. Falha ou resposta incompatível produz 503 externo seguro; erros brutos do provedor não são repassados. Repetições da entrada já `processed` continuam recebendo 200 sem chamar n8n, enviar mensagem ou criar outro recibo JSONL.

## Outbox, falhas e dados privados

`data/whatsapp-outbox.sqlite3`, **schema 1**, é separado da inbox `data/whatsapp-inbox.sqlite3` (schema 1) e de `data/agenda.db` (schema 2). Os esquemas anteriores foram preservados.

| Estado da outbox | Comportamento |
|---|---|
| `sending` | Reserva persistida antes da chamada ao provedor; outra tentativa fica bloqueada |
| `accepted` | Aceite confirmado e fingerprint persistido; pode ser reutilizado sem novo envio se o claim da inbox ainda estiver ativo |
| `uncertain` | Resultado do envio não confirmado; bloqueia repetição automática |

O envio usa apenas `https://waba-sandbox.360dialog.io/v1/messages`, com TLS verificado, proxies do ambiente ignorados, redirecionamentos desativados e nenhuma retentativa automática. Só HTTP 200/201 com um único identificador de mensagem válido confirma o aceite. Timeout, resposta inválida, falha ao persistir o aceite ou outro erro após a reserva deixam o resultado incerto; se nem essa gravação puder ser feita, `sending` continua bloqueando.

A outbox guarda UUID técnico, estado, horários UTC e somente um SHA-256 com namespace para o ID de saída aceito. Não guarda telefone, conteúdo, chave, ID original ou resposta bruta do provedor. Esse fingerprint é pseudônimo, não anônimo. Os dados necessários para enviar passam em memória ao provedor; isso é diferente de persistir conteúdo localmente.

Não há TTL, lease, reabertura automática ou promessa de execução exatamente uma vez. Uma queda entre reserva, envio e persistência pode exigir reconciliação manual, inclusive se o envio ainda não tiver ocorrido. Preserve os três bancos e backups consistentes; não apague a outbox ou a inbox para forçar reenvio. A confirmação no banco da inbox continua anterior ao recibo JSONL auxiliar, cuja falha posterior mantém 200.

## Configuração e tempos

Use os [pré-requisitos do sandbox](whatsapp-sandbox.md#instalar-e-executar): Docker/n8n, API, receptor, chave privada, política de remetente e, para callbacks externos, túnel HTTPS registrado. A mesma credencial Bearer `Genius API - local` atende a nova rota. Para importar a versão atual do fluxo:

```powershell
.\.venv\Scripts\python.exe scripts/setup_whatsapp_sandbox.py --apply
```

O instalador reutiliza os IDs e segredos existentes, publica somente seu fluxo e reinicia brevemente o n8n. Reinicie as instâncias verificadas da API e do receptor ao atualizar o código. Nenhum telefone ou token precisa aparecer em argumentos. O export público permanece inativo e com referências de credencial somente por nome; o manifesto do sandbox deve acompanhar o export efetivamente aplicado.

| Camada | Limite configurado |
|---|---|
| Receptor → n8n | Conexão 2 s; leitura 20 s |
| HTTP do n8n → API | Timeout 15.000 ms; workflow até 30 s |
| API → 360dialog | Conexão 3 s; leitura 8 s |

São limites por camada. Eles não estabelecem um prazo global de entrega: leituras de rede, processamento e persistência não compartilham um único cronômetro.

## Testes e resultado do ensaio real

Na parte 8, a suíte aprovou **613 testes Python**: 551 anteriores, 12 da consulta autoritativa de claims, 31 de transporte/outbox e 19 de serviço/API. As cinco suítes JavaScript foram executadas naquele marco, totalizando **502 verificações**: 215 + 82 + 63 + 67 + 75 do sandbox. Não houve falhas ou erros. A parte 9 ampliou a suíte Python para 664 sem alterar API, receptor, inbox, outbox ou arquivos n8n/manifestos; JavaScript não foi repetido.

Os testes usam provedores simulados e bases temporárias. Cobrem autenticação, entrada estrita, estado real do claim, somente status, lote com várias mensagens, correlação, concorrência, reuso de aceite, falhas incertas e dados privados. Os registros 080–085 preservam as provas anteriores; os 22 cenários/23 requisições da parte 7 consultavam apenas status e não enviaram mensagens.

O ensaio real desta parte tem origem **sintética local**: um POST controlado ao receptor chama n8n/API locais e então o provedor real, com destinatário previamente autorizado. Isso permite conferir envio real e repetição do mesmo ID sem alegar uma nova mensagem de entrada real pelo WhatsApp/HTTPS. O resultado efetivo não deve ser confundido com os testes simulados.

### Resultado do ensaio real

Em **24/10/2026**, passaram **nove verificações HTTP** com o receptor, n8n, API, inbox e outbox reais. A origem foi um POST sintético controlado em loopback, com o telefone autorizado somente em memória; a saída para a 360dialog foi real. **Um pedido de resposta foi aceito pelo provedor**, com HTTP e identificador de mensagem validados pelo transporte. A outbox terminou `accepted`, e o claim da inbox, `processed`.

| Verificação | Resultado |
|---|---|
| Entrada sintética nova, percurso local até o provedor real | 200 no receptor; um único aceite de saída |
| Repetição do mesmo ID | 200, sem novo envio |
| Mesmo ID com texto alterado | 200, sem novo envio |
| Chamada direta para o claim já `processed` | 409, sem envio |
| Lote sintético somente de status | 200 com skip; nenhuma reserva de saída |
| API sem Bearer | 401 |
| UUID de claim desconhecido | 409 |
| Campo extra `to` no comando interno | 422 |
| Webhook n8n sem chave | 403 |

Essa prova confirma **saída real aceita pelo provedor**, a partir de **entrada sintética local**. Não foi uma nova mensagem de entrada real do usuário pelo WhatsApp/HTTPS. Separadamente do aceite técnico, o usuário confirmou “Recebi” e informou que recebeu a mensagem enquanto almoçava. Essa é uma confirmação humana do recebimento, sem captura ou atribuição de um status de entrega/leitura do provedor. Não houve operação Google ou segundo envio nas repetições.

O endereço HTTPS temporário anterior perdeu conectividade. Recuperamos somente o processo verificado do túnel e registramos/conferimos o novo callback com POST e GET 200; a raiz pública respondeu 404, conforme a exposição restrita. Essa recuperação não muda a origem loopback do ensaio de envio acima.

| Evidência | Conteúdo |
|---|---|
| `086_whatsapp_respostas_testes.json` | Implementação e validação local Python/JavaScript |
| `087_whatsapp_resposta_envio_real.json` | Resultado e proveniência do ensaio real de saída, separado da entrada sintética |

Os originais e os resultados anteriores estão no [índice de evidências](evidencias.md). A parte 9 de imagem/template foi concluída depois, em ensaio CLI separado com ledger próprio `data/whatsapp-media-probes.sqlite3`, schema 1. O ledger vincula UUID, caso e hash da definição/destinatário e reutiliza aceites sem reenvio; não substitui a outbox da resposta fixa. As provas 088–090 e os comandos de prévia/execução estão no [guia de mídias/templates](whatsapp-midias-templates.md). A parte 10 de revisão foi concluída posteriormente no escopo de teste.

Para reproduzir somente os testes automatizados, sem enviar mensagem:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_whatsapp*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_sandbox_webhook.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test n8n/tests/whatsapp-sandbox.test.cjs
```
