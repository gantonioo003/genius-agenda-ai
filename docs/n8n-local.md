# n8n local

O n8n coordena os fluxos de automação. A instância local já tem um fluxo publicado de consultas por webhook → API Python → Google Calendar. WhatsApp e IA entram nas próximas etapas.

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

## Primeiro fluxo publicado

O [guia de consulta por webhook](n8n-primeiro-fluxo.md) reúne o export, a configuração e o comando de teste. O fluxo consulta dia, semana e mês. As chaves ficam no gerenciador privado; `secrets/n8n-local.json` guarda a chave de entrada e os IDs locais, fora do Git.

A API precisa estar ligada em `127.0.0.1:8000`. Nesta instalação, o Docker Desktop alcançou essa porta por `host.docker.internal`, sem ampliar o bind para a rede. Não é necessário importar novamente o fluxo ao reiniciar os serviços.

Referência: <https://docs.n8n.io/hosting/installation/docker/>.

## Respostas ao usuário

O fluxo de consultas inclui `presentation`, com mensagem clara, tipo e próximo passo. Para ver apenas a mensagem, use `scripts/test_n8n.py --date 2026-10-23 --text-only`. A [camada de respostas](n8n-respostas-falhas.md) também foi conferida localmente com dúvidas, conflitos e resultados de escrita fictícios. Ela não executa nem repete operações.

## Teste integrado e disparo agendado

A parte 9 adicionou [um ensaio reproduzível](n8n-testes-integracao.md) com CRUD na API isolada `8001` e um Schedule Trigger que consulta o status da API normal. Os dois fluxos são publicados temporariamente pelo runner, despublicados ao terminar e exportados inativos. O fluxo normal de consultas permanece publicado. O ensaio não configura lembretes nem altera compromissos Google reais.

## Fechamento e exports

A etapa 09 foi [concluída](fechamento-etapa-09.md) com três exports públicos conferidos contra a instância. O [inventário dos fluxos](../n8n/README.md) distingue o fluxo normal das fixtures e aponta os comandos de reprodução. O [manifesto](../n8n/exports-manifest.json) registra seus hashes. Os exports estão inativos, sem credenciais ou dados de execução; na instância, somente a consulta normal permanece publicada.
