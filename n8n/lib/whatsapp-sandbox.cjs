'use strict';

// Somente metadados normalizados. Nenhuma mensagem, telefone ou credencial.
function validateSandboxMetadata(source) {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const body = source?.body;
  const keys = ['source', 'request_id', 'message_count', 'status_count', 'event_types'];
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const failure = () => ({valid: false, http_status: 422,
    response: {status: 'invalid_request', api_reachable: false, request_id: null}});
  if (!object(source) || !object(body) || Object.keys(body).length !== keys.length ||
      !keys.every(key => Object.hasOwn(body, key)) ||
      (source.query !== undefined && (!object(source.query) || Object.keys(source.query).length))) return failure();
  if (body.source !== '360dialog_sandbox' || typeof body.request_id !== 'string' ||
      !uuid.test(body.request_id)) return failure();
  if (![body.message_count, body.status_count].every(value =>
      Number.isInteger(value) && value >= 0 && value <= 100) ||
      body.message_count + body.status_count === 0) return failure();
  if (!Array.isArray(body.event_types) || body.event_types.length === 0 || body.event_types.length > 20 ||
      !body.event_types.every(value => typeof value === 'string' && /^[a-z][a-z0-9_]{0,31}$/.test(value)) ||
      new Set(body.event_types).size !== body.event_types.length) return failure();
  const allowed = new Set([
    'message_text', 'message_image', 'message_audio', 'message_video', 'message_document',
    'message_sticker', 'message_location', 'message_contacts', 'message_interactive',
    'message_button', 'message_reaction', 'message_order', 'message_system',
    'message_unknown', 'message_unsupported',
    'status_sent', 'status_delivered', 'status_read', 'status_failed', 'status_played',
  ]);
  if (!body.event_types.every(value => allowed.has(value)) ||
      body.event_types.some(value => value.startsWith('message_')) !== (body.message_count > 0) ||
      body.event_types.some(value => value.startsWith('status_')) !== (body.status_count > 0) ||
      body.event_types.filter(value => value.startsWith('message_')).length > body.message_count ||
      body.event_types.filter(value => value.startsWith('status_')).length > body.status_count) return failure();
  // Dados da origem deixam de circular apos a validacao.
  return {valid: true, request_id: body.request_id, should_reply: body.message_count > 0};
}

function sandboxApiFailure(requestId) {
  return {http_status: 503,
    response: {status: 'api_unavailable', api_reachable: false, request_id: requestId}};
}

function normalizeSandboxStatus(input, requestId, shouldReply) {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const body = input?.body;
  const keys = ['api_version', 'request_id', 'operation_id', 'status', 'action',
    'mutation_state', 'message', 'data', 'error'];
  const dataKeys = ['authenticated', 'reply_state', 'reused'];
  const replyState = shouldReply ? 'accepted' : 'skipped';
  const mutationState = shouldReply ? 'confirmed' : 'not_applicable';
  if (typeof shouldReply !== 'boolean' || !object(input) || input.statusCode !== 200 || !object(body) ||
      Object.keys(body).length !== keys.length || !keys.every(key => Object.hasOwn(body, key)) ||
      body.api_version !== '1' || body.request_id !== requestId || body.operation_id !== null ||
      body.status !== 'succeeded' || body.action !== 'whatsapp_test_reply' || body.mutation_state !== mutationState ||
      body.error !== null || typeof body.message !== 'string' || body.message.length > 2000 ||
      !object(body.data) || Object.keys(body.data).length !== dataKeys.length ||
      !dataKeys.every(key => Object.hasOwn(body.data, key)) || body.data.authenticated !== true ||
      body.data.reply_state !== replyState || typeof body.data.reused !== 'boolean') {
    return sandboxApiFailure(requestId);
  }
  return {http_status: 200,
    response: {status: 'received', api_reachable: true, request_id: requestId, reply_state: replyState}};
}


// Apenas confirma ativação durável. O worker Python responde depois, fora do callback.
function normalizeSandboxAgentStatus(input, requestId, shouldReply) {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const body = input?.body;
  const keys = ['api_version', 'request_id', 'operation_id', 'status', 'action',
    'mutation_state', 'message', 'data', 'error'];
  const dataKeys = ['authenticated', 'queue_state', 'reused'];
  const queueState = shouldReply ? 'queued' : 'skipped';
  const canonicalId = typeof requestId === 'string' &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(requestId);
  if (!canonicalId || typeof shouldReply !== 'boolean' || !object(input) || input.statusCode !== 200 || !object(body) ||
      Object.keys(body).length !== keys.length || !keys.every(key => Object.hasOwn(body, key)) ||
      body.api_version !== '1' || body.request_id !== requestId || body.operation_id !== null ||
      body.status !== 'succeeded' || body.action !== 'whatsapp_agent_turn' || body.mutation_state !== 'not_applicable' ||
      body.error !== null || typeof body.message !== 'string' || body.message.length > 2000 ||
      !object(body.data) || Object.keys(body.data).length !== dataKeys.length ||
      !dataKeys.every(key => Object.hasOwn(body.data, key)) || body.data.authenticated !== true ||
      body.data.queue_state !== queueState || typeof body.data.reused !== 'boolean') {
    return sandboxApiFailure(requestId);
  }
  return {http_status: 200,
    response: {status: 'received', api_reachable: true, request_id: requestId, reply_state: queueState}};
}

module.exports = {validateSandboxMetadata, normalizeSandboxStatus, normalizeSandboxAgentStatus, sandboxApiFailure};
