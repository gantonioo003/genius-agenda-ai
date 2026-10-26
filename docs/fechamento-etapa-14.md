# Etapa 14 — imagens com IA

Concluída em 26/10/2026, no escopo compacto de entrega.

## Entrega

- Fundo gerado com IA uma vez e reutilizado; os dados são escritos por Python/Pillow, sem enviar dados da agenda ao gerador de imagens.
- Card com título, data e horário legíveis. Exemplo fictício enviado uma vez, aceito pela 360dialog e recebido pelo usuário, com captura original 155.
- Aviso de **60 minutos** prepara o card; aviso de **15 minutos** continua em texto. A conversa normal do agente permanece em texto.
- Composição e disponibilidade HTTPS verificadas antes da leitura final e das guardas de envio. O card fica vinculado ao título, início, fim, fuso e evento exatos. Se a versão mudou, usa o texto atualizado.
- Falhas antes do envio permitem alternativa textual. Resultado incerto de transporte conserva o estado protegido e não autoriza outro envio.
- Publicação de PNG por capacidade aleatória de 256 bits, válida por dez minutos, sem listagem, com verificação do hash e `Cache-Control: no-store`. Credenciais, URLs de webhook e destinatário permanecem privados.

## Validação

Suíte completa: **2394 testes Python aprovados**, com 50 testes acrescentados na etapa 14. Inclui o conjunto anterior; não somar os 21 focais da demonstração novamente. As 981 verificações JavaScript são históricas; n8n não teve seus fluxos alterados nesta etapa.

Os testes cobrem composição, fontes e limites, fusos e horário ambíguo, expiração e integridade da mídia, escolha do canal, falta de hospedagem, reserva única, cancelamento/reagendamento durante preparo, perfil/canal mudando, reinício e resultado incerto. As expectativas antigas de texto das fixtures foram atualizadas para a apresentação amigável.

Runtime conferido: card habilitado, servidor de mídia e API carregados; saúde da API/n8n e preservação do receptor/webhook verificadas. A ativação não criou evento nem enviou nova mensagem. Registros 156/157 e [índice de evidências](evidencias.md).

## Limites e próxima etapa

O recebimento comprovado é o da demonstração manual. O primeiro lembrete agendado com card, vindo de um evento real Google, ainda precisa ser recebido e conferido na etapa de qualidade. A etapa 14 não declara a entrega geral concluída.

O ambiente local exige API, n8n e servidor/túnel de mídia ativos. A hospedagem é temporária; se não estiver acessível antes do envio, o lembrete segue em texto. O sandbox continua sujeito à janela de atendimento e aos seus templates disponíveis. Não foram adicionados templates contextuais de produção nem geração de um fundo novo a cada entrevista.

Próxima etapa: painel web reduzido (15), seguido de qualidade/documentação (16) e vídeo/entrega (17). [Detalhes e reprodução](cards-com-ia.md).
