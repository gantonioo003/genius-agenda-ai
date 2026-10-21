# Genius Agenda AI

Assistente pelo WhatsApp para organizar entrevistas de emprego no Google Calendar, com lembretes automáticos e preparação personalizada. Desenvolvido para o Desafio Genius Agenda AI.

**Etapa 07 concluída:** criação, consultas, identificação, reagendamento, edição das informações e exclusão com confirmação funcionando no Google Calendar por comandos Python. Revisão final sem bloqueadores, com 139 testes aprovados. A entrevista fictícia foi removida ao final da demonstração, com ausência conferida pela API e na interface. Próxima etapa: regras da agenda. Fluxos do n8n e atendimento pelo WhatsApp ainda estão pendentes.

## Histórico do desenvolvimento

Registro do que foi realizado até esta versão, da definição do projeto à validação da base:

| Etapa | O que fizemos | Resultado |
|---|---|---|
| 1. Escopo | Escolhemos entrevistas de emprego como aplicação do desafio e definimos as responsabilidades de Python, n8n e Google Calendar. | CRUD e lembretes como entrega principal; briefings e imagens por IA como evolução. |
| 2. Repositório e ambiente | Criamos o repositório e a pasta local; conferimos Python, Git e Docker. | Base de trabalho preparada no Windows com Python 3.14.3. |
| 3. n8n local | Configuramos o n8n em Docker Compose, com versão fixada, porta local e volume persistente; acessamos a interface e criamos o usuário local. | Instância iniciada e verificação de saúde aprovada. Ainda sem workflows. |
| 4. Configuração Python | Criamos a `.venv`, instalamos e fixamos as dependências; separamos configurações locais em `.env` e exemplos públicos em `.env.example`. | Ambiente isolado e configuração validada por `app/settings.py`. |
| 5. API mínima | Implementamos a aplicação FastAPI, o comando de inicialização e o endpoint `/health`. | `/health` e `/docs` responderam com HTTP 200. |
| 6. Persistência | Criamos o SQLite, a tabela `app_metadata`, as transações e a verificação de gravação e leitura em conexões diferentes. | Persistência confirmada e quatro testes do banco aprovados. |
| 7. Agenda de teste | Criamos a agenda separada `Genius Testes`, preservando a agenda principal para uso pessoal. | Agenda disponível com fuso `America/Recife` e acesso de proprietário. |
| 8. Cadastro no Google | Criamos e selecionamos o projeto Google Cloud, ativamos a Calendar API e configuramos OAuth externo em teste, usuário de teste e cliente para computador. | JSON do cliente salvo na pasta privada `secrets/`. |
| 9. Autorização e seleção | Implementamos o login OAuth no Python, autorizamos a conta pelo navegador e salvamos o token e a identificação da agenda. Adicionamos rejeição de agenda principal, nomes ambíguos e ID ausente. | Consulta real ao Google confirmada em 21/10/2026; cinco testes locais de seleção aprovados. |
| 10. Revisão da base | Documentamos a execução e as decisões; revisamos o `.gitignore`, os arquivos públicos e as dependências; registramos a base em Git. | Nove testes iniciais aprovados, dependências consistentes e credenciais, token e banco fora do Git. |
| 11. Preparação da primeira entrevista | Confirmamos novamente o acesso, definimos o evento fictício e implementamos serviço independente da IA, conector Google e comando com prévia. | Quinze testes de criação aprovados e comando pronto para execução explícita. |
| 12. Primeira gravação real | Executamos o comando de criação para a entrevista de 22/10/2026, das 15h às 15h30, sem convidados. | Google confirmou a criação em `Genius Testes` em 21/10/2026, com ID e link do evento. |
| 13. Conferência da resposta | Consultamos o evento criado pelo ID, usando somente leitura (`events.get`). | Status `confirmed`, título, início/fim e link conferidos no retorno real do Google e exibidos no terminal. |
| 14. Conferência visual e evidências | Abrimos o evento no Google Agenda e organizamos os prints relevantes da configuração e do resultado. | Título, data, horário, duração e agenda conferidos na interface; seis capturas indexadas e rotina de documentação visual registrada. |
| 15. Consulta por dia | Implementamos serviço de consulta, limites por fuso, paginação e comando Python de leitura. | Consulta da data 22/10/2026 retornou a entrevista das 15h às 15h30; 16 testes novos aprovados e evidências 007/008 registradas. |
| 16. Consulta por semana e mês | Ampliamos o serviço e o comando com períodos civis, mantendo o formato da consulta diária. | Em 21/10/2026, semana de 19 a 25/10 e outubro completo retornaram a mesma entrevista; saídas reais e visão mensal registradas nas evidências 009 a 011. |
| 17. Identificação do evento | Implementamos busca por título com data/período, consulta exata por ID e revalidação antes de confirmar um resultado único. | Em 21/10/2026, a entrevista foi identificada por título e ID; busca sem correspondência pediu nova informação. Dois candidatos fictícios em simulação local produziram esclarecimento, sem seleção automática. Registros 012 a 015. |
| 18. Reagendamento | Implementamos prévia, validação de horários, PATCH de início/fim protegido por ETag e leitura posterior de confirmação. | Em 21/10/2026, movemos a entrevista de 22/10, 15h–15h30 para 23/10, 16h–16h30, mantendo ID, título e duração. Resultado conferido pela API e na interface; registros 016 a 019. |
| 19. Edição das informações | Implementamos edição parcial de título, descrição e local, prévia, proteção por versão e confirmação posterior. | Em 21/10/2026, os três campos foram atualizados no mesmo evento, preservando 23/10, 16h–16h30. Novo título e dados conferidos pela API e na interface; registros 020 a 023. |
| 20. Exclusão com confirmação | Implementamos prévia pelo ID, código vinculado à agenda e à versão, DELETE condicionado por ETag e consulta posterior. | Em 21/10/2026, removemos a entrevista fictícia após conferir título e 23/10, 16h–16h30. Ausência pelo ID, na consulta diária e na interface; registros 024 a 027. |
| 21. Fechamento da etapa 07 | Revisamos cobertura, correspondência do código testado, resultados reais e documentação; atualizamos os guias e o planner. | 139 testes aprovados no código atual, 27 evidências indexadas e nenhum bloqueador no escopo de comandos Python. Etapa 07 concluída em 21/10/2026. |

A validação real incluiu descoberta da agenda, criação, consultas, identificação, reagendamento, edição e exclusão, com conferências na interface. Ambiguidades e falhas concorrentes foram verificadas em testes locais. O [fechamento da etapa 07](docs/fechamento-etapa-07.md) registra as entregas e os limites. O SQLite contém metadados internos, sem tabelas de mensagens ou lembretes.

## Arquitetura planejada

```text
WhatsApp → n8n → API Python + IA → Google Calendar
                        ↕
                      SQLite
```

O n8n coordenará os fluxos; Python concentrará interpretação, validações e operações; SQLite guardará o controle local de mensagens e lembretes. O Google Calendar será a referência dos compromissos. Será utilizada uma IA pronta por API.

Atualmente, API, n8n e comandos Google funcionam separadamente. Os serviços das operações de agenda ficam em `app/services/`, sem dependência do modelo de IA; o conector Google é injetado nesses serviços. O Google Cloud foi usado para cadastrar e autorizar o aplicativo; a aplicação roda localmente.

## Executar localmente

Ambiente validado: Windows, Python **3.14.3**, Docker **29.4.0** e Compose **5.1.2**. Execute na raiz do repositório:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
.\.venv\Scripts\python.exe -m app
```

- API: <http://localhost:8000/health>
- Documentação da API: <http://localhost:8000/docs>

Com o Docker Desktop em execução, abra outro terminal na raiz do projeto:

```powershell
docker compose up -d
```

n8n: <http://localhost:5678>. O endpoint `/health` verifica apenas a API; não comprova as integrações.

### Google Calendar

Configure o cliente e a conta conforme o [guia de configuração](docs/configuracao.md). Na primeira autorização:

```powershell
.\.venv\Scripts\python.exe -m app.google_calendar --authorize
```

Para conferir o acesso já autorizado:

```powershell
.\.venv\Scripts\python.exe -m app.google_calendar
```

O comando consulta a lista de agendas e confirma a seleção de `Genius Testes`. `--authorize` reaproveita um token válido e abre o login quando necessário. Credenciais, autorização e banco locais não acompanham o clone.

### Primeira entrevista — etapa 06 concluída

Dados originais da criação: **22/10/2026, das 15h às 15h30**, em `America/Recife`, sem convidados. A parte 4 da etapa 07 mudou o evento para **23/10, 16h–16h30**. Para conferir a prévia da solicitação original sem acessar o Google:

```powershell
.\.venv\Scripts\python.exe -m app.create_test_interview
```

O resultado é `status: preview`. Para criar o evento real, executamos em 21/10/2026:

```powershell
.\.venv\Scripts\python.exe -m app.create_test_interview --execute
```

A primeira execução retornou `status: ok`, `calendar_name: Genius Testes`, `reused: false`, ID e link do evento. A consulta posterior pelo ID retornou `confirmed`; título, data, horário e duração também foram conferidos na interface. O registro visual é a captura 006.

`--execute` pode gravar na agenda: consulta o ID estável, reutiliza um evento correspondente e tenta criar se o ID estiver ausente. Consulte a [implementação, verificações e limites desta etapa](docs/primeira-entrevista.md).

A entrevista foi posteriormente reagendada, editada e excluída na demonstração do CRUD. Esse comando registra a criação original; não o use para consultar o estado atual ou recriar automaticamente o evento removido. Uma nova demonstração deve usar uma nova fixture com ID próprio.

### Consulta por dia — etapa 07, parte 1 concluída

```powershell
.\.venv\Scripts\python.exe -m app.list_events --date 2026-10-23 --timezone America/Recife
```

Consulta somente de leitura, sem precisar iniciar API ou n8n. A validação inicial encontrou a entrevista em 22/10, 15h–15h30; após o reagendamento, a consulta de 23/10 retornou o mesmo ID, das 16h às 16h30. Depois da exclusão, essa consulta retornou zero eventos. Os resultados incluem ID, título, início/fim e status. Consulte o [guia da consulta por dia](docs/consulta-por-dia.md).

### Consulta por semana e mês — etapa 07, parte 2 concluída

Use uma data de referência dentro da semana ou mês desejado:

```powershell
.\.venv\Scripts\python.exe -m app.list_events --period week --date 2026-10-22 --timezone America/Recife
.\.venv\Scripts\python.exe -m app.list_events --period month --date 2026-10-22 --timezone America/Recife
```

Semana: **segunda a domingo**. Mês: primeiro ao último dia. Na validação anterior à exclusão, as duas consultas reais retornaram `count: 1`, com a entrevista então existente. O JSON explicita a data de referência, o início e o fim exclusivo do período. Sem `--period`, o comando continua consultando apenas o dia. Consulte as [regras e a validação semanal/mensal](docs/consulta-semana-mes.md).

### Identificar o evento — etapa 07, parte 3 concluída

```powershell
.\.venv\Scripts\python.exe -m app.select_event --title "entrevista tecnica" --date 2026-10-23
```

Antes da exclusão, o comando encontrou a entrevista e retornou `selected`, com seu ID confirmado por uma nova leitura no Google. Também aceita `--event-id` para uma identificação exata. Mais de um candidato retorna `needs_clarification`, opções e pergunta; nenhum retorna `not_found`, com pedido para conferir a busca. A entrevista removida já não será selecionada. Esses resultados não alteram a agenda. Consulte as [regras de seleção e os comandos](docs/identificacao-eventos.md).

### Reagendar — etapa 07, parte 4 concluída

O comando `app.reschedule_event` recebe `--event-id`, `--start`, `--end` e `--timezone`. O padrão mostra uma prévia de leitura; `--execute` aplica somente os horários, com proteção contra mudança concorrente e nova consulta de confirmação.

A execução real moveu a entrevista para **23/10/2026, 16h–16h30**, mantendo o mesmo ID e título. Uma repetição com os horários atuais retorna `unchanged`, sem nova escrita. Consulte os [comandos, verificações e limites do reagendamento](docs/reagendamento.md).

### Editar informações — etapa 07, parte 5 concluída

O comando `app.edit_event` recebe o ID e os campos a editar: `--title`, `--description` e `--location`. Campos omitidos são preservados; a prévia é o padrão e `--execute` aplica a mudança. Há flags explícitas para limpar descrição ou local.

A edição real alterou os três campos da entrevista, mantendo ID, data e horário. O PATCH é protegido por versão e seguido de outra leitura para confirmar o resultado. Consulte os [comandos, testes e limites da edição](docs/edicao-informacoes.md).

### Excluir com confirmação — etapa 07, parte 6 concluída

`app.delete_event --event-id ID_DO_EVENTO` mostra título, data, horários e código de confirmação. Após conferir a prévia, a chamada com `--execute --confirm CODIGO_DA_PREVIA` relê a versão, exclui com `If-Match` e verifica a ausência. Confirmação antiga é recusada; evento já ausente retorna `already_absent`.

A entrevista fictícia foi excluída pelo mesmo ID. Uma consulta independente pelo ID e pelo dia confirmou a ausência; a interface também foi conferida. Consulte os [comandos, proteção e limites da exclusão](docs/exclusao-confirmada.md). A revisão final do conjunto encerrou a etapa 07.

## Validação realizada

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m app.database --check
```

- **139 testes locais:** quatro do SQLite, cinco de seleção de agenda, quinze de criação, dezesseis de consulta diária, quatorze de consulta semanal/mensal, dezoito de identificação, vinte de reagendamento, vinte e quatro de edição das informações e 23 de exclusão, incluindo preservação de dados, concorrência e falhas.
- **Dependências:** nenhuma inconsistência encontrada por `pip check`.
- **Verificações reais:** API HTTP 200, n8n inicializado, autorização/descoberta da agenda, criação, consultas, identificação, reagendamento, edição e exclusão confirmados pelo Google. Os resultados das alterações também foram conferidos na interface.

Os testes automatizados usam conectores simulados; as operações reais foram validadas separadamente. O CRUD funciona por comandos Python. Ainda não está exposto na API HTTP nem conectado ao WhatsApp; não há teste de ponta a ponta ou envio de mensagens e lembretes.

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| `app/main.py` / `app/__main__.py` | Aplicação FastAPI e inicialização do servidor |
| `app/settings.py` | Leitura e validação das configurações |
| `app/database.py` | SQLite, metadados e transações |
| `app/google_calendar.py` | OAuth, seleção da agenda e adaptador das operações CRUD |
| `app/services/interviews.py` | Serviço de criação independente da IA e do conector |
| `app/services/agenda.py` | Consultas por dia, semana e mês, com limites por fuso |
| `app/services/event_selection.py` | Identificação por ID ou título, revalidação e esclarecimento |
| `app/services/rescheduling.py` | Serviço de reagendamento e validação de horários e fuso |
| `app/services/event_details.py` | Validação e serviço de edição parcial de título, descrição e local |
| `app/services/event_deletion.py` | Confirmação por ID/versão e serviço de exclusão |
| `app/create_test_interview.py` | Dados da entrevista fictícia, prévia e execução explícita |
| `app/list_events.py` | Comando de consulta por período, data de referência e fuso |
| `app/select_event.py` | Comando de identificação, sem escrita na agenda |
| `app/reschedule_event.py` | Prévia e execução explícita do reagendamento |
| `app/edit_event.py` | Prévia e execução explícita da edição das informações |
| `app/delete_event.py` | Prévia e exclusão explícita com código de confirmação |
| `tests/` | Testes locais do banco e das operações de agenda |
| `compose.yaml` | Instância local do n8n |
| `.env.example` / `requirements.txt` | Configuração pública e dependências fixadas |
| `docs/` | Guias de configuração e operação |

## Dados e operação

`.env`, `.venv/`, `secrets/` e `data/` são locais e ignorados pelo Git. O SQLite fica em `data/agenda.db`; os dados do n8n ficam no volume Docker `genius-agenda-ai_n8n_data`. Nunca publique tokens ou credenciais em código, logs, prints ou exports.

Para parar, use `Ctrl+C` no terminal da API e `docker compose stop` para o n8n. Para retomar, inicie o Docker, execute `docker compose up -d` e reinicie a API. Fechar o navegador ou parar os serviços preserva os dados; `docker compose down -v` remove o volume do n8n.

No OAuth externo em teste, com os escopos Calendar utilizados, o refresh token expira em sete dias. A aplicação local precisa estar ligada para executar automações. Consulte o [guia de configuração e diagnóstico](docs/configuracao.md) e a [operação do n8n](docs/n8n-local.md).

## Próximas etapas

1. Etapa 08: consolidar validações de datas, fusos e ambiguidades; detectar conflitos e evitar duplicações.
2. Conectar n8n, API e WhatsApp de teste; adicionar a IA e o contexto da conversa.
3. Implementar lembretes persistentes e configuráveis, com recuperação de falhas e sincronização de alterações.
4. Adicionar resumos, briefings e imagem gerada por IA no dia da entrevista, com alternativa por texto.
5. Criar painel web com entrevistas, lembretes, briefings/cards e modo demonstração identificado.
6. Concluir testes de ponta a ponta e documentação; gravar o vídeo solicitado pelo recrutador e organizar a entrega.

## Documentação visual

Os marcos importantes são registrados continuamente em capturas numeradas e saídas reais de terminal, incluindo prints relevantes enviados pelo usuário. Consulte o [registro de evidências](docs/evidencias.md). O acervo fica na pasta local `data/evidencias/`, fora do Git.
