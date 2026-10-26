# WhatsApp: mídias e templates

Revisão: **24/10/2026**.

**Complemento em 25/10/2026 — etapa 12, parte 2:** a [revisão do canal dos lembretes](canal-lembretes.md) define formato de texto e bloqueio fora de janela válida sem template contextual. O `disclaimer` deste ensaio comprova envio de um modelo fixo, não lembrete personalizado fora de janela. O agente dinâmico substituiu a resposta fixa na etapa 11; esta página preserva o ensaio histórico de mídia/template.

**Etapa 10 de teste concluída pela alternativa 360dialog:** a parte 10 revisou e documentou as capacidades verificadas, com confirmação humana de imagem/template preservada e sem novos envios. O [fechamento da etapa 10](fechamento-etapa-10.md) centraliza checklist, reprodução e verificações finais; o bot ativo permanece com resposta fixa, e o provisionamento Meta original não foi resolvido.

Esta parte verifica o envio de uma imagem e de um template pelo sandbox 360dialog, por uma CLI manual separada. A API e o fluxo n8n de resposta automática continuam com seu escopo atual.

## Capacidades documentadas e verificadas

| Recurso | Situação |
| --- | --- |
| Templates do sandbox | `disclaimer/en` aceito no ensaio real; os outros modelos do inventário não foram enviados. |
| Imagem por URL pública | Uma imagem por link foi aceita no ensaio real do sandbox; isso não testa todos os formatos/tamanhos. |
| Upload ou recuperação por `media ID` | Indisponível no sandbox. |
| Criar ou editar templates | Indisponível no sandbox. |

Fontes: [sandbox 360dialog](https://docs.360dialog.com/docs/get-started/sandbox) e [mensagens de mídia](https://docs.360dialog.com/docs/messaging/media).

## Limites aplicáveis

- A chave do sandbox atende um único destinatário. A cota é de **200 requisições à API de mensagens**, incluindo rejeições; ao esgotá-la, retorna HTTP 429. [Fonte](https://docs.360dialog.com/docs/get-started/sandbox).
- Mensagens livres dependem da janela de **24 horas após uma mensagem do usuário**. Fora dela, é necessário um template aprovado; o sandbox não documenta uma exceção. [Fonte](https://docs.360dialog.com/docs/messaging/overview).
- A referência geral aceita imagens **JPEG/PNG, até 5 MB**, com 8 bits e cores RGB/RGBA. O teste usa JPEG muito abaixo desse limite. [Fonte](https://docs.360dialog.com/docs/messaging/media/upload-retrieve-or-delete-media#supported-media-types).
- `image.link` deve apontar para o arquivo acessível por HTTP(S), e não para uma página HTML. Aceitação pela API ainda pode ser seguida de falha ao obter a mídia; entrega exige conferir os status ou o WhatsApp. [Fonte](https://docs.360dialog.com/docs/messaging/media).

## Templates predefinidos

| Nome exato | Idioma dos exemplos | Componentes |
| --- | --- | --- |
| `disclaimer` | `en` | `[]` |
| `first_welcome_messsage` | `en` | Um parâmetro de texto no corpo. |
| `interactive_template_sandbox` | `en` | Dois botões `quick_reply`, índices 0 e 1, com parâmetros `payload`. |

`first_welcome_messsage` tem três letras **s** em `messsage`. A documentação pede confirmar o inventário com a equipe Partner APIs. O teste selecionado usa somente `disclaimer`. [Fonte](https://docs.360dialog.com/docs/get-started/sandbox).

Um template pode ter um cabeçalho de imagem quando essa estrutura é definida e aprovada. Isso não permite acrescentar um cabeçalho ao `disclaimer`. Um lembrete personalizado com imagem fora da janela exige um template próprio aprovado, recurso que não pode ser criado neste sandbox. [Elementos de templates](https://docs.360dialog.com/docs/resources/templates/template-elements).

## Contratos do teste

Destino fixo: `POST https://waba-sandbox.360dialog.io/v1/messages`, com `Content-Type: application/json` e chave privada no cabeçalho `D360-API-KEY`. [Fonte](https://docs.360dialog.com/docs/get-started/sandbox).

Imagem por link usada no ensaio de capacidade:

```json
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "<destinatario_autorizado>",
  "type": "image",
  "image": {
    "link": "https://interactive-examples.mdn.mozilla.net/media/cc0-images/elephant-660-480.jpg"
  }
}
```

Template:

```json
{
  "messaging_product": "whatsapp",
  "to": "<destinatario_autorizado>",
  "type": "template",
  "template": {
    "name": "disclaimer",
    "language": {"code": "en"},
    "components": []
  }
}
```

O destinatário real e a chave vêm apenas da configuração privada. Os exemplos não devem receber esses valores no Git.

## Imagem de referência

A imagem é um [exemplo público da MDN](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/figure), hospedado no [repositório de recursos MDN](https://github.com/mdn/shared-assets). Mostra um elefante ao pôr do sol, sem dados dos eventos ou do usuário.

Arquivo original verificado: JPEG, **39.662 bytes**, **660 × 480 pixels**, 8 bits, três canais. SHA-256:

```text
5e23375e9ccb0287e0ab7ea71b40acdfe41ca38323352ff12269382d469fb460
```

Essa foto é apenas uma referência para testar o transporte. O card contextual do produto, a geração de imagem e o briefing continuam em etapas futuras.

## Execução controlada e resultados

A CLI `scripts/test_whatsapp_media.py` usa `--kind image|template` e `--operation-id <UUIDv4>`. Exemplo de prévia, executado na raiz do repositório:

```powershell
.venv/Scripts/python.exe scripts/test_whatsapp_media.py --kind image --operation-id <UUIDv4>
```

O modo padrão apresenta a prévia sem rede; `--execute` autoriza o envio daquela operação. `--kind template` seleciona exclusivamente `disclaimer`, idioma `en`. Os casos fixos são `image_link_v1` e `template_disclaimer_v1`. O ensaio executou **uma tentativa de imagem e uma de template** ao destinatário já autorizado.

O registro separado `data/whatsapp-media-probes.sqlite3` associa a operação ao hash do payload completo e guarda estado, horários e hash do identificador de resposta. Não guarda telefone, texto ou identificador do provedor em claro. A mesma operação não deve repetir um envio aceito, iniciado ou incerto. Esse controle é local; não representa idempotência garantida pelo provedor.

Rejeições ou respostas perdidas ficam bloqueadas para repetição automática, sem fallback. O mesmo UUID com destinatário, caso ou conteúdo diferente é recusado antes da rede. O hash do binding inclui o destinatário apenas em memória; nenhum telefone é salvo em claro.

**Testes locais:** **664 testes Python aprovados**, sem falhas, erros ou ignorados: 613 anteriores, 20 de mídia no transporte, 18 do ledger e 13 do serviço/CLI. Os provedores foram substituídos e os bancos eram temporários. As 502 verificações JavaScript aprovadas na parte 8 são históricas; os arquivos n8n foram preservados e não houve nova execução JavaScript.

**Resultado real:** **Dois envios aceitos pela 360dialog**, um `image` por link e um `disclaimer/en`. Os dois registros terminaram `accepted`; repetir cada UUID retornou `reused: true` sem novo envio ou alteração do registro. Foram duas chamadas externas de teste, sem operação Google. Não se atribui a esse ensaio um teste fora da janela de 24 horas. Separadamente do aceite técnico, o usuário confirmou “Chegaram os dois”: imagem e template recebidos no WhatsApp. Essa confirmação humana não é um status de entrega/leitura do provedor.

Evidências registradas:

- **088:** resultados locais e JSON das capacidades verificadas.
- **089:** JPEG original usado como referência, preservando os bytes e o hash acima.
- **090:** JSON sanitizado das tentativas reais, distinguindo aceitação pela API de entrega confirmada.

As evidências públicas omitem chave, telefone, URL secreta do callback, conteúdo privado e identificadores originais. Hashes são pseudônimos técnicos, não uma garantia de anonimato.

**Parte 9 concluída nesse escopo de transporte e capacidades.** O envio foi uma execução manual explícita; o bot automático continua respondendo somente com o texto fixo da parte 8. Cards contextualizados, briefings, agendamento de lembretes e templates próprios continuam nas entregas futuras. A revisão da parte 10 concluiu somente a integração de teste pela alternativa 360dialog, conforme o [relatório final](fechamento-etapa-10.md) e o registro 091; essas entregas futuras não foram marcadas como prontas.
