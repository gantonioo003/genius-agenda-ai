# Preparo, resumos e horários livres no painel

A tela **Preparo** usa os mesmos serviços do WhatsApp, com a sessão protegida do painel. As consultas não criam eventos, reservam horários ou enviam mensagens.

## Resumo da semana ou do mês

Escolha semana ou mês e uma data de referência. Ao pedir o resumo, o servidor consulta a agenda atual e faz uma tentativa de organização com o modelo configurado. O texto usa datas e horários legíveis, sem exigir leitura de códigos, UTC ou identificadores.

O resultado informa se foi **organizado com IA** ou se é um **resumo factual**. Falha, limite ou indisponibilidade do modelo podem produzir a alternativa factual; falha na coleta do calendário não é tratada como agenda vazia. Um período sem compromissos não precisa chamar o modelo.

O resumo só é gerado por uma ação explícita. Trocar de tela ou atualizar a agenda não chama o Gemini em segundo plano. O modelo recebe os agregados e as opções editoriais preparados pelo serviço existente, sem receber descrições, participantes, IDs reais ou as notas do briefing.

## Horários livres

Informe as datas, a faixa do dia, a duração e uma margem opcional entre compromissos. O cálculo considera a ocupação da agenda configurada e seu fuso, sem usar IA para decidir quais intervalos estão livres.

As opções são alternativas e podem se sobrepor entre si. Nenhum horário é reservado pelo resultado, que representa a disponibilidade na hora da consulta. Para agendar, use **Novo compromisso** e confira a prévia; as validações de conflito são refeitas na confirmação.

## Briefing e checklist

Selecione uma entrevista da agenda e, se desejar, acrescente notas sobre o que quer preparar. O briefing relê os dados atuais do evento e separa:

- Informações cadastradas: título, data, horário, descrição e local.
- Contexto fornecido por você, sem tratá-lo como informação verificada externamente.
- Informações ausentes que precisam ser conferidas.
- Checklist genérico sugerido, sem deduzir empresa, cargo ou etapa pelo título.

O briefing e o checklist atuais são factuais; não usam pesquisa externa nem geração livre do modelo. Marcar os itens serve para acompanhar o preparo na própria sessão do navegador. Esse progresso não altera o Google Calendar e é limpo ao sair; não é sincronizado entre dispositivos.

## Segurança e rotas

Os POSTs abaixo exigem cookie da sessão, origem válida e CSRF, antes de ler o pedido ou acessar o calendário/modelo. As credenciais de Google e Gemini continuam no servidor.

| Rota local | Uso |
|---|---|
| `/painel/api/agenda/summary` | Resumo por semana/mês, a partir dos fatos atuais |
| `/painel/api/agenda/availability` | Até três alternativas de horário, sem reserva |
| `/painel/api/preparation/briefing` | Briefing e checklist de um evento selecionado |

Os endpoints da API de serviço em `/api/v1` mantêm a autenticação existente. O painel não recebe a chave dessa API. Sessão encerrada limpa os resultados privados; respostas antigas não devem substituir uma nova seleção ou consulta.

[Acesso local](painel-web-api.md) · [Agenda com confirmação](painel-web-agenda.md) · [Fases do painel](painel-web.md) · [Evidências](evidencias.md).


## Validação deste marco

Suíte final com **2.684 testes Python** e **61 testes Node** aprovada. O navegador conferiu os quatro recursos, alternativa factual, fonte vazia e fonte indisponível em ambiente controlado. Após renovar a autorização Google, a API instalada confirmou resumo **organizado com IA** sobre quatro compromissos reais, três horários livres e briefing factual com seis itens. A comparação antes/depois não encontrou alteração na agenda ou nos ledgers de execução/lembretes. Nenhuma mensagem foi enviada. Resultados e limites estão nos registros 193–197 do [índice de evidências](evidencias.md).
