# Persistência dos lembretes — etapa 12, parte 3

O banco próprio `data/reminders.sqlite3`, schema 1, conserva configuração, avisos, transições e observações do canal. A biblioteca está em `app/reminder_store.py`; a inicialização explícita está em `app/init_reminders.py`. Esta entrega não consulta o Google, não gera avisos automaticamente e não envia mensagens. As próximas partes conectarão esses serviços e o n8n.

## Inicialização e configuração

Na raiz do projeto, usando o ambiente Python já instalado:

```powershell
.\.venv\Scripts\python.exe -B -m app.init_reminders
```

O comando cria uma base vazia e reconhecida, ou confere a existente. Não habilita lembretes, não infere consentimento e não substitui banco alheio ou de versão desconhecida. `--database` permite escolher outro arquivo privado. O JSON informa somente prontidão, versão e existência de configuração; não mostra caminho, evento ou destinatário. Importar o módulo ou construir um `ReminderStore` não abre o banco.

A configuração é um único perfil explícito para agenda e vínculo privado do destinatário. Ausência de perfil mantém o recurso sem configuração; `configure()` usa `enabled=False` e `permission_granted=False` por padrão. Os intervalos são `day_before=None`, `preparation=60` e `imminent=15`, conforme [regras da parte 1](regras-lembretes.md). Não há variável `.env`, rota ou configuração pelo LLM criada nesta parte.

Criação exige `expected_revision=None`. Alteração exige a revisão atual: uma escrita antiga não pode sobrescrever configuração mais recente. Os três slots são explícitos, com inteiro estrito de 1 a 10080 minutos ou `None`, únicos e em ordem decrescente. O retorno contém offsets em tupla imutável. Permissão é uma configuração de operação confiável; não deriva do `START` do sandbox nem de uma afirmação gerada pelo modelo.

## Dados privados e identidade

| Tabela | Conteúdo |
|---|---|
| `reminder_metadata` | Tipo do banco e versão do schema |
| `reminder_config` | Perfil único, agenda, vínculo, intervalos, habilitação, permissão e revisão |
| `reminders` | Evento/ocorrência, início UTC, horário/prazo do aviso, tipo, título mínimo, fuso, revisão, estado e tentativa |
| `reminder_history` | Transições ordenadas, motivo controlado, tentativa e horário; sem método de apagar histórico |
| `channel_observations` | Primeiro registro por hash do ID de entrada, vínculo, instante original válido ou rejeição, recebido-em e proveniência |

Cada aviso tem identidade estável derivada de **agenda + ID exato da ocorrência + início UTC canônico + slot + vínculo do destinatário**. A mesma combinação tem restrição `UNIQUE` no SQLite, além da chave de hash. Alterar título, fuso de apresentação, offset ou revisão não cria outra identidade. O início equivalente em outro offset UTC também não duplica o aviso.

O banco é privado e já está coberto por `data/` e `*.sqlite3` no `.gitignore`. Guarda ID da agenda/evento e título mínimo, portanto não é um arquivo anônimo. O vínculo e o ID de entrada são hashes fornecidos pela integração confiável; a biblioteca valida sua forma, sem comprovar sozinha sua origem. Não guarda telefone, chave, texto de conversa, descrição do evento nem payload de envio. Representações dos objetos e erros são genéricas para evitar vazamento acidental em logs.

## Estados e reservas

| Estado | Significado |
|---|---|
| `pending` | Aviso persistido, aguardando horário e validações |
| `blocked` | Impedimento local registrado, sem iniciar transporte |
| `reserved` | Uma tentativa possui a reserva, ainda sem transporte iniciado |
| `sending` | Tentativa marcada como iniciada; nunca liberar por timeout/TTL |
| `accepted` | Provedor aceitou; fingerprint registrado, sem afirmar entrega/leitura |
| `uncertain` | Pode ter havido envio; bloquear repetição automática |
| `cancelled` | Pendente/reserva anterior ao transporte invalidada |
| `expired` | Prazo do aviso terminou sem iniciar transporte |

`reserve()` usa transação `BEGIN IMMEDIATE` e token UUID por tentativa. Só o resultado `new` permite avançar a reserva; retornos `pending`, `expired`, `blocked` e `accepted` não autorizam iniciar outra tentativa. Duas instâncias concorrentes não recebem duas reservas novas para a mesma identidade.

`mark_sending()` exige o token atual, releitura da configuração e relógio dentro de `due <= now < min(due + 120s, start)`. Se o prazo terminou, registra vencimento e não marca envio iniciado. O serviço futuro também precisará revalidar o evento e o canal imediatamente antes de chamar o transporte; **essas operações do banco não são um gate do WhatsApp nem prova de estado atual do Google**.

`release_reserved()` libera somente uma reserva que nunca avançou para `sending`. O token antigo deixa de funcionar após liberação, cancelamento ou nova reserva. Uma reserva abandonada permanece bloqueada após reinício, sem TTL. `sending` só pode ser finalizado como `accepted` ou `uncertain` pelo token correspondente; os dois estados impedem reenvio, mesmo após mudança de configuração. O resultado verdadeiro de uma tentativa iniciada pode ser registrado depois da desativação do perfil.

## Replanejamento e alterações

- `plan()` deriva destino, agenda e offset do perfil e exige sua revisão vigente. Calcula horários em UTC; normaliza o título e valida o fuso IANA. Não consulta se o evento existe, se está confirmado ou se é uma ocorrência permitida: isso pertence à geração/sincronização das partes 4 e 5.
- Replanejar a mesma identidade atualiza campos ainda não enviados; `cancelled/expired` sem envio potencial podem ser reconsiderados com a mesma identidade. Histórico permanece. Reservas ativas, `sending`, `accepted` e `uncertain` não são renovados por `plan()`.
- Mudar intervalo recalcula `pending/blocked`; desativar slot/perfil/permissão cancela esses avisos e reservas ainda anteriores ao transporte. Mudar agenda/vínculo cancela os antigos, sem redirecioná-los. Configurar não ressuscita avisos cancelados automaticamente.
- Início novo produz identidade nova. **Criá-la não cancela a antiga sozinho**: o [sincronizador da parte 5](sincronizacao-lembretes.md) confirma a alteração e cancela a anterior em lote próprio. O futuro serviço de envio deverá reler o evento exato para impedir envio de um início desatualizado.
- `expire_due()` vence apenas `pending/blocked` cujo prazo terminou; não libera reservas ou envios iniciados/incertos. `cancel()` e os métodos de transição recusam estados incompatíveis.

## Observações da janela do canal

`observe_inbound()` oferece persistência para a integração futura. Nenhum callback real foi ligado a ela nesta parte. O receptor atual continua com o comportamento anterior; o banco novo começa sem conhecimento da janela.

A primeira observação é congelada pelo hash global do ID, incluindo registros inválidos. Repetir o ID com timestamp ou vínculo diferente não o converte em nova mensagem. Timestamp original deve representar segundos Unix positivos e não ultrapassar o recebimento UTC; recebimento futuro é recusado. Texto de proveniência `sandbox_callback_capacity` pode produzir observação válida; tipos diferentes e origem `synthetic` ficam inválidos. Um teste sintético não prova origem real por simplesmente declarar essa proveniência: o chamador confiável precisa separar os ambientes.

`last_inbound()` consulta a observação válida mais recente por vínculo. Mensagem fora de ordem não estende a janela pelo horário de chegada; recibo, status, envio do bot e processamento do n8n não são usados. Não há backfill a partir de timestamps técnicos antigos. Dados ausentes continuam desconhecidos. A validação de janela de 24h com margem e seleção de template permanece para a parte 7, conforme [revisão do canal](canal-lembretes.md).

A URL secreta/filtro de remetente do sandbox continua sem assinatura criptográfica de origem. Essa proveniência limitada fica explícita na observação; o novo banco não transforma essa proteção em garantia adicional.

## Integridade, testes e operação

Operações usam transações SQLite curtas, commit antes do retorno e rollback em falha. A abertura e o uso conferem versão, estrutura e invariantes; base estrangeira, índice ausente, registro incoerente ou corrupção causam erro genérico. Não há migração destrutiva, limpeza por TTL ou recuperação que apague a barreira contra reenvio.

Os testes usam SQLite real, dados fictícios e relógio injetado. Incluem concorrência, reinício, revisão de configuração, fronteira de prazo, token antigo, rollback e observações inválidas/repetidas. A conferência entre processos inclui interrupção após persistir `sending` e retomada bloqueada. Nenhum desses ensaios chama Google, LLM ou WhatsApp.

Conservar este banco nos backups junto aos demais históricos privados. Remover a base perde a proteção dos lembretes; não usá-lo como forma de liberar envio incerto. Uma futura implantação com agendador ativo precisará parar os escritores ou usar backup consistente do SQLite. Entrega/leitura, reconciliação externa e política completa de recuperação continuam pendentes nas próximas partes.


**Continuação — parte 4:** `ReminderPlan` e `plan_batch()` foram acrescentados ao schema 1, preservando `plan()`. A [geração dos avisos](geracao-lembretes.md) valida a consulta completa e grava o lote com CAS e rollback único. A consulta manual já existe; os limites de sincronização e envio descritos acima continuam pendentes.


**Continuação — parte 5:** `sync_snapshot()` e `apply_sync()` acrescentam reconciliação atômica por configuração/escopo/histórico completos, sem alterar o schema 1. Reservas com conteúdo/fuso alterado são canceladas e replanejadas com token anterior inválido; `sending/accepted/uncertain` continuam protegidos. Consultar a [operação e os limites](sincronizacao-lembretes.md).
