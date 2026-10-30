# Agenda pelo painel — consultas e CRUD com confirmação

Fase 4 da etapa 15 concluída em 28/10/2026. O painel em <http://127.0.0.1:8000/painel/#agenda> permite consultar por dia, semana e mês e criar, editar, reagendar ou excluir pelo formulário. O WhatsApp permanece disponível: os dois canais usam a mesma agenda configurada e os mesmos serviços de domínio.

## Como usar

Depois de [autorizar o navegador local](painel-web-api.md#abrir-e-autorizar-o-navegador):

1. Na Agenda, escolha **Dia**, **Semana** ou **Mês**. A pesquisa filtra somente o período carregado.
2. Clique em **Novo compromisso**, ou abra um evento para **Editar**, **Reagendar** ou **Excluir**. A seleção conserva o identificador; não é preciso copiar IDs ou redigitar os horários antigos.
3. Preencha os dados e peça a prévia. O sistema confere datas, fuso, intervalo, evento e conflitos antes de oferecer a confirmação.
4. Confira o evento, os dados atuais e os novos valores. **Confirmar** executa somente essa proposta; **Cancelar** impede a execução da prévia pendente.
5. Aguarde o resultado confirmado. Se houver falha de conexão ou resultado incerto, use a consulta de estado oferecida pela interface; não repita a gravação como uma nova operação.

A criação aceita título, início, término e descrição opcional. O contrato existente não aceita local nessa operação: ele pode ser acrescentado depois em **Editar**, que também altera título e descrição. O reagendamento pede somente o novo início e término. Eventos de dia inteiro podem ser editados e excluídos; o formulário atual de reagendamento aceita apenas eventos com horário.

Datas e horas seguem o fuso da agenda vinculado no servidor. Horários civis inexistentes ou ambíguos não são aceitos silenciosamente. Conflitos bloqueiam a operação e mostram os compromissos envolvidos. Encontrar horários livres, briefing, checklist e resumos pelo painel continuam no escopo da fase 5.

## Prévia, aprovação e execução

O navegador não grava diretamente pelo endpoint genérico de eventos. O formulário usa uma camada protegida por sessão, origem e CSRF:

| Método e rota | Responsabilidade |
|---|---|
| `POST /painel/api/agenda/preview` | Validar o formulário e registrar uma proposta vinculada ao navegador |
| `GET /painel/api/agenda/{operation_id}` | Consultar o resultado persistido, sem executar outra tentativa |
| `POST /painel/api/agenda/{operation_id}/confirm` | Aprovar e executar a proposta exata; recebe somente `{}` |
| `POST /painel/api/agenda/{operation_id}/cancel` | Cancelar a prévia pendente; recebe somente `{}` |

A prévia recebe `request_id` UUID estável, `action` (`create`, `edit`, `reschedule` ou `delete`), o `event_id` selecionado quando aplicável e o objeto `event` com os campos permitidos. Campos extras, valores inválidos, JSON repetido ou excessivo e identificadores malformados são rejeitados. Os detalhes da sessão permanecem descritos no [guia de acesso](painel-web-api.md).

O servidor conserva a proposta, o evento, a agenda e sua versão em uma conversa privada criada para a operação. A confirmação normal vale cinco minutos e é limitada também pelo prazo do registro da operação. A aprovação relê o estado; mudanças externas, troca da agenda, expiração ou consumo da confirmação exigem uma nova prévia. O navegador não pode substituir os dados durante a confirmação.

O registro privado `data/panel-operations.sqlite3` vincula `request_id`, operação e sessão. Repetir o mesmo pedido retorna a operação existente; trocar os dados usando o mesmo `request_id` é rejeitado. Uma transição atômica permite somente uma confirmação concorrente, e cancelamento não pode vencer junto com ela. Outro navegador não lê nem confirma essa operação.

As escritas usam os serviços e os ledgers existentes de criação, edição, reagendamento e exclusão do agente. Eles conservam validações, controle de versão, conflito, idempotência e conferência posterior. Uma falha depois do início da execução pode produzir resultado **não confirmado**; isso bloqueia repetição automática. Uma operação interrompida no estado de confirmação também não é retomada como uma nova gravação.

Abrir, navegar ou atualizar a tela continua sem alterar eventos, chamar Gemini, disparar lembretes ou enviar mensagens WhatsApp. A prévia consulta o calendário e grava somente seus registros locais. A confirmação explícita pode alterar o Google Calendar. Os GETs de lembretes continuam somente de leitura; o fluxo periódico existente sincroniza seus registros separadamente.

## Validação concluída e limites da evidência

- **2.665 testes Python** na suíte completa e **39 testes Node** do frontend aprovados; conjuntos separados.
- Testes cobrem propriedade da sessão, CSRF, duplicidade, propostas alteradas ou expiradas, confirmações concorrentes, cancelamento, conflitos, versões externas e resultados incertos.
- No navegador, o aplicativo e seus stores reais foram ligados a um calendário isolado. Criação, edição, cancelamento de prévia, reagendamento e exclusão passaram, além das consultas de semana/mês e da apresentação no celular. Esse cenário exercita o fluxo da interface ao serviço e à persistência, sem gravar no Google real.
- Na API instalada com Google real, o ensaio criou e cancelou somente uma prévia de criação. A agenda antes/depois permaneceu igual; os SHA-256 dos ledgers de execução e lembretes ficaram inalterados. Não houve confirmação executada, escrita Google ou mensagem WhatsApp nesse ensaio.

Os resultados não representam um novo CRUD completo gravado no Google nem prontidão permanente dos provedores. O ensaio real de CRUD pelo WhatsApp anterior continua sendo uma evidência distinta. O painel permanece local, para um proprietário e uma agenda; hospedagem pública, autenticação multiusuário e isolamento entre clientes exigem outra evolução.

[Telas e próximas fases](painel-web.md) · [Acesso, rotas e segurança](painel-web-api.md) · [Índice público de evidências](evidencias.md).
