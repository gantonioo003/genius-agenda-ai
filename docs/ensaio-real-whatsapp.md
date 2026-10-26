# Ensaio real pelo WhatsApp — etapa 16

## Confirmado em 26/10/2026

O usuário enviou ao sandbox o pedido de criação de “QA16 — Lembrete fictício”, das 16h55 às 17h25 em Recife. O agente respondeu com a criação confirmada. A leitura independente do ID exato no Google confirmou título, data e horários; a agenda do dia estava vazia na baseline anterior.

O ciclo periódico n8n gerou e enviou o aviso de 60 minutos. Às 15h55, o usuário recebeu o card com os dados desse evento e apresentou a captura original 159. O registro local conserva uma única transição `sending` e o aceite do envio às 18:55:27 UTC. Não houve envio manual desse card para suprir o teste. A imagem de demonstração da etapa 14 era outro envio e outro conteúdo.

## Em andamento

Consultar, editar descrição, reagendar e excluir o mesmo evento fictício com a confirmação do produto. Conferir os campos e a ausência pelo ID no Google. O recebimento do aviso textual de 15 minutos ainda não foi demonstrado neste ensaio. Esses itens não estão declarados concluídos.

## Operação e evidências

Baseline 158; captura original 159; leitura e ledger 160. A automação de interface do Edge foi interrompida pela ferramenta por não conseguir validar a URL atual, antes de qualquer mensagem. O usuário enviou o pedido pelo WhatsApp; a conferência foi feita por CLI Google e leitura dos registros existentes. Sem publicação de segredos ou identificadores privados nas evidências resumidas.


## Correção encontrada no ensaio

A captura 161 confirmou a consulta real e mostrou dois problemas: cabeçalho repetindo a mesma data e esclarecimento genérico na edição apesar de título literal e consulta anterior válida. A consulta diária agora apresenta a data uma vez, mantendo datas em eventos que atravessam dias. A edição literal reutiliza o período do contexto válido para refazer a busca no Google; não escolhe o primeiro evento nem assume o ID anteriormente selecionado. Mais de um candidato continua exigindo escolha, e contexto ausente pede somente a data que falta.

Passaram 281 testes focados de diálogo, consulta, criação, edição, exclusão e autorização, com dez novos casos. A última suíte completa continua sendo a de 2.394 da etapa 14; não foi reexecutada integralmente nesta correção. A API foi recarregada sem apagar contexto ou fila. A repetição real da edição segue pendente de conferência. Registros 161/162.


## Consulta e edição reais confirmadas

Após a correção, o usuário repetiu a consulta e a edição. A captura original 163 mostra a consulta diária com data única, a prévia do evento correto e a confirmação “sim”, seguida de resposta de alteração aplicada. A leitura independente do mesmo ID no Google conferiu a descrição “Teste real de qualidade pelo WhatsApp”, preservando título, início, fim, status e local. Prova 164. Reagendamento e exclusão continuam no ensaio.


## Ensaio concluído

Reagendamento confirmado com prévia e “sim”, refletido no novo início dos avisos; texto de 15 minutos recebido. Exclusão confirmada com prévia e “sim”, ausência pelo ID exato no Google e aviso futuro cancelado. As capturas originais 167/168 e o relatório 170 registram os limites de cada prova. O evento adicional criado pelo usuário foi preservado. [Fechamento consolidado](fechamento-etapa-16.md).
