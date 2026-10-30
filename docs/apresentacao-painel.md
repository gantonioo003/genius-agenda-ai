# Apresentação do projeto

Material para apresentar o Genius Agenda AI no GitHub, em uma conversa técnica ou no LinkedIn. O painel tem um ambiente conectado e um modo demonstração com dados fictícios; deixe essa diferença explícita ao mostrar cada um.

## Materiais

- [Abrir a demonstração local](http://127.0.0.1:8000/painel/demo/): permite explorar agenda, preparo e lembretes sem credenciais ou alterações reais.
- [Tour visual do painel](assets/painel-tour.mp4): clipe sem narração, montado com cenas reais do modo demonstração. As cenas têm duração editorial; não representam uma medição do tempo de resposta.
- [Prévia animada acelerada](assets/painel-tour.gif): versão de 12 segundos, sem medição de desempenho.
- [Como executar e o que foi validado](../README.md): configuração, testes e limites atuais.
- [Arquitetura e estados](arquitetura.md), [fluxo WhatsApp](whatsapp-agente.md) e [modo demonstração](painel-web-demonstracao.md): detalhes para a conversa técnica.

O vídeo enviado ao recrutador em 26/10/2026 permanece um registro histórico. Este tour apresenta o painel acrescentado depois; não substitui aquela gravação nem comprova novas operações no Google ou novos recebimentos no WhatsApp.

## Fala de abertura

“O Genius Agenda AI é um assistente para organizar entrevistas pelo WhatsApp e por um painel web. Ele permite consultar, criar, editar, reagendar e excluir compromissos no Google Calendar, além de ajudar no preparo e enviar lembretes. O modelo interpreta os pedidos; Python valida os dados e controla a execução. Para apresentar o painel com segurança, também existe um modo de demonstração com dados fictícios.”

## Roteiro de 60–90 segundos

| Tempo aproximado | Tela ou ação | Fala sugerida |
|---|---|---|
| 0–10 s | Visão geral da demonstração | “O painel reúne a mesma experiência de agenda, com uma visão do dia e da próxima entrevista. Este ambiente usa somente exemplos fictícios.” |
| 10–25 s | Agenda e confirmação | “É possível consultar dia, semana e mês e fazer alterações. Antes de confirmar, a pessoa confere o compromisso e os dados da operação. Horários sobrepostos são sinalizados.” |
| 25–40 s | Preparo | “O preparo reúne contexto e checklist. Na integração real, o resumo pode ser organizado com IA; a disponibilidade é calculada pela agenda. Aqui, o conteúdo é demonstrativo e nenhuma IA é chamada.” |
| 40–55 s | Lembretes e card | “Os avisos têm estado e histórico. O projeto usa card uma hora antes e texto quando faltam quinze minutos. Aceite do provedor é diferente de confirmação de entrega. A prévia do painel não envia mensagens.” |
| 55–75 s | Diagrama ou fluxos n8n | “No WhatsApp, a entrada Python valida e registra a mensagem. O n8n ativa o processamento; o worker usa Gemini e os serviços da agenda. Python confere as regras, opera o Google e devolve a resposta pela 360dialog.” |
| 75–90 s | Repositório | “O projeto inclui testes de regras, falhas e repetições, documentação da API e um modo isolado para explorar a interface. É um ambiente local de portfólio, com as limitações de teste documentadas.” |

Para uma apresentação mais curta, mostre visão geral → uma confirmação → preparo → um card. Use o diagrama para explicar a integração; não é necessário abrir todos os endpoints ou bancos.

## Pipeline em linguagem simples

1. A pessoa envia uma mensagem ao número do **sandbox 360dialog**, um ambiente de teste que recebe mensagens reais de WhatsApp.
2. A 360dialog encaminha a mensagem à **entrada HTTPS em Python**. Ela confere o remetente autorizado, valida o conteúdo e registra a identidade da mensagem para evitar processamento repetido.
3. O **n8n** recebe os metadados e chama a API autenticada com o identificador do pedido. O texto e o destinatário não precisam circular pelo workflow.
4. O **worker Python** processa a fila durável. O agente usa **Gemini** para interpretar pedidos e os serviços de Python para consultar ou preparar operações.
5. Os serviços validam datas, conflitos, contexto e confirmações antes de executar no **Google Calendar**. A interpretação do modelo, sozinha, não autoriza uma alteração.
6. O **Python** envia a resposta pela **360dialog**, que faz a ponte com o WhatsApp. O n8n não é o transporte final dessa resposta.

O **SQLite** conserva contexto, confirmações, filas e registros de execução/envio. Isso permite identificar repetições e tratar resultados incertos sem simplesmente refazer uma operação.

O painel conectado usa os serviços da mesma agenda por rotas protegidas. Formulários de CRUD não precisam pedir ao Gemini que interprete os campos já estruturados. A demonstração fica inteiramente em memória e não percorre esse pipeline.

## Texto sugerido para LinkedIn

Desenvolvi o **Genius Agenda AI**, um assistente de agenda que integra WhatsApp e Google Calendar, agora com um painel web responsivo.

O projeto permite consultar e gerenciar entrevistas, acompanhar lembretes e reunir informações de preparo. Usei Python/FastAPI, Gemini, SQLite e n8n em Docker; a comunicação WhatsApp foi validada no sandbox 360dialog.

Além das operações, trabalhei nas situações que exigem mais cuidado: nomes ambíguos, conflitos de horário, confirmações vinculadas à alteração, pedidos repetidos e resultados de envio incertos. O painel inclui um modo demonstração com dados fictícios, para explorar a interface sem acessar uma agenda real.

O repositório reúne o código, os testes e as limitações conhecidas. O tour visual mostra o painel em demonstração; a integração real e seus ensaios estão documentados separadamente.

Código: [github.com/gantonioo003/genius-agenda-ai](https://github.com/gantonioo003/genius-agenda-ai)

## Versão para currículo

**Genius Agenda AI — assistente de agenda com IA**

- Integração de WhatsApp e Google Calendar com Python/FastAPI, Gemini, SQLite e n8n, incluindo CRUD, contexto de conversa, confirmações, conflitos e prevenção de repetições.
- Desenvolvimento de painel web responsivo com agenda, preparo, histórico de lembretes e modo demonstração isolado; testes automatizados, API documentada e validação da integração em ambiente de teste.

## Limites que vale saber explicar

- O WhatsApp atual usa um destinatário autorizado no sandbox; não é uma implantação comercial para múltiplos clientes.
- O painel conectado é local e de um proprietário. Serviços, computador e conectividade precisam estar disponíveis.
- As marcações de checklist ficam na memória da página. A demonstração não persiste alterações e não chama integrações.
- Resumos com IA dependem do provedor; briefing e checklist atuais são factuais/genéricos, e horários livres são cálculo de disponibilidade.
- Prévia de card não é comprovante de envio. Aceite do provedor não comprova entrega ou leitura.
- Resultados de testes, ensaios reais e confirmação humana estão separados no [índice de evidências](evidencias.md).
