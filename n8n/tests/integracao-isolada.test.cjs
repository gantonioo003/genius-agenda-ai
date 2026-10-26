// Preparacao e grafo do ensaio: sem Docker, sockets, OAuth ou execucao de eventos.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const file = path.join(__dirname, '../workflows/integracao-isolada-teste.json');
const workflow = JSON.parse(fs.readFileSync(file, 'utf8'));
const nodes = Object.fromEntries(workflow.nodes.map(node => [node.name, node]));
const tests = [];
const test = (name, callback) => {
  try { callback(); tests.push({name, status: 'passed'}); }
  catch (error) { tests.push({name, status: 'failed', message: error.message}); }
};
const canary = 'FIXTURE_PRIVATE_INPUT';
const prepare = (body, extra = {}) => new Function('$input', '$execution',
  nodes['Preparar operacao de teste'].parameters.jsCode)(
  {first: () => ({json: {body, ...extra}})}, {id: 'static-001'})[0].json;
const expression = (source, item) => typeof source === 'string' && source.startsWith('={{')
  ? new Function('$json', 'return (' + source.slice(3, -2).trim() + ')')(item) : source;
const httpNodes = workflow.nodes.filter(node => node.type === 'n8n-nodes-base.httpRequest');
const reject = body => {
  const result = prepare(body);
  assert.equal(result.valid, false);
  assert.equal(result.http_status, 422);
  assert.equal(result.presentation.auto_retry, false);
  assert.equal(result.presentation.execution_allowed, false);
  assert.equal(result.path, undefined);
  assert.equal(result.method, undefined);
  assert.equal(JSON.stringify(result).includes(canary), false);
};
const mappings = {
  list: ['GET', '/api/v1/events', 'list', 'read'],
  get: ['GET', '/api/v1/events/fixture_123', 'get', 'read'],
  create: ['POST', '/api/v1/events', 'create', 'execute'],
  edit: ['PATCH', '/api/v1/events/fixture_123', 'edit', 'execute'],
  reschedule: ['PATCH', '/api/v1/events/fixture_123/schedule', 'reschedule', 'execute'],
  preview: ['POST', '/api/v1/events/fixture_123/deletion-preview', 'delete', 'preview'],
  delete: ['POST', '/api/v1/events/fixture_123/delete', 'delete', 'execute'],
};

for (const [action, [method, route, contextAction, phase]] of Object.entries(mappings)) {
  test('fixed route, method, context and fixture origin: ' + action, () => {
    const result = prepare({action, event_id: 'fixture_123'});
    assert.equal(result.valid, true);
    assert.equal(result.method, method);
    assert.equal(result.path, route);
    assert.deepEqual(result.context, {action: contextAction, phase, request_id: 'n8n-fixture-static-001'});
    const http = method === 'GET' ? nodes['API isolada - leitura'] : nodes['API isolada'];
    const url = expression(http.parameters.url, result);
    assert.equal(url, 'http://host.docker.internal:8001' + route);
    assert.equal(expression(http.parameters.method, result), method);
    assert.equal(http.parameters.sendBody, method !== 'GET');
  });
}
for (const [label, body] of [
  ['missing', undefined], ['null', null], ['array', []], ['string', canary],
  ['number', 12], ['boolean', true], ['missing action', {}],
]) test('reject invalid input shape: ' + label, () => reject(body));

for (const [label, action] of [
  ['unknown', canary], ['array create', ['create']], ['array delete', ['delete']],
  ['object', {toString: canary}], ['null', null], ['number', 1],
  ['inherited property', 'constructor'], ['prototype', '__proto__'],
]) test('reject invalid action type/value: ' + label, () => reject({action, body: {title: canary}}));

for (const field of ['url', 'host', 'method', 'path', 'headers', 'credentials', 'calendar_id', 'request_id']) {
  test('reject caller-controlled top-level field: ' + field, () => reject({action: 'list', [field]: canary}));
}

for (const [label, event_id] of [
  ['missing', undefined], ['empty', ''], ['number', 123], ['array', ['fixture']],
  ['slash', '../events'], ['encoded slash', '%2f'], ['query', 'event?url=' + canary],
  ['absolute URL', 'https://example.invalid/' + canary], ['newline', 'fixture\n123'],
  ['too long', 'a'.repeat(257)],
]) test('reject unsafe event identity: ' + label, () => reject({action: 'delete', event_id}));

test('event identity admits only bounded opaque characters', () => {
  for (const event_id of ['a', 'genius-a_1', 'a'.repeat(256)]) {
    const result = prepare({action: 'get', event_id});
    assert.equal(result.valid, true);
    assert.equal(result.path, '/api/v1/events/' + event_id);
  }
});

for (const field of ['body', 'query']) {
  for (const [label, value] of [['null', null], ['array', []], ['string', canary], ['number', 1]]) {
    test('reject non-object ' + field + ': ' + label, () => reject({action: 'create', [field]: value}));
  }
}
for (const field of ['url', 'method', 'headers', 'api_token', canary]) {
  test('reject unexpected query field: ' + field, () => reject({action: 'list', query: {[field]: canary}}));
}
test('preserve API payload and filter values for Python validation', () => {
  const body = {operation_id: 'fixture-operation', event: {title: 'Ficticio'}};
  const query = {date: '2030-06-17', period: 'week', time_zone: 'America/Recife'};
  const result = prepare({action: 'create', body, query});
  assert.equal(result.valid, true);
  assert.deepEqual(result.body, body);
  assert.deepEqual(result.query, query);
});
test('incoming headers/query and supplied request IDs never set API origin or correlation', () => {
  const result = prepare({action: 'list'}, {headers: {authorization: canary},
    query: {url: canary}, request_id: canary});
  assert.equal(result.valid, true);
  assert.equal(JSON.stringify(result).includes(canary), false);
  assert.deepEqual(result.query, {});
  assert.equal(result.request_id, 'n8n-fixture-static-001');
});
test('invalid requests bypass HTTP through a strict boolean branch', () => {
  const branch = nodes['Pedido valido?'].parameters.conditions;
  assert.equal(branch.options.typeValidation, 'strict');
  assert.equal(branch.conditions[0].leftValue, '={{ $json.valid }}');
  assert.equal(branch.conditions[0].operator.type, 'boolean');
  assert.deepEqual(workflow.connections['Pedido valido?'].main[0].map(x => x.node), ['Operacao de leitura?']);
  assert.deepEqual(workflow.connections['Pedido valido?'].main[1].map(x => x.node), ['Responder teste']);
});
test('GET and writes select separate HTTP nodes before any request', () => {
  const branch = nodes['Operacao de leitura?'].parameters.conditions;
  assert.equal(branch.options.typeValidation, 'strict');
  assert.equal(branch.conditions[0].operator.type, 'boolean');
  for (const action of Object.keys(mappings)) {
    const prepared = prepare({action, event_id: 'fixture_123'});
    const output = expression(branch.conditions[0].leftValue, prepared) ? 0 : 1;
    const selected = workflow.connections['Operacao de leitura?'].main[output];
    assert.equal(selected.length, 1);
    assert.equal(selected[0].node, prepared.method === 'GET' ? 'API isolada - leitura' : 'API isolada');
  }
});
test('sendBody uses literal booleans required by HTTP Request displayOptions', () => {
  assert.equal(httpNodes.length, 2);
  for (const http of httpNodes) assert.equal(typeof http.parameters.sendBody, 'boolean');
  const read = nodes['API isolada - leitura'];
  assert.equal(read.parameters.method, 'GET');
  assert.equal(read.parameters.sendBody, false);
  assert.equal(Object.hasOwn(read.parameters, 'jsonBody'), false);
  const write = nodes['API isolada'];
  assert.equal(write.parameters.sendBody, true);
  assert.equal(write.parameters.contentType, 'json');
  assert.equal(write.parameters.specifyBody, 'json');
  const body = {operation_id: 'fixture-json', event: {title: 'Ficticio'}};
  const encoded = expression(write.parameters.jsonBody, {body});
  assert.deepEqual(typeof encoded === 'string' ? JSON.parse(encoded) : encoded, body);
});
test('single API call per acyclic path, success/error converge on response', () => {
  const reached = new Set();
  function visit(name, seen, calls) {
    assert.ok(nodes[name], 'unknown node');
    assert.ok(!seen.has(name), 'cycle found');
    reached.add(name);
    const count = calls + (nodes[name].type === 'n8n-nodes-base.httpRequest' ? 1 : 0);
    assert.ok(count <= 1, 'API repeated');
    const next = new Set([...seen, name]);
    for (const edges of workflow.connections[name]?.main ?? []) {
      for (const edge of edges) visit(edge.node, next, count);
    }
  }
  visit('Webhook de teste', new Set(), 0);
  assert.equal(reached.size, workflow.nodes.length);
  for (const http of httpNodes) {
    assert.deepEqual(workflow.connections[http.name].main.map(edges => edges.map(x => x.node)),
      [['Apresentar resposta'], ['Falha de transporte']]);
  }
  for (const name of ['Apresentar resposta', 'Falha de transporte']) {
    assert.deepEqual(workflow.connections[name].main.flat().map(x => x.node), ['Responder teste']);
  }
});
test('fixture HTTP cannot redirect or retry and preserves API status/body', () => {
  for (const http of httpNodes) {
    assert.equal(http.retryOnFail, false);
    assert.equal(http.onError, 'continueErrorOutput');
    assert.equal(http.parameters.options.redirect.redirect.followRedirects, false);
    assert.equal(http.parameters.options.response.response.fullResponse, true);
    assert.equal(http.parameters.options.response.response.neverError, true);
    assert.equal(http.parameters.options.timeout, 15000);
    assert.equal(http.parameters.authentication, 'genericCredentialType');
    assert.equal(http.parameters.genericAuthType, 'httpBearerAuth');
  }
});
test('transport fault uses the prepared action/phase and never repeats writes', () => {
  for (const action of ['list', 'delete']) {
    const prepared = prepare({action, event_id: 'fixture_123'});
    const select = name => {
      assert.equal(name, 'Preparar operacao de teste');
      return {first: () => ({json: prepared})};
    };
    const result = new Function('$input', '$', nodes['Falha de transporte'].parameters.jsCode)(
      {first: () => ({json: {error: {message: canary}}})}, select)[0].json;
    assert.equal(result.http_status, 502);
    assert.equal(result.presentation.kind, action === 'delete' ? 'uncertain' : 'error');
    assert.equal(result.presentation.auto_retry, false);
    assert.equal(result.presentation.execution_allowed, false);
    assert.equal(JSON.stringify(result).includes(canary), false);
  }
});
test('public export is inactive, fixture-authenticated and contains no private credential values', () => {
  assert.equal(workflow.active, false);
  assert.deepEqual(workflow.pinData, {});
  assert.equal(workflow.settings.saveDataSuccessExecution, 'none');
  assert.equal(workflow.settings.saveDataErrorExecution, 'none');
  assert.equal(workflow.settings.saveManualExecutions, false);
  assert.equal(workflow.settings.saveExecutionProgress, false);
  assert.equal(nodes['Webhook de teste'].parameters.authentication, 'headerAuth');
  assert.equal(nodes['Webhook de teste'].parameters.responseMode, 'responseNode');
  for (const node of workflow.nodes) {
    for (const reference of Object.values(node.credentials ?? {})) {
      assert.deepEqual(Object.keys(reference), ['name']);
      assert.match(reference.name, /Fixture/);
    }
  }
});

const report = {scope: 'Isolated JavaScript execution and static fixture-workflow review. No n8n, network, Google or credentials accessed.',
  workflow_sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'),
  tests: tests.length, passed: tests.filter(item => item.status === 'passed').length,
  failed: tests.filter(item => item.status === 'failed').length, scenarios: tests};
if (process.argv[2]) fs.writeFileSync(process.argv[2], JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({tests: report.tests, passed: report.passed, failed: report.failed}));
for (const item of tests.filter(item => item.status === 'failed')) console.log(JSON.stringify(item));
if (report.failed) process.exitCode = 1;
