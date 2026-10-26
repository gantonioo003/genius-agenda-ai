# Fechamento da etapa 08 — regras da agenda

**Concluída em 22/10/2026.** As oito partes consolidaram validação, seleção sem ambiguidades, conflitos, identidade persistente de criação e recuperação de resultados incertos. Os serviços permanecem independentes do modelo de IA e executáveis pelos comandos Python.

## Entregas e evidências

| Parte concluída | Entrega confirmada | Evidência e natureza |
|---|---|---|
| 1. Revisar as regras | Diagnóstico de dez verificações, com base de 139 testes e arquivos identificados por hash. | **028 — local**, sem Google; preserva o diagnóstico anterior às correções. |
| 2. Consolidar datas e fusos | Regra compartilhada de horários, duração em UTC, zona IANA, offsets e transições de horário de verão. Suíte com 155 testes. | **029 — local**, com quatro intervalos aceitos e onze casos inválidos. |
| 3. Tratar ambiguidades | Perguntas estruturadas para dados ausentes, inválidos ou seleção incerta; preparação sem escrita. Suíte com 181 testes. | **030 — local**, nove exemplos fictícios; não representa eventos da conta Google. |
| 4. Detectar conflitos | Conferência de ocupação antes de criar/reagendar, limites exclusivos e trava local entre consulta e gravação. Suíte com 217 testes. | **031–034 — Google real**, incluindo dois bloqueios e conferência posterior; **035 — captura real** dos horários preservados. |
| 5. Evitar duplicações | Migração SQLite para esquema 2; reserva durável, conteúdo vinculado à identidade original e reutilização após reabrir o programa. Suíte com 264 testes. | **036 — local**, backup e migração; **037–039 — Google real**, somente leituras, incluindo repetição em outro processo. |
| 6. Tratar falhas e resultados incertos | Escrita sem reenvio implícito, conferência por ID, erros seguros e resultado `recovered` quando o estado esperado é confirmado. Suíte com 351 testes. | **040 — local**; **041–043 e 045 — híbridos**, escrita real com perda local simulada da resposta; **044 — captura real histórica**; **046 — Google real**, conferência final sem injeção. |
| 7. Testar o conjunto | Oito percursos atravessando serviços, adaptador, SDK, transporte e SQLite; conflitos, repetição, reabertura, concorrência e falhas combinadas. Suíte com 359 testes. | **047 — local**, duas rodadas completas, saída do runner e contagens HTTP simuladas. |
| 8. Revisar e fechar | Consolidação das entregas, limites, resultados, documentação e sequência do projeto. | **048 — auditoria local de fechamento**; não é uma nova operação no Google nem uma nova rodada de testes. |

Os detalhes e a origem de cada arquivo ficam em `data/evidencias/INDICE.md`. Evidências privadas permanecem locais e fora do Git. A captura 044 mostra a entrevista temporária antes da exclusão; a leitura 046 confirmou sua ausência e a preservação das entrevistas A e B. Imagens mostram o estado visível; os JSONs registram bloqueios, identidade e recuperação.

O acervo ao fechar a etapa reúne **48 registros**: **13 imagens**, **27 JSONs de operações com o Google** — 23 sem injeção e quatro híbridos — e **8 registros locais**. Os quatro híbridos não representam indisponibilidade real do serviço.

## Resultado dos testes e conferência dos arquivos

A base passou de **139 para 359 testes**, com **220 casos novos**: 16 de datas/fusos, 26 de preparação e esclarecimentos, 36 de conflitos e trava local, 47 de identidade persistente, 87 de falhas e recuperação e oito percursos integrados.

As rodadas registradas na preparação isolada e no repositório terminaram com **359 aprovados**, sem falhas, erros, testes ignorados ou IDs duplicados. O runner de auditoria bloqueou conexões desde a descoberta e registrou **zero tentativas de rede**. Os arquivos privados de configuração, cliente OAuth, token e banco foram conferidos antes/depois e preservados; seus valores e hashes não foram publicados.

No fechamento, os **48 arquivos Python da aplicação e dos testes** foram comparados aos hashes do relatório aprovado do repositório: todos correspondem. Essa conferência preserva a relação entre código e resultados sem repetir uma suíte cujo código não mudou. O registro 047 contém as execuções; o 048 documenta a auditoria final.

Os percursos integrados usam código real dos serviços, adaptador, SDK, transporte e SQLite temporário, com a sessão HTTP simulando o Google. A reabertura nesses testes cria novos objetos sobre o mesmo arquivo, dentro do mesmo processo; a verificação real da parte 5 também incluiu outro processo Python. O helper não expande recorrências nem projeta `fields`, e não substitui a validação real da integração. Reprodução e matriz detalhada: [testes do conjunto](testes-conjunto.md).

## Limites preservados

- A trava serializa processos que compartilham o **mesmo arquivo SQLite**. Instâncias com bancos diferentes e alterações externas no Google não participam desse controle; não existe transação atômica entre SQLite e Calendar.
- A identidade persistente cobre criação de eventos. É necessário preservar o banco para manter os vínculos entre mensagens/operações e o pedido original. Deduplicação de mensagens do WhatsApp e estados de lembretes pertencem às próximas etapas.
- `ready` significa pedido preparado; não reserva horário, não executa a ação e não dispensa as verificações do executor. O token de exclusão vincula a confirmação à versão do evento; não é autenticação nem prova de consentimento do usuário.
- Leituras insuficientes mantêm o resultado incerto. Não há fila persistida de retentativas para PATCH/DELETE nem garantia contra mudanças externas após a conferência.
- A API mínima e o ambiente n8n já existem, mas as rotas de operações da agenda e os fluxos n8n correspondentes ainda serão integrados. WhatsApp, interpretação por IA, lembretes, briefings, imagens e painel web permanecem no roteiro.

## Próxima etapa

**09 — expor os serviços pela API e conectar os fluxos n8n**, preservando as regras já testadas, erros operacionais e proteção dos segredos. As credenciais continuam na configuração privada; não devem entrar nos exemplos, respostas, logs ou arquivos versionados.
