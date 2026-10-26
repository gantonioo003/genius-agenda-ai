# Exclusão com confirmação — etapa 07, parte 6

O comando exclui um único evento identificado pelo ID completo, na agenda própria de teste selecionada. A execução tem duas chamadas: conferir o evento e confirmar a versão apresentada. Não depende da IA, da API HTTP ou do n8n.

## Comandos

Na raiz do repositório, com o acesso ao Google configurado, substitua `ID_DO_EVENTO` pelo ID encontrado na consulta:

```powershell
.\.venv\Scripts\python.exe -m app.delete_event --event-id ID_DO_EVENTO
```

A prévia retorna `needs_confirmation`, título, data, horários, fuso, pergunta e `confirmation_token`. Confira os dados e copie o código completo para a segunda chamada:

```powershell
.\.venv\Scripts\python.exe -m app.delete_event --event-id ID_DO_EVENTO --execute --confirm CODIGO_DA_PREVIA
```

Sem `--execute`, não há exclusão. `--execute` sem código válido e `--confirm` sem execução são rejeitados antes de abrir o cliente Google. O fuso padrão é `America/Recife`; `--timezone` permite outro fuso válido para conferir os horários.

## Confirmação e estados

1. O serviço valida ID, fuso e opções. O adaptador seleciona a agenda de teste fixada, sem recorrer à agenda principal.
2. A prévia consulta o ID exato e a versão atual (ETag). O código combina operação, agenda, ID e versão.
3. A execução consulta novamente o evento. Qualquer mudança de versão invalida o código e exige uma nova prévia.
4. O DELETE usa `If-Match` com a versão conferida, `sendUpdates=none`, sem corpo e sem retentativa automática de escrita.
5. Outra consulta pelo ID precisa confirmar a ausência: HTTP 404/410 ou registro cancelado do mesmo ID. O corpo vazio retornado pelo DELETE, sozinho, não encerra a verificação.

| Estado | Significado |
|---|---|
| `needs_confirmation` | Evento disponível; prévia somente de leitura |
| `deleted` | DELETE aceito e ausência confirmada por nova leitura |
| `already_absent` | O evento já não está disponível; nenhuma exclusão nova foi confirmada |

Em `deleted`, o campo `event` preserva os dados conferidos antes da remoção para identificar o que foi excluído; o estado da operação está no `status` principal.

Se o evento desaparecer entre a leitura e o DELETE, o comando verifica novamente a ausência e retorna `already_absent`. Uma falha de rede ou leitura após a tentativa deixa o resultado incerto: consulte o ID antes de repetir. Mudança concorrente detectada por HTTP 412 exige nova prévia.

O código identifica a versão conferida; não é uma credencial, assinatura de usuário ou comprovação de consentimento humano. Não tem prazo de expiração: vale enquanto essa versão for atual. Na integração com WhatsApp, ainda será necessário manter uma confirmação pendente por usuário e conversa, com validade e tratamento da resposta.

## Escopo

Eventos comuns com horário, eventos de dia inteiro e uma ocorrência específica de série são aceitos. Uma série inteira e tipos especiais de evento são rejeitados. A operação não cria outro evento nem altera outros campos. A prévia lê somente os campos necessários, sem descrição ou local.

O serviço fica em `app/services/event_deletion.py`, o adaptador em `app/google_calendar.py` e a interface de terminal em `app/delete_event.py`. São componentes independentes do modelo de IA. A operação também foi exposta pela [API HTTP na etapa 09, parte 6](exclusao-api.md). As integrações n8n e WhatsApp permanecem pendentes.

## Verificação

Em **21/10/2026**, executamos a prévia do ID da fixture e conferimos **Entrevista técnica — Empresa Exemplo (teste), 23/10/2026, 16h–16h30, America/Recife**, na agenda `Genius Testes`. Com o código dessa prévia, a execução retornou `deleted` após reler a ausência.

Uma verificação independente confirmou: evento ausente pelo ID, nenhum evento na consulta de 23/10 e nova prévia retornando `already_absent`. A visão diária do Google Agenda também não mostrou a entrevista. Os registros reais são **024 a 027** em `data/evidencias/INDICE.md`: prévia, execução, consultas posteriores e captura original do navegador.

A fixture foi removida ao final do teste. Os exemplos anteriores de criação, consulta e alteração descrevem o histórico do mesmo evento; para outra demonstração completa, será preciso preparar uma nova fixture com ID próprio.

Os **23 testes novos** cobrem confirmação obrigatória e validação antes de I/O, vínculo à versão/agenda/ID, prévia sem escrita, proteção concorrente, resposta vazia, ausência e cancelamento, falhas após a tentativa, agenda segura e escopo de recorrência. A suíte completa passou com **139 testes**. Os testes locais usam conectores simulados; não acessam a conta Google nem excluem eventos reais.

A revisão do conjunto encerrou a etapa 07. Consulte as entregas, evidências e limites no [registro de fechamento](fechamento-etapa-07.md).

Referências: [DELETE de eventos](https://developers.google.com/workspace/calendar/api/v3/reference/events/delete) e [versões de recursos e If-Match](https://developers.google.com/workspace/calendar/api/guides/version-resources).


**Atualização na etapa 08, parte 6:** uma resposta de escrita perdida ou inválida agora é conferida por leitura do mesmo ID. O resultado inclui `recovered: true` quando essa leitura confirma o estado esperado; se a conferência falhar, permanece um erro seguro, sem nova escrita automática. Os registros e limites estão no [guia de falhas e recuperação](falhas-recuperacao.md).
