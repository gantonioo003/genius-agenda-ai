# Resumos com IA — etapa 13, parte 3

> **Atualização do bloco B:** o resumo já está [conectado ao WhatsApp](preparo-whatsapp.md), com apresentação simples por dia e horários legíveis. API/CLI e o papel limitado da IA foram preservados. As provas e observações de runtime abaixo descrevem o marco histórico da parte 3.

**Marco da parte 3, implementado em 26/10/2026 por API avulsa e CLI.** A coleta fornece os fatos. A IA escolhe a organização e até três destaques de um catálogo calculado pelo Python; o Python monta o texto, preservando datas, horários, títulos e totais. Não é redação factual livre pelo modelo. Naquele marco, a intenção ainda não estava ligada ao WhatsApp.

## Uso

```powershell
.\.venv\Scripts\python.exe -B -m app.summarize_agenda --period week --date 2030-06-19
.\.venv\Scripts\python.exe -B -m app.summarize_agenda --period month --metadata-only
```

A CLI usa o provedor configurado, sem alterar `.env` ou exigir nova chave. `--metadata-only` omite o texto e títulos da saída, preservando agregados, período e modo de geração. Erro de leitura termina com exit code 1 e código seguro; alternativa factual bem-sucedida termina com 0 e motivo explícito.

```text
POST /api/v1/agenda/summary
Authorization: Bearer <chave privada já configurada>
Content-Type: application/json
```

```json
{"period":"month","date":"2030-06-19"}
```

`period` aceita somente `week`/`month`; `date` ISO é opcional e, quando ausente, usa hoje no fuso efetivo da agenda. A fronteira herda Bearer/correlação e `Cache-Control: no-store`. Não aceita query, campos extras, JSON com chaves duplicadas, corpo acima de 4.096 bytes, outro content-type ou datas ambíguas. Não aceita prompt, contexto enviado pelo cliente, ID de conversa, agenda ou escolha de fuso. Autenticação e validação vêm antes de factories, Google ou modelo. A espera de corpo tem limite de dois segundos, e o trabalho síncrono roda fora do loop ASGI.

O envelope usa `action: summarize_agenda`, `operation_id: null`, `mutation_state: not_applicable` e `data.execution_allowed: false`. Os [exemplos de entrada](../examples/preparation/summary-requests.json) não contêm credenciais. O processo API persistente não foi reiniciado neste marco: a rota nova foi verificada em ASGI e a validação real usou a CLI instalada. Um processo anterior precisa do restart normal `python -m app` para carregar as rotas novas.

## Organização por IA sem invenção factual

1. Validar entrada e coletar novamente a fonte completa pelo [serviço da parte 2](coleta-contexto-agenda.md).
2. Revalidar schema, vínculo com período/data pedidos, contagens, dias, detalhes, instantes e flags. Fonte inválida/incompleta falha antes do modelo e não produz resumo vazio.
3. Guardar cópia canônica imutável dos fatos. Alterar o dict original durante uma chamada não muda o texto final.
4. Calcular no backend um catálogo `H1…H8` de frases factuais: totais, confirmados/provisórios, situação temporal, dias com compromissos e omissões. Não atribuir importância, disponibilidade ou prioridade.
5. Enviar ao modelo somente período, contadores e catálogo, sem títulos, IDs, links, descrições, participantes, telefone ou contexto inteiro. O limite de entrada configurado, até 4.000 caracteres, continua aplicado.
6. Aceitar exclusivamente um plano JSON com duas chaves: `layout` (`chronological` ou `upcoming_first`) e `highlights` (zero a três referências distintas existentes no catálogo).
7. Renderizar por Python os fatos preservados e os destaques selecionados. O plano nunca executa ferramenta, escrita ou envio.

O modelo não recebe títulos porque não precisa deles para escolher layout e destaques de agregados. Eles aparecem somente no texto renderizado localmente. Isso reduz o compartilhamento de texto livre; o período/contagens continuam sendo dados derivados da agenda quando se usa uma agenda real. A chamada usa o adaptador configurado e não cria sessão remota de conversa.

O parser rejeita chaves extras/duplicadas, NaN, referências desconhecidas/repetidas, mais de três destaques, comandos, fatos livres e plano acima de 2.048 bytes UTF-8. Aceita JSON simples ou um único bloco `json` cercado, sem prefácio. Identificador do modelo e metadados de uso precisam respeitar o contrato comum. A resposta bruta do provedor não é ecoada, persistida ou usada como narrativa alternativa. Um formato válido não permite introduzir título, empresa, horário, contagem ou ação: esses campos nem existem no plano aceito.

`AgendaSummaryService` depende da coleta e da interface de modelo existente; `summary_plan.py` valida, projeta, interpreta o plano e renderiza. Os adaptadores Gemini/OpenAI, a coleta de contexto e os cinco exports n8n permanecem byte a byte iguais. Não houve nova dependência, schema/banco, prompt das intenções antigas ou mudança de confirmação/rascunho. Há somente instrução nova e específica do organizador estrutural.

## Alternativa factual e erros

| Situação | Resultado |
|---|---|
| Plano válido sobre fonte não vazia | HTTP 200, `summary.mode: ai_organized`, `reason: null`, identificador seguro do modelo |
| Período vazio confirmado | HTTP 200, modo `factual`, motivo `empty_period`; não constrói modelo |
| Projeção excede o orçamento de entrada | HTTP 200, modo `factual`, `context_budget_exceeded`; fatos completos preservados, sem chamada |
| Chave ausente, limite, timeout, falha ou plano inválido | HTTP 200, modo `factual`, motivo controlado; fatos renderizados e modelo `null` |
| Entrada inválida | HTTP 422, `invalid_request`, `data: null`; sem fonte/modelo |
| Setup indisponível/limite da coleta | HTTP 503, `calendar_setup_unavailable`/`agenda_context_limit_exceeded`, `data: null` |
| Falha de leitura/fonte inválida | HTTP 502, `agenda_collection_failed`, `data: null`; sem modelo e sem resumo inventado |

Motivos do modelo são os códigos fechados `llm_not_configured`, `llm_unavailable`, `llm_invalid_response`, `llm_credentials_rejected`, `llm_rate_limited` e `llm_timeout`. Há no máximo uma construção de modelo e uma chamada `generate` por pedido elegível, sem segunda tentativa de geração. As retentativas de leitura do calendário continuam conforme a parte 2; isso não é retry de IA ou de gravação.

`generation.model` só é preenchido quando um plano real foi aceito. Alternativa factual não é rotulada como IA, não oculta erro de Google e não usa memória antiga como agenda atual. Erro interno de renderização não é declarado sucesso.

## Texto, períodos e limites

O texto contém período/fuso, instante UTC da consulta, totais conferidos, provisórios, situação temporal, dia inteiro, detalhes com datas/horários e omissões explícitas. Layout cronológico preserva a ordem da fonte; `upcoming_first` agrupa em andamento, futuros e encerrados, sem afirmar importância. O total é por ocorrência; somar contagens dos dias não equivale ao total se houver compromissos multidia.

O renderer mantém até dez eventos detalhados da coleta e todos os horários completos, com limite interno de 3.500 caracteres. Se necessário, abrevia somente títulos e avisa. `details` descreve a projeção da fonte, e o rodapé informa quantos eventos estão sem detalhes individuais. Os detalhes omitidos não são enviados ao modelo nem analisados individualmente. O restante das contagens vem da fonte completa. Não há sugestão de horário livre, briefing, alteração automática, múltiplas mensagens ou alerta proativo nesta parte.

Datas e UTC/DST seguem a coleta. A validação aceita o dia de 23h com meia-noite local normalizada de São Paulo em novembro/2018 e mantém recusa do dia civil eliminado de Apia em dezembro/2011. A correção evita rejeitar uma fonte temporal válida só por uma política mais restrita introduzida no gerador.

## Provas

**2.126 testes Python aprovados, 82 novos, sem skips.** São 29 do plano/renderizador, 22 do serviço, 18 da API ASGI, oito da CLI e cinco percursos com adaptador Gemini/SDK de calendário e HTTP fictício. A suíte completa conserva as operações anteriores. As 981 verificações JavaScript são históricas, sem nova execução; suas fontes e cinco exports não mudaram. Registro 143 com hashes e log finais.

A revisão independente aprovou 12 grupos de probes, incluindo 186 combinações de layout/destaques com fonte de 2.000 eventos/dez detalhes: o maior texto observado teve 3.066 caracteres. Cobriu JSON/fatos falsos, fontes inválidas, cópia imutável, fallback único, DST, Bearer antes de corpo/factories e saída de metadados. Fixtures não comprovam chamada ao provedor real.

Separadamente, o Gemini real `gemini-3.5-flash-lite` aceitou um plano com três eventos inteiramente fictícios. Nenhum Google foi aberto nesse probe, e o exemplo renderizado guardado na evidência 144 é 100% fictício. Depois, com autorização explícita para compartilhar somente os agregados descritos acima, a CLI instalada conferiu a agenda real: **0 evento(s) na semana** e **3 no mês**, referência de 26/10/2026. O mês não vazio foi `ai_organized`; o período vazio não precisa de IA. Foram registrados somente modo, período e agregados, sem títulos ou resposta bruta do modelo. Isso não é conversa ou recebimento WhatsApp, nem prova de que a rota já foi carregada num processo API anterior.

Os hashes das 230 fontes testadas, dos adaptadores/coletor antigos e dos cinco exports foram conferidos. `.env` e as configurações privadas de sandbox/agendamento conferidas foram preservadas; não se afirma congelamento de bancos/token OAuth, que podem ser atualizados pelos serviços existentes. O PDF fica para o fechamento da etapa 13. JSON demonstra este marco melhor que repetir uma captura sem interface nova.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*summary*.py'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_summarize_agenda.py'
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

**Parte 3/10 concluída.** Disponibilidade e preparo compacto foram implementados depois, e o bloco B integrou as três capacidades ao WhatsApp. O resumo teve confirmação humana antes da simplificação visual; a versão formatada foi conferida por leitura real instalada, sem novo envio. A prova API atual inclui HTTP real 200 em `ai_organized`. O fechamento da etapa continua pendente; consulte o [estado atual e os limites](preparo-whatsapp.md).
