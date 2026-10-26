# Resumos e preparo no WhatsApp

**Etapa 13 concluída no escopo compacto:** resumo semanal/mensal, preparo com checklist, horários livres e sugestão conservadora de reagendamento estão integrados ao agente WhatsApp. Passaram 2.344 testes Python, 19 novos. O [fechamento da etapa 13](fechamento-etapa-13.md) preserva os limites das provas; isso não encerra a entrega do projeto.

## Pedidos aceitos

O reconhecimento é um parser determinístico limitado em português, sem nova ferramenta ou chamada de LLM para interpretar esses pedidos. Use uma intenção por mensagem e os formatos explícitos abaixo.

| Capacidade | Exemplos |
|---|---|
| Resumo | `Resume minha semana`; `Resumo do mês`; `Resumo da próxima semana`; `Resumo do próximo mês`; `Resumo da semana de 2030-06-19` |
| Preparo e checklist | `Me prepara para essa entrevista`, após selecionar uma ocorrência nas consultas existentes; `Briefing id ID_DA_OCORRENCIA`; `Checklist id ID_DA_OCORRENCIA` |
| Contexto opcional | `Briefing id ID_DA_OCORRENCIA Contexto: Quero revisar minhas experiências com APIs.` |
| Horários livres | `Horários livres amanhã das 09:00 às 12:00 por 30 minutos`; `Horários livres 2030-06-20 das 09:00 às 12:00 por 30 minutos margem 15 min` |
| Intervalo de datas | `Horários livres de 2030-06-20 a 2030-06-21 das 09:00 às 12:00 por 30 minutos` |
| Sugestão para uma entrevista | `Sugira horários para reagendar essa entrevista amanhã das 09:00 às 12:00`; `Sugira horários para reagendar id ID_DA_OCORRENCIA 2026-10-27 das 09:00 às 12:00` |

Datas explícitas de entrada usam ISO; hoje/amanhã e períodos relativos usam o relógio UTC convertido ao fuso atualizado da agenda. Não há escolha de agenda/fuso por mensagem nem expediente presumido. Faixa, duração, margem e limites da [disponibilidade](horarios-livres.md) continuam valendo. Contexto do preparo aceita até 1.200 caracteres; a mensagem inteira tem limite de 4.000.

Falta de período, data/faixa/duração, evento selecionado ou informação ambígua gera pergunta, sem abrir os serviços quando a ambiguidade é detectável no parser. Texto citado/negado não é uma intenção positiva. Misturar preparo com criação, edição ou exclusão exige esclarecimento. “Primeira entrevista”, número isolado e opção de horário não selecionam nem executam um evento. Não é criado um rascunho novo para completar esses pedidos.

## Respostas e fatos

- **Resumo:** a IA pode escolher somente layout e até três destaques de um catálogo factual. Recebe período, agregados e catálogo, sem IDs, títulos, links, descrições ou conversa. O Python preserva os fatos e monta a apresentação. Falha de IA após fonte válida usa a alternativa factual existente; falha de fonte não produz resumo vazio.
- **Preparo:** relê a ocorrência exata e reúne título, data/horário, local, contexto informado e até seis itens genéricos de checklist. Usa template factual, sem IA ou pesquisa. Descrição/notas não são verificação externa; instruções nelas continuam dados. Falta ou mudança de evento pede nova seleção.
- **Disponibilidade:** calcula até três alternativas futuras, que podem se sobrepor, apenas na agenda autorizada. Mostra duração, margem quando diferente de zero e indicação de mais opções somente quando comprovada. Nenhuma reserva é criada.
- **Sugestão de reagendamento:** relê o ID literal ou a seleção atual pelo briefing e deriva a duração real em minutos inteiros, sem aceitar duração escolhida pelo solicitante. A busca conserva a ocupação do horário original; por isso pode omitir alternativas sobrepostas ao próprio evento. Evento ausente, alterado, de dia inteiro ou com duração não suportada pede esclarecimento. A seleção existente é atualizada por CAS; opções não são armazenadas e não podem ser aplicadas por número/ordinal.

No WhatsApp, a apresentação usa datas como **20/06/2030**, horários como **9h às 9h30**, listas curtas e **“Horário de Recife.”** no caso normal dessa agenda. Códigos ISO/IANA e referências `S1` não aparecem nessa apresentação. Datas diferentes continuam identificadas; offsets UTC aparecem quando necessários para distinguir horas repetidas ou uma transição dentro da opção. Os contratos e textos detalhados da API/CLI permanecem disponíveis nos guias próprios.

O briefing compacto só é montado quando os fatos, contexto e checklist estruturados passam pela validação. Falta de dados válidos não vira uma resposta bonita inventada. O limite final continua sendo 3.500 caracteres. A integração não cria armazenamento próprio de preparo; mensagens e saídas seguem as regras privadas do transporte existente.

## Confirmações anteriores

Antes de executar a nova capacidade informativa, o orquestrador revoga por CAS uma confirmação CRUD anterior ainda não consumida e avisa que aquela aprovação perdeu validade. Preserva o rascunho e não cancela o compromisso. Falha ou disputa nessa revogação interrompe o novo caminho antes dos serviços.

Confirmações já consumidas, operações em execução/incertas e seus ledgers são preservados. A resposta informativa não pede aprovação de horário e não transforma “sim”, número ou ordinal em escrita. Uma alteração futura exige pedido explícito, releitura, conflitos e confirmação pelo executor existente.

No WhatsApp, o pedido de reagendamento agora passa **sempre por prévia**, inclusive quando vem como imperativo completo. Somente a confirmação explícita posterior pode entrar no executor existente. Pedir uma sugestão não cria essa aprovação e não move o evento. As APIs diretas permanecem com os contratos anteriores.

## Provas e limites

Passaram **2.325 testes Python, 56 novos sobre a base de 2.269**: 33 do parser/apresentação, 12 da orquestração/CAS, quatro das factories e sete dos helpers/apresentação de resumos. Foram conferidas **253 fontes**. As **981 verificações JavaScript são históricas**; os cinco exports n8n foram preservados.

Três chamadas HTTP reais retornaram 200: resumo em `ai_organized`, preparo com seis itens e disponibilidade com três opções. Duas solicitações reais no WhatsApp foram processadas, com um aceite e um status `delivered` por saída. O usuário confirmou o resumo com uma imagem e os horários por resposta. **Essas confirmações ocorreram antes da simplificação visual.** O print 149 documenta a resposta anterior; não comprova recebimento humano do formato final. Depois, as três capacidades instaladas produziram respostas formatadas em um ensaio de leitura real Google/Gemini, sem enviar mensagens. A API foi recarregada e suas rotas/fila ociosa conferidas. Essa prova de serviço permanece separada do recebimento humano. O registro 148 consolida a integração.

O endpoint temporário antigo ficou inacessível enquanto o cadastro do provedor ainda apontava para ele. A recuperação usou um novo host de túnel HTTP/2 e a confirmação GET do cadastro, preservando a política do receptor e as credenciais existentes. Isso não comprova hospedagem estável ou prontidão de produção. Não publicar a URL privada nem credenciais nos exemplos.

O novo caminho de sugestão e a prévia obrigatória têm 19 testes novos aprovados: 12 do serviço e sete do parser/fluxo. A suíte completa passou com 2.344 testes e 256 fontes conferidas. Leitura real da sugestão e prévia pendente foram conferidas com Google, planejador fixo e stores temporários; o snapshot do evento permaneceu igual, com zero escrita, LLM ou mensagem. Os 981 JavaScript seguem históricos e os cinco exports foram preservados. [Fechamento e provas](fechamento-etapa-13.md). Ainda não houve confirmação humana de uma nova sugestão pelo WhatsApp.

Próximas etapas: card simples, painel reduzido opcional conforme orçamento, qualidade e vídeo até 18h. O ensaio real de CRUD e o recebimento de lembrete continuam pendentes na qualidade. As durações do [plano de hoje](plano-entrega-hoje.md) são estimativas, não execuções concluídas.
