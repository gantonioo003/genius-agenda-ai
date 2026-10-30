# Fechamento do painel web

A etapa 15 está concluída no escopo local documentado: Visão geral, Agenda, Preparo, Lembretes e uma demonstração independente com dados fictícios. WhatsApp e painel conectado reutilizam os serviços da mesma agenda.

## Validação consolidada

| Verificação | Resultado e alcance |
|---|---|
| Suíte Python completa | **2.707 testes aprovados**, zero falhas, erros ou testes ignorados |
| Frontend Node | **105 testes aprovados**, zero falhas ou testes ignorados; 84 do painel conectado e 21 da demonstração |
| Segurança em código | Sessão, CSRF, confirmações vinculadas, conflitos, versões, resultados incertos, leitura dos lembretes e prévias privadas revisados |
| Acessibilidade | Revisão manual e de código de navegação por teclado, foco, diálogos, contraste e movimento reduzido |
| Apresentação | Capturas e tour visual com dados fictícios, identificados como demonstração |

Os testes automatizados usam cenários controlados e não provam entrega de mensagens ou novas gravações no Google. Os ensaios reais históricos permanecem registrados separadamente no [índice de evidências](evidencias.md) e no [ensaio WhatsApp](ensaio-real-whatsapp.md). As contagens de marcos anteriores são históricas, não devem ser somadas à suíte atual.

O link para o conteúdo principal, a entrada de foco no diálogo, o fechamento com `Esc` e o retorno ao botão de origem foram conferidos por teclado. O contraste do selo da demonstração foi corrigido, e avisos de erro/descarte receberam anúncio para leitores de tela. A revisão não equivale a uma certificação WCAG nem a testes completos com todas as tecnologias assistivas.

## Materiais para o portfólio

- [Tour visual do painel](assets/painel-tour.mp4): clipe editado, sem narração, montado com cenas reais da demonstração fictícia. As durações são editoriais, sem medição de latência.
- [Roteiro, currículo e LinkedIn](apresentacao-painel.md): texto preparado para apresentação; nenhuma publicação em rede social foi feita pelo sistema.
- [Telas e experiência](painel-web.md), [acesso protegido](painel-web-api.md), [agenda](painel-web-agenda.md), [preparo](painel-web-preparo.md), [lembretes](painel-web-lembretes.md) e [demonstração](painel-web-demonstracao.md).

O vídeo enviado ao recrutador em 26/10/2026 é um registro histórico do fluxo WhatsApp. O novo tour apresenta o painel posterior; não substitui a gravação nem comprova novas operações ou recebimentos.

## Limites mantidos

O painel conectado é local, para um proprietário, com pareamento e sessão; não foi publicado como serviço com múltiplos usuários. As chaves ficam no servidor. Escritas exigem prévia e confirmação; dados alterados externamente exigem nova conferência, e resultados incertos bloqueiam repetição automática.

Lembretes são acompanhados em leitura, sem botões de disparo ou recuperação. Aceite do provedor não comprova entrega ou leitura. A prévia do card é reconstruída com dados atuais e identificada como não enviada; não é uma cópia histórica.

Resumo pode usar IA ou alternativa factual identificada. Briefing/checklist são factuais; disponibilidade é calculada e não reserva horários. Notas e marcações ficam na memória da página. A demonstração também fica em memória, usa referência fixa de 27/10/2026 e não acessa Google, Gemini, n8n, SQLite ou WhatsApp.

Serviços locais e computador precisam permanecer ligados para o canal real. O sandbox, os túneis temporários e os templates disponíveis continuam com os limites descritos nos guias de operação.
