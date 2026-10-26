# Falhas e recuperação — etapa 08, parte 6

Uma resposta perdida não informa se o Google aplicou a operação. O conector separa falha antes da gravação de resultado incerto após uma tentativa e consulta o estado antes de permitir qualquer conclusão. Esta proteção está implementada nos serviços e comandos locais; a integração com WhatsApp e as rotas HTTP de operações da agenda permanecem pendentes.

## Política de execução

1. Validar o pedido e resolver a agenda própria de teste. Erros de dados, autenticação, banco ou leitura inicial interrompem o fluxo antes do envio da mutação.
2. Enviar o POST, PATCH ou DELETE uma única vez, com `execute(num_retries=0)`. Atualizações e exclusões preservam o controle de versão por ETag/`If-Match`.
3. Se houver falha de rede, HTTP 408/429/5xx, falha de decodificação ou resposta de gravação malformada, consultar o mesmo ID na mesma agenda. Não enviar outra gravação dentro da recuperação.
4. Confirmar o resultado somente se a leitura demonstrar o estado esperado. Leitura divergente, incompleta ou também indisponível mantém o resultado incerto e produz erro operacional seguro.

Reagendamento, edição de informações e exclusão já realizam uma leitura final mesmo quando a resposta da escrita é normal. Na criação, a resposta normal precisa confirmar o evento; se for insuficiente, a recuperação faz uma leitura pelo ID estável.

## O que cada operação confirma

| Operação | Condição para confirmar a recuperação |
|---|---|
| Criar | ID esperado, estado `confirmed`, dados equivalentes ao pedido, fingerprint privada correspondente e link válido. O registro local passa para `confirmed` somente após essa conferência. |
| Reagendar | ID exato, novos instantes de início e fim equivalentes em UTC e transparência preservada. |
| Editar informações | ID exato, campos solicitados com os novos valores, demais informações preservadas e datas, horários, fuso e condição de dia inteiro preservados. |
| Excluir | Consulta posterior retorna HTTP 404/410 ou tombstone `cancelled` com o ID exato. Um evento ainda ativo ou um tombstone de outro ID impede confirmar a exclusão. |

Na criação, o registro durável e os vínculos de conteúdo da [parte 5](duplicacoes.md) preservam a identidade entre execuções. Se tanto a resposta de criação quanto a consulta de recuperação falharem, a reserva continua pendente; a próxima execução consulta o ID original antes de considerar novo envio. Um pedido anteriormente confirmado cujo evento foi removido não é recriado automaticamente.

## Campos de resultado

`CreatedInterview`, `RescheduleResult`, `DetailsResult` e `DeletionResult` incluem `recovered`, exposto também no JSON de sucesso dos comandos:

- `recovered: true`: a resposta da tentativa de escrita foi incerta ou malformada, mas a consulta posterior confirmou o resultado esperado.
- `recovered: false`: o resultado seguiu o fluxo normal, inclusive prévia, ausência já constatada ou operação sem alteração.

Na criação, `reused` é independente: indica que um evento existente foi reaproveitado. Uma primeira criação recuperada após timeout pode retornar `recovered: true` e `reused: false`; uma execução posterior que encontra esse evento pode retornar `reused: true` e `recovered: false`. Nenhum desses campos representa uma nova gravação automática.

## Respostas que exigem outra ação

| Situação | Comportamento implementado |
|---|---|
| HTTP 412 | A versão mudou. Interromper e consultar novamente; não atualizar o ETag e repetir a escrita, nem transformar a resposta em sucesso por recuperação automática. |
| HTTP 401 | Interromper a operação. Conferir a autorização e renová-la quando necessário. |
| HTTP 403 | Conferir permissões e configuração. O SDK pode repetir leituras quando o motivo específico for limite de taxa; não há reenvio automático da escrita. |
| HTTP 429 ou 5xx em leitura | O SDK recebe `num_retries=2`: até duas retentativas, além da tentativa inicial, com backoff. Uma leitura ainda falha não confirma nenhum estado. |
| HTTP 408/429/5xx após tentativa de escrita | Consultar o estado pelo ID; não repetir POST/PATCH/DELETE. |
| Resposta com ID errado, página inválida ou paginação inconsistente | Interromper. Não tratar como evento ausente, lista vazia ou horário disponível. |

A classificação de erros e a recomendação de backoff seguem a [documentação oficial do Google Calendar](https://developers.google.com/workspace/calendar/api/guides/errors). O projeto aplica uma política própria mais conservadora para escritas: conferir o estado antes de decidir os próximos passos.

## Transporte, autorização e encerramento

`app/google_transport.py` fornece `SingleAttemptHttp`, compatível com o SDK por meio de `httplib2.Response`. Usa `requests.Session` com `HTTPAdapter(max_retries=0)` em HTTP e HTTPS, uma chamada de rede por tentativa explícita, `allow_redirects=False` e verificação TLS habilitada. Assim, reconexões ou redirecionamentos do transporte não repetem silenciosamente uma gravação nem encaminham seu corpo e autorização para outro destino.

O timeout de conexão/leitura é de 20 segundos por tentativa; não representa um prazo global para toda a operação ou suas leituras. A renovação explícita de credenciais também recebe timeout de 20 segundos. `AuthorizedHttp` é configurado com `max_refresh_attempts=0`, evitando o reenvio da requisição original após resposta de autenticação. A possibilidade de atualização e repetição por essa camada está descrita na [referência oficial de `AuthorizedHttp`](https://googleapis.dev/python/google-auth-httplib2/latest/google_auth_httplib2.html).

As respostas e sessões são fechadas ao final do uso. Uma falha de conexão no encerramento do cliente registra aviso seguro e preserva o resultado já obtido; não torna uma operação confirmada em falha que incentive novo envio. Erros de transporte apresentados ao usuário não incluem URL, token, headers nem corpo da requisição.

## Conferir um resultado incerto

Na raiz do repositório, substitua o marcador pelo ID completo registrado para a operação:

```powershell
.\.venv\Scripts\python.exe -m app.select_event --event-id "ID_COMPLETO_DO_EVENTO" --timezone America/Recife
```

Esse comando consulta a agenda de teste configurada e não cria, altera ou exclui eventos. A resolução da agenda pode atualizar metadados locais. Confira o ID, a agenda e os dados retornados; se a consulta falhar, o resultado continua sem confirmação. Não execute um comando de criação para fazer essa verificação.

Os comandos operacionais tratam falhas de autenticação, rede e armazenamento local com mensagem segura em `stderr` e saída 1, sem JSON de sucesso em `stdout`. Erros de sintaxe dos argumentos continuam sendo responsabilidade do `argparse`, com saída 2. Ausência validada ou falta de informação pode ser um resultado normal da seleção; falha técnica não é convertida em ausência.

## Verificação e limites

Os testes locais simulam escrita aceita com resposta perdida, confirmação por leitura, estado divergente, falha na própria recuperação, dados malformados, controle de versão e interrupção antes da gravação. Os testes do transporte exercitam o SDK para confirmar uma tentativa de mutação e retentativas explícitas de leitura, sem rede real.

Não há fila automática persistida de retentativas para PATCH e DELETE. A proteção não fornece transação atômica entre SQLite e Google Calendar, nem impede que outra aplicação altere o evento antes ou depois da conferência. Uma resposta incerta permanece identificada como tal quando faltam evidências; o sistema não promete entrega ou alteração confirmada apenas porque enviou a requisição.


## Verificação em 22/10/2026

A suíte completa passou com **351 testes**, na preparação isolada e no ambiente do repositório. São os 264 anteriores e **87 novos**: recuperação das escritas (28), cliente/autorização/leituras (31), transporte (16) e fronteiras de erro das CLIs (12). Os testes usam HTTP e provedores simulados, além de SQLite temporário; não acessam a conta Google. O registro local **040** preserva a saída da suíte e sua identificação.

Também validamos as quatro operações em `Genius Testes`, com uma entrevista temporária e sem convidados. **As chamadas ao Google foram reais; a perda da resposta foi injetada localmente depois de receber HTTP 2xx. Não houve indisponibilidade real do Google.** O observador recusaria uma segunda escrita na mesma fase.

| Operação real | Estado confirmado após a perda local da resposta |
|---|---|
| Criar | Entrevista temporária em 23/10, 17h–17h30, com ID e conteúdo conferidos |
| Reagendar | Mesmo evento movido para 17h30–18h, com ocupação preservada |
| Editar | Título, descrição e local alterados, com horários preservados |
| Excluir | Mesmo evento removido após conferir sua prévia; ausência relida pelo ID |

Cada operação retornou **`recovered: true`**, após **uma única tentativa de escrita** e leitura de conferência. Os registros **041, 042, 043 e 045** são evidências híbridas identificadas como tal: operação real com falha local controlada. Não representam quatro incidentes reais do serviço.

A captura **044** mostra o evento após o reagendamento e a edição, antes de sua remoção. Ela comprova apenas os dados visíveis; os JSONs demonstram a resposta perdida e a recuperação. O registro **046** é uma leitura real final sem injeção: a entrevista temporária está ausente e a agenda voltou aos mesmos dois eventos anteriores, A e B. A imagem 044 permanece como histórico desse teste, e não como estado atual da agenda.

**Parte 6 concluída.** A parte 7, [testes do conjunto](testes-conjunto.md), também foi concluída, com 359 testes aprovados. **Etapa 08 concluída em 22/10/2026**, com o código da suíte aprovada conferido por hashes. Consulte o [fechamento](fechamento-etapa-08.md). Próxima: **etapa 09 — operações na API e fluxos n8n**.
