# Primeiro fluxo n8n — consulta da agenda

Etapa 09, parte 7. O fluxo `Genius Agenda AI - Consulta de teste` recebe um pedido JSON, consulta a API Python e devolve os eventos e o código HTTP. Foi publicado e conferido localmente em **23/10/2026**.

```text
Webhook POST → validar consulta → GET na API Python → responder JSON
                    └ inválida: 422       └ falha de conexão: 502 seguro
```

O pedido de teste é estruturado. Interpretação por IA, WhatsApp, fluxos n8n de escrita sobre Google real e lembretes entram nas próximas etapas. A fixture de escrita isolada foi validada na parte 9; veja o [fechamento da etapa 09](fechamento-etapa-09.md).

## Arquivos

| Arquivo | Uso |
|---|---|
| [`n8n/workflows/consulta-eventos.json`](../n8n/workflows/consulta-eventos.json) | Export importável, inativo e sem credenciais ou dados de eventos |
| [`scripts/setup_n8n.py`](../scripts/setup_n8n.py) | Importa as credenciais privadas, importa/publica o fluxo e reinicia somente o contêiner local |
| [`scripts/test_n8n.py`](../scripts/test_n8n.py) | Envia uma consulta sem mostrar a chave no terminal ou nos argumentos |
| [`n8n/tests/consulta-eventos.test.cjs`](../n8n/tests/consulta-eventos.test.cjs) | Exercita a validação, as respostas e as proteções do export, sem rede |
| `secrets/n8n-local.json` | Identificadores locais e chave de entrada do webhook; privado e ignorado pelo Git |

## Preparar e executar

Pré-requisitos: ambiente Python e OAuth configurados conforme o [guia inicial](configuracao.md), `API_AUTH_TOKEN` no `.env`, Docker Desktop ligado e usuário local do n8n criado. A imagem usada é a `2.44.4` fixada no Compose.

No terminal, na raiz do repositório:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe scripts/setup_n8n.py --apply
```

O comando usa a CLI oficial dentro do contêiner `genius-agenda-ai-n8n`. Atribui os registros ao projeto pessoal do administrador existente; não cria outra conta. Importações posteriores reutilizam os IDs de `secrets/n8n-local.json`, atualizando somente este fluxo e suas duas credenciais. Isso substitui edições feitas manualmente neste fluxo: salve alterações no export antes de reaplicar. Preserve o arquivo privado para evitar criar outra cópia ao configurar novamente.

As chaves passam pela entrada padrão do processo, sem aparecer nos argumentos ou na saída. O arquivo temporário de importação tem modo `0600` e é removido ao final. O n8n armazena as credenciais em seu gerenciador, cifradas com a chave da instância no volume Docker. Não exporte credenciais nem publique o volume.

Em outro terminal, mantenha a API ligada:

```powershell
.\.venv\Scripts\python.exe -m app
```

Depois envie uma consulta:

```powershell
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23 --period week
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23 --period month
```

A data acima é a utilizada na conferência real. Escolha outra com `--date AAAA-MM-DD`; o comando imprime dados da agenda, portanto não publique sua saída sem revisão.

## Pedido e resposta

Endereço local: `POST http://localhost:5678/webhook/genius-agenda/consultar`. Autenticação de entrada: header `X-Genius-Webhook-Key`, cujo valor é lido de `secrets/n8n-local.json` pelo comando de teste.

```json
{
  "date": "2026-10-23",
  "period": "day",
  "time_zone": "America/Recife"
}
```

`date` é obrigatória. `period` aceita `day`, `week` ou `month`; se ausente, usa `day`. Se `time_zone` estiver ausente, usa `America/Recife`. Vazio, `null`, tipo incorreto, data inexistente, query inesperada ou campo desconhecido são recusados. A API valida o fuso e os limites civis finais.

O webhook envia apenas os três filtros para `GET http://host.docker.internal:8000/api/v1/events`, com a credencial Bearer própria da API e `X-Request-ID: n8n-<id da execução>`. A chave recebida pelo webhook não é encaminhada ao Python. URL, método, rota e agenda não podem ser escolhidos pelo pedido.

| Resultado | HTTP |
|---|---|
| Consulta concluída, inclusive lista vazia | `200`, envelope original da API |
| Chave do webhook ausente/incorreta | `403` em texto, recusa nativa do n8n antes do fluxo |
| Pedido inválido | `422` seguro, sem repetir valores recebidos |
| API recusa sua credencial ou está sem chave configurada | `401` ou `503`, envelope e status preservados |
| Falha operacional estruturada da API | `502`/`503`, envelope e status preservados |
| Sem conexão, timeout ou falha de transporte | `502`, `api_unavailable`, sem erro bruto |
| Resposta fora do envelope esperado | `502`, `invalid_api_response` |

As respostas produzidas pelo fluxo têm `Cache-Control: no-store` e `X-Request-ID`. Retentativas e redirecionamentos estão desativados; o timeout HTTP é de 15 segundos e o limite do fluxo de 30 segundos. O normalizador confere o envelope mínimo, não todo o DTO de eventos; esse contrato continua sob responsabilidade do Python.

O caminho `/webhook/` funciona com o fluxo publicado e os serviços ligados, sem clicar em Execute. `/webhook-test/` é outro endereço: depende de o editor estar escutando um teste. Não trocar um pelo outro.

## Configuração privada e operação local

O Python permanece em `127.0.0.1:8000`. Nesta instalação, o Docker Desktop alcançou essa porta por `host.docker.internal`; não foi necessário mudar para `0.0.0.0`. Essa comunicação deve ser conferida novamente em outra máquina, especialmente Linux; não é uma configuração de implantação pública. n8n e API precisam estar em execução.

O export desabilita salvamento de execuções de sucesso, erro, manuais e progresso, e não contém dados fixados. Na versão utilizada, o n8n ainda cria registros técnicos e entrada transitória, com exclusão lógica até a limpeza automática. Portanto, isso não significa ausência imediata de dados no disco. Trate todo o volume como privado e restrinja seu acesso. As evidências selecionadas ficam separadas, em `data/evidencias/`.

Para parar sem apagar a configuração, use `docker compose stop` e encerre a API. Para continuar, inicie ambos; não precisa importar o fluxo novamente. Não use `docker compose down -v` para uma parada comum.

## Conferência realizada

Em 23/10/2026, **18 verificações HTTP pelo n8n real** aprovaram consultas por dia/semana/mês, defaults, lista vazia, autenticação do webhook, entradas inválidas, erro de fuso da API, indisponibilidade e preservação de `401`/`503`. Para os dois últimos códigos, processos Python temporários usaram uma chave de teste diferente/ausente; a configuração real foi preservada e a API normal restaurada ao final.

As consultas válidas alcançaram o Google real. Os três eventos de `Genius Testes` permaneceram idênticos antes/depois; o fluxo possui somente GET para o Python. Não houve instrumentação de contagem do transporte Google nesta parte. O teste de indisponibilidade desligou somente o processo da API iniciado para esta tarefa e conferiu a recuperação posterior.

**80 cenários JavaScript locais** verificaram os filtros, as datas, a passagem de erros, a sanitização e a configuração do export:

```powershell
node n8n/tests/consulta-eventos.test.cjs
```

Esses testes não abrem o Docker nem acessam o Google. A suíte Python permanece em **444 testes previamente aprovados**; os arquivos da aplicação foram conferidos por hash, sem nova rodada por não haver mudança no Python da aplicação. Evidências: **060** (HTTP/Google e falhas locais), **061** (canvas publicado) e **062** (revisão local).

## Referências

- [CLI do n8n](https://docs.n8n.io/hosting/cli-commands/)
- [HTTP Request](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/)
- [Respond to Webhook](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook/)

## Atualização — respostas e falhas, parte 8

O webhook agora preserva os nove campos da API e acrescenta `presentation` com mensagem, tipo e próximo passo. Todos os caminhos passam pelo apresentador compartilhado; o alvo GET e a proteção privada continuam iguais. O comando `scripts/test_n8n.py` aceita `--text-only`. A suíte atual tem 215 cenários do apresentador e 82 do fluxo; os 80 cenários citados acima pertencem ao marco da parte 7. Na parte 8, 19 verificações HTTP foram aprovadas. Consulte o [tratamento de respostas e seus limites](n8n-respostas-falhas.md).

## Atualização — ensaio integrado, parte 9

O [ensaio de integração](n8n-testes-integracao.md) exercitou as operações pelo n8n/API/SDK reais com Google em memória e comprovou um disparo natural de status. As fixtures são separadas e ficam inativas ao final; este fluxo de consultas continua ligado à API normal e ao Google real somente para leitura.
