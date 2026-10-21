# Evidências de evolução

As capturas e saídas reais de terminal ficam em `data/evidencias/`, com índice em `data/evidencias/INDICE.md`. Essa pasta é local e ignorada pelo Git, pois os registros podem mostrar dados pessoais da conta de teste.

A cada marco visual relevante, salvar a captura numerada e registrar data, origem, etapa e resultado. Prints enviados durante o desenvolvimento também integram o acervo quando acrescentam uma evidência útil. Os rótulos distinguem preparação, confirmação pela API e resultado na interface.

## Registros

| Registro | Evidência | O que demonstra |
|---|---|---|
| 001 | Preparação da agenda `Genius Testes` | Nome e fuso preenchidos no formulário de criação |
| 002 | Projeto Google Cloud disponível | Projeto `Genius Agenda AI` criado e selecionado no painel |
| 003 | Calendar API ativada | Status `Ativado` na configuração da API |
| 004 | Configuração OAuth criada | Confirmação da configuração inicial; cliente OAuth ainda não configurado naquela tela |
| 005 | Permissões solicitadas | Tela de consentimento com os acessos solicitados; a imagem antecede a autorização |
| 006 | Primeira entrevista na agenda | Título, 22/10/2026, 15h–15h30, duração de 30 minutos, agenda `Genius Testes` e visibilidade particular |
| 007 | Saída JSON da consulta por dia | Comando Python retornou um evento real para 22/10/2026, com ID e horários da entrevista |
| 008 | Comparação com a visão diária do Google Agenda | Captura do navegador mostrando a mesma entrevista na data consultada |
| 009 | Saída JSON da consulta por semana | Um evento entre 19/10/2026, 00h e 26/10/2026, 00h exclusivo, no fuso America/Recife |
| 010 | Saída JSON da consulta por mês | Um evento entre 01/10/2026, 00h e 01/11/2026, 00h exclusivo, no fuso America/Recife |
| 011 | Comparação com a visão mensal do Google Agenda | Entrevista visível em outubro, com data, horário e agenda conferidos no detalhe |
| 012 | Identificação real pelo título | Busca por título sem acento encontrou um evento e confirmou seu ID em nova leitura |
| 013 | Identificação real pelo ID | O ID completo identificou a mesma entrevista de 22/10/2026, 15h–15h30 |
| 014 | Busca real sem correspondência | `not_found`, sem evento selecionado, com pergunta para conferir título e data |
| 015 | Ambiguidade em simulação local | Dois eventos fictícios produziram `needs_clarification`, candidatos e pergunta; nenhum foi selecionado |
| 016 | Prévia real do reagendamento | Horário original e solicitação de mudança apresentados sem gravação |
| 017 | Reagendamento real confirmado | Mesmo ID e título movidos de 22/10, 15h–15h30 para 23/10, 16h–16h30 |
| 018 | Consulta real posterior | Confirmação pelo ID e comparação das datas original e nova |
| 019 | Conferência visual do reagendamento | Google Agenda mostra a entrevista na nova data e horário |
| 020 | Prévia real da edição das informações | Dados atuais e três campos solicitados, sem gravação |
| 021 | Edição real confirmada | Novo título, descrição e local, preservando ID, data e horário |
| 022 | Conferência real posterior | Três campos relidos e identificação pelo novo título |
| 023 | Conferência visual da edição | Interface mostra os três campos e o horário preservado |
| 024 | Prévia real da exclusão | ID, título, data e horários apresentados com código de confirmação, sem exclusão |
| 025 | Exclusão real confirmada | DELETE da entrevista fictícia seguido de leitura confirmando ausência |
| 026 | Conferência real posterior à exclusão | Ausência pelo ID e na consulta do dia; nova prévia retorna `already_absent` |
| 027 | Conferência visual da exclusão | Visão diária de 23/10 com `Genius Testes` habilitada, sem a entrevista |

Os registros 001 a 005 foram enviados pelo usuário durante a configuração. Os registros 006, 008, 011, 019, 023 e 027 são capturas reais do navegador, preservadas sem edição. Os registros 007, 009, 010, 012 a 014, 016 a 018, 020 a 022 e 024 a 026 são resultados reais de operações com o Google, em JSON. O registro 015 é uma simulação local com eventos fictícios, identificada como tal. O acervo contém onze imagens, quinze registros de operações reais e um resultado simulado. As imagens e resultados antigos preservam o histórico anterior às alterações e à exclusão da fixture.

A visão mensal inclui outras agendas habilitadas, como feriados; as operações Python são restritas a `Genius Testes`.
