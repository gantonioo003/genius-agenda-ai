# Fechamento da etapa 10 — WhatsApp de teste

**Concluída em 24/10/2026 no escopo de integração de teste pela alternativa 360dialog Sandbox.** Foram entregues entrada HTTPS, autorização de remetente, deduplicação, resposta fixa e ensaios separados de imagem/template. O aplicativo e o portfólio Meta foram criados, mas o erro de emissão do número de teste pela Meta continua sem solução; a alternativa não substitui essa pendência por uma alegação de sucesso.

O bot ativo responde com um texto fixo. Conversa com IA, operações de calendário por WhatsApp, lembretes, briefings, cards contextualizados e painel web continuam em etapas posteriores. Este fechamento não conclui o projeto inteiro nem caracteriza implantação de produção.

## Checklist das dez partes

| Parte | Entrega no percurso de teste | Resultado |
|---|---|---|
| 1 | Preparar aplicativo e acesso | Conta de desenvolvedor, app e portfólio Meta criados; diagnóstico preservado |
| 2 | Obter canal e destinatário de teste | 360dialog ativada para o WhatsApp autorizado; emissão Meta original continua pendente |
| 3 | Configurar credenciais privadas | Chave do provedor, referências n8n e tokens próprios fora do Git |
| 4 | Disponibilizar HTTPS e callback | Túnel restrito ao receptor, cadastro e conferência do callback; retomada documentada |
| 5 | Conectar recebimento ao n8n/Python | Mensagem real chegou à ponte; somente metadados entram no n8n |
| 6 | Proteger a entrada | Caminho secreto, esquema completo e telefone exato; lotes proibidos rejeitados antes da inbox/fluxo |
| 7 | Tratar repetições | Inbox persistente, reserva antes do fluxo e bloqueio de resultados pendentes/incertos |
| 8 | Responder mensagem | API autenticada, decisão pelo claim, outbox e resposta fixa; usuário confirmou recebimento |
| 9 | Verificar imagem e template | CLI separada; imagem por link e `disclaimer/en` aceitos; usuário confirmou os dois itens |
| 10 | Revisar integração e documentação | Auditoria, conferência dos exports/manifestos e registro final 091; resultados abaixo |

## Capacidades e limites

O percurso ativo é `360dialog → HTTPS → receptor → inbox → n8n → API autenticada → outbox → 360dialog`. O n8n recebe somente origem, UUID técnico, contagens e tipos. A API decide pela inbox em `processing`: um claim com mensagens novas permite uma resposta fixa; somente status não envia mensagem. O [guia de respostas](whatsapp-respostas.md) documenta os contratos e os tempos por camada.

Repetições concluídas recebem 200 sem novo fluxo, envio ou recibo JSONL. `processing`, `sending` e `uncertain` bloqueiam repetição automática, sem TTL ou expiração. Uma queda pode exigir reconciliação manual; não há garantia de execução exatamente uma vez. `accepted` confirma aceite do provedor, não entrega/leitura. As confirmações humanas da resposta e dos dois itens de mídia estão registradas separadamente dos estados técnicos.

| Banco privado | Uso | Esquema |
|---|---|---|
| `data/agenda.db` | Histórico das operações da agenda, preservado | 2 |
| `data/whatsapp-inbox.sqlite3` | Identidades de mensagens/status e estado de recebimento | 1 |
| `data/whatsapp-outbox.sqlite3` | Reserva e aceite/incerteza da resposta fixa por claim | 1 |
| `data/whatsapp-media-probes.sqlite3` | Operações dos ensaios manuais de imagem/template | 1 |

Hashes de IDs e de bindings são pseudônimos, não dados anônimos. Os bancos de WhatsApp não guardam telefone, texto ou ID original em claro. O telefone autorizado fica somente na política privada e é usado em memória no envio/cálculo de vínculo. Preserve os quatro bancos e backups consistentes; apagar ou substituir registros remove a memória das repetições.

O sandbox tem destinatário fixo e cota limitada; não permite criar templates próprios ou trabalhar com mídia por ID. A parte 9 verificou uma imagem pública específica e somente `disclaimer/en`. Os envios de mídia são por CLI, sem nova rota de API, bloco n8n ou envio automático de cards. Limites de tamanho, janela de atendimento, inventário e fontes estão no [guia de mídias/templates](whatsapp-midias-templates.md).

O caminho secreto e o filtro de telefone não são assinatura HMAC nem prova criptográfica de autoria. O túnel é temporário e exige novo registro quando o hostname mudar. O [guia do sandbox](whatsapp-sandbox.md) explica essa fronteira e a retomada; o [histórico da Meta](whatsapp-teste.md) preserva o provisionamento não resolvido.

## Reproduzir e retomar

Use a raiz do repositório e o ambiente Python preparado. A instalação inicial, Docker/n8n, OAuth e segredos são descritos no [README](../README.md), na [operação local n8n](n8n-local.md) e no [guia do sandbox](whatsapp-sandbox.md#instalar-e-executar).

1. Preserve `.env`, `secrets/`, os quatro bancos e o volume n8n. `D360_API_KEY` pertence ao `.env`; o identificador exato autorizado fica em `secrets/whatsapp-sandbox-security.json`. Nenhum telefone/token precisa aparecer em argumentos, export ou print.
2. Inicie Docker/n8n, API e receptor. Em uma instalação nova, aplique o fluxo próprio e configure o remetente por entrada oculta:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe scripts/setup_whatsapp_sandbox.py --apply
.\.venv\Scripts\python.exe scripts/configure_sandbox_sender.py --apply
```

```powershell
# Terminais separados; reutilize as instâncias já verificadas, se estiverem ativas.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m app.sandbox_webhook
```

3. Para entrada externa, abra o Quick Tunnel somente para `127.0.0.1:8787` e registre a origem nova. O script monta o caminho privado, sem imprimi-lo:

```powershell
.\data\tools\cloudflared.exe tunnel --no-autoupdate --url http://127.0.0.1:8787 --protocol http2
.\.venv\Scripts\python.exe scripts/register_sandbox_webhook.py --origin https://SEU-HOST.trycloudflare.com --apply
```

4. Para conferir imagem/template sem envio, use a prévia da CLI. `--execute` é o passo explícito de envio real e consome cota; mantenha o mesmo UUID para repetir a mesma operação sem reenviar um aceite:

```powershell
.\.venv\Scripts\python.exe scripts/test_whatsapp_media.py --kind image --operation-id <UUID_V4_DA_OPERACAO>
.\.venv\Scripts\python.exe scripts/test_whatsapp_media.py --kind template --operation-id <OUTRO_UUID_V4_DA_OPERACAO>
```

5. Os testes automatizados usam provedores substituídos e bases temporárias. Podem ser reproduzidos sem enviar novas mensagens:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node n8n/tests/present-api-response.test.cjs
node n8n/tests/consulta-eventos.test.cjs
node n8n/tests/integracao-isolada.test.cjs
node n8n/tests/disparo-agendado.test.cjs
node --test n8n/tests/whatsapp-sandbox.test.cjs
```

Encerre os terminais manuais com `Ctrl+C` e use `docker compose stop` para preservar o volume. Processos de segundo plano exigem conferir os PIDs/identidades registrados no runtime privado. Não é preciso recriar segredos ou reinstalar o workflow a cada reinício; o túnel novo exige novo cadastro de origem.

## Validação final

A última suíte aprovada tem **664 testes Python**, incluindo os 51 acrescentados na parte 9: 20 do sender, 18 do ledger e 13 do serviço/CLI. As **502 verificações JavaScript** foram aprovadas nas cinco suítes na parte 8; não foram repetidas na parte 9. O fechamento documental preserva esses resultados e não atribui a uma conferência operacional uma nova execução das suítes.

Foram aprovadas **15 verificações HTTP atuais**, com API, n8n e receptor locais reais: saúde, autenticação ausente/incorreta/correta, comando inválido, claim inexistente/concluído, webhook sem chave, caminho secreto incorreto, remetente proibido e replays de mensagem/status já processados. As linhas das duas bases de saída permaneceram idênticas. **Nenhuma nova mensagem de WhatsApp ou operação Google** foi executada nesse fechamento. A origem pública restrita respondeu 404.

Os **quatro workflows instalados** foram exportados pelo CLI n8n 2.44.4 e comparados à projeção pública sanitizada. Nos dois fluxos ativos, também foi exportada a versão efetivamente publicada com `--published`. Os quatro arquivos públicos testados foram preservados byte a byte, inativos e sem dados fixados ou IDs privados de credenciais. O [manifesto de fechamento](../n8n/whatsapp-closure-manifest.json) registra hashes e alcance; os manifestos anteriores permanecem históricos.

A revisão independente conferiu autorização antes do serviço, rejeição integral de lotes proibidos, status sem resposta, persistência/concorrência, 429/5xx/timeout, corpo inválido ou excessivo e falhas ao persistir o aceite. Não encontrou bloqueador. Os sete hashes da suíte 664 e os 23 arquivos preservados da parte 9 continuam iguais; as suítes completas **não foram repetidas** porque o código não mudou. A agenda segue no schema 2, com as mesmas contagens, e as três bases WhatsApp passaram na verificação de integridade.

O planner foi atualizado e conferido visualmente: etapa 10 concluída pela alternativa 360dialog, etapa 11 de agente de IA como próxima, mantendo as 17 etapas e o painel antes do vídeo. O registro 092 é uma renderização fiel desse PDF, não uma execução nova do sistema. O acervo terminou com **92 registros: 27 imagens e 65 JSONs**, com cópia espelhada e os 90 originais anteriores preservados.

## Matriz de provas

| Registro | Evidência e alcance |
|---|---|
| 080 | Recebimento real inicial: HTTPS → n8n → GET de status autenticado; anterior ao filtro final |
| 081 | Canvas publicado no marco inicial; prova de configuração histórica, complementada pelos resultados HTTP |
| 082 | Implementação da política obrigatória e testes iniciais; preserva o estado anterior ao vínculo exato |
| 083 | Recuperação do túnel, vínculo explícito do identificador e nova mensagem real autorizada |
| 084 | Testes locais da inbox e deduplicação persistente |
| 085 | 22 cenários/23 requisições loopback, concorrência e reinício real; dados fictícios, sem saída WhatsApp |
| 086 | Serviço/API de resposta fixa e validação de 613 testes Python/502 verificações JS naquele marco |
| 087 | Um envio real de resposta, disparado por entrada sintética local; replay sem novo envio e confirmação humana separada |
| 088 | 664 testes Python e ensaios de capacidades de mídia/template |
| 089 | JPEG público de teste, preservado com bytes/hash; não é card contextual ou imagem de IA |
| 090 | Imagem por link e `disclaimer/en` aceitos, replays reutilizados e confirmação humana dos dois itens |
| 091 | `091_fechamento_etapa_10.json`: auditoria final e verificações operacionais, sem novos envios |
| 092 | `092_planner_etapa_10_concluida.png`: render fiel do planner atualizado, sem prova de nova execução |

Os originais locais são privados e têm referências sanitizadas no [índice de evidências](evidencias.md). Exports públicos ficam inativos, com referências de credencial somente por nome. A conferência da instância e dos manifestos pertence ao registro final; um export sozinho não é backup do volume ou da configuração privada.

## Pendências que seguem para o projeto

- Resolver separadamente a emissão original do número de teste Meta, se esse percurso voltar a ser necessário.
- Integrar interpretação da conversa/IA aos serviços de calendário, com seleção e confirmação informadas.
- Construir lembretes persistentes, briefings e cards contextualizados; definir templates próprios e implantação compatível com o uso final.
- Criar o painel web, completar os testes de ponta a ponta do produto e preparar o vídeo de demonstração.

Essas entregas não são tratadas como prontas pelo fechamento da integração de teste.
