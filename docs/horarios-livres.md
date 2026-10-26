# Horários livres — etapa 13, parte 4

> **Atualização do bloco B:** disponibilidade já está [conectada ao WhatsApp](preparo-whatsapp.md), com datas/horas legíveis e sem `S1`, ISO ou código IANA no caso normal. As validações e o contrato detalhado API/CLI foram preservados. As provas e observações de runtime da parte 4 abaixo são históricas.

**Marco da parte 4, implementado em 26/10/2026 por serviço, API avulsa e CLI.** O Python calcula até três alternativas futuras na agenda autorizada, considerando duração, faixa diária, ocupação e margem opcional. Não chama IA, não cria reserva e não altera eventos. A sugestão de reagendamento vinculada a uma entrevista foi adiada; a integração WhatsApp foi concluída depois no bloco B.

## Uso

```powershell
.\.venv\Scripts\python.exe -B -m app.find_free_slots --date-from 2030-06-19 --date-to 2030-06-19 --window-start 09:00 --window-end 12:00 --duration-minutes 30
.\.venv\Scripts\python.exe -B -m app.find_free_slots --date-from 2030-06-19 --date-to 2030-06-20 --window-start 09:00 --window-end 18:00 --duration-minutes 45 --buffer-minutes 15 --counts-only
```

`--counts-only` omite a lista de opções e conserva metadados/contagens. O comando termina com 0 quando a leitura completa permite concluir, inclusive sem opções futuras; erro tem exit code 1 e código sanitizado, sem diagnóstico bruto do Google.

```text
POST /api/v1/agenda/availability
Authorization: Bearer <chave privada já configurada>
Content-Type: application/json
```

```json
{"date_from":"2030-06-19","date_to":"2030-06-19","window_start":"09:00","window_end":"12:00","duration_minutes":30,"buffer_minutes":0}
```

Datas inicial/final são **inclusivas**, com até **31 dias civis**. Datas exigem ISO completo. A faixa diária é explícita: início `00:00` a `23:59`; fim no mesmo dia ou `24:00`, sempre posterior. Não há faixa atravessando a madrugada, múltiplas faixas por dia ou expediente presumido. Todos os dias escolhidos no intervalo são considerados, inclusive fins de semana; não há preferência de trabalho inferida ou persistida.

Duração aceita inteiro de **1 a 1440 minutos**; margem aceita inteiro de **0 a 240**, padrão zero. Booleanos, decimais e strings numéricas são recusados no JSON. A grade de início é fixa em quartos de hora locais (`00/15/30/45`), independente da duração. Faixa iniciada às 09:07 gera a primeira candidata às 09:15. Não há opção de cliente para mudar grade/limite, selecionar agenda/fuso ou excluir um evento da ocupação.

A fronteira herda Bearer, correlação e `Cache-Control: no-store`. Rejeita query, campos extras/repetidos, JSON inválido, outro content-type e corpo acima de 4.096 bytes; a espera do corpo é limitada a dois segundos. Autenticação e validação lexical acontecem antes de abrir a fonte. O trabalho síncrono roda fora do loop ASGI. Veja [exemplos de entrada](../examples/preparation/availability-requests.json).

## Fuso e tempo

O fuso vem exclusivamente do vínculo da agenda, não do host, chat ou cliente. Cada fronteira diária deve existir e representar **um único instante** nesse fuso. Fronteira ambígua ou inexistente retorna erro de entrada após a leitura do vínculo, sem declarar horários livres. Pode-se escolher uma faixa diferente, com fronteiras inequívocas. `24:00` representa a meia-noite seguinte e também precisa ser válida/unívoca.

Transições no interior de uma faixa válida são suportadas. A grade local é enumerada nos dois folds e conferida por roundtrip; horários inexistentes são ignorados e instantes repetidos continuam distintos, com offset explícito. As candidatas são ordenadas por UTC antes de selecionar as três primeiras. A duração é de minutos reais em UTC: atravessar uma transição pode fazer o fim civil aparentar um horário menor ou maior; os offsets identificam os instantes corretos.

O relógio UTC deve ser aware e não regressivo. O corte é atualizado após leitura **e encerramento do cliente**; não são oferecidos inícios anteriores a esse instante. O resultado é uma fotografia dessa consulta, não garantia de que um horário continuará livre na hora de uma futura execução.

## Ocupação, duração e margem

`confirmed` e `tentative` opacos bloqueiam. Transparência omitida equivale a `opaque`; `transparent` e cancelados não bloqueiam. Convites ocultos são incluídos na consulta, seguindo a política dos [conflitos existentes](conflitos.md). O cálculo não conhece a agenda de participantes, salas ou outras agendas e não garante disponibilidade pessoal fora do calendário vinculado.

Os intervalos usam fim exclusivo: um compromisso terminado às 10h permite começar às 10h, se a margem for zero. Intervalos ocupados sobrepostos/adjacentes são unidos antes da procura. A margem expande cada ocupado para ambos os lados; não altera duração ou faixa do compromisso proposto, nem estabelece tempo de deslocamento. A consulta também se estende antes/depois das bordas para enxergar vizinhos que só conflitam por causa da margem. Por exemplo, um compromisso terminado às 08h50 bloqueia início às 09h com margem de 15 minutos, mesmo estando fora da faixa original.

Dia inteiro é convertido pelo adaptador para fronteiras civis no fuso da agenda, com fim exclusivo. O primeiro instante válido do dia cobre gaps de meia-noite; um fold usa a fronteira UTC mais cedo. Dias de 23/25h são respeitados. Um dia eliminado inteiro não ganha uma fronteira fabricada. Recorrências vêm expandidas com ID próprio, sem interpretação de RRULE ou exclusão da série.

As opções são **alternativas**, portanto podem se sobrepor entre si, por exemplo 09h–09h30 e 09h15–09h45. Não são três reservas simultâneas. A parte 4 não aceita `exclude_event_id`; excluir a ocorrência que será movida exige identificação/releitura pelo servidor e pertence à parte 5. Selecionar `S1` não autoriza uma alteração.

## Fonte completa e saída mínima

`GoogleAvailabilityCalendarGateway` é um adaptador novo. Lê somente ID, estado, datas/horários, transparência e metadados de ocorrência; não solicita título, descrição, local, participantes ou links. O serviço recebe intervalos UTC normalizados e DTOs privados. O conector CRUD, a coleta de resumos, os modelos e os fluxos existentes não foram alterados.

Até **20 páginas lógicas e 2.000 itens brutos**, contando duplicados, cancelados, transparentes e fora da faixa. Exatamente o limite é aceito somente em página terminal. Página vazia com token válido é seguida; token repetido, formato inválido, cópia divergente de ID, intervalo/offset/transparência desconhecidos, falha tardia ou limite com paginação pendente abortam a leitura inteira. Não há prefixo parcial transformado em disponibilidade. O SDK conserva até duas retentativas controladas de leitura por chamada; páginas não são contagem de tentativas HTTP.

O vínculo efetivo é relido antes de listar e precisa manter ID/fuso; agenda principal, acesso sem propriedade, ausência ou drift são recusados. A descoberta/OAuth existente pode renovar token/metadados locais, sem escrever eventos. `complete: true` significa paginação terminal validada dentro desses limites, não um snapshot transacional ou uma garantia contra alterações externas posteriores.

O envelope usa `action: find_free_slots`, `operation_id: null`, `mutation_state: not_applicable` e `execution_allowed: false`. Os dados incluem request, período/fuso, instante de observação, até três slots `S1…S3`, `count`, `limit: 3`, `more_available`, `scope: authorized_calendar_only` e `reservation_created: false`. `period` delimita a extensão geral; somente a faixa diária de `request` é elegível. Não se trata de uma faixa contínua livre entre a primeira e a última data.

`more_available` significa que foi encontrada pelo menos uma quarta opção; não é o total de todos os horários livres. Nenhum ID da agenda/ocupados, título ou intervalo ocupado é devolvido. As referências S são locais à resposta, não IDs de evento nem comandos executáveis.

| Situação | Resultado |
|---|---|
| Leitura completa e opções futuras | HTTP 200 com até três opções e indicação de outras |
| Nenhuma opção futura na faixa | HTTP 200, lista vazia; não afirma indisponibilidade global |
| Entrada inválida ou fronteira civil impossível/ambígua | HTTP 422, `invalid_request`, sem opções |
| Setup indisponível | HTTP 503, `calendar_setup_unavailable` |
| Limite de coleta | HTTP 503, `availability_limit_exceeded` |
| Fonte incompleta/inválida ou falha operacional | HTTP 502, `availability_collection_failed`, `data: null`; nenhum horário declarado livre |

## Verificação e prova real

Passaram **2.225 testes Python, 99 novos**, sem casos ignorados: 34 do serviço, 35 do adaptador/SDK, 18 da API ASGI, oito da CLI e quatro percursos compostos com transporte Google fictício. A suíte completa também verifica as operações anteriores. As 981 verificações JavaScript permanecem históricas, sem rerun; fontes e cinco exports foram preservados. A revisão independente aprovou 12 grupos de probes, incluindo 150 cenários comparados a um cálculo independente em UTC e transições de Nova York/Lord Howe/São Paulo/Havana/Apia. Registro 145.

Separadamente, duas execuções da CLI instalada consultaram **27/10/2026, 09h–12h, duração de 30 minutos**, no fuso efetivo `America/Recife`, com margens zero e 15 minutos. Cada leitura retornou três alternativas e indicação de mais opções. As **seis opções foram revalidadas**, incluindo a margem correspondente, pelo verificador de conflitos legado inalterado: todas ficaram livres naquele momento. São seis verificações lógicas, não contagem de todos os requests HTTP. Nenhum evento artificial foi criado para produzir ocupação; os casos ocupados e transições foram exercitados com fixtures. Registro 146 guarda somente resultado mínimo/opções, sem campos de eventos ocupados ou resposta bruta do Google.

Não houve IA, reserva, escrita de evento, mensagem, publicação ou restart do processo API persistente para esse ensaio. A rota foi testada em ASGI; o processo antigo precisa do restart normal `python -m app` para carregar o endpoint novo. A CLI foi validada instalada, e a integração das novas intenções ao WhatsApp continua na parte 8.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*availability*.py'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_find_free_slots.py'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

Foram conferidos os hashes das 239 fontes testadas, dos conectores/modelos anteriores e dos cinco exports. No ensaio real, os hashes privados conferidos limitam-se a `.env`, JSONs `whatsapp-*.json` e `reminders-n8n-local.json`; bancos/token OAuth podem mudar pelos serviços existentes e não são declarados congelados. Os 144 registros anteriores e espelhos ficam preservados. Não foi criado print sem nova interface; JSON comprova este marco. O PDF será consolidado no fechamento da etapa 13.

**Parte 4/10 concluída.** O bloco B já confirmou a rota por HTTP real 200 com três alternativas e integrou o pedido ao WhatsApp. O usuário confirmou os horários recebidos antes da simplificação visual; o formato final passou por leitura real instalada, sem novo envio. Isso não cria reserva ou prova uma nova sugestão vinculada a evento. [Provas atuais](preparo-whatsapp.md). A sequência restante segue reduzida no [plano de entrega de hoje](plano-entrega-hoje.md), com sugestão específica de reagendamento adiada e fechamento pendente.
