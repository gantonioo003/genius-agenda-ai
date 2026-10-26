# Prevenção de duplicações — etapa 08, parte 5

A criação mantém um registro persistente no SQLite para reconhecer pedidos repetidos, inclusive após reiniciar o programa. A regra atua no conector Google e usa `app/services/creation_identity.py` para identificar o conteúdo, sem depender da IA.

## Comportamento

| Solicitação | Resultado |
|---|---|
| Mesmo identificador de operação e mesmo conteúdo | Consulta o ID original; reutiliza o evento confirmado, com `reused: true` |
| Outro identificador, mas mesmo conteúdo canônico na mesma agenda | Aponta para o registro e o ID originais; reutiliza o evento após conferir seus dados |
| Mesmo identificador com conteúdo diferente | Recusa a operação; o identificador não pode ser reaproveitado para outro pedido |
| Evento existente com dados divergentes ou identidade incompatível | Recusa a confirmação; não sobrescreve nem insere outro evento |
| Registro confirmado cujo evento foi removido | Recusa a repetição; não recria o compromisso |
| Tentativa anterior com resultado incerto | Consulta o ID persistido e recupera o resultado quando os dados correspondem |

O resultado `reused` só é confirmado depois de conferir o ID, o estado `confirmed`, o link, a impressão privada da criação e o conteúdo do evento retornado pelo Google. Um registro local, sozinho, não prova que o compromisso existe.

## O que identifica o conteúdo

A chave considera título, descrição e os instantes de início e fim:

- Título em Unicode NFC, removendo somente os espaços das extremidades.
- Descrição em Unicode NFC, preservando seus espaços.
- Início e fim convertidos para UTC.

Maiúsculas, acentos e espaços internos não são aproximados. O nome do fuso é uma forma de apresentação; horários válidos que representam os mesmos instantes têm a mesma identidade. A validação de datas, offsets e fusos continua obrigatória antes da criação.

Essa comparação é exata após a normalização descrita. Não existe busca aproximada por título para decidir que dois pedidos são iguais. Um evento semelhante criado por outra ferramenta continua sujeito à checagem de conflitos.

## Registro e sequência de gravação

O esquema SQLite **2** acrescenta duas tabelas:

- `creation_requests`: agenda, hash do conteúdo, ID do evento, impressão da criação, estado e datas técnicas. A agenda e o hash identificam um único pedido; o ID do evento também é único nessa agenda.
- `creation_operations`: relaciona o hash de cada identificador de operação ao pedido original. Uma operação fica vinculada a um único conteúdo dentro da agenda.

A inicialização migra o esquema **1 → 2** em uma transação, preservando os metadados existentes. Inicializar novamente mantém os registros. Versões desconhecidas continuam recusadas.

O fluxo de criação segue esta ordem:

1. Validar os dados e selecionar a agenda autorizada.
2. Reservar a identidade em estado `pending` e confirmar essa transação **antes de qualquer POST** ao Google.
3. Adquirir a trava de escrita, reler o registro e consultar o ID persistido.
4. Se houver evento correspondente, reutilizá-lo. Se estiver ausente e o registro ainda for `pending`, verificar conflitos antes de tentar criar com esse mesmo ID.
5. Conferir a resposta e marcar `confirmed` usando a conexão da própria trava. O POST não tem retentativa automática.

A reserva inicial permanece salva se uma chamada externa falhar. A confirmação ocorre em outra transação, dentro da trava; uma falha pode deixá-la como `pending` mesmo após o Google salvar o evento. Na retomada, o programa consulta o ID original antes de decidir o próximo passo. Nunca gera outro ID para contornar um resultado incerto.

## Eventos alterados, removidos e legados

Repetir a criação não desfaz uma edição. Se o título, a descrição ou os instantes atuais divergirem do pedido original, a conferência falha e solicita revisar a agenda. Um registro já `confirmed` com evento ausente também não volta automaticamente a `pending`.

Para eventos criados antes deste registro persistente, permanece a fórmula original do ID derivado da operação. A recuperação consulta somente esse ID e exige a impressão privada e os dados compatíveis. Não procura por texto nem adota outro evento apenas porque possui o mesmo título e horário.

## Privacidade, operação e limites

O registro de criação armazena hashes, identificadores, estado e datas técnicas; não grava o texto do título ou da descrição. **Hashes não tornam os dados anônimos.** O SQLite e suas evidências permanecem locais, ignorados pelo Git; o código do esquema é versionado para permitir reproduzir a instalação.

A trava serializa processos que usam o **mesmo arquivo SQLite**. Instâncias com bancos diferentes não compartilham esse histórico, e alterações externas no Google continuam possíveis. Não existe transação atômica entre o banco local e o Calendar.

O backup deve conservar o banco com seu registro de criação, além das configurações necessárias. Apagar o SQLite perde o vínculo persistido entre operações diferentes com o mesmo conteúdo; manter apenas o código não preserva esse histórico.

Esta parte cobre a deduplicação de **criação de eventos**. O tratamento do identificador de mensagens repetidas do WhatsApp permanece separado, na **etapa 10**, de integração do WhatsApp. As regras de [conflitos](conflitos.md) continuam aplicadas antes de uma nova gravação.


## Verificação em 22/10/2026

A suíte passou com **264 testes**, no staging e no ambiente do repositório: 217 existentes e **47 novos** — 26 do registro persistente e 21 da identidade e integração com um Google simulado. Bancos temporários reais exercitaram a migração, rollback, reabertura, chaves estrangeiras e concorrência. Duas chamadas simultâneas com operações diferentes e conteúdo igual produziram apenas um INSERT no provedor simulado. Falha após o provedor salvar foi recuperada pelo mesmo ID, inclusive com outra operação.

Na migração do banco utilizado no desenvolvimento, fizemos e conferimos o backup `data/backups/agenda_antes_duplicacoes_v2_20261022.db`. A versão mudou de 1 para 2 e os dois metadados existentes, incluindo seus valores e datas, foram preservados. O registro privado **036** guarda os hashes dessa conferência, sem expor seus valores.

Na agenda real `Genius Testes`, repetimos o pedido original da entrevista A de **23/10, 15h–15h30** duas vezes; ambas as respostas retornaram `reused: true`, com o mesmo ID existente. Em outro processo Python, um novo identificador para o mesmo conteúdo também reutilizou A. O identificador original com conteúdo diferente foi recusado. As leituras anteriores e posteriores continuaram mostrando os mesmos dois eventos, A e B.

Os registros **037 a 039** preservam essas leituras e respostas reais. Durante a verificação, uma proteção no transporte permitiu somente GET; houve **zero tentativas de escrita no Google**. Os dados retornados não foram simulados. A captura 035 do marco anterior já mostra os horários preservados; os novos JSONs comprovam a reutilização por ID, a reabertura em outro processo e a contagem inalterada. Não foi necessário outro print equivalente.

**Parte 5 concluída.** A parte 6, [falhas e recuperação](falhas-recuperacao.md), acrescentou a consulta imediata após uma resposta de escrita incerta e o campo `recovered`. A parte 7, [testes do conjunto](testes-conjunto.md), também foi concluída. **Etapa 08 concluída em 22/10/2026**, com 359 testes aprovados e código conferido por hashes. Consulte o [fechamento](fechamento-etapa-08.md). Próxima: **etapa 09 — operações na API e fluxos n8n**.
