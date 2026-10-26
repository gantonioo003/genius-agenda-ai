# Fechamento da etapa 11 — agente de IA

**Concluída em 25/10/2026 no escopo do WhatsApp de teste pela 360dialog.** As doze partes entregaram interpretação estruturada, consultas, contexto privado, esclarecimentos, confirmações e execução das operações de agenda pelos serviços Python existentes. O fluxo n8n publicado ativa uma fila durável; o worker processa e responde ao único destinatário autorizado.

A versão final tem **1.561 testes Python aprovados, 53 novos na parte 11, e 29 verificações HTTP finais**. Uma consulta diária real foi recebida no WhatsApp e confirmada pelo usuário na parte 10. As mutações conversacionais e as aprovações das sondagens Gemini foram verificadas com agenda e consentimento fictícios. O fechamento preserva essa distinção e não caracteriza implantação de produção.

## Checklist das doze partes

| Parte | Entrega | Registro e alcance |
|---|---|---|
| 1 | Definir pedidos, ferramentas e limites | 093; contrato e cenários fictícios de aceite |
| 2 | Conectar modelo por adaptador independente | 094 e diagnóstico 101; OpenAI preservado, seleção Gemini efetivada na parte 10 |
| 3 | Conectar consultas e preparação HTTP | 095/096; ferramentas de leitura e preparação sem executor de escrita |
| 4 | Persistir contexto mínimo por conversa | 097/098; SQLite, TTL, isolamento, CAS e reinício |
| 5 | Esclarecer dados faltantes e ambiguidades | 099/100; rascunho, correções, escolha explícita e releitura |
| 6 | Vincular confirmações à proposta | 102/103; evento/versão/conteúdo, expiração, revogação e consumo único |
| 7 | Criar pelo agente | 104/105; comando explícito, validações e ledger durável, com agenda fixture |
| 8 | Editar e reagendar o evento correto | 106/107; snapshot, versão, campos preservados e reconciliação, com agenda fixture |
| 9 | Excluir após aprovação específica | 108/109; proposta obrigatória, consumo único e ausência conferida, com agenda fixture |
| 10 | Integrar conversa e resposta ao WhatsApp/n8n | 110–114; fila/worker/outbox, Gemini selecionado e consulta humana recebida |
| 11 | Verificar conversas, conflitos, repetições e falhas | 115–118; nove ajustes, testes finais, sondagens reais com agenda fictícia e retomada operacional |
| 12 | Revisar entregas, documentação, evidências e planner | 119/120; auditoria documental, índice consolidado e planner atualizado para a etapa de lembretes |

Os guias de [escopo e ferramentas](agente-ia.md), [integração WhatsApp](whatsapp-agente.md) e [conversas e falhas](testes-conversas-agente.md) detalham os contratos. Os critérios de aceite fictícios não são transcrições de conversas executadas.

## Percurso e garantias verificadas

O receptor valida o callback e o remetente, reserva identidades na inbox e persiste o trabalho antes de ativar o n8n. A ponte recebe somente origem, UUID técnico, contagens e tipos; texto e telefone ficam fora do workflow. A API autentica a ativação; o worker usa o contexto e os serviços existentes, persiste a resposta e reserva a saída antes do transporte 360dialog.

O modelo propõe intenção e argumentos estruturados. A aplicação valida os dados, controla a autorização e escolhe os executores. Criar, editar e reagendar dependem de pedido explícito e dados sustentados pelo texto/contexto; um pedido de esperar pela aprovação é respeitado. Exclusão sempre depende de prévia e aprovação inequívoca da proposta exata, ainda válida. Aprovar outra proposta ou repetir “sim” não autoriza uma nova exclusão.

Contexto, rascunho e confirmação têm prazo e revisão. Criações, alterações e exclusões mantêm ledgers separados, sem expirar resultados incertos. Repetições confirmadas ou incertas reconciliam por leitura, sem repetir a escrita. A fila respeita a ordem por remetente; interrupção em processamento/envio e incerteza bloqueiam retomada cega. Uma resposta já preparada pode continuar após reinício sem nova interpretação ou operação de agenda.

As nove correções da parte 11 cobrem espera por confirmação, ID exato, vínculo entre texto e campos, vários pedidos de escrita, correções ignoradas, prévia recusada no transporte, término inventado, continuidade de consulta e contenção SQLite. O ledger de exclusões passou de 1 s para 5 s de espera por conexão bloqueada; a regressão exige reserva única e o mesmo nonce, sem repetir DELETE. A causa exata das falhas iniciais de concorrência não foi capturada e permanece sem diagnóstico direto.

## Resultados e matriz de provas

| Prova | Resultado confirmado | Limite |
|---|---|---|
| Python final, parte 11 — 115 | 1.561 aprovados, zero falhas/erros/skips; 53 novos: 13 de conversas, 17 de receptor/API/worker, 22 de autorização e um de contenção | Provedores substituídos; falhas e rodadas intermediárias preservadas |
| HTTP TCP final — 116 | 29 verificações: 17 requisições de negócio e 12 observações, dois processos e SQLite reais | Ponte n8n, modelo, Google e saída fictícios |
| JavaScript — 110 | 540 cenários nas cinco suítes; 38 novos na parte 10 | Resultado histórico; sem reexecução na parte 11 ou no fechamento |
| n8n Docker publicado — 112 | 42 verificações e 14 chamadas ao webhook; API/worker/SQLite reais | Parte 10; modelo, Google e sender fixtures; escritas somente fictícias |
| Gemini real — 117 | 15 chamadas entre as sondagens preservadas; sequência final de três HTTP 200: dia → semana → prévia de exclusão por ID explicitamente informado | Agenda e aprovação sintéticas; uma exclusão fictícia, repetição sem modelo ou nova exclusão; falhas anteriores preservadas |
| WhatsApp humano — 114 | Consulta diária persistida, envio aceito e recebimento confirmado pelo usuário | Parte 10; somente leitura; contagem interna Google/LLM não instrumentada |
| Retomada — 118 | API/receptor atualizados com fila ociosa; túnel recuperado e callback cadastrado/conferido | Sem nova mensagem WhatsApp; n8n, credenciais e exports preservados |
| Auditoria do fechamento — 119 | Revisão documental, hashes, links e proteção de arquivos privados no Git | Sem nova suíte, chamada a provedor ou mudança de runtime |
| Planner — 120 | Renderização do PDF com a etapa 11 concluída e a etapa 12 de lembretes como próxima | Prova do documento atualizado, sem nova execução do produto |

HTTP 200 do modelo comprova uma resposta do provedor, não a correção completa da conversa. A primeira sondagem da parte 11 inventou um término e motivou correção; outras tentativas também ficam no histórico. A sequência final de exclusão usou identificador explícito. Um nome fictício solto interpretado como título e seguido de pergunta de data não é tratado como uma seleção por ID bem-sucedida.

O ACK do callback e o estado `queued` comprovam recebimento/ativação da fila. O aceite da 360dialog não comprova entrega ou leitura no aparelho. A confirmação humana da consulta é registrada separadamente. **Houve zero chamadas Google reais e zero envios WhatsApp nas sondagens e testes da parte 11; no fechamento documental não houve nova suíte, chamada a provedor ou mutação.**

## Revisão documental e evidências

README, guia do agente, integração WhatsApp, conversas/falhas e índices foram alinhados ao fechamento. A documentação conserva o resultado final, as tentativas que falharam e a origem de cada prova. O planner registra a etapa 11 concluída e a etapa 12, de lembretes, como próxima. A renderização do planner comprova o documento atualizado, não uma execução adicional do sistema.

O acervo consolidado tem **120 registros: 28 imagens e 92 saídas/relatórios em JSON**. Os 118 registros anteriores foram preservados; o fechamento acrescenta `119_agente_fechamento_auditoria.json` e `120_planner_etapa_11_concluida.png`. Os originais e seus espelhos são locais; `data/` permanece fora do Git. Capturas são usadas quando demonstram estado visual relevante; testes e HTTP ficam em saídas/JSON reais, sem fabricar imagem de terminal. Consulte o [índice sanitizado](evidencias.md) e o índice privado `data/evidencias/INDICE.md`.

Este marco não altera código, testes, workflows, credenciais, bancos ou configuração de runtime. Os resultados de execução pertencem aos registros listados; a revisão de fontes/artefatos não é contada como uma nova suíte.

## Operação, privacidade e limites

Para retomar, preserve `.env`, `secrets/`, volume n8n e os bancos privados de agenda, inbox, outboxes, fila, contexto e ledgers. Inicie os serviços existentes conforme o [guia WhatsApp](whatsapp-agente.md#configuração-e-reprodução), sem duplicar instâncias ou reinstalar o fluxo a cada reinício. Um túnel temporário encerrado pode exigir nova origem e novo cadastro do callback. A fila/outbox dinâmicas são separadas das bases da resposta fixa histórica.

Credenciais, callback secreto, destinatário, IDs privados e conversas reais não entram em exemplos, prints, logs públicos ou exports. Os hashes são pseudônimos, não dados anônimos. Remoção lógica não apaga páginas SQLite, journals ou backups de forma forense. Resultados incertos podem conservar dados para conferência; apagá-los perde a memória de repetição.

Não há transação distribuída entre Google e WhatsApp, garantia geral de exatamente uma vez ou retry automático após resultado desconhecido. Agenda alterada externamente e resposta perdida exigem conferência. Fixtures comprovam as regras para os planos exercitados; as sondagens não medem a qualidade geral do Gemini. Datas relativas, interpretações sem vínculo local suficiente e primeira mensagem sem horário/duração podem exigir prévia completa. Uma duração não informada não deve ser inventada.

O canal continua restrito ao sandbox e destinatário privado. O caminho secreto e o filtro de remetente não são assinatura criptográfica da Meta. O provisionamento original do número Meta permanece pendente; os ensaios anteriores de imagem e `disclaimer/en` não comprovam templates personalizados ou lembretes fora da janela de atendimento.

## Próxima etapa e pendências do projeto

A **etapa 12 é a implementação dos lembretes**. Persistência, disparo, identidade dos envios, recuperação e política do canal ainda precisam ser definidos e verificados nesse escopo. O fechamento do agente não implementa lembretes nem confirma compatibilidade do sandbox com os envios futuros.

Continuam pendentes a validação de mutações reais pela conversa WhatsApp, o provisionamento Meta original quando necessário, templates próprios e implantação de produção. Briefings, cards contextualizados por IA, painel web, testes completos do produto e vídeo seguem nas etapas posteriores do planner. A etapa 11 está encerrada no escopo e nas fronteiras de prova descritos acima.
