# Painel web — acesso local, agenda e preparo

Acesso protegido e consultas integrados na fase 3, em 27/10/2026; **CRUD com confirmação integrado na fase 4, em 28/10/2026**. O painel consulta a agenda e os lembretes e também permite criar, editar, reagendar e excluir usando os serviços existentes. A fase 5 conecta resumos, horários livres e briefing/checklist à área [Preparo](painel-web-preparo.md). A fase 6 acrescenta acompanhamento dos avisos e prévia privada dos cards. O WhatsApp permanece disponível e usa a mesma agenda configurada. [Fluxo da agenda e limites de validação](painel-web-agenda.md).

## Abrir e autorizar o navegador

Com a configuração privada e as credenciais existentes do projeto já preparadas, inicie a API na raiz do repositório, caso ela ainda não esteja em execução:

```powershell
.\.venv\Scripts\python.exe -m app
```

1. Abra <http://127.0.0.1:8000/painel/> no próprio computador.
2. Clique em **Acessar minha agenda**. A tela apresenta um código público válido por até cinco minutos.
3. No terminal local, na pasta do projeto, execute o comando mostrado pela tela. Exemplo fictício:

   ```powershell
   .\.venv\Scripts\python.exe -m app.panel_access authorize ABCD-EFGH
   ```

4. Volte à aba: ela confere a autorização e carrega as consultas. **Sair** revoga o acesso desse navegador e limpa os dados exibidos.

O proprietário deve aprovar somente o código observado na própria aba. O código não permite acesso pela API; a aprovação depende do terminal com acesso ao banco privado do projeto e se vincula à sessão que originou aquele código. Depois da aprovação, o código é consumido. Um código expirado exige iniciar o pareamento novamente.

Use o mesmo endereço durante o acesso: `localhost` e `127.0.0.1` são origens diferentes para o navegador. A porta do exemplo é a padrão da API; se `API_PORT` tiver sido alterada, use a porta configurada.

## O que está conectado

| Área | Dados e comportamento atuais |
|---|---|
| Visão geral | Dia atual no fuso efetivo da agenda, próximos compromissos do período consultado e estados reais de leitura |
| Agenda | Dia, semana e mês; pesquisa no período; detalhes completos; criação, edição, reagendamento e exclusão com prévia e confirmação |
| Preparo | Abas Entrevista, Resumos e Horários livres: briefing/checklist factual com notas, resumo semana/mês e disponibilidade calculada |
| Lembretes | Filtros, paginação, estado persistido, motivos, histórico do registro e prévia privada dos cards disponíveis |

As datas de referência vêm do servidor e do fuso da agenda. Erros não são apresentados como dia livre. Informações conservadas após falha de atualização aparecem identificadas como desatualizadas. Requisições antigas não podem substituir os dados da seleção ou da sessão atual.

Criar, editar, reagendar e excluir exigem uma prévia persistida e uma confirmação explícita da mesma operação. Cancelar a prévia impede sua execução. Abrir ou atualizar uma tela continua sem chamar Gemini, alterar eventos, disparar lembretes ou enviar mensagens WhatsApp. As operações pelo WhatsApp continuam disponíveis.

## Rotas do navegador

Os arquivos da interface e a API são servidos na mesma origem. As consultas e os comandos privados exigem a sessão local aprovada; a chave de serviço usada por outras integrações não é uma credencial do frontend.

| Método e rota | Uso |
|---|---|
| `POST /painel/api/auth/start` | Iniciar pareamento com corpo JSON vazio `{}` e origem exata |
| `GET /painel/api/session` | Conferir sessão ausente, pendente ou aprovada |
| `POST /painel/api/auth/logout` | Revogar sessão; exige origem exata e proteção CSRF |
| `GET /painel/api/connection/status` | Conferir acesso e resposta da API, sem declarar prontidão dos provedores |
| `GET /painel/api/events` | Consultar `date` e `period=day`, `week` ou `month`; `time_zone`, se informado, deve corresponder ao fuso vinculado |
| `GET /painel/api/events/{event_id}` | Abrir detalhes do evento escolhido; alteração externa durante a leitura exige atualização |
| `GET /painel/api/reminders` | Consultar `states`, `limit` de 1 a 50 e `cursor`; o cursor conserva a referência da paginação |
| `GET /painel/api/reminders/{reminder_id}` | Consultar registro e histórico sanitizado do lembrete |
| `GET /painel/api/reminders/{reminder_id}/card-preview` | Reconstruir PNG privado com dados atuais do compromisso, sem publicar ou enviar a imagem |
| `POST /painel/api/agenda/preview` | Preparar criação, edição, reagendamento ou exclusão; inclui `request_id` estável e os dados do formulário |
| `GET /painel/api/agenda/{operation_id}` | Consultar o estado da operação deste navegador, sem repetir gravação |
| `POST /painel/api/agenda/{operation_id}/confirm` | Confirmar e executar somente a proposta persistida; corpo JSON vazio `{}` |
| `POST /painel/api/agenda/{operation_id}/cancel` | Cancelar a prévia pendente; corpo JSON vazio `{}` |
| `POST /painel/api/agenda/summary` | Resumo de `period=week` ou `month`, com `date` opcional; uma tentativa de IA e alternativa factual |
| `POST /painel/api/agenda/availability` | Até três alternativas com datas, faixa diária, duração e margem; sem IA ou reserva |
| `POST /painel/api/preparation/briefing` | Relê `event_id` e compõe briefing/checklist factual, com `user_context` opcional de até 1.200 caracteres |

Os identificadores usados nas rotas vêm das respostas autenticadas e ficam associados à seleção da interface. O navegador não acessa diretamente o SQLite nem recebe telefones, credenciais, vínculos privados de agenda ou identificadores internos de tentativas de envio. Texto externo é tratado como texto seguro.

O detalhe do lembrete diferencia fatos persistidos e previsão: **formato previsto** usa a antecedência e a configuração atual; não é prova do formato de uma mensagem antiga. **Aceito pelo provedor** significa aceite da tentativa, sem comprovar entrega ou leitura. Resultados incertos não oferecem reenvio automático.

## Lembretes e prévia privada

Os filtros da tela são **Pendentes** (`pending`, `reserved`, `sending`), **Envios aceitos** (`accepted`), **Atenção** (`blocked`, `uncertain`, `expired`) e **Todo o histórico**. Cancelados aparecem no histórico completo. A paginação e as contagens correspondem ao filtro consultado; um aviso cujo horário passou continua com o estado persistido, acompanhado dessa indicação. O detalhe mostra as transições registradas, seus motivos e horários.

**Visualizar card** é uma ação explícita disponível quando o formato previsto é card: antecedência de 60 minutos e cards habilitados na configuração atual. A rota exige a mesma sessão aprovada e origem válida das consultas, sem token CSRF por ser GET. O servidor lê o ledger em modo somente leitura, confere o vínculo da agenda e relê o compromisso por identificador. Remoção, cancelamento ou divergência de título, início ou vínculo bloqueiam a prévia; a tela conserva a alternativa em texto.

O PNG usa o título completo e o intervalo atuais na composição local sobre o fundo existente. O slogan **Prévia do compromisso** e o rodapé **Prévia • não enviada** identificam a reconstrução. Ela não recupera a imagem histórica nem comprova envio, entrega ou leitura. Não chama Gemini, gera novo fundo com IA, publica mídia ou envia WhatsApp. O arquivo temporário é removido após a composição, inclusive em falhas.

A resposta de imagem tem até 5 MiB, dimensões de 1.254 × 1.254 pixels, `Content-Type: image/png`, `Cache-Control: private, no-store`, `Vary: Cookie` e `X-Content-Type-Options: nosniff`. Erros são JSON sanitizado: registro/compromisso ausente (`404`), dados alterados (`409`), formato previsto em texto (`422`) ou composição indisponível (`503`). Falhas de leitura da agenda não são apresentadas como prévia válida. [Uso e validação da fase 6](painel-web-lembretes.md).

## Sessão e proteção local

- O identificador da sessão tem 256 bits aleatórios. Apenas seu resumo SHA-256 fica no banco privado `data/panel-access.sqlite3`; o valor do cookie não aparece na interface ou no comando de autorização.
- O cookie usa `HttpOnly`, `SameSite=Strict` e `Path=/painel`. A sessão aprovada expira após 30 minutos sem atividade ou oito horas no total, mesmo com atividade. Sair a revoga imediatamente.
- O HTTP deste procedimento é restrito a loopback no computador do proprietário. O cookie usa `Secure` quando servido por HTTPS. Este acesso local não é uma configuração de autenticação para publicação na Internet.
- Host e cliente devem ser locais, na porta configurada. Cabeçalhos de encaminhamento e tentativas de acesso por túnel são rejeitados nas rotas do painel; isso não altera o webhook WhatsApp existente.
- Verificações de origem, metadados da requisição e token CSRF protegem os POSTs autenticados. Iniciar pareamento, sair e confirmar/cancelar aceitam somente JSON vazio limitado; a prévia e os recursos de preparo aceitam formulários JSON estritos e limitados; há limites de frequência e de sessões pendentes.
- Respostas usam `Cache-Control: no-store` e `Vary: Cookie`, além dos cabeçalhos de proteção de conteúdo da interface. Nenhuma chave de API ou credencial de provedor é copiada para JavaScript, URL ou armazenamento persistente do navegador.
- Sessão ausente, origem inválida, limite atingido e armazenamento indisponível produzem erros sanitizados. Uma falha de autenticação limpa os dados privados da tela.

O acesso depende do controle do computador e do armazenamento privado. Não há cadastro de usuários, recuperação por email, controle por empresa ou isolamento entre clientes. Para hospedar ou vender como serviço, autenticação, HTTPS, autorização por usuário e operação precisam de uma fase própria.

## Leitura e efeitos locais

O leitor de lembretes abre o SQLite com `mode=ro`, usa transação de leitura e fecha a conexão. Não inicializa tabelas, cria índices, configura regras ou executa `tick`, `dispatch` ou `recover`. Os GETs do painel não mudam o ledger de lembretes.

Isso não significa ausência de toda escrita local: o banco de acesso registra sessões e atividade. O conector Google existente também pode renovar credenciais OAuth e atualizar metadados locais do vínculo da agenda durante sua resolução. Essas leituras e atualizações de acesso não criam, editam ou excluem compromissos no Google Calendar. Uma operação confirmada pelo formulário pode gravar no Google, por meio dos executores existentes; prévia e confirmação possuem registros privados próprios.

## Recursos de preparo

Os três POSTs reutilizam contratos e serviços existentes, com sessão/Origin/CSRF verificados antes do corpo e das fábricas de calendário/modelo. Não exigem chave de serviço no navegador.

Somente o resumo pode chamar Gemini: os agregados permitidos são organizados uma vez, por pedido explícito. Agenda vazia pula a IA; indisponibilidade do modelo permite alternativa factual identificada; falha de leitura não produz agenda falsamente vazia. Texto arbitrário do modelo não vira resposta factual. A apresentação usa datas e horas legíveis.

Disponibilidade é cálculo Python e não reserva. Briefing/checklist são factuais/genéricos, sem LLM ou pesquisa externa; notas do briefing não são enviadas ao modelo. Marcar itens, navegar, retomar sessão e atualizar agenda não geram IA automaticamente. Notas e marcações permanecem somente na memória da página.

Essas ferramentas não criam, editam ou excluem eventos, disparam lembretes ou enviam WhatsApp. Escritas continuam restritas à confirmação de uma operação da Agenda. [Uso e limites](painel-web-preparo.md).

## Demonstração separada

<http://127.0.0.1:8000/painel/demo/> serve o protótipo com exemplos fictícios, identificado como prévia. Ele não usa a sessão para ler agenda real e não executa operações nos provedores. As alterações de exemplo ficam em memória e desaparecem ao recarregar. O servidor independente em <http://127.0.0.1:8015> continua disponível apenas para essa prévia histórica; [instruções do frontend](../web/README.md).

## Registro histórico da fase 3

Suíte limpa de **2.605 testes Python e 17 testes Node do frontend** aprovada no fechamento. A cobertura inclui pareamento de uso único, expiração, revogação, armazenamento do identificador somente como hash, limites de acesso, origem/CSRF, consultas e tratamento de respostas antigas. Os testes de autenticação também foram conferidos com as otimizações de Python ativadas.

As verificações locais reais passaram para agenda, detalhe, lembretes e histórico usando a sessão aprovada. A comparação do banco de lembretes antes e depois confirmou conteúdo inalterado. Isso comprova as leituras verificadas neste marco; não comprova CRUD pelo painel, prontidão permanente dos provedores ou entrega de mensagem.

## Registro histórico da fase 4

A suíte completa daquele fechamento passou com **2.665 testes Python e 39 testes Node**. No navegador, o aplicativo e os stores reais foram exercitados com calendário isolado para criar, editar, cancelar prévia, reagendar, excluir e consultar semana/mês, incluindo tela estreita. Isso comprova a integração dos formulários com os serviços e a persistência; não equivale a uma nova escrita no Google.

Na API instalada, a validação com Google real preparou e cancelou uma prévia de criação sem confirmá-la. A comparação conservou a agenda antes/depois; os SHA-256 dos ledgers de execução e lembretes permaneceram iguais. Nenhuma escrita Google ou mensagem WhatsApp foi feita por esse ensaio. Detalhes de sessão, idempotência, conflitos e resultados incertos ficam no [fechamento da agenda](painel-web-agenda.md). O plano completo permanece na [definição do painel](painel-web.md).

## Estado da fase 5

Os recursos estão conectados pela sessão local. Cobertura e resultados atuais são consolidados no [fechamento do preparo](painel-web-preparo.md), distinguindo fontes controladas, leituras reais e chamadas reais de modelo. As contagens das fases 3 e 4 acima são históricas.

## Referências técnicas

As decisões de cookie, expiração e revogação usam como referência a [OWASP — Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html). A proteção de origem e CSRF combina medidas descritas na [OWASP — Cross-Site Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html). O serviço de HTML e arquivos utiliza os tipos de resposta documentados em [FastAPI — Custom Response](https://fastapi.tiangolo.com/advanced/custom-response/). Essas referências fundamentam decisões de implementação; não representam certificação ou auditoria externa.
