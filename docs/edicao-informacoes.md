# Alterar informações — etapa 07, parte 5

**Concluída e validada em 21/10/2026.**

## Implementação

Em 21/10/2026, criamos `app/services/event_details.py`, independente da IA e do Google, e o comando `app/edit_event.py`. O conector passou a editar título, descrição e local pelo ID exato na agenda de teste configurada.

O conector lê o evento e seu ETag, envia somente os campos informados e confirma o resultado por outra leitura. Data e horário são preservados e também conferidos no resultado. Nenhuma dependência ou permissão foi acrescentada.

## Executar

Na raiz do projeto, com a conta já autorizada, substitua `ID_RETORNADO_PELA_CONSULTA` pelo ID identificado:

```powershell
$eventId = "ID_RETORNADO_PELA_CONSULTA"
.\.venv\Scripts\python.exe -m app.edit_event --event-id $eventId --title "Entrevista técnica - Empresa Exemplo (teste)" --description "Preparar apresentação do projeto." --location "Online - sala de teste"
```

O padrão é **prévia somente de leitura**, com `status: preview`, dados atuais em `before`, campos solicitados em `requested` e `after` nulo. Acrescente `--execute` para aplicar a edição explicitamente.

Para alterar somente um campo, informe apenas esse campo:

```powershell
.\.venv\Scripts\python.exe -m app.edit_event --event-id $eventId --location "Online - sala de teste" --execute
```

Para remover explicitamente uma informação:

```powershell
.\.venv\Scripts\python.exe -m app.edit_event --event-id $eventId --clear-description --execute
.\.venv\Scripts\python.exe -m app.edit_event --event-id $eventId --clear-location --execute
```

Esses comandos de limpeza são exemplos de uso; a demonstração real preservou a nova descrição e o novo local. API, Docker e n8n não precisam estar iniciados. `--timezone` controla a exibição dos horários, com padrão `America/Recife`; não altera o horário do evento.

## Regras da edição

- Pelo menos um campo deve ser informado. Título vazio ou só com espaços é rejeitado; espaços nas extremidades do novo título são removidos.
- Campo omitido fica intacto. No serviço Python, `None` representa omissão; `""` limpa descrição ou local. A CLI oferece flags explícitas de limpeza.
- A requisição só pode conter `summary`, `description` e `location` fornecidos. Datas, convidados, lembretes e outras propriedades não entram no PATCH.
- Ausência e texto vazio de descrição/local são equivalentes ao conferir o resultado. O título real é comparado sem usar o rótulo de exibição de eventos sem título.
- Eventos comuns temporizados, de dia inteiro e ocorrências individuais são suportados. Séries recorrentes completas e tipos especiais são rejeitados.
- IDs ausentes, cancelamento, estado desconhecido, identidade ou versão inválida impedem a gravação. A operação não procura outro evento como substituto.
- A edição usa `If-Match` com o ETag lido. Um erro 412 interrompe o processo; não há reaplicação automática com outra versão.
- O PATCH usa `num_retries=0`. Falha de gravação ou confirmação posterior produz erro com indicação de resultado possivelmente aplicado; consultar o ID antes de repetir.

| Resultado | Significado |
|---|---|
| `preview` | Dados atuais e solicitação exibidos, sem gravação |
| `updated` | Edição aceita e confirmada por nova leitura, com dados omitidos e horários preservados |
| `unchanged` | Os campos informados já tinham os mesmos valores; nenhuma escrita foi necessária |

Erros operacionais ou de validação retornam código 1 e mensagem em stderr, sem JSON de sucesso. Erros de sintaxe dos argumentos retornam código 2. A prévia não trava o evento entre comandos; a execução relê o estado atual e protege o intervalo GET/PATCH. A demonstração usa `sendUpdates="none"` em um evento sem convidados.

## Validação real

Em **21/10/2026**, alteramos os três campos da entrevista fictícia:

| Campo | Valor após a edição |
|---|---|
| Título | Entrevista técnica — Empresa Exemplo (teste) |
| Descrição | Entrevista fictícia atualizada. Preparar apresentação do projeto Genius Agenda AI. |
| Local | Online — sala fictícia de teste |
| Data e horário preservados | 23/10/2026, 16h–16h30, America/Recife |

O ID permaneceu o mesmo. A confirmação incluiu a leitura posterior dos três campos e a identificação da entrevista pelo novo título. Os registros locais separam prévia (020), edição confirmada (021), conferência posterior (022) e captura real do Google Agenda (023).

Os registros das etapas anteriores mantêm o título e a descrição históricos. A entrevista foi posteriormente excluída com confirmação; os comandos de edição exigem um evento disponível. O comando de criação original não deve ser usado para restaurar o evento removido; outra demonstração deve preparar um novo evento de teste com ID próprio.

## Testes e revisão

Criamos `tests/test_event_details.py` com **24 testes novos**. A suíte completa passou com **116 testes**, na preparação isolada e no repositório. Os testes automatizados usam clientes e persistência falsos, sem rede ou credenciais; a edição real foi validada separadamente.

Os casos cobrem as sete combinações possíveis de campos, omissão e limpeza explícita, normalização do título, comparação com valores reais, prévia, ausência de escrita redundante, PATCH restrito, ETag, ordem GET/PATCH/GET, validação dos dados preservados e dos horários, falhas e resultados incertos, calendários inseguros, dia inteiro, ocorrência específica e argumentos da CLI. A revisão independente não encontrou bloqueadores no escopo.

## Continuação

A exclusão com confirmação e a revisão final foram concluídas; a **etapa 07 está encerrada**, conforme o [registro de fechamento](fechamento-etapa-07.md). O transporte HTTP/n8n/WhatsApp e os lembretes seguem pendentes. Descrição e local são lidos nesta operação explícita e ficam no acervo privado; as consultas gerais continuam pedindo apenas seus campos mínimos.

Referências: [edição parcial com events.patch](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch) e [controle de versão com ETag](https://developers.google.com/workspace/calendar/api/guides/version-resources).


**Atualização na etapa 08, parte 6:** uma resposta de escrita perdida ou inválida agora é conferida por leitura do mesmo ID. O resultado inclui `recovered: true` quando essa leitura confirma o estado esperado; se a conferência falhar, permanece um erro seguro, sem nova escrita automática. Os registros e limites estão no [guia de falhas e recuperação](falhas-recuperacao.md).
