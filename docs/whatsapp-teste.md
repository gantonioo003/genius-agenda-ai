# WhatsApp de teste — etapa 10

**Etapa 10 concluída em 24/10/2026 no escopo de WhatsApp de teste pela alternativa 360dialog Sandbox.** A parte 10 revisou integração, configuração, proteção, deduplicação, resposta fixa e ensaios separados de imagem/template. A última suíte aprovada tem 664 testes Python; as 502 verificações JavaScript são o resultado da parte 8, sem nova execução na parte 9. O usuário confirmou a resposta e, separadamente, imagem/template. A emissão original do número Meta continua pendente, e IA, operações de agenda por conversa, lembretes, briefings e interface seguem em etapas futuras. O [fechamento da etapa 10](fechamento-etapa-10.md) reúne checklist, reprodução e a auditoria final sem novos envios. O histórico dos marcos e da falha Meta está preservado abaixo.

## Parte 1: cadastro na Meta — concluída

**Em 23/10/2026**, o usuário criou sua conta do Facebook, concluiu o cadastro de desenvolvedor, criou o portfólio e o aplicativo `Genius Agenda AI` e selecionou o caso de uso WhatsApp. O aplicativo está em desenvolvimento e não foi publicado.

A configuração foi conferida diretamente no navegador interno após o login do usuário. A tela de entrada do WhatsApp apresentou os termos da Plataforma e da API em Nuvem; o usuário informou ter continuado, e a interface passou a mostrar a visão geral e o acesso à área “Etapa 1. Experimente”. O agente não clicou no aceite.

## Parte 2: número de teste pela Meta — pendente por erro de criação

Na área Experimente, o botão “Reivindicar número de teste” informa que serão gerados um telefone e um token para teste. A solicitação retornou **“Ocorreu um erro ao criar a conta de teste. Tente novamente.”**

Foram observadas **quatro tentativas: três no navegador interno e uma pelo usuário no Edge**. As três primeiras ocorreram antes da confirmação do e-mail de contato. Após a confirmação, conferimos novamente a ausência de número e fizemos uma única nova solicitação; o mesmo erro apareceu. O remetente continuou sem valor e o exemplo de chamada sem Bearer. As capturas 071/072/075 preservam os resultados antes e depois da confirmação. O painel “Ações necessárias”, com filtros abrangentes, informou que não há ações necessárias no momento. Essa ausência não identifica a causa do erro nem comprova a elegibilidade de todos os recursos da conta.

| Item | Estado observado |
|---|---|
| Cadastro de desenvolvedor e aplicativo | Criados; painel acessível e aplicativo em desenvolvimento |
| Portfólio empresarial | Criação confirmada; verificação empresarial não concluída |
| Aceite inicial do WhatsApp | Realizado pelo usuário; área Experimente acessível |
| Conta/número de teste | Quatro solicitações exibiram erro, inclusive após confirmar o e-mail; nenhum número disponível na interface |
| Token e destinatário | Token não exibido; destinatário não configurado neste percurso |
| Contato do perfil no portfólio | E-mail confirmado; aviso de confirmação observado e pendência removida |
| Webhook e mensagens pela Meta direta | Não conectados neste percurso; a alternativa do sandbox foi validada separadamente |

A interface não forneceu código ou motivo detalhado. Não atribuímos o erro à idade da conta, a bloqueio ou a indisponibilidade geral e não há prazo confirmado de liberação. A comparação com o Edge foi realizada e o erro também apareceu lá. Na conferência somente de leitura do Business Suite, Contas do WhatsApp mostrou “Nenhuma conta do WhatsApp adicionada” e Contas de mensagens mostrou “No Messaging accounts added”, no portfólio selecionado. Essas listas vazias não são prova de inexistência de qualquer recurso no backend nem identificam a causa do provisionamento.

## Contato confirmado e próximo passo

O registro 073 mostra o estado histórico de **“Confirmação pendente”**. Após o usuário concluir a confirmação, observamos o aviso **“Email address confirmed.”** e a ausência da pendência nas informações de contato do perfil. A captura 074 preserva esse estado; o aviso já havia desaparecido no momento da imagem.

A confirmação do e-mail não resolveu a criação da conta de teste: a tentativa seguinte retornou o mesmo erro, registrado na captura 075. A verificação empresarial permanece separada e não concluída; a interface não identificou isso como causa do erro.

Foi preparado um relato local em `data/diagnosticos/relato-meta-conta-teste.md`, com identificadores do app/portfólio, sequência das tentativas e referências às capturas. O relato não foi enviado. Na sessão autenticada, a [ferramenta de relato de bugs](https://developers.facebook.com/support/bugs/report/) exibiu **“Não há acesso à ferramenta de relato de bug da plataforma”**. A tela exige cadastro de desenvolvedor e função de administrador ou desenvolvedor em pelo menos um app com mais de 24 horas de existência. A captura 076 preserva o aviso. Esse requisito é para acessar a ferramenta; não explica nem promete resolver a criação do número. Após o app cumprir o requisito de idade, conferir o acesso e usar o relato preparado, mantendo o mesmo app e portfólio. Não há causa ou prazo de liberação do número confirmado.

A [documentação oficial do WhatsApp](https://developers.facebook.com/documentation/business-messaging/whatsapp/support) aponta suporte ao desenvolvedor, fórum e ferramenta de bugs; descreve Suporte Direto para desenvolvedores empresariais, como parceiros ou parceiros gerenciados. Nenhuma solicitação foi enviada a esses canais. A investigação da Meta foi preservada; o desenvolvimento seguiu pela alternativa descrita abaixo, com validação real de recebimento.

Durante aquele diagnóstico da Meta, nenhum número pessoal foi registrado como remetente, nenhuma forma de pagamento foi adicionada, nenhum novo app/portfólio foi criado na recuperação e nenhum token foi salvo no projeto. Aquele marco não alterou código, fluxos n8n ou eventos do Google.

## Diagnóstico por outros caminhos

Em 23/10/2026, também consultamos a Página Inicial do Suporte para Empresas. Perfil e portfólio apareceram sem problemas de publicidade; o aplicativo constava sem problema da fonte de dados nos últimos 30 dias e a lista de contas de mensagens mostrava zero. Esses resultados não comprovam elegibilidade para o WhatsApp nem identificam a causa da falha.

Abrimos o WhatsApp Manager pelo menu Todas as ferramentas e pelos links da configuração de produção do próprio app. As rotas de visão geral e números exibiram **“Esse conteúdo não está disponível no momento”**. A captura 077 preserva esse resultado. Na configuração de produção, inspecionamos as opções de webhook e registro comercial sem preencher campos nem cadastrar números. Não encontramos uma segunda emissão de número de teste nas opções inspecionadas. O console consultado não forneceu um código específico de provisionamento.

### Alternativa implementada: 360dialog Sandbox

A [documentação oficial](https://docs.360dialog.com/docs/get-started/sandbox) descreve ativação por `START` para `+55 11 4673-3492`, seguida de chave própria. Permite texto livre para um destinatário fixo e exige webhook HTTPS. Limita a 200 requisições, inclusive rejeitadas; templates são predefinidos e mídia por ID não está disponível.

**Recebimento real confirmado em 23/10/2026.** O usuário enviou `START`, recebeu a chave e a salvou no `.env` privado. O registro 079 preserva a preparação histórica sem valor ou hash da credencial; o print com a chave não integra o acervo.

Implementamos uma entrada Python restrita, um túnel HTTPS temporário e um fluxo n8n separado e autenticado. O registro autenticado do callback retornou 200, e a consulta posterior confirmou o mesmo endereço. A mensagem real seguinte alcançou o fluxo e a consulta de status da API, com confirmação HTTP 200.

Na conexão inicial, ajustamos a validação para o envelope real que traz telefone e identificador de escopo juntos, reiniciamos a entrada e confirmamos o recebimento seguinte. Naquele marco, passaram 478 testes Python e 470 verificações JavaScript. O registro 080 separa os testes sintéticos daquela mensagem real; a captura 081 preserva o canvas publicado.

**Parte 6 — implementação da proteção da entrada concluída:** o receptor exige `secrets/whatsapp-sandbox-security.json`, com um único campo `allowed_phone`. O configurador `scripts/configure_sandbox_sender.py --apply` solicita por entrada oculta a representação exata do telefone usada pelo provedor para o WhatsApp autorizado, removendo somente `+`, espaços, parênteses e hífens. Ele falha se a entrada oculta não estiver disponível e não substitui outro identificador já configurado. Não há normalização automática do nono dígito, aliases ou aprendizado por callbacks. O arquivo anterior de quatro campos do workflow foi preservado.

Mensagens precisam trazer `from` igual ao telefone autorizado; status precisam trazer `recipient_id` correspondente. Telefone diferente ou evento somente com BSUID recebe 403. Um identificador adicional com formato limitado pode acompanhar o telefone correto, mas não libera um telefone errado. Todo o lote é verificado antes do n8n; qualquer evento proibido rejeita o conjunto sem recibo de sucesso.

Na implementação da proteção passaram 499 testes Python, incluindo 43 do receptor e 7 do configurador, e 20 verificações HTTP sintéticas; esse marco foi preservado no registro 082. O primeiro teste real após ativar o filtro recebeu 403 porque o identificador não coincidiu exatamente com a política. Mantivemos o bloqueio.

A mensagem seguinte inicialmente não chegou porque o túnel público perdeu conectividade. Recuperamos somente os processos verificados do túnel, validamos a nova origem HTTPS e registramos/conferimos o callback com POST e GET 200. O reenvio real de “segurança 2”, às **20:06:43.179954 UTC**, chegou e recebeu 403. A comparação booleana confirmou `all_events_phone_matches_policy: false` e `all_events_phone_matches_br_legacy: true`, sem persistir telefone ou conteúdo. Isso comprovou a relação de representação neste ambiente, sem estabelecer uma regra geral para números brasileiros.

Somente então vinculamos explicitamente um único identificador à política privada, preservando a versão anterior em `secrets/whatsapp-sandbox-security.before-identity-binding.json`, e reiniciamos o receptor. O código público manteve autorização exata; a comparação de formato legado não autoriza mensagens. Cinco casos HTTP finais passaram: identificador fixado 200; representação original, outro telefone, BSUID isolado e lote misto 403.

O total passou a **26 verificações HTTP sintéticas**: 20 iniciais, um probe de recuperação e cinco finais. Nenhuma mensagem de saída ou operação Google foi executada. Os 499 testes Python e as 470 verificações JavaScript são resultados anteriores preservados; não foram repetidos na retomada, que modificou configuração e processos. O registro 083 documenta essa distinção.

**Confirmação real após o vínculo final:** o novo envio do usuário chegou às **20:09:30.176124 UTC**, com diagnóstico `succeeded`, HTTP 200 e recibo UUID `2487cab1-9409-4678-a991-b639604c7810`, uma mensagem, nenhum status e tipo `message_text`. O diagnóstico confirmou correspondência exata com a política; o booleano de formato legado foi falso e não participou da autorização. Sete recibos sintéticos foram separados dos recebimentos reais. Essa prova histórica fecha a parte 6 de segurança da entrada. A implementação e a validação posteriores da parte 7 estão separadas a seguir.

Somente contagens, tipos e um UUID técnico seguem para o n8n. Texto, telefones e IDs originais dos callbacks não são persistidos na inbox ou nos registros de recebimento, nem encaminhados ao fluxo. A inbox acrescentada na parte 7 guarda hashes de identidade e metadados técnicos; hashes persistentes são pseudônimos e precisam permanecer privados. O identificador escolhido explicitamente para autorização fica na política privada, com a versão anterior no backup privado desta intervenção; ambos são ignorados pelo Git. **Na conexão e na segurança iniciais, o retorno HTTP confirmava somente o webhook, sem mensagem ao usuário.** A parte 8 acrescentou depois uma resposta fixa; interpretação e operações da agenda por WhatsApp continuam pendentes.

O caminho secreto e o filtro de telefone limitam o acesso, mas não comprovam autoria criptográfica. A inbox adiciona controle de replay por identidade e estado; ela também não comprova a origem de um callback. A [documentação específica do sandbox](https://docs.360dialog.com/docs/get-started/sandbox) não documenta assinatura HMAC ou headers customizados nos callbacks. Headers de autenticação existem na documentação geral de produção; não foram presumidos para esse sandbox. Essa distinção e as fontes estão no [guia da integração](whatsapp-sandbox.md#proteções-e-limites-atuais).

O [guia do sandbox](whatsapp-sandbox.md) documenta a reprodução, o registro do endereço privado, a retomada dos serviços e os limites. A compatibilidade com toda a entrega final ainda precisa ser validada.

## Parte 7: mensagens repetidas — concluída

A entrada passou a usar `data/whatsapp-inbox.sqlite3`, schema 1, separado do banco da agenda, preservado no schema 2. Mensagens usam o hash de namespace + categoria + ID do provedor; mudar texto, tipo ou timestamp não altera sua identidade. Status usam ID + fase e ignoram o timestamp, permitindo fases fora de ordem. IDs iguais entre mensagem e status ficam separados. Não há reconstrução das identidades dos recibos históricos.

Todo o lote é autenticado, validado e autorizado antes de derivar hashes ou reservar eventos. O claim persiste `processing` antes da chamada ao n8n. Repetições já `processed` recebem 200 sem novo encaminhamento ou recibo. Em lote com concluídos e novos, só os novos seguem; qualquer evento `processing`/`uncertain` bloqueia o lote com 503 sem inserir suas identidades novas. A confirmação no banco antecede o JSONL auxiliar: falha no recibo depois de concluir mantém 200, evitando nova execução.

Não existe expiração, lease ou reexecução automática. Uma queda após a reserva pode bloquear inclusive um evento ainda não encaminhado; reconciliar o resultado exige decisão explícita. Não é uma garantia de execução exatamente uma vez. Preserve a inbox e backups consistentes; corrupção, versão incompatível ou uso de outra base impedem iniciar o receptor. Consulte o [guia de deduplicação](whatsapp-deduplicacao.md) para os estados e a recuperação.

Passaram **551 testes Python**: 499 anteriores +18 casos do receptor +34 do store. Os conjuntos específicos têm 61 testes do receptor, 34 do store e 7 do configurador. As 470 verificações JavaScript são o resultado histórico preservado, sem nova execução.

A integração foi validada em **22 cenários/23 requisições HTTP loopback**, com destino fixo `http://127.0.0.1:8787`, IDs/dados fictícios e n8n/API reais locais. Nove identidades novas geraram nove encaminhamentos/consultas de status e nove recibos. Repetições mantiveram os totais, inclusive após reinício real do receptor; um ID novo depois do reinício foi aceito. IDs originais, telefone e texto não foram encontrados no banco, journals, recibos ou diagnósticos. O telefone autorizado foi usado somente em memória no percurso local.

O ensaio inicialmente proposto pelo HTTPS público com telefone privado foi rejeitado pela revisão automática e não executado. A alternativa ficou restrita ao loopback: **não foi reenvio real do provedor/WhatsApp nem validação pública HTTPS da deduplicação**. As provas reais anteriores 080–083 continuam separadas. Não houve mensagem de saída ou ação de agenda; o canvas n8n não mudou e não recebeu outro print. Os registros 084/085 documentam esse marco da parte 7; a parte 8 foi implementada posteriormente.

## Parte 8: resposta fixa — implementação concluída

Em 24/10/2026, substituímos o GET de status do fluxo por `POST /api/v1/whatsapp/sandbox/reply`, com Bearer e UUID coincidente no corpo/header. A API consulta um claim íntegro em `processing`: mensagens novas permitem uma resposta fixa por claim; somente status recebe `skipped`, sem envio. O n8n continua sem telefone, texto, ID original ou chave do provedor.

A saída reserva `sending` em `data/whatsapp-outbox.sqlite3` (schema 1) antes de chamar o endereço fixo da 360dialog. Aceite válido e persistido vira `accepted`; falhas incertas ficam bloqueadas em `uncertain` ou `sending`, sem expiração ou retentativa automática. Um aceite já registrado pode ser reutilizado sem novo envio enquanto o mesmo claim ainda está ativo. Inbox schema 1 e agenda schema 2 foram preservados. `accepted` corresponde a `mutation_state: confirmed`, mas não comprova entrega/leitura. Resultado incerto usa `unknown`.

Passaram **613 testes Python** (551 anteriores +12 de consulta de claims +31 de transporte/outbox +19 de serviço/API) e **502 verificações JavaScript**, com as cinco suítes executadas nesta entrega. O [guia de respostas](whatsapp-respostas.md) detalha limites por camada, contratos, privacidade e recuperação.

O ensaio de saída desta parte tem origem sintética local, percorre n8n/API locais e usa o provedor real para o destinatário previamente autorizado; não representa uma nova mensagem real de entrada por WhatsApp/HTTPS. Passaram **nove verificações HTTP**: o percurso iniciado por POST sintético loopback produziu um aceite real da 360dialog, persistido em outbox `accepted` e inbox `processed`. Repetições do mesmo ID, inclusive com texto alterado, não enviaram outra resposta; somente status não reservou saída. Foram conferidos os bloqueios de autenticação, claim desconhecido/concluído e campo extra. Essa prova não comprova entrega/leitura no aparelho. O [resultado efetivo](whatsapp-respostas.md#resultado-do-ensaio-real) está separado em 087; 086 contém os testes locais. A recusa automática do ensaio HTTPS da parte 7 pertence àquele histórico, cujo resultado continua separado. Interpretação, ações de agenda e demais entregas continuam fora do escopo concluído da integração de teste.

## Parte 9: imagem e template — concluída

Os ensaios acrescentaram métodos estreitos ao sender para imagem HTTPS e template predefinido, sem um envio de payload arbitrário. A execução é separada do produto ativo: `scripts/test_whatsapp_media.py --kind image|template --operation-id <UUID>` mostra prévia sem rede; `--execute` envia explicitamente ao WhatsApp já autorizado. Não foi adicionada rota ou conexão n8n para mídia.

O ledger `data/whatsapp-media-probes.sqlite3`, schema 1, vincula UUID, caso e hash do payload/destinatário. O destinatário entra no hash somente em memória e não fica armazenado como telefone. O vínculo impede trocar o conteúdo de uma operação; aceites são reutilizados e resultados pendentes/incertos não autorizam reenvio automático. Inbox, outbox e agenda anteriores foram preservados.

Uma imagem por link público e o template `disclaimer/en` com `components: []` tiveram **dois aceites reais**, um por caso. Repetir as mesmas operações retornou `reused: true`, sem nova chamada ao provedor. A imagem foi um JPEG público do MDN de 39.662 bytes, 660×480 e 8 bits, usado somente para testar transporte; não é card de entrevista ou imagem de IA. A prova registra aceite do sandbox, com confirmação humana do recebimento dos dois itens, sem atribuir status de entrega/leitura do provedor.

Passaram **664 testes Python** (613 anteriores +20 do sender +18 do ledger +13 de serviço/CLI). As **502 verificações JavaScript** pertencem à parte 8, sem nova execução; todos os arquivos n8n e manifestos ficaram idênticos. Código da API, receptor, inbox e outbox foi preservado. Os registros 088–090 separam testes locais, imagem pública e aceites reais. O [guia de mídias/templates](whatsapp-midias-templates.md) detalha restrições de mídia por ID, templates personalizados, tamanho e janela de atendimento, com fontes. A parte 10 foi concluída depois, na revisão deste escopo de teste; veja o [fechamento da etapa 10](fechamento-etapa-10.md).

## Parte 10: revisão e fechamento — concluída

A alternativa de teste 360dialog foi fechada com limites e proveniência separados: entrada real histórica, resposta real disparada por entrada sintética e mídia por CLI. A confirmação humana da resposta e dos dois itens foi preservada. O [relatório final](fechamento-etapa-10.md) centraliza checklist, comandos e os resultados operacionais do registro 091. O provisionamento Meta original permanece sem solução; este fechamento não conclui o produto inteiro.

## Evidências preservadas

- **069_meta_portfolio_criado.png:** print original do usuário confirmando o portfólio, antes de concluir o app.
- **070_meta_whatsapp_aceite_pendente.png:** captura real anterior ao aceite; não representa o estado atual.
- **071_meta_erro_conta_teste.png:** captura real da segunda tentativa, com erro e botão desabilitado. Não comprova emissão de número ou token.
- **072_meta_erro_conta_teste_edge.png:** print original enviado pelo usuário, preservado sem edição; mesmo erro na tentativa pelo Edge.
- **073_meta_email_confirmacao_pendente.png:** captura direta do Business Suite, com confirmação de e-mail pendente e opção Receber código; estado histórico anterior à confirmação.
- **074_meta_email_confirmado.png:** captura direta do contato após a confirmação, sem o indicador de pendência.
- **075_meta_erro_apos_email_confirmado.png:** captura direta da única nova solicitação após confirmar o e-mail; mesmo erro genérico e nenhum número disponível na interface.
- **076_meta_acesso_relato_bug.png:** captura direta do aviso de indisponibilidade da ferramenta de bugs e dos requisitos de acesso; nenhuma submissão realizada.
- **077_meta_whatsapp_manager_indisponivel.png:** captura direta do Manager indisponível por um link indicado no painel.
- **078_diagnostico_alternativas_whatsapp.json:** relato histórico das verificações reais adicionais e da alternativa apenas pesquisada naquele marco.
- **079_sandbox_chave_emitida_preparacao_local.json:** observação da emissão apresentada pelo usuário e preparação do campo privado; sem chave, hash ou cópia do print com credencial.

- **080_whatsapp_sandbox_integracao_real.json:** resultados locais, registro do callback, verificações HTTP e um recebimento real confirmado, sem conteúdo ou credenciais.
- **081_n8n_sandbox_recebimento_publicado.png:** canvas real dos sete blocos com estado Published no marco inicial; demonstra configuração, e o registro 080 comprova o percurso executado.
- **082_whatsapp_sandbox_seguranca_entrada.json:** marco histórico da proteção obrigatória por telefone, 499 testes Python e 20 verificações HTTP sintéticas, antes da confirmação do formato.
- **083_whatsapp_sandbox_remetente_confirmado.json:** recuperação da conectividade, comparação booleana do reenvio real rejeitado, vínculo explícito de identidade, 26 verificações HTTP sintéticas acumuladas e nova mensagem real autorizada HTTP 200; recibos sintéticos e reais separados.

- **084_whatsapp_deduplicacao_testes.json:** resultado de 551 testes Python e regras de reserva, conclusão e bloqueio da inbox.
- **085_whatsapp_deduplicacao_http_local.json:** 22 cenários/23 requisições loopback com n8n/API reais locais, nove identidades novas, concorrência, reinício real e conferência dos dados persistidos.

- **086_whatsapp_respostas_testes.json:** implementação da resposta fixa e validação de 613 testes Python/502 verificações JavaScript.
- **087_whatsapp_resposta_envio_real.json:** resultado e proveniência do ensaio de saída pelo provedor real, a partir de entrada sintética local.
- **088_whatsapp_midias_templates_testes.json:** validação da parte 9 com 664 testes Python; JavaScript histórico e fluxo ativo preservados.
- **089_whatsapp_imagem_publica_teste.jpg:** JPEG público usado para testar envio por link, sem representar um card do produto.
- **090_whatsapp_midias_templates_envios_reais.json:** dois aceites reais de imagem/template e replay reutilizado sem novo envio.

- **091_fechamento_etapa_10.json:** revisão integrada da alternativa de teste, configuração/exports e verificações finais sem novos envios.

Os arquivos originais ficam em `data/evidencias/`, fora do Git, com índice e cópia espelhada no acervo do trabalho.
