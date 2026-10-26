# Canal dos lembretes — etapa 12, parte 2

**Revisão do canal concluída em 25/10/2026. Persistência adicionada na parte 3; ingresso, controle de janela e envio implementados na [parte 7](envio-lembretes.md), em 26/10/2026. O texto abaixo preserva o contrato e as lacunas observadas na revisão original.** A primeira versão usará texto curto com título e início no fuso da agenda. O sandbox permite demonstrar esse texto dentro de uma janela válida, mas não oferece um template contextual para garantir lembretes fora dela. A [parte 3](persistencia-lembretes.md) implementou o armazenamento desses dados, ainda sem ligar o callback a ele; a parte 7 deverá aplicar a decisão antes do transporte.

## Regras atuais do provedor

| Tema | Regra conferida | Fonte |
|---|---|---|
| Janela de atendimento | Mensagem do usuário abre/renova 24h; fora dela, somente template aprovado para sua finalidade. Envio do bot e status de entrega/leitura não constituem mensagem do usuário. | [Política WhatsApp](https://business.whatsapp.com/policy), [mensageria 360dialog](https://docs.360dialog.com/docs/messaging/overview) |
| Permissão | Contato autorizado e permissão para receber mensagens são necessários; respeitar a desistência. Uma allowlist técnica não substitui essa permissão. | [Política WhatsApp](https://business.whatsapp.com/policy) |
| Destino e transporte de teste | Chave vinculada a um único destinatário; endpoint sandbox `/v1/messages` e callback HTTPS. A conta atual usa telefone, não BSUID. | [Sandbox](https://docs.360dialog.com/docs/get-started/sandbox) |
| Cota | 200 requisições à API de mensagens, inclusive rejeitadas; esgotamento retorna 429. Saldo restante não foi consultado nem deduzido dos logs locais. | [Sandbox](https://docs.360dialog.com/docs/get-started/sandbox) |
| Templates de teste | Somente três modelos predefinidos; não há criação/edição. Nenhum deles é um lembrete contextual aprovado para este projeto. | [Sandbox](https://docs.360dialog.com/docs/get-started/sandbox) |
| Mídia | Sandbox não permite upload/recuperação por media ID. Há ensaio anterior de imagem por URL; a primeira versão de lembretes usará texto. | [Sandbox](https://docs.360dialog.com/docs/get-started/sandbox), [ensaio local](whatsapp-midias-templates.md) |

Inventário documentado: `disclaimer` tem texto fixo; `first_welcome_messsage` personaliza o nome do destinatário; `interactive_template_sandbox` demonstra botões. O sender do projeto só implementa `disclaimer/en`, sem parâmetros. Não adaptar nomes/variáveis desses modelos para disfarçar um lembrete. A conta de produção precisará disponibilizar um template adequado e aprovado; isso não foi criado nem contratado nesta revisão.

A 360dialog também documenta ligações como abertura da janela, mas a primeira versão local só reconhecerá **mensagens de texto recebidas e validadas**: o ingresso atual não fornece observação confiável de chamadas. Essa escolha é conservadora e pode bloquear texto que o provedor aceitaria. As regras gerais serão respeitadas no sandbox, que não documenta dispensa da janela.

## Dois relógios diferentes

- **Antecedência do evento:** 60/15 minutos por padrão; 1440 minutos opcional, conforme [regras dos lembretes](regras-lembretes.md).
- **Permissão do canal:** até 24h após a última mensagem elegível do usuário autorizado. Um evento para amanhã não abre a janela; o aviso opcional de 24h também não a renova.

O scheduler poderá funcionar sem conversa em andamento. Para garantir mensagens proativas quando a janela estiver fechada, entretanto, será necessário um canal com template contextual aprovado. **O requisito de avisar sem o usuário conversar fica parcialmente demonstrável no sandbox, não garantido em qualquer horário.** Não contornar isso pedindo mensagens periódicas, enviando `disclaimer` ou trocando para imagem/texto após falha.

## Formato escolhido

Texto determinístico, sem geração pelo LLM, com dados relidos do evento exato. Exemplo fictício:

```text
Lembrete da agenda
Entrevista técnica
Início: 01/11/2026 às 18:00 (America/Recife)
```

O mesmo formato serve aos três tipos estáveis; não afirmar “falta exatamente uma hora”, “em 15 minutos” ou “amanhã”, pois intervalo e atraso podem variar. Mostrar data e hora absolutas, sempre com o fuso IANA da agenda selecionada. Não incluir descrição, participantes, telefone, IDs internos, link de reunião ou mídia por padrão.

Decisões de produto para a futura implementação:

- Título: normalização Unicode NFC; converter cada caractere de whitespace (inclusive quebra de linha/tabulação) em espaço; remover os demais controles Unicode das categorias `Cc/Cf`, incluindo bidirecionais; por fim colapsar espaços e aparar as bordas. Usar “Compromisso” se vazio. Limitar a 160 pontos de código, incluindo reticência final `…` quando houver corte.
- Corpo: no máximo 1000 pontos de código pelo `len()` Python, após normalização; é orçamento local conservador, não limite publicado do sandbox. O sender existente aceita até 4096 pelo `len()`. Se campos obrigatórios não couberem ou forem inválidos, bloquear o formato; não cortar data/hora/fuso.
- Payload sandbox: `messaging_product:whatsapp`, `recipient_type:individual`, `to` resolvido exclusivamente da política privada, `type:text`, `text.body` com o corpo. Não obter o destino do LLM, título ou tarefa do n8n. O [contrato JSON](../examples/reminders/channel-contract.json) usa placeholder fictício.
- Template futuro: usar somente nome, idioma e componentes efetivamente aprovados para lembrete; validar conta, destinatário, finalidade e parâmetros. Sua existência não é presumida pelo contrato. Categoria, aprovação e custos serão conferidos ao preparar esse canal.

## Lacunas confirmadas no código atual

| Código | Situação observada | Trabalho necessário |
|---|---|---|
| `sandbox_webhook.py` / `whatsapp_inbox.py` | Timestamp só tem validação sintática de dígitos e é descartado; `created_at/updated_at` são horários técnicos locais. | Validar/persistir o instante original de uma nova mensagem elegível e seu vínculo. Não fazer backfill usando esses horários locais. |
| `whatsapp_agent_store.py` | Jobs/contexto possuem vínculo privado e horários de processamento, sem última mensagem do usuário. | Persistir observação do canal por vínculo, separada do TTL de conversa. |
| `whatsapp_sandbox_sender.py` | Texto não verifica janela. Template exposto é apenas `disclaimer/en`. | Gate de canal imediatamente antes do transporte; não chamar o sender diretamente pelo scheduler. |
| `whatsapp_outbox.py` | Aceite/incerteza e fingerprint, sem identidade de lembrete ou conciliação com status. | Ledger semântico próprio; aceite não será prova de entrega/leitura. |
| Conector de agenda | Consulta ocorrência exata e expande recorrências, mas DTOs atuais não comprovam ID/fuso real da agenda de ponta a ponta. | Resolver agenda autorizada e fuso efetivo antes de preparar avisos. |

O callback do sandbox usa URL com capacidade secreta e filtro de remetente; **isso não é assinatura criptográfica de origem**. Não há garantia adicional de autenticidade sendo criada nesta parte. A limitação deve acompanhar o registro de proveniência; entrada sintética de testes fica em ambiente separado e nunca abre janela no canal real.

## Decisão de canal a implementar

1. Exigir configuração de lembretes habilitada, destinatário/vínculo privados corretos e permissão vigente para esses avisos. Desativação/desistência bloqueia futuros envios. Não inferir permissão comercial de um `START` de teste.
2. Registrar somente nova mensagem de texto do usuário autorizado, recebida pelo callback real e validada. Preservar hash do ID, vínculo, timestamp original em segundos Unix convertido para UTC, instante de recebimento UTC e proveniência com a limitação acima. Timestamp ausente, inválido ou futuro em relação ao recebimento não abre/estende janela. Não substituir pelo horário local. Duplicata não muda a primeira observação; entrada fora de ordem não reduz nem prolonga artificialmente a janela.
3. Calcular a última observação válida por vínculo de forma transacional. Janela desconhecida em banco antigo continua desconhecida até nova entrada válida. Envio do bot/template, leitura/entrega, replay, retry do n8n, consulta ao Google e teste sintético não renovam esse instante. Mudança do vínculo não herda janela anterior.
4. Reler evento e atualizar o relógio. Aplicar simultaneamente a janela do aviso da parte 1 e a do canal. Para texto, usar `last_user_message_utc <= now_utc < last_user_message_utc + 86400s - 60s`. Os 60 segundos são margem operacional local: não iniciar envio perto do limite; não são exigência do WhatsApp nem garantia contra latência.
5. Se a janela de texto estiver desconhecida, fechada ou dentro da margem, exigir template contextual aprovado. **No sandbox atual ele não existe: registrar bloqueio de canal, sem transporte.** Só reconsiderar a mesma identidade se houver nova evidência de janela, antes do prazo do aviso; depois do prazo, vencer sem rajada retroativa.
6. Reservar a identidade semântica uma única vez antes da tentativa, com exclusão mútua. Reavaliar relógio, evento/configuração e canal no ponto final de envio. A reserva isolada não autoriza mandar após o prazo. Resultado potencialmente enviado permanece incerto; não fazer fallback de tipo nem retry cego. O provedor continua autoritativo no aceite.

Motivos ilustrativos do contrato, ainda sem schema físico: `channel_window_unknown`, `channel_window_closed`, `channel_window_margin`, `template_unavailable`, `permission_missing`, `recipient_mismatch`, `invalid_format` e `reminder_expired`. Bloqueio local anterior ao transporte difere de aceite, entrega e resultado incerto; não consumir uma tentativa de envio nem registrar sucesso por ter agendado uma tarefa.

## Conferência e alcance

Parte 2 concluída como revisão do canal, contrato e exemplos. Foram confrontados código público local, fontes oficiais e ensaios históricos, incluindo a confirmação humana de texto, imagem e template de teste; esses ensaios não demonstram lembrete fora da janela. Os exemplos locais de decisão/formato verificam o documento, **não uma funcionalidade ativa**. Não houve novas mensagens, consulta de saldo, chamada privada de provedor, restart ou alteração de código/banco/configuração/workflow.

O registro 123 no [índice de evidências](evidencias.md) conserva fontes, hashes, resultados e limites. A suíte histórica continua em 1.561 testes Python; não foi repetida para esta alteração documental. O PDF será consolidado no fechamento da etapa; esta parte não gerou um print sem resultado visual novo. Próxima entrega: persistir configuração, observações de canal e estados dos lembretes.


**Atualização da parte 3:** [SQLite e observações imutáveis implementados](persistencia-lembretes.md), com início vazio e sem usar horários técnicos antigos para abrir janela. As lacunas da tabela acima descrevem o caminho anterior do bot, que continua sem integração ao novo store. O gate real, a revalidação do Google e o transporte de lembretes serão conectados nas partes seguintes.


**Atualização da parte 7:** o receptor atual grava observações de mensagens novas autorizadas antes do encaminhamento; status, mídia, timestamps futuros/inválidos e replays não abrem janela. Não houve backfill da base real por testes ou horários locais. A decisão de texto é revalidada na transação que marca `sending`; fora da janela ou na margem o aviso é bloqueado. Template contextual continua indisponível no sandbox. O [guia de envio](envio-lembretes.md) descreve a implementação e distingue testes com fixtures da verificação do runtime sem avisos devidos.
