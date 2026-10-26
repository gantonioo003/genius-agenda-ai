# Verificação periódica dos lembretes — etapa 12, parte 6

**Marco histórico da parte 6:** este guia registra a versão de quatro nós, ainda sem transporte. O setup abaixo agora instala a versão atual da **parte 8**, com recuperação, planejamento e dispatch em oito nós; para operação atual, consulte [recuperação dos lembretes](recuperacao-lembretes.md). O contrato de `/tick` continua somente local.

O workflow `Genius Agenda AI - Verificação periódica de lembretes` chama a API Python a cada minuto. A API primeiro [sincroniza os avisos acompanhados](sincronizacao-lembretes.md) e depois [gera os próximos planos](geracao-lembretes.md), usando o banco privado existente, schema 1. As alterações são locais: o fluxo não envia WhatsApp, não chama LLM e não escreve eventos no Google.

## Preparação e instalação

O computador precisa permanecer ligado, com Docker Desktop/n8n 2.44.4 e API Python na porta `8000` em execução. A instalação existente deve ter `secrets/n8n-local.json` e a credencial Bearer `Genius API - local`. O acesso Google e a política privada do destinatário são usados pela API; essas credenciais não são importadas no workflow. Consulte a [operação local do n8n](n8n-local.md) para instalação, conta e volume, e a [geração de lembretes](geracao-lembretes.md) para configurar explicitamente o perfil.

A API usa `REMINDER_DATABASE_PATH`, com padrão `data/reminders.sqlite3`; caminhos relativos partem da raiz do projeto. O perfil precisa existir nessa mesma base. Trocar o caminho não transfere configuração ou histórico e não deve ser usado para liberar uma reserva ou tentativa incerta.

Na raiz do projeto, inicie os serviços existentes se estiverem parados; a API fica em seu próprio terminal:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe -B -m app
```

Em outro terminal, instale/publice somente o novo workflow:

```powershell
.\.venv\Scripts\python.exe -B scripts/setup_reminder_schedule.py --apply
```

`--apply` é obrigatório. O setup verifica a versão do n8n, a credencial existente e o ID reservado; importa pelo CLI, publica e reinicia brevemente o n8n, preservando seu volume. Reaplicar reutiliza o próprio ID. O arquivo privado `secrets/reminders-n8n-local.json` contém somente `schema_version: 1`, `workflow_id` e `api_credential_id`; nenhuma credencial é criada, substituída ou exportada. As configurações anteriores ficam preservadas. Não use o setup de consulta normal para importar este workflow.

O [export público](../n8n/workflows/lembretes-periodicos.json) continua com `active: false`, sem ID local de workflow/credencial, `staticData`, dados fixados ou resultados. A credencial aparece somente pelo nome. Na instância validada, o novo fluxo está publicado; consulta e sandbox também permanecem publicados, e as duas fixtures continuam despublicadas. O [manifesto](../n8n/reminders-schedule-manifest.json) registra a comparação dos grafos.

## Contrato do pulso

| Item | Valor |
|---|---|
| Método e rota | `POST /api/v1/reminders/tick` |
| Destino do n8n | `http://host.docker.internal:8000/api/v1/reminders/tick` |
| Autenticação | Bearer da credencial existente `Genius API - local` |
| Corpo | Exatamente `{}`, sem parâmetros de consulta |
| Content-Type | `application/json` |
| Correlação | `X-Request-ID: reminder-tick-<execution_id>`, caracteres seguros e até 128 posições |
| Disparo | Schedule Trigger de 1 minuto, fuso `America/Fortaleza` |
| Limites n8n | HTTP 120.000 ms; workflow 150 s; sem retries ou redirecionamentos |

O request ID serve para correlação, não como chave de idempotência dos avisos. A identidade persistente dos lembretes continua sendo a definida pelo [ledger](persistencia-lembretes.md). A API autentica antes de analisar o corpo; corpo não vazio, outro tipo JSON, consulta ou correlação inválida são recusados com 422, sem reproduzir a entrada. Falta de autenticação recebe 401. Não é necessário expor um novo webhook, túnel ou porta pública para este fluxo.

O envelope v1 tem nove campos: `api_version`, `request_id`, `operation_id`, `status`, `action`, `mutation_state`, `message`, `data` e `error`. A ação é `reminder_tick`, `operation_id` é nulo e `mutation_state` é `not_applicable`, pois não há mutação externa. `data` contém apenas `tick_status`, `skip_reason`, `local_mutation_state`, os resumos `synchronization`/`generation`, `messages_sent: 0` e `external_mutations: 0`.

| Resposta | Significado |
|---|---|
| HTTP 200, `completed` | Sincronização `synchronized` e geração `planned`; ambas as fases concluíram |
| HTTP 200, `skipped` | Perfil ausente (`not_configured`), desligado (`disabled`), sem permissão (`permission_required`) ou outro tick em andamento (`busy`); fases `not_run` e contagens zero |
| HTTP 503, `failed` | Não foi possível concluir a sincronização ou uma preparação anterior; não confirma fase local concluída |
| HTTP 503, `partial` | Ao menos a sincronização concluiu antes de uma falha posterior; seus resultados permanecem |

Os skips de perfil acontecem antes de carregar o vínculo privado do WhatsApp ou abrir o cliente Google. O fluxo não habilita perfil nem concede permissão. Um perfil ativo deve continuar vinculado à agenda e à política privada autorizadas; vínculo divergente é falha, não skip ou autorização para outro destino.

`local_mutation_state: applied` confirma a conclusão das fases locais, inclusive quando nenhuma linha foi inserida ou alterada. `partial` conserva ao menos a sincronização concluída; `not_applied` acompanha skip/falha sem fase concluída. As contagens de avisos considerados e os estados descrevem os lotes, não novas inserções ou mensagens enviadas.

## Ordem, concorrência e falhas

Cada tick adquire um lock de SO não bloqueante associado ao caminho canônico da base. Ele é compartilhado por ticks em processos distintos da API: enquanto um trabalha, outro recebe `busy`. O lock dura até o trabalho real terminar, inclusive quando o n8n deixa de aguardar a resposta. Não há TTL, remoção do arquivo de lock ou cancelamento por timeout que autorize sobreposição; o fechamento do descritor, inclusive pelo SO após interrupção do processo, libera a exclusão. Isso não substitui as transações e verificações de revisão já existentes no SQLite.

A sincronização precede a geração para reconciliar os eventos já acompanhados antes de descobrir novos planos. As duas fases usam commits independentes; não constituem uma única transação. Se a geração falhar, a sincronização já confirmada permanece e a API retorna 503 `partial`, sem inventar rollback. Se o encerramento do cliente ou do lock falhar depois da geração, a resposta também é `partial`, conservando **sincronização `synchronized` e geração `planned`**, com seus contadores.

Não há retry interno do pulso nem loop de retentativas no HTTP. O próximo minuto inicia uma nova conferência e o ledger conserva a proteção das identidades/estados. Os limites do n8n não são um prazo global do trabalho síncrono na API e não liberam reservas ou envios incertos por tempo decorrido. Estados `sending`, `accepted` e `uncertain` continuam protegidos.

O normalizador exige correlação exata, chaves/enums esperados e contadores inteiros não negativos. Aceita somente os oito estados conhecidos do ledger; descarta mensagem e cabeçalhos da API. HTTP 503, erro de transporte ou DTO incompatível encerra a execução com um erro fixo, sem propagar payload bruto. Nenhum `completed` é fabricado para falha ou skip.

## Operação e privacidade

O n8n é a única fonte periódica desta parte; nenhum timer Python foi iniciado. PC suspenso, Docker parado ou API indisponível interrompem a verificação. Ao reiniciar os serviços e manter o workflow publicado, os próximos pulsos voltam a conferir o estado atual; isso não autoriza envio retroativo. Para parar somente esta rotina, despublique o workflow de lembretes na instância. Preserve o banco, suas configurações e o volume do n8n.

O workflow não salva dados de execuções de sucesso, erro, manuais ou progresso e não usa `staticData`. Seus nós transportam correlação e agregados, sem IDs de eventos, títulos, telefone, token ou corpo de mensagem. Logs técnicos podem comprovar modo, horário e conclusão dos nós sem conservar suas saídas. O banco privado continua guardando planos e histórico local; o helper Google também pode renovar OAuth ou atualizar metadados locais da agenda, como nas partes anteriores.

O transporte fica para a **parte 7**: será necessário reler evento, relógio, perfil e canal antes de enviar, respeitar janela/template e registrar aceite/incerteza. A execução periódica atual não comprova entrega, leitura ou envio de um lembrete.

## Verificação de 25/10/2026

Passaram **1.778 testes Python**, incluindo **35 novos**, e **719 cenários JavaScript**, incluindo **179 novos** dos lembretes. Os testes cobrem autenticação/corpo, ordem das fases, skips, lock entre processos, falhas/cleanup, resultados parciais, projeção segura, grafo, credenciais nomeadas e setup simulado. Para repetir as verificações específicas:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_reminder_tick*.py" -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_setup_reminder_schedule.py -v
node --test n8n/tests/lembretes-periodicos.test.cjs
```

O preflight autenticado da API retornou HTTP 200 `completed`, com sincronização/geração concluídas e contagens zero, `messages_sent: 0` e `external_mutations: 0`. Esse resultado ocioso não demonstra criação, cancelamento ou reagendamento de um aviso real.

Separadamente, o log técnico do n8n registrou disparo **natural em 25/10/2026 às 20:44:20.699 -03:00**, modo `trigger`, sem execução manual, com os nós HTTP e normalizador concluídos. As saídas dessa execução não foram retidas: a conclusão do normalizador prova somente que a API devolveu um DTO válido `completed` **ou** `skipped`. Não atribuir contagens, novos planos ou entregas a esse disparo.

As quatro versões instaladas anteriores e as duas versões publicadas anteriores foram preservadas, junto às credenciais/configurações privadas. O novo grafo instalado e o publicado correspondem ao export público inativo. Os registros técnicos `verification.json`, `runtime-api.json` e `runtime-n8n.json` fundamentam essas verificações e são consolidados no [índice de evidências](evidencias.md). O manifesto público contém a comparação e os hashes dos exports, sem segredos. Não foi criada captura de interface: os testes e logs técnicos constituem a evidência desta entrega de backend.
