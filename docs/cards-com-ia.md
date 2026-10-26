# Cards com IA

## Etapa 14 — bloco 1: visual preparado em 26/10/2026

Fundo original gerado com a ferramenta integrada de imagens, em PNG RGB de 1254 × 1254. A composição foi conferida visualmente: mesa, laptop e planta na parte superior; área creme livre abaixo para a informação da entrevista. Sem nomes, datas, horários ou dados da agenda enviados à geração.

Arquivos versionados em `assets/cards/`:
- `interview-background-v1.png`: fundo reutilizável.
- `interview-background-v1.prompt.txt`: prompt final de geração.
- `interview-template-v1.json`: posições, cores, fontes, limites e regras dos campos.

O modelo reserva marca, chamada, título em duas linhas, data, horário e fuso amigáveis. Os exemplos de formatação no JSON são fictícios. Título longo terá quebra e reticências; data e horário devem permanecer completos. A chamada “Chegou o seu dia!” só será usada no dia local da entrevista.

## Registro do bloco 1

Planejamento ao terminar o primeiro bloco: compor os textos por código usando os dados validados do evento, verificar legibilidade e integrar um envio de demonstração com alternativa em texto. O fundo é reutilizado: não há geração nova para cada evento.

O primeiro marco entregou o fundo e a definição do modelo. Naquele momento, ainda não comprovava composição dinâmica, publicação de mídia, envio de card próprio, recebimento no WhatsApp ou disparo automático. A imagem 152 do acervo é o arquivo gerado original, não uma captura de aplicativo. A suíte de 2.344 testes Python anterior permanece histórica; para este conjunto de arquivos estáticos foram conferidos PNG, JSON, limites dos campos e hashes, sem nova execução da suíte.


## Composição e envio — bloco 2 concluído

Em 26/10/2026, `app/interview_card.py` passou a compor os campos por Pillow 12.3.0 sobre o fundo aprovado. O título quebra em até duas linhas, com reticências se necessário; data e hora não são truncadas. Horários ambíguos de mudança de fuso recusam o card e conservam uma legenda textual com offsets. A imagem de exemplo está em `assets/cards/interview-demo-v1.png`.

`app/interview_card_delivery.py` usa o ledger existente para reservar a operação e impedir reenvio do que foi aceito ou ficou incerto. Uma falha de renderização/hospedagem antes do envio, ou recusa local comprovada, permite texto. Timeout, resposta incerta ou erro depois da tentativa bloqueiam novo envio até conferência. A legenda também acompanha a imagem e mantém os dados legíveis.

O teste real enviou uma única imagem fictícia, aceita pela 360dialog e recebida pelo usuário, que apresentou a captura 155. Nenhum evento Google foi criado ou alterado. Foram aprovados 21 testes focados, incluindo composição, fuso, título longo, fallback, reserva/reuso e servidor de mídia. Os 2.344 testes da suíte geral anterior não foram reexecutados nesta parte.

### Reprodução da demonstração

Instalar `requirements.txt` no ambiente do projeto. Na raiz, renderizar sem rede:

```powershell
.venv/Scripts/python.exe scripts/demo_interview_card.py --date 2026-10-26 --operation-id 791fa8f9-814e-40d6-b737-fcbe6ffac3c7
```

Para um novo ensaio, usar um UUID v4 novo; para repetir a mesma operação, conservar seu UUID e conteúdo. A opção `--execute --image-link <URL_HTTPS_DO_PNG>` envia somente ao destinatário da política privada existente. Sem link, o envio explícito usa texto. Nunca reaproveitar uma identidade para conteúdo diferente ou trocar o UUID para contornar um resultado incerto.

O servidor `python -m app.card_media_server --image data/cards/interview-demo-v1.png` expõe apenas esse PNG, na porta local 8790, por até quatro horas; a rota é `/cards/demo-<sha256>.png`. Somente o exemplo fictício foi publicado por um túnel HTTPS separado. Não há listagem de diretórios nem acesso a `.env`; API e webhook não foram alterados. A URL é temporária: para uma nova demonstração, verificar novamente a disponibilidade e o hash. Não usar essa hospedagem de demonstração para dados privados de entrevistas reais.

O sandbox não oferece upload/recuperação por media ID, por isso usamos um link direto HTTPS para o PNG. A aceitação inicial pode preceder uma falha posterior de mídia; neste teste o recebimento humano foi confirmado. Fontes: [sandbox 360dialog](https://docs.360dialog.com/docs/get-started/sandbox), [mensagens de mídia](https://docs.360dialog.com/docs/messaging/media).

### Contexto de uso e limite atual

A conversa do agente permanece em texto. O card foi entregue como demonstração manual complementar, com campos fictícios. Após o teste manual, a regra de card aos 60 minutos e texto aos 15 minutos foi integrada e ativada. Um comando para pedir card na conversa não foi acrescentado. O recebimento agendado real ainda será conferido na qualidade. [Fechamento da etapa 14](fechamento-etapa-14.md).


## Cards nos lembretes — integração automática

`REMINDER_CARDS_ENABLED=true` habilita o adaptador dos lembretes. O padrão público em `.env.example` é desabilitado. `secrets/card-media-runtime.json` informa somente a origem HTTPS de mídia ao adaptador; o processo atual já foi preparado localmente.

Para reproduzir em outro ambiente, gerar a demonstração, iniciar `python -m app.card_media_server --image data/cards/interview-demo-v1.png --cache-root data/cards/published`, fornecer um túnel HTTPS para a porta 8790 e salvar sua origem no JSON privado (`{"origin":"https://seu-host-publico"}`). Ativar a flag no `.env` e reiniciar a API. Usar a mesma API e o ciclo periódico n8n existentes; não há segundo scheduler.

Somente o lembrete de 60 minutos chama a preparação. O aviso de 15 minutos usa texto curto com o tempo restante real; atrasos pequenos não são apresentados como quinze minutos exatos. O card é preparado antes da leitura final do evento e do CAS `sending`. O envio permanece ligado à mesma identidade persistente; não usa o ledger de demonstrações.

O cache `data/cards/published` guarda PNG e metadados privados. A URL aleatória só serve o PNG correspondente e expira em dez minutos. O arquivo local pode continuar no cache privado após expirar; sua URL deixa de ser atendida. A disponibilidade pública e o hash são conferidos antes do envio. Caso contrário, retorna para texto antes de tentar uma imagem.

Assim que a tentativa de imagem começa, timeouts ou respostas incertas não disparam texto ou outra imagem automaticamente. O estado fica protegido para conferência. Aceite do provedor e recebimento humano continuam sendo evidências distintas.
