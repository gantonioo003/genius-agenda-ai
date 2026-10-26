'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '../workflows/whatsapp-sandbox.json'), 'utf8'));
const lib = require('../lib/whatsapp-sandbox.cjs');
const id = 'b147bde8-ef4f-4d45-b152-ff2a725e6ca1';
const metadata = () => ({source: '360dialog_sandbox', request_id: id, message_count: 1,
  status_count: 0, event_types: ['message_text']});
const api = (shouldReply = true, reused = false) => ({statusCode: 200, body: {api_version: '1', request_id: id,
  operation_id: null, status: 'succeeded', action: 'whatsapp_test_reply',
  mutation_state: shouldReply ? 'confirmed' : 'not_applicable',
  message: 'Resultado da resposta de teste.', data: {authenticated: true,
    reply_state: shouldReply ? 'accepted' : 'skipped', reused}, error: null}});
const agentApi = (shouldReply = true, reused = false) => ({statusCode: 200, body: {api_version: '1', request_id: id,
  operation_id: null, status: 'succeeded', action: 'whatsapp_agent_turn', mutation_state: 'not_applicable',
  message: 'Turnos persistidos e ativados.', data: {authenticated: true,
    queue_state: shouldReply ? 'queued' : 'skipped', reused}, error: null}});
const node = name => workflow.nodes.find(item => item.name === name);

test('validacao aceita mensagens e statuses sem transportar corpo privado', () => {
  assert.deepEqual(lib.validateSandboxMetadata({body: metadata(), query: {}, headers: {authorization: 'PRIVATE'}}),
    {valid: true, request_id: id, should_reply: true});
  assert.deepEqual(lib.validateSandboxMetadata({body: {...metadata(), message_count: 0,
    status_count: 100, event_types: ['status_sent']}}), {valid: true, request_id: id, should_reply: false});
});

test('lote misto e todos os tipos suportados preservam somente UUID e decisao local', () => {
  const messageTypes = ['text', 'image', 'audio', 'video', 'document', 'sticker', 'location',
    'contacts', 'interactive', 'button', 'reaction', 'order', 'system', 'unknown', 'unsupported'];
  const statusTypes = ['sent', 'delivered', 'read', 'failed', 'played'];
  const body = {...metadata(), message_count: 100, status_count: 100,
    event_types: [...messageTypes.map(value => 'message_' + value),
      ...statusTypes.map(value => 'status_' + value)]};
  assert.deepEqual(lib.validateSandboxMetadata({body}),
    {valid: true, request_id: id, should_reply: true});
  assert.equal(JSON.stringify(lib.validateSandboxMetadata({body})).includes('message_text'), false);
});

const invalid = [
  ['corpo ausente', undefined], ['lista', []], ['telefone extra', {...metadata(), from: 'PRIVATE'}],
  ['mensagem extra', {...metadata(), text: 'PRIVATE'}], ['source errado', {...metadata(), source: 'other'}],
  ['id nao UUID', {...metadata(), request_id: 'PRIVATE'}],
  ['UUID nao canonico', {...metadata(), request_id: id.replaceAll('-', '')}],
  ['source ausente', (({source, ...rest}) => rest)(metadata())],
  ['contagem negativa', {...metadata(), message_count: -1}],
  ['contagem string', {...metadata(), message_count: '1'}],
  ['contagem boolean', {...metadata(), message_count: true}],
  ['contagem fracionada', {...metadata(), message_count: 1.5}],
  ['contagem excessiva', {...metadata(), status_count: 101}],
  ['contagem nula', {...metadata(), message_count: 0}],
  ['tipos vazios', {...metadata(), event_types: []}],
  ['tipos repetidos', {...metadata(), event_types: ['message_text', 'message_text']}],
  ['tipo objeto', {...metadata(), event_types: [{text: 'PRIVATE'}]}],
  ['tipo com telefone', {...metadata(), event_types: ['+5511999999999']}],
  ['tipo com espaco', {...metadata(), event_types: ['private message']}],
  ['tipo muito longo', {...metadata(), event_types: ['a'.repeat(33)]}],
  ['muitos tipos', {...metadata(), event_types: Array.from({length: 21}, (_, i) => `type_${i}`)}],
  ['tipo desconhecido', {...metadata(), event_types: ['message_private']}],
  ['fase de status desconhecida', {...metadata(), message_count: 0, status_count: 1,
    event_types: ['status_pending']}],
  ['status sem contagem', {...metadata(), event_types: ['message_text', 'status_sent']}],
  ['mensagem sem contagem', {...metadata(), message_count: 0, status_count: 1,
    event_types: ['message_text', 'status_sent']}],
  ['contagem sem tipo de mensagem', {...metadata(), event_types: ['status_sent']}],
  ['contagem sem tipo de status', {...metadata(), status_count: 1}],
  ['mais tipos de mensagem que mensagens', {...metadata(), event_types: ['message_text', 'message_image']}],
  ['mais fases que status', {...metadata(), message_count: 0, status_count: 1,
    event_types: ['status_read', 'status_delivered']}],
];
for (const [name, body] of invalid) test(`rejeita ${name}`, () => {
  assert.deepEqual(lib.validateSandboxMetadata({body}), {valid: false, http_status: 422,
    response: {status: 'invalid_request', api_reachable: false, request_id: null}});
});
test('rejeita query parameters e limites nao inteiros', () => {
  assert.equal(lib.validateSandboxMetadata({body: metadata(), query: {target: 'PRIVATE'}}).valid, false);
  assert.equal(lib.validateSandboxMetadata({body: metadata(), query: []}).valid, false);
  assert.equal(lib.validateSandboxMetadata({body: {...metadata(), message_count: Infinity}}).valid, false);
});

test('aceitacao da resposta tem correlacao exata e quatro campos externos', () => {
  assert.deepEqual(lib.normalizeSandboxStatus(api(), id, true), {http_status: 200,
    response: {status: 'received', api_reachable: true, request_id: id, reply_state: 'accepted'}});
});
test('lote somente de status exige skipped sem mutacao', () => {
  assert.deepEqual(lib.normalizeSandboxStatus(api(false), id, false), {http_status: 200,
    response: {status: 'received', api_reachable: true, request_id: id, reply_state: 'skipped'}});
});
for (const shouldReply of [true, false]) test(`reuso booleano aprovado para should_reply=${shouldReply}`, () => {
  assert.equal(lib.normalizeSandboxStatus(api(shouldReply, true), id, shouldReply).http_status, 200);
});
for (const [shouldReply, input] of [[false, api(true)], [true, api(false)]]) {
  test(`estado incompativel com should_reply=${shouldReply} nao confirma o webhook`, () => {
    assert.deepEqual(lib.normalizeSandboxStatus(input, id, shouldReply), lib.sandboxApiFailure(id));
  });
}
for (const flag of [undefined, null, 0, 1, 'true']) test(`decisao nao booleana ${String(flag)} falha segura`, () => {
  assert.deepEqual(lib.normalizeSandboxStatus(api(), id, flag), lib.sandboxApiFailure(id));
});
for (const status of [201, 301, 401, 403, 422, 500, 503]) test(`status ${status} vira falha segura`, () => {
  assert.deepEqual(lib.normalizeSandboxStatus({...api(), statusCode: status}, id, true), lib.sandboxApiFailure(id));
});
const badApi = [
  undefined, {statusCode: 200, body: 'PRIVATE'},
  {...api(), body: {...api().body, request_id: 'another'}},
  {...api(), body: {...api().body, status: 'failed'}},
  {...api(), body: {...api().body, mutation_state: 'applied'}},
  {...api(), body: {...api().body, data: {...api().body.data, authenticated: false}}},
  {...api(), body: {...api().body, data: null}},
  {...api(), body: {...api().body, error: {message: 'PRIVATE'}}},
  {...api(), body: {...api().body, private: 'PRIVATE'}},
  {...api(), body: {...api().body, action: null}},
  {...api(), body: {...api().body, action: 'events_create'}},
  {...api(), body: {...api().body, api_version: 1}},
  {...api(), body: {...api().body, operation_id: id}},
  {...api(), body: {...api().body, mutation_state: 'not_applicable'}},
  {...api(), body: {...api().body, data: {...api().body.data, reply_state: 'skipped'}}},
  {...api(), body: {...api().body, data: {...api().body.data, reply_state: 'delivered'}}},
  {...api(), body: {...api().body, data: {...api().body.data, reused: 1}}},
  {...api(), body: {...api().body, data: {...api().body.data, reused: null}}},
  {...api(), body: {...api().body, data: {authenticated: true, reply_state: 'accepted'}}},
  {...api(), body: {...api().body, data: {...api().body.data, provider_message_id: 'PRIVATE'}}},
  {...api(), body: {...api().body, message: 'PRIVATE'.repeat(300)}},
  {...api(), body: {...api().body, message: null}},
];
badApi.forEach((input, i) => test(`resposta invalida ${i} nao revela detalhes`, () => {
  const result = lib.normalizeSandboxStatus(input, id, true);
  assert.deepEqual(result, lib.sandboxApiFailure(id));
  assert.equal(JSON.stringify(result).includes('PRIVATE'), false);
}));

test('workflow executa os mesmos validadores testados', () => {
  assert.ok(node('Validar metadados').parameters.jsCode.includes(lib.validateSandboxMetadata.toString()));
  assert.ok(node('Confirmar fila').parameters.jsCode.includes(lib.normalizeSandboxAgentStatus.toString()));
  assert.ok(node('Falha segura').parameters.jsCode.includes(lib.sandboxApiFailure.toString()));
  const runValidate = new Function('$input', node('Validar metadados').parameters.jsCode);
  const valid = runValidate({first: () => ({json: {body: metadata()}})});
  assert.deepEqual(valid, [{json: {valid: true, request_id: id, should_reply: true}}]);
  const runNormalize = new Function('$input', '$', node('Confirmar fila').parameters.jsCode);
  assert.deepEqual(runNormalize({first: () => ({json: agentApi()})}, () => ({first: () => valid[0]})),
    [{json: {http_status: 200, response: {status: 'received', api_reachable: true,
      request_id: id, reply_state: 'queued'}}}]);
  const statusOnly = runValidate({first: () => ({json: {body: {...metadata(), message_count: 0,
    status_count: 1, event_types: ['status_read']}}})});
  assert.deepEqual(runNormalize({first: () => ({json: agentApi(false)})}, () => ({first: () => statusOnly[0]})),
    [{json: {http_status: 200, response: {status: 'received', api_reachable: true,
      request_id: id, reply_state: 'skipped'}}}]);
  const runFailure = new Function('$', node('Falha segura').parameters.jsCode);
  assert.deepEqual(runFailure(() => ({first: () => valid[0]})), [{json: lib.sandboxApiFailure(id)}]);
});

test('export inativo ativa somente a fila protegida sem transportar texto ou destinatario', () => {
  assert.equal(workflow.active, false);
  assert.equal(Object.hasOwn(workflow, 'id'), false);
  assert.deepEqual(workflow.pinData, {});
  const webhook = node('Receber metadados');
  assert.deepEqual(webhook.credentials, {httpHeaderAuth: {name: 'Genius WhatsApp Sandbox - entrada'}});
  assert.equal(webhook.parameters.authentication, 'headerAuth');
  assert.equal(webhook.parameters.httpMethod, 'POST');
  assert.equal(webhook.parameters.path, 'genius-agenda/whatsapp-sandbox');
  const http = workflow.nodes.filter(item => item.type === 'n8n-nodes-base.httpRequest');
  assert.equal(http.length, 1);
  assert.equal(http[0].parameters.method, 'POST');
  assert.equal(http[0].parameters.url, 'http://host.docker.internal:8000/api/v1/whatsapp/sandbox/agent');
  assert.equal(http[0].parameters.sendBody, true);
  assert.equal(http[0].parameters.contentType, 'json');
  assert.equal(http[0].parameters.specifyBody, 'json');
  assert.equal(http[0].parameters.jsonBody, '={{ { request_id: $json.request_id } }}');
  assert.deepEqual(http[0].parameters.headerParameters.parameters,
    [{name: 'X-Request-ID', value: '={{ $json.request_id }}'}]);
  assert.equal(http[0].parameters.options.timeout, 15000);
  assert.deepEqual(http[0].parameters.options.response.response,
    {fullResponse: true, neverError: true, responseFormat: 'json'});
  assert.deepEqual(http[0].credentials, {httpBearerAuth: {name: 'Genius API - local'}});
  assert.equal(http[0].retryOnFail, false);
  assert.equal(http[0].onError, 'continueErrorOutput');
  assert.equal(http[0].parameters.options.redirect.redirect.followRedirects, false);
  assert.deepEqual(workflow.settings, {executionOrder: 'v1', saveDataSuccessExecution: 'none',
    saveDataErrorExecution: 'none', saveManualExecutions: false, saveExecutionProgress: false,
    executionTimeout: 30, callerPolicy: 'none'});
  assert.equal(JSON.stringify(workflow).includes('callback_token'), false);
  assert.equal(JSON.stringify(workflow).includes('D360_API_KEY'), false);
});

test('grafo so ativa o agente apos validar; todos os caminhos respondem sem esperar LLM', () => {
  const target = (name, output = 0) => workflow.connections[name].main[output].map(item => item.node);
  assert.deepEqual(target('Metadados validos?', 0), ['Enfileirar agente']);
  assert.deepEqual(target('Metadados validos?', 1), ['Responder recebimento']);
  assert.deepEqual(target('Enfileirar agente', 0), ['Confirmar fila']);
  assert.deepEqual(target('Enfileirar agente', 1), ['Falha segura']);
  assert.deepEqual(target('Confirmar fila'), ['Responder recebimento']);
  assert.deepEqual(target('Falha segura'), ['Responder recebimento']);
});


test('metadados exigem UUID4 canonico igual ao contrato de ativacao', () => {
  for (const request_id of [id.toUpperCase(), id.replace('-4d45-', '-1d45-')]) {
    assert.equal(lib.validateSandboxMetadata({body: {...metadata(), request_id}}).valid, false);
  }
});

for (const shouldReply of [true, false]) for (const reused of [true, false]) {
  test(`fila confirma sem provar envio: mensagens=${shouldReply}, repeticao=${reused}`, () => {
    const result = lib.normalizeSandboxAgentStatus(agentApi(shouldReply, reused), id, shouldReply);
    assert.deepEqual(result, {http_status: 200, response: {status: 'received', api_reachable: true,
      request_id: id, reply_state: shouldReply ? 'queued' : 'skipped'}});
    assert.equal(JSON.stringify(result).includes('accepted'), false);
    assert.equal(JSON.stringify(result).includes('delivered'), false);
  });
}

const invalidAgent = [
  undefined, {statusCode: 200, body: 'PRIVATE'},
  {...agentApi(), statusCode: 202},
  {...agentApi(), body: {...agentApi().body, request_id: 'another'}},
  {...agentApi(), body: {...agentApi().body, status: 'failed'}},
  {...agentApi(), body: {...agentApi().body, action: 'whatsapp_test_reply'}},
  {...agentApi(), body: {...agentApi().body, mutation_state: 'confirmed'}},
  {...agentApi(), body: {...agentApi().body, operation_id: id}},
  {...agentApi(), body: {...agentApi().body, api_version: 1}},
  {...agentApi(), body: {...agentApi().body, error: {private: 'PRIVATE'}}},
  {...agentApi(), body: {...agentApi().body, private: 'PRIVATE'}},
  {...agentApi(), body: {...agentApi().body, message: 'PRIVATE'.repeat(300)}},
  {...agentApi(), body: {...agentApi().body, message: null}},
  {...agentApi(), body: {...agentApi().body, data: null}},
  {...agentApi(), body: {...agentApi().body, data: {...agentApi().body.data, authenticated: false}}},
  {...agentApi(), body: {...agentApi().body, data: {...agentApi().body.data, queue_state: 'accepted'}}},
  {...agentApi(), body: {...agentApi().body, data: {...agentApi().body.data, queue_state: 'skipped'}}},
  {...agentApi(), body: {...agentApi().body, data: {...agentApi().body.data, reply: 'PRIVATE'}}},
  {...agentApi(), body: {...agentApi().body, data: {...agentApi().body.data, recipient: 'PRIVATE'}}},
  {...agentApi(), body: {...agentApi().body, data: {...agentApi().body.data, reused: 1}}},
  {...agentApi(), body: {...agentApi().body, data: {authenticated: true, queue_state: 'queued'}}},
];
invalidAgent.forEach((input, i) => test(`fila rejeita resposta invalida ${i} sem dados privados`, () => {
  const result = lib.normalizeSandboxAgentStatus(input, id, true);
  assert.deepEqual(result, lib.sandboxApiFailure(id));
  assert.equal(JSON.stringify(result).includes('PRIVATE'), false);
}));

for (const shouldReply of [undefined, null, 0, 1, 'true']) {
  test(`fila nao aceita decisao nao booleana ${String(shouldReply)}`, () => {
    assert.deepEqual(lib.normalizeSandboxAgentStatus(agentApi(), id, shouldReply), lib.sandboxApiFailure(id));
  });
}

test('status nao enfileira trabalho mesmo com resposta de mensagens', () => {
  assert.deepEqual(lib.normalizeSandboxAgentStatus(agentApi(), id, false), lib.sandboxApiFailure(id));
});

test('workflow nao chama resposta fixa, LLM ou Google nem repete chamadas', () => {
  const value = JSON.stringify(workflow);
  assert.equal(value.includes('/whatsapp/sandbox/reply'), false);
  assert.equal(value.includes('/agent/dialogue'), false);
  assert.equal(value.includes('api.openai.com'), false);
  assert.equal(value.includes('googleapis.com'), false);
  for (const item of workflow.nodes) {
    assert.notEqual(item.retryOnFail, true);
    if (item.type === 'n8n-nodes-base.code') {
      assert.equal(item.parameters.jsCode.includes('console.log'), false);
    }
    for (const reference of Object.values(item.credentials || {})) assert.deepEqual(Object.keys(reference), ['name']);
  }
  const response = node('Responder recebimento');
  assert.equal(response.parameters.responseBody, '={{ $json.response }}');
});

test('gerador sandbox e idempotente e conserva todos os outros exports', () => {
  const os = require('node:os');
  const {execFileSync} = require('node:child_process');
  const parent = fs.realpathSync(os.tmpdir());
  const temporary = fs.mkdtempSync(path.join(parent, 'genius-n8n-agent-'));
  try {
    fs.mkdirSync(path.join(temporary, 'lib'));
    fs.mkdirSync(path.join(temporary, 'workflows'));
    for (const name of ['build-workflow.cjs', 'lib/whatsapp-sandbox.cjs', 'workflows/whatsapp-sandbox.json']) {
      fs.copyFileSync(path.join(__dirname, '..', name), path.join(temporary, name));
    }
    const protectedExports = ['consulta-eventos.json', 'disparo-agendado-teste.json', 'integracao-isolada-teste.json'];
    for (const name of protectedExports) fs.writeFileSync(path.join(temporary, 'workflows', name), 'PROTECTED-' + name);
    const generated = path.join(temporary, 'workflows/whatsapp-sandbox.json');
    const before = fs.readFileSync(generated, 'utf8');
    execFileSync(process.execPath, [path.join(temporary, 'build-workflow.cjs'), '--sandbox']);
    assert.equal(fs.readFileSync(generated, 'utf8'), before);
    execFileSync(process.execPath, [path.join(temporary, 'build-workflow.cjs'), '--sandbox']);
    assert.equal(fs.readFileSync(generated, 'utf8'), before);
    for (const name of protectedExports) assert.equal(fs.readFileSync(path.join(temporary, 'workflows', name), 'utf8'), 'PROTECTED-' + name);
  } finally {
    const resolved = fs.realpathSync(temporary);
    assert.equal(path.dirname(resolved), parent);
    assert.ok(path.basename(resolved).startsWith('genius-n8n-agent-'));
    fs.rmSync(resolved, {recursive: true});
  }
});

for (const requestId of [null, undefined, 'not-a-uuid', id.toUpperCase()]) {
  test(`fila exige correlacao canonica ${String(requestId)}`, () => {
    const input = agentApi();
    input.body.request_id = requestId;
    assert.deepEqual(lib.normalizeSandboxAgentStatus(input, requestId, true), lib.sandboxApiFailure(requestId));
  });
}
