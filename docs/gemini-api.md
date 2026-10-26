# Gemini pela API

Em 25/10/2026, a chave salva no `.env` foi lida e o Gemini 3.5 Flash-Lite respondeu a dois pedidos reais com conteúdo fictício. Um validou texto; o outro produziu um plano estruturado de criação com dados faltantes, retornando `needs_clarification` e `execution_allowed: false`. Nenhum dos dois acessou a agenda ou enviou WhatsApp. Registro 113 no [índice](evidencias.md).

O teste inicial do Gemini 3.8 Flash teve timeout; a segunda tentativa recebeu HTTP 503/`UNAVAILABLE`. A consulta de metadados desse modelo respondeu 200. A seleção local passou explicitamente para `gemini-3.5-flash-lite`; não existe troca automática de provedor/modelo em caso de erro. A OpenAI permanece como adaptador opcional, com a limitação de créditos já registrada nos marcos anteriores.

## Configuração privada

```dotenv
LLM_PROVIDER=gemini
GEMINI_API_KEY=<CHAVE_PRIVADA>
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_MAX_OUTPUT_TOKENS=2048
LLM_TIMEOUT_SECONDS=30
```

A chave pertence ao projeto Google selecionado no AI Studio e fica somente no `.env`, ignorado pelo Git. Reinicie a API depois de mudar a configuração. O exemplo público mantém o valor vazio. `Settings` usa `SecretStr` e exclui o segredo da representação e serialização; erros de validação não reproduzem o valor.

`LLM_PROVIDER` aceita `openai` ou `gemini`; ausência conserva `openai` por compatibilidade. O padrão interno de `GEMINI_MODEL` é `gemini-3.8-flash`; a configuração local acima seleciona o modelo efetivamente validado. `GEMINI_MAX_OUTPUT_TOKENS` aceita inteiro de 128 a 4096. Os limites comuns de mensagem/timeout continuam em [LLM API](llm-api.md). Uma chave Gemini ausente ou rejeitada não libera chamadas OpenAI.

## Adaptador e contratos

[`app/language_model.py`](../app/language_model.py) seleciona o adaptador para prévia, consultas e diálogo. [`app/gemini_llm.py`](../app/gemini_llm.py) implementa os mesmos resultados e validadores do contrato existente. Os serviços de agenda, o contexto, as confirmações e os executores continuam independentes do fornecedor.

Cada chamada faz um único POST HTTPS a `generativelanguage.googleapis.com`, com modelo validado no caminho e chave somente no cabeçalho `x-goog-api-key`. Não herda proxies, segue redirecionamentos ou repete automaticamente. Conexão: 5 segundos; leitura: valor configurado, sem promessa de prazo absoluto da resposta inteira. O transporte limita o tamanho do retorno e converte falhas em códigos seguros, sem erro bruto ou segredo.

Consultas/diálogo enviam ferramentas nativas com schemas da aplicação e seleção restrita à lista permitida. Apenas um candidato concluído e uma chamada de ferramenta válida podem virar plano. Argumentos extras, tipos inválidos, vários planos, truncamento e bloqueios de segurança falham antes do executor. Partes de raciocínio/assinaturas opacas não viram resposta, histórico ou autorização. O ajuste `thinkingLevel: LOW` é aplicado somente ao modelo 3.8 Flash, sem enviar essa opção a modelos diferentes.

## Faixa gratuita e dados

O Google lista entrada/saída gratuitas para o 3.5 Flash-Lite, sujeitas à disponibilidade e às cotas do projeto. O teste não ativou faturamento, comprou créditos ou fez inscrição em plano pago. O código não consegue garantir a condição de faturamento da conta; confira o projeto no AI Studio antes de mudar seu plano.

Na faixa gratuita, o conteúdo pode ser usado para melhorar os produtos Google. Por isso, as primeiras validações usaram dados fictícios. O bot envia somente a mensagem e o contexto mínimo necessário ao plano; não envia telefone, chaves Google/360dialog ou credenciais. Evite dados sensíveis e confira as condições do projeto antes de uso além da demonstração.

Fontes oficiais consultadas em 25/10/2026: [chaves](https://ai.google.dev/gemini-api/docs/api-key), [modelo](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite), [preços e uso de dados](https://ai.google.dev/gemini-api/docs/pricing) e [generateContent](https://ai.google.dev/api/generate-content).

## Verificações

Os 25 testes do adaptador usam transporte substituído e conferem chave no cabeçalho, destino fixo, schemas, parsing estrito, falhas e ausência de retries. Sete testes da seleção verificam os provedores e a ausência de fallback. A suíte completa passou com 1.508 testes Python. A prova real dos dois HTTP 200 é separada desses testes locais; não comprova conversas completas ou escrita real na agenda.


Atualização da parte 11: [conversas e falhas verificadas](testes-conversas-agente.md), com nove ajustes e 1.561 testes aprovados. As mutações conversacionais deste marco usaram agenda fictícia; a prova WhatsApp humana da parte 10 permanece histórica. O ledger de exclusões aguarda bloqueios SQLite por até 5 s, sem repetir escrita; prévia definitivamente recusada no transporte é descartada antes de liberar outro turno.
