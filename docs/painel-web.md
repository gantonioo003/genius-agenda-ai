# Painel web — telas e experiência

Definição da fase 1 da etapa 15, em 27/10/2026. Objetivo: oferecer uma interface para acompanhar a agenda, preparar entrevistas e entender os lembretes, usando os serviços existentes do Genius Agenda AI.

**Estado atual:** fases 1 a 8 concluídas no escopo documentado. A área [Preparo](painel-web-preparo.md) reúne entrevista, resumos e horários livres. Além do [acesso protegido](painel-web-api.md), a [agenda do painel](painel-web-agenda.md) permite consultar por dia, semana e mês, criar, editar, reagendar e excluir com confirmação vinculada no servidor. O painel e o WhatsApp usam a mesma agenda configurada; a demonstração fictícia permanece separada. As descrições abaixo conservam a definição da experiência; limites e diferenças da implementação estão nos guias de cada área e no [fechamento do painel](fechamento-painel-web.md). A entrega e o vídeo de 26/10 continuam como históricos; esta evolução é para o portfólio.

## Navegação

Quatro destinos principais, sempre na mesma ordem:

| Destino | Pergunta que responde | Endereço atual |
|---|---|---|
| Visão geral | O que merece minha atenção agora? | `/painel/#visao-geral` |
| Agenda | Quais compromissos tenho? | `/painel/#agenda` |
| Preparo | Qual é o contexto da entrevista escolhida? | `/painel/#preparo` |
| Lembretes | Quando serei avisado e o que aconteceu com os envios? | `/painel/#lembretes` |

No celular, navegação inferior com ícone e nome; cabeçalho curto e conteúdo em uma coluna. No computador, menu lateral discreto, área principal com largura confortável de leitura e uma coluna auxiliar somente quando acrescentar contexto. Detalhes abrem sobre a tela atual; voltar conserva período, filtros e posição da lista.

O WhatsApp continua como canal de conversa e operações. Os formulários do painel também criam, editam, reagendam e excluem, reutilizando preparação, confirmação e executores existentes. As duas entradas usam a mesma agenda, sem copiar eventos entre canais. A interpretação de linguagem natural continua pertencendo ao agente Gemini. Um segundo chat completo dentro do painel fica fora desta primeira versão.

## 1. Visão geral

**Hierarquia:** data de hoje → próxima entrevista → agenda do dia → atenção aos lembretes.

- Próximo compromisso com título, dia, horário, local ou link disponível e ação **Preparar entrevista**. Se houver compromissos simultâneos, mostrar o grupo para escolha.
- Lista curta de hoje, ordenada pelo início, com acesso ao detalhe e à agenda completa. Eventos de dia inteiro aparecem identificados, sem horário inventado.
- Resumo compacto com quantidade de compromissos no dia e lembretes que precisam de atenção. Só apresentar contagens completas quando a fonte confirmar o total.
- Ações **Novo compromisso** e **Ver minha semana**, com destaque principal para a ação mais relevante no contexto.
- Estado de atualização discreto: horário da última consulta e erro local do bloco afetado. A saúde da API não vira selo de que todas as integrações estão funcionando.

**Sem compromissos:** “Seu dia está livre”, com ações de consultar outra data ou criar um compromisso. **Falha de leitura:** “Não foi possível atualizar sua agenda”, preservando dados anteriores identificados como desatualizados; não declarar o dia livre.

## 2. Agenda

**Hierarquia:** período e navegação de datas → Dia / Semana / Mês → compromissos → detalhe contextual.

- Abrir no dia atual. Botão **Hoje**, controles de anterior/próximo e seleção de data.
- Dia e semana com ordem cronológica; mês com calendário e indicação de compromissos. No celular, selecionar o dia no calendário mostra a lista logo abaixo.
- Pesquisa por título no período carregado, claramente identificada como pesquisa nesse período. A busca global por nome do agente não deve ser confundida com esse filtro.
- Detalhe com título, data e horário, descrição, local/link, situação e ações **Editar**, **Reagendar**, **Preparar** e **Excluir**.
- O item aberto conserva o identificador do evento internamente. A pessoa não precisa copiar IDs ou redigitar data e horário antigos.
- **Novo compromisso:** título, início, término e descrição opcional, com duração calculada e erros junto aos campos. O contrato atual de criação não aceita local; ele pode ser acrescentado depois em **Editar**.
- **Editar:** título, descrição e local. **Reagendar:** pedir o novo intervalo, apresentando o atual como referência. Eventos de dia inteiro podem ser editados ou excluídos; o formulário de reagendamento aceita somente eventos com horário.
- Antes de criar, editar, reagendar ou excluir, mostrar uma prévia legível e pedir confirmação explícita. Na edição e no reagendamento, destacar o que muda.
- A confirmação deve ficar vinculada no servidor ao evento, à proposta exata e à versão lida. A fase 4 reutiliza proposta → aprovação → execução do agente, adaptando a entrada dos formulários. O navegador confirma o identificador de uma operação persistida; não envia um PATCH direto com dados que possam mudar após a prévia.
- Conflitos mostram os eventos envolvidos e bloqueiam a gravação. Dados alterados externamente exigem nova leitura e nova confirmação. A busca de horários livres está disponível em **Preparo**, sem criar reservas.

No computador, detalhes em painel lateral. No celular, folha que sobe da base, com conteúdo rolável e ação principal acessível ao polegar. O gesto de fechar tem alternativa por botão e teclado; alterações não salvas pedem uma decisão.

**Exemplo de apresentação:** “Entrevista técnica · Amanhã, 11h às 11h30”. Em um dia selecionado, não repetir a data em cada linha. Não exibir ISO, UTC, ETag ou IDs internos na experiência normal.

## 3. Preparo

Três abas: **Entrevista**, **Resumos** e **Horários livres**. Cada uma tem formulário, carregamento, resultado e erro próprios; a ausência de entrevistas não bloqueia as outras duas ferramentas.

- **Entrevista:** seleção recebida da Agenda ou da Visão geral, dados atuais e campo opcional de até 1.200 caracteres para informações recebidas ou pontos a revisar. O botão **Preparar entrevista** pede explicitamente o briefing/checklist ao servidor.
- **Briefing e checklist:** composição factual, com descrição e local cadastrados, notas fornecidas e até seis sugestões genéricas. Não chama Gemini ou pesquisa empresa, cargo, requisitos ou entrevistadores. Dados ausentes são indicados; informações não são deduzidas do título.
- **Checklist marcado:** progresso e notas ficam somente na memória da página, separados por entrevista/preparo. Não há persistência dessas marcações no SQLite ou Google; recarregar ou sair descarta esse progresso.
- **Resumos:** semana ou mês, com data de referência e geração explícita. A IA organiza os agregados preparados pelo serviço existente; títulos, descrições, notas do briefing e identificadores de eventos não integram o pedido ao modelo. Falha/limite da IA mantém um resumo factual identificado; falha da leitura da agenda não vira resumo vazio.
- **Horários livres:** datas, faixa diária, duração e margem antes/depois dos compromissos. Cálculo determinístico, sem IA; até três alternativas legíveis, que podem se sobrepor. A consulta não reserva e a disponibilidade pode mudar. Nesta fase, usar uma alternativa exige iniciar a criação ou o reagendamento na Agenda.

Abrir a aba, trocar entrevista, marcar checklist ou atualizar consultas gerais não gera resumo nem refaz automaticamente uma chamada de IA. Alterar parâmetros invalida o resultado anterior; respostas atrasadas não substituem a seleção atual. [Uso e limites do preparo](painel-web-preparo.md).

## 4. Lembretes

**Hierarquia:** próximos avisos → itens que precisam de atenção → histórico filtrável → detalhe.

- Mostrar evento, horário do compromisso, momento do aviso, formato e situação.
- Destacar a regra atual: card com 1 hora de antecedência e texto com 15 minutos. Configuração de intervalos pelo painel fica para evolução posterior.
- Filtros simples: **Próximos**, **Aceitos pelo provedor**, **Atenção** e **Histórico**. O histórico inclui cancelados por reagendamento/exclusão e avisos expirados, com motivo legível.
- Detalhe com linha do tempo e prévia do card quando houver imagem disponível. Imagem ausente ou expirada recebe uma explicação; não bloquear o texto do lembrete.
- Não oferecer reenvio automático de um resultado incerto. A primeira versão será de acompanhamento; comandos operacionais de envio/recuperação ficam fora da navegação comum.

### Vocabulário dos estados

| Estado existente | Texto planejado | Significado |
|---|---|---|
| `pending` | Agendado | Aguarda o momento do aviso |
| `reserved` | Preparando envio | O sistema reservou esta tentativa |
| `sending` | Enviando | A tentativa foi iniciada |
| `accepted` | Aceito pelo provedor | O provedor aceitou; não comprova entrega ou leitura |
| `blocked` | Aviso bloqueado | Exibir o motivo disponível em linguagem simples |
| `uncertain` | Envio não confirmado | Conferir o estado antes de repetir |
| `cancelled` | Cancelado | Exibir o motivo, como reagendamento ou exclusão |
| `expired` | Prazo encerrado | O intervalo de envio já passou |

Não fabricar estados “Entregue” ou “Lido”. Falhas operacionais devem aparecer pelos registros e motivos reais; não há um estado persistido genérico `failed` nesse ledger.

## Referências e direção visual

Referência: três capturas de Instagram enviadas em 27/10/2026 e preferência explícita pela experiência mobile. Aproveitar a hierarquia limpa, contraste, áreas de toque confortáveis, superfícies discretas e transições contínuas, com identidade própria do Genius Agenda AI. As fotos, conversas e dados pessoais dessas referências não integram os recursos públicos do projeto.

### Aparência

- Tema escuro inicial: fundo próximo de `#0B0E11`, superfície `#15191F`, texto principal `#F5F5F7`, secundário `#A8AFB9` e divisórias `#292E36`. Valores são propostas a conferir na fase visual, incluindo contraste.
- Um acento violeta, próximo de `#7568FF`, e transição pontual para `#C254D9` em foco visual. Concentrar cor na ação principal, seleção e pequenos indicadores.
- Tipografia sem serifa, títulos objetivos e horários com boa leitura. Conteúdo com espaço para respirar, poucos contornos e profundidade discreta.
- Ícones de traço simples, com rótulo na navegação; cantos suaves em controles, folhas e cartões. Evitar colocar cada informação em um novo cartão.
- Faixa horizontal compacta de dias no celular, com seleção clara e rolagem natural. Aproveitar a familiaridade dos elementos circulares para datas, sem criar uma área social artificial.
- Card de entrevista como destaque visual contextual; a imagem não domina toda a agenda nem substitui os dados legíveis.

### Movimento e resposta da interface

- Navegação preserva o cabeçalho e os controles comuns; conteúdo entra com deslocamento curto e mudança de opacidade. Planejar 180–240 ms como ponto de partida.
- O compromisso selecionado mantém continuidade visual ao abrir detalhes. No celular, a folha acompanha o gesto de abertura/fechamento de forma previsível.
- Ao passar o mouse ou focar pelo teclado, dar destaque sutil ao item e permitir uma prévia curta de informações já carregadas. Na tela de toque, o toque abre os mesmos detalhes.
- Controles respondem imediatamente ao toque, seleção e foco. Campos mostram validação próxima do problema; sucesso atualiza o compromisso e confirma a ação com uma mensagem curta.
- Carregamento inicial usa espaços reservados no formato do conteúdo; atualizações conservam a estrutura da tela. Mostrar progresso por etapa quando conhecido, sem porcentagem ou previsão inventada.
- Rolagem e posição preservadas ao voltar. Sem trocas automáticas de tela, atalhos escondidos indispensáveis ou mudanças de agenda causadas por hover/gesto.
- Prévia não dispara Gemini, escrita, envio WhatsApp ou processamento de lembretes. O efeito visual usa dados disponíveis; consultas caras partem de uma ação explícita.
- Respeitar preferência de movimento reduzido, manter foco visível, oferecer controles por teclado e garantir que o movimento não seja necessário para entender ou usar a tela.

## Comportamentos comuns

- Datas e horas em português: “Hoje, 16h às 16h30”, “Amanhã, 9h” ou “27/10/2026”. O fuso efetivo da agenda guia os cálculos; a identificação do fuso aparece em informações/configuração ou quando necessária para evitar confusão, sem repetir “Horário de Recife” em cada resposta.
- Cada área tem carregamento, vazio, erro e nova tentativa próprios. Falha de consulta não produz contagens zero nem apaga fatos já exibidos.
- Toda escrita só anuncia conclusão quando a API confirmar o resultado. Cliques repetidos não geram novas operações, e um timeout não autoriza repetir uma gravação.
- Texto externo da agenda aparece como texto seguro. Links têm ação identificável; códigos e diagnósticos técnicos ficam fora da tela principal.
- O navegador não recebe a chave de serviço da API, chaves Gemini, OAuth Google ou acesso ao SQLite. O painel usa sessão local aprovada pelo proprietário e uma camada de consultas e comandos no servidor; [procedimento de acesso](painel-web-api.md#abrir-e-autorizar-o-navegador).
- A demonstração em `/painel/demo/` tem identificação permanente, data de cenário explícita e dados fictícios isolados em memória. Ela não acessa provedores nem usa consultas reais; o fluxo completo da fase 7 inclui operações simuladas com confirmação e restauração dos exemplos.

## Base técnica existente e lacunas

A API de serviços mantém o prefixo `/api/v1`; o navegador conectado usa a camada própria `/painel/api`, protegida por sessão local. Esta tabela é referência técnica, não conteúdo da interface.

| Necessidade | Base disponível | Estado / próximo trabalho |
|---|---|---|
| Visão geral e agenda | `GET /painel/api/events` com dia, semana e mês | Conectado na fase 3; pesquisa somente no período carregado |
| Detalhes completos | `GET /painel/api/events/{event_id}` | Descrição e local completos, com validação da identidade e da versão consultada |
| Criar/editar/reagendar | `POST /painel/api/agenda/preview` e confirmação da operação | Conectados na fase 4, com validações, conflitos, versão esperada e executores do agente |
| Exclusão | A mesma prévia de operação e confirmação no servidor | Conectada na fase 4, vinculada ao evento exato e à versão relida |
| Agente e confirmações | Serviços de preparação, confirmação e execução já usados pelo WhatsApp | Reutilizados pelo painel; prévia, confirmação, cancelamento e estado têm rotas próprias protegidas |
| Resumo, disponibilidade e briefing | POSTs protegidos com os serviços existentes | Conectados na fase 5; somente o resumo usa organização opcional por IA |
| Acompanhar lembretes | `GET /painel/api/reminders` e `GET /painel/api/reminders/{reminder_id}` | Listagem, paginação, motivos e histórico conectados; leitor SQLite somente de leitura |
| Saúde e acesso | Sessão local e `GET /painel/api/connection/status` | Acesso e resposta da API verificados; não declarar prontidão agregada dos provedores |
| Prévia de cards e checklist marcado | Gerador de card e briefing factual existentes | Checklist em memória na fase 5; PNG privado com dados atuais e rótulo de prévia na fase 6 |
| Demonstração | `/painel/demo/` e servidor estático independente na porta 8015 | Cenário completo isolado em memória, com operações simuladas e restauração dos exemplos |

Os POSTs de `tick`, `dispatch` e `recover` dos lembretes fazem trabalho operacional. **Abrir ou atualizar uma tela não os chama.** Os GETs novos projetam dados mínimos, sem telefone, vínculo privado ou identificadores de tentativas. O formato do aviso é identificado como previsto; aceite do provedor não comprova entrega ou leitura.

Não existe `GET /events?title=` na API de serviços. O filtro visual usa os eventos do período retornado; a busca global por nome do agente não foi exposta ao painel. Os serviços de CRUD permanecem disponíveis pelo WhatsApp. Credenciais podem ser renovadas e metadados locais do vínculo Google atualizados durante a leitura, conforme o conector existente; a consulta não altera eventos ou envia mensagens. [Contrato e limites da fase 3](painel-web-api.md).

## Sequência de construção

| Fase | Entrega | Estado |
|---|---|---|
| 1. Definir telas | Este documento: navegação, conteúdos, fluxos, limites e direção visual | Concluída como definição |
| 2. Criar o visual | Protótipo responsivo das quatro telas, componentes e transições | Concluída como prévia local |
| 3. Conectar à API | Sessão local, agenda/detalhes reais e lembretes/histórico protegidos | Concluída para consultas reais |
| 4. Montar a agenda | Consultas e CRUD pelo painel com confirmação e estados persistidos | Concluída; [validação e limites](painel-web-agenda.md) |
| 5. Exibir preparo | Entrevista com briefing/checklist factual, resumo e cálculo de horários livres | Concluída; [uso e limites](painel-web-preparo.md) |
| 6. Mostrar lembretes e cards | Filtros, contagens, histórico e prévia privada; sem executar disparos | Concluída; [uso e limites](painel-web-lembretes.md) |
| 7. Modo demonstração | Dados fictícios, operações com confirmação, preparo, avisos e reset | Concluída; [explorar e limites](painel-web-demonstracao.md) |
| 8. Testar e apresentar | Fluxos, acessibilidade, documentação, capturas e tour visual do painel | Concluída; [validação e materiais](fechamento-painel-web.md) |

### Critérios para a fase visual

As quatro telas devem ser reconhecíveis sem explicação, com uma hierarquia principal e navegação consistente. O protótipo deve demonstrar desktop e celular, abrir/fechar detalhes, trocar períodos e mostrar estados de carregamento/vazio/erro/confirmação com dados fictícios identificados. Dados demonstrativos não constituem evidência de integração real. Transições e atalhos precisam continuar compreensíveis com movimento reduzido e uso por teclado.

## Conferência histórica da fase 1

Leitura estática de `app/main.py`, rotas `app/api_*.py`, `app/reminder_store.py` e documentação de preparo, segurança e operação. Nenhuma rota nova, teste de provedor, alteração de agenda ou mensagem WhatsApp foi executada para esta fase. A suíte de 2.530 testes citada no README pertence ao marco funcional anterior, não valida um painel ainda não implementado.


## Atualização da fase 2

Em 27/10/2026 foi construída a [prévia visual local](painel-web-visual.md), com navegação e interações sobre exemplos em memória. A conferência do navegador e as capturas correspondem ao protótipo; não comprovam a integração da próxima fase.

## Atualização da fase 3

Em 27/10/2026, o painel em `http://127.0.0.1:8000/painel/` passou a usar consultas reais protegidas por pareamento local. A sessão pendente vale cinco minutos; a aprovada tem 30 minutos de inatividade e limite de oito horas. A prévia histórica permanece em `/painel/demo/` ou no servidor independente da porta 8015.

A validação do marco fechou com 2.605 testes Python e 17 testes Node do frontend aprovados. Leituras locais reais de agenda, detalhe, lembretes e histórico passaram, com o banco de lembretes inalterado. A leitura não cria/edita/exclui eventos, chama IA ou envia mensagens. O acesso é de proprietário único no próprio computador; não implementa hospedagem pública ou múltiplos usuários. [Acesso, contrato, evidências e limites](painel-web-api.md).

## Atualização da fase 4

Em 28/10/2026, os formulários de criação, edição, reagendamento e exclusão passaram a usar o fluxo persistido de prévia → confirmação → execução. A seleção conserva o evento correto, confirmações antigas não autorizam novas mudanças e resultados incertos bloqueiam repetição automática. Consultas por dia, semana e mês permanecem integradas.

A suíte completa fechou com **2.665 testes Python e 39 testes Node** aprovados. O navegador exercitou o aplicativo e seus stores reais com calendário isolado: criação, edição, cancelamento de prévia, reagendamento, exclusão, semana, mês e celular. Na API instalada com Google real, foi criada e cancelada somente uma prévia: a comparação antes/depois conservou o calendário e os bancos de execução e lembretes. Não houve novo CRUD gravado no Google nesse ensaio. [Fluxo, contrato e limites da agenda](painel-web-agenda.md).

## Atualização da fase 5

Em 28/10/2026, Preparo passou a expor resumo, disponibilidade e briefing/checklist por POSTs protegidos pela sessão e CSRF. A geração é explícita; consultas periódicas não chamam o modelo. A tela diferencia resumo organizado com IA, resumo factual, cálculo de disponibilidade e preparo com dados fornecidos.

Notas e checklist marcado ficam somente na memória da página. Não há envio das notas ao LLM, pesquisa externa, reserva ou alteração da agenda por essas ferramentas. Resultados e limites deste marco são consolidados no [fechamento do preparo](painel-web-preparo.md) e no [índice de evidências](evidencias.md), separados dos ensaios históricos acima.

## Atualização da fase 6

Em 28/10/2026, Lembretes ganhou filtros por estado, contagens do conjunto consultado e histórico cronológico de cada tentativa. A prévia do card é uma composição local solicitada por clique, relida do Google, protegida pela sessão e marcada como não enviada. Não comprova a imagem histórica nem a entrega do aviso.

Passaram 221 testes Python focados no painel/cards e 84 testes Node. Desktop, celular e falhas foram conferidos com dados controlados; na API instalada, uma prévia com dados reais foi validada sem salvar seus bytes ou alterar o ledger de lembretes. Não houve mensagens, escritas Google ou chamadas de IA. [Uso, segurança e validação](painel-web-lembretes.md).

## Atualização da fase 7

Em 28/10/2026, o modo demonstração foi concluído com cenário identificado, atalhos para explorar, CRUD com confirmação, conflitos, preparo, avisos e restauração dos exemplos. Os dados ficam somente na aba. O resumo é ilustrativo/calculado a partir dos exemplos; nenhuma IA, integração ou envio real é executado.

A política de conteúdo bloqueia conexões e o servidor estático só serve os arquivos permitidos da demo. A sessão da agenda conectada continua separada. Passaram 227 testes Python focados e 105 testes Node; os fluxos foram conferidos no desktop e no celular. [Uso e evidências](painel-web-demonstracao.md).


## Atualização da fase 8

O fechamento consolidou **2.707 testes Python e 105 testes Node aprovados**, sem falhas, erros ou testes ignorados. A revisão de acessibilidade combinou leitura de código e uso por teclado: link para o conteúdo principal, foco no diálogo, fechamento com `Esc` e retorno ao controle de origem. Foram ajustados o contraste do selo da demonstração e os anúncios de erro/descarte para tecnologias assistivas. Isso não constitui uma auditoria de conformidade WCAG.

Capturas públicas e um [tour visual editado, sem narração](assets/painel-tour.mp4) apresentam somente dados fictícios. O [material de apresentação](apresentacao-painel.md) inclui roteiro e textos para portfólio. O vídeo histórico enviado ao recrutador não foi substituído. [Resultados, evidências e limites do fechamento](fechamento-painel-web.md).
