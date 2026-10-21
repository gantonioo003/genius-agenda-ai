# n8n local

O n8n coordena os fluxos de automação. Nesta etapa ele está preparado para uso local; Google Calendar, WhatsApp e a API Python ainda serão conectados.

## Executar

1. Abra o Docker Desktop e aguarde a inicialização.
2. No terminal, dentro da pasta do projeto, execute:

```powershell
docker compose up -d
```

3. Acesse <http://localhost:5678> no navegador.
4. No primeiro acesso, crie o usuário administrador local com seu nome, e-mail e uma senha que você escolher. Essa conta pertence à instância local; não é uma assinatura do n8n Cloud.

## Comandos úteis

```powershell
# Estado e verificação de saúde
docker compose ps

# Ver os últimos registros
docker compose logs --tail 50 n8n

# Parar sem apagar os dados
docker compose stop

# Iniciar novamente
docker compose start
```

## Onde ficam os dados

Fluxos, credenciais e configurações ficam no volume Docker `genius-agenda-ai_n8n_data`, montado em `/home/node/.n8n`. Os dados não ficam dentro da pasta do Git ou do OneDrive. O volume continua existindo depois de parar ou recriar o contêiner. Isso garante persistência local, mas não substitui backup.

Não apague esse volume e não use `docker compose down -v` para uma parada normal: a opção `-v` remove os dados persistidos.

## Configuração desta instalação

- Imagem oficial com versão fixada: `docker.n8n.io/n8nio/n8n:2.44.4`.
- Porta `5678` disponível somente em `127.0.0.1`, no computador local.
- Fuso horário: `America/Fortaleza`.
- Reinício automático quando o Docker voltar, exceto se o contêiner tiver sido parado manualmente.
- Verificação de saúde via `/healthz/readiness`.
- Telemetria de diagnóstico desativada.

O Docker precisa estar em execução. Para receber mensagens de serviços externos, ainda será necessário configurar uma URL HTTPS pública de webhook. Essa conexão não faz parte da instalação local inicial.

## Próximo passo

Criar um fluxo simples no n8n e fazê-lo chamar um endpoint de teste da API Python. Credenciais reais devem ser configuradas no gerenciador de credenciais, sem exportá-las para o Git.

Referência: <https://docs.n8n.io/hosting/installation/docker/>.
