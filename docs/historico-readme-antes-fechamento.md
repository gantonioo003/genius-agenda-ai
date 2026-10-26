# Arquivo histórico do README

Snapshot anterior ao fechamento da etapa 16. Consulte o README atual para o estado vigente.

# Genius Agenda AI

Assistente pelo WhatsApp para organizar entrevistas de emprego no Google Calendar, com lembretes automáticos, resumos e preparo factual. Desenvolvido para o Desafio Genius Agenda AI.

**Entrega de 26/10, até 18h:** resumos/preparo e card compacto concluídos; próximos: painel reduzido opcional, qualidade e vídeo. O ensaio de CRUD pelo WhatsApp e o recebimento real de lembrete continuam pendentes; as etapas seguintes não estão declaradas concluídas. [Plano e estimativas](docs/plano-entrega-hoje.md).

**Etapa 16 em validação real:** criação pelo WhatsApp e card de lembrete agendado recebidos e conferidos no Google. Consulta, edição, reagendamento e exclusão do ensaio seguem em andamento. [Provas e pendências](docs/ensaio-real-whatsapp.md).

**Etapa 14 concluída no escopo compacto:** card com fundo de IA e dados por código, recebido no WhatsApp na demonstração. Regra automática de card aos 60 minutos e texto aos 15 minutos integrada e ativada, com alternativa textual e proteção contra repetição. **2394 testes Python aprovados** na suíte completa. Recebimento agendado real permanece para a qualidade. [Fechamento e limites](docs/fechamento-etapa-14.md).

**Etapa 13 concluída no escopo compacto:** resumo semanal/mensal, preparo factual com checklist, horários livres e sugestão conservadora de reagendamento no WhatsApp. **2.344 testes Python aprovados, 19 novos**, com 256 fontes conferidas. O usuário confirmou resumo e horários antes da simplificação visual; a nova sugestão ainda não tem recebimento humano comprovado. [Fechamento e limites](docs/fechamento-etapa-13.md).

Preparo e disponibilidade usam Python; IA organiza somente o resumo. A sugestão leve relê a entrevista, deriva sua duração e mantém a ocupação original na busca. Reagendamento no WhatsApp exige prévia e confirmação posterior. Este fechamento encerra A/B/C da etapa 13 compacta; qualidade, ensaio real do essencial e vídeo continuam nas etapas seguintes.

**Etapa 12 concluída no escopo de teste:** regras 60/15 minutos (24h opcional), persistência, sincronização, envio protegido e recuperação integrados ao ciclo periódico n8n. **1.956 testes Python e 981 verificações JavaScript aprovados** na parte 9; fontes e exports preservados na revisão final. README, documentação, 139 evidências e planner atualizados. Recebimento de lembrete real e template contextual fora da janela do sandbox permanecem pendentes. [Entregas, reprodução e limites](docs/fechamento-etapa-12.md). Próxima etapa: **13 — Resumos e preparo**.

**Etapa 11 concluída — agente de IA no WhatsApp de teste:** consultas, contexto, esclarecimentos, confirmações e executores de agenda integrados ao n8n. A versão final tem 1.561 testes Python e 29 verificações HTTP aprovados; a consulta real pelo WhatsApp teve recebimento confirmado pelo usuário na parte 10. As mutações conversacionais foram verificadas com agenda fictícia. Entregas, evidências e planner revisados; lembretes implementados na etapa 12. [Fechamento, resultados e limites](docs/fechamento-etapa-11.md).

**Etapa 10 concluída — WhatsApp de teste via 360dialog:** entrada HTTPS restrita, deduplicação e resposta fixa; imagem e template verificados e recebidos pelo usuário. Falhas, quatro exports e evidências revisados. O provisionamento Meta original continua pendente. O agente de IA foi integrado na etapa 11. [Entregas, reprodução e limites](docs/fechamento-etapa-10.md).

**Etapa 09 concluída — API e fluxos n8n:** rotas CRUD autenticadas, consulta normal publicada, respostas seguras e três exports sem segredos conferidos com a instância. O ensaio integrado aprovou 34 verificações HTTP e um disparo agendado natural; as escritas n8n usaram Google simulado. A preparação HTTP foi adicionada na etapa 11, parte 3; escritas n8n sobre Google real e WhatsApp com IA seguem na evolução conversacional. [Entregas, resultados e limites](docs/fechamento-etapa-09.md).

## Histórico do desenvolvimento

Registro do que foi realizado até esta versão, da definição do projeto à validação da base:

| Etapa | O que fizemos | Resultado |
|---|---|---|
| 1. Escopo | Escolhemos entrevistas de emprego como aplicação do desafio e definimos as responsabilidades de Python, n8n e Google Calendar. | CRUD e lembretes como entrega principal; briefings e imagens por IA como evolução. |
| 2. Repositório e ambiente | Criamos o repositório e a pasta local; conferimos Python, Git e Docker. | Base de trabalho preparada no Windows com Python 3.14.3. |
| 3. n8n local | Configuramos o n8n em Docker Compose, com versão fixada, porta local e volume persistente; acessamos a interface e criamos o usuário local. | Instância iniciada e verificação de saúde aprovada; o primeiro workflow foi acrescentado no marco 36. |
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
| 22. Revisão das regras existentes | Em 22/10/2026, conferimos serviços, conector, banco e testes; reproduzimos diferenças de validação e tratamento de erros em 10 verificações locais. | Parte 1 da etapa 08 concluída, sem alterar o comportamento: lacunas e prioridades registradas; diagnóstico local 028. Próxima parte: consolidar datas, fusos e entradas. |
| 23. Datas, duração e fusos | Em 22/10/2026, extraímos regras compartilhadas de intervalo UTC e fuso; aplicamos na criação/reagendamento e preservamos os períodos de consulta. | Parte 2 concluída: 155 testes aprovados, incluindo 16 novos; 15 exemplos locais registrados no 029. Entradas inválidas são rejeitadas antes da conexão. |
| 24. Ambiguidades e esclarecimentos | Em 22/10/2026, adicionamos serviço e CLI de preparação para as cinco ações, com perguntas por dados faltantes/correções e seleção somente de leitura. | Parte 3 concluída: 181 testes aprovados, incluindo 26 novos; nove exemplos locais no 030. Dúvidas não selecionam automaticamente nem executam ações. |
| 25. Conflitos de horário | Em 22/10/2026, implementamos consulta de ocupação, bloqueio antes de criar/reagendar e serialização entre processos que usam o mesmo SQLite. | Parte 4 concluída: 217 testes aprovados, incluindo 29 de conflitos e sete da trava. Na agenda real, criação e reagendamento conflitantes foram bloqueados; horário adjacente ficou livre. Registros 031 a 035. |
| 26. Prevenção de duplicações | Em 22/10/2026, adicionamos registro persistente de pedidos e operações, identidade canônica e recuperação pelo ID original. Migramos o SQLite do esquema 1 para 2, com backup e preservação de dois metadados da aplicação. | Parte 5 concluída: 264 testes aprovados. A entrevista A foi reutilizada duas vezes pela operação original e, em novo processo Python, por outra operação com o mesmo conteúdo; conteúdo alterado foi recusado. As duas entrevistas permaneceram inalteradas, sem escrita no Google. Registros 036 a 039. |
| 27. Falhas e recuperação | Em 22/10/2026, padronizamos erros das CLIs, endurecemos leituras e transporte e adicionamos confirmação por ID após resposta de escrita incerta. | Parte 6 concluída: 351 testes aprovados. As quatro operações reais com perda local simulada da resposta retornaram `recovered: true`, com uma escrita cada; fixture temporária removida e A/B preservadas. Registros 040 a 046. |
| 28. Testes do conjunto | Em 22/10/2026, adicionamos oito percursos integrados com serviços, SDK, transporte e SQLite reais, simulando somente a fronteira HTTP do Google. | Parte 7 concluída: 359 testes aprovados no staging e no repositório, sem falhas, erros ou testes ignorados. Nenhuma tentativa de rede ou alteração na conta Google; credenciais, configuração e banco locais preservados. Registro local 047. |
| 29. Fechamento da etapa 08 | Em 22/10/2026, revisamos as entregas, os resultados, os limites e a documentação, conferindo os hashes do código da suíte aprovada. | Etapa 08 concluída: 359 testes aprovados e 48 evidências indexadas; auditoria local 048. Próxima entrega: operações na API e fluxos n8n, na etapa 09. |
| 30. Contrato entre n8n e Python | Em 22/10/2026, definimos rotas v1, dados de entrada, respostas, códigos HTTP, identidade da criação e confirmação de exclusão; registramos exemplos fictícios. | Parte 1 da etapa 09 concluída: contrato e exemplos conferidos localmente; registro 049. Nenhuma rota de agenda ou autenticação foi implementada nesta parte. |
| 31. Proteção da API | Em 22/10/2026, implementamos chave Bearer, router v1 protegido, correlação segura e rota de status. Geramos a chave somente no `.env` privado. | Parte 2 concluída: 372 testes aprovados, incluindo 13 novos; servidor local respondeu 401 sem chave e 200 com chave válida. Registros 050/051, sem Google ou n8n. |
| 32. Consultas pela API | Em 22/10/2026, expusemos consultas por dia, semana, mês e ID no router autenticado, com filtros estritos, respostas mínimas e classificação segura de falhas. | Parte 3 concluída: 383 testes aprovados, incluindo 11 novos. HTTP real consultou os dois eventos de 23/10, semana e mês; confirmou um ID existente e a ausência de um ID excluído. Doze GETs no Calendar, sem mutações; registros 052/053. |
| 33. Criação pela API | Em 22/10/2026, adicionamos POST autenticado com JSON estrito, validações dos serviços existentes e classificação segura do resultado. | Parte 4 concluída: 402 testes aprovados, incluindo 19 novos. HTTP 201 criou uma entrevista de 23/10, 17h–17h30; repetições retornaram 200 com o mesmo ID e conflito retornou 409. Um POST real no Google, anteriores preservados; registros 054/055. |
| 34. Alterações pela API | Em 22/10/2026, expusemos edição parcial e reagendamento por ID, com JSON estrito, validações existentes, ETag e confirmação do retorno. | Parte 5 concluída: 424 testes aprovados, incluindo 22 novos. A entrevista da API foi movida para 23/10, 18h–18h30 e teve título/descrição/local atualizados. Dois PATCHs reais; repetições sem nova escrita, conflito bloqueado e demais eventos preservados. Registros 056/057. |
| 35. Exclusão pela API | Em 22/10/2026, adicionamos prévia e execução com código vinculado à agenda, ao ID e à versão, preservando confirmação, ETag e leitura posterior. | Parte 6 implementada: 444 testes aprovados, incluindo 20 novos. Prévia HTTP/Google real, bloqueio sem código/inválido e rejeição de código incorreto; zero DELETEs reais. A entrevista foi mantida. Exclusão e recuperação completas verificadas em simulação; registros 058/059. |
| 36. Primeiro fluxo n8n | Em 23/10/2026, publicamos webhook autenticado com validação, GET na API Python e resposta JSON, incluindo erros seguros. Export e comandos de configuração/teste adicionados. | Parte 7 concluída: 18 verificações HTTP reais e 80 cenários JavaScript locais aprovados. Dia/semana/mês consultaram o Google, três eventos preservados, API restaurada e canvas registrado; evidências 060 a 062. |
| 37. Respostas e falhas no n8n | Em 23/10/2026, acrescentamos apresentação reutilizável para dúvidas, conflitos, confirmação, sucessos e resultados incertos; conectamos todos os caminhos da consulta ao apresentador. | Parte 8 concluída: 215 testes do apresentador, 82 do fluxo e 19 verificações HTTP aprovadas. Perguntas e resultados de escrita usam dados fictícios; consultas e falhas também foram verificadas no fluxo real, com três eventos preservados. Registros 063/064. |
| 38. Testes de integração e agendamento | Em 23/10/2026, exercitamos n8n → API → serviços/SDK/SQLite com HTTP Google em memória e publicamos temporariamente um Schedule Trigger para consultar o status da API normal. | Parte 9 concluída: 34 verificações HTTP e 130 testes estáticos aprovados; 10 escritas somente simuladas, sem duplicação indevida. Disparo natural HTTP 200 confirmado, dois fluxos de ensaio despublicados e três eventos reais preservados. Registros 065 a 067. |
| 39. Fechamento da etapa 09 | Em 23/10/2026, exportamos/conferimos os três fluxos instalados, preservamos os arquivos testados sem segredos e consolidamos inventário, reprodução e limites. | Dez partes encerradas; manifesto dos exports, links e proteção do Git conferidos. Código e 67 evidências anteriores preservados; auditoria 068, sem repetir suítes ou operações de agenda. |
| 40. Cadastro inicial na Meta | Em 23/10/2026, o usuário criou sua conta, concluiu o cadastro de desenvolvedor e recebeu a confirmação de criação do portfólio `Genius Agenda AI`. | Parte 1 da etapa 10 em andamento: portfólio criado, sem verificação empresarial concluída. Aplicativo ainda no assistente; nenhum número ou token WhatsApp configurado. Captura original 069. |
| 41. Aplicativo Meta disponível | Em 23/10/2026, confirmamos no navegador interno o aplicativo criado, em desenvolvimento, e abrimos o caso de uso WhatsApp vinculado ao portfólio. | A tela oferece número de teste e exige aceite dos termos para continuar. Nenhum aceite, token, número ou mensagem foi configurado pelo agente; captura 070. |
| 42. Área de teste WhatsApp | Em 23/10/2026, após o aceite do usuário, acessamos Experimente e solicitamos o número de teste. | Duas tentativas retornaram erro ao criar a conta, com atualização e conferência de ausência entre elas. Ações necessárias sem pendências; causa não informada. Captura 071; nenhuma mensagem enviada. |
| 43. Comparação de navegadores e contato Meta | Em 23/10/2026, o usuário reproduziu o erro no Edge; conferimos as listas de contas WhatsApp/mensagens e o contato do perfil no Business Suite. | Listas exibidas vazias, e-mail com confirmação pendente e opção Receber código disponível. Causa do erro ainda desconhecida. Sem novas solicitações de número, código enviado, mudança de contato ou permissões; registros 072/073. |
| 44. Confirmação do contato e nova tentativa Meta | Em 23/10/2026, conferimos o e-mail confirmado e solicitamos o número de teste mais uma vez. | Mesmo erro genérico após a confirmação; nenhum número/token exibido. Evidências 074/075 preservadas e relato local preparado para suporte, sem envio. |
| 45. Acesso ao relato de bugs Meta | Conferimos os canais oficiais e a ferramenta de envio na sessão autenticada. | Ferramenta indisponível; a tela exige conta de desenvolvedor e função em app com mais de 24 horas. Isso não identifica a causa do erro do número. Registro 076; relato não enviado. |
| 46. Caminhos alternativos do WhatsApp | Conferimos status empresarial, outras opções do app, links do WhatsApp Manager e um sandbox de outro provedor. | Nenhum motivo explícito encontrado; Manager indisponível pelos links do painel. Sandbox 360dialog avaliado na documentação, ainda sem ativação, adaptação ou teste real. Registros 077/078. |
| 47. Credencial do sandbox alternativo | O usuário enviou START à 360dialog e apresentou a resposta de emissão da chave. | Campo privado D360_API_KEY preparado no .env para configuração local. Chave ainda não validada por API; webhook, adaptação e respostas automáticas pendentes. Registro textual 079, sem reproduzir a credencial. |
| 48. Recebimento real pelo sandbox | Em 23/10/2026, conectamos a 360dialog a uma entrada HTTPS restrita, publicamos um fluxo n8n autenticado e corrigimos a validação de remetentes com dois identificadores. | Mensagem real do usuário percorreu HTTPS → n8n → status da API com sucesso HTTP 200. Suíte com 478 testes Python e 470 verificações JavaScript aprovada; evidências 080/081. A confirmação HTTP ainda não envia resposta pelo WhatsApp nem opera a agenda. |
| 49. Segurança da entrada | Exigimos política privada de remetente, rejeição integral de lotes não autorizados e configuração com entrada oculta; revisamos os recursos documentados do sandbox. | 499 testes Python e 20 verificações HTTP sintéticas aprovados; n8n e credenciais anteriores preservados. Teste real após o filtro retornou 403 por diferença do identificador; diagnóstico de formato e nova confirmação pendentes. Registro 082. |
| 50. Remetente real confirmado | Recuperamos o túnel HTTPS, conferimos no teste real a representação do identificador e fixamos somente esse valor na política privada, mantendo os bloqueios. | Parte 6 concluída: nova mensagem real passou pelo n8n e status Python com HTTP 200. Cinco verificações HTTP após o ajuste e uma de recuperação somam-se às 20 anteriores; 499 testes Python preservados. Registro 083; deduplicação é a próxima parte. |
| 51. Deduplicação das mensagens | Registramos hashes de identidade em uma inbox SQLite separada antes do n8n, com reserva atômica e bloqueio de resultados incertos. | Parte 7 concluída: 551 testes Python e 22 cenários HTTP locais em 23 requisições. Nove identidades novas geraram nove encaminhamentos; repetições foram ignoradas, inclusive após reinício real. Dados fictícios, sem mensagem de saída ou operação Google; registros 084/085. |
| 52. Primeira resposta pelo WhatsApp | Em 24/10/2026, conectamos o fluxo à rota autenticada de resposta fixa, com consulta do claim e reserva persistente na outbox. | Parte 8 concluída: uma entrada sintética local gerou um envio real pela 360dialog, aceito e recebido pelo usuário. Nove verificações HTTP confirmaram proteção, status sem resposta e repetição sem segundo envio; 613 testes Python e 502 JavaScript aprovados. Registros 086/087. |
| 53. Imagens e templates do sandbox | Em 24/10/2026, adicionamos ensaios explícitos com prévia sem rede e registro separado, e revisamos formatos, janela e modelos disponíveis. | Parte 9 concluída: 664 testes Python aprovados; uma imagem JPEG pública e `disclaimer/en` aceitos em dois envios reais, sem segundo envio ao repetir as operações. Cards e templates personalizados não foram criados. Registros 088 a 090. |
| 54. Fechamento da etapa 10 | Em 24/10/2026, revisamos falhas, integridade das bases e reprodução; conferimos quatro exports instalados e as versões publicadas dos dois ativos. | Etapa 10 concluída na alternativa 360dialog: 15 verificações HTTP atuais sem novo envio/Google; resultados de 664 testes Python e 502 JS preservados por hashes. Manifesto final, 92 evidências e planner atualizados; registros 091/092. |
| 55. Definição do agente de IA | Em 24/10/2026, mapeamos oito ferramentas sobre os serviços/rotas existentes e definimos pedidos, esclarecimentos, autorização, privacidade e limites. | Parte 1 da etapa 11 concluída: contrato e 12 cenários fictícios revisados; preparação HTTP, contexto, texto WhatsApp e resposta dinâmica identificados como pendências. Sem LLM conectado, rede ou mudança no runtime; registro documental 093. |
| 56. Adaptador LLM e prévia pela API | Em 24/10/2026, implementamos protocolo de modelo, adaptador OpenAI Responses, configuração privada opcional e rota autenticada de prévia, sem dependências novas. | Parte 2 implementada e aguardando credencial para teste real: 718 testes Python aprovados, sem acesso a provedor, Google ou WhatsApp. Serviços de agenda e n8n preservados; registro 094. |
| 57. Consultas do agente e preparação HTTP | Em 24/10/2026, conectamos ferramentas de leitura por dia/semana/mês/ID e expusemos a preparação existente sem executor de escrita. | Parte 3 implementada: 777 testes Python aprovados e quatro consultas ASGI/Google reais com planos fixos locais; três eventos encontrados e ID confirmado. Sem inferência real, alteração de evento ou envio WhatsApp. Registros 095/096. |
| 58. Contexto mínimo por conversa | Em 24/10/2026, acrescentamos conversa explícita, resumo persistente das consultas, expiração de 30 minutos, isolamento e descarte da seleção ambígua. | Parte 4 implementada: 865 testes Python e 19 casos HTTP TCP com reinício real de processo; modelo e Google simulados. Sem histórico integral, consentimento de escrita ou WhatsApp dinâmico. Registros 097/098. |
| 59. Esclarecimentos na conversa | Em 24/10/2026, acrescentamos rascunho persistente, perguntas específicas, correções e seleção explícita com releitura do evento. | Parte 5 implementada: 976 testes Python e 22 casos HTTP TCP com reinício; modelo/Google simulados. Chave real lida, conexão bloqueada por falta de créditos; sem inferência ou escrita real. Registros 099 a 101. |
| 60. Confirmações vinculadas à proposta | Em 24/10/2026, implementamos prévia, aprovação explícita, prazo fixo, revogação e consumo interno único, com vínculo ao evento e à alteração exata. | Parte 6 implementada: 1.075 testes Python e 26 casos HTTP TCP com reinício. Alterações, expiração e repetição bloqueadas; mensagens fictícias e zero escrita real. Registros 102/103. |
| 61. Criação pelo agente | Em 25/10/2026, adicionamos o comando HTTP explícito sobre o rascunho preparado, com ledger separado, vínculo à agenda, consumo de proposta aprovada e reconciliação sem repetir escrita. | Parte 7 implementada no executor HTTP: 1.160 testes Python (85 novos) e 27 casos HTTP TCP com dois processos aprovados. Google/LLM como fixtures e zero envios WhatsApp; ligação dinâmica ainda pendente. Registros 104/105. |
| 62. Edição e reagendamento pelo agente | Em 25/10/2026, adicionamos comandos explícitos sobre o rascunho, com evento/agenda/versão vinculados, consumo de aprovação quando ativa, ledger separado e conferência completa do resultado. | Parte 8 implementada no executor HTTP: 1.257 testes Python (97 novos) e 33 casos HTTP TCP com dois processos aprovados. Google/LLM/consentimento como fixtures; sem escrita real ou envio WhatsApp. Registros 106/107. |
| 63. Exclusão com confirmação pelo agente | Em 25/10/2026, adicionamos o comando explícito que exige proposta aprovada para agenda/evento/versão exatos, reserva o consumo, usa DELETE condicional e verifica ausência. | Parte 9 implementada no executor HTTP: 1.367 testes Python (110 novos) e 62 casos HTTP TCP com dois processos aprovados. Provedores e aprovação como fixtures; sem exclusão real ou envio WhatsApp. Repetições somente consultam. Registros 108/109. |
| 64. Agente no WhatsApp e Gemini | Em 25/10/2026, substituímos a resposta fixa do fluxo ativo por fila durável, contexto e orquestração dos serviços; selecionamos Gemini após a indisponibilidade de créditos OpenAI. | Parte 10 implementada: 1.508 testes Python, 540 cenários JavaScript, 29 verificações HTTP locais e 42 com n8n publicado; dois testes reais fictícios Gemini HTTP 200. Uma consulta diária real foi respondida no aparelho, com confirmação do usuário; conversas completas e revisão final seguem nas partes 11/12. Registros 110 a 114. |
| 65. Conversas e falhas do agente | Em 25/10/2026, testamos contexto, conflitos, correções, cancelamento, aprovação, duplicação, reinício e resultados incertos; corrigimos nove caminhos. | Parte 11 concluída: 1.561 testes Python (53 novos), 29 verificações HTTP e sondagens Gemini reais com agenda fictícia. Falhas intermediárias preservadas; zero Google/WhatsApp reais neste marco. Registros 115 a 118. Revisão final registrada no marco seguinte. |
| 66. Fechamento da etapa 11 | Em 25/10/2026, revisamos as doze partes, os resultados, os limites, a documentação, as evidências e o planner. | Etapa 11 concluída no escopo de teste: resultados finais de 1.561 testes Python e 29 verificações HTTP preservados, sem nova suíte ou chamada de provedor no fechamento. Consulta WhatsApp humana da parte 10 e sondagens Gemini com agenda fictícia distinguidas. Próxima etapa: lembretes. |
| 67. Regras dos lembretes | Em 25/10/2026, conferimos o enunciado original e definimos padrão 60/15 min, 24h opcional, configuração, UTC/fuso da agenda, tolerância e identidade dos avisos. | Parte 1 da etapa 12 concluída no escopo documental; sem scheduler, envio ou mudança de runtime. Exemplos e regras conferidos; registros 121/122. Próxima parte: conferir o canal. |
| 68. Canal dos lembretes | Revisão do sandbox e da janela de 24h, formato mínimo de texto e lacunas de timestamp/gate; exemplos de decisão e formato conferidos. | Parte 2 da etapa 12 concluída no escopo documental; fora da janela falta template contextual. Sem novo envio ou implementação. Registro 123; próxima parte: persistência. |
| 69. Persistência dos lembretes | SQLite próprio com perfil/revisão, identidade estável, histórico, reservas por tentativa, estados protegidos e observações de canal imutáveis. | 1.599 testes Python, 38 novos; retomada entre processos sem liberar envio iniciado. Banco real inicializado vazio. Sem scheduler, nova consulta Google ou envio. Registros 124/125. |
| 70. Geração dos lembretes | Consulta limitada de oito dias, ID/fuso reais da agenda, ocorrências expandidas e lote SQLite com CAS; repetição mantém identidades. | 1.663 testes Python, 64 novos; consulta real e reexecução documentadas no registro 127. Sem escrita de eventos, LLM, WhatsApp ou scheduler. Registros 126/127. |
| 71. Sincronização dos lembretes | Conferência por ID, reagendamento/cancelamento em lote, invalidação segura de reserva e CAS do escopo/histórico completos. | 1.743 testes Python, 80 novos; estado atual do banco conferido sem criar casos fictícios. Sem envio ou escrita no Google. Registros 128/129. |
| 72. Verificação periódica dos lembretes | Rota autenticada, sincronização antes da geração, trava de SO e fluxo n8n a cada minuto; setup específico reutiliza a credencial existente. | 1.778 testes Python, 35 novos; 719 JavaScript, 179 novos. Preflight real concluído com contagens zero e disparo natural confirmado. Quatro fluxos anteriores preservados, sem envio WhatsApp. Registros 130/131. |
| 73. Envio sem duplicar | Dispatch autenticado após o tick, reserva por identidade, duas leituras exatas do evento, gate final transacional e observações de entrada imutáveis. | 1.874 testes Python (+96) e 853 JavaScript (+134); aceite único e incerteza após reinício em fixtures. Rotas reais concluídas sem avisos devidos e fluxo de seis nós publicado, com disparo natural. Registros 132/133. |
| 74. Recuperação segura de falhas | Reserva anterior ao transporte volta à validação após lock exclusivo e CAS completo; envio iniciado vira incerto, sem liberar repetição. Instalador confere papéis, conexões, código e flags do fluxo. | 1.936 testes Python (+62) e 962 JavaScript (+109), incluindo crash real e 44 registros protegidos preservados. API e fluxo de oito nós conferidos no runtime vazio; registros 134/135. |
| 75. Testes do conjunto de lembretes | 14 cenários com SDK/SQLite, seis por TCP com processos e 19 do grafo; 30 envelopes TCP também passaram pelos validadores exportados. | 1.956 testes Python (+20) e 981 JavaScript (+19), sem skips. Onze processos, cinco reinícios e zero chamadas externas nos ensaios. Runtime e cinco exports conferidos por leitura; registros 136/137. |
| 76. Fechamento da etapa 12 | Revisão das dez partes, fontes/logs aprovados, README, guias, exports, evidências e planner. | Etapa encerrada em 26/10/2026 no escopo de teste; 1.956 Python/981 JavaScript preservados por hash. Acervo de 139 registros; auditoria 138 e captura do PDF 139. Próxima: resumos e preparo (13). |
| 77. Definição de resumos e preparo | Em 26/10/2026, conferimos o desafio e definimos cinco capacidades de leitura, dados, disponibilidade, uso da IA e isolamento de confirmação. | Parte 1/10 da etapa 13 definida; contrato e 24 critérios fictícios conferidos documentalmente. Código, provas anteriores e runtime preservados; registro 140. Próxima: compor a consulta da semana/mês. |
| 78. Coleta para resumos | Contexto factual de semana/mês, fuso autorizado, paginação limitada, contagens integrais e projeção mínima; API Bearer e CLI. | 2.044 testes Python (+88) e duas leituras CLI Google reais; sem IA, escrita de eventos ou nova intenção WhatsApp. Registros 141/142; próxima: resumos com IA. |
| 79. Resumos com IA | Plano editorial validado escolhe layout e até três destaques; texto factual pelo Python, sem títulos enviados ao modelo; fallback determinístico. | 2.126 Python (+82), revisão independente e Gemini real conferidos; contexto/coletor/adaptadores/exports preservados. Sem novas intenções WhatsApp ou escrita de evento; registros 143/144. |
| 80. Horários livres | Duração/faixa explícitas, grade local de 15 minutos, ocupação completa e margem; três alternativas por API/CLI, sem reserva. | 2.225 Python (+99), revisão independente e duas leituras Google reais com seis revalidações de conflito. Sem IA, escrita ou novas intenções WhatsApp; registros 145/146. |
| 81. Preparo compacto | Briefing factual e checklist genérico na mesma resposta, com seleção exata, releitura, contexto informado e limites. | 2.269 Python (+44), serviço/CLI instalados conferidos no Google; registro 147. Sem nova chamada de IA ou escrita. |
| 82. Resumos e preparo no WhatsApp | Parser limitado conecta resumo, briefing/checklist e disponibilidade; revogação CAS protege confirmações anteriores. Apresentação simplificada para leitura no celular. | 2.325 Python (+56), três chamadas API reais 200 e duas solicitações WhatsApp confirmadas pelo usuário antes da nova apresentação. Registros 148/149; formato final ainda sem nova confirmação humana. |
| 83. Fechamento compacto da etapa 13 | Sugestão conservadora relê a ocorrência e deriva a duração; reagendamento WhatsApp exige prévia e confirmação. Limites, README, guias e planner consolidados. | 2.344 Python (+19), 256 fontes e leitura/prévia reais com snapshot preservado, sem escrita, LLM ou mensagem. Registros 150/151; sem nova confirmação humana da sugestão. |

A validação real incluiu descoberta da agenda, criação, consultas, identificação, reagendamento, edição, exclusão, bloqueio de conflitos e reutilização de pedidos, com conferências de API e interface nos respectivos marcos. Ambiguidades e falhas concorrentes foram verificadas em testes locais. O [fechamento da etapa 07](docs/fechamento-etapa-07.md) registra as entregas e os limites. O banco da agenda contém metadados e registros de criação; a inbox separada mantém identidades de mensagens/status. Os lembretes têm [persistência própria](docs/persistencia-lembretes.md) e [geração manual](docs/geracao-lembretes.md); a [verificação periódica](docs/lembretes-n8n.md) já sincroniza e gera avisos a cada minuto no n8n. O [envio](docs/envio-lembretes.md) agora aplica reserva, revalidação e janela do canal antes do transporte; a [recuperação seletiva](docs/recuperacao-lembretes.md) já está disponível, e os [testes do conjunto](docs/testes-conjunto-lembretes.md) foram concluídos. O [fechamento da etapa 12](docs/fechamento-etapa-12.md) reúne os resultados e as pendências de validação real.

## Arquitetura planejada

```text
WhatsApp → n8n → API Python + IA → Google Calendar
                        ↕
                      SQLite
```

O n8n coordenará os fluxos; Python concentrará interpretação, validações e operações. O SQLite já mantém metadados, identidades de criação, inbox de mensagens/status, outbox de respostas e contexto, rascunhos e propostas de confirmação em banco separado; os lembretes já possuem configuração, histórico e planejamento local acionado a cada minuto pelo n8n, com transporte condicionado à reserva e às validações do evento e do canal. O Google Calendar será a referência dos compromissos. Será utilizada uma IA pronta por API.

Atualmente, o n8n consulta o Google Calendar por meio da API Python, usando um webhook de teste autenticado. O grupo v1 exige chave de acesso e disponibiliza status, consultas, criação, edição, reagendamento, exclusão com confirmação e ativação do agente WhatsApp; a resposta fixa permanece como rota legada. Os comandos Python continuam disponíveis. Os serviços das operações de agenda ficam em `app/services/`, sem dependência do modelo de IA; o conector Google é injetado nesses serviços. O Google Cloud foi usado para cadastrar e autorizar o aplicativo; a aplicação roda localmente.

O [contrato HTTP v1](docs/contrato-api-v1.md) define os pedidos e as respostas entre os componentes. Os [exemplos de comunicação](examples/api/v1/communication-examples.json) são fictícios e planejados. `request_id` identifica a tentativa HTTP; `operation_id` preserva a identidade da criação entre tentativas. A preparação e a prévia não alteram eventos; apenas o executor solicita a alteração.

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

Para usar as rotas protegidas, configurar uma chave privada em `API_AUTH_TOKEN`. A [configuração da proteção](docs/seguranca-api.md) mostra como gerá-la sem exibi-la no terminal. Sem a chave configurada, o acesso v1 fica indisponível; o health público continua funcionando. Reiniciar a API após alterar sua configuração.

- API: <http://localhost:8000/health>
- Documentação da API: <http://localhost:8000/docs>

Com o Docker Desktop em execução, abra outro terminal na raiz do projeto:

```powershell
docker compose up -d
```

n8n: <http://localhost:5678>. O endpoint `/health` verifica apenas a API; não comprova as integrações.

### Primeiro fluxo n8n — etapa 09, parte 7 concluída

O webhook recebe uma consulta estruturada, chama `GET /api/v1/events` e devolve o envelope e o status da API. Consulta dia, semana e mês; não executa escritas. Com a API e o Docker ligados, configure uma vez e depois teste:

```powershell
.\.venv\Scripts\python.exe scripts/setup_n8n.py --apply
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23
```

O setup importa/publica o fluxo e reinicia somente o n8n local; a chave permanece no gerenciador privado. O [guia do primeiro fluxo](docs/n8n-primeiro-fluxo.md) explica pré-requisitos, filtros, respostas, privacidade e reprodução. O export em `n8n/workflows/consulta-eventos.json` não contém chaves nem eventos. A entrada WhatsApp foi ligada separadamente ao agente na parte 10 da etapa 11; [configuração e limites](docs/whatsapp-agente.md).

### Respostas e falhas — etapa 09, parte 8 concluída

O webhook acrescenta `presentation` ao envelope técnico, com mensagem clara e próximo passo. Dúvidas aguardam dados; conflitos pedem outro horário; resultado de escrita incerto pede conferir a agenda antes de reenviar. O apresentador não executa ações, e o fluxo permanece GET, sem retentativas automáticas.

```powershell
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23 --text-only
node n8n/demo-responses.cjs
```

O primeiro comando consulta a agenda real; o segundo mostra seis respostas fictícias, sem rede ou escrita. Consulte os [estados, testes e limites do tratamento](docs/n8n-respostas-falhas.md). As perguntas de preparação e os conflitos ainda não são produzidos por executores n8n conectados.

### Integração e agendamento — etapa 09, parte 9 concluída

O ensaio atravessa n8n, HTTP, API e SDK reais, simulando somente o HTTP Google para o CRUD. Confere identidade, repetições, conflitos, confirmação e falhas; um Schedule Trigger real consulta o status da API e é despublicado após a prova. Para repetir, com API e Docker ligados:

```powershell
.\.venv\Scripts\python.exe scripts/test_n8n_integration.py --apply
```

O comando usa porta/banco/credenciais isolados, reinicia brevemente o n8n e limpa seus fluxos ao final. Consulte [escopo, pré-requisitos e resultados](docs/n8n-testes-integracao.md). O disparo técnico ainda não é o sistema de lembretes.

### WhatsApp de teste — recebimento real validado

O recebimento inicial `360dialog → HTTPS → entrada Python → n8n → status da API` foi confirmado com uma mensagem real do usuário. O fluxo continua recebendo somente contagens, tipos e um UUID técnico. Na parte 8, sua chamada mudou para a rota de resposta fixa: Python verifica a inbox, reserva a saída e envia pela 360dialog ao destinatário privado autorizado.

A entrada exige uma política privada com o identificador exato do remetente; outro telefone, somente BSUID ou um lote misto recebem 403 antes do n8n. O identificador autorizado foi confirmado em nova mensagem real, com HTTP 200 após o ajuste explícito da política. O [guia do sandbox](docs/whatsapp-sandbox.md) reúne instalação, inicialização, registro do endereço HTTPS, privacidade, testes e retomada após desligar o computador. O túnel é temporário e requer novo registro quando seu endereço mudar. A etapa 10 está concluída no percurso de teste pela 360dialog, conforme o registro de fechamento.

A [deduplicação persistente](docs/whatsapp-deduplicacao.md) reserva cada identidade antes de encaminhar. Repetições concluídas recebem 200 sem nova execução; resultados pendentes/incertos ficam bloqueados para reconciliação, sem reenvio automático. A inbox guarda hashes e metadados técnicos, sem copiar telefone, texto ou ID original.

Em 24/10/2026, uma entrada sintética em loopback percorreu n8n/API e gerou uma resposta real, cujo recebimento o usuário confirmou no WhatsApp. Os testes do mesmo ID e do mesmo ID com texto alterado não geraram outro envio; somente status não envia resposta. O [guia de respostas](docs/whatsapp-respostas.md) separa o aceite técnico dessa confirmação do usuário. A integração com o agente foi acrescentada depois, na parte 10 da etapa 11, e as conversas controladas foram testadas na parte 11.

A [parte 9 de imagens e templates](docs/whatsapp-midias-templates.md) validou dois envios explícitos: imagem pública de teste e `disclaimer/en`. A CLI usa um registro separado e não modifica o fluxo de resposta fixa. O sandbox limita mídia por ID e criação de templates; um lembrete personalizado fora da janela exige template próprio aprovado. O [fechamento da etapa 10](docs/fechamento-etapa-10.md) consolida reprodução, falhas, exports e os limites do percurso de teste.

### Consultas HTTP — etapa 09, parte 3 concluída

Depois de iniciar a API, usar `/docs` → **Authorize** com a chave privada. Consultas disponíveis:

- `GET /api/v1/events?date=2026-10-23&period=day`
- `GET /api/v1/events?date=2026-10-23&period=week`
- `GET /api/v1/events?date=2026-10-23&period=month`
- `GET /api/v1/events/{event_id}`

O fuso padrão é `America/Recife`; o ID deve ser completo e codificado na URL. Dados inválidos são recusados antes de abrir o Google. Lista vazia, evento ausente e falha de leitura têm respostas distintas. Consulte o [guia de consultas, respostas e verificação](docs/consultas-api.md).

### Criação pelo agente — etapa 11, parte 7 implementada

O diálogo prepara um pedido e esclarece os dados; `ready` continua sem executar escrita. O comando autenticado `POST /api/v1/agent/create` aceita somente `conversation_id` e usa os parâmetros e o `operation_id` produzidos pelo servidor. Uma proposta ativa exige aprovação correspondente e consumo único; pedidos completos sem proposta podem seguir o comando explícito sem confirmação genérica adicional.

O ledger privado `data/agent-creations.sqlite3` (schema 1) registra a tentativa antes de escrever. Repetir estados `running`, `unknown` ou `confirmed` somente consulta o Google; resultado incerto não expira nem libera outro POST. Conflitos, agenda vinculada e idempotência global continuam nos serviços existentes. O [guia de criação](docs/criacao-agente.md) apresenta contrato, recuperação e limites. O contexto permanece no schema 3. O [orquestrador WhatsApp](docs/whatsapp-agente.md), acrescentado na parte 10, reutiliza esse executor após conferir a autorização do pedido.

### Editar e reagendar pelo agente — etapa 11, parte 8 implementada

`POST /api/v1/agent/edit` e `/agent/reschedule` recebem somente `conversation_id`. Usam o evento e os parâmetros completos do rascunho; proposta ativa exige aprovação exata e consumo único. O servidor vincula a agenda e a versão, envia `If-Match`, conserva campos não solicitados e confere o resultado por leitura. O diálogo continua preparatório.

O ledger privado `data/agent-updates.sqlite3` (schema 1) registra a operação antes do PATCH, sem TTL. Repetir `running`, `unknown` ou `confirmed` somente consulta o estado; divergência posterior não provoca reaplicação. [Contrato, recuperação e evidências](docs/alteracoes-agente.md). A parte 10 ligou o [orquestrador WhatsApp/n8n](docs/whatsapp-agente.md) a esse executor, mantendo as validações.

### Criação HTTP — etapa 09, parte 4 concluída

`POST /api/v1/events` recebe `operation_id` e `event` com título, início/fim e fuso; descrição é opcional. Reutiliza as regras de horários, conflitos e duplicações. Retorna **201** em criação nova confirmada, **200** em reutilização e **409** em conflito. Falha sem resultado comprovado retorna **502/unknown**, sem repetir a gravação automaticamente.

A validação real criou uma entrevista fictícia em `Genius Testes` para **23/10/2026, 17h–17h30, America/Recife**, sem convidados. Repetir o pedido, inclusive com outra operação e conteúdo igual, reutilizou o mesmo ID. Os dois eventos anteriores permaneceram iguais. O registro persistente foi preservado com backup. A mesma entrevista foi depois editada e reagendada na parte 5, documentada abaixo. Consulte o [payload, respostas e limites da criação HTTP](docs/criacao-api.md).

### Alterações HTTP — etapa 09, parte 5 concluída

`PATCH /api/v1/events/{event_id}` edita título, descrição e local; `PATCH /api/v1/events/{event_id}/schedule` altera data e horários. As duas rotas usam o ID completo, exigem Bearer e validam os dados antes do conector. Campos omitidos ou `null` são preservados; `""` limpa descrição/local. O reagendamento mantém a verificação de conflitos.

A validação real moveu a entrevista fictícia da API para **23/10/2026, 18h–18h30, America/Recife**, e alterou suas três informações. Foram dois PATCHs, com `If-Match` e confirmação por leitura. Repetições retornaram `unchanged` sem outra gravação; horário ocupado retornou 409. Os outros dois eventos e o registro de criação permaneceram iguais. Consulte os [payloads, respostas e limites das alterações](docs/alteracoes-api.md).

### Exclusão pelo agente — etapa 11, parte 9 implementada

`POST /api/v1/agent/delete` recebe somente `conversation_id`. Exige rascunho completo e aprovação específica da proposta de exclusão, sempre vinculada à agenda, ao ID, à versão e aos dados apresentados. O servidor consome essa aprovação uma única vez, deriva o token técnico internamente e verifica a ausência após o DELETE com `If-Match`. O diálogo continua preparatório.

O ledger privado `data/agent-deletions.sqlite3` (schema 1) vincula a execução à proposta, sem TTL. Repetir `running`, `unknown` ou `confirmed` somente lê o estado, sem outro DELETE; ausência observada não prova qual cliente removeu o evento. [Contrato, recuperação e evidências](docs/exclusao-agente.md). A parte 10 ligou o [orquestrador WhatsApp/n8n](docs/whatsapp-agente.md) a esse executor, mantendo as validações.

### Exclusão HTTP — etapa 09, parte 6 implementada

`POST /api/v1/events/{event_id}/deletion-preview` consulta o evento e retorna a pergunta e o código da versão. Após apresentar esses dados e receber confirmação explícita, o cliente chama `POST /api/v1/events/{event_id}/delete` com o código exato. Sem código válido, a chamada é recusada; uma mudança de versão exige nova prévia e confirmação. O código não comprova sozinho o consentimento humano.

A prévia e os bloqueios foram validados no Google real, sem enviar DELETE. A entrevista fictícia de **23/10, 18h–18h30** foi preservada. Os testes locais verificaram a exclusão completa, a confirmação de ausência, concorrência e recuperação com HTTP Google simulado. Consulte o [guia da exclusão em duas etapas](docs/exclusao-api.md).

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

`--execute` pode gravar na agenda: consulta o ID persistido e reutiliza um evento correspondente. Só tenta criar quando o registro está `pending` e o ID está ausente; um pedido já confirmado não recria um evento removido. Consulte a [implementação da primeira entrevista](docs/primeira-entrevista.md) e as [regras atuais de repetição](docs/duplicacoes.md).

A entrevista foi posteriormente reagendada, editada e excluída na demonstração do CRUD. Esse comando registra a criação original; não o use para consultar o estado atual ou recriar automaticamente o evento removido. Uma nova demonstração deve usar uma nova fixture com ID próprio.

### Consulta por dia — etapa 07, parte 1 concluída

```powershell
.\.venv\Scripts\python.exe -m app.list_events --date 2026-10-23 --timezone America/Recife
```

Consulta somente de leitura, sem precisar iniciar API ou n8n. A validação inicial encontrou a entrevista em 22/10, 15h–15h30; após o reagendamento, a consulta de 23/10 retornou o mesmo ID, das 16h às 16h30. Depois da exclusão, essa consulta retornou zero eventos. Em 22/10, a demonstração de conflitos adicionou duas novas fixtures para 23/10: A, 15h–15h30, e B, 16h–16h30. A entrevista original continua removida. Os resultados incluem ID, título, início/fim e status. Consulte o [guia da consulta por dia](docs/consulta-por-dia.md).

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

### Regras de datas e fusos — etapa 08, parte 2 concluída

A criação e o reagendamento agora exigem o mesmo padrão: início/fim com offset UTC, fuso válido, hora local coerente e duração positiva medida em UTC. Tipos inválidos e conversões fora do intervalo suportado produzem erro de validação antes de acessar o Google. As consultas mantêm os seus períodos civis e aceitam limites absolutos em outro offset. Consulte as [regras, exemplos e verificação](docs/datas-fusos.md).

### Esclarecimentos — etapa 08, parte 3 concluída

O comando `app.prepare_request --request-file CAMINHO_JSON` prepara pedidos estruturados de criar, consultar, reagendar, editar e excluir. Retorna `needs_clarification`, `needs_correction` ou `ready`. Quando há dúvida, devolve pergunta e candidatos, sem parâmetros de execução. `ready` indica preparação dos dados; a ação e a confirmação de exclusão continuam separadas.

Os exemplos em `examples/requests/` permitem conferir pedidos incompletos e prontos sem acessar o Google. A seleção de um evento completo pode consultar a agenda configurada, sempre sem escrita. A interpretação de texto livre e a continuação pelo WhatsApp entram nas etapas de integração e IA. Consulte o [contrato e os comandos de esclarecimento](docs/esclarecimentos.md).

### Conflitos de horário — etapa 08, parte 4 concluída

```powershell
.\.venv\Scripts\python.exe -m app.check_conflicts --start "2026-10-23T15:15:00-03:00" --end "2026-10-23T15:45:00-03:00" --timezone America/Recife
```

O comando consulta o intervalo e retorna `available` ou `conflict`, sem alterar eventos. Criação e reagendamento verificam a ocupação antes de gravar; no reagendamento, somente o próprio ID é ignorado. Eventos cancelados ou marcados como livres não bloqueiam; horários adjacentes são permitidos. Uma consulta livre não reserva o horário.

Na demonstração real de 22/10, criamos duas novas entrevistas fictícias para 23/10, sem convidados. A criação de C às 15h15 e a tentativa de mover B para esse horário foram bloqueadas por A; C permaneceu ausente e B manteve 16h–16h30. O intervalo adjacente de 15h30 a 16h ficou livre. Resultados e interface foram registrados nas evidências 031 a 035.

A trava SQLite serializa a checagem e a escrita entre processos que usam o mesmo banco local. Alterações externas no Google ou instâncias com outro banco ainda podem ocorrer nesse intervalo; não há transação atômica com o Google Calendar. Consulte as [regras, comandos e limites dos conflitos](docs/conflitos.md).

### Prevenção de duplicações — etapa 08, parte 5 concluída

A criação persiste a identidade do pedido antes de enviar a gravação. Repetir a operação, ou usar outra operação com o mesmo título, descrição e instantes canônicos na mesma agenda, reutiliza o ID original após conferir o evento. Reaproveitar uma operação com conteúdo diferente é recusado. Eventos com conteúdo divergente ou removidos não são sobrescritos nem recriados pela repetição de um pedido confirmado.

Na validação real de 22/10, adotamos no registro a entrevista A de 23/10, 15h–15h30, conservando seu ID e impressão originais. Duas repetições da operação retornaram `reused: true`; um novo processo Python com outra operação e o mesmo conteúdo também retornou esse ID. O pedido alterado foi recusado. Leituras anteriores e posteriores confirmaram as mesmas duas entrevistas; uma guarda impediu chamadas de escrita ao Google nessa demonstração. Registros 036 a 039.

O esquema 2 acrescenta `creation_requests` e `creation_operations`, com migração transacional dos metadados existentes. A proteção depende do histórico no mesmo SQLite; outros bancos não compartilham esse registro. A entrada WhatsApp também possui [deduplicação própria por ID](docs/whatsapp-deduplicacao.md), em banco separado. Consulte o [registro, a identidade e os limites da deduplicação](docs/duplicacoes.md).

### Falhas e recuperação — etapa 08, parte 6 concluída

Quando a resposta de uma escrita é perdida ou inválida, o conector consulta o mesmo ID e confirma os dados antes de retornar `recovered: true`. Se a leitura também falhar ou divergir, mantém um erro seguro; POST/PATCH/DELETE não são repetidos automaticamente. Autenticação, rede e SQLite têm mensagens controladas nas CLIs.

Na agenda real de teste, criamos, reagendamos, editamos e excluímos uma fixture temporária, injetando localmente a perda da resposta **depois de cada sucesso HTTP do Google**. As quatro recuperações foram confirmadas, com uma escrita por operação. A agenda terminou com os mesmos dois eventos anteriores. A captura 044 registra o teste antes da remoção; os JSONs demonstram a recuperação. Não houve indisponibilidade real do Google. Consulte [comportamento, verificação e limites](docs/falhas-recuperacao.md).

### Testes do conjunto — etapa 08, parte 7 concluída

Oito percursos integrados verificam CRUD, consultas, conflitos, repetições, ambiguidades, versões concorrentes e recuperação de falhas. Conferem o estado final, a quantidade de escritas e a persistência após reabrir o banco e o cliente. O SDK e o transporte executam seu código real; a sessão HTTP usa um provedor em memória, sem rede ou credenciais reais. Consulte o [escopo, os comandos e os resultados](docs/testes-conjunto.md).

## Validação realizada

- **2.344 testes Python atuais, 19 novos no fechamento compacto da etapa 13:** 12 do serviço de sugestão conservadora e sete do parser/fluxo; 256 fontes conferidas e instaladas. [Fechamento, provas e limites](docs/fechamento-etapa-13.md). Os 981 JavaScript são históricos e os cinco exports foram preservados.
- **2.325 testes Python históricos, 56 novos na integração e apresentação WhatsApp:** parser/apresentação, CAS, factories e helpers; 253 fontes conferidas. Três chamadas API reais e dois recebimentos WhatsApp anteriores à simplificação visual são provas separadas. [Resultados e limites](docs/preparo-whatsapp.md).
- **2.269 testes Python históricos, 44 novos no preparo compacto:** identidade/releitura, contexto, limites, API/CLI e SDK com Google fictício. Serviço e CLI instalados conferidos por leitura real; [provas e limites](docs/preparo-entrevista.md).
- **2.225 testes Python históricos, 99 novos na etapa 13, parte 4:** disponibilidade, calendário, API/CLI e composição com SDK sobre HTTP fictício. Duas leituras CLI Google reais e seis revalidações pelo verificador legado, sem reservas; [provas e limites](docs/horarios-livres.md). As 981 verificações JavaScript são históricas e não foram repetidas nesta parte.

- **2.126 testes Python na etapa 13, parte 3, incluindo 82 novos:** plano/renderizador, serviço, API/CLI e integração com adaptadores/transporte fictício. Gemini real validado separadamente; [resultados e limites](docs/resumos-com-ia.md). As 981 verificações JavaScript permanecem históricas, sem nova execução nesta parte.

- **2.044 testes Python na etapa 13, parte 2, incluindo 88 novos:** coleta de calendário, contexto, API/CLI e composição com SDK/transporte sobre HTTP fictício. Duas consultas CLI Google reais separadas; [resultados e limites](docs/coleta-contexto-agenda.md). Os totais dos marcos anteriores abaixo são históricos. As 981 verificações JavaScript foram preservadas sem nova execução nesta parte.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m app.database --check
```

- **Conversas e falhas — 25/10/2026:** 1.561 testes Python, 53 novos, e 29 verificações HTTP com fontes finais; contexto, conflitos, duplicação e resultados incertos conferidos. As sondagens Gemini usaram agenda fictícia; nenhum Google/WhatsApp real neste marco. [Resultado e limites](docs/testes-conversas-agente.md).
- **Agente no WhatsApp — 25/10/2026:** 1.508 testes Python, 540 cenários JavaScript, 29 verificações HTTP locais e 42 com n8n publicado aprovados; provedores simulados nos ensaios de mutação. Dois pedidos Gemini fictícios HTTP 200 e uma consulta diária real pelo WhatsApp, recebida pelo usuário; zero mutações reais neste marco. [Fluxo e limites](docs/whatsapp-agente.md).
- **Excluir pelo agente — 25/10/2026:** 1.367 testes Python aprovados, sem falhas, erros ou ignorados; 110 novos (27 de persistência, 39 de serviço, 20 de gateway e 24 de API). Mais 62 casos HTTP TCP com dois processos e SQLite reais. Aprovação obrigatória/exata, consumo único, versão, ausência, reinício e recuperação sem outro DELETE conferidos. Modelo/gateway/consentimento como fixtures; zero chamadas reais Google/LLM ou envios WhatsApp. [Detalhes](docs/exclusao-agente.md).
- **Editar e reagendar pelo agente — 25/10/2026:** 1.257 testes Python aprovados, sem falhas, erros ou ignorados; 97 novos (26 de persistência, 33 de serviço, 20 de gateway e 18 de API). Mais 33 casos HTTP TCP com dois processos e SQLite reais. Modelo, gateway e consentimento fictícios; agenda/versão, conflitos, campos preservados, reinício e recuperação por leitura conferidos. Zero chamadas reais Google/LLM ou envios WhatsApp neste marco. [Detalhes](docs/alteracoes-agente.md).
- **Criação pelo agente — 25/10/2026:** 1.160 testes Python aprovados, sem falhas, erros ou ignorados; 85 novos (24 de store, 27 de serviço, 16 de gateway e 18 de API). Mais 27 casos HTTP TCP em loopback com dois processos, SQLite real e modelo/gateway como fixtures. Preparação sem escrita, comando explícito, conflito, persistência, confirmação e recuperação conferidos; zero chamadas reais Google/LLM ou envios WhatsApp. [Escopo e reprodução](docs/criacao-agente.md).
- **Fechamento WhatsApp:** 15 verificações HTTP aprovadas naquele fechamento sem novas mensagens ou chamadas Google; quatro exports instalados e duas versões publicadas conferidos. Código, bancos e evidências anteriores preservados.
- **Etapa 10 — 664 testes Python locais:** os 613 anteriores mais 20 de mídia no transporte, 18 do ledger e 13 do serviço/CLI de ensaios. Autorização, concorrência, binding, persistência e falhas aprovadas, sem erros ou testes ignorados; provedores substituídos nesses testes.
- **Deduplicação HTTP local:** 22 cenários em 23 requisições, com IDs/dados fictícios e n8n/API reais. Nove identidades novas geraram nove encaminhamentos; repetições não aumentaram os totais, inclusive após reiniciar o receptor. Não foi retransmissão do provedor ou teste positivo pelo HTTPS público. [Escopo e limitações](docs/whatsapp-deduplicacao.md).
- **540 cenários JavaScript locais:** cinco suítes executadas na parte 10 da etapa 11 — 215 +82 +63 +67 +113 do sandbox; 38 cenários novos. Os resultados de marcos anteriores permanecem históricos.
- **Imagem e template reais:** dois ensaios aceitos pela 360dialog, com uma imagem pública e `disclaimer/en`. As repetições reutilizaram os registros sem novo envio. São testes de transporte; não validam cards personalizados ou lembretes fora da janela.
- **Resposta real do sandbox:** nove verificações HTTP aprovadas; entrada sintética local, um envio real aceito pela 360dialog e recebimento confirmado pelo usuário. Repetições não enviaram outra resposta; status isolado não reservou saída. Nenhuma operação Google.
- **Dependências:** nenhuma inconsistência encontrada por `pip check`.
- **Contrato da API:** exemplos JSON conferidos localmente, com preparação e validações dos serviços existentes. Essa verificação não é teste HTTP nem conexão com n8n e não acrescenta casos à suíte de 359 testes.
- **Proteção HTTP:** servidor local real respondeu 200 no health, 401 sem chave, 401 com chave incorreta, 200 com chave válida e 422 com correlação inválida; usou banco temporário e não expôs a credencial.
- **Consultas HTTP com Google real:** dia, semana, mês e ID retornaram 200; evento excluído retornou 404. As requisições sem chave/inválidas foram recusadas antes do conector. Foram 12 GETs no Calendar e zero mutações; os dois eventos foram preservados.
- **Criação HTTP com Google real:** 201 novo, 200 nas repetições e 409 para horário ocupado. Um POST e 16 GETs no Calendar; dia passou de dois para três eventos, com os anteriores preservados. Entradas inválidas e chamada sem chave não abriram o conector.
- **Alterações HTTP com Google real:** edição e reagendamento confirmados em 200, conflito em 409 e repetições em `unchanged`. Dois PATCHs somente na fixture da API, com os mesmos três IDs ao final e os outros compromissos preservados.
- **Prévia de exclusão HTTP com Google real:** 200 com evento/pergunta, 422 sem código ou com formato inválido e 502 seguro para código bem formado incorreto. O transporte fez somente GET e os três eventos permaneceram iguais. A exclusão por HTTP foi validada localmente com provedor simulado.
- **Primeiro fluxo n8n:** 18 verificações HTTP aprovadas, com consultas reais de dia/semana/mês, recusa de entrada inválida, bloqueio do webhook sem chave e respostas 401/503/502 preservadas ou sanitizadas. API normal restaurada; mesmos três eventos ao final. O export tem 80 cenários JavaScript aprovados, separados da suíte Python.
- **Tratamento de respostas n8n:** 215 cenários do apresentador e 82 do fluxo aprovados sem rede. Mais 19 verificações pelo webhook real conferiram mensagens, códigos e recuperação; os três eventos permaneceram iguais. Dúvidas, conflitos e resultados de escrita foram testados com dados fictícios, sem executar gravações.
- **Ensaio de integração:** 34 verificações HTTP reais entre n8n e API, com SDK/SQLite reais e Google em memória; 80 GETs e dez escritas simuladas, sem eventos fictícios restantes. Um disparo natural do Schedule Trigger recebeu HTTP 200 da API normal. Dois fluxos de teste despublicados e três eventos reais iguais antes/depois. Mais 130 testes estáticos conferiram isolamento, grafo e agendamento.
- **Verificações reais:** API HTTP 200, n8n conectado para consultas, autorização/descoberta da agenda e CRUD confirmados pelo Google. Conflitos impediram uma criação e um reagendamento; a repetição reutilizou o evento original inclusive em outro processo Python. Os registros distinguem as respostas da API das conferências na interface.

Os testes automatizados substituem provedores e transportes nas fronteiras documentadas. As validações reais são separadas: Google por comandos/API, WhatsApp fixo e mídia nas etapas anteriores, Gemini com dados fictícios e callback HTTPS neste marco. O agente dinâmico já está conectado ao n8n; conversas completas foram exercitadas com provedores controlados na parte 11. As mutações reais pelo canal permanecem sem validação; o [fechamento da etapa 11](docs/fechamento-etapa-11.md) preserva esse limite e a prova humana de consulta da parte 10. [Escopo, testes e recuperação](docs/whatsapp-agente.md).

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| `app/main.py` / `app/__main__.py` | Aplicação FastAPI e inicialização do servidor |
| `app/api_security.py` | Bearer, comparação exata, correlação segura e router v1 protegido |
| `app/api_whatsapp.py` | Comando autenticado de resposta fixa por claim ativo, sem aceitar destinatário ou texto |
| `app/api_deletion.py` | Prévia e exclusão HTTP com confirmação por ID/versão e resultados seguros |
| `app/api_updates.py` | Edição e reagendamento HTTP com validação, snapshots mínimos e falhas seguras |
| `app/api_creation.py` | Criação HTTP estrita, uso do serviço existente e estados seguros de mutação |
| `app/api_agent_creation.py` / `app/services/agent_creation.py` | Comando explícito de criação do rascunho, consentimento e reconciliação |
| `app/agent_creation_store.py` | Ledger privado de execução com identidade e resultado duráveis, sem TTL |
| `app/api_agent_updates.py` / `app/services/agent_updates.py` | Edição e reagendamento do rascunho por comando explícito, com versão vinculada e conferência |
| `app/agent_update_store.py` / `app/services/update_snapshot.py` | Ledger privado de alterações e snapshot completo anterior, sem TTL para resultados |
| `app/api_agent_deletion.py` / `app/services/agent_deletion.py` | Exclusão do rascunho só após aprovação específica, com vínculo de versão e conferência de ausência |
| `app/agent_deletion_store.py` | Reserva privada de uso único por proposta, com resultado durável e reconciliação |
| `app/conversation_store.py` | Contexto privado, revisão, prazo, rascunho e proposta de confirmação |
| `app/api_queries.py` | Consultas HTTP autenticadas, filtros estritos, DTO mínimo e falhas seguras |
| `app/settings.py` | Leitura e validação das configurações |
| `app/sandbox_webhook.py` | Entrada HTTPS restrita, autorização e encaminhamento de eventos novos ao n8n |
| `app/whatsapp_inbox.py` | Identidades persistentes de mensagens/status, reservas atômicas e estados de processamento |
| `app/whatsapp_outbox.py` / `app/whatsapp_sandbox_sender.py` | Reserva persistente da resposta e transporte restrito ao sandbox |
| `app/services/whatsapp_reply.py` | Resposta fixa por claim, decisão pela inbox e coordenação independente da IA |
| `app/sandbox_media_probe.py` / `app/whatsapp_probe_ledger.py` | Casos fixos de mídia/template e controle persistente separado dos ensaios |
| `scripts/test_whatsapp_media.py` | Prévia sem rede e envio explícito de imagem/template ao destinatário privado |
| `app/database.py` | SQLite, migração, metadados, registro de criação e trava das operações |
| `app/google_calendar.py` | OAuth, seleção da agenda e adaptador das operações CRUD |
| `app/google_transport.py` | Transporte com timeout e sem reenvio ou redirecionamento implícito |
| `app/cli_errors.py` | Tratamento seguro de falhas de autenticação, rede e SQLite nas CLIs |
| `app/services/interviews.py` | Serviço de criação independente da IA e do conector |
| `app/services/agenda.py` | Consultas por dia, semana e mês, com limites por fuso |
| `app/services/time_rules.py` | Validação comum de fuso, intervalo UTC, duração e hora civil |
| `app/services/conflicts.py` | Consulta de ocupação e relatório de conflitos independente da IA |
| `app/services/creation_identity.py` | Identidade exata do conteúdo para reconhecer criações repetidas |
| `app/services/request_preparation.py` | Pedidos incompletos, correções e seleção com esclarecimento, sem escrita |
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
| `app/prepare_request.py` | Preparação de JSON e perguntas, com consultas Google sob demanda |
| `app/check_conflicts.py` | Consulta de disponibilidade por intervalo, sem escrita na agenda |
| `examples/requests/` | Pedidos fictícios para demonstrar perguntas e preparação |
| `app/reminder_store.py` / `app/init_reminders.py` | Persistência dos lembretes e inicialização explícita, sem disparos |
| `app/services/reminders.py` / `app/reminder_calendar.py` / `app/plan_reminders.py` | Consulta Google e preparação local dos próximos avisos, sem envio |
| `app/services/reminder_sync.py` / `app/sync_reminders.py` | Sincronização manual dos avisos por ID exato, sem transporte |
| `app/api_reminders.py` / `app/services/reminder_tick.py` | Tick autenticado, trava entre processos e coordenação de sincronização/geração |
| `app/api_reminder_delivery.py` / `app/services/reminder_delivery.py` | Dispatch autenticado e envio com reserva, dupla leitura e gate transacional do canal |
| `app/api_reminder_recovery.py` / `app/services/reminder_recovery.py` | Recuperação local de reservas seguras e bloqueio durável de envios incertos |
| `scripts/setup_reminder_schedule.py` / `n8n/workflows/lembretes-periodicos.json` | Instalação específica e verificação periódica de um minuto no n8n |
| `tests/` | Testes locais do banco e das operações de agenda |
| `n8n/workflows/` / `n8n/tests/` | Cinco exports públicos inativos, exemplos fictícios e testes sem rede |
| `n8n/README.md` / `n8n/*manifest.json` | Inventário e manifestos de conferência dos fluxos instalados/publicados |
| `n8n/lib/` / `n8n/build-workflow.cjs` | Apresentador compartilhado e geração do Code node testado |
| `scripts/setup_n8n.py` / `scripts/test_n8n.py` | Configuração e consulta do webhook sem expor chaves |
| `scripts/setup_whatsapp_sandbox.py` / `scripts/register_sandbox_webhook.py` | Instalação do fluxo separado e registro do callback privado na 360dialog |
| `scripts/configure_sandbox_sender.py` | Configuração explícita e oculta do único remetente autorizado |
| `scripts/test_n8n_integration.py` / `scripts/n8n_integration_worker.py` | Ensaio HTTP isolado e API de teste com Google em memória |
| `compose.yaml` | Instância local do n8n |
| `.env.example` / `requirements.txt` | Configuração pública e dependências fixadas |
| `docs/` | Guias de configuração e operação |

## Dados e operação

`.env`, `.venv/`, `secrets/` e `data/` são locais e ignorados pelo Git. O banco da agenda fica em `data/agenda.db`, a inbox em `data/whatsapp-inbox.sqlite3` e a outbox em `data/whatsapp-outbox.sqlite3`; os dados do n8n ficam no volume Docker `genius-agenda-ai_n8n_data`. Nunca publique tokens ou credenciais em código, logs, prints ou exports.

Conserve os bancos nos backups: agenda, inbox, outbox, contexto e criações do agente guardam históricos distintos. O contexto fica em `data/agent-conversations.sqlite3` (schema 3, com TTL); os ledgers `data/agent-creations.sqlite3`, `data/agent-updates.sqlite3` e `data/agent-deletions.sqlite3` (todos schema 1) conservam resultados de execução sem TTL, inclusive os incertos. Apagar uma conversa não elimina esse ledger nem desfaz eventos. Consulte [persistência e recuperação da criação](docs/criacao-agente.md). O registro separado `data/whatsapp-media-probes.sqlite3` conserva o vínculo dos ensaios para impedir repetição; ele também permanece privado. Hashes são pseudônimos, não dados anônimos. Apagar ou substituir inbox/outbox perde a proteção contra repetições; resultados pendentes/incertos exigem reconciliação, sem liberação ou reenvio automáticos. O banco dos lembretes, `data/reminders.sqlite3` (schema 1), também conserva histórico e reservas sem TTL; remover a base perde a proteção contra repetição. Seu estado inicial vazio está no registro 125; a configuração explícita e o resultado da geração ficam no registro 127. Consulte a [persistência dos lembretes](docs/persistencia-lembretes.md). A fila/outbox do agente dinâmico são separadas, em `data/whatsapp-agent.sqlite3` e `data/whatsapp-agent-outbox.sqlite3`; mensagens ficam privadas durante processamento. Consulte os [limites de recuperação atuais](docs/whatsapp-agente.md) e os [da resposta fixa histórica](docs/whatsapp-respostas.md).

Para parar, use `Ctrl+C` no terminal da API e `docker compose stop` para o n8n. Para retomar, inicie o Docker, execute `docker compose up -d` e reinicie a API. Fechar o navegador ou parar os serviços preserva os dados; `docker compose down -v` remove o volume do n8n.

No OAuth externo em teste, com os escopos Calendar utilizados, o refresh token expira em sete dias. A aplicação local precisa estar ligada para executar automações. Consulte o [guia de configuração e diagnóstico](docs/configuracao.md) e a [operação do n8n](docs/n8n-local.md).

## Próximas etapas

1. **Etapa 14:** card simples, com estimativa de 25–40 minutos; não iniciado neste fechamento.
2. **Etapa 15:** painel simples opcional se o orçamento permitir, estimado em 35–50 minutos. Não é requisito do enunciado.
3. **Etapa 16:** qualidade, estimada em 40–60 minutos, incluindo ensaio real de CRUD e recebimento de lembrete ainda pendentes.
4. **Etapa 17:** vídeo e conferência da entrega até 18h, estimados em 30–45 minutos. [Ordem, critérios e cortes](docs/plano-entrega-hoje.md). As durações são estimativas, não registros de execução.

A etapa 12 está [concluída no escopo de teste](docs/fechamento-etapa-12.md). Antes de afirmar uso contínuo, ainda será necessário validar recebimento real de lembrete, canal autorizado fora da janela e hospedagem estável; o sandbox não fornece o template contextual necessário.

## Documentação visual

Os marcos importantes são registrados em capturas numeradas e saídas reais, incluindo prints relevantes enviados pelo usuário. O acervo tem **151 registros: 32 imagens e 119 saídas/relatórios em JSON**. A captura 044 é histórica: o evento temporário já foi removido. A 149 mostra o resumo recebido antes da simplificação visual; a 151 registra o planner, sem provar nova mensagem. Consulte o [registro de evidências](docs/evidencias.md). Os arquivos ficam em `data/evidencias/`, fora do Git.
