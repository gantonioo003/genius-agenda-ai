# Proteção da API — etapa 09, parte 2

A API usa uma chave Bearer configurada em `API_AUTH_TOKEN`. A chave real fica no `.env` privado, ignorado pelo Git. `.env.example` publica somente o nome da configuração, com valor vazio. Nenhuma credencial é incluída nos exemplos, respostas, logs ou esquema OpenAPI.

## Rotas e comportamento

| Requisição | Resultado |
|---|---|
| `GET /health`, sem chave | Saúde básica pública, com o formato anterior |
| `GET /api/v1/status`, sem chave ou com credencial inválida | HTTP 401, desafio `WWW-Authenticate: Bearer` e erro seguro |
| `GET /api/v1/status`, com a chave configurada | HTTP 200, `authenticated: true` e nome do serviço |
| `GET /api/v1/events` e `GET /api/v1/events/{event_id}` | Mesmo Bearer obrigatório; consultas implementadas na parte 3 |
| `POST /api/v1/events` | Mesmo Bearer obrigatório; criação com corpo estrito implementada na parte 4 |
| `PATCH /api/v1/events/{event_id}` e `PATCH /api/v1/events/{event_id}/schedule` | Mesmo Bearer obrigatório; alterações com corpo estrito implementadas na parte 5 |
| `POST /api/v1/events/{event_id}/deletion-preview` e `/delete` | Mesmo Bearer obrigatório; código da versão exigido no executor |
| Rota protegida sem chave configurada no servidor | HTTP 503; acesso fechado |

`/api/v1/status` verifica somente a autenticação da aplicação. Não consulta o Google Calendar nem comprova conexão com a agenda. A inicialização da aplicação continua preparando seu banco configurado; o handler de status não consulta eventos.

As futuras operações devem ser adicionadas ao router produzido por `create_api_router()`, que aplica a dependência de segurança a todas as rotas registradas nele. Caminhos desconhecidos, métodos não suportados e redirecionamentos mantêm o comportamento do roteador; não representam acesso às operações protegidas.

## Configurar em outro ambiente

Copiar `.env.example` para `.env` se o arquivo ainda não existir. Para gerar uma chave privada sem escrevê-la no terminal, executar uma vez na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; from dotenv import set_key; set_key('.env', 'API_AUTH_TOKEN', secrets.token_urlsafe(32))"
```

O comando troca a chave se for executado novamente. Após uma troca, reiniciar a API e atualizar a credencial do cliente que a utiliza. As outras configurações do `.env` permanecem preservadas.

O cliente envia `Authorization: Bearer <valor privado de API_AUTH_TOKEN>`. A chave não é aceita na query nem no corpo. Quando conectarmos o n8n, ela ficará em uma credencial privada; não deve ser colocada em parâmetros exportados do workflow. O uso remoto exige transporte HTTPS; esta etapa mantém o servidor no endereço local já configurado.

## Validação e respostas seguras

A configuração aceita chaves de 32 a 128 caracteres ASCII entre letras, números, `_` e `-`, compatíveis com `secrets.token_urlsafe(32)`. O valor é preservado exatamente. O modelo usa `SecretStr`, oculta a entrada nas mensagens de validação e exclui o campo de representações/exportações comuns.

Essa proteção não torna segura qualquer serialização de exceções: não registrar `ValidationError.json()` ou `.errors()` com os padrões que incluem a entrada. Quando uma análise estruturada for necessária, omitir a entrada com `include_input=False` e selecionar somente campos de erro seguros. O código atual não publica essas exceções.

A comparação da credencial usa `secrets.compare_digest`. Cabeçalhos `Authorization` duplicados, esquema diferente de Bearer, credencial vazia, incorreta ou com dados extras não autenticam a chamada. A validação não normaliza a chave nem imprime o valor recebido.

O identificador `X-Request-ID` segue o [contrato v1](contrato-api-v1.md). Quando ausente, o servidor gera um UUID. Quando inválido ou duplicado, usa um UUID seguro na resposta e devolve erro 422 após autenticar, sem repetir o valor malformado. Respostas de segurança usam o envelope comum, sem corpo recebido, detalhes internos ou `str(exception)`.

A autenticação não deve ser confundida com autorização OAuth do Google. Ela protege a chamada entre cliente e Python; não implementa cadastro de usuários, autorização por pessoa, consentimento da conversa ou integração WhatsApp.

As consultas da parte 3 usam o mesmo router protegido e um handler de validação que seleciona somente nomes de campos seguros. No comando `python -m app`, o log de acesso está desativado para não registrar URLs de consulta; ao usar outro comando/servidor, aplicar a mesma configuração.

As rotas de criação, alterações e exclusão tratam erros do JSON e da validação sem expor o conteúdo recebido. O framework pode analisar JSON antes de executar dependências; por isso, autenticação anterior ao parsing não é garantida. Nenhum desses erros chama o conector. As futuras rotas com corpo devem manter esse tratamento.

## Verificação histórica da parte 2

As rodadas completas na preparação isolada e no repositório aprovaram **372 testes**, incluindo **13 novos de segurança**. Os casos percorrem ASGI, middleware, router, dependências e handlers reais, com chave fictícia e banco injetado. Cobrem também OpenAPI, configuração sigilosa, correlação inválida, duplicação de headers, Unicode, ausência de chave e proteção herdada por futuras rotas. Não houve nova dependência.

O runner bloqueou conexões da aplicação, com uma exceção restrita ao `socketpair` de loopback que a biblioteca padrão usa para sinalização interna do asyncio no Windows. Foram 41 conexões internas por rodada e zero tentativas de conexão da aplicação. Isso não representa chamadas HTTP ou acesso ao Google.

Também iniciamos o servidor FastAPI/Uvicorn real em `127.0.0.1`, com porta temporária e SQLite de teste. As chamadas retornaram **200 no health público, 401 sem chave, 401 com chave incorreta, 200 com chave correta e 422 com correlação inválida após autenticação**. O OpenAPI exigiu Bearer na rota de status, sem publicar a chave. O servidor temporário foi encerrado ao final.

A chave privada foi gerada uma única vez no `.env`, sem impressão. Cliente OAuth, token Google e banco real foram preservados. O registro **050** guarda os testes locais; **051** guarda as respostas HTTP reais do servidor local. Nenhum desses registros é operação na conta Google ou conexão com n8n. Não foi necessária captura de interface: os códigos HTTP e respostas registradas demonstram o resultado.

Se a API já estava aberta antes da alteração, reiniciá-la para carregar o novo código e a chave. A parte 3 já disponibilizou as consultas de eventos, com 383 testes aprovados e conferência HTTP/Google real; consulte o [guia de consultas](consultas-api.md).

A [criação HTTP](criacao-api.md) foi concluída na parte 4, com 402 testes aprovados e registro real de uma criação, reutilização e conflito, sem publicação de credenciais.

As [alterações HTTP](alteracoes-api.md) foram concluídas na parte 5, com 424 testes aprovados e edição/reagendamento reais limitados à fixture da API. Os snapshots de edição incluem descrição e local necessários à operação; consultas gerais e snapshots de reagendamento mantêm somente os campos mínimos.

A [exclusão HTTP](exclusao-api.md) foi implementada na parte 6, com 444 testes aprovados e prévia/bloqueios conferidos no Google real. O código de versão aparece somente na prévia bem-sucedida, não é credencial e não comprova consentimento humano. Esse consentimento deve ser coletado antes do executor e receber persistência na integração futura.
