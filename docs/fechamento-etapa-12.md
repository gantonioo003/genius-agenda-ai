# Fechamento da etapa 12 — lembretes

**Concluída em 26/10/2026 no escopo da agenda de teste e do sandbox 360dialog.** As dez partes entregaram regras configuráveis, persistência, geração/sincronização, processamento periódico, envio com revalidação e recuperação seletiva. O n8n publicado executa **recover → tick → dispatch** a cada minuto; os serviços Python e o SQLite controlam identidade, prazo, autorização e histórico.

A versão aprovada na parte 9 tem **1.956 testes Python e 981 verificações JavaScript**, sem casos ignorados na execução consolidada. **Essas suítes não foram reexecutadas neste fechamento documental.** O código aprovado e os cinco exports são preservados. As verificações com provedores fictícios, o runtime real ocioso e o recebimento humano permanecem provas distintas: **nenhum lembrete real recebido pelo usuário foi demonstrado nesta etapa**.

## Checklist das dez partes

- [x] 1. [Definir regras, intervalos, fusos e atrasos](regras-lembretes.md) — padrão 60/15 minutos e aviso de 24h opcional; registros 121/122.
- [x] 2. [Conferir canal e formato](canal-lembretes.md) — texto mínimo, janela de atendimento e limites dos templates; registro 123.
- [x] 3. [Persistir configuração e estados](persistencia-lembretes.md) — perfil/revisão, identidade, tentativas, histórico e observações imutáveis; registros 124/125.
- [x] 4. [Consultar a agenda e gerar avisos](geracao-lembretes.md) — consulta completa limitada e lote SQLite com CAS; registros 126/127.
- [x] 5. [Sincronizar alterações e cancelamentos](sincronizacao-lembretes.md) — GET exato, ausência qualificada e reconciliação atômica; registros 128/129.
- [x] 6. [Publicar a verificação periódica](lembretes-n8n.md) — Schedule Trigger de um minuto, API autenticada e exclusão entre processos; registros 130/131.
- [x] 7. [Enviar com reserva persistente](envio-lembretes.md) — duas leituras do evento, gate final de perfil/prazo/canal e proteção contra repetição; registros 132/133.
- [x] 8. [Recuperar somente tentativas seguras](recuperacao-lembretes.md) — lock positivo, snapshot/CAS e preservação de envio potencial; registros 134/135.
- [x] 9. [Testar o conjunto](testes-conjunto-lembretes.md) — relógio/SDK/SQLite, TCP/processos, reinícios e grafo distribuído; registros 136/137.
- [x] 10. Revisar entregas, README, evidências e PDF — este fechamento e os registros 138/139 de auditoria final e renderização do planner.

Os registros 121–137 conservam o resultado e o alcance de cada implementação. Os registros 138/139 pertencem ao fechamento; não representam nova rodada das suítes ou nova comprovação de envio. A numeração e os arquivos locais estão no [índice de evidências](evidencias.md).

## Comportamento entregue

Até três tipos estáveis aceitam intervalos de 1 a 10080 minutos ou desligado; o padrão é 60/15, com `day_before` opcional. O cálculo usa UTC e a apresentação, o IANA efetivo da agenda. Transporte só pode iniciar em `due <= now < min(due + 120 s, start)`, sem rajada retroativa após atraso/reinício.

A identidade combina agenda, ocorrência, início UTC, tipo e vínculo do destinatário. Título, fuso, revisão e execução n8n não criam outro aviso. Reagendamento invalida o início antigo; retornar a um início aceito/incerto não reabre envio. Cancelamento exige prova exata, não ausência numa listagem ou falha de consulta.

O envio reserva a identidade entre duas leituras exatas do evento; a transação final valida claim, perfil, relógio e canal antes de `sending`. O texto determinístico contém título/início/fuso, sem LLM ou escrita de eventos Google. Aceite persistido é `accepted`; falha após possível transporte mantém `uncertain`/`sending`, sem retry cego.

A recuperação exige posse positiva do lock de SO compartilhado. Reserva sem envio potencial pode voltar à validação ou expirar; `sending` abandonado vira `uncertain` com a mesma tentativa. Aceites/incertezas permanecem protegidos, sem TTL, `force` ou reset. O schema continua em **1**.

O workflow tem oito nós, três POSTs autenticados com `{}`, HTTPs de 120 s e limite total de 420 s, sem retries/redirects. Falha ou DTO incompatível interrompe a sequência. Commit desconhecido não vira rollback ou contagens zero; resultado conhecido conserva contadores, mas HTTP 503 impede a fase seguinte.

## Resultados e limites das provas

| Prova | Resultado confirmado | Limite |
|---|---|---|
| Suíte Python — 136 | 1.956 aprovados, incluindo 20 novos na parte 9; sem skips | Provedores e relógios substituídos nas fronteiras documentadas; resultado preservado, sem reexecução na parte 10 |
| JavaScript — 136 | 981 verificações em sete suítes, incluindo 19 novas do ciclo completo; sem skips | Harness executa código/conexões do export; não substitui o motor n8n |
| Calendário integrado — 136 | 14 percursos com serviços, gateway, SDK, transporte e SQLite reais | HTTP Google em memória e sender fictício; não comprova acesso ou entrega reais |
| TCP/processos — 136 | Seis cenários, 11 processos isolados, cinco reinícios e dez ciclos/30 envelopes consumidos pelo grafo | Rede restrita a loopback, zero tentativas externas e provedores fictícios |
| Runtime — 127, 129, 131, 133, 135 e 137 | Consultas/preflights ociosos, contratos autenticados, cinco grafos instalados e três publicados conferidos; ciclos naturais registrados | Agenda consultada sem eventos elegíveis e ledger real sem avisos/reservas a processar; nenhum transporte real de lembrete demonstrado |

O ledger real permaneceu vazio; não foram inseridos eventos ou mensagens sintéticas para fabricar um resultado. Consultas/contadores zero comprovam o percurso ocioso. Eventos, aceites, incertezas e interrupções foram exercitados em bases isoladas.

Sem payloads salvos, os logs naturais comprovam DTOs válidos `completed` ou `skipped`, sem atribuir contagens ou mensagens ao disparo. **`accepted` confirma aceite, não entrega/leitura**; nos testes, é simulado. Confirmações humanas anteriores de consulta, texto, imagem e template não são recebimento de lembrete.

## Reprodução e preservação

Prepare perfil/banco pelos guias de [persistência](persistencia-lembretes.md) e [geração](geracao-lembretes.md), com permissão explícita, não inferida do LLM ou `START`. [Sincronização](sincronizacao-lembretes.md), [envio](envio-lembretes.md) e [recuperação](recuperacao-lembretes.md) detalham operação e falhas. O [inventário n8n](../n8n/README.md) registra setup com `--apply`, versão publicada e export público inativo; reinício normal não exige reimportação.

O [guia do conjunto](testes-conjunto-lembretes.md#fontes-de-teste-e-reprodução) contém comandos e relatório TCP usado pelo harness. Os ensaios exigem subprocessos/loopback e bases isoladas, sem OAuth/callback reais. Casos impedidos ou ignorados não contam como aprovados.

Preserve `data/reminders.sqlite3`, histórico, `.env`, `secrets/`, volume n8n e demais bancos nos backups. Apagar/restaurar a base não autoriza reenvio incerto. Não publique bancos, credenciais, telefone, IDs, títulos, mensagens ou payloads brutos. Rotas/nós expõem correlação e agregados; o n8n mantém desativado o salvamento de sucesso, erro, manuais e progresso.

## Pendências fora do fechamento

O sandbox permanece limitado ao destinatário privado autorizado. Sem nova observação válida da entrada, a janela é desconhecida; texto exige janela de 24h com margem operacional local de 60 s. Fora dela, não há template contextual de lembrete configurado. Os templates fixos já testados não garantem avisos personalizados em qualquer horário.

PC suspenso, API ou Docker parados interrompem os pulsos. O endereço HTTPS do túnel é temporário e pode exigir novo cadastro quando mudar. A URL secreta do callback e o filtro de remetente não equivalem a assinatura criptográfica da Meta. O provisionamento Meta original, um canal/template adequado para produção e a validação humana de um lembrete real continuam pendentes.

Google, SQLite e WhatsApp não compartilham uma transação distribuída: ainda existe corrida entre a última leitura externa e o transporte. A recuperação local não oferece reconciliação de entrega no provedor nem liberação de resultado incerto. Esses limites permanecem explícitos no encerramento da etapa 12. **A próxima etapa do planner é a 13 — Resumos e preparo.**
