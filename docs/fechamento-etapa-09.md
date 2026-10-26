# Fechamento da etapa 09 — API e fluxos n8n

**Concluída em 23/10/2026.** A etapa entregou o contrato e as rotas de agenda autenticadas, o fluxo normal de consultas n8n, apresentação segura de respostas, um ensaio integrado de CRUD e um disparo agendado de teste. Os três exports foram conferidos com os fluxos instalados e preparados para o Git sem segredos.

O fluxo normal publicado consulta o Google real pela API Python. As escritas pelo n8n foram verificadas em uma fixture separada, com o HTTP do Google simulado. O agendamento consulta o status da API; o sistema de lembretes permanece para a próxima integração.

## Entregas das dez partes

| Parte | Entrega | Evidências |
|---|---|---|
| 1. Comunicação | Contrato v1, identidade da criação, envelopes e exemplos fictícios. | 049 — conferência local |
| 2. Proteção | Bearer, comparação segura, correlação e status autenticado; segredos privados. | 050/051 — testes e HTTP local |
| 3. Consultas | Dia, semana, mês e ID, com filtros estritos e respostas mínimas. | 052/053 — testes e leituras Google reais |
| 4. Criação | POST com validações, conflitos e reutilização persistente da operação. | 054/055 — testes e criação/repetições no Google real |
| 5. Alterações | Edição e reagendamento com ETag e conferência posterior. | 056/057 — testes e dois PATCHs Google reais |
| 6. Exclusão | Prévia e execução com código vinculado à agenda/ID/versão. | 058/059 — exclusão completa simulada; prévia/bloqueios reais, sem DELETE real neste marco |
| 7. Primeiro fluxo | Webhook autenticado → GET na API → resposta; configuração e cliente de teste. | 060–062 — HTTP, consultas Google reais, canvas e testes locais |
| 8. Respostas e falhas | Apresentador reutilizável com perguntas, conflitos, confirmação e resultado incerto, sem reenvio automático. | 063/064 — testes locais e webhook real |
| 9. Integração | CRUD e falhas através do n8n/API reais com Google em memória; Schedule Trigger natural. | 065–067 — testes estáticos, HTTP integrado e canvas despublicado |
| 10. Export e fechamento | Correspondência dos exports instalados, revisão de segredos, inventário, documentação e preservação do acervo. | 068 — auditoria local; sem nova operação de agenda ou rodada de testes |

Os guias de [contrato](contrato-api-v1.md), [primeiro fluxo](n8n-primeiro-fluxo.md), [respostas](n8n-respostas-falhas.md) e [integração](n8n-testes-integracao.md) detalham cada percurso. Os registros completos ficam no acervo privado `data/evidencias/` e no seu índice.

## Fluxos exportados

| Export público | Finalidade | Estado da instância no fechamento |
|---|---|---|
| [`consulta-eventos.json`](../n8n/workflows/consulta-eventos.json) | Sete blocos; webhook de consulta, GET na API `8000`, apresentação e resposta. | Publicado |
| [`integracao-isolada-teste.json`](../n8n/workflows/integracao-isolada-teste.json) | Nove blocos; fixture CRUD na API isolada `8001`; somente o HTTP Google é simulado. | Despublicado; worker encerrado |
| [`disparo-agendado-teste.json`](../n8n/workflows/disparo-agendado-teste.json) | Quatro blocos; Schedule Trigger, limite de duas tentativas, GET de status e resumo técnico. | Despublicado após a prova |

Todos os arquivos públicos têm **`active: false`**, `pinData` vazio e referências de credenciais somente por nome. Não carregam chaves, valores de autenticação, IDs locais de workflow/credencial, dados de `staticData`, histórico de execução ou eventos fixados. Identificadores dos blocos e do webhook permanecem para preservar o grafo e a importação; não são credenciais.

No fechamento, `n8n export:workflow` exportou cada um dos três fluxos instalados. A conferência removeu referências privadas e campos de conta/execução, forçou o estado público inativo e comparou nome, blocos, parâmetros, conexões e configurações com os arquivos já testados. As estruturas corresponderam exatamente e os bytes públicos foram preservados. Nenhuma credencial foi exportada. O [manifesto](../n8n/exports-manifest.json) registra a versão, a finalidade e o SHA-256 de cada arquivo; o [inventário n8n](../n8n/README.md) orienta a configuração.

## Testes e provas preservados

- **444 testes Python** aprovados na parte 6; os 58 arquivos Python da aplicação e dos testes continuam iguais aos hashes daquele relatório.
- **297 testes JavaScript** da parte 8: 215 do apresentador e 82 do fluxo normal.
- **130 testes JavaScript** da parte 9: 63 da fixture e 67 do agendamento. As quatro suítes JavaScript reúnem 427 casos; as rodadas anteriores não são somadas novamente.
- **34 verificações HTTP** da parte 9: operações, conflitos, confirmação, repetição e falhas. Foram 80 GETs e dez escritas no transporte Google simulado, com nenhum evento fictício restante.
- **Disparo natural:** 23/10/2026, 02:26:31 em `America/Fortaleza`; `GET /api/v1/status` recebeu HTTP 200. Expressão em modo `production` e metadado da execução em modo `trigger`.
- **Preservação:** os mesmos três eventos reais antes/depois do ensaio; dois fluxos de teste despublicados, processo isolado encerrado e consulta normal mantida publicada.

O fechamento documental não repetiu essas suítes nem o ensaio. A auditoria conferiu a correspondência dos arquivos, exports, links, proteção dos arquivos privados e evidências anteriores. A captura 067 comprova a estrutura e o estado visível; a execução natural é demonstrada no registro 066. O acervo passa a **68 registros: 15 imagens e 53 JSONs**.

## Reproduzir a configuração

Na raiz do projeto, com dependências, OAuth, chave da API, Docker e usuário local do n8n configurados:

```powershell
.\.venv\Scripts\python.exe -m app
```

Em outro terminal:

```powershell
docker compose up -d
.\.venv\Scripts\python.exe scripts/setup_n8n.py --apply
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23
```

O setup é necessário na configuração/importação inicial ou após mudar o fluxo; para retomar uma instalação existente, basta iniciar os serviços. A data acima reproduz a consulta documentada; use outra data para consultar outro período. Para o ensaio isolado e a limpeza dos fluxos, seguir os pré-requisitos de [testes de integração](n8n-testes-integracao.md). O runner reinicia brevemente o n8n e publica somente suas fixtures separadas.

## Limites e continuidade

- `POST /requests/prepare` permanece planejado; o serviço de preparação e a CLI já existem. Perguntas da biblioteca foram testadas, mas falta conectá-las à conversa.
- O fluxo normal n8n publicado é de consulta. Fluxos de escrita sobre o Google real e persistência da confirmação do usuário serão conectados na evolução da integração. A fixture isolada não deve ser exposta como entrada de produção.
- A API já oferece CRUD. A exclusão HTTP exige código da prévia; o código não substitui consentimento informado. A parte 6 não apagou um evento real, e o CRUD integrado da parte 9 usou Google simulado.
- O disparo técnico de status não implementa lembretes confiáveis. Ainda faltam estados duráveis, recuperação de disparos perdidos e entrega pelo WhatsApp.
- Criação repetida depende do SQLite preservado. Não há idempotência persistente geral de PATCH/DELETE, nem transação atômica entre SQLite e Google.
- Credenciais ficam no `.env`/`secrets` e no gerenciador do n8n. O volume privado pode manter entradas e metadados transitórios até a limpeza automática, mesmo com o salvamento de histórico desabilitado.

A continuidade é configurar o WhatsApp de teste e integrar a conversa, a preparação e as ações autorizadas. Depois entram IA, lembretes persistentes, briefings/imagens e painel de demonstração. A etapa 09 fecha a base de API e automação; a entrega completa pelo WhatsApp continua no roteiro.
