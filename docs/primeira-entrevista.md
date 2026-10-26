# Primeira entrevista

## Estado

**Etapa 06 concluída em 21/10/2026.**

Passos concluídos: acesso à agenda confirmado, dados definidos, função Python implementada, primeira criação real executada e conferência visual realizada. Em 21/10/2026, o Google confirmou o evento na agenda `Genius Testes`, retornando ID e link, com `reused: false`; os dados também foram conferidos na interface.

Os 24 testes locais, incluindo 15 de criação com conector simulado, foram executados separadamente da validação real.

## Fechamento dos sete passos

- [x] Confirmar o acesso do Python à agenda `Genius Testes`.
- [x] Definir título, data, horário, duração, fuso e ausência de convidados.
- [x] Implementar o serviço de criação independente da IA e o conector Google.
- [x] Executar a criação real com `python -m app.create_test_interview --execute`.
- [x] Conferir o retorno, o identificador e o link no terminal; reler o evento pelo ID.
- [x] Conferir título, data, horário e duração na interface do Google Agenda.
- [x] Atualizar o histórico do README, os comandos, os resultados e as evidências visuais.

## Conferência pelo identificador

Após a criação, consultamos o evento pelo ID em uma chamada real `events.get`. O Google retornou `status: confirmed`; o título e os horários foram comparados com os dados aprovados. A confirmação, o identificador e o link foram exibidos no terminal. Essa verificação foi somente de leitura.

## Conferência visual

Após o login no navegador, abrimos os detalhes do evento no Google Agenda. Conferimos o título aprovado, 22/10/2026, das 15h às 15h30 (GMT−03), duração de 30 minutos, agenda `Genius Testes` e visibilidade particular. O print foi preservado como registro 006 do [acervo de evidências](evidencias.md).

## Dados aprovados

| Campo | Valor |
|---|---|
| Agenda | `Genius Testes`, selecionada pelo ID fixado ou configurado |
| Título | Entrevista de estágio — Empresa Exemplo (teste) |
| Início | `2026-10-22T15:00:00-03:00` |
| Fim | `2026-10-22T15:30:00-03:00` |
| Duração | 30 minutos |
| Fuso | `America/Recife` |
| Descrição | Entrevista fictícia para testar o agendamento pelo Genius Agenda AI. |
| Convidados | Nenhum |

## Implementação

- `app/services/interviews.py`: dados da entrevista, validações básicas e serviço com conector injetado; não importa Google, n8n ou bibliotecas de IA.
- `app/google_calendar.py`: reutiliza o OAuth e a seleção segura da agenda; consulta um ID estável e usa `events.insert` quando o evento ainda não existe.
- `app/create_test_interview.py`: contém os dados aprovados. Sem argumentos, apenas mostra a prévia; a escrita exige `--execute`.

O serviço exige título, fuso, início/fim com deslocamento UTC, fim posterior ao início e identificador de operação. O conector confirma a propriedade da agenda e rejeita a principal antes de escrever. O evento é preparado com visibilidade privada, sem convidados e sem lembretes padrão do Google. Os lembretes pelo WhatsApp serão implementados depois.

## Comandos

Na raiz do projeto, conferir os dados sem rede, credenciais ou escrita:

```powershell
.\.venv\Scripts\python.exe -m app.create_test_interview
```

O resultado esperado é `status: preview`. Com a conta já autorizada, o comando utilizado para a criação real foi:

```powershell
.\.venv\Scripts\python.exe -m app.create_test_interview --execute
```

O segundo comando pode gravar na agenda de teste e já foi executado com sucesso. Ele consulta o ID estável, reutiliza um evento correspondente e tenta criar se receber HTTP 404. Não o reexecute apenas para conferir o evento. O sucesso só é informado após resposta válida do Google e inclui ID do evento, link, nome da agenda e indicação de reutilização de uma criação anterior. Não é necessário iniciar a API nem o Docker para executar estes comandos Python.

Retorno da primeira execução, resumido aos campos de confirmação:

```json
{
  "status": "ok",
  "calendar_name": "Genius Testes",
  "reused": false
}
```

O ID e o link também foram retornados e exibidos no terminal. `reused: false` descreve essa primeira criação; uma repetição que encontre o mesmo evento retorna `reused: true`. A releitura real por ID confirmou `status: confirmed`.

## Repetição e falhas

O identificador da operação de teste é `genius-primeira-entrevista-2026-10-22-v1`; dele é derivado o ID enviado ao Google. Uma repetição consulta esse ID antes de tentar inserir. Um evento existente precisa corresponder ao pedido e aos dados esperados; evento alterado, cancelado ou conflito de dados produz erro.

O POST de criação não recebe retentativas automáticas. Em caso de timeout, o resultado é tratado como indeterminado: o Google pode ter gravado antes de a resposta se perder. Repita com o mesmo identificador para consultar o resultado; não gere outro ID para a mesma operação. Uma corrida com conflito HTTP 409 também é resolvida por consulta ao ID existente.

Essa proteção é inicial e vale para a mesma operação na agenda selecionada. O controle completo de mensagens, retentativas persistentes e lembretes ainda será construído. No marco da etapa 06, ainda não havia detecção de conflitos nem a regra compartilhada de fusos. Na etapa 08, acrescentamos [datas e fusos](datas-fusos.md) e [conflitos antes de criar/reagendar](conflitos.md). Participantes e criação de recorrência continuam fora deste comando. A validação da primeira criação incluiu testes locais, resposta real da API, releitura pelo ID e conferência visual no Google Agenda.

Referências: [criar eventos](https://developers.google.com/workspace/calendar/api/guides/create-events) e [contrato de events.insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert).


**Atualização na etapa 08, parte 6:** uma resposta de escrita perdida ou inválida agora é conferida por leitura do mesmo ID. O resultado inclui `recovered: true` quando essa leitura confirma o estado esperado; se a conferência falhar, permanece um erro seguro, sem nova escrita automática. Os registros e limites estão no [guia de falhas e recuperação](falhas-recuperacao.md).
