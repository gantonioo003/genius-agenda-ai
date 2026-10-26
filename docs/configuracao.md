# Configuração local

Este guia detalha como reproduzir o ambiente. O histórico do desenvolvimento e o estado da implementação estão no [README](../README.md).

## Reproduzir a configuração desde o início

Os comandos abaixo usam PowerShell na raiz do repositório. Ambiente validado: Windows, Python **3.14.3**, Git, Docker Desktop **29.4.0** e Docker Compose **5.1.2**. A imagem do n8n está fixada no `compose.yaml`.

### 1. Obter o projeto

```powershell
git clone https://github.com/gantonioo003/genius-agenda-ai.git
cd genius-agenda-ai
python --version
```

Se já tiver a pasta do projeto, entre nela e continue na próxima etapa. Se `python` abrir a Microsoft Store, use o caminho do executável Python instalado no lugar desse comando.

### 2. Preparar o Python e as configurações

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

O ambiente virtual separa as dependências deste projeto. Não é necessário ativá-lo: os comandos usam seu executável diretamente. A cópia condicional preserva um `.env` que já existe.

O arquivo `.env.example` contém apenas exemplos, sem chaves ou tokens. As variáveis de ambiente do sistema têm prioridade sobre o `.env`. Os caminhos relativos das configurações são resolvidos a partir da raiz do projeto.

| Configuração | Padrão | Uso |
|---|---|---|
| `APP_NAME` | `Genius Agenda AI` | Nome apresentado pela API |
| `APP_ENV` | `development` | Ambiente da aplicação |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | Endereço local da API |
| `LOG_LEVEL` | `info` | Nível dos registros do servidor |
| `DATABASE_PATH` | `data/agenda.db` | Arquivo SQLite local |
| `GOOGLE_CLIENT_FILE` | `secrets/google-calendar-client.json` | Cadastro OAuth privado |
| `GOOGLE_TOKEN_FILE` | `secrets/google-calendar-token.json` | Autorização da conta Google |
| `GOOGLE_CALENDAR_NAME` | `Genius Testes` | Nome da agenda separada de teste |
| `GOOGLE_CALENDAR_ID` | vazio | ID explícito opcional da agenda |

### 3. Iniciar e conferir a API

```powershell
.\.venv\Scripts\python.exe -m app
```

Abra <http://localhost:8000/health> ou, em outro terminal:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Resposta esperada com as configurações de exemplo:

```json
{
  "status": "ok",
  "service": "Genius Agenda AI",
  "environment": "development"
}
```

Isso confirma que o programa está respondendo. **Não comprova acesso ao Google, n8n, WhatsApp ou IA.** A documentação interativa está em <http://localhost:8000/docs>.

Para parar o processo iniciado nesse terminal, pressione `Ctrl+C`. Reinicie a API depois de alterar código ou configurações.

### 4. Criar e verificar o banco local

O SQLite já vem com o Python. A API cria `data/agenda.db` automaticamente quando inicia; também é possível inicializá-lo e conferir a gravação com:

```powershell
.\.venv\Scripts\python.exe -m app.database --check
```

O comando grava um registro temporário, confirma a transação, fecha a conexão e lê o registro por outra conexão. Ao terminar, remove somente seu próprio registro de teste. O resultado esperado contém `"status": "ok"`.

O esquema SQLite 2 contém `app_metadata` (versão e identificação da agenda), `creation_requests` e `creation_operations` (identidade persistente das criações). A inicialização migra o esquema 1 em uma transação, preservando os metadados. As consultas usam parâmetros; erros desfazem a transação em andamento. A reserva de criação é confirmada antes de chamar o Google e permanece para recuperar tentativas incertas. Não há cópia local dos textos das entrevistas nem tabelas de mensagens ou lembretes. Consulte [duplicações e cuidados com backup](duplicacoes.md).

O banco fica na pasta `data/`, ignorada pelo Git. Parar a API ou desligar o computador preserva esse arquivo. O banco do n8n fica em outro lugar: o volume Docker.

### 5. Subir o n8n local

Abra o Docker Desktop, aguarde sua inicialização e execute:

```powershell
docker compose up -d
docker compose ps
```

Abra <http://localhost:5678>. No primeiro acesso, crie o usuário administrador dessa instalação local. Essa conta não exige uma assinatura do n8n Cloud.

Os dados ficam no volume `genius-agenda-ai_n8n_data`. A configuração expõe a porta apenas no próprio computador e inclui uma verificação de saúde do n8n. Ela não instala nem configura workflows automaticamente.

Para comandos, persistência e detalhes da instalação, consulte [n8n local](n8n-local.md).

### 6. Preparar a agenda e o acesso Google

Esta é a sequência usada para autorizar o projeto. Quem clonar o repositório precisa configurar e autorizar a própria conta; os arquivos privados não acompanham o clone.

1. No Google Agenda, abra **Configurações → Adicionar agenda → Criar nova agenda**. Crie uma agenda separada chamada `Genius Testes` e confira seu fuso horário.
2. No Google Cloud, crie e selecione um projeto chamado `Genius Agenda AI`.
3. Em **APIs e serviços → Biblioteca**, procure **Google Calendar API** e clique em **Ativar** no projeto selecionado.
4. Em **Google Auth Platform**, configure o aplicativo: nome `Genius Agenda AI`, e-mail de suporte, público **Externo** e e-mail de contato. Mantenha a publicação em **Testing / Testando** nesta fase.
5. Em **Público-alvo → Usuários de teste**, adicione a conta que possui a agenda `Genius Testes`.
6. Em **Clientes**, crie um cliente OAuth do tipo **App para computador**, com nome como `Genius Agenda AI Local`.
7. Baixe o JSON do cliente e guarde-o como `secrets/google-calendar-client.json`. Crie a pasta `secrets` se ainda não existir. Não cole o conteúdo no README, em prints ou no Git.

Os nomes dos menus podem variar conforme o idioma do painel. A autorização OAuth é a permissão dada pelo usuário ao aplicativo; a senha da conta não é guardada pelo projeto.

### 7. Autorizar e confirmar a conexão Google

```powershell
.\.venv\Scripts\python.exe -m app.google_calendar --authorize
```

Quando precisa de autorização, o comando abre o navegador. Entre na conta de teste, confira o nome do aplicativo e autorize as duas permissões solicitadas:

- `calendar.calendarlist.readonly`: localizar as agendas da conta.
- `calendar.events.owned`: consultar, criar, alterar e excluir eventos em agendas próprias.

Se aparecer o aviso de aplicativo em teste, confira se é o aplicativo que você acabou de cadastrar antes de continuar. O retorno do login usa uma porta temporária de `127.0.0.1`, com PKCE. O processo aguarda até dez minutos.

O token é salvo em `secrets/google-calendar-token.json`. A opção `--authorize` **reaproveita um token válido**; ela permite abrir o login quando necessário e não força troca de conta. O início da API não abre login automaticamente.

Para conferir depois sem iniciar um login interativo:

```powershell
.\.venv\Scripts\python.exe -m app.google_calendar
```

Resultado real obtido nesta máquina em 21/10/2026:

```json
{
  "status": "ok",
  "calendar": "Genius Testes",
  "timezone": "America/Recife",
  "access": "owner",
  "operation": "authorization_and_calendar_discovery_only"
}
```

O comando consulta a lista de agendas no Google, identifica a agenda escolhida e salva seu ID no SQLite. Ainda não lê nem modifica eventos. Falhas de autorização, API desativada ou agenda ausente não recebem uma resposta simulada de sucesso.

O escopo de eventos abrange agendas próprias autorizadas; a seleção de uma única agenda é uma proteção do código. Ele exige uma agenda separada, pertencente ao usuário, e rejeita a agenda principal. Se houver nomes repetidos, configure `GOOGLE_CALENDAR_ID` no `.env`. Um ID explícito tem prioridade sobre o ID salvo; um ID salvo ou configurado ausente produz erro, sem escolher outra agenda silenciosamente.

No modo OAuth externo **Testing**, com permissões Calendar, o refresh token expira em sete dias. Se a autorização expirar ou for revogada, execute novamente com `--authorize`. Isso não apaga o projeto nem a agenda. A renovação de um access token expirado é tentada automaticamente enquanto o refresh token for aceito.

## Parar hoje e continuar depois

- Pode fechar as abas do Google: o projeto, a agenda e a autorização já salva continuam existindo.
- Para parar o n8n sem apagar seus dados, execute `docker compose stop`. Sair do Docker Desktop também interrompe os contêineres.
- Para parar a API iniciada no terminal, use `Ctrl+C`. Desligar o computador interrompe os serviços locais.
- Para continuar, abra o Docker Desktop, execute `docker compose up -d` e inicie a API com `.\.venv\Scripts\python.exe -m app`.

Não use `docker compose down -v` para encerrar o trabalho: essa opção remove o volume com os dados do n8n. Lembretes e recebimento de mensagens não ficam ativos com este computador desligado; a implantação contínua será definida depois.

## Problemas comuns

| Situação | O que conferir |
|---|---|
| `python` abre a Microsoft Store | Use o executável Python instalado para criar a `.venv` |
| Docker não conecta | Abra o Docker Desktop e espere o mecanismo iniciar |
| Porta 8000 ou 5678 ocupada | Confira se a instância do próprio projeto já está aberta antes de iniciar outra |
| OAuth retorna acesso negado | Confira o projeto selecionado, a conta cadastrada como usuário de teste e as permissões concedidas |
| Cliente OAuth não encontrado | Confira o arquivo e o caminho de `GOOGLE_CLIENT_FILE` |
| Autorização expirada ou revogada | Rode o comando Google com `--authorize` e conclua o consentimento |
| Agenda não encontrada ou ambígua | Confira a conta autorizada, a agenda separada, o nome e o ID explícito ou salvo |
| `.env` alterado sem efeito | Reinicie o processo e confira se uma variável do sistema está sobrescrevendo o arquivo |

## Referências usadas

- [Google Calendar: guia inicial Python](https://developers.google.com/workspace/calendar/api/quickstart/python)
- [Google Calendar: permissões de acesso](https://developers.google.com/workspace/calendar/api/auth)
- [Google OAuth: expiração de tokens](https://developers.google.com/identity/protocols/oauth2#expiration)
- [n8n: instalação oficial com Docker](https://docs.n8n.io/hosting/installation/docker/)


### Falhas de acesso e operação

O transporte aplica timeout de 20 segundos por tentativa, com TLS verificado e sem redirecionamentos ou reenvios implícitos. Leituras usam até duas retentativas do SDK; escritas são enviadas uma vez e conferidas por ID quando a resposta é incerta. As CLIs apresentam erros seguros de autorização, conexão e banco, sem JSON de sucesso. Consulte [recuperação e comandos de verificação](falhas-recuperacao.md).
