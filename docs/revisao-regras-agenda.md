# Regras da agenda — revisão inicial

**Etapa 08, parte 1 concluída em 22/10/2026.** Revisão dos serviços, comandos, conector Google, banco e testes existentes. Esta parte preserva o diagnóstico anterior às correções; a evolução das partes seguintes está registrada abaixo.

**Atualização de 22/10/2026 — parte 2 concluída:** as lacunas de validação de fuso/horários e tipos na criação foram corrigidas com uma regra compartilhada com o reagendamento. A suíte passou a 155 testes. Consulte [datas, duração e fusos](datas-fusos.md). As observações e o registro 028 abaixo preservam o diagnóstico anterior à correção.

**Parte 3 concluída em 22/10/2026:** o serviço de preparação passou a diferenciar dados ausentes, dados inválidos e dúvidas na seleção, com perguntas estruturadas para as cinco ações e sem executar escrita. A suíte passou a 181 testes. Consulte [ambiguidades e esclarecimentos](esclarecimentos.md). A continuação persistida da conversa permanece nas etapas de integração/IA.

**Parte 4 concluída em 22/10/2026:** criação e reagendamento passaram a conferir ocupação, com regra independente da IA e trava SQLite entre consulta e gravação local. A suíte passou a 217 testes; criação e reagendamento conflitantes também foram bloqueados na agenda real de teste, com registros 031 a 035. Consulte [conflitos de horário](conflitos.md). As tabelas de lacunas abaixo preservam o diagnóstico inicial.

**Parte 5 concluída em 22/10/2026:** o SQLite passou ao esquema 2, com reserva durável de criação e vínculo das operações ao conteúdo e ao ID original. Repetições após reabrir o programa e com outro identificador reutilizaram o evento real. A suíte passou a 264 testes; registros 036 a 039. Consulte [prevenção de duplicações](duplicacoes.md).

**Parte 6 concluída em 22/10/2026:** padronizamos falhas nas CLIs, endurecemos as leituras e substituímos o transporte para evitar reenvios implícitos. Uma resposta de escrita incerta agora é conferida por ID e pode retornar `recovered: true`. A suíte passou a 351 testes; as quatro operações reais com perda local simulada da resposta foram recuperadas, registros 040 a 046. Consulte [falhas e recuperação](falhas-recuperacao.md).

**Parte 7 concluída em 22/10/2026:** acrescentamos oito testes que atravessam serviços, SDK, transporte e SQLite, simulando o Google somente na sessão HTTP. As duas rodadas completas passaram com 359 testes e zero tentativas de conexão; configuração, credenciais e banco reais foram preservados. O registro local 047 guarda a saída e os resultados. Consulte [testes do conjunto](testes-conjunto.md).

**Parte 8 e etapa 08 concluídas em 22/10/2026:** revisão final das entregas, evidências e limites, com os hashes do código conferidos contra a suíte de 359 testes aprovada. O registro local 048 documenta a auditoria de fechamento. Consulte o [fechamento da etapa](fechamento-etapa-08.md). Próxima: **etapa 09 — operações na API e fluxos n8n**.

## Proteções existentes

| Regra | Estado observado | Implementação e cobertura |
|---|---|---|
| Intervalo válido | Criação e reagendamento exigem início/fim com deslocamento UTC e fim posterior ao início | `validate_interview`, `validate_schedule_change`; testes de criação/reagendamento |
| Períodos e fusos de consulta | Dia, semana e mês respeitam limites civis, fim exclusivo e mudanças de horário de verão | `services/agenda.py`; testes diários e de períodos |
| Fuso do reagendamento | Rejeita fuso inexistente, offset incompatível e horário local inexistente; distingue hora ambígua por offset explícito | `validate_schedule_change`; testes de reagendamento |
| Identidade e ambiguidade | ID exato, busca normalizada por título e período; vários candidatos exigem esclarecimento; resultado único é relido | `services/event_selection.py`; testes de seleção |
| Agenda de destino | Exige agenda própria de teste, rejeita principal e não substitui um ID fixado ausente por outro calendário | `choose_calendar` / `resolve_calendar`; testes de seleção e das operações |
| Edição parcial | Campos omitidos são preservados; limpeza é explícita; título vazio é rejeitado | `services/event_details.py`; testes de edição |
| Alteração concorrente | PATCH e DELETE usam versão lida e `If-Match`; erro 412 interrompe a operação | Adaptador Google; testes de reagendamento, edição e exclusão |
| Exclusão com confirmação | Prévia e código vinculado a operação/agenda/ID/versão; nova leitura precisa confirmar ausência | `services/event_deletion.py`; testes de exclusão |
| Repetição da mesma operação | Criação deriva o ID de `operation_id` e confere conteúdo; edição/reagendamento iguais não escrevem; exclusão já ausente não repete o DELETE | Adaptador Google; testes de repetição e concorrência |
| Falhas após alterações | As quatro operações consultam o ID após resposta de escrita incerta; confirmam o estado esperado com `recovered` ou mantêm erro seguro; não repetem a escrita automaticamente | Adaptador Google, transporte e testes de falhas |

Os serviços continuam independentes da IA. Descrições e locais são lidos apenas nas operações que precisam deles. As consultas gerais pedem campos mínimos; registros privados permanecem fora do Git.

## Diagnóstico histórico anterior às correções

A tabela preserva as lacunas identificadas quando a suíte tinha 139 testes; elas foram atendidas nas partes 2 a 7. Seus textos e contagens descrevem aquela revisão inicial, não o estado atual do código.

| Lacuna | Consequência | Parte prevista |
|---|---|---|
| Criação apenas verifica se o texto do fuso não está vazio; não valida a zona IANA nem a coerência com o horário/offset | Um pedido inválido pode passar pela validação interna do serviço e chegar ao conector | 2 — consolidar datas e fusos |
| Validação de criação chama `.strip()` antes de verificar tipos de título, fuso e identificador da operação | Entradas malformadas podem gerar exceção sem a mensagem de validação esperada | 2 — validar as entradas do serviço |
| Perguntas por campos faltantes e seleção pendente ainda não têm um contrato comum de operação | A futura integração precisa distinguir dado inválido, dado ausente e escolha ambígua | 3 — consolidar esclarecimentos; contexto de conversa entra na etapa 11 |
| Criar/reagendar não consulta os outros eventos para decidir se o intervalo está ocupado | Duas entrevistas diferentes podem sobrepor horários | 4 — conflitos antes de gravar |
| Proteção de repetição depende de reutilizar o mesmo `operation_id` | A mesma solicitação com identificador novo gera outro ID; não há controle persistente dessas operações no SQLite | 5 — duplicações e identidade estável da solicitação |
| Falhas de transporte não recebem o mesmo tratamento em todos os comandos | `TransportError` pode escapar da CLI de criação; listagem já retorna erro controlado | 6 — padronizar falhas e resultados incertos |
| As novas regras ainda não têm os seus casos de teste | Os 139 testes atuais cobrem a base, mas não comprovam bloqueio de conflitos ou deduplicação geral | 7 — testar o conjunto ampliado |

O comando de criação atual usa uma entrevista fixa e controlada. As lacunas de fuso e tipo foram reproduzidas chamando o serviço com entradas fictícias; não foram eventos inválidos enviados ao Google.

## Diagnóstico executado

Executamos **10 verificações locais**, com funções de validação, conector injetado sem I/O e erros de transporte simulados:

- Um fuso IANA inexistente, um horário `-03:00` declarado como `UTC` e uma hora inexistente de horário de verão passaram pela validação da criação. Os mesmos três casos foram rejeitados pelo reagendamento.
- Título `None` na criação produziu `AttributeError`; título não textual na edição foi rejeitado com `ValueError`.
- `TransportError` simulado antes da conexão escapou da CLI de criação; a CLI de consulta retornou código 1 e mensagem controlada, sem JSON de sucesso.

O registro **028** do acervo é um diagnóstico local em JSON, identificado como tal. Não houve chamada real ao Google, uso de credenciais OAuth ou alteração de eventos.

Conferimos por SHA256 que os **28 arquivos Python de aplicação/testes** permanecem iguais aos da suíte anteriormente aprovada com **139 testes**. A suíte não foi repetida porque a revisão não alterou comportamento. As 10 verificações de diagnóstico não foram adicionadas à contagem da suíte.

## Critérios adotados nas partes 2 a 7

1. Reaproveitar a validação de intervalo/fuso de forma consistente na criação e no reagendamento, rejeitando entradas inválidas antes de abrir a conexão.
2. Manter `selected`, `needs_clarification` e `not_found` distintos; uma dúvida não deve selecionar ou modificar um evento automaticamente.
3. Na verificação de conflito, considerar limites exclusivos, madrugada, dia inteiro e ocorrências; ignorar o próprio ID ao reagendar. Falha de consulta não significa horário livre. A proteção por ETag do evento não bloqueia outra execução de criar um evento diferente no mesmo intervalo; essa concorrência precisa ser considerada.
4. Diferenciar repetição da mesma solicitação de outro compromisso legítimo com título parecido. Definir a identidade estável da operação antes de conectar a entrada de mensagens; a deduplicação de mensagens Meta será feita na etapa 10.
5. Padronizar erros, manter escritas sem repetição automática cega e verificar o estado antes de repetir uma operação incerta. Revisar também respostas incompletas do provedor.
6. Acrescentar testes das regras novas e atualizar documentação/evidências ao fechar cada parte.

O SQLite agora contém `app_metadata`, `creation_requests` e `creation_operations`. A parte 4 introduziu a trava `BEGIN IMMEDIATE`; a parte 5 acrescentou a reserva durável antes do POST e a confirmação pela conexão da própria trava. Mensagens, confirmações por conversa e estados de lembretes ainda não têm tabelas. Escritas externas no Google não participam da trava local. Não há integração de operações com HTTP, n8n, WhatsApp ou IA nesta parte.

**Etapa 08 concluída em 22/10/2026.** As oito partes foram encerradas, preservando o histórico e os limites de cada verificação. Próxima: **etapa 09 — operações na API e fluxos n8n**. A etapa 07 permanece concluída.
