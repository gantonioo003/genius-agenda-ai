# Conversas completas do agente — etapa 11, parte 11

Validação de continuidade, autorização, conflitos, repetição e recuperação de
falhas. O agente usa os serviços e executores existentes; o texto do modelo e o
ACK do callback não comprovam uma gravação na agenda ou uma entrega ao usuário.
Este marco mantém separadas as provas locais, a inferência real e a conversa
humana pelo canal. A parte 12 consolidou o [fechamento da etapa 11](fechamento-etapa-11.md), preservando os resultados e limites deste marco.

## Método e resultado

Os testes são executados em uma cópia pública isolada, sem `.env`, credenciais
ou bases do runtime. `ConversationStore`, ledgers de criação/alteração/exclusão,
serviços, validações e executores usam SQLite real. Modelo, gateway Google e
sender WhatsApp são fixtures determinísticas; falhas são injetadas nas
fronteiras de persistência e de transporte. Reinícios reabrem os stores nos
mesmos arquivos, preservando o estado externo simulado.

O conjunto acrescenta **53 testes**: 13 trajetórias de conversa, 17 de callback/API/worker, 22 de autorização e uma regressão de contenção SQLite. Cada cenário confere respostas públicas, contexto persistido e quantidade/identidade das escritas. Importar o harness não executa novamente seus testes nem infla a contagem.

**Suíte Python final da parte 11: 1.561 testes aprovados, zero falhas/erros/skips; 53 novos sobre 1.508.** O registro 115 conserva os logs e a identificação das fontes finais, conferidos no fechamento. A rodada anterior à sétima
correção passou com **1.552 testes**; esse resultado intermediário não substitui
a validação da versão final.

As **29 verificações HTTP TCP locais** passaram novamente com as fontes finais: API, receptor, worker e SQLite reais, ponte n8n e provedores fictícios. São 17 requisições de negócio e 12 observações, em dois processos. Os **540
cenários JavaScript** e as **42 verificações com Docker n8n publicado** são
resultados históricos preservados da parte 10; esta parte não acrescenta uma
nova execução JavaScript ou uma nova prova com n8n publicado.

## Trajetórias de conversa

Arquivo: [`test_agent_conversation_paths.py`](../tests/test_agent_conversation_paths.py).

| Trajetória | Verificação principal |
| --- | --- |
| Término faltante → intervalo inválido → correção → consulta | Não grava antes da correção; cria uma vez e relê uma mudança externa posterior. |
| Correção da data no rascunho | Remove horários da data antiga e exige horários válidos para a nova data. |
| Cancelamento → fragmento atrasado → novo pedido | O fragmento não ressuscita o pedido cancelado; o novo comando completo é independente. |
| Falha do modelo no meio do rascunho → recuperação | Preserva os dados, descarta autorização herdada e exige prévia/aprovação antes de criar. |
| Dois candidatos → horário ambíguo → escolha explícita → edição → repetição | Horário não vira número de opção; somente o alvo aprovado muda, uma vez. |
| Candidato alterado após a apresentação | Exige nova seleção e apresenta o estado atual antes de aceitar aprovação. |
| Conflito → sugestão de alternativa → aprovação | O conflito não grava; a pergunta sobre outro horário só prepara; a aprovação aplica o novo intervalo. |
| Revogação → aprovação atrasada → nova proposta | A aprovação antiga não executa; um novo pedido recebe outra proposta. |
| Expiração no limite de 300 segundos → repetição → nova prévia | A proposta expirada permanece inválida até uma nova prévia e aprovação. |
| Consulta → semana seguinte com falha → nova consulta → referência ambígua | Falha preserva o último período confirmado; múltiplos resultados removem seleção única. |
| Evento selecionado removido externamente → consulta → edição referencial | A ausência remove a referência; a edição pede identificação e não grava. |
| Criação aplicada com resposta perdida → comando repetido após reinício | Reconcilia a criação existente sem uma segunda escrita. |

## Callback, fila e entrega

Arquivo: [`test_agent_delivery_paths.py`](../tests/test_agent_delivery_paths.py).
Os 17 cenários percorrem receptor, API autenticada, worker e stores reais,
mantendo os provedores substituídos:

- Lotes mistos repetidos, mídias e status fora de ordem conservam uma resposta
  por mensagem nova, sem enviar conteúdo privado à ponte.
- Perda antes da ativação mantém o trabalho pendente e a ordem do remetente;
  perda depois da ativação preserva o trabalho executável sem repetir o callback.
- Falha ao concluir a inbox depois do efeito não recria a operação nem a resposta.
- Resposta preparada e sessão persistida sobrevivem a reinício sem nova
  interpretação do modelo ou nova operação de agenda.
- Interrupções em `processing` ou `delivering` bloqueiam a retomada cega e a
  passagem de trabalhos posteriores do mesmo remetente.
- Falhas de commit da sessão ou da resposta após gravação mantêm o estado
  incerto e impedem criar um contexto novo para repetir a operação.
- Resultado de envio incerto, status tardio, falha ao persistir o recibo e falha
  ao concluir a fila não liberam reenvio automático.
- Rejeição definitiva encerra o envio; uma prévia rejeitada perde sua capacidade
  de aprovação. Falhar ao persistir esse descarte bloqueia o seguimento.
- Resposta de agenda perdida permite reconciliação por leitura quando há prova;
  a ausência dessa prova mantém o resultado desconhecido, sem nova escrita.

## Falhas encontradas e proteções de autorização

Arquivos: [`test_agent_conversation_authorization.py`](../tests/test_agent_conversation_authorization.py),
[`whatsapp_agent.py`](../app/services/whatsapp_agent.py) e
[`whatsapp_agent_worker.py`](../app/whatsapp_agent_worker.py).

A auditoria e a sondagem real identificaram as sete falhas abaixo. Suas
proteções passaram na suíte da versão final; os dois ajustes adicionais
registrados adiante completam os nove caminhos corrigidos neste marco:

| Falha reproduzida | Proteção e regressão |
| --- | --- |
| Pedido completo com “aguarde minha confirmação” conservava execução automática | Reconhecer espera/revisão/aprovação posterior; apresentar prévia e aguardar resposta afirmativa exata. |
| ID do evento era aceito como substring de outro token opaco | Exigir identificador completo e exato, inclusive em consulta que pudesse criar uma seleção para edição posterior. |
| Valores presentes no texto podiam ser atribuídos a campos trocados ou omitidos | Conferir o papel de título, descrição e local; se a interpretação divergir, exigir conferência da proposta estruturada. |
| Duas escritas na mesma mensagem podiam executar só a escolhida pelo modelo | Pedir um pedido por vez antes de chamar o modelo; descartar rascunho/confirmação anteriores desse pedido múltiplo. |
| Correção textual de título, descrição ou local podia ser ignorada na continuação | Romper a autorização herdada quando a proposta não refletir a correção; não gravar silenciosamente o valor antigo. |
| Prévia com rejeição definitiva de envio ainda permitia “sim” posterior | Descartar a confirmação não apresentada com controle de revisão; falha ao persistir esse descarte mantém bloqueio seguro. |
| Gemini inferiu um término não informado durante a continuação | Exigir término ou duração sustentados pela conversa; manter pedido de esclarecimento em vez de tornar aprovável um término inventado. |

As regressões também preservam comandos válidos de um único campo e limpeza
explícita de descrição/local. Uma aprovação vale para a proposta apresentada e
seu estado; não é uma autorização genérica para reinterpretar ou repetir escritas.

## Concorrência e inferência real

As rodadas intermediárias registraram duas falhas no teste antigo
`test_processes_share_one_claim`: um processo reservou e o outro recebeu
`deletion_unavailable`. O caso foi repetido **três vezes isoladamente, com três
aprovações**, sem afrouxar a asserção. As falhas permanecem no registro 115.
O teste não observou duas reservas bem-sucedidas. A reprodução controlada e a
melhoria posterior do timeout estão descritas adiante.

O timeout SQLite original era de 1.000 ms e as transações usam `BEGIN IMMEDIATE`. Contenção
ou I/O transitório são hipóteses; o tipo/código do erro subjacente não foi
identificado porque `_connection` projeta `OSError`/erros SQLite em uma mensagem
pública fixa. Não há prova suficiente para afirmar que a causa foi timeout.
O diagnóstico e as três repetições estão em `concurrency-check.json`, junto aos
demais registros locais do marco.

Uma primeira sondagem conversacional com Gemini real fez **duas requisições,
ambas HTTP 200**, mas falhou na expectativa de manter o término faltante: a
continuação retornou prévia em vez de esclarecimento. Houve **zero chamadas
Google, zero envios WhatsApp e zero mutações reais**; as fixtures também não
gravaram. Essa falha motivou a sétima proteção, e HTTP 200 sozinho não valida a
conversa. A sondagem final de consulta/exclusão usou três requisições ao modelo. O histórico separa as tentativas, seus cenários e suas falhas.

**Resultado final da sondagem Gemini e da prova humana: três requisições reais HTTP 200 concluíram consulta diária, continuação para semana e prévia de exclusão com identificador explícito. Aprovação sintética executou uma exclusão somente fictícia, e repeti-la não chamou o modelo nem excluiu de novo. Uma sondagem anterior validou três turnos de criação fictícia após corrigir o término inventado; sua tentativa posterior de exclusão falhou com segurança e permanece no histórico. Houve 15 chamadas Gemini entre todas as sondagens, sem Google/WhatsApp reais neste marco. Não foi solicitada nova confirmação humana pelo canal; a parte 10 preserva sua prova de recebimento.**
Um teste de modelo com gateway fixture não comprova entrega WhatsApp. Uma prova
humana deve distinguir aceite do provedor, recebimento no aparelho, confirmação
do usuário e eventual efeito real de agenda, conforme o que foi efetivamente
observado. O recebimento humano já registrado na parte 10 continua histórico.

## Reprodução local

Na raiz da cópia pública preparada com as fontes finais e o ambiente Python já
instalado, sem configuração privada:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'tests'
.\.venv\Scripts\python.exe -m unittest test_agent_conversation_paths test_agent_delivery_paths test_agent_conversation_authorization test_whatsapp_agent_api_worker -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

O executável pode apontar para o venv existente fora da cópia; isso não exige
copiar `.env`, credenciais ou bancos. No ambiente Windows usado nesta validação,
SQLite, subprocessos e testes HTTP locais exigiram execução fora da restrição do
sandbox. Esses comandos não são uma autorização para chamar provedores reais.
As sondagens reais ficam separadas e devem preservar seus limites explícitos.

## Privacidade, evidência e limites

Os registros **115 a 118** mantêm logs/JSON reais de cada rodada,
hashes das fontes verificadas, contadores, limites e resultados seguros no
[índice de evidências](evidencias.md). A falha inicial de concorrência e a
sondagem Gemini que falhou devem permanecer no histórico, separadas das
repetições aprovadas. Capturas só acrescentam valor quando mostram um estado
visual relevante; regras, testes e HTTP são documentados em texto/JSON.

Não registrar `.env`, chaves, tokens, callback secreto, destinatário, IDs
privados, texto de conversas reais ou respostas brutas do provedor. Conteúdo
fictício dos cenários pode constar nos registros locais. Não publicar parâmetros
de subprocessos que carreguem configuração privada.

Fixtures determinísticas comprovam a orquestração e as barreiras de escrita
para os planos exercitados; não medem a qualidade geral de interpretação do
Gemini. Reconciliação e deduplicação não constituem uma transação distribuída
entre Google e WhatsApp. Resultado desconhecido exige conferência e não libera
retry automático. O [fechamento](fechamento-etapa-11.md) confere as pendências e a versão das
fontes, sem converter resultados históricos em novas provas deste marco.


## Ajustes adicionais comprovados

“E nesta semana?” não seguia o caminho de consulta com contexto; as formas nesta/nessa/neste/nesse agora reutilizam a consulta anterior, com regressão local e prova Gemini real. O pedido final de exclusão explicita que o valor é um identificador; um nome fictício solto pode corretamente produzir uma busca por título e pedir a data.

A contenção SQLite foi reproduzida separadamente com bloqueio de 1,3 s: `OperationalError`, código 5/`SQLITE_BUSY`. O ledger de exclusões passou a aguardar até 5 s por uma conexão bloqueada, sem repetir DELETEs. O teste novo falha com 1 s e passa com 5 s, exige uma única reserva e o mesmo nonce entre processos. A causa exata das falhas iniciais não foi capturada e continua sem confirmação; o novo cenário controlado comprova a melhoria e a suíte final passa sem afrouxar a asserção.

As sete proteções da tabela, o encaminhamento de continuidade e a espera de contenção formam nove ajustes deste marco. Há um limite conservador: primeira mensagem sem qualquer horário/duração e sem rascunho anterior pode continuar exigindo prévia completa; o filtro de término inventado cobre principalmente a continuação e mensagens com horário incompleto. Datas relativas e interpretações sem vínculo local suficiente podem exigir conferência.

API/receptor foram reiniciados com a fila ociosa e o novo código; o túnel temporário encerrado foi recuperado, com cadastro e leitura do callback confirmados. N8n e suas credenciais/exports permaneceram iguais. Evidências 115 a 118 no índice. A parte 11 está concluída neste escopo; a parte 12 encerrou a revisão documental, preservando estes resultados sem novas execuções.
