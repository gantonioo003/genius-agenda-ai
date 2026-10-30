# Painel web Genius Agenda AI

HTML, CSS e módulos JavaScript nativos, sem compilação ou dependências frontend. Há dois ambientes distintos: painel conectado com consultas e operações protegidas, e um modo demonstração completo com exemplos fictícios.

## Painel conectado — porta 8000

Na raiz do repositório, com a configuração privada existente preparada, inicie a API se necessário:

```powershell
.\.venv\Scripts\python.exe -m app
```

Abra <http://127.0.0.1:8000/painel/> e clique em **Acessar minha agenda**. A tela mostra um código de pareamento. Autorize somente o código da própria aba pelo comando exibido, em um terminal local na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m app.panel_access authorize ABCD-EFGH
```

O código acima é fictício; use o da tela. Ele vale por até cinco minutos. Após a aprovação, a sessão usa cookie `HttpOnly` e `SameSite=Strict`, com 30 minutos de inatividade e limite total de oito horas. **Sair** revoga a sessão. Nenhuma chave de serviço ou de provedor precisa ser digitada no navegador.

Nesta fase, o painel conectado permite:

- Consultar eventos por dia, semana e mês, com pesquisa no período carregado.
- Abrir detalhes completos e o contexto factual da entrevista selecionada.
- Criar, editar, reagendar e excluir com prévia e confirmação vinculadas à operação no servidor.
- Acompanhar lembretes com filtros, paginação, motivos, histórico e prévia privada dos cards disponíveis.
- Usar **Preparo** nas abas Entrevista, Resumos e Horários livres, com geração/busca explícitas.

O painel e o WhatsApp usam a mesma agenda configurada. A criação pede título, início, término e descrição opcional; local pode ser acrescentado depois em **Editar**. Eventos de dia inteiro podem ser editados e excluídos, mas não reagendados pelo formulário atual. Briefing/checklist usam dados cadastrados e notas fornecidas, sem IA ou pesquisa externa; disponibilidade é calculada e não reserva. Somente o resumo pode ser organizado por IA, com alternativa factual identificada. As funções continuam disponíveis no WhatsApp. Abrir/atualizar a tela não chama IA, altera a agenda ou dispara envios.

O acesso é para o proprietário no próprio computador, restrito a loopback e à porta configurada. `8000` é a porta padrão; se `API_PORT` tiver sido alterada, use seu valor. Este mecanismo não implementa acesso remoto ou múltiplos usuários. [Acesso, rotas, segurança e limites](../docs/painel-web-api.md).

## Preparo no painel conectado

Escolha **Entrevista**, **Resumos** ou **Horários livres**. Gerar resumo, preparar entrevista e buscar horários exigem ações explícitas; nenhuma dessas ferramentas grava no Google ou envia mensagens.

Notas e marcações do checklist ficam na memória da página, por entrevista/preparo, e se perdem ao recarregar ou sair. Elas não são enviadas ao LLM. Resumo usa período/data; horários livres usam datas, faixa diária, duração e margem. Mudar parâmetros invalida resultados anteriores. [Uso e limites](../docs/painel-web-preparo.md).

## Lembretes e cards no painel conectado

Use **Pendentes**, **Envios aceitos**, **Atenção** ou **Todo o histórico** e abra um registro para conferir horário previsto, estado, motivo e transições. A contagem e a paginação seguem o filtro selecionado. Um horário já passado não transforma automaticamente um estado pendente em envio realizado. **Aceito pelo provedor** não confirma entrega nem leitura no WhatsApp; a tela não repete envios.

**Formato previsto** considera a configuração atual e não descreve necessariamente uma mensagem antiga. Quando há card previsto, **Visualizar card** consulta `GET /painel/api/reminders/{id}/card-preview` com a sessão local e reconstrói uma imagem privada a partir dos dados atuais da agenda. A prévia é identificada como **não enviada** e não é uma cópia histórica. Compromissos removidos ou alterados bloqueiam a imagem; os dados do aviso permanecem em texto.

A composição reutiliza o fundo local, sem Gemini, publicação de mídia ou WhatsApp. O PNG não é armazenado como card público; temporários são removidos e a resposta usa cache privado desabilitado. Imagens e resultados antigos são descartados ao mudar a seleção ou encerrar a sessão. [Uso, limites e validação](../docs/painel-web-lembretes.md).

## Demonstração fictícia

Com a API ativa, abra <http://127.0.0.1:8000/painel/demo/>. O modo é separado dos dados reais e não exige pareamento. O selo **Demonstração • dados fictícios** permanece visível, inclusive nos diálogos e no celular. O cenário representa 27/10/2026, às 12h.

Também é possível abrir o mesmo modo em um servidor estático independente, sem os serviços da API:

```powershell
.\.venv\Scripts\python.exe -B scripts/preview_panel.py
```

Abra <http://127.0.0.1:8015>. Para outra porta, acrescente `--port 8016`. Esse servidor escuta apenas no próprio computador e serve somente os arquivos permitidos da demonstração; não oferece arquivos do painel conectado nem rotas da API. Encerre com `Ctrl+C`.

A demonstração permite experimentar as quatro telas e simular CRUD, conflitos, confirmações, checklist, resumo, horários livres e cards. Os dados ficam apenas em memória e são restaurados ao recarregar, com referência fixa em 27/10/2026. Não chama Google Calendar, Gemini, n8n, SQLite ou WhatsApp. **Aceito pelo provedor** não é apresentado como entregue ou lido.

Em **Sobre a demonstração**, experimente carregamento, ausência de resultados e erro. É possível navegar por teclado, fechar detalhes com `Esc`, usar tela estreita e respeitar a preferência de movimento reduzido do sistema. Abrir `index.html` diretamente como arquivo não carrega os módulos; use um dos servidores HTTP acima.

## Organização

- `live.html`, `live.js` e `live.css`: consultas, formulários com confirmação, preparo e pareamento local.
- `index.html`, `app.js`, `demo.js` e `demo.css`: demonstração completa com exemplos isolados, guia de exploração e reset.
- `styles.css` e recursos visuais locais: base de apresentação compartilhada.

Não há recursos visuais remotos. A imagem reutiliza `assets/cards/interview-background-v1.png`. Texto externo é escapado na renderização; a interface conectada consulta somente a própria origem, e a demonstração bloqueia conexões.

[Telas e funcionamento](../docs/painel-web.md) · [Registro histórico do visual](../docs/painel-web-visual.md) · [Acesso e rotas](../docs/painel-web-api.md) · [Agenda, validação e limites da fase 4](../docs/painel-web-agenda.md).

## Registro histórico da validação da fase 4

**2.665 testes Python e 39 testes Node aprovados.** O navegador conferiu os fluxos completos com aplicativo e stores reais ligados a um calendário isolado, incluindo semana, mês e celular. Na API instalada, o ensaio Google somente preparou e cancelou uma prévia; agenda e ledgers de execução/lembretes ficaram inalterados. Não houve novo CRUD gravado no Google nesse ensaio.

## Validação da fase 5

Testes, conferências do navegador e eventuais verificações reais são registrados no [fechamento do preparo](../docs/painel-web-preparo.md), separados das contagens históricas acima. A tela distingue organização por IA, fatos fornecidos e cálculo de disponibilidade; falha de provedor não vira resultado vazio bem-sucedido.

## Demonstração: exploração e isolamento

Os atalhos da visão geral levam à agenda, ao preparo e a um card fictício. **Restaurar demonstração**, no cabeçalho, ou **Restaurar exemplos**, nas informações, limpa formulários, confirmações, filtros e checklist e volta ao cenário inicial. Recarregar também descarta a simulação. Duas abas são independentes.

Reagendar cancela os avisos anteriores e cria novos exemplos; excluir cancela os pendentes. Os snapshots e os históricos já aceitos/incertos são preservados. A confirmação é revalidada antes de mudar os exemplos; nada sai do navegador. Os estados de bloqueio, incerteza e prazo encerrado são explicitamente simulados. [Uso, validação e limites](../docs/painel-web-demonstracao.md).


## Validação final e apresentação

Na raiz do repositório:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/panel_frontend.test.mjs tests/panel_demo.test.mjs
```

Fechamento: **2.707 testes Python e 105 testes Node aprovados**, sem falhas ou testes ignorados. A revisão manual e de código abrangeu teclado, foco, diálogos, contraste e movimento reduzido; não é certificação de acessibilidade.

O [tour visual](../docs/assets/painel-tour.mp4) usa cenas reais da demonstração fictícia, editadas e sem narração. O tempo de cada cena é editorial, não uma medida de desempenho. [Roteiro para apresentar](../docs/apresentacao-painel.md) · [Resultados e limites](../docs/fechamento-painel-web.md).
