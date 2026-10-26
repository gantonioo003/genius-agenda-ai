# Deduplicação da entrada WhatsApp

**Parte 7 concluída:** a entrada do sandbox registra identidades de eventos em SQLite antes de encaminhá-los ao n8n. Repetir um evento concluído confirma o recebimento sem nova chamada ao fluxo ou novo recibo JSONL. A parte 8 acrescentou posteriormente uma resposta fixa com outbox própria; o [guia de respostas](whatsapp-respostas.md) registra sua implementação e o resultado do ensaio real. A integração de teste foi encerrada depois, conforme o [fechamento da etapa 10](fechamento-etapa-10.md).

Na validação da parte 7, o percurso era metadados → n8n → `GET /api/v1/status`, sem saída pelo WhatsApp. Desde a parte 8, o fluxo faz POST autenticado de resposta: a API consulta o claim `processing`, envia uma resposta fixa por claim com mensagens novas ou ignora somente status. A inbox e suas regras descritas aqui permanecem; a outbox é separada. A configuração e o recebimento real anteriores estão no [guia do sandbox](whatsapp-sandbox.md). Nenhum desses passos opera o Google Calendar.

## Identidade e privacidade

[`app/whatsapp_inbox.py`](../app/whatsapp_inbox.py) calcula uma chave SHA-256 com o namespace `genius-agenda:360dialog:whatsapp-inbox:v1`. A composição é canônica e separa mensagens de status:

| Evento | Identidade usada | Resultado ao repetir |
|---|---|---|
| Mensagem | Namespace + categoria `message` + ID do provedor | Mesmo ID é a mesma mensagem, mesmo que texto, tipo ou timestamp mudem |
| Status | Namespace + categoria `status` + ID do provedor + fase, como `status_read` | Mesmo ID e fase são repetição; mudar somente o timestamp não cria evento novo |
| Mensagem e status com o mesmo ID | Categorias diferentes | São identidades independentes |
| Mesma mensagem de texto com outro ID | ID diferente | É mensagem nova |

As fases de status podem chegar fora de ordem: `read` antes de `delivered` não é rejeitado por isso. Cada fase é deduplicada separadamente; este módulo não calcula um estado final de entrega.

O banco guarda somente o hash, o tipo limitado do evento, um UUID técnico de processamento, o estado e os horários técnicos UTC `created_at` e `updated_at`. Não guarda o ID original, o timestamp informado pelo provedor, telefone, conteúdo, token ou corpo do callback. Hashes persistentes são **pseudônimos, não dados anônimos**: continuam permitindo correlacionar repetições e exigem proteção e backup privados.

O caminho secreto, o esquema de **todo** o lote e todos os telefones são verificados antes de derivar hashes ou reservar eventos na inbox. Um evento proibido rejeita o lote inteiro com 403, mesmo se seu ID já tiver sido processado. O filtro de remetente permanece por igualdade exata; a deduplicação não acrescenta assinatura ou prova criptográfica de autoria.

## Banco separado e inicialização

A inbox usa `data/whatsapp-inbox.sqlite3`, **schema 1**, separado de `data/agenda.db`. O banco da agenda foi preservado com schema 2; esta entrega não migra suas tabelas ou operações.

| Tabela da inbox | Conteúdo |
|---|---|
| `inbox_metadata` | Versão do esquema próprio |
| `inbox_events` | `event_key`, `event_type`, `request_id`, `state`, `created_at`, `updated_at` |

Importar o módulo ou chamar a factory não abre arquivos. Ao iniciar o receptor, seu lifespan inicializa e verifica a inbox antes de aceitar tráfego. Uma base corrompida, de outro sistema ou com versão/esquema incompatível impede a inicialização; falhas de acesso durante uma requisição retornam 503 sem encaminhar o evento. O módulo usa apenas bibliotecas padrão do Python para persistência e transações.

Não existe carga retroativa dos recibos anteriores. Eles não contêm os IDs originais necessários para reconstruir as chaves; a proteção começa nos eventos recebidos com esta versão em execução.

## Estados e comportamento HTTP

O claim usa uma transação SQLite para reservar as identidades novas em `processing` **antes** da chamada ao n8n. A chave primária e a transação impedem que instâncias concorrentes reservem o mesmo evento como novo.

| Situação do lote | Encaminhamento | Resposta externa |
|---|---|---|
| Só eventos novos | Encaminha um resumo dos eventos únicos novos | 200 após confirmação compatível do n8n e conclusão no banco |
| Todos já `processed` | Nenhum; não acrescenta recibo JSONL | 200 `{"status":"received"}` |
| `processed` misturado com eventos novos | Encaminha somente os novos; contagens e tipos excluem os repetidos | 200 após concluir os novos |
| Qualquer evento em `processing` ou `uncertain` | Bloqueia todo o lote e não insere as identidades novas daquele lote | 503 `{"status":"unavailable"}` |
| Falha ao reservar na inbox | Nenhum | 503 seguro |
| Falha, timeout ou retorno incompatível do n8n | Marca o claim como `uncertain`; se a gravação falhar, mantém `processing` | 503 seguro |
| Falha ao concluir o claim no banco | Tenta registrar incerteza e mantém o bloqueio | 503 seguro |
| Banco concluído, mas falha ao gravar o JSONL auxiliar | Mantém `processed` e registra `receiptfail` quando possível | 200; repetir não encaminha novamente |

O UUID enviado no contrato de cinco campos é o do claim novo. Eventos duplicados dentro do próprio lote também entram uma única vez. O contrato de entrada de cinco campos não mudou. Na parte 8, o retorno de sucesso do n8n ganhou `reply_state: accepted|skipped`, além de `status`, `api_reachable` e `request_id`; o receptor exige o estado compatível antes de concluir a inbox.

`complete()` acontece antes do recibo JSONL: **SQLite é a referência de processamento; o JSONL é evidência auxiliar**. Diagnósticos registram somente resultados, contagens e indicadores limitados, sem o hash ou o ID original.

## Falhas, reinício e recuperação

Reiniciar o receptor preserva a deduplicação quando a mesma inbox é mantida. Também preserva bloqueios: se o processo cair depois de reservar e antes de concluir, o evento continua `processing`, inclusive quando a chamada externa ainda não chegou a acontecer.

Não há expiração por tempo, lease, liberação de pendências ou reexecução automática. O comportamento prioriza impedir a repetição de um efeito cujo resultado não foi confirmado. **Não é garantia de execução exatamente uma vez**: o serviço externo e a inbox não compartilham uma transação.

Para investigar um claim pendente, confira o resultado no destino antes de decidir qualquer recuperação. Preserve um backup consistente da inbox e dos registros técnicos, com a entrada interrompida ou usando a API de backup do SQLite. A recuperação exige decisão explícita; não foi criado um comando para reabrir claims nesta parte. Não apague a base, não altere IDs para contornar o bloqueio e não substitua a inbox por uma cópia vazia: isso remove a memória das repetições. Preserve também o banco da agenda e seus backups separadamente.

## Integração com a outbox da parte 8

A API consulta o claim pela inbox, sem criar ou reabrir eventos a partir do UUID recebido. Só um claim íntegro em `processing` pode seguir. A resposta fixa é reservada por UUID em `data/whatsapp-outbox.sqlite3`, schema 1, com estados `sending`, `accepted` e `uncertain`. Somente status não reserva envio.

Um aceite da outbox pode ser reutilizado sem nova chamada ao provedor enquanto o claim continua ativo. Depois de a inbox virar `processed`, sua repetição é confirmada antes de chegar ao n8n/API. Se a resposta externa se perder, as duas bases podem indicar aceite de saída e entrada incerta; a recuperação exige reconciliação explícita e não libera replay automático. `accepted` não equivale a entregue/lido. Preserve inbox, outbox e agenda em backups consistentes.

Na parte 8 passaram **613 testes Python e 502 verificações JavaScript**, com as cinco suítes JS executadas. O [guia de respostas](whatsapp-respostas.md) separa esse resultado atual e o ensaio real dos marcos históricos abaixo.

## Testes e evidências — marco da parte 7 (23/10/2026)

A suíte completa passou com **551 testes Python**: os 499 anteriores mais 18 casos no receptor e 34 testes do novo store. O conjunto inclui **61 testes do receptor**, **34 do store** e **7 do configurador de remetente**. As **470 verificações JavaScript** são o resultado histórico anterior, preservado sem nova execução nesta entrega.

A prova de integração executou **22 cenários em 23 requisições HTTP loopback**, incluindo duas requisições concorrentes. O destino ficou travado em `http://127.0.0.1:8787`, com n8n e API Python locais reais. Foram usados IDs e dados fictícios; o telefone privado autorizado foi usado somente em memória no percurso local. **Não foi um reenvio do WhatsApp ou do provedor, nem um teste de deduplicação através do HTTPS público.**

As nove identidades novas resultaram em nove encaminhamentos/consultas de status e nove recibos. Repetições dos mesmos IDs não aumentaram esses totais. Um reinício real do receptor separou os 17 primeiros cenários dos cinco finais: as repetições continuaram sem novo encaminhamento e um ID novo continuou sendo aceito. A conferência não encontrou IDs originais, telefone ou texto no banco, journals, recibos ou diagnósticos. Não houve mensagem de saída nem operação de agenda.

| Evidência | Conteúdo |
|---|---|
| `084_whatsapp_deduplicacao_testes.json` | Implementação, estados e resultado dos testes locais |
| `085_whatsapp_deduplicacao_http_local.json` | Ensaio HTTP loopback, concorrência, reinício real e conferência dos dados persistidos |

O primeiro ensaio proposto pelo HTTPS público com o telefone privado foi rejeitado pela revisão automática e não executado. A alternativa foi o ensaio restrito ao loopback descrito acima. Os registros 080–083 continuam sendo provas históricas do recebimento real anterior, sem atribuir a eles a nova deduplicação. O canvas n8n não mudou; não foi criada uma captura repetida. Veja o [índice de evidências](evidencias.md).

Para reproduzir os testes com dados sintéticos, na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_whatsapp_inbox.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_sandbox_webhook.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_configure_sandbox_sender.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Esses testes isolam suas bases temporárias e simulam chamadas externas. Não envie outra mensagem pelo aplicativo para testar uma repetição de ID: um envio novo recebe outro identificador e deve ser aceito como evento novo.
