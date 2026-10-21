# Etapa 07 — consultar e editar

**Concluída em 21/10/2026**, no escopo de operações por comandos Python sobre a agenda própria `Genius Testes`.

## Entregas conferidas

| Parte | Entrega | Verificação registrada |
|---|---|---|
| 1 | [Consulta por dia](consulta-por-dia.md) | Saída real e comparação com a agenda, registros 007/008 |
| 2 | [Consulta por semana e mês](consulta-semana-mes.md) | Períodos e resultados reais, registros 009 a 011 |
| 3 | [Identificação por ID ou título](identificacao-eventos.md) | Resultados reais 012 a 014; ambiguidade simulada no 015 |
| 4 | [Reagendamento](reagendamento.md) | Mesmo ID, novos horários, confirmação posterior e interface, 016 a 019 |
| 5 | [Edição de título, descrição e local](edicao-informacoes.md) | Campos atualizados e horário preservado, 020 a 023 |
| 6 | [Exclusão com confirmação](exclusao-confirmada.md) | Prévia, execução, ausência posterior e interface, 024 a 027 |
| 7 | Revisão do conjunto | Código testado, cobertura, guias, links e acervo conferidos; planner atualizado |

A criação foi validada na etapa 06. A etapa 07 completou consulta, alteração e exclusão sobre o mesmo evento, sem criar compromissos adicionais para as demonstrações.

## Testes e revisão final

A execução mais recente da suíte completa aprovou **139 testes**: SQLite (4), seleção da agenda (5), criação (15), consulta diária (16), consulta semanal/mensal (14), identificação (18), reagendamento (20), edição (24) e exclusão (23).

No fechamento, conferimos que os arquivos atuais de aplicação e testes correspondem por SHA256 aos arquivos da suíte aprovada. Nenhuma mudança de comportamento foi necessária nesta revisão; os testes não foram repetidos após as alterações exclusivamente documentais. A revisão independente não encontrou bloqueadores no escopo.

Os casos cobrem limites de período e fuso, paginação, candidatos ambíguos, ausência de resultado, preservação dos campos não solicitados, proteção concorrente por versão, confirmação obrigatória de exclusão e falhas com resultado incerto. Esses testes usam clientes simulados; as demonstrações reais e as capturas têm registros separados.

O [acervo de evidências](evidencias.md) contém **27 registros**: 11 imagens, 15 resultados de operações reais e uma simulação identificada. As cópias privadas e o índice foram conferidos; os arquivos permanecem fora do Git.

## Estado final da demonstração

A entrevista fictícia foi criada para **22/10, 15h–15h30**, reagendada para **23/10, 16h–16h30**, renomeada para **Entrevista técnica — Empresa Exemplo (teste)** e excluída com confirmação. A última verificação real não encontrou o evento pelo ID nem na consulta de 23/10. A visão diária confirmou a ausência.

As contagens de testes e os resultados individuais dos guias preservam a evolução de cada parte. A entrevista já foi removida: exemplos históricos não indicam um evento ainda disponível. Uma nova demonstração de criação/alteração deve preparar outro evento de teste com ID próprio.

## Limites e próxima etapa

As operações de agenda ainda não estão expostas por HTTP nem conectadas ao n8n ou WhatsApp. Não há interpretação por IA, confirmação persistida por conversa ou lembretes automáticos. A prévia da CLI de exclusão identifica a versão conferida; o controle de consentimento por usuário será implementado na integração de conversa.

O reagendamento atende eventos comuns com horário e ocorrências específicas; dia inteiro e séries completas exigem outro fluxo. Edição e exclusão aceitam eventos de dia inteiro, mas rejeitam séries completas e tipos especiais.

**Próxima etapa: 08 — regras da agenda.** Consolidar as validações existentes, detectar conflitos e evitar duplicações. O encerramento da etapa 07 não representa a conclusão dessas regras nem do atendimento pelo WhatsApp.
