const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '../workflows/disparo-agendado-teste.json'), 'utf8'));
const nodes = Object.fromEntries(workflow.nodes.map(node => [node.name, node]));
const tests = [];
const test = (name, callback) => {
  try { callback(); tests.push({name, status: 'passed'}); }
  catch (error) { tests.push({name, status: 'failed', message: error.message}); }
};
const canary = 'CANARY_PRIVATE_SCHEDULE_VALUE_2026';
const stateKey = 'schedule_smoke_test_v1';
const run = (name, state, input = {}, execution = {id: '9001', mode: 'production'}) => new Function(
  '$getWorkflowStaticData', '$input', '$execution', nodes[name].parameters.jsCode,
)(scope => { assert.equal(scope, 'global'); return state; }, {first: () => ({json: input})}, execution);
const begin = (state, execution) => run('Limitar a duas consultas', state, {}, execution);
const finish = (state, input, execution) => run('Registrar resumo seguro', state, input, execution);
const ok = (executionId = '9001') => ({statusCode: 200, headers: {authorization: canary}, body: {
  api_version: '1', request_id: `n8n-schedule-${executionId}`, operation_id: null, status: 'succeeded', action: null,
  mutation_state: 'not_applicable', message: 'Acesso autorizado.', data: {authenticated: true, service: 'Genius Agenda AI'}, error: null,
}});

test('uses real Schedule Trigger every minute with explicit local timezone', () => {
  const trigger = nodes['Disparo a cada minuto'];
  assert.equal(trigger.type, 'n8n-nodes-base.scheduleTrigger'); assert.equal(trigger.typeVersion, 1.4);
  assert.deepEqual(trigger.parameters.rule.interval, [{field: 'minutes', minutesInterval: 1}]);
  assert.equal(workflow.settings.timezone, 'America/Fortaleza');
  assert.equal(workflow.nodes.filter(node => /[Tt]rigger|webhook/.test(node.type)).length, 1);
});
test('only calls fixed protected status endpoint with GET and named bearer credential', () => {
  const http = nodes['Consultar status protegido'];
  assert.equal(http.type, 'n8n-nodes-base.httpRequest');
  assert.equal(http.parameters.method, 'GET');
  assert.equal(http.parameters.url, 'http://host.docker.internal:8000/api/v1/status');
  assert.equal(http.parameters.authentication, 'genericCredentialType');
  assert.equal(http.parameters.genericAuthType, 'httpBearerAuth');
  assert.deepEqual(http.credentials, {httpBearerAuth: {name: 'Genius API - local'}});
  assert.equal(http.parameters.sendBody, undefined); assert.equal(http.parameters.sendQuery, undefined);
  assert.deepEqual(http.parameters.headerParameters.parameters, [{name: 'X-Request-ID', value: '={{ $json.request_id }}'}]);
});
test('never redirects, retries or waits into the next minute', () => {
  const http = nodes['Consultar status protegido'];
  assert.equal(http.retryOnFail, false); assert.equal(http.onError, 'continueErrorOutput');
  assert.equal(http.parameters.options.redirect.redirect.followRedirects, false);
  assert.equal(http.parameters.options.response.response.neverError, true);
  assert.equal(http.parameters.options.response.response.fullResponse, true);
  assert.equal(http.parameters.options.timeout, 15000);
  assert.equal(workflow.settings.executionTimeout, 30);
});
test('inactive clean export retains no execution payloads, pin data or runtime state', () => {
  assert.equal(workflow.active, false); assert.deepEqual(workflow.pinData, {});
  assert.equal(workflow.staticData, undefined); assert.equal(workflow.id, undefined);
  assert.equal(workflow.settings.saveDataSuccessExecution, 'none');
  assert.equal(workflow.settings.saveDataErrorExecution, 'none');
  assert.equal(workflow.settings.saveManualExecutions, false);
  assert.equal(workflow.settings.saveExecutionProgress, false); assert.equal(workflow.settings.callerPolicy, 'none');
});
test('only guard can reach HTTP and both outcomes converge to the safe recorder', () => {
  assert.deepEqual(workflow.connections['Disparo a cada minuto'].main[0].map(edge => edge.node), ['Limitar a duas consultas']);
  assert.deepEqual(workflow.connections['Limitar a duas consultas'].main[0].map(edge => edge.node), ['Consultar status protegido']);
  assert.deepEqual(workflow.connections['Consultar status protegido'].main.map(output => output.map(edge => edge.node)),
    [['Registrar resumo seguro'], ['Registrar resumo seguro']]);
  assert.equal(workflow.connections['Registrar resumo seguro'], undefined);
  assert.equal(workflow.nodes.length, 4);
});
test('starts one correlated attempt and counts one authenticated status response', () => {
  const state = {};
  assert.deepEqual(begin(state), [{json: {request_id: 'n8n-schedule-9001'}}]);
  assert.equal(state[stateKey].attempts, 1); assert.equal(state[stateKey].last_result, 'pending');
  const result = finish(state, ok());
  assert.equal(result.length, 1); assert.equal(result[0].json.last_result, 'succeeded');
  assert.equal(state[stateKey].successes, 1); assert.equal(state[stateKey].failures, 0);
  assert.equal(state[stateKey].last_execution_id, '9001'); assert.equal(state[stateKey].last_mode, 'production');
  assert.match(state[stateKey].last_run_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
  assert.equal(JSON.stringify([state, result]).includes(canary), false);
});
for (const lastResult of ['succeeded', 'transport_error', 'http_error', 'invalid_response']) test(`limits to two total attempts when outcome is ${lastResult}`, () => {
  const state = {};
  for (let index = 1; index <= 2; index += 1) {
    const execution = {id: String(index), mode: 'production'};
    assert.equal(begin(state, execution).length, 1);
    const response = lastResult === 'succeeded' ? ok(String(index)) : lastResult === 'transport_error' ? {error: {message: canary}} :
      lastResult === 'http_error' ? {statusCode: 503, body: canary} : {statusCode: 200, body: canary};
    finish(state, response, execution); assert.equal(state[stateKey].last_result, lastResult);
  }
  const snapshot = JSON.stringify(state);
  assert.deepEqual(begin(state, {id: '3', mode: 'production'}), []); assert.equal(JSON.stringify(state), snapshot);
  assert.equal(state[stateKey].successes + state[stateKey].failures, 2);
});
test('uncompleted attempt fails closed and cannot start another request', () => {
  const state = {}; begin(state);
  assert.deepEqual(begin(state, {id: '9002', mode: 'production'}), []);
  assert.equal(state[stateKey].attempts, 1);
});
test('recording duplicate output cannot double-count a completed call', () => {
  const state = {}; begin(state); finish(state, ok()); const snapshot = JSON.stringify(state);
  assert.deepEqual(finish(state, ok()), []); assert.equal(JSON.stringify(state), snapshot);
});
test('different execution cannot finalize or overwrite a pending result', () => {
  const state = {}; begin(state); const snapshot = JSON.stringify(state);
  assert.deepEqual(finish(state, ok(), {id: '9002', mode: 'production'}), []); assert.equal(JSON.stringify(state), snapshot);
});
test('preserves first timestamp but retains only a tiny safe summary on the second call', () => {
  const state = {}; begin(state); finish(state, ok());
  const firstRun = state[stateKey].first_run_at;
  state[stateKey].discarded_extra = canary;
  begin(state, {id: '9002', mode: 'production'}); finish(state, ok('9002'), {id: '9002', mode: 'production'});
  assert.equal(state[stateKey].first_run_at, firstRun); assert.equal(state[stateKey].successes, 2);
  assert.equal(JSON.stringify(state).includes(canary), false);
  assert.deepEqual(Object.keys(state[stateKey]).sort(), ['version', 'attempts', 'successes', 'failures', 'first_run_at', 'last_run_at',
    'last_result', 'last_http_status', 'last_execution_id', 'last_mode'].sort());
});
for (const value of [null, [], 'bad', {}, {version: 2}, {version: 1, attempts: -1, successes: 0, failures: 0},
  {version: 1, attempts: 3, successes: 3, failures: 0}, {version: 1, attempts: 1, successes: 0, failures: 0},
  {version: 1, attempts: 0, successes: -1, failures: 1}, {version: 1, attempts: 0.5, successes: 0, failures: 0.5},
]) test(`rejects unexpected saved state ${JSON.stringify(value)}`, () => {
  const state = {[stateKey]: value}; const snapshot = JSON.stringify(state);
  assert.deepEqual(begin(state), []); assert.equal(JSON.stringify(state), snapshot);
});
for (const id of [undefined, null, 1, '', 'bad\n' + canary, 'A'.repeat(116), 'A'.repeat(129)]) test(`rejects unsafe execution id ${JSON.stringify(id)}`, () => {
  const state = {}; assert.deepEqual(begin(state, {id, mode: 'production'}), []); assert.deepEqual(state, {});
});
test('correlation header accepts the largest id that fits the API 128-character limit', () => {
  const state = {}; const execution = {id: 'A'.repeat(115), mode: 'production'};
  const result = begin(state, execution); assert.equal(result[0].json.request_id.length, 128);
  finish(state, ok(execution.id), execution); assert.equal(state[stateKey].last_result, 'succeeded');
});
for (const mode of ['test', 'manual', 'trigger', undefined, canary]) test(`blocks HTTP outside Code node production mode ${JSON.stringify(mode)}`, () => {
  const state = {}; assert.deepEqual(begin(state, {id: '9001', mode}), []);
  assert.deepEqual(state, {}); assert.equal(JSON.stringify(state).includes(canary), false);
});
for (const [name, mutate] of [
  ['missing version', result => delete result.body.api_version], ['extra body field', result => result.body.secret = canary],
  ['different request id', result => result.body.request_id = canary], ['operation id', result => result.body.operation_id = canary],
  ['write action', result => result.body.action = 'delete'], ['unconfirmed mutation', result => result.body.mutation_state = 'unknown'],
  ['error despite success', result => result.body.error = {message: canary}], ['failed status', result => result.body.status = 'failed'],
  ['not authenticated', result => result.body.data.authenticated = false], ['wrong service', result => result.body.data.service = canary],
  ['extra data', result => result.body.data.events = [{title: canary}]], ['missing message', result => result.body.message = null],
  ['array body', result => result.body = [canary]], ['HTML body', result => result.body = '<html>' + canary + '</html>'],
  ['null body', result => result.body = null], ['no data', result => result.body.data = null],
]) test(`rejects inconsistent status envelope: ${name}`, () => {
  const state = {}; begin(state); const response = ok(); mutate(response); const result = finish(state, response);
  assert.equal(state[stateKey].last_result, 'invalid_response'); assert.equal(state[stateKey].failures, 1);
  assert.equal(JSON.stringify([state, result]).includes(canary), false);
});
for (const statusCode of [301, 401, 403, 404, 422, 500, 502, 503]) test(`records safe HTTP ${statusCode} failure without body or headers`, () => {
  const state = {}; begin(state);
  const result = finish(state, {statusCode, body: {secret: canary}, headers: {authorization: canary}});
  assert.equal(state[stateKey].last_result, 'http_error'); assert.equal(state[stateKey].last_http_status, statusCode);
  assert.equal(state[stateKey].successes, 0); assert.equal(state[stateKey].failures, 1);
  assert.equal(JSON.stringify([state, result]).includes(canary), false);
});
for (const statusCode of [undefined, null, '200', 0, 600, 200.1]) test(`records transport or malformed HTTP failure ${JSON.stringify(statusCode)}`, () => {
  const state = {}; begin(state);
  const result = finish(state, {statusCode, error: {message: canary, stack: canary}, headers: {authorization: canary}, body: canary});
  assert.equal(state[stateKey].last_result, 'transport_error'); assert.equal(state[stateKey].last_http_status, null);
  assert.equal(JSON.stringify([state, result]).includes(canary), false);
});

const report = {kind: 'local_scheduled_workflow_checks', passed: tests.filter(item => item.status === 'passed').length,
  failed: tests.filter(item => item.status === 'failed').length, tests};
if (process.argv[2]) fs.writeFileSync(process.argv[2], JSON.stringify(report, null, 2) + '\n');
console.log(`${report.passed} passed, ${report.failed} failed`);
for (const failed of tests.filter(item => item.status === 'failed')) console.error(failed.name + ': ' + failed.message);
process.exitCode = report.failed ? 1 : 0;
