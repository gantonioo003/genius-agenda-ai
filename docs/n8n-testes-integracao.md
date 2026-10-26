# Testes de integração n8n — etapa 09, parte 9

**Concluída em 23/10/2026.** O ensaio atravessou o n8n e a API HTTP reais, verificou as operações e falhas com calendário isolado e confirmou um disparo natural do agendador. Ao final, os dois fluxos de ensaio foram despublicados, o servidor de teste encerrado e o fluxo normal de consultas permaneceu publicado.

## O que foi real e o que foi simulado

| Camada | Execução neste ensaio |
|---|---|
| Webhook, n8n e HTTP local | Reais, incluindo autenticação, mapeamento, apresentação e códigos HTTP |
| API FastAPI, serviços, SDK Calendar, ETags e SQLite | Código real da aplicação, em processo e banco separados |
| HTTP do Google durante o CRUD | Em memória, com `CalendarHttpFake`; sem OAuth ou alteração de compromissos reais |
| Consulta normal antes/depois | Google real, somente leitura; mesmos três eventos de `Genius Testes` |
| Agendamento | Schedule Trigger real, consultando o status autenticado da API normal; não consulta eventos |

Esse é um ensaio integrado com uma fronteira externa simulada. Não comprova CRUD pelo n8n no Google real nem envio de WhatsApp ou lembretes. O fluxo normal de consultas continua GET; o fluxo com escritas é uma fixture de teste inativa, apontando exclusivamente para a porta `8001`.

## Resultados confirmados

**34 verificações HTTP aprovadas:**

- Criação, consultas por ID/dia/semana/mês, edição, reagendamento e exclusão.
- Repetições de criação reutilizando o mesmo evento; mesmo conteúdo com outro identificador sem novo POST.
- Edição/reagendamento repetidos como `unchanged`, sem novo PATCH.
- Conflito 409 e intervalo inválido 422 sem escrita.
- Prévia de exclusão, bloqueio sem código/incorreto/obsoleto, nova prévia e exclusão; repetição em evento ausente sem novo DELETE.
- Falha de leitura sem lista vazia inventada e recuperação posterior.
- Perda da resposta de criação, edição e exclusão, com confirmação por leitura e uma única escrita em cada caso.
- Resultado de criação incerto como 502/`unknown`; recuperação por reenvio explícito da mesma operação, sem outro POST.

O transporte Google **simulado** registrou **90 chamadas: 80 GETs, 3 POSTs, 4 PATCHs e 3 DELETEs**. As dez escritas representam os três eventos fictícios e suas alterações/limpezas; nenhum evento simulado permaneceu ao final. Os deltas foram conferidos a cada pedido. Essas contagens não são chamadas à conta Google real. Nas falhas de leitura, o SDK pode fazer três tentativas GET; os blocos HTTP n8n não repetem as chamadas de escrita.

### Disparo agendado

O fluxo `Genius Agenda AI - Disparo agendado de teste` foi publicado temporariamente, com intervalo de um minuto e limite de duas tentativas. Houve **um disparo natural confirmado**, sem clicar em Execute:

- Horário: **23/10/2026, 02:26:31, America/Fortaleza** (`05:26:31 UTC`).
- API normal: `GET /api/v1/status` → **HTTP 200**.
- Resumo persistido: uma tentativa, um sucesso, zero falhas, modo `production`.
- Metadado da execução n8n: modo `trigger`, confirmando o acionamento automático.

O resumo guarda somente contadores, horário, estado e correlação técnica. O fluxo foi despublicado após a prova. O `staticData` é usado apenas para este ensaio de baixo volume; não é o mecanismo de persistência dos futuros lembretes, não garante recuperação de disparos perdidos e não substitui uma fila/estado durável de entrega.

## Reproduzir

Pré-requisitos: Docker/n8n configurados, usuário local existente, API normal ligada em `127.0.0.1:8000`, OAuth/agenda de teste funcionais e primeiro fluxo de consultas configurado. Node é necessário para os testes estáticos. Não pode haver outro serviço na porta `8001`.

Na raiz do repositório:

```powershell
node n8n/tests/integracao-isolada.test.cjs
node n8n/tests/disparo-agendado.test.cjs
.\.venv\Scripts\python.exe scripts/test_n8n_integration.py --apply
```

O comando Python importa/publica temporariamente os dois fluxos, reinicia o n8n, inicia o worker isolado, executa o percurso HTTP e aguarda o disparo natural. Depois despublica apenas os dois fluxos de teste, reinicia o n8n para aplicar essa mudança e encerra o worker. Há uma breve interrupção local do n8n nos reinícios. Os IDs de teste são reutilizados nas próximas execuções.

Não execute simultaneamente duas cópias do ensaio. O arquivo `data/n8n-integration-runs/running.lock` impede concorrência normal. Se o computador ou processo for interrompido à força, confira os serviços e os dois fluxos de teste antes de retirar uma trava remanescente. O `finally` tenta limpar falhas usuais; não garante limpeza após queda do computador. O limite de duas tentativas do agendamento é uma proteção adicional do ensaio, não uma garantia de entrega.

Os relatórios e o SQLite isolado ficam em `data/n8n-integration-runs/<execução>/`; os IDs reutilizáveis, em `data/n8n-integration-fixtures.json`. Tudo é ignorado pelo Git. O worker recebe sua configuração por stdin, usa credenciais exclusivas do ensaio e bloqueia acesso a credenciais Google. As rotas administrativas `__test` existem somente nesse processo isolado, com autenticação, e desaparecem quando ele encerra.

Os exports públicos ficam inativos e contêm somente referências de credenciais. Não use `scripts/setup_n8n.py --workflow` para importar essas fixtures sobre o fluxo normal: o runner atribui IDs separados e faz a limpeza apropriada.

## Arquivos

| Arquivo | Papel |
|---|---|
| [`scripts/test_n8n_integration.py`](../scripts/test_n8n_integration.py) | Orquestração, verificações, prova natural e limpeza |
| [`scripts/n8n_integration_worker.py`](../scripts/n8n_integration_worker.py) | API real com transporte Google em memória e banco isolado |
| [`n8n/workflows/integracao-isolada-teste.json`](../n8n/workflows/integracao-isolada-teste.json) | Fixture autenticada para CRUD e erros na API `8001` |
| [`n8n/workflows/disparo-agendado-teste.json`](../n8n/workflows/disparo-agendado-teste.json) | Schedule Trigger → limite → GET de status → resumo seguro |
| [`tests/calendar_http_fake.py`](../tests/calendar_http_fake.py) | Fronteira HTTP Google simulada, já utilizada nos testes da aplicação |

No bloco HTTP n8n, `sendBody` exige um booleano literal. A fixture separa leitura e escrita em dois blocos e seleciona apenas um por pedido; as escritas usam `sendBody: true`, permitindo que os campos JSON estejam disponíveis. Ambos apontam para `8001`, sem URL livre, redirecionamento ou retentativa automática. O grafo é acíclico e não volta ao executor.

## Verificações locais e evidências

**130 testes estáticos aprovados:** 63 da fixture de integração e 67 do agendamento. Conferem rotas/métodos, tipos, entradas hostis, credenciais sem valores no export, grafo com uma chamada HTTP por caminho, modos de execução, limite de duas tentativas e resumo sem dados privados.

Os **297 testes JavaScript anteriores** e os **444 testes Python anteriores** permanecem como referência das partes anteriores; não foram somados a esta nova contagem. O código da aplicação Python não foi alterado e seus 58 arquivos continuam iguais aos da suíte aprovada. A documentação separa testes estáticos, HTTP com Google simulado, leitura real e agendamento natural.

Evidências: **065** (testes estáticos e auditoria), **066** (percurso HTTP, contagens, agendamento e limpeza) e **067** (canvas do agendamento, já despublicado). A imagem demonstra a estrutura e o estado da interface; a execução natural é comprovada pelos metadados e pelo resumo do registro 066.

Referências oficiais: [Schedule Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/), [staticData](https://docs.n8n.io/code/cookbook/builtin/get-workflow-static-data/), [metadados de execução](https://docs.n8n.io/code/builtin/n8n-metadata/).
