# Genius Agenda AI

Assistente pelo WhatsApp para organizar entrevistas de emprego no Google Calendar, com lembretes automáticos e preparação personalizada. Desenvolvido para o Desafio Genius Agenda AI.

**Estado atual:** base local implementada e acesso ao Google autorizado. As operações com eventos, os fluxos do n8n e o atendimento pelo WhatsApp ainda estão pendentes.

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
| 10. Revisão da base | Documentamos a execução e as decisões; revisamos o `.gitignore`, os arquivos públicos e as dependências. | Nove testes aprovados, dependências consistentes e credenciais, token e banco fora do Git. Commit e push desta base ainda pendentes. |

A validação Google identificou a **agenda**; ainda não consultou, criou, alterou ou excluiu eventos. O SQLite contém metadados internos, sem tabelas de mensagens ou lembretes.

## Arquitetura planejada

```text
WhatsApp → n8n → API Python + IA → Google Calendar
                        ↕
                      SQLite
```

O n8n coordenará os fluxos; Python concentrará interpretação, validações e operações; SQLite guardará o controle local de mensagens e lembretes. O Google Calendar será a referência dos compromissos. Será utilizada uma IA pronta por API.

Atualmente, API, n8n e comando de autorização Google funcionam separadamente. O Google Cloud foi usado para cadastrar e autorizar o aplicativo; a aplicação roda localmente.

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

## Validação realizada

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m app.database --check
```

- **9 testes locais:** quatro de persistência e transações; cinco de seleção segura da agenda.
- **Dependências:** nenhuma inconsistência encontrada por `pip check`.
- **Verificações reais:** API HTTP 200, n8n inicializado e autorização/descoberta da agenda no Google.

Ainda não há teste de ponta a ponta, CRUD de eventos ou envio de mensagens e lembretes.

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| `app/main.py` / `app/__main__.py` | Aplicação FastAPI e inicialização do servidor |
| `app/settings.py` | Leitura e validação das configurações |
| `app/database.py` | SQLite, metadados e transações |
| `app/google_calendar.py` | OAuth e descoberta da agenda de teste |
| `tests/` | Testes locais do banco e da seleção de agenda |
| `compose.yaml` | Instância local do n8n |
| `.env.example` / `requirements.txt` | Configuração pública e dependências fixadas |
| `docs/` | Guias de configuração e operação |

## Dados e operação

`.env`, `.venv/`, `secrets/` e `data/` são locais e ignorados pelo Git. O SQLite fica em `data/agenda.db`; os dados do n8n ficam no volume Docker `genius-agenda-ai_n8n_data`. Nunca publique tokens ou credenciais em código, logs, prints ou exports.

Para parar, use `Ctrl+C` no terminal da API e `docker compose stop` para o n8n. Para retomar, inicie o Docker, execute `docker compose up -d` e reinicie a API. Fechar o navegador ou parar os serviços preserva os dados; `docker compose down -v` remove o volume do n8n.

No OAuth externo em teste, com os escopos Calendar utilizados, o refresh token expira em sete dias. A aplicação local precisa estar ligada para executar automações. Consulte o [guia de configuração e diagnóstico](docs/configuracao.md) e a [operação do n8n](docs/n8n-local.md).

## Próximas etapas

1. Criar a primeira entrevista real; depois implementar consulta, alteração e exclusão de eventos.
2. Validar datas, fusos, ambiguidades, conflitos e duplicações.
3. Conectar n8n, API e WhatsApp de teste; adicionar a IA e o contexto da conversa.
4. Implementar lembretes persistentes e configuráveis, com recuperação de falhas e sincronização de alterações.
5. Adicionar resumos, briefings e imagem gerada por IA no dia da entrevista, com alternativa por texto.
6. Concluir testes de ponta a ponta, documentação de entrega e demonstração.
