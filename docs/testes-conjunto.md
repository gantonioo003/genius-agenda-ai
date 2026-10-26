# Testes do conjunto — etapa 08, parte 7

Os oito cenários de `tests/test_agenda_integration.py` verificam percursos completos entre preparação do pedido, serviços, conector da agenda, SDK Google, transporte HTTP e SQLite. Cada teste usa um banco temporário e datas fixas, sem depender do relógio ou dos arquivos privados do projeto.

## Matriz de verificação

| Cenário | Resultado e proteções conferidos |
|---|---|
| Ciclo completo | Preparar, criar, consultar por dia/semana/mês, selecionar, editar, reagendar e excluir. Preservar o ID, exigir confirmação e impedir recriação automática do pedido após a exclusão. Conferir uma criação, duas alterações e uma exclusão. |
| Conflitos e repetições | Bloquear criação e reagendamento sobrepostos, inclusive quando a primeira página da consulta estiver vazia. Repetições com o mesmo ID de operação ou com outro ID reutilizam o evento. Após liberar o intervalo, a reserva pendente pode concluir com seu ID original. |
| Criação incerta e reabertura | Aplicar a criação, perder a resposta e falhar nas três tentativas de leitura de conferência. Manter a reserva `pending`; reabrir o mesmo arquivo SQLite com novo banco e cliente, recuperar por outro ID de mensagem e passar para `confirmed`, sem segundo POST. |
| Recuperação e confirmação desatualizada | Recuperar reagendamento após resposta perdida. Recusar a confirmação de exclusão obtida antes dessa mudança. Uma nova confirmação permite excluir; a resposta perdida da exclusão é conferida sem repetir PATCH ou DELETE. |
| Pedido ambíguo | Encontrar dois candidatos, pedir esclarecimento e não escrever. Após selecionar o ID exato, alterar somente o evento escolhido e preservar integralmente o outro. |
| Alteração concorrente | Simular mudança externa de ETag entre leitura e PATCH. Enviar `If-Match`, receber HTTP 412, não repetir a escrita e preservar título e local externos. |
| Madrugada, UTC e limites | Encontrar a entrevista nos dois dias atravessados e no dia equivalente em UTC. Detectar sobreposição, permitir intervalo iniciado no fim exclusivo e reutilizar o mesmo evento quando o pedido repetir os instantes em UTC. |
| Agenda principal | Recusar uma agenda marcada como principal durante a descoberta, antes de reservar identidade no banco ou enviar qualquer mutação. |

Além do resultado retornado, os testes conferem o histórico das chamadas HTTP, IDs, métodos, ETags, `sendUpdates=none`, estado remoto simulado e registros SQL de pedidos e operações. A reabertura usa o mesmo arquivo SQLite com novos objetos, sem aproveitar o estado em memória do cliente anterior.

## Camadas reais e fronteira simulada

Executam seu código de produção: serviços, regras de seleção e conflito, `GoogleCalendarGateway`, descoberta da agenda de teste, SDK Google Calendar, `AuthorizedHttp`, `SingleAttemptHttp` e banco SQLite temporário. O SDK usa sua descoberta estática local e credenciais anônimas, sem acessar OAuth. As tentativas de leitura continuam executando a política do SDK; apenas a espera entre elas é removida para o teste.

Somente a sessão `requests.Session` é substituída por `tests/calendar_http_fake.py`. O helper mantém os eventos em memória, devolve respostas HTTP e permite injetar resposta perdida, leitura HTTP 503 e alteração concorrente. Ele implementa paginação simples, mas não expande recorrências, não projeta `fields` e não reproduz o protocolo inteiro do Google.

Portanto, este marco é uma **validação local de integração entre camadas**. Não acessa Google, rede, credenciais pessoais, WhatsApp ou n8n. Os testes específicos existentes continuam responsáveis pelos demais contratos, inclusive respostas malformadas e ocorrências recorrentes. As verificações reais registradas nas partes anteriores permanecem evidências distintas.

## Reproduzir

Na raiz do repositório, executar somente os oito cenários:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_agenda_integration.py -v
```

Executar também a suíte completa:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Resultado em 22/10/2026

As rodadas na preparação isolada e no repositório passaram com **359 testes**: os 351 anteriores e **8 novos testes integrados**, sem falhas, erros, testes ignorados ou IDs de teste duplicados. Não foram necessárias mudanças no código da aplicação ou nas dependências.

O runner de auditoria bloqueou `socket.create_connection`, `socket.socket.connect` e `socket.socket.connect_ex` desde a descoberta dos testes: **zero tentativas de conexão** nas duas rodadas. Os arquivos reais de configuração, cliente OAuth, token e banco da aplicação foram conferidos antes e depois e permaneceram iguais. A checagem de hashes não publicou os valores dos arquivos privados.

O registro local `data/evidencias/047_testes_conjunto_validacao_local.json` preserva os resultados por teste, as contagens HTTP simuladas dos oito percursos, a saída real do runner e os hashes dos arquivos Python testados. O bloqueio adicional de sockets pertence à rodada de auditoria registrada; os comandos de reprodução acima usam o runner padrão do `unittest` e as mesmas simulações dos testes.

**Parte 7 concluída. Etapa 08 encerrada em 22/10/2026**, com 359 testes aprovados e código conferido por hashes. O [fechamento da etapa](fechamento-etapa-08.md) consolida resultados e limites. Próxima: **etapa 09 — operações na API e fluxos n8n**.
