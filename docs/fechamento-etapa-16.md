# Etapa 16 — qualidade e documentação

Concluída em 26/10/2026 no escopo de entrega do desafio.

## Resultados reais

- Criação pelo WhatsApp, consulta diária, edição de descrição, reagendamento e exclusão com confirmação concluídos no mesmo evento fictício QA16.
- Edição conferida por leitura independente do Google, preservando título e horários. Reagendamento para 17h10–17h40 confirmado no WhatsApp e refletido nos avisos persistidos. O GET independente posterior já encontrou o evento excluído; não se alega uma leitura intermediária que não ocorreu.
- Card de 1 hora e texto de 15 minutos recebidos e confirmados pelo usuário. Cada aviso antigo tem uma única transição de envio. Após a exclusão, o aviso futuro do novo horário ficou cancelado por `event_cancelled`.
- Evento fictício QA16 removido. A conversa adicional criada pelo usuário para o dia seguinte foi preservada.
- Perguntas genéricas encontradas no ensaio corrigidas com preparação literal restrita e busca fresca; candidatos ambíguos continuam exigindo escolha. Data diária sem repetição, fuso padrão implícito e confirmação de exclusão em linguagem simples.

## Verificações

360 testes focados atuais aprovados, incluindo autorização, contexto, criação, alteração, exclusão, apresentação, lembretes e cards. A última suíte completa anterior tinha 2.394 testes; não foi reexecutada integralmente após as correções de conversa. As 981 verificações JavaScript são históricas. Os conjuntos se sobrepõem e não devem ser somados.

API recarregada após conferir fila ociosa, preservando contexto, n8n, receptor e túneis. A revisão de fontes públicas e cinco exports não encontrou credenciais reais; `.env`, `secrets/`, `data/` e `.venv/` permanecem fora do Git. Não foi executado outro reinício destrutivo após o ensaio real; casos de reinício e falhas também têm a cobertura automatizada anterior.

## Limites

Ambiente local: computador, API, Docker/n8n e entrada HTTPS precisam estar ligados. Túnel e hospedagem de mídia são temporários. Sandbox de um destinatário, janela de atendimento e templates limitados; não se declara implantação de produção ou template contextual fora da janela. Gemini pode exigir esclarecimento ou reformulação; os fallbacks locais cobrem formatos completos restritos, não linguagem natural irrestrita. Falhas incertas bloqueiam novo envio até conferência.

README, arquitetura, evidências e planner consolidados. Acervo: 173 registros, 44 imagens e 129 JSON, com originais privados. Painel web adiado por decisão de prazo. Próxima etapa: vídeo, conferência de links e entrega até 18h. A publicação no GitHub deve conter somente a versão pública revisada.
