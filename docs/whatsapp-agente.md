# Agente na conversa WhatsApp — etapa 11, parte 10

Implementado em 25/10/2026. O sandbox publicado passou a ativar o agente Python, substituindo a resposta fixa no fluxo ativo. A seleção local usa [Gemini 3.5 Flash-Lite](gemini-api.md). A entrada continua restrita ao único remetente configurado privadamente. A etapa 11 foi concluída em 25/10/2026 com a revisão da parte 12. Este guia conserva o alcance e os resultados da integração da parte 10; o [fechamento](fechamento-etapa-11.md) consolida a versão final e suas pendências.

## Percurso

1. O receptor valida o callback completo, o token de acesso e o remetente; reserva IDs novos na inbox existente.
2. Persiste um trabalho por mensagem nova na fila SQLite privada, antes de chamar o n8n.
3. Encaminha somente `source`, `request_id`, `message_count`, `status_count` e `event_types`. Texto, telefone e resposta não entram no workflow.
4. O n8n chama o comando autenticado de ativação da fila e recebe `queued` ou `skipped`. O callback confirma recebimento sem esperar a IA.
5. O worker Python processa a conversa, usa os serviços existentes e prepara uma resposta persistente.
6. Reserva a saída na outbox própria e envia pelo 360dialog somente ao destinatário privado permitido.

Status de entrega/leitura não chama o modelo nem responde. Mídia recebe orientação por texto; não é baixada ou enviada ao LLM. A CLI de [imagem/template](whatsapp-midias-templates.md) continua separada.

## Pedido, contexto e autorização

O serviço [`WhatsAppAgentService`](../app/services/whatsapp_agent.py) reutiliza consultas, diálogo, preparação, confirmações e executores existentes. Uma intenção incompleta pergunta o dado faltante; opções ambíguas exigem escolha. O vínculo privado conserva o UUID da conversa e a autorização controlada pelo servidor, com a expiração do contexto existente.

Criar/editar/reagendar exige pedido explícito e campos vinculados ao texto/contexto verificado. Perguntas, hipóteses, citações, negações e dados não sustentados pelo pedido não autorizam execução automática. Algumas formas de data/seleção exigem prévia para conferir os dados. Uma prévia longa demais é revogada em vez de truncada em conteúdo que possa ser aprovado sem conferência.

Excluir sempre apresenta o evento e exige resposta afirmativa inequívoca para a proposta exata ainda válida. O consentimento é conferido e consumido antes de executar o serviço de exclusão; o modelo não decide a aprovação. Uma confirmação existente também precisa ser respeitada nos demais comandos. As rotas de diálogo/preparação isoladas continuam sem executar; quem liga a intenção aos executores é este orquestrador de transporte.

A resposta é formatada a partir do resultado estruturado: perguntas, opções, conflitos, resultado confirmado ou falha segura. Não publica ETag, token de confirmação, hashes, telefone ou detalhes brutos do provedor. O texto do modelo não comprova sucesso de uma escrita.

## API utilizada pelo n8n

`POST /api/v1/whatsapp/sandbox/agent`, sob Bearer e `Content-Type: application/json`, recebe exatamente `{request_id}`. Exige UUID4 canônico igual ao `X-Request-ID`, sem query/campos extras, com JSON estrito de até 1 KiB. Autenticação ocorre antes de construir ou consultar o serviço.

Em sucesso, o envelope v1 usa `action: whatsapp_agent_turn`, `status: succeeded`, `operation_id: null`, `mutation_state: not_applicable` e `data: {authenticated: true, queue_state: queued|skipped, reused: boolean}`. **Isso comprova ativação da fila, não execução da agenda ou entrega WhatsApp.** `queued` exige trabalho já persistido; `skipped` corresponde a um claim ativo somente de status. Claim ausente/inconsistente ou resultado incerto recebe erro seguro; não recebe texto/telefone livre do cliente.

O fluxo normaliza somente esse contrato. HTTP sem retries ou redirecionamentos, execução sem histórico salvo; 15 segundos no nó HTTP e 30 segundos no workflow. A rota antiga `/whatsapp/sandbox/reply` permanece disponível explicitamente, mas o export ativo atualizado usa `/agent`.

## Persistência e falhas

`WHATSAPP_AGENT_DATABASE_PATH` seleciona a fila `data/whatsapp-agent.sqlite3`, schema 1; receptor e API usam o mesmo caminho. `WHATSAPP_AGENT_OUTBOX_PATH` seleciona `data/whatsapp-agent-outbox.sqlite3`, separado da antiga resposta fixa. A fila tem capacidade limitada e não descarta incertezas para abrir espaço.

Os estados são `staged → queued → processing → prepared → delivering → sent`, com saídas `rejected` e `uncertain`. Cada remetente respeita a ordem de entrada; um trabalho anterior em aberto impede o próximo. Nonces protegem as transições. O vínculo usa HMAC do token privado e do destinatário: trocar a configuração não redireciona respostas antigas a outra pessoa.

O texto fica privadamente na fila até preparar a resposta; a resposta fica até o envio terminal. `sent`/`rejected` removem logicamente ambos, preservando metadados. Conversas/rascunhos seguem seu TTL próprio. Limpeza SQL não apaga de forma forense páginas, journals ou backups; trabalhos incertos podem conservar dados para conferência.

Uma resposta já `prepared` pode continuar após reinício sem reinterpretar ou repetir a operação. Se a outbox já confirmou o aceite, o worker apenas conclui a fila. Interrupção em `processing`/`delivering` e resultado `uncertain` não são retomados cegamente; bloqueiam o remetente para análise manual. Não há comando administrativo de reset/reprocessamento automático. Uma rejeição comprovada do provedor encerra aquele envio sem retry e permite uma mensagem nova independente.

As garantias de versão, conflitos, deduplicação da criação e reconciliação dos executores continuam aplicadas. Não há promessa de transação distribuída/exatamente uma vez com Google e WhatsApp. Aceite do 360dialog não equivale a entrega ou leitura pelo usuário.

## Configuração e reprodução

Além do [Gemini](gemini-api.md), mantenha no `.env` privado:

```dotenv
WHATSAPP_AGENT_ENABLED=true
WHATSAPP_AGENT_DATABASE_PATH=data/whatsapp-agent.sqlite3
WHATSAPP_AGENT_OUTBOX_PATH=data/whatsapp-agent-outbox.sqlite3
```

Em `secrets/whatsapp-sandbox-local.json`, `agent_enabled: true` ativa o modo correspondente no receptor. Os outros IDs e tokens privados são preservados. A API e o receptor devem estar alinhados com o export antes de receber mensagens.

```powershell
.\.venv\Scripts\python.exe scripts/setup_whatsapp_sandbox.py --apply
.\.venv\Scripts\python.exe -m app
.\.venv\Scripts\python.exe -m app.sandbox_webhook
```

O primeiro comando publica a ponte e reinicia somente o n8n, preservando o volume. Execute os dois servidores em terminais separados, sem criar instâncias duplicadas. Docker/n8n precisa estar aberto; mantenha a configuração de host utilizada pela integração Docker. O [túnel e o registro privado](whatsapp-sandbox.md) são passos separados; um túnel temporário encerrado pode exigir novo endereço/cadastro. Não copie o callback secreto para documentação ou prints.

O sandbox usa token de capacidade na URL e restrição de remetente, conforme a [segurança existente](whatsapp-sandbox.md). Isso não é assinatura criptográfica da Meta e não transforma o sandbox em canal de produção.

## Validação deste marco

- 1.508 testes Python: 141 novos sobre 1.367 — fila/entrada (42), orquestrador (47), API/worker (20), Gemini (25) e seleção do provedor (7).
- 540 cenários JavaScript: 38 novos sobre 502; sandbox 113, consulta 82, agendado 67, integração 63 e apresentação 215. O harness Node agrupa quatro runners e reporta 117 itens; o total de cenários usa os contadores efetivos.
- 29 verificações HTTP locais com ponte n8n fixture: 17 requisições de negócio, 12 observações, dois processos e SQLite reais; contexto, reinício, rejeição e incerteza.
- 42 verificações com n8n Docker publicado e 14 chamadas ao webhook: API/worker/fila reais; modelo, Google e saída substituídos. Criação, dois PATCHs e exclusão ocorreram somente nas fixtures.
- Dois pedidos reais ao Gemini com conteúdo fictício retornaram HTTP 200. O registro do callback 360dialog teve POST/GET 200 e HTTPS 404 na raiz esperada; isso não é prova de uma conversa humana completa.

Os quatro exports foram novamente conferidos com a instância, incluindo as versões publicadas dos dois ativos, sem exportar credenciais. Três exports anteriores preservados; somente o sandbox mudou. Registros 110 a 114 no [índice](evidencias.md). Na prova real do registro 114, a consulta diária foi persistida no contexto, um envio foi aceito e o usuário confirmou o recebimento no aparelho. Nenhuma mutação real foi executada; a contagem interna de requisições Google/LLM dessa consulta não foi instrumentada. A parte 11 verificou conversas, conflitos e mutações com provedores controlados e Gemini com agenda fictícia. Conflitos e mutações reais pelo canal permanecem sem validação; a confirmação humana deste registro se refere à consulta diária.


Atualização da parte 11: [conversas e falhas verificadas](testes-conversas-agente.md), com nove ajustes e 1.561 testes aprovados. As mutações conversacionais deste marco usaram agenda fictícia; a prova WhatsApp humana da parte 10 permanece histórica. O ledger de exclusões aguarda bloqueios SQLite por até 5 s, sem repetir escrita; prévia definitivamente recusada no transporte é descartada antes de liberar outro turno.


A parte 12 encerrou a revisão documental sem novas suítes, chamadas a provedores ou envios. As 29 verificações HTTP foram reexecutadas com as fontes finais na parte 11; os 540 cenários JavaScript e as 42 verificações com n8n desta página permanecem resultados históricos da parte 10. Próxima etapa: lembretes, conforme o [fechamento](fechamento-etapa-11.md).
