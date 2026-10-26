const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const file = path.join(__dirname, '../workflows/consulta-eventos.json');
const workflow = JSON.parse(fs.readFileSync(file, 'utf8'));
const nodes = Object.fromEntries(workflow.nodes.map(n => [n.name, n]));
const run = (name, input) => new Function('$input', '$execution', nodes[name].parameters.jsCode)(
  {first: () => ({json: input}), all: () => [{json: input}]}, {id: 'review-001'})[0].json;
const tests = [];
const test = (name, callback) => {
  try { callback(); tests.push({name, status: 'passed'}); }
  catch (error) { tests.push({name, status: 'failed', message: error.message}); }
};
const canary = 'CANARY_PRIVATE_VALUE_2026';
const date = '2026-10-23';
const validate = (body, query = {}) => run('Validar consulta', {body, query, headers: {'content-type': 'application/json'}});
const envelope = code => ({api_version: '1', request_id: 'review-api-001', operation_id: null,
  status: code === 200 ? 'succeeded' : 'failed', action: 'list', mutation_state: 'not_applicable',
  message: 'Resposta segura.', data: code === 200 ? {calendar_name: 'Genius Testes', period: {start:'2026-10-23T00:00:00-03:00',end:'2026-10-24T00:00:00-03:00',time_zone:'America/Recife'}, events: []} : null,
  error: code === 200 ? null : {code: ({401:'unauthorized',422:'invalid_request',502:'operation_failed',503:'calendar_setup_unavailable'})[code], retryable: false, fields: []}});
test('default day and Recife only when absent', () => assert.deepEqual(validate({date}), {
  valid: true, date, period: 'day', time_zone: 'America/Recife', request_id: 'n8n-review-001'}));
for (const period of ['day', 'week', 'month']) test(`accept period ${period}`, () => {
  const result = validate({date, period, time_zone: 'UTC'});
  assert.equal(result.valid, true); assert.equal(result.period, period); assert.equal(result.time_zone, 'UTC');
});
for (const value of ['2024-02-29', '2000-02-29', '0001-01-01', '9999-12-31']) test(`accept civil date ${value}`, () => {
  assert.equal(validate({date: value}).valid, true);
});
for (const value of ['2026-02-30', '2023-02-29', '1900-02-29', '2026-13-01', '2026-00-01', '2026-01-00', '2026-01-32', '0000-01-01', '2026-1-01', '2026-01-1', '2026-10-23T10:00:00Z', '', null, 20261023, canary]) {
  test(`reject invalid date ${JSON.stringify(value)}`, () => assert.equal(validate({date: value}).valid, false));
}
for (const body of [undefined, null, [], 'text', 1, true, {}]) test(`reject invalid body ${JSON.stringify(body)}`, () => {
  assert.equal(validate(body).valid, false);
});
for (const period of ['', null, 1, false, [], {}, 'year', 'DAY', canary]) test(`reject invalid period ${JSON.stringify(period)}`, () => {
  assert.equal(validate({date, period}).valid, false);
});
for (const time_zone of ['', null, 1, false, [], {}, 'America/ Recife', 'A'.repeat(101)]) test(`reject invalid zone shape ${JSON.stringify(time_zone)}`, () => {
  assert.equal(validate({date, time_zone}).valid, false);
});
test('unknown IANA zone delegates semantic validation to Python', () => assert.equal(validate({date, time_zone: 'Unknown/Zone'}).valid, true));
for (const key of ['url', 'method', 'headers', 'calendar_id', 'user_id', 'action', canary]) test(`reject unknown field ${key}`, () => {
  const result = validate({date, [key]: canary});
  assert.equal(result.valid, false); assert.equal(result.http_status, 422); assert.equal(JSON.stringify(result).includes(canary), false);
});
test('reject external query without echoing name or value', () => {
  const result = validate({date}, {[canary]: canary}); assert.equal(result.valid, false); assert.equal(JSON.stringify(result).includes(canary), false);
});
test('project only filters and own correlation, without incoming authorization', () => {
  const result = run('Validar consulta', {body: {date}, query: {}, headers: {authorization: canary}, request_id: canary});
  assert.equal(result.valid, true); assert.equal(JSON.stringify(result).includes(canary), false);
});
for (const statusCode of [200, 422, 502, 503]) test(`preserve API HTTP ${statusCode}`, () => {
  const body = envelope(statusCode); const result=run('Normalizar resposta',{statusCode,body}); assert.equal(result.http_status,statusCode); assert.deepEqual(result.response,body); assert.equal(result.presentation.auto_retry,false); assert.equal(result.presentation.execution_allowed,false);
});
for (const statusCode of [401, 422, 503]) test(`preserve infrastructure HTTP ${statusCode}`, () => {
  const body = {...envelope(statusCode), action: null, mutation_state: 'not_applied'}; if(statusCode===503)body.error.code='api_auth_unavailable';
  const result=run('Normalizar resposta',{statusCode,body}); assert.equal(result.http_status,statusCode); assert.deepEqual(result.response,body); assert.equal(result.presentation.auto_retry,false);
});
for (const [name, input] of [
  ['HTML', {statusCode: 200, body: `<html>${canary}</html>`}], ['null', {statusCode: 200, body: null}],
  ['array', {statusCode: 200, body: [canary]}], ['extra field', {statusCode: 200, body: {...envelope(200), [canary]: canary}}],
  ['missing field', {statusCode: 200, body: {api_version: '1', data: canary}}], ['redirect', {statusCode: 301, body: envelope(200)}],
  ['string status', {statusCode: '200', body: envelope(200)}], ['write action', {statusCode: 200, body: {...envelope(200), action: 'delete'}}],
  ['unsafe request ID', {statusCode: 200, body: {...envelope(200), request_id: 'unsafe\n' + canary}}],
  ['200 infrastructure', {statusCode: 200, body: {...envelope(200), action: null, mutation_state: 'not_applied'}}],
]) test(`sanitize unexpected upstream ${name}`, () => {
  const result = run('Normalizar resposta', input); assert.equal(result.http_status, 502);
  assert.equal(result.response.error.code, 'invalid_api_response'); assert.equal(JSON.stringify(result).includes(canary), false);
});
test('transport failure strips raw error, headers, stack and URL', () => {
  const result = run('Falha de conexao', {error: {message: canary, stack: canary, headers: {authorization: canary}}, url: canary});
  assert.equal(result.http_status, 502); assert.equal(result.response.error.code, 'api_unavailable'); assert.equal(JSON.stringify(result).includes(canary), false);
});
test('constant API target, GET only, redirects and automatic retries disabled', () => {
  const node = nodes['Consultar API']; const p = node.parameters;
  assert.equal(p.method, 'GET'); assert.equal(p.url, 'http://host.docker.internal:8000/api/v1/events');
  assert.equal(node.retryOnFail, false); assert.equal(p.options.redirect.redirect.followRedirects, false);
  assert.equal(p.options.response.response.fullResponse, true); assert.equal(p.options.response.response.neverError, true);
  assert.equal(node.onError, 'continueErrorOutput'); assert.ok(p.options.timeout > 0); assert.equal(p.sendBody, undefined);
  assert.deepEqual(p.queryParameters.parameters.map(x => x.name), ['date', 'period', 'time_zone']);
  assert.deepEqual(p.headerParameters.parameters.map(x => x.name), ['X-Request-ID']); assert.equal(p.genericAuthType, 'httpBearerAuth');
});
test('explicit JSON object, status, cache and correlation in response', () => {
  const p = nodes['Responder pedido'].parameters; assert.equal(p.respondWith, 'json'); assert.equal(p.responseBody, '={{ { ...$json.response, presentation: $json.presentation } }}');
  assert.equal(p.options.responseCode, '={{ $json.http_status }}'); assert.deepEqual(p.options.responseHeaders.entries, [
    {name: 'Cache-Control', value: 'no-store'}, {name: 'X-Request-ID', value: '={{ $json.response.request_id }}'}]);
});
test('separate webhook and API credentials without embedded values', () => {
  const hook = nodes['Pedido de teste']; const api = nodes['Consultar API'];
  assert.equal(hook.parameters.httpMethod, 'POST'); assert.equal(hook.parameters.authentication, 'headerAuth');
  assert.equal(hook.parameters.responseMode, 'responseNode');
  for (const node of [hook, api]) for (const reference of Object.values(node.credentials)) assert.deepEqual(Object.keys(reference), ['name']);
  assert.notEqual(hook.credentials.httpHeaderAuth.name, api.credentials.httpBearerAuth.name);
});
test('no saved executions, progress or pinned data; inactive export', () => {
  assert.equal(workflow.active, false); assert.deepEqual(workflow.pinData, {});
  assert.equal(workflow.settings.saveDataSuccessExecution, 'none'); assert.equal(workflow.settings.saveDataErrorExecution, 'none');
  assert.equal(workflow.settings.saveManualExecutions, false); assert.equal(workflow.settings.saveExecutionProgress, false);
  assert.ok(workflow.settings.executionTimeout > nodes['Consultar API'].parameters.options.timeout / 1000);
});
test('invalid requests and transport errors pass through the presenter',()=>{
  assert.equal(workflow.connections['Consulta valida?'].main[1][0].node,'Normalizar resposta');
  assert.equal(workflow.connections['Falha de conexao'].main[0][0].node,'Normalizar resposta');
  const invalid=validate({date:'2026-02-30'});
  assert.equal(run('Normalizar resposta',invalid).presentation.kind,'correction');
  const transport=run('Falha de conexao',{error:{message:canary}});
  assert.equal(run('Normalizar resposta',transport).presentation.kind,'error');
});
test('no path cycles or repeats the API call',()=>{
  function visit(name,seen,apiCalls){
    assert.ok(!seen.includes(name),'cycle');
    const count=apiCalls+(name==='Consultar API'?1:0);assert.ok(count<=1,'API repeated');
    for(const output of workflow.connections[name]?.main??[])for(const edge of output)visit(edge.node,[...seen,name],count);
  }
  visit('Pedido de teste',[],0);
  assert.deepEqual(workflow.connections['Normalizar resposta'].main.flat().map(x=>x.node),['Responder pedido']);
});
test('bundled presenter matches shared tested source',()=>{
  const lib=fs.readFileSync(path.join(__dirname,'../lib/present-api-response.js'),'utf8').replace(/\nif \(typeof module[^\n]*\n?$/,'\n');
  assert.ok(nodes['Normalizar resposta'].parameters.jsCode.startsWith(lib));
});
const report = {scope: 'Isolated JavaScript and static workflow review only. No n8n, Google or credentials accessed.',
  workflow_sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'), tests: tests.length,
  passed: tests.filter(x => x.status === 'passed').length, failed: tests.filter(x => x.status === 'failed').length, scenarios: tests};
if (process.argv[2]) fs.writeFileSync(process.argv[2], JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({tests: report.tests, passed: report.passed, failed: report.failed}));
for (const failure of tests.filter(x => x.status === 'failed')) console.log(JSON.stringify(failure));
if (report.failed) process.exitCode = 1;
