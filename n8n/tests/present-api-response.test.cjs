'use strict';

// Casos ficticios e locais: nenhuma chamada a Docker, Google ou API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {presentApiResponse, presentTransportFailure} = require('../lib/present-api-response.js');
const demonstration = require('./fixtures/response-cases.json');

const cases = [];
const test = (name, run) => cases.push({name, run});
const copy = value => structuredClone(value);
const context = (action = 'list', phase = 'read') => ({action, phase, request_id: 'demo-request-001'});
const event = (overrides = {}) => ({
  event_id: 'evento-ficticio-1', title: 'Entrevista ficticia',
  start: '2030-06-17T10:00:00-03:00', end: '2030-06-17T10:30:00-03:00',
  all_day: false, time_zone: 'America/Recife', status: 'confirmed',
  html_link: 'https://example.invalid/evento', ...overrides,
});
const envelope = (overrides = {}) => ({
  api_version: '1', request_id: 'demo-request-001', operation_id: null,
  status: 'succeeded', action: 'list', mutation_state: 'not_applicable',
  message: 'Consulta concluida.',
  data: {calendar_name: 'Agenda ficticia', period: {
    start: '2030-06-17T00:00:00-03:00', end: '2030-06-18T00:00:00-03:00', time_zone: 'America/Recife',
  }, events: [event()]}, error: null, ...overrides,
});
const preparation = (overrides = {}) => ({reason: null, question: null,
  missing_fields: [], invalid_fields: [], event: null, candidates: [], parameters: null, ...overrides});
const creation = (overrides = {}) => envelope({action: 'create', operation_id: 'demo-intencao-1',
  mutation_state: 'confirmed', data: {event_id: 'evento-ficticio-1',
    html_link: null, calendar_name: 'Agenda ficticia', reused: false, recovered: false, ...overrides}});
const update = (action, result, recovered = false) => envelope({action, mutation_state: 'confirmed', data: {
  calendar_name: 'Agenda ficticia', before: event(), after: event(), result, recovered,
}});
const deletionPreview = () => envelope({status: 'needs_confirmation', action: 'delete', mutation_state: 'not_applied',
  data: {calendar_name: 'Agenda ficticia', event: event(), confirmation_token: 'a'.repeat(64),
    question: 'Confirma excluir o evento ficticio?', result: 'needs_confirmation'}});
const conflict = action => envelope({status: 'conflict', action, mutation_state: 'not_applied',
  operation_id: action === 'create' ? 'demo-intencao-1' : null,
  data: {status: 'conflict', calendar_name: 'Agenda ficticia', start: event().start,
    end: event().end, time_zone: 'America/Recife', conflicts: [event()]},
  error: {code: 'schedule_conflict', retryable: false, fields: []}});
const failed = (ctx, code, state, overrides = {}) => envelope({status: 'failed', action: ctx.action,
  mutation_state: state, data: null, error: {code, retryable: false, fields: []}, ...overrides});

function safe(result) {
  assert.equal(typeof result.http_status, 'number');
  assert.equal(typeof result.presentation.text, 'string');
  assert.ok(result.presentation.text.length > 0);
  assert.equal(result.presentation.auto_retry, false);
  assert.equal(result.presentation.execution_allowed, false);
  assert.equal(typeof result.presentation.requires_user_input, 'boolean');
  assert.equal(Object.hasOwn(result, 'parameters'), false);
  return result;
}
function expect(input, ctx, kind, nextStep, asks = false) {
  const before = copy({input, ctx});
  const result = safe(presentApiResponse(input, ctx));
  assert.deepEqual({input, ctx}, before, 'A apresentacao nao pode modificar a resposta nem o contexto.');
  assert.equal(result.presentation.kind, kind);
  assert.equal(result.presentation.next_step, nextStep);
  assert.equal(result.presentation.requires_user_input, asks);
  assert.equal(result.http_status, input.statusCode);
  assert.deepEqual(result.response, input.body, 'O envelope tecnico precisa ser preservado.');
  return result;
}
function invalid(input, ctx) {
  if (arguments.length < 2) ctx = context();
  const result = safe(presentApiResponse(input, ctx));
  assert.equal(result.http_status, 502);
  assert.equal(result.response.status, 'failed');
  assert.equal(result.response.error.code, 'invalid_api_response');
  assert.equal(result.response.data, null);
  assert.notEqual(result.presentation.kind, 'success');
  assert.notEqual(result.presentation.kind, 'empty');
  return result;
}

test('consulta: um resultado e plural correto', () => {
  const result = expect({statusCode: 200, body: envelope()}, context(), 'success', 'done');
  assert.match(result.presentation.text, /1 compromisso neste/);
});
test('consulta: dois resultados', () => {
  const body = envelope(); body.data.events.push(event({event_id: 'evento-ficticio-2'}));
  const result = expect({statusCode: 200, body}, context(), 'success', 'done');
  assert.match(result.presentation.text, /2 compromissos/);
});
test('consulta vazia e um sucesso verdadeiro', () => {
  const body = envelope(); body.data.events = [];
  const result = expect({statusCode: 200, body}, context(), 'empty', 'done');
  assert.match(result.presentation.text, /Não há compromissos/);
});
test('AgendaEvent permite html_link nulo', () => {
  const body = envelope(); body.data.events[0].html_link = null;
  expect({statusCode: 200, body}, context(), 'success', 'done');
});
test('eventos de dia inteiro conservam as datas e fim exclusivo', () => {
  const body = envelope(); body.data.events = [event({start: '2030-06-17', end: '2030-06-18', all_day: true})];
  expect({statusCode: 200, body}, context(), 'success', 'done');
});
test('consulta por ID', () => {
  const body = envelope({action: 'get', data: {calendar_name: 'Agenda ficticia', event: event()}});
  expect({statusCode: 200, body}, context('get'), 'success', 'done');
});
test('evento ausente nao vira sucesso ou lista vazia', () => {
  const body = failed(context('get'), 'event_not_found', 'not_applicable', {status: 'not_found'});
  expect({statusCode: 404, body}, context('get'), 'not_found', 'identify_event', true);
});

for (const [name, statusCode, changes] of [
  ['nova', 201, {}], ['reutilizada', 200, {reused: true}],
  ['recuperada apos resposta perdida', 201, {recovered: true}],
]) test(`criacao ${name}`, () => {
  expect({statusCode, body: creation(changes)}, context('create', 'execute'), 'success', 'done');
});
test('criacao reutilizada informa que nao criou outra entrevista', () => {
  const result = presentApiResponse({statusCode: 200, body: creation({reused: true})}, context('create', 'execute'));
  assert.match(result.presentation.text, /reutilizado/);
  assert.match(result.presentation.text, /não foi criada outra/);
});
for (const [action, changed] of [['edit', 'updated'], ['reschedule', 'rescheduled']]) {
  for (const [result, recovered] of [[changed, false], [changed, true], ['unchanged', false]]) {
    test(`${action}: ${result}, recovered=${recovered}`, () => {
      const output = expect({statusCode: 200, body: update(action, result, recovered)}, context(action, 'execute'), 'success', 'done');
      if (recovered) assert.match(output.presentation.text, /confirmada após conferir/);
      if (result === 'unchanged') assert.match(output.presentation.text, /Nenhuma nova alteração/);
    });
  }
}
for (const [result, recovered] of [['deleted', false], ['deleted', true], ['already_absent', false]]) {
  test(`exclusao: ${result}, recovered=${recovered}`, () => {
    const body = envelope({action: 'delete', mutation_state: 'confirmed', data: {
      calendar_name: 'Agenda ficticia', event_id: 'evento-ficticio-1', result, recovered}});
    expect({statusCode: 200, body}, context('delete', 'execute'), 'success', 'done');
  });
}
test('previa de evento ja ausente nao afirma exclusao executada', () => {
  const body = envelope({action: 'delete', mutation_state: 'not_applied', data: {
    calendar_name: 'Agenda ficticia', event: null, confirmation_token: null, question: null, result: 'already_absent'}});
  const result = expect({statusCode: 200, body}, context('delete', 'preview'), 'success', 'done');
  assert.match(result.presentation.text, /Nenhuma exclusão foi realizada/);
});

for (const action of ['create', 'list', 'reschedule', 'edit', 'delete']) {
  test(`preparacao ${action} pronta nao autoriza executar`, () => {
    const body = envelope({status: 'ready', action, mutation_state: 'not_applied', data: preparation({parameters: {demo: true}})});
    const result = expect({statusCode: 200, body}, context(action, 'prepare'), 'prepared', 'await_requested_action');
    assert.match(result.presentation.text, /Nenhuma alteração foi executada/);
  });
}
for (const action of [null, 'create', 'list', 'reschedule', 'edit', 'delete']) {
  for (const status of ['needs_clarification', 'needs_correction']) test(`preparacao ${String(action)}: ${status}`, () => {
    const correction = status === 'needs_correction';
    const body = envelope({status, action, mutation_state: 'not_applied', data: preparation({
      reason: correction ? 'invalid_information' : 'missing_information',
      question: 'Pergunta tecnica ficticia.', missing_fields: correction ? [] : ['start', 'end'],
      invalid_fields: correction ? ['time_zone'] : [],
    })});
    expect({statusCode: 200, body}, context(action, 'prepare'), correction ? 'correction' : 'question', 'provide_details', true);
  });
}
test('preparacao ambigua pergunta qual compromisso sem escolher o primeiro', () => {
  const body = envelope({status: 'needs_clarification', action: 'edit', mutation_state: 'not_applied', data: preparation({
    reason: 'ambiguous_event', candidates: [event(), event({event_id: 'evento-ficticio-2'})]})});
  const result = expect({statusCode: 200, body}, context('edit', 'prepare'), 'question', 'provide_details', true);
  assert.match(result.presentation.text, /mais de um compromisso/);
  assert.equal(result.response.data.event, null);
});
test('preparacao sem evento pede conferir titulo e data', () => {
  const body = envelope({status: 'needs_clarification', action: 'edit', mutation_state: 'not_applied',
    data: preparation({reason: 'event_not_found'})});
  const result = expect({statusCode: 200, body}, context('edit', 'prepare'), 'question', 'provide_details', true);
  assert.match(result.presentation.text, /conferir o título e a data/);
});
test('confirmacao nao mostra token tecnico nem executa exclusao', () => {
  const body = deletionPreview();
  const result = expect({statusCode: 200, body}, context('delete', 'preview'), 'confirmation', 'confirm_selected_event', true);
  assert.match(result.presentation.text, /Confirma a exclusão/);
  assert.equal(JSON.stringify(result.presentation).includes(body.data.confirmation_token), false);
  assert.equal(result.response.data.confirmation_token, body.data.confirmation_token);
});
for (const action of ['create', 'reschedule']) test(`conflito em ${action} pede outro horario`, () => {
  expect({statusCode: 409, body: conflict(action)}, context(action, 'execute'), 'conflict', 'choose_another_time', true);
});

const validContexts = [context(), context('get'), context('delete', 'preview'),
  ...['create', 'reschedule', 'edit', 'delete'].map(action => context(action, 'execute')),
  ...[null, 'create', 'list', 'reschedule', 'edit', 'delete'].map(action => context(action, 'prepare'))];
for (const ctx of validContexts) {
  const label = `${ctx.action}/${ctx.phase}`;
  for (const [statusCode, code] of [[401, 'unauthorized'], [503, 'api_auth_unavailable']]) {
    test(`${label}: erro de acesso ${statusCode} nao revela segredo`, () => {
      const body = failed(ctx, code, 'not_applied', {action: null});
      expect({statusCode, body}, ctx, 'error', 'check_configuration');
    });
  }
  test(`${label}: entrada invalida nao e repetida`, () => {
    const state = ctx.phase === 'read' ? 'not_applicable' : 'not_applied';
    expect({statusCode: 422, body: failed(ctx, 'invalid_request', state)}, ctx, 'correction', 'provide_details', true);
  });
  test(`${label}: configuracao Calendar indisponivel`, () => {
    const state = ['read', 'preview'].includes(ctx.phase) ? 'not_applicable' : 'not_applied';
    expect({statusCode: 503, body: failed(ctx, 'calendar_setup_unavailable', state)}, ctx, 'error', 'check_configuration');
  });
  test(`${label}: falha operacional segura`, () => {
    const execute = ctx.phase === 'execute';
    const state = execute ? 'unknown' : ['read', 'preview'].includes(ctx.phase) ? 'not_applicable' : 'not_applied';
    const result = expect({statusCode: 502, body: failed(ctx, execute ? 'operation_not_confirmed' : 'operation_failed', state)},
      ctx, execute ? 'uncertain' : 'error', execute ? 'verify_state' : 'check_connection');
    if (execute) {
      assert.match(result.presentation.text, /se a alteração aconteceu/);
      assert.doesNotMatch(result.presentation.text, /[Nn]enhuma alteração|não ocorreu|não foi alterad/);
    }
  });
  test(`${label}: perda da resposta nao agenda retentativa`, () => {
    const result = safe(presentTransportFailure(ctx));
    assert.equal(result.http_status, 502);
    assert.equal(result.response.mutation_state,
      ctx.phase === 'execute' ? 'unknown' : ['read', 'preview'].includes(ctx.phase) ? 'not_applicable' : 'not_applied');
    assert.equal(result.presentation.next_step, ctx.phase === 'execute' ? 'verify_state' : 'check_connection');
  });
}

test('preview: 503 usa not_applicable, mas 422 usa not_applied', () => {
  const ctx = context('delete', 'preview');
  expect({statusCode: 503, body: failed(ctx, 'calendar_setup_unavailable', 'not_applicable')}, ctx, 'error', 'check_configuration');
  expect({statusCode: 422, body: failed(ctx, 'invalid_request', 'not_applied')}, ctx, 'correction', 'provide_details', true);
  invalid({statusCode: 503, body: failed(ctx, 'calendar_setup_unavailable', 'not_applied')}, ctx);
  invalid({statusCode: 422, body: failed(ctx, 'invalid_request', 'not_applicable')}, ctx);
});
test('validacao de request_id pode ser erro de infraestrutura 422 na consulta', () => {
  expect({statusCode: 422, body: failed(context(), 'invalid_request', 'not_applied', {action: null})},
    context(), 'correction', 'provide_details', true);
});

for (const key of Object.keys(envelope())) test(`envelope sem ${key} e rejeitado`, () => {
  const body = envelope(); delete body[key]; invalid({statusCode: 200, body});
});
for (const [label, change] of [
  ['versao numerica', body => {body.api_version = 1;}],
  ['versao futura', body => {body.api_version = '2';}],
  ['campo extra', body => {body.debug = 'NAO_RETORNAR';}],
  ['mensagem vazia', body => {body.message = '';}],
  ['mensagem longa', body => {body.message = 'x'.repeat(2001);}],
  ['correlacao vazia', body => {body.request_id = '';}],
  ['correlacao Unicode', body => {body.request_id = 'requisição';}],
  ['correlacao CRLF', body => {body.request_id = 'id\r\nHeader: x';}],
  ['operacao em consulta', body => {body.operation_id = 'demo-1';}],
  ['acao divergente', body => {body.action = 'create';}],
  ['mutacao em consulta', body => {body.mutation_state = 'confirmed';}],
  ['sucesso incerto', body => {body.mutation_state = 'unknown';}],
  ['status desconhecido', body => {body.status = 'ok';}],
  ['data nulo', body => {body.data = null;}],
  ['data array', body => {body.data = [];}],
  ['eventos nulos', body => {body.data.events = null;}],
  ['eventos objeto', body => {body.data.events = {};}],
  ['evento incompleto', body => {body.data.events = [{}];}],
  ['evento nulo', body => {body.data.events = [null];}],
  ['titulo nao texto', body => {body.data.events[0].title = 1;}],
  ['all_day nao booleano', body => {body.data.events[0].all_day = 'false';}],
  ['periodo sem fim', body => {delete body.data.period.end;}],
]) test(`DTO invalido: ${label}`, () => {
  const body = envelope(); change(body); invalid({statusCode: 200, body});
});
for (const value of [null, [], 'erro', 42, true]) test(`corpo invalido ${JSON.stringify(value)}`, () => {
  invalid({statusCode: 200, body: value});
});
for (const value of [null, undefined, [], 'erro']) test(`entrada HTTP invalida ${String(value)}`, () => invalid(value));
for (const statusCode of ['200', 201, 204, 400, 500]) test(`HTTP incoerente ${statusCode}`, () => invalid({statusCode, body: envelope()}));
for (const ctx of [undefined, null, {}, context('list', 'execute'), context('create', 'read'),
  context('edit', 'preview'), context('list', 'invalid'), context('invalid', 'prepare')]) {
  test(`contexto invalido ${JSON.stringify(ctx)}`, () => invalid({statusCode: 200, body: envelope()}, ctx));
}
for (const [input, ctx] of [
  [{statusCode: 201, body: creation()}, context()],
  [{statusCode: 200, body: deletionPreview()}, context('delete', 'execute')],
  [{statusCode: 200, body: envelope({status: 'ready', mutation_state: 'not_applied', data: preparation({parameters: {}})})}, context()],
  [{statusCode: 409, body: conflict('create')}, context('edit', 'execute')],
]) test(`acao/fase nao pode ser trocada: ${input.body.status}/${ctx.action}/${ctx.phase}`, () => invalid(input, ctx));

test('criacao nova HTTP200 viola o contrato de HTTP201', () => invalid({statusCode: 200, body: creation()}, context('create', 'execute')));
test('criacao reutilizada nao pode usar HTTP201', () => invalid({statusCode: 201, body: creation({reused: true})}, context('create', 'execute')));
test('ready sem acao nao e pedido preparado', () => invalid({statusCode: 200, body: envelope({
  status: 'ready', action: null, mutation_state: 'not_applied', data: preparation({parameters: {}})})}, context(null, 'prepare')));
test('pergunta nao pode devolver parametros executaveis', () => invalid({statusCode: 200, body: envelope({
  status: 'needs_clarification', action: 'create', mutation_state: 'not_applied', data: preparation({parameters: {demo: true}})})}, context('create', 'prepare')));
test('pergunta nao pode selecionar evento', () => invalid({statusCode: 200, body: envelope({
  status: 'needs_clarification', action: 'edit', mutation_state: 'not_applied', data: preparation({event: event()})})}, context('edit', 'prepare')));
for (const key of ['calendar_name', 'question', 'result', 'event', 'confirmation_token']) {
  test(`previa sem ${key} e rejeitada`, () => {
    const body = deletionPreview(); delete body.data[key]; invalid({statusCode: 200, body}, context('delete', 'preview'));
  });
}
test('previa nao aceita data.result contraditorio', () => {
  const body = deletionPreview(); body.data.result = 'deleted'; invalid({statusCode: 200, body}, context('delete', 'preview'));
});
test('previa nao aceita token de confirmacao inventado fora do formato', () => {
  const body = deletionPreview(); body.data.confirmation_token = 'MARCADOR_FICTICIO'; invalid({statusCode: 200, body}, context('delete', 'preview'));
});
for (const key of ['calendar_name', 'start', 'end', 'time_zone']) test(`conflito sem ${key} nao e relatorio completo`, () => {
  const body = conflict('create'); delete body.data[key]; invalid({statusCode: 409, body}, context('create', 'execute'));
});
test('conflito sem compromissos nao e conflito confirmado', () => {
  const body = conflict('reschedule'); body.data.conflicts = []; invalid({statusCode: 409, body}, context('reschedule', 'execute'));
});
test('edicao nao pode trocar o identificador', () => {
  const body = update('edit', 'updated'); body.data.after.event_id = 'outro-evento';
  invalid({statusCode: 200, body}, context('edit', 'execute'));
});
test('unchanged nao pode ser sucesso recuperado inventado', () => invalid({statusCode: 200, body: update('reschedule', 'unchanged', true)}, context('reschedule', 'execute')));
for (const mutate of [
  body => {body.error.retryable = true;}, body => {body.error.fields = 'date';},
  body => {body.error.fields = [42];}, body => {body.error = null;},
]) test(`erro invalido nao agenda repeticao: ${String(mutate)}`, () => {
  const body = failed(context(), 'operation_failed', 'not_applicable'); mutate(body); invalid({statusCode: 502, body});
});

test('mensagens, perguntas, candidatos e IDs privados nao aparecem na apresentacao', () => {
  const marker = 'SEGREDO_FICTICIO_NAO_E_CREDENCIAL_9876';
  for (const status of ['needs_clarification', 'needs_correction']) {
    const body = envelope({status, action: 'edit', message: marker, mutation_state: 'not_applied', data: preparation({
      question: marker, reason: marker, missing_fields: ['start', marker], invalid_fields: ['end', marker],
      candidates: [event({title: marker, event_id: marker}), event({title: marker})],
    })});
    const result = safe(presentApiResponse({statusCode: 200, body}, context('edit', 'prepare')));
    assert.equal(JSON.stringify(result.presentation).includes(marker), false);
  }
  const body = creation({event_id: marker, html_link: `https://example.invalid/${marker}`});
  body.operation_id = marker; body.message = marker;
  assert.equal(JSON.stringify(presentApiResponse({statusCode: 201, body}, context('create', 'execute')).presentation).includes(marker), false);
});
test('erro bruto e headers internos nao sao fonte da apresentacao', () => {
  const marker = 'ERRO_BRUTO_FICTICIO_NAO_PUBLICAR';
  const body = failed(context(), 'operation_failed', 'not_applicable', {message: marker});
  const result = presentApiResponse({statusCode: 502, body, headers: {Authorization: marker}, error: {message: marker, stack: marker}}, context());
  assert.equal(JSON.stringify(result.presentation).includes(marker), false);
  assert.equal(JSON.stringify(result).includes('Authorization'), false);
});
test('campos desconhecidos de duvida nao sao refletidos', () => {
  const body = envelope({status: 'needs_clarification', action: null, mutation_state: 'not_applied', data: preparation({
    missing_fields: ['__proto__', 'constructor', 'SEGREDO_FICTICIO', 'start', 'start']})});
  const result = presentApiResponse({statusCode: 200, body}, context(null, 'prepare'));
  assert.equal(result.presentation.text, 'Pode informar a data e o horário de início?');
});
test('objeto de entrada congelado e permitido: apresentador sem efeitos colaterais', () => {
  function freeze(value) {
    if (value && typeof value === 'object') {Object.values(value).forEach(freeze); Object.freeze(value);}
    return value;
  }
  const input = freeze({statusCode: 200, body: envelope()}); const ctx = freeze(context());
  expect(input, ctx, 'success', 'done');
});
test('fallback de request_id invalido e seguro', () => {
  const ctx = context(); ctx.request_id = 'INVALIDO\r\n';
  const result = invalid({statusCode: 200, body: 'invalido'}, ctx);
  assert.equal(result.response.request_id, 'n8n-unknown');
});
test('falha de transporte sem contexto falha de forma segura', () => {
  const result = safe(presentTransportFailure());
  assert.equal(result.http_status, 502);
  assert.equal(result.response.error.code, 'invalid_api_response');
});

for (const example of demonstration.cases) test(`demonstracao ficticia: ${example.name}`, () => {
  const result = safe(presentApiResponse(example.input, example.context));
  assert.equal(result.http_status, example.input.statusCode);
  assert.equal(result.presentation.kind, example.expected_kind);
  assert.equal(result.presentation.next_step, example.expected_next_step);
  assert.deepEqual(result.response, example.input.body);
});

const results = [];
for (const {name, run} of cases) {
  try {run(); results.push({name, status: 'passed'});}
  catch (error) {results.push({name, status: 'failed', error: error.message});}
}
const failedCases = results.filter(item => item.status === 'failed');
const report = {kind: 'local_response_presenter_tests', generated_at: new Date().toISOString(),
  synthetic_data_only: true, network_calls: 0, mutations: 0,
  total: results.length, passed: results.length - failedCases.length, failed: failedCases.length, cases: results};
const reportArgument = process.argv.indexOf('--report');
if (reportArgument >= 0) {
  const reportPath = process.argv[reportArgument + 1];
  if (!reportPath) throw new Error('--report exige um caminho de arquivo.');
  fs.mkdirSync(path.dirname(path.resolve(reportPath)), {recursive: true});
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n', 'utf8');
}
for (const item of failedCases) console.error(`FAIL ${item.name}\n  ${item.error}`);
console.log(`${report.passed}/${report.total} cenarios locais aprovados; ${report.failed} falhas. Sem rede, Google ou Docker.`);
process.exitCode = failedCases.length ? 1 : 0;
