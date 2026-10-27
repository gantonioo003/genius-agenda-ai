# Operações por nome e interpretação do Gemini

Atualizado em 27/10/2026.

## Uso

O nome identifica o compromisso para editar, reagendar e excluir. Não é necessário consultar uma data antes nem copiar um identificador do Google.

- “Quero apagar Entrevista Projeto.”
- “Faz a exclusão de Entrevista Projeto.”
- “Atualiza o local de Entrevista Projeto para Sala B.”
- “Gostaria de mudar a descrição de Entrevista Projeto para Conversa técnica sobre Python.”
- “Troca o horário de Entrevista Projeto para 28/10/2026, das 10h às 10h30.”

O Gemini interpreta cada pedido normal, escolhe a operação e preenche os dados. O Python pesquisa o nome no calendário, relê o resultado e aplica as regras de validação e confirmação. Verbos, flexões e formas nominais são aceitos pelo modelo; o vocabulário local auxilia o controle de intenção, negação e autorização, sem ser o único intérprete.

Se o nome identificar mais de um evento, o agente mostra as opções e pede uma escolha. A pessoa pode informar a data para refinar quando quiser. No reagendamento continuam necessários o novo início e término; na edição, os dados que serão alterados. Reagendamento e exclusão conservam prévia e confirmação. Nomes não encontrados geram uma pergunta sobre o nome.

## Busca

A busca sem data percorre páginas de toda a agenda, comparando títulos sem diferenciar caixa e acentos. Partes do nome também podem corresponder; havendo mais de um resultado, o agente não escolhe pelo usuário. A última consulta ou um evento lembrado não escondem candidatos em outras datas.

Datas ou períodos informados explicitamente continuam sendo filtros. A data do novo horário não vira filtro para encontrar o evento antigo. Palavras como “segunda” dentro do próprio título também não criam filtros por suposição.

Séries recorrentes são expandidas em ocorrências concretas; uma operação não é direcionada ao evento mestre por engano. Uma busca incompleta, paginação inválida ou falha de leitura não é tratada como prova de unicidade. A implementação limita a coleta ao detectar 21 candidatos e falha com segurança se atingir 100 páginas sem concluir as leituras necessárias.

## Resposta da IA

O transporte HTTP do Gemini reutiliza conexões separadas por thread, sem guardar conversa remota ou chave em cabeçalhos persistentes. As respostas são fechadas após leitura e as conexões são encerradas no ciclo de vida da API.

Somente no adaptador Gemini, os campos opcionais nulos de preparação podem ser omitidos da resposta técnica. O servidor os repõe como `None` e aplica as mesmas verificações de nomes de campos, tipos, enumerações e tamanho. Campos centrais continuam obrigatórios, e a configuração/OpenAI permanecem com seus contratos originais. Nos exemplos de teste, o JSON de argumentos ficou 43–64% menor; isso mede bytes, não uma garantia de redução de latência.

O modelo, os limites de saída e os timeouts não foram trocados. A IA continua responsável pela interpretação; a melhoria evita reconstruir conexões e gerar campos nulos desnecessários. O tempo ainda depende do Gemini, Google e transporte WhatsApp.

## Validação

Suíte Python completa: **2530 testes aprovados**, sem falhas ou erros. O Gemini real interpretou seis frases fictícias, incluindo exclusão nominal, apagar, alterar local, trocar horário, mudar descrição e nome duplicado. A busca global foi exercitada no Google real com um título fictício inexistente, sem gravar eventos. Relatórios privados: 176/177.

As amostras de tempo são pequenas e variáveis, e usam calendário simulado durante a interpretação Gemini; não comprovam um ganho fixo no WhatsApp. Não foram feitas novas exclusões, alterações ou entregas de mensagem reais nesta validação.

Referências: [busca Google](https://developers.google.com/workspace/calendar/api/v3/reference/events/list), [ocorrências](https://developers.google.com/workspace/calendar/api/v3/reference/events/instances), [schema de funções Gemini](https://ai.google.dev/api/generate-content#FunctionDeclaration), [reutilização HTTP](https://requests.readthedocs.io/en/latest/user/advanced/).
