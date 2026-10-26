# Conexão do modelo pela API — etapa 11, parte 2

**Histórico OpenAI:** implementação local validada em 24/10/2026; a chave real retornou `429 credit_balance_exhausted` e esse fornecedor continua sem inferência bem-sucedida registrada. **Atualização em 25/10/2026:** o usuário escolheu Gemini; o adaptador independente respondeu a dois testes reais fictícios e o fluxo WhatsApp passou ao agente. [Configuração Gemini e limites](gemini-api.md) · [Transporte WhatsApp](whatsapp-agente.md). O restante deste guia descreve o contrato e o adaptador OpenAI original.

## Implementação

| Arquivo | Responsabilidade |
|---|---|
| [`app/llm.py`](../app/llm.py) | Interface `LanguageModel`, resultado mínimo `ModelReply`, erros seguros e adaptador REST `OpenAIResponsesModel`. |
| [`app/services/agent_preview.py`](../app/services/agent_preview.py) | Prévia em português, sem histórico, ferramentas ou dependência dos serviços de calendário. |
| [`app/api_agent.py`](../app/api_agent.py) | Rota protegida, validação antes de construir o modelo e envelope sem permissão de execução. |
| [`app/settings.py`](../app/settings.py) | Chave secreta opcional, modelo e limites de transporte/entrada/saída. |
| [`scripts/test_llm.py`](../scripts/test_llm.py) | Prévia local sem rede por padrão; uma chamada explícita com pedido fictício fixo usando `--execute`. |

O serviço depende do protocolo `LanguageModel`, não da classe OpenAI. Trocar o adaptador não altera criação, consulta, edição, reagendamento ou exclusão. Não foram acrescentados SDK, LangChain ou outras dependências: o adaptador usa `requests`, já fixado no projeto. A prévia conserva seu modo sem ferramentas. A [parte 3](consultas-agente.md) acrescentou separadamente planos estruturados e ferramentas de consulta; as escritas do agente continuam para as partes seguintes.

## Configuração privada

No `.env` da raiz do projeto:

```dotenv
OPENAI_API_KEY=<CHAVE_PRIVADA>
LLM_MODEL=gpt-5-mini
LLM_TIMEOUT_SECONDS=30
LLM_MAX_OUTPUT_TOKENS=600
LLM_MAX_INPUT_CHARS=4000
```

O exemplo público mantém a chave vazia. Nunca envie o valor pelo chat, em screenshot, query, argumento de terminal ou arquivo versionado. `Settings` usa `SecretStr`, exclui a chave de sua representação/serialização e não a reproduz em mensagens de validação. Reinicie a API após alterar o `.env`; a CLI lê a configuração em cada execução.

O padrão `gpt-5-mini` é configurável e tem suporte documentado a Responses e ferramentas; sua disponibilidade na conta precisa ser confirmada por uma chamada real. Não é uma promessa de acesso, saldo ou custo fixo. Não há escolha automática de outro modelo em caso de falha.

Os limites aceitos são: timeout de leitura entre 5 e 60 segundos, saída entre 128 e 2048 tokens e entrada entre 1 e 4000 caracteres. O identificador do modelo é ASCII, com até 100 caracteres e sem URL. Limites inteiros vindos do `.env` aceitam dígitos ASCII e passam pela mesma validação de faixa, sem conversão silenciosa de booleanos ou decimais.

## Prévia autenticada

`POST /api/v1/agent/preview`, com a chave Bearer da própria API e `Content-Type: application/json`:

```json
{
  "message": "Quero marcar uma entrevista fictícia, mas ainda não defini a data e o horário. O que preciso informar?"
}
```

Não aceita query, campos extras, chaves JSON repetidas, mensagem vazia ou Unicode inválido. O corpo bruto é limitado a 24 KiB; a mensagem segue o limite configurado. A validação ocorre antes da factory do serviço/modelo. A chamada síncrona roda em worker, sem bloquear o event loop durante o transporte.

Em sucesso, a API retorna `action: agent_preview`, `status: succeeded`, `mutation_state: not_applied`, `operation_id: null` e:

```json
{
  "reply": "<TEXTO_DA_PREVIA>",
  "model": "<MODELO_RETORNADO_PELO_PROVEDOR>",
  "input_tokens": null,
  "output_tokens": null,
  "execution_allowed": false
}
```

Esse é um formato ilustrativo, não a captura de uma resposta real. Os contadores vêm do provedor quando disponíveis; ausência permanece `null`, sem inventar zero. O nome retornado pode ser o snapshot correspondente ao alias configurado. A mensagem externa do envelope informa que nenhuma operação de agenda foi executada; o texto gerado é uma prévia e não comprova fatos sobre a agenda.

| HTTP / código | Significado |
|---|---|
| `401 unauthorized` | Bearer da própria API ausente ou incorreto; modelo não é construído. |
| `422 invalid_request` | Corpo/query/mensagem inválidos; modelo não é construído. |
| `503 llm_not_configured` | Chave do modelo ausente. `/health` e as operações existentes continuam independentes dela. |
| `503 llm_credentials_rejected` | Provedor respondeu 401/403; conferir a chave e as permissões privadas. |
| `503 llm_rate_limited` | Provedor respondeu 429; conferir limite/saldo na conta, sem repetir automaticamente. |
| `504 llm_timeout` | Timeout do transporte; não há nova tentativa automática. |
| `502 llm_invalid_response` | Resposta incompleta, recusa, chamada de ferramenta, JSON/UTF-8 inválido ou conteúdo fora do contrato. |
| `502 llm_unavailable` | Outras falhas operacionais, com mensagem segura. |

Todos os erros têm `retryable: false`. Detalhes brutos do provedor, chave e mensagem de entrada não são devolvidos em erros. Nenhum resultado desta rota autoriza criar/alterar/excluir eventos ou enviar mensagens.

## Transporte e privacidade

O adaptador realiza um único POST ao destino fixo `https://api.openai.com/v1/responses`, com verificação TLS, sem proxy herdado do ambiente, sem redirecionamentos e sem retries. A conexão tem timeout de 5 segundos; a leitura usa o valor configurado. Esses timeouts não são um prazo global absoluto para toda a resposta.

Envia somente instruções fixas, mensagem atual, modelo, limite de saída e `store: false`. Não envia telefone, chave Google/360dialog, dados da agenda, identificador de conversa ou `previous_response_id`; a chave OpenAI vai apenas no header do destino fixo. Não há ferramentas, armazenamento de conversa ou histórico oculto entre chamadas.

`store: false` desativa a persistência de estado da resposta para recuperação pela API; **não significa retenção zero de todos os dados pelo provedor**. O texto enviado continua sujeito às políticas e configurações da conta. Não registre prompts ou respostas privadas em logs/exports/evidências públicas.

A resposta externa tem limite de 128 KiB e exige JSON UTF-8 estrito, status completo e texto final de assistente. Limite de texto: 8000 caracteres. Ferramentas retornadas indevidamente, recusas, estados incompletos e resultados incoerentes não viram sucesso. Os erros usam uma lista fechada; o serviço valida também o resultado de modelos injetados.

## Reprodução

Na raiz do projeto, mostrar o pedido fictício sem ler a chave nem fazer rede:

```powershell
.\.venv\Scripts\python.exe scripts/test_llm.py
```

Após configurar a credencial, executar uma tentativa real com o mesmo pedido fictício, sem WhatsApp ou Google:

```powershell
.\.venv\Scripts\python.exe scripts/test_llm.py --execute
```

Testes locais com modelos/transporte substituídos e bases temporárias:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_llm_transport.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agent_preview.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_llm_cli.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Resultado deste marco

Passaram **718 testes Python**, sem falhas, erros ou casos ignorados: os 664 anteriores e 54 novos (25 de transporte, 24 de serviço/API/configuração e cinco da CLI/reprodução). A suíte completa rodou numa cópia isolada sem `.env`, credenciais ou bancos reais. Modelos/Google/360dialog foram substituídos nos testes; isso não é uma chamada real a esses provedores. Os exports e as bibliotecas n8n permanecem inalterados; as 502 verificações JavaScript anteriores não foram repetidas.

O primeiro ensaio local encontrou a leitura estrita de inteiros do `.env`; foi corrigida e incluída na suíte. Dois testes de subprocesso inicialmente estavam com diretório de execução incorreto no runner isolado; o runner foi corrigido, sem alteração desses testes/serviços. A rodada final aprovou todo o conjunto.

A ausência inicial de `OPENAI_API_KEY` foi conferida apenas por indicador booleano, sem imprimir valores de configuração. O usuário está sendo orientado a criar a chave na própria conta. **A parte 2 permanece aguardando a validação real após configurar a credencial.** Não foram enviados eventos/telefones ao modelo nem feitas operações Google ou envios WhatsApp. Registro local: `094_llm_api_testes_locais.json`, no [índice de evidências](evidencias.md). Não foi criado print do formulário de chave como prova de conexão.

## Fontes oficiais conferidas em 24/10/2026

- [Criação e armazenamento da chave](https://developers.openai.com/api/docs/quickstart).
- [Geração de texto e Responses API](https://developers.openai.com/api/docs/guides/text).
- [Modelo GPT-5 mini e capacidades](https://developers.openai.com/api/docs/models/gpt-5-mini).
- [Controles de dados e retenção](https://developers.openai.com/api/docs/guides/your-data).
- [Permissões da plataforma](https://developers.openai.com/api/docs/guides/rbac).

## Evolução do contexto de consultas

A descrição de prévia sem histórico acima continua válida para `/agent/preview`. A [parte 4](contexto-conversa.md) acrescenta memória mínima explícita somente a `/agent/query`: último período, fusos e existência de seleção podem entrar nas instruções. Não envia o ID persistido do Google, UUID da conversa ou transcrições anteriores; o texto atual continua sujeito às políticas do provedor. `store: false` e a ausência de estado remoto permanecem. A validação real do modelo ainda aguarda a chave.

## Diálogo preparatório e teste de conexão — parte 5

`plan_dialogue` compartilha o transporte existente e usa quatro ferramentas próprias, campos anuláveis estritos e argumentos de até 24 KiB. A prévia e os planos de consulta conservam seus contratos. O [guia de esclarecimentos](esclarecimentos-agente.md) descreve o resumo enviado, a persistência local e as validações, sem executor de escrita.

O registro 101 distingue a chave configurada de uma inferência concluída: três requisições reais limitadas e manuais, todas 429, com diagnóstico `credit_balance_exhausted`; nenhum evento Google ou envio WhatsApp. O erro público continua `llm_rate_limited`, sem detalhes privados. Créditos esgotados exigem corrigir o saldo antes de outro teste; não ativamos retries. Consulte a [documentação oficial de erros](https://developers.openai.com/api/docs/guides/error-codes). Não é necessário trocar a chave apenas por esse retorno.
