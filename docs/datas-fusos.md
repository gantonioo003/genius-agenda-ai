# Datas, duração e fusos — etapa 08, parte 2

**Concluída e validada em 22/10/2026.**

Criação e reagendamento compartilham a validação de horários em `app/services/time_rules.py`, sem dependência de Google, n8n ou IA. As consultas usam a mesma validação de intervalo absoluto e de nome do fuso, preservando as regras próprias dos períodos civis.

## Regras aplicadas

- Início e fim devem ser objetos `datetime` com deslocamento UTC. Uma data sem horário, um texto ainda não interpretado ou um horário sem offset é rejeitado.
- O nome do fuso deve ser um texto válido disponível na base `zoneinfo`, como `America/Recife`. Valores vazios, tipos incorretos e nomes desconhecidos são rejeitados.
- O fim precisa ser posterior ao início **em UTC**. A duração resulta da diferença entre esses instantes; zero e duração negativa são rejeitados.
- Na criação e no reagendamento, hora local e offset precisam corresponder ao fuso declarado. O serviço rejeita divergências em vez de adivinhar qual horário o usuário pretendia.
- Horários locais inexistentes em uma mudança de horário de verão são rejeitados. Quando uma hora ocorre duas vezes, um offset explícito válido identifica a ocorrência desejada.
- Conversões que ultrapassam os limites representáveis de `datetime` retornam `ValueError`, com mensagem de validação, antes de qualquer conexão.
- A criação também valida os tipos de título, descrição e identificador da operação. Título e identificador precisam conter texto; a descrição pode ser vazia. O identificador estável não é normalizado nem trocado por outro.

Não foi acrescentado limite comercial de duração mínima/máxima nem bloqueio geral de datas passadas. A regra desta parte exige duração positiva e horários coerentes. A comparação usa a duração real, incluindo mudanças de offset.

## Exemplos de duração

| Início | Fim | Fuso | Duração real |
|---|---|---|---|
| 23/10/2026, 16h, `-03:00` | 23/10/2026, 16h30, `-03:00` | America/Recife | 30 minutos |
| 22/10/2026, 23h45, `-03:00` | 23/10/2026, 00h15, `-03:00` | America/Recife | 30 minutos |
| 08/03/2026, 01h30, `-05:00` | 08/03/2026, 03h30, `-04:00` | America/New_York | 1 hora |
| 01/11/2026, 01h45, `-04:00` | 01/11/2026, 01h15, `-05:00` | America/New_York | 30 minutos |

Os dois últimos casos são entradas fictícias para verificar horário de verão. O relógio civil, sozinho, não determina a duração.

## Implementação e compatibilidade

| Função | Responsabilidade |
|---|---|
| `load_zone` | Validar tipo/nome do fuso e carregar `ZoneInfo` |
| `validate_interval` | Validar instantes com offset, ordenar em UTC e retornar `timedelta` positivo |
| `validate_event_interval` | Aplicar também a correspondência entre hora civil, offset e zona do agendamento |

`validate_interview` e `validate_schedule_change` chamam a mesma regra de agendamento. Os serviços validam antes de delegar; o adaptador Google repete a validação antes de selecionar a agenda. O comando de criação valida a prévia antes de abrir o cliente; o de reagendamento faz o mesmo para entradas interpretadas pela CLI.

`agenda.load_zone` continua disponível como importação da função compartilhada, preservando os demais módulos. `validate_period` aceita limites absolutos em UTC mesmo quando o fuso de exibição é outro: período de consulta e horário civil de um novo agendamento têm responsabilidades distintas. Dia, semana e mês continuam sendo delimitados pelas meias-noites locais.

## Verificação e evidências

Criamos **16 testes novos** em `tests/test_time_rules.py`. A suíte completa passou com **155 testes**, na preparação isolada e no ambiente do repositório. A revisão independente não encontrou bloqueadores no escopo.

Os casos verificam duração em UTC entre offsets e datas civis diferentes, dias de 23/25 horas, horas repetidas, horários inexistentes, offsets fracionários, extremos de datas, tipos inválidos e rejeição antes do gateway ou seleção da agenda. A mesma matriz de horários é aplicada à criação e ao reagendamento. Há verificações de compatibilidade da consulta por intervalo absoluto e de preservação exata do identificador estável da criação.

Depois de aplicar o código, executamos **15 exemplos locais**: quatro aceitos com duração conferida e onze rejeitados com erro controlado. Eles estão no registro **029** de `data/evidencias/INDICE.md`. Esse JSON documenta execuções do código com entradas fictícias, sem chamada real ao Google ou alteração de eventos; os exemplos não são adicionados à contagem da suíte.

As lacunas de fuso inválido, offset incoerente e hora inexistente aceitas pela criação no diagnóstico 028 passaram a ser rejeitadas também na criação. Título, descrição e identificador com tipos incorretos retornam erros de validação.

O diagnóstico inicial [das regras existentes](revisao-regras-agenda.md) permanece como histórico. Esclarecimentos, conflitos e identidade persistente das criações foram acrescentados nas partes 3 a 5; o tratamento das falhas e a recuperação por leitura foram acrescentados na parte 6.

As partes 3, [ambiguidades e esclarecimentos](esclarecimentos.md), 4, [conflitos de horário](conflitos.md), 5, [prevenção de duplicações](duplicacoes.md), 6, [falhas e recuperação](falhas-recuperacao.md), e 7, [testes do conjunto](testes-conjunto.md), também foram concluídas. **Etapa 08 concluída em 22/10/2026**, com 359 testes aprovados e código conferido por hashes. Consulte o [fechamento](fechamento-etapa-08.md). Próxima: **etapa 09 — operações na API e fluxos n8n**.
