# Reagendamentos sucessivos

Revisão de 26/10/2026, após a entrega do vídeo.

## Problema observado

Depois de consultar amanhã e mover a entrevista para hoje, o pedido seguinte ainda procurava na data da consulta anterior. Quando o usuário repetia a identificação do compromisso com seus horários atuais, esses horários também podiam substituir o destino já pedido. Isso gerava “não encontrei” e uma prévia de reagendamento sem mudança real.

## Comportamento corrigido

- A busca pelo título usa a data atual do evento lembrado somente após uma nova leitura, conferência do identificador, estado e título exato. O ID lembrado não desempata resultados; a busca por título e período continua detectando duplicados.
- Uma data de origem explícita prevalece. A data do destino em `start/end` permanece separada dos critérios usados para localizar o compromisso atual.
- O período de semana/mês é conservado, para não esconder candidatos com o mesmo título. Uma seleção ausente ou de outro título não muda o período por suposição.
- Respostas que identificam o evento conservam o início e término desejados. Um pedido explícito de correção pode substituí-los. Uma linha copiada da prévia continua sendo identificação mesmo se o modelo sugerir iniciar outro rascunho.
- Dados inválidos ou outros campos ausentes são esclarecidos antes de depender de uma leitura do calendário. Planos válidos com IDs explícitos mantêm seu fluxo.
- Reagendamento e exclusão continuam exigindo prévia e confirmação vinculadas ao evento e à versão. A revisão não altera eventos automaticamente.

## Validação

Suíte Python completa: **2438 testes aprovados**, sem falhas ou erros, em 226.713 segundos. O teste de conversa completa usa stores reais temporários e calendário/modelo simulados: consulta amanhã, primeiro reagendamento para hoje, segundo reagendamento e exclusão, com cada escrita condicionada a `sim`.

As regressões também cobrem títulos duplicados, falha de leitura, data de origem explícita, horário de destino em outro dia, título com palavras como “Mudança”, correção explícita e dados inválidos. A captura privada 174 documenta o problema anterior; o relatório 175 registra estes testes. Não houve novo teste humano no WhatsApp nem escritas no Google nesta revisão.
