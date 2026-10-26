# Respostas e falhas no n8n

**Etapa 09, parte 8 — implementada em 23/10/2026.** Acrescentamos um apresentador de respostas reutilizável e o conectamos aos caminhos de sucesso, entrada inválida e falha de conexão do primeiro fluxo.

O fluxo publicado continua sendo de **consultas**. Dúvidas, conflitos, confirmação, preparação e resultados de escrita são tratados pela biblioteca e conferidos com exemplos fictícios; os fluxos n8n de escrita sobre Google real e a preparação HTTP ainda não estão conectados ao fluxo normal. Na parte 9, uma [fixture separada](n8n-testes-integracao.md) exercitou escritas com o HTTP Google simulado. WhatsApp e IA permanecem nas etapas futuras.

## Comportamento

| Resposta da API | Apresentação | Próximo passo |
|---|---|---|
| Consulta concluída com eventos | Quantidade de compromissos encontrada | Encerrar |
| Consulta concluída com lista vazia válida | Não há compromissos no período | Encerrar |
| `needs_clarification` / `needs_correction` | Perguntar pelos dados faltantes ou a corrigir | Aguardar informação |
| `ready` | Informar que os dados estão preparados, sem afirmar que executou | Aguardar a ação solicitada |
| `needs_confirmation` | Pedir confirmação, sem incluir o código no texto | Aguardar consentimento |
| `conflict`, HTTP 409 | Informar horário ocupado e perguntar outro horário | Aguardar escolha |
| `not_found`, HTTP 404 | Pedir que o usuário confira o compromisso | Identificar o evento |
| Criação/edição/reagendamento/exclusão confirmados | Comunicar o resultado conferido | Encerrar |
| Criação reutilizada / alteração `unchanged` | Informar reutilização ou que já estava como solicitado | Encerrar sem outra escrita |
| Falha de consulta | Informar indisponibilidade, sem inventar lista vazia | Conferir conexão |
| Escrita com `mutation_state: unknown` | “Não foi possível confirmar se a alteração aconteceu. Confira a agenda antes de enviar o pedido novamente.” | Conferir o estado antes de qualquer reenvio |
| Envelope incoerente ou inesperado | HTTP 502 seguro; em contexto de escrita, manter incerteza | Não presumir sucesso nem ausência de escrita |

O apresentador **não executa ações**. Todas as apresentações têm `auto_retry: false` e `execution_allowed: false`; esses campos são orientação, e o grafo do fluxo também impede retorno ao bloco HTTP. No fluxo normal de consultas, o único bloco HTTP permanece GET, com retentativas e redirecionamentos desativados. A fixture de integração tem caminhos próprios de leitura/escrita, sem retorno ao executor.

## Contrato do webhook

A API Python mantém seu contrato de nove campos. O webhook preserva esses campos e acrescenta `presentation`, separado de `data` e dos erros técnicos. Exemplo fictício de consulta:

```json
{
  "presentation": {
    "kind": "success",
    "text": "Encontrei 3 compromissos neste período.",
    "next_step": "done",
    "requires_user_input": false,
    "auto_retry": false,
    "execution_allowed": false
  }
}
```

O exemplo mostra somente o campo novo; ele não substitui o envelope completo. Códigos HTTP legítimos são preservados. Falha de transporte ou resposta incompatível gera 502 seguro. A recusa de autenticação do próprio webhook continua sendo o 403 nativo do n8n, antes do fluxo, sem `presentation`.

O contexto de chamada define a ação esperada e a fase (`read`, `prepare`, `preview` ou `execute`). No fluxo normal publicado, é sempre `list`/`read`. A fixture isolada define a ação/fase de cada operação de ensaio. Uma resposta dizendo que criou ou excluiu algo nesse caminho é rejeitada, mesmo que tenha formato de sucesso. HTTP, estado e dados mínimos precisam ser coerentes; `recovered` só aparece como confirmação quando o envelope declara resultado confirmado.

## Texto, dados e confirmação

As mensagens usam templates e nomes de campos permitidos. Não copiam `message`, `question`, erros brutos, cabeçalhos, URLs internas, IDs de operação ou código de confirmação para o texto apresentado. Uma propriedade desconhecida não reaparece na pergunta. O envelope técnico original continua privado e pode conter dados dos eventos ou, na prévia, o código que um futuro executor precisará.

Antes de escolher entre candidatos ou confirmar uma exclusão, a futura interface/integração deve mostrar o resumo seguro do evento e obter a resposta do usuário. O texto genérico “compromisso selecionado” sozinho não é confirmação informada. Esta parte não implementa essa conversa nem armazena consentimento e não libera exclusão automática.

Na integração futura com WhatsApp, enviar a apresentação apropriada e o resumo autorizado, em vez de encaminhar o JSON técnico inteiro. A validação e as decisões de escrita continuam nos serviços Python; o apresentador não substitui ETag, confirmação ou controle de identidade da operação.

## Arquivos e reprodução

- [`n8n/lib/present-api-response.js`](../n8n/lib/present-api-response.js): biblioteca sem rede ou operações de agenda.
- [`n8n/build-workflow.cjs`](../n8n/build-workflow.cjs): incorpora a mesma fonte testada no Code node do export.
- [`n8n/workflows/consulta-eventos.json`](../n8n/workflows/consulta-eventos.json): fluxo com apresentação nos três caminhos.
- [`n8n/tests/present-api-response.test.cjs`](../n8n/tests/present-api-response.test.cjs): matriz do apresentador.
- [`n8n/tests/consulta-eventos.test.cjs`](../n8n/tests/consulta-eventos.test.cjs): validação, configuração, grafo sem ciclos e correspondência da fonte incorporada.
- [`n8n/tests/fixtures/response-cases.json`](../n8n/tests/fixtures/response-cases.json): seis exemplos fictícios, sem acesso a serviços.

Na raiz do repositório, com Node instalado:

```powershell
node n8n/tests/present-api-response.test.cjs
node n8n/tests/consulta-eventos.test.cjs
node n8n/demo-responses.cjs
```

O último comando mostra dúvidas, conflito, confirmação, resultado incerto, preparação e sucesso **fictícios**, sem executar pedidos. O texto não contém códigos de confirmação nem identificadores de operação.

Depois de alterar a biblioteca, regenerar e conferir o export antes de reaplicar no n8n:

```powershell
node n8n/build-workflow.cjs
node n8n/tests/consulta-eventos.test.cjs
.\.venv\Scripts\python.exe scripts/setup_n8n.py --apply
```

O setup atualiza o mesmo fluxo e reinicia somente o n8n, preservando o volume. Com a API ligada, a consulta pode mostrar apenas o texto preparado:

```powershell
.\.venv\Scripts\python.exe scripts/test_n8n.py --date 2026-10-23 --text-only
```

Para os demais pré-requisitos, credenciais e cuidados com o volume, consulte o [primeiro fluxo](n8n-primeiro-fluxo.md). O salvamento do histórico continua desabilitado, mas a versão do n8n usada pode manter entrada e metadados transitórios até a limpeza automática. O volume e as evidências permanecem privados.

## Validação deste marco

**297 cenários JavaScript locais aprovados:** 215 do apresentador e 82 do fluxo. Incluem perguntas, conflitos, estados incertos, confirmação, sucessos recuperados, contexto incorreto, DTOs inconsistentes, proteção do texto e ausência de caminhos que repitam a chamada. São testes sem rede, distintos da suíte Python.

**19 verificações pelo webhook real aprovadas:** consultas de dia/semana/mês/defaults/lista vazia, entrada inválida, autenticação, erro de fuso, API desligada, resposta malformada, 401/503 e recuperação. As consultas válidas alcançaram o Google real. A resposta malformada e os erros de configuração foram provocados em servidores/processos locais temporários; a API normal foi restaurada. Os mesmos três eventos permaneceram iguais. Não houve instrumentação da contagem do transporte Google nesta parte.

O Python da aplicação permanece igual aos **58 arquivos da suíte de 444 testes previamente aprovada**, sem nova rodada porque não houve alteração nessa camada. Evidência **063**: testes e exemplos locais. Evidência **064**: fluxo HTTP real e falhas locais controladas. A captura 061 preserva a configuração visual do marco anterior; nesta parte, JSON e saídas de testes demonstram melhor o tratamento das respostas.
