'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {prepareReminderTick, normalizeReminderTick, normalizeReminderDispatch, normalizeReminderRecovery} = require('../lib/reminder-tick.cjs');
const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '../workflows/lembretes-periodicos.json'), 'utf8'));
const requestId = 'reminder-tick-42';
const sync = () => ({status: 'synchronized', events_checked: 3, tracked_reminders: 4,
  reminders_considered: 4, cancelled_count: 1, replanned_count: 1, unchanged_count: 2,
  state_counts: {pending: 2, blocked: 1, cancelled: 1}});
const generation = () => ({status: 'planned', events_seen: 3, eligible_events: 2,
  skipped_count: 1, reminders_considered: 4, state_counts: {pending: 3, blocked: 1}});
const api = () => ({statusCode: 200, body: {api_version: '1', request_id: requestId,
  operation_id: null, status: 'succeeded', action: 'reminder_tick', mutation_state: 'not_applicable',
  message: 'Verificacao local concluida.', data: {tick_status: 'completed', skip_reason: null,
    local_mutation_state: 'applied', synchronization: sync(), generation: generation(),
    messages_sent: 0, external_mutations: 0}, error: null}});
function skipped(reason = 'disabled') {
  const input = api();
  Object.assign(input.body, {status: 'skipped'});
  Object.assign(input.body.data, {tick_status: 'skipped', skip_reason: reason, local_mutation_state: 'not_applied'});
  for (const summary of [input.body.data.synchronization, input.body.data.generation]) {
    for (const key of Object.keys(summary)) summary[key] = key === 'status' ? 'not_run' : key === 'state_counts' ? {} : 0;
  }
  return input;
}
function failed(partial = false) {
  const input = skipped();
  input.statusCode = 503;
  input.body.status = 'failed';
  input.body.data.tick_status = partial ? 'partial' : 'failed';
  input.body.data.skip_reason = null;
  input.body.data.local_mutation_state = partial ? 'partial' : 'not_applied';
  if (partial) {
    input.body.data.synchronization = sync();
    input.body.data.generation.status = 'failed';
  }
  input.body.error = {code: partial ? 'reminder_tick_partial' : 'reminder_tick_failed', retryable: false, fields: []};
  return input;
}
const rejects = input => assert.throws(() => normalizeReminderTick(input, requestId), /^Error: reminder_tick_(invalid_response|failed|partial)$/);

test('request ID vem apenas da execucao, com prefixo e limite de 128 caracteres', () => {
  assert.deepEqual(prepareReminderTick('42'), {request_id: requestId});
  assert.deepEqual(prepareReminderTick(42), {request_id: requestId});
  assert.equal(prepareReminderTick('a'.repeat(114)).request_id.length, 128);
});
for (const id of ['', null, undefined, {}, [], -1, Infinity, 1.1, 'a'.repeat(115), 'with space', 'x\r\nPRIVATE', '/path', '_leading']) {
  test(`request ID invalido rejeitado com erro fixo: ${JSON.stringify(id)}`, () => {
    assert.throws(() => prepareReminderTick(id), /^Error: reminder_tick_invalid_request_id$/);
  });
}

test('completed retorna somente correlacao, estados e contadores sem copiar mensagem ou headers', () => {
  const input = api();
  input.headers = {authorization: 'PRIVATE'};
  input.body.message = 'PRIVATE';
  const result = normalizeReminderTick(input, requestId);
  assert.deepEqual(result, {request_id: requestId, status: 'succeeded', tick_status: 'completed',
    skip_reason: null, local_mutation_state: 'applied', synchronization: sync(), generation: generation(),
    messages_sent: 0, external_mutations: 0});
  assert.equal(JSON.stringify(result).includes('PRIVATE'), false);
  result.synchronization.state_counts.pending = 99;
  assert.equal(input.body.data.synchronization.state_counts.pending, 2);
});
test('completed vazio indica fases concluidas, sem afirmar novas linhas ou mensagens', () => {
  const input = skipped();
  input.body.status = 'succeeded';
  Object.assign(input.body.data, {tick_status: 'completed', skip_reason: null, local_mutation_state: 'applied'});
  input.body.data.synchronization.status = 'synchronized';
  input.body.data.generation.status = 'planned';
  assert.equal(normalizeReminderTick(input, requestId).tick_status, 'completed');
  assert.equal(normalizeReminderTick(input, requestId).generation.reminders_considered, 0);
});
for (const reason of ['not_configured', 'disabled', 'permission_required', 'busy']) {
  test(`skip ${reason} e valido e nao fabrica completed`, () => {
    const output = normalizeReminderTick(skipped(reason), requestId);
    assert.equal(output.status, 'skipped');
    assert.equal(output.tick_status, 'skipped');
    assert.equal(output.skip_reason, reason);
    assert.equal(output.messages_sent, 0);
  });
}
for (const partial of [false, true]) test(`HTTP503 ${partial ? 'partial' : 'failed'} falha execucao com erro fixo`, () => {
  const input = failed(partial);
  input.body.message = 'PRIVATE event title or provider details';
  assert.throws(() => normalizeReminderTick(input, requestId),
    partial ? /^Error: reminder_tick_partial$/ : /^Error: reminder_tick_failed$/);
});
test('falha no encerramento preserva generation planned sem transformar HTTP503 em sucesso', () => {
  const input = failed(true);
  input.body.data.generation = generation();
  assert.throws(() => normalizeReminderTick(input, requestId), /^Error: reminder_tick_partial$/);
  assert.deepEqual(input.body.data.generation, generation());
});
test('falha na sincronizacao nao pode virar sucesso', () => {
  const input = failed();
  input.body.data.synchronization.status = 'failed';
  assert.throws(() => normalizeReminderTick(input, requestId), /^Error: reminder_tick_failed$/);
});
for (const statusCode of [undefined, null, '200', 201, 202, 301, 401, 422, 500]) {
  test(`HTTP inesperado ${String(statusCode)} nao confirma tick`, () => rejects({...api(), statusCode}));
}
for (const input of [undefined, null, [], {}, {error: {message: 'PRIVATE'}}, {statusCode: 200}, {statusCode: 503, body: 'PRIVATE'}]) {
  test(`transporte/corpo invalido ${JSON.stringify(input)} tem erro seguro`, () => rejects(input));
}
for (const level of ['envelope', 'data', 'sync', 'generation', 'error']) {
  test(`campo privado extra em ${level} e rejeitado`, () => {
    const input = level === 'error' ? failed() : api();
    const target = level === 'envelope' ? input.body : level === 'data' ? input.body.data :
      level === 'sync' ? input.body.data.synchronization : level === 'generation' ? input.body.data.generation : input.body.error;
    target.private_event = 'PRIVATE';
    rejects(input);
  });
}
for (const key of Object.keys(api().body)) test(`campo obrigatorio ausente no envelope: ${key}`, () => {
  const input = api(); delete input.body[key]; rejects(input);
});
for (const key of Object.keys(api().body.data)) test(`campo obrigatorio ausente no data: ${key}`, () => {
  const input = api(); delete input.body.data[key]; rejects(input);
});
for (const [key, value] of [['api_version', '2'], ['request_id', 'reminder-tick-other'], ['operation_id', 'PRIVATE'],
  ['status', 'failed'], ['action', 'send_message'], ['mutation_state', 'confirmed'], ['message', ''], ['error', {}]]) {
  test(`envelope incompativel: ${key}`, () => { const input = api(); input.body[key] = value; rejects(input); });
}
for (const phase of ['synchronization', 'generation']) {
  const prototype = api().body.data[phase];
  for (const key of Object.keys(prototype).filter(key => !['status', 'state_counts'].includes(key))) {
    for (const invalid of [-1, 1.5, '1', true, null, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      test(`${phase}.${key} rejeita contagem ${String(invalid)}`, () => {
        const input = api(); input.body.data[phase][key] = invalid; rejects(input);
      });
    }
  }
  for (const value of [null, [], {private_event: 1}, {pending: -1}, {pending: '1'}, {pending: true}, {pending: 1.5}]) {
    test(`${phase}.state_counts restrito: ${JSON.stringify(value)}`, () => {
      const input = api(); input.body.data[phase].state_counts = value; rejects(input);
    });
  }
  test(`${phase}.state_counts aceita todos e somente os oito estados do ledger`, () => {
    const input = api();
    input.body.data[phase].state_counts = Object.fromEntries(['pending', 'blocked', 'reserved', 'sending', 'accepted',
      'uncertain', 'cancelled', 'expired'].map((key, index) => [key, index]));
    assert.deepEqual(normalizeReminderTick(input, requestId)[phase].state_counts, input.body.data[phase].state_counts);
  });
}
for (const mutate of [
  data => data.messages_sent = 1, data => data.external_mutations = 1,
  data => data.tick_status = 'partial', data => data.skip_reason = 'busy',
  data => data.local_mutation_state = 'not_applied', data => data.synchronization.status = 'not_run',
  data => data.generation.status = 'failed',
]) test(`sucesso contraditorio rejeitado ${mutate.toString()}`, () => {
  const input = api(); mutate(input.body.data); rejects(input);
});
for (const mutate of [
  data => data.skip_reason = null, data => data.skip_reason = 'unknown',
  data => data.local_mutation_state = 'applied', data => data.synchronization.events_checked = 1,
  data => data.generation.state_counts = {pending: 0}, data => data.generation.status = 'planned',
]) test(`skip contraditorio rejeitado ${mutate.toString()}`, () => {
  const input = skipped(); mutate(input.body.data); rejects(input);
});
for (const mutate of [
  input => input.body.error.retryable = true, input => input.body.error.fields = ['PRIVATE'],
  input => input.body.error.code = 'provider_detail', input => input.body.status = 'succeeded',
  input => input.body.data.local_mutation_state = 'applied', input => input.body.data.skip_reason = 'busy',
  input => input.body.data.generation.status = 'planned',
]) test(`falha contraditoria rejeitada ${mutate.toString()}`, () => {
  const input = failed(); mutate(input); assert.throws(() => normalizeReminderTick(input, requestId), /^Error: reminder_tick_invalid_response$/);
});

test('grafo publico tem uma fonte ScheduleTrigger, sem staticData, webhook, dados fixados ou IDs privados', () => {
  assert.deepEqual(Object.keys(workflow).sort(), ['name', 'active', 'nodes', 'connections', 'settings', 'pinData', 'tags'].sort());
  assert.equal(workflow.name, 'Genius Agenda AI - Verificação periódica de lembretes');
  assert.equal(workflow.active, false);
  assert.deepEqual(workflow.pinData, {});
  assert.deepEqual(workflow.tags, []);
  assert.deepEqual(workflow.nodes.map(node => node.type), ['n8n-nodes-base.scheduleTrigger', 'n8n-nodes-base.code',
    'n8n-nodes-base.httpRequest', 'n8n-nodes-base.code', 'n8n-nodes-base.httpRequest', 'n8n-nodes-base.code',
    'n8n-nodes-base.httpRequest', 'n8n-nodes-base.code']);
  assert.deepEqual(workflow.nodes[0].parameters, {rule: {interval: [{field: 'minutes', minutesInterval: 1}]}});
  assert.equal(JSON.stringify(workflow).includes('staticData'), false);
  assert.equal(JSON.stringify(workflow).includes('console.'), false);
  assert.equal(JSON.stringify(workflow).includes('$getWorkflowStaticData'), false);
  const expectedEdge = node => ({node, type: 'main', index: 0});
  assert.deepEqual(workflow.connections, {
    'Verificar a cada minuto': {main: [[expectedEdge('Preparar tick')]]},
    'Preparar tick': {main: [[expectedEdge('Solicitar recuperacao local')]]},
    'Solicitar verificacao protegida': {main: [[expectedEdge('Validar resumo seguro')], [expectedEdge('Validar resumo seguro')]]},
    'Validar resumo seguro': {main: [[expectedEdge('Solicitar disparo protegido')]]},
    'Solicitar disparo protegido': {main: [[expectedEdge('Validar resumo do disparo')], [expectedEdge('Validar resumo do disparo')]]},
    'Solicitar recuperacao local': {main: [[expectedEdge('Validar resumo da recuperacao')], [expectedEdge('Validar resumo da recuperacao')]]},
    'Validar resumo da recuperacao': {main: [[expectedEdge('Solicitar verificacao protegida')]]},
  });
});
test('HTTP possui somente destino local fixo, corpo vazio e credencial nomeada; sem retry ou redirect', () => {
  const http = workflow.nodes[2];
  assert.deepEqual(http.credentials, {httpBearerAuth: {name: 'Genius API - local'}});
  assert.equal(http.parameters.method, 'POST');
  assert.equal(http.parameters.url, 'http://host.docker.internal:8000/api/v1/reminders/tick');
  assert.equal(http.parameters.authentication, 'genericCredentialType');
  assert.equal(http.parameters.genericAuthType, 'httpBearerAuth');
  assert.equal(http.parameters.sendBody, true);
  assert.equal(http.parameters.contentType, 'json');
  assert.equal(http.parameters.specifyBody, 'json');
  assert.equal(http.parameters.jsonBody, '{}');
  assert.deepEqual(http.parameters.headerParameters, {parameters: [
    {name: 'Content-Type', value: 'application/json'}, {name: 'X-Request-ID', value: '={{ $json.request_id }}'}]});
  assert.deepEqual(http.parameters.options, {redirect: {redirect: {followRedirects: false}}, timeout: 120000,
    response: {response: {fullResponse: true, neverError: true, responseFormat: 'json'}}});
  assert.equal(http.retryOnFail, false);
  assert.equal(http.onError, 'continueErrorOutput');
});
test('configuracoes desativam salvamento de execucoes e limitam duracao', () => {
  assert.deepEqual(workflow.settings, {executionOrder: 'v1', timezone: 'America/Fortaleza',
    saveDataSuccessExecution: 'none', saveDataErrorExecution: 'none', saveManualExecutions: false,
    saveExecutionProgress: false, executionTimeout: 420, callerPolicy: 'none'});
});

const dispatchCounters = ['reminders_considered', 'expired_count', 'cancelled_count', 'blocked_count',
  'accepted_count', 'uncertain_count', 'failed_count', 'transport_attempts'];
const dispatchApi = () => ({statusCode: 200, body: {api_version: '1', request_id: requestId,
  operation_id: null, status: 'succeeded', action: 'reminder_dispatch', mutation_state: 'confirmed',
  message: 'Processamento concluido; aceite nao confirma entrega.', data: {dispatch_status: 'completed', skip_reason: null,
    reminders_considered: 3, expired_count: 1, cancelled_count: 0, blocked_count: 1,
    accepted_count: 1, uncertain_count: 0, failed_count: 0, transport_attempts: 1}, error: null}});
function dispatchSkipped(reason = 'disabled') {
  const input = dispatchApi();
  input.body.status = 'skipped';
  input.body.mutation_state = 'not_applied';
  Object.assign(input.body.data, {dispatch_status: 'skipped', skip_reason: reason});
  for (const key of dispatchCounters) input.body.data[key] = 0;
  return input;
}
function dispatchFailed(partial = false) {
  const input = dispatchSkipped();
  input.statusCode = 503;
  input.body.status = 'failed';
  Object.assign(input.body.data, {dispatch_status: partial ? 'partial' : 'failed', skip_reason: null,
    reminders_considered: 1, failed_count: partial ? 0 : 1,
    uncertain_count: partial ? 1 : 0, transport_attempts: partial ? 1 : 0});
  input.body.mutation_state = partial ? 'unknown' : 'not_applied';
  input.body.error = {code: partial ? 'reminder_dispatch_partial' : 'reminder_dispatch_failed', retryable: false, fields: []};
  return input;
}
const rejectDispatch = input => assert.throws(() => normalizeReminderDispatch(input, requestId),
  /^Error: reminder_dispatch_(invalid_response|failed|partial)$/);

test('disparo completed projeta somente estados fixos e contadores sem mensagem ou headers', () => {
  const input = dispatchApi();
  input.headers = {authorization: 'PRIVATE'};
  input.body.message = 'PRIVATE event phone and provider details';
  const output = normalizeReminderDispatch(input, requestId);
  assert.deepEqual(output, {request_id: requestId, status: 'succeeded', mutation_state: 'confirmed', ...input.body.data});
  assert.equal(JSON.stringify(output).includes('PRIVATE'), false);
  output.accepted_count = 999;
  assert.equal(input.body.data.accepted_count, 1);
});
test('disparo completed sem transporte nao afirma aceite ou entrega', () => {
  const input = dispatchSkipped();
  input.body.status = 'succeeded';
  Object.assign(input.body.data, {dispatch_status: 'completed', skip_reason: null});
  const output = normalizeReminderDispatch(input, requestId);
  assert.equal(output.mutation_state, 'not_applied');
  assert.equal(output.accepted_count, 0);
  assert.equal(output.transport_attempts, 0);
});
for (const reason of ['not_configured', 'disabled', 'permission_required', 'busy']) {
  test(`disparo skipped ${reason} sem fabricar envio`, () => {
    const output = normalizeReminderDispatch(dispatchSkipped(reason), requestId);
    assert.equal(output.status, 'skipped');
    assert.equal(output.skip_reason, reason);
    assert.equal(output.transport_attempts, 0);
    assert.equal(output.mutation_state, 'not_applied');
  });
}
for (const partial of [false, true]) test(`disparo HTTP503 ${partial ? 'partial' : 'failed'} gera erro fixo sem payload privado`, () => {
  const input = dispatchFailed(partial);
  input.body.message = 'PRIVATE';
  assert.throws(() => normalizeReminderDispatch(input, requestId),
    partial ? /^Error: reminder_dispatch_partial$/ : /^Error: reminder_dispatch_failed$/);
});
test('disparo parcialmente aceito antes de falha local preserva confirmed sem repetir', () => {
  const input = dispatchFailed(true);
  Object.assign(input.body.data, {accepted_count: 1, uncertain_count: 0, failed_count: 1});
  input.body.mutation_state = 'confirmed';
  assert.throws(() => normalizeReminderDispatch(input, requestId), /^Error: reminder_dispatch_partial$/);
});
for (const state of ['unknown', 'not_applied']) test(`falha inesperada sem contadores pode ser ${state} mas sempre falha n8n`, () => {
  const input = dispatchFailed();
  input.body.data = null;
  input.body.mutation_state = state;
  assert.throws(() => normalizeReminderDispatch(input, requestId), /^Error: reminder_dispatch_failed$/);
});
for (const input of [undefined, null, [], {}, {error: {message: 'PRIVATE'}}, {statusCode: 200},
  {statusCode: 503, body: 'PRIVATE'}]) {
  test(`disparo transporte/corpo invalido ${JSON.stringify(input)} tem erro seguro`, () => rejectDispatch(input));
}
for (const statusCode of [undefined, null, '200', 201, 202, 301, 401, 422, 500]) {
  test(`disparo HTTP inesperado ${String(statusCode)} nao confirma aceite`, () => rejectDispatch({...dispatchApi(), statusCode}));
}
for (const level of ['envelope', 'data', 'error']) test(`disparo rejeita campo privado extra em ${level}`, () => {
  const input = level === 'error' ? dispatchFailed() : dispatchApi();
  const target = level === 'envelope' ? input.body : level === 'data' ? input.body.data : input.body.error;
  target.private_event = 'PRIVATE';
  rejectDispatch(input);
});
for (const key of Object.keys(dispatchApi().body)) test(`disparo campo obrigatorio ausente no envelope ${key}`, () => {
  const input = dispatchApi(); delete input.body[key]; rejectDispatch(input);
});
for (const key of Object.keys(dispatchApi().body.data)) test(`disparo campo obrigatorio ausente no data ${key}`, () => {
  const input = dispatchApi(); delete input.body.data[key]; rejectDispatch(input);
});
for (const key of dispatchCounters) for (const value of [-1, 1.5, '1', true, null, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
  test(`disparo ${key} rejeita contagem ${String(value)}`, () => {
    const input = dispatchApi(); input.body.data[key] = value; rejectDispatch(input);
  });
}
for (const [key, value] of [['api_version', '2'], ['request_id', 'reminder-tick-other'], ['operation_id', 'PRIVATE'],
  ['status', 'failed'], ['action', 'send_arbitrary'], ['mutation_state', 'not_applied'], ['message', ''], ['error', {}]]) {
  test(`disparo envelope incompativel ${key}`, () => { const input = dispatchApi(); input.body[key] = value; rejectDispatch(input); });
}
for (const mutate of [data => data.transport_attempts = 2, data => data.accepted_count = 2,
  data => data.uncertain_count = 1, data => data.failed_count = 1, data => data.skip_reason = 'busy',
  data => data.dispatch_status = 'partial']) test(`disparo sucesso contraditorio ${mutate.toString()}`, () => {
  const input = dispatchApi(); mutate(input.body.data); rejectDispatch(input);
});
for (const mutate of [data => data.skip_reason = null, data => data.skip_reason = 'unknown',
  data => data.reminders_considered = 1, data => data.failed_count = 1, data => data.dispatch_status = 'completed']) {
  test(`disparo skip contraditorio ${mutate.toString()}`, () => {
    const input = dispatchSkipped(); mutate(input.body.data); rejectDispatch(input);
  });
}
for (const mutate of [input => input.body.error.retryable = true, input => input.body.error.fields = ['PRIVATE'],
  input => input.body.error.code = 'provider_detail', input => input.body.status = 'succeeded',
  input => input.body.data.failed_count = 0, input => input.body.data.skip_reason = 'busy',
  input => input.body.mutation_state = 'confirmed']) test(`disparo falha contraditoria ${mutate.toString()}`, () => {
  const input = dispatchFailed(); mutate(input);
  assert.throws(() => normalizeReminderDispatch(input, requestId), /^Error: reminder_dispatch_invalid_response$/);
});
test('HTTP de disparo herda credencial e controles do tick sem aceitar parametros ou retry', () => {
  const tick = workflow.nodes[2], dispatch = workflow.nodes[4];
  assert.deepEqual(dispatch.credentials, tick.credentials);
  assert.deepEqual(dispatch.parameters, {...tick.parameters,
    url: 'http://host.docker.internal:8000/api/v1/reminders/dispatch'});
  assert.equal(dispatch.retryOnFail, false);
  assert.equal(dispatch.onError, 'continueErrorOutput');
  assert.equal(dispatch.parameters.jsonBody, '{}');
  assert.equal(dispatch.parameters.options.timeout, 120000);
  assert.equal(dispatch.parameters.options.redirect.redirect.followRedirects, false);
});
test('normalizador de disparo embutido e identico ao helper testado e tem erro seguro', () => {
  const code = workflow.nodes[5].parameters.jsCode;
  assert.equal(code, normalizeReminderDispatch.toString() +
    "\nreturn [{json: normalizeReminderDispatch($input.first().json, $('Preparar tick').first().json.request_id)}];\n");
  const getNode = name => { assert.equal(name, 'Preparar tick'); return {first: () => ({json: {request_id: requestId}})}; };
  assert.deepEqual(new Function('$input', '$', code)({first: () => ({json: dispatchApi()})}, getNode),
    [{json: normalizeReminderDispatch(dispatchApi(), requestId)}]);
  assert.throws(() => new Function('$input', '$', code)({first: () => ({json: {error: 'PRIVATE'}})}, getNode),
    /^Error: reminder_dispatch_invalid_response$/);
});
test('tick com falha interrompe o fluxo antes de qualquer disparo', () => {
  let dispatchCalls = 0;
  const run = tick => {
    normalizeReminderTick(tick, requestId);
    dispatchCalls += 1;
    return normalizeReminderDispatch(dispatchApi(), requestId);
  };
  assert.throws(() => run(failed()), /^Error: reminder_tick_failed$/);
  assert.throws(() => run(failed(true)), /^Error: reminder_tick_partial$/);
  assert.throws(() => run({error: 'PRIVATE'}), /^Error: reminder_tick_invalid_response$/);
  assert.equal(dispatchCalls, 0);
  assert.equal(run(api()).accepted_count, 1);
  assert.equal(dispatchCalls, 1);
});
const recoveryCounters = ['reservations_recovered', 'expired_count', 'uncertain_count', 'protected_count'];
const recoveryApi = () => ({statusCode: 200, body: {api_version: '1', request_id: requestId,
  operation_id: null, status: 'succeeded', action: 'reminder_recovery', mutation_state: 'not_applicable',
  message: 'Recuperacao local concluida sem transporte.', data: {recovery_status: 'completed', skip_reason: null,
    reservations_recovered: 2, expired_count: 1, uncertain_count: 1, protected_count: 3,
    messages_sent: 0, external_mutations: 0}, error: null}});
function recoverySkipped(reason = 'disabled') {
  const input = recoveryApi();
  input.body.status = 'skipped';
  Object.assign(input.body.data, {recovery_status: 'skipped', skip_reason: reason});
  for (const key of recoveryCounters) input.body.data[key] = 0;
  return input;
}
function recoveryFailed(known = false) {
  const input = known ? recoveryApi() : recoverySkipped();
  input.statusCode = 503;
  input.body.status = 'failed';
  Object.assign(input.body.data, {recovery_status: 'failed', skip_reason: null});
  input.body.error = {code: 'reminder_recovery_failed', retryable: false, fields: []};
  return input;
}
const rejectRecovery = input => assert.throws(() => normalizeReminderRecovery(input, requestId),
  /^Error: reminder_recovery_(invalid_response|failed)$/);

test('recuperacao projeta somente correlacao e contadores sem IDs, texto ou headers', () => {
  const input = recoveryApi();
  input.headers = {authorization: 'PRIVATE'};
  input.body.message = 'PRIVATE calendar title phone or provider details';
  const output = normalizeReminderRecovery(input, requestId);
  assert.deepEqual(output, {request_id: requestId, status: 'succeeded', ...input.body.data});
  assert.equal(JSON.stringify(output).includes('PRIVATE'), false);
  output.reservations_recovered = 999;
  assert.equal(input.body.data.reservations_recovered, 2);
});
test('recuperacao completed com zeros confirma conclusao sem inventar trabalho', () => {
  const input = recoverySkipped();
  input.body.status = 'succeeded';
  Object.assign(input.body.data, {recovery_status: 'completed', skip_reason: null});
  const output = normalizeReminderRecovery(input, requestId);
  assert.equal(output.recovery_status, 'completed');
  assert.equal(output.reservations_recovered, 0);
  assert.equal(output.messages_sent, 0);
});
for (const reason of ['not_configured', 'disabled', 'permission_required', 'busy']) {
  test(`recuperacao skipped ${reason} nao afirma reserva liberada`, () => {
    const output = normalizeReminderRecovery(recoverySkipped(reason), requestId);
    assert.equal(output.status, 'skipped');
    assert.equal(output.skip_reason, reason);
    assert.equal(output.reservations_recovered, 0);
  });
}
for (const known of [false, true]) test(`recuperacao failed com contadores ${known ? 'confirmados' : 'zerados'} interrompe fluxo`, () => {
  const input = recoveryFailed(known);
  input.body.message = 'PRIVATE';
  assert.throws(() => normalizeReminderRecovery(input, requestId), /^Error: reminder_recovery_failed$/);
});
test('commit desconhecido data=null falha sem fabricar contadores', () => {
  const input = recoveryFailed();
  input.body.data = null;
  assert.throws(() => normalizeReminderRecovery(input, requestId), /^Error: reminder_recovery_failed$/);
});
for (const statusCode of [undefined, null, '200', 201, 202, 301, 401, 422, 500]) {
  test(`recuperacao HTTP inesperado ${String(statusCode)} nao confirma lote`, () => rejectRecovery({...recoveryApi(), statusCode}));
}
for (const input of [undefined, null, [], {}, {error: {message: 'PRIVATE'}}, {statusCode: 200},
  {statusCode: 503, body: 'PRIVATE'}]) {
  test(`recuperacao transporte/corpo invalido ${JSON.stringify(input)} tem erro seguro`, () => rejectRecovery(input));
}
for (const level of ['envelope', 'data', 'error']) test(`recuperacao rejeita campo privado extra em ${level}`, () => {
  const input = level === 'error' ? recoveryFailed() : recoveryApi();
  const target = level === 'envelope' ? input.body : level === 'data' ? input.body.data : input.body.error;
  target.private_event = 'PRIVATE';
  rejectRecovery(input);
});
for (const key of Object.keys(recoveryApi().body)) test(`recuperacao campo obrigatorio ausente no envelope ${key}`, () => {
  const input = recoveryApi(); delete input.body[key]; rejectRecovery(input);
});
for (const key of Object.keys(recoveryApi().body.data)) test(`recuperacao campo obrigatorio ausente no data ${key}`, () => {
  const input = recoveryApi(); delete input.body.data[key]; rejectRecovery(input);
});
for (const key of recoveryCounters) for (const value of [-1, 1.5, '1', true, null, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
  test(`recuperacao ${key} rejeita contagem ${String(value)}`, () => {
    const input = recoveryApi(); input.body.data[key] = value; rejectRecovery(input);
  });
}
for (const key of ['messages_sent', 'external_mutations']) for (const value of [1, -1, '0', false, null]) {
  test(`recuperacao ${key} exige numero zero e nao ${String(value)}`, () => {
    const input = recoveryApi(); input.body.data[key] = value; rejectRecovery(input);
  });
}
for (const [key, value] of [['api_version', '2'], ['request_id', 'reminder-tick-other'], ['operation_id', 'PRIVATE'],
  ['status', 'failed'], ['action', 'recover_arbitrary'], ['mutation_state', 'confirmed'], ['message', ''], ['error', {}]]) {
  test(`recuperacao envelope incompativel ${key}`, () => {
    const input = recoveryApi(); input.body[key] = value; rejectRecovery(input);
  });
}
for (const mutate of [data => data.recovery_status = 'failed', data => data.skip_reason = 'busy',
  data => data.messages_sent = 1, data => data.external_mutations = 1]) {
  test(`recuperacao sucesso contraditorio ${mutate.toString()}`, () => {
    const input = recoveryApi(); mutate(input.body.data); rejectRecovery(input);
  });
}
for (const mutate of [data => data.skip_reason = null, data => data.skip_reason = 'unknown',
  data => data.reservations_recovered = 1, data => data.protected_count = 1,
  data => data.recovery_status = 'completed']) {
  test(`recuperacao skip contraditorio ${mutate.toString()}`, () => {
    const input = recoverySkipped(); mutate(input.body.data); rejectRecovery(input);
  });
}
for (const mutate of [input => input.body.error.retryable = true, input => input.body.error.fields = ['PRIVATE'],
  input => input.body.error.code = 'provider_detail', input => input.body.status = 'succeeded',
  input => input.body.data.recovery_status = 'completed', input => input.body.data.skip_reason = 'busy']) {
  test(`recuperacao falha contraditoria ${mutate.toString()}`, () => {
    const input = recoveryFailed(); mutate(input);
    assert.throws(() => normalizeReminderRecovery(input, requestId), /^Error: reminder_recovery_invalid_response$/);
  });
}
test('HTTP de recuperacao usa mesma credencial, corpo vazio e sem retry ou redirect', () => {
  const tick = workflow.nodes.find(node => node.name === 'Solicitar verificacao protegida');
  const recovery = workflow.nodes.find(node => node.name === 'Solicitar recuperacao local');
  assert.deepEqual(recovery.credentials, tick.credentials);
  assert.deepEqual(recovery.parameters, {...tick.parameters,
    url: 'http://host.docker.internal:8000/api/v1/reminders/recover'});
  assert.equal(recovery.retryOnFail, false);
  assert.equal(recovery.onError, 'continueErrorOutput');
  assert.equal(recovery.parameters.jsonBody, '{}');
  assert.equal(recovery.parameters.options.timeout, 120000);
  assert.equal(recovery.parameters.options.redirect.redirect.followRedirects, false);
});
test('normalizador de recuperacao embutido e identico ao helper testado', () => {
  const code = workflow.nodes.find(node => node.name === 'Validar resumo da recuperacao').parameters.jsCode;
  assert.equal(code, normalizeReminderRecovery.toString() +
    "\nreturn [{json: normalizeReminderRecovery($input.first().json, $('Preparar tick').first().json.request_id)}];\n");
  const getNode = name => { assert.equal(name, 'Preparar tick'); return {first: () => ({json: {request_id: requestId}})}; };
  assert.deepEqual(new Function('$input', '$', code)({first: () => ({json: recoveryApi()})}, getNode),
    [{json: normalizeReminderRecovery(recoveryApi(), requestId)}]);
  assert.throws(() => new Function('$input', '$', code)({first: () => ({json: {error: 'PRIVATE'}})}, getNode),
    /^Error: reminder_recovery_invalid_response$/);
});
test('recuperacao com falha bloqueia tick e disparo mesmo se preserva resultado confirmado', () => {
  let tickCalls = 0, dispatchCalls = 0;
  const run = recovery => {
    normalizeReminderRecovery(recovery, requestId);
    tickCalls += 1;
    normalizeReminderTick(api(), requestId);
    dispatchCalls += 1;
    return normalizeReminderDispatch(dispatchApi(), requestId);
  };
  for (const input of [recoveryFailed(), recoveryFailed(true), {error: 'PRIVATE'},
    {...recoveryFailed(), body: {...recoveryFailed().body, data: null}}]) {
    assert.throws(() => run(input), /^Error: reminder_recovery_(invalid_response|failed)$/);
  }
  assert.equal(tickCalls, 0);
  assert.equal(dispatchCalls, 0);
  assert.equal(run(recoveryApi()).accepted_count, 1);
  assert.equal(tickCalls, 1);
  assert.equal(dispatchCalls, 1);
});

test('codigo embutido e identico a biblioteca testada e executa sem importacoes ou estado', () => {
  const prepare = workflow.nodes[1].parameters.jsCode;
  const normalize = workflow.nodes[3].parameters.jsCode;
  assert.equal(prepare, prepareReminderTick.toString() + '\nreturn [{json: prepareReminderTick($execution.id)}];\n');
  assert.equal(normalize, normalizeReminderTick.toString() +
    "\nreturn [{json: normalizeReminderTick($input.first().json, $('Preparar tick').first().json.request_id)}];\n");
  assert.deepEqual(new Function('$execution', prepare)({id: '42'}), [{json: {request_id: requestId}}]);
  const getNode = name => { assert.equal(name, 'Preparar tick'); return {first: () => ({json: {request_id: requestId}})}; };
  assert.deepEqual(new Function('$input', '$', normalize)({first: () => ({json: api()})}, getNode),
    [{json: normalizeReminderTick(api(), requestId)}]);
  assert.throws(() => new Function('$input', '$', normalize)({first: () => ({json: {error: 'PRIVATE'}})}, getNode),
    /^Error: reminder_tick_invalid_response$/);
});
