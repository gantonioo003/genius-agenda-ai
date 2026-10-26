# Resumos e preparo — etapa 13

**Etapa 13 concluída no escopo compacto A/B/C:** resumo, horários livres, preparo/checklist e sugestão conservadora de reagendamento no WhatsApp. Passaram **2.344 testes Python, 19 novos**, com 256 fontes conferidas. O [fechamento da etapa 13](fechamento-etapa-13.md) separa entregas, limites e provas. As confirmações humanas anteriores continuam restritas a resumo/horários antes da simplificação visual; a nova sugestão não tem recebimento humano comprovado.

> **Replanejado em 26/10/2026 para entrega às 18h:** as partes 1-4 foram seguidas por preparo compacto, integração WhatsApp e sugestão leve conservadora. Briefing personalizado por IA, opções persistidas e aplicação por ordinal permanecem fora do escopo compacto. O desenho original abaixo conserva a evolução posterior. [Plano de entrega](plano-entrega-hoje.md).

**Base implementada:** funcionamento, [coleta](coleta-contexto-agenda.md), [resumos com IA](resumos-com-ia.md) e [horários livres](horarios-livres.md). Briefing/checklist foram reunidos no bloco A; B conecta as capacidades ao WhatsApp. A sugestão leve deriva a duração da entrevista e inclui sua ocupação original, sem reserva ou escrita.

## Objetivo e relação com o desafio

Transformar a agenda autorizada em um resumo útil e ajudar na preparação das entrevistas. O enunciado original cita resumos coerentes no objetivo e no cenário de consulta mensal; classifica resumos semanais/mensais e sugestões de reagendamento como nível avançado, e briefings personalizados como diferencial. Portanto, a consulta factual já existente é a base; a apresentação com IA, sugestões e preparo são a evolução desta etapa. Checklist, limites abaixo e formato de sugestões são decisões deste projeto, não exigências literais do recrutador. A classificação avançada da seção de níveis não reduz a exigência de tratar conflitos do módulo 1; as proteções já existentes continuam vigentes.

A fonte conferida é `Desafio_Genius_Agenda_AI_Auto_Genius_BRANDED.docx`: objetivo, exemplos de interação, módulo 5, cenário da agenda mensal e níveis de entrega. A conferência e o hash da fonte ficam na evidência 140. Imagens/cards (14), painel (15) e validação final/vídeo (16/17) continuam separados. As pendências reais dos lembretes não são resolvidas por este contrato; veja o [fechamento da etapa 12](fechamento-etapa-12.md).

## Pedidos e resultados previstos

| Capacidade lógica futura | Exemplos de pedido | Dados necessários | Resultado e limites |
|---|---|---|---|
| `summarize_agenda` | “Resume minha semana”; “Como está minha agenda do mês?” | Semana/mês e referência resolvidos; agenda/fuso autorizados; consulta completa | Período, totais conferidos e compromissos organizados por dia; texto curto da IA somente sobre os fatos disponíveis |
| `find_free_slots` | “Quais horários de 30 minutos tenho livres sexta, entre 9h e 12h?” | Período, faixa diária, duração e restrições explícitas | Até três opções futuras, ordenadas por início, livres na agenda consultada naquele instante; nenhuma reserva |
| `suggest_reschedule` | “Sugere outro horário para essa entrevista amanhã à tarde” | Evento único relido, duração válida, período/faixa e restrições | Opções referidas ao evento correto, com duração preservada; se “à tarde” não tiver faixa definida, perguntar |
| `prepare_briefing` | “Me prepara para a entrevista de amanhã” | Evento único relido; informações relevantes do evento e fornecidas pelo usuário | Fatos confirmados, contexto informado, lacunas e sugestões de preparo claramente separados |
| `build_checklist` | “O que preciso preparar para essa entrevista?” | Evento identificado; contexto opcional | Lista curta de ações sugeridas; itens específicos só se sustentados pelos dados; pode complementar o briefing |

Esses nomes são responsabilidades lógicas do desenho original. Resumo, horários livres e preparo compacto possuem API/CLI e integração WhatsApp por parser limitado; a sugestão leve foi acrescentada ao caminho de conversa sem nova ferramenta do modelo. Os exemplos amplos da tabela não representam toda a gramática implementada; os [formatos aceitos](preparo-whatsapp.md) são explícitos. Briefing/checklist usam template factual e sugestões genéricas. Personalização por IA e opções persistidas permanecem evolução posterior. Misturas com alterações pedem esclarecimento; “próxima”, número ou ordinal não selecionam automaticamente uma entrevista.

## Datas, períodos e fatos da agenda

Reutilizar as [consultas existentes](consultas-agente.md) e os [períodos civis](consulta-semana-mes.md): semana de segunda a domingo e mês civil, com fim exclusivo no início do período seguinte. “Esta semana/mês” inclui o período completo, inclusive compromissos já encerrados, identificando o que já passou. “Próxima semana” significa a semana civil seguinte; “resto da semana” começa no instante atual e exige suporte explícito ao recorte, não um filtro inventado pelo modelo. Data ambígua pede esclarecimento.

O relógio confiável é UTC do servidor, convertido ao fuso IANA da agenda para expressões relativas e apresentação. Não usar o relógio alegado numa mensagem, o fuso do Windows ou o último fuso mencionado no chat como configuração da agenda. Intervalos são comparados em UTC; mudanças de horário de verão e dias civis precisam de validação no backend.

Listar todos os resultados do período dentro dos limites operacionais configurados. Se uma página falhar, houver limite atingido ou dados incoerentes, não afirmar completude, totais finais ou disponibilidade. A [coleta da parte 2](coleta-contexto-agenda.md) limita a leitura a 20 páginas e 2.000 itens brutos, além de 16.000 bytes de contexto compacto. Esses limites pertencem ao adaptador novo; consultas legadas não mudaram. Exceder o limite, ou atingir o teto de páginas/itens com paginação ainda pendente, falha sem retornar páginas omitidas como se o resultado fosse completo. Os limites exatos numa fonte terminal e o orçamento exato de contexto são aceitos. Reduzir o período quando necessário. Uma agenda vazia é resultado válido somente após leitura completa bem-sucedida. Cancelados não entram como compromissos ativos; dia inteiro aparece como dia inteiro, sem horário fabricado; recorrência usa ocorrências e IDs próprios.

O período usa sobreposição, não apenas eventos cujo início esteja dentro dele. Contar cada ocorrência ativa por ID exato uma única vez, mesmo atravessando dias; a agenda diária pode mostrá-la em mais de um dia sem duplicar o total do período. Exibir `tentative` como provisório quando retornado, separado de confirmado. Não publicar soma de horas como “tempo ocupado”: eventos sobrepostos e dias inteiros exigiriam regra própria de união e recorte, fora desta primeira composição.

Totais, datas, horários, durações e agrupamentos vêm do backend. “Importante” não significa prioridade criada pela IA: apresentar critérios verificáveis, como proximidade do início, ou prioridade explicitamente fornecida. Não presumir cargo, etapa seletiva, resultado, tempo de deslocamento ou importância por título. Se o objetivo do destaque estiver incerto, perguntar. Informar quando algo for apenas sugestão de organização.

A resposta WhatsApp futura respeitará o limite atual de **3.500 caracteres**, em uma única resposta, com no máximo **dez eventos detalhados**. A fonte deve continuar completa: condensar por dia e informar explicitamente “mostrando X de N” se houver mais eventos; oferecer consulta de um período menor. Na estratégia efetivamente implementada na parte 3, o modelo recebe somente período/agregados/catálogo; os detalhes de até dez eventos ficam no Python e não são transmitidos à IA. Essa decisão mais restrita substitui a projeção originalmente proposta para os resumos. Não confundir limitação de exibição/contexto com falha de leitura nem alegar análise individual de eventos omitidos. Não disparar várias mensagens para contornar o limite.

## Disponibilidade e sugestões

Calcular intervalos livres em Python, sem pedir à IA que faça contas ou escolha o que conta como ocupado. Reutilizar a semântica de [conflitos](conflitos.md), com projeção própria que preserve os campos de ocupação necessários. A listagem mínima atual não contém `transparency`; não basta repassá-la ao LLM para inferir disponibilidade.

- Considerar apenas a agenda autorizada. Dizer “livre nesta agenda, na consulta de agora”, sem garantir disponibilidade pessoal, de terceiros, de outra agenda ou de uma sala.
- Exigir datas/faixa de horas e duração. Para reagendamento, reaproveitar a duração atual válida, relida do evento. Não presumir expediente, fim de semana livre ou que “à tarde” signifique uma faixa já acordada. Buffers explícitos do usuário são respeitados; o padrão do projeto é zero, informado como ausência de margem para deslocamento/preparo.
- Usar os mesmos intervalos semiabertos do controle de conflitos: fim de um compromisso pode coincidir com início do próximo se não houver buffer. Para sugestões, a grade inicial escolhida é de 15 minutos, duração em minutos reais e no máximo três opções; são decisões de produto implementadas na [parte 4](horarios-livres.md).
- Bloquear eventos opacos e tentativos conforme as regras existentes; um evento com transparência explicitamente livre não bloqueia. Dia inteiro ocupado bloqueia o período civil correspondente, incluindo dias sucessivos com fim exclusivo. Na projeção de ocupação validada do conector, transparência omitida é tratada como `opaque`, como já ocorre nos conflitos; isso é diferente de tentar recuperar esse campo após descartá-lo na listagem mínima. Demais dados de ocupação desconhecidos ou inválidos bloqueiam uma conclusão segura, em vez de virar tempo livre.
- Na evolução completa, uma exclusão da ocupação própria deverá usar somente o ID exato, preservando os demais. **A versão compacta atual é conservadora e não exclui o evento original da ocupação**, podendo omitir alternativas sobrepostas ao próprio horário. Reagendamento de dia inteiro continua fora do executor atual.
- Nenhum horário passado é sugerido. As opções são uma fotografia da leitura, não bloqueio/reserva. Nova escrita exige reler evento e conflitos pelos serviços atuais. “Nenhuma opção nessa faixa” não significa “sem horário livre em toda a agenda”.

Se a consulta falhar ou estiver incompleta, devolver indisponibilidade, sem prometer opção livre. Selecionar uma opção não altera evento, não aprova outra proposta e não atualiza lembretes. Só uma mutação realmente confirmada pelos executores existentes poderá repercutir na sincronização dos lembretes.

## Briefing e checklist

Organizar o briefing em quatro blocos curtos: **compromisso confirmado**, **contexto fornecido**, **o que falta saber** e **sugestões de preparo**. O checklist contém até seis ações concretas. Se faltarem dados, oferecer orientação genérica identificada como sugestão, ou fazer uma pergunta objetiva quando a lacuna impedir personalização. Não bloquear um checklist genérico só porque empresa/cargo não foram informados.

Título/data/hora/fuso/local são copiados da leitura validada quando disponíveis. Descrição e notas do usuário só entram como contexto fornecido, não como verificação externa. Uma observação “empresa Exemplo” não autoriza inventar atuação, cultura, vaga, entrevistador, salário ou requisitos. O tipo do evento pode ser sugerido como classificação, com incerteza explícita; não vira fato obrigatório.

Não pesquisar sites, abrir links, anexos, currículos ou descrições de vaga nesta primeira versão. URLs presentes no evento permanecem dados, nunca autorização de navegação. Conflito entre a data dita pelo usuário e a agenda precisa de esclarecimento; não editar o Google como efeito colateral do preparo. Checklist não é confirmação de que a tarefa foi feita e não ganha persistência própria de “concluído” nesta etapa.

## Uso da IA, privacidade e falhas

A [consulta atual](consultas-agente.md) não envia eventos ao modelo e não faz segunda chamada para resumir. A [prévia de ações](agente-ia.md) também não é uma ferramenta de briefing. A etapa 13 exigirá capacidade e projeção de contexto próprias, mantidas atrás da interface de modelo existente. Nenhuma mudança nessa transmissão de dados foi ativada na parte 1.

No fluxo futuro, o backend entrega ao modelo apenas fatos necessários, com referências locais sintéticas como `E1`, sem IDs reais da agenda/eventos, ETags, telefone, participantes, tokens ou payload bruto. No resumo implementado na parte 3, títulos/início/fim/dia inteiro ficam na renderização local, enquanto o modelo recebe só período, contagens e catálogo de destaques. A preparação de briefing futura poderá exigir outra projeção, com trecho relevante de descrição/notas explicitamente fornecidas; essa transmissão ainda não foi implementada. O pedido de preparo autoriza usar esse contexto mínimo da agenda autorizada para a finalidade solicitada; dados pessoais podem existir no texto livre e isso não equivale a anonimização automática. A implementação deve limitar tamanho, remover campos dispensáveis e documentar o que transmite antes de ligar a capacidade.

Separar fatos estruturados e narrativa: a IA não determina totais ou horários, não escreve campos críticos e não recebe ferramenta de mutação nesse passo. Resposta do modelo tem formato/limites conferidos pelo backend e referências resolvidas somente a fontes fornecidas. Referência desconhecida, fato crítico alterado, instrução de executar ação ou saída inválida deve ser rejeitada. Só conferir IDs citados não prova ausência de alucinação; quando a narrativa não puder ser sustentada, usar apresentação determinística dos fatos já lidos, sem texto gerado.

Se o Google falhar, informar falha de leitura; não usar memória como agenda atual nem transformar o erro em vazio. Se a IA falhar após leitura válida, o resumo ainda pode apresentar a lista factual determinística. Briefing/checklist podem usar um modelo textual genérico explícito, sem alegar personalização que não ocorreu. Nenhum retry de escrita/envio incerto é criado por esta etapa. Aplicam-se os limites de chamadas/tamanho/timeout já configurados no adaptador; corte no contexto deve ser detectado, nunca mascarado como leitura completa.

Títulos, descrições, notas, mensagens e respostas são conteúdo não confiável. Instruções dentro deles não mudam política, agenda, remetente ou ferramentas. Texto gerado não é enviado automaticamente a terceiros nem salvo em histórico novo por padrão. Qualquer persistência futura precisa de finalidade, retenção e isolamento definidos antes da implementação; não armazenar respostas/contexto de preparo em `data/reminders.sqlite3`.

## Contexto e autorização

Reutilizar seleção de evento e isolamento da conversa dos [serviços de contexto](contexto-conversa.md), com releitura atual antes de preparar uma ocorrência. Não guardar uma cópia da agenda como se fosse atual. Sugestões e notas de preparo precisam de estado tipado separado de uma proposta de criação/edição/exclusão; `E1` do resumo ou “primeira opção” não é ID de evento, autorização ou comando executável.

O WhatsApp reconhece respostas afirmativas antes do LLM, e leituras anteriores podiam preservar uma proposta de escrita. A integração do bloco B passou a revogar por CAS uma confirmação CRUD anterior ainda não consumida antes da nova capacidade informativa. Preserva o rascunho e avisa que a aprovação anterior perdeu validade, sem cancelar o compromisso. Se o contexto mudar concorrentemente ou a revogação falhar, interrompe o caminho antes dos serviços e orienta a retomar o pedido explícito. Confirmações consumidas e seus resultados continuam preservados.

Uma confirmação já consumida não é revogada, e bloqueios existentes do remetente/fila não são contornados para entrar nesse modo. Operações `running`, `unknown`, aceites/incertezas e ledgers não são apagados nem liberados por essa revogação. “Sim”, “confirmo”, número ou ordinal isolado depois de resumo/briefing não autorizam uma mutação anterior nem aplicam uma sugestão. A resposta informativa não deve terminar em “quer esse horário?”: orientar um pedido inequívoco, por exemplo “Reagende a entrevista selecionada para [data/hora]”. Uma opção será relida e preparada pelos executores atuais; confirmação é vinculada ao evento e à alteração exatos. Exclusão conserva a confirmação obrigatória existente. Resumos e preparo, por si, nunca executam escrita.

## Reutilização e entregas seguintes

| Já existe | Ainda será necessário |
|---|---|
| Consulta por dia/semana/mês/ID e períodos civis | [Coleta factual e projeção mínima implementadas](coleta-contexto-agenda.md) (parte 2) |
| Interface de LLM e adaptadores independentes | Resumo contextual, validação e fallback factual (parte 3) |
| Validação de duração/fuso e conflitos | Cálculo de janelas livres e política de restrições (parte 4) |
| Seleção/rascunho/confirmação e executores CRUD | Sugestões tipadas sem execução automática (parte 5) |
| Consulta exata do evento | Projeção mínima e preparação contextual (partes 6/7) |
| Fila, contexto e envio WhatsApp/n8n | Novas intenções, isolamento e revogação segura de confirmação (parte 8) |

Os nomes do desenho original identificam capacidades lógicas. Quatro caminhos informativos estão integrados por parser, sem novo schema de ferramentas do modelo, migração ou variável de ambiente. A [coleta factual](coleta-contexto-agenda.md) conserva API/CLI e envelope próprios; os estados funcionais do contrato original não acrescentam status HTTP à coleta. A sugestão entregue é conservadora; narrativa personalizada de preparo e aplicação de opções persistidas continuam futuras.

## Sequência da etapa 13 - escopo de entrega revisado

- [x] 1. Definir funcionamento, pedidos, dados, respostas e limites.
- [x] 2. Buscar compromissos da semana/mês por API/CLI; registros 141/142.
- [x] 3. Gerar resumos com IA e alternativa factual por API/CLI; registros 143/144.
- [x] 4. Encontrar horários livres com duração, faixa e conflitos por API/CLI; registros 145/146.
- [x] A. Unir briefing e checklist em um preparo compacto (antigas partes 6/7) — template factual, API/CLI e leitura real; registro 147.
- [x] B. Conectar resumo, preparo/checklist, disponibilidade e sugestão conservadora ao WhatsApp, com parser limitado, proteção CAS e apresentação simples (antiga parte 8).
- [x] Sugestão leve: evento relido, duração atual derivada, ocupação original preservada; reagendamento exige prévia e confirmação posterior.
- [x] C. Validar e fechar o escopo compacto da etapa, com 2.344 testes Python e documentação/registros 150/151 (antigas partes 9/10). Qualidade geral e vídeo continuam nas etapas 16/17.

**Limites da sugestão entregue:** mantém o horário original como ocupado, não guarda opções nem aceita ordinal; pode omitir alternativas sobrepostas ao próprio evento. Personalização extensa de briefing e aplicação de sugestões persistidas continuam futuras. Painel simples é opcional conforme o orçamento. Qualidade ainda precisa do ensaio real de CRUD e de lembrete recebido. Consulte o [plano e as estimativas](plano-entrega-hoje.md).

## Verificação da definição original — parte 1

O [contrato legível por máquina](../examples/preparation/functional-contract.json) e os [24 cenários fictícios](../examples/preparation/acceptance-cases.json) registram decisões a implementar. A verificação local confere JSON, referências, coerência dos limites e preservação das fontes existentes; não executa esses comportamentos, modelo, Google, WhatsApp ou n8n. As suítes anteriores de 1.956 Python/981 JavaScript permanecem históricas, sem nova execução. O registro 140 é JSON porque não houve nova interface ou resultado visual; o PDF permanece preservado para a consolidação da etapa.


## Coleta factual — parte 2

A [nova coleta](coleta-contexto-agenda.md) usa fuso da agenda autorizada e fornece fatos, contadores por período/dia e até dez detalhes com omissões explícitas. Datas/horários não dependem do modelo; nenhum contexto foi enviado à IA. A suíte atual passou com **2.044 testes Python, 88 novos**, e as fontes JavaScript/exports permanecem iguais aos 981 cenários históricos. Duas leituras CLI reais do Google foram registradas separadamente dos testes isolados. Registros 141/142; próxima parte: resumos com IA e alternativa factual.


## Resumos — parte 3

Os [resumos estruturais com IA](resumos-com-ia.md) foram implementados com uma chamada opcional ao modelo e renderização factual por Python. O payload usa somente período/agregados/catálogo; títulos e IDs não são transmitidos. Fonte inválida falha antes do modelo, e indisponibilidade/plano inválido usa alternativa factual identificada. Suíte atual de 2.126 testes Python (+82), provas Gemini reais separadas e registros 143/144. A próxima parte é encontrar horários livres; a integração dessas novas intenções ao WhatsApp continua na parte 8.


## Disponibilidade — parte 4

Os [horários livres](horarios-livres.md) já são calculados por serviço/API/CLI, sem IA, estado de conversa ou reserva. A busca exige intervalo de até 31 dias, faixa diária e duração; usa fuso da agenda, grade de 15 minutos, margem explícita e até três alternativas. Fonte incompleta falha sem declarar livre. Suíte atual: 2.225 Python (+99); duas leituras reais e seis verificações pelo legado, registros 145/146. Excluir o próprio evento e transformar uma opção em sugestão de reagendamento pertencem à parte 5.

## Integração compacta — bloco B

O [guia WhatsApp](preparo-whatsapp.md) registra os formatos aceitos e a apresentação humana. Os totais anteriores pertencem aos respectivos marcos; a integração anterior passou com 2.325 Python (+56) e 253 fontes. Três chamadas API reais e duas respostas WhatsApp confirmadas pelo usuário antecedem este fechamento; o print 149 conserva a apresentação anterior. O formato simplificado foi conferido por leitura real sem envio. A sugestão e uma prévia pendente passaram com Google real, planejador fixo e stores temporários, preservando o snapshot do evento, sem escrita, LLM ou mensagem. A suíte final de 2.344 e os limites estão no [fechamento da etapa 13](fechamento-etapa-13.md).
