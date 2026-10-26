# Regras dos lembretes — etapa 12

**Etapa 12 concluída em 26/10/2026 no escopo de teste.** Regras definidas na parte 1, implementadas nas partes 3–8 e verificadas no conjunto na parte 9 (1.956 Python/981 JavaScript). Padrão de **1 hora e 15 minutos antes do início**, com **24 horas opcional** e intervalos ajustáveis. Este documento preserva a decisão original; o [fechamento](fechamento-etapa-12.md) reúne o estado final, a reprodução e os limites do sandbox. As continuações ao final são registros históricos de cada parte.

## Requisito e decisão de produto

O enunciado original `Desafio_Genius_Agenda_AI_Auto_Genius_BRANDED.docx`, módulo 4, diz: “Enviar alertas antes dos eventos; por exemplo, 24 horas, 1 hora e 15 minutos antes.” Também exige configurar os intervalos, atualizar/cancelar avisos quando eventos mudarem, evitar duplicações, respeitar o fuso do calendário e disparar sem depender de uma conversa ativa.

Logo, **30/5 não é uma exigência do recrutador**: era a sugestão anterior do planner. O usuário autorizou escolher os intervalos. Adotamos 60/15 para alinhar o exemplo do desafio e dar um aviso de preparação e outro de proximidade. O aviso de 24h fica desligado por padrão para manter dois avisos; pode ser habilitado. Essa preferência não altera o que é obrigatório: automação configurável, persistente e confiável.

## Intervalos e configuração

| Tipo estável | Padrão | Uso |
|---|---|---|
| `day_before` | Desligado; sugestão ao ativar: 1440 min | Preparação no dia anterior |
| `preparation` | 60 min | Organizar material e disponibilidade |
| `imminent` | 15 min | Preparar a entrada na chamada ou o início do compromisso |

A primeira versão tem até três tipos estáveis. Cada um aceita um inteiro de **1 a 10080 minutos** (sete dias) ou desligado; booleanos, decimais, strings numéricas, zero e negativos não são valores válidos do contrato JSON. Intervalos ativos devem ser únicos e manter a ordem `day_before > preparation > imminent`. É permitido desativar qualquer aviso ou todos. O limite de sete dias e os três tipos são decisões desta implementação, não exigências do enunciado.

A configuração inicial é explícita e única para a agenda e o destinatário autorizados. Não há mudança automática pelo LLM, interpretação livre de uma mensagem ou override por evento nesta primeira versão. A [parte 3](persistencia-lembretes.md) implementou sua persistência explícita, ainda sem rota ou configuração pelo LLM; o [JSON de contrato](../examples/reminders/rules-contract.json) descreve a decisão, não é configuração já carregada pela aplicação.

## Relógio e fusos

- Usar início e relógio com timezone; comparar e persistir instantes em UTC. `due_utc = start_utc - offset_minutes`.
- Exibir data/hora no **fuso IANA da agenda selecionada**, sem usar o fuso do Windows ou o último fuso pedido no chat. A agenda de teste atual usa `America/Recife`; esse valor não substitui a leitura/validação da agenda numa instalação diferente.
- Antecedência significa minutos reais decorridos. Em mudança de horário de verão, calcular em UTC e depois converter para apresentação; não fabricar um horário local inexistente.
- Fuso/instante ausente, inválido ou incoerente bloqueia a preparação/envio até correção. O LLM não decide quando um aviso vence.

Exemplo: entrevista às **18h em Recife** gera avisos às **17h e 17h45**, no mesmo dia. Com 1h/10min, o segundo seria às 17h50. O aviso opcional de 24h seria às 18h do dia anterior.

## Eventos elegíveis

Somente compromissos com horário, início futuro e status `confirmed`, na agenda explicitamente permitida. Dia inteiro, `tentative`, cancelados e eventos já iniciados não geram avisos nesta versão. Recorrências podem usar somente ocorrências expandidas com ID próprio; não criar alerta para o recurso da série ou interpretar sua regra por conta própria. O conector atual já lista ocorrências expandidas; a geração e a sincronização validam os dados necessários à reconciliação.

O domínio fica nos serviços Python. O n8n aciona uma verificação periódica; o SQLite mantém o histórico autoritativo, e o disparo não depende de uma mensagem do usuário naquele momento. A permissão de transporte continua condicionada ao canal. Serviços locais parados não enviam notificações; persistência permite retomar e classificar o que venceu, sem prometer execução com o computador desligado.

## Atrasos, reinício e evento criado em cima da hora

O n8n está configurado para verificar a cada **60 segundos**, com tolerância de **dois minutos** por aviso; o intervalo configurado não garante latência do motor ou dos provedores. A janela é `due_utc <= now_utc < min(due_utc + 120 segundos, start_utc)`. No limite final, o aviso já venceu; nunca iniciar envio no início ou depois do evento.

| Situação | Comportamento definido |
|---|---|
| Ainda não chegou o horário do aviso | Manter pendente |
| Venceu e está dentro da janela de dois minutos | Pode reservar o envio após revalidar evento e canal |
| A janela terminou | Registrar como vencido; não enviar retroativamente |
| Sistema reinicia depois de perder um aviso | Preservar histórico, vencer o perdido e manter os avisos futuros; sem rajada acumulada |
| Evento criado ou reagendado depois do horário de um aviso | Aplicar a mesma janela; não improvisar um aviso imediato fora dela |
| Evento já começou ou terminou | Não enviar aviso prévio |

Exemplo: entrevista às 18h criada às 17h20 não recebe o aviso das 17h; o das 17h45 continua previsto. Criada às 17h46 pode receber o último aviso dentro da tolerância; às 17h47 ele já venceu. A tolerância é uma decisão de produto implementada, sem garantia de latência de entrega do provedor.

## Alterações e cancelamentos

Antes de reservar/enviar, reler o evento exato na agenda permitida. Conferir destinatário, status, início e elegibilidade atuais. Atualizar o relógio depois da leitura do Google e imediatamente antes do transporte; uma validação antiga não autoriza enviar depois da janela. Existe uma corrida residual entre a última leitura e o envio: não há transação atômica entre Google e WhatsApp. Uma ocorrência fora da faixa de listagem ou uma falha do Google não comprova exclusão; cancelamento/ausência precisa de evidência específica do conector, como recurso cancelado ou ausência inequívoca.

- **Início alterado:** invalidar pendentes do início anterior e planejar o novo início. Aceites e incertezas anteriores permanecem no histórico.
- **Título, descrição ou local alterados:** atualizar o texto ainda não enviado; não renovar um aviso já aceito.
- **Fuso de exibição alterado, mesmo instante UTC:** não criar uma segunda identidade de aviso; atualizar a apresentação do pendente.
- **Evento cancelado/removido:** cancelar avisos pendentes após confirmar esse estado. Restaurar o evento não apaga o histórico de envios.
- **Retornar ao mesmo início antigo:** reutilizar a identidade anterior; não reenviar um tipo já aceito ou incerto.

Desativar um tipo cancela seus pendentes, sem apagar histórico. Trocar um intervalo recalcula somente avisos ainda não enviados. Se o novo horário já venceu, registrar vencimento; se é futuro, atualizar o pendente. Alterar, desligar ou religar a configuração não apaga a prova de aceite/incerteza nem força reenvio do mesmo tipo para o mesmo início.

## Identidade, estados e falhas

A identidade semântica implementada combina **agenda + ID exato da ocorrência + início UTC + tipo estável + vínculo do destinatário autorizado**. Offset, prazo calculado, título, ETag e revisão da configuração não criam outra identidade. Não usar o ID da execução do n8n, o texto do LLM ou uma nova tentativa como identidade de negócio. A [parte 3](persistencia-lembretes.md) implementou o schema 1 próprio, com configuração, ledger, histórico e observações do canal.

Reservar a identidade antes do transporte, usando persistência e exclusão mútua. Duas verificações concorrentes ou uma resposta repetida não podem reservar outro envio do mesmo aviso. Aceite confirma somente a aceitação do provedor; entrega/leitura e confirmação humana são fatos separados.

Erro de leitura impede o envio, sem marcar evento ausente. Uma falha com ausência de envio/aceite comprovada só pode ser repetida com a mesma identidade, dentro da janela e após nova validação. Timeout, interrupção ou resposta perdida após possível envio permanece **incerto**: não gerar novo ID, expirar o bloqueio ou reenviar cegamente. Outros tipos de aviso são operações distintas; não converter incerteza do aviso anterior em prova de aceite ou de falha.

O destinatário continua fixo na política privada. Uma troca de configuração não redireciona mensagens pendentes de outra identidade. Mensagens devem usar dados mínimos atuais: título, data, início e fuso; não copiar descrição privada, telefones ou segredos. Se houver atraso, não afirmar “falta exatamente uma hora”; mostrar o horário atual do compromisso.

## Canal e alcance desta parte

A [revisão da parte 2](canal-lembretes.md) definiu texto determinístico dentro de janela válida e bloqueio sem template contextual fora dela. O sandbox atual não permite criar esse template; o [controle de janela](envio-lembretes.md) foi implementado na parte 7 e bloqueia envios quando não há permissão válida. Não confundir independência da conversa com permissão irrestrita de envio. Aceite, entrega e incerteza continuam fatos distintos.

## Sequência da etapa 12

- [x] 1. Definir regras, intervalos, fusos e comportamento de atraso.
- [x] 2. Conferir o canal e o formato permitido para disparos — contrato e limites revisados; gate aplicado no envio da parte 7.
- [x] 3. Persistir configuração, estados/histórico e observações do canal — SQLite implementado e integrado às partes seguintes.
- [x] 4. Consultar a agenda e gerar os próximos avisos — lote transacional; comando manual e ciclo periódico.
- [x] 5. Sincronizar alterações e cancelamentos — consulta exata, lote atômico e estados protegidos; comando e ciclo periódico.
- [x] 6. Configurar a verificação periódica no n8n — publicada a cada minuto; ciclo final recover → tick → dispatch com disparo natural confirmado.
- [x] 7. Enviar com reserva persistente e sem duplicações — dupla leitura, gate do canal e proteção de aceites/incertezas; runtime sem avisos devidos no ensaio.
- [x] 8. Tratar falhas e recuperar somente tentativas seguras — lock exclusivo, lote/CAS, expiração e incerteza preservada; sem retry de transporte.
- [x] 9. Testar horários, concorrência, mudanças, falhas e reinícios — SDK, SQLite, TCP/processos e grafo exportado; provedores fictícios e runtime conferido separadamente.
- [x] 10. Revisar entregas, README, evidências e PDF — fontes/logs preservados, 139 registros e planner renderizado; registros 138/139.

## Verificação documental original — parte 1

Na parte 1, o contrato e os exemplos eram critérios de implementação, sem scheduler implementado naquela data. A conferência desta parte verifica exemplos de horários/configuração e a consistência dos documentos. Não acrescenta testes à suíte histórica de 1.561, não chama Google/Gemini/360dialog e não altera banco, `.env`, API ou workflows. O registro 121 e a renderização 122 do planner ficam no [índice de evidências](evidencias.md).


**Continuação — parte 3:** a [persistência](persistencia-lembretes.md) agora possui testes de aplicação com SQLite real. O escopo documental dos registros 121/123 acima permanece histórico; o novo resultado e a inicialização vazia ficam nos registros 124/125. Consultar a agenda, sincronizar mudanças e aplicar o gate antes de enviar continuam pendentes.


**Continuação — parte 4:** a [geração manual](geracao-lembretes.md) resolve ID/fuso reais da agenda, consulta oito dias e grava um lote atômico por revisão. Não cancela por ausência nem sincroniza inícios antigos nesta parte. Registros 126/127; próxima parte: sincronização.


**Continuação — parte 5:** a [sincronização manual](sincronizacao-lembretes.md) cancela inícios antigos e avisos de eventos removidos/inelegíveis após leitura exata. Recusa falhas e mudanças concorrentes, sem liberar envio potencial. Registros 128/129; próxima parte: verificação periódica no n8n.


**Continuação — parte 6:** a [verificação periódica](lembretes-n8n.md) chama uma rota autenticada a cada minuto, sincronizando antes de gerar, com exclusão entre processos e contagens mínimas. Preflight real e disparo natural do n8n foram conferidos separadamente; nenhum envio ou escrita de evento Google. Registros 130/131. Próxima parte: revalidação final, gate do canal e reserva/envio persistentes.


**Continuação — parte 7:** o [envio protegido](envio-lembretes.md) está ligado ao n8n por dispatch autenticado, após sincronização/geração. O callback novo registra observações imutáveis do canal; envio exige perfil, evento, janela e prazo atuais. 1.874 testes Python e 853 JavaScript aprovados; envio único e reinício verificados com provedores fictícios. Rotas e disparo natural reais conferidos sem avisos devidos, sem afirmar entrega humana. Registros 132/133; próxima parte: recuperação seletiva de falhas.


**Continuação — parte 8:** a [recuperação segura](recuperacao-lembretes.md) roda antes de tick/dispatch no n8n. Só recupera reserva comprovadamente anterior ao transporte sob a trava exclusiva, com perfil/relógio/histórico atuais; `sending` abandonado vira `uncertain`, e aceites/incertezas anteriores permanecem intactos. Testes com interrupção real e runtime sem avisos a recuperar documentados nos registros 134/135. Próxima parte: testar o conjunto.


**Continuação — parte 9:** os [testes do conjunto](testes-conjunto-lembretes.md) passaram com 1.956 testes Python e 981 JavaScript, sem casos ignorados. Novos percursos integram relógio/SDK/ledger, HTTP e reinícios; 30 envelopes TCP foram validados pelo código do grafo exportado. Código funcional, cinco exports e serviços reais preservados. Registros 136/137; próxima parte: fechar a etapa.


**Fechamento — parte 10 (26/10/2026):** dez partes revisadas e encerradas no escopo de teste. A auditoria 138 preserva as fontes, logs e cinco exports da suíte aprovada; a captura 139 mostra o planner atualizado e a etapa 13 como próxima. Não houve nova execução da suíte, restart, publicação, mudança de configuração privada ou envio de lembrete neste fechamento. O [guia final](fechamento-etapa-12.md) distingue implementação, ensaios isolados e validação real pendente.
