'use strict';

// These functions are embedded verbatim in the public workflow. No runtime state,
// payload, event identifier, header or provider message is retained here.
function prepareReminderTick(executionId) {
  const id = typeof executionId === 'string' ? executionId :
    Number.isSafeInteger(executionId) && executionId >= 0 ? String(executionId) : '';
  const requestId = 'reminder-tick-' + id;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(id) ||
      !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(requestId)) {
    throw new Error('reminder_tick_invalid_request_id');
  }
  return {request_id: requestId};
}

function normalizeReminderTick(input, requestId) {
  const fail = () => { throw new Error('reminder_tick_invalid_response'); };
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length &&
    keys.every(key => Object.hasOwn(value, key));
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const states = ['pending', 'blocked', 'reserved', 'sending', 'accepted', 'uncertain', 'cancelled', 'expired'];
  const counts = value => object(value) && Object.keys(value).every(key => states.includes(key) && count(value[key]));
  const syncKeys = ['status', 'events_checked', 'tracked_reminders', 'reminders_considered',
    'cancelled_count', 'replanned_count', 'unchanged_count', 'state_counts'];
  const generationKeys = ['status', 'events_seen', 'eligible_events', 'skipped_count',
    'reminders_considered', 'state_counts'];
  const summary = (value, keys, statuses) => exact(value, keys) && statuses.includes(value.status) &&
    keys.filter(key => !['status', 'state_counts'].includes(key)).every(key => count(value[key])) && counts(value.state_counts);
  const emptySummary = value => Object.entries(value).every(([key, item]) =>
    key === 'status' || (key === 'state_counts' ? Object.keys(item).length === 0 : item === 0));
  const cloneSummary = value => ({...value, state_counts: {...value.state_counts}});
  if (typeof requestId !== 'string' || !/^reminder-tick-[A-Za-z0-9][A-Za-z0-9._-]*$/.test(requestId) ||
      requestId.length > 128 || !object(input) || ![200, 503].includes(input.statusCode)) return fail();
  const body = input.body;
  if (!exact(body, ['api_version', 'request_id', 'operation_id', 'status', 'action',
    'mutation_state', 'message', 'data', 'error']) || body.api_version !== '1' ||
    body.request_id !== requestId || body.operation_id !== null || body.action !== 'reminder_tick' ||
    body.mutation_state !== 'not_applicable' || typeof body.message !== 'string' ||
    body.message.length === 0 || body.message.length > 2000) return fail();
  const data = body.data;
  if (!exact(data, ['tick_status', 'skip_reason', 'local_mutation_state', 'synchronization',
    'generation', 'messages_sent', 'external_mutations']) || data.messages_sent !== 0 ||
    data.external_mutations !== 0 ||
    !summary(data.synchronization, syncKeys, ['not_run', 'synchronized', 'failed']) ||
    !summary(data.generation, generationKeys, ['not_run', 'planned', 'failed'])) return fail();
  if (input.statusCode === 503) {
    const partial = data.tick_status === 'partial';
    if (body.status !== 'failed' || !['failed', 'partial'].includes(data.tick_status) ||
        data.skip_reason !== null || data.local_mutation_state !== (partial ? 'partial' : 'not_applied') ||
        !exact(body.error, ['code', 'retryable', 'fields']) ||
        body.error.code !== (partial ? 'reminder_tick_partial' : 'reminder_tick_failed') ||
        body.error.retryable !== false || !Array.isArray(body.error.fields) || body.error.fields.length !== 0 ||
        (partial && (data.synchronization.status !== 'synchronized' || !['failed', 'planned'].includes(data.generation.status))) ||
        (!partial && (!['not_run', 'failed'].includes(data.synchronization.status) || data.generation.status !== 'not_run'))) return fail();
    throw new Error(partial ? 'reminder_tick_partial' : 'reminder_tick_failed');
  }
  if (body.error !== null) return fail();
  if (data.tick_status === 'completed') {
    if (body.status !== 'succeeded' || data.skip_reason !== null || data.local_mutation_state !== 'applied' ||
        data.synchronization.status !== 'synchronized' || data.generation.status !== 'planned') return fail();
  } else if (data.tick_status === 'skipped') {
    if (body.status !== 'skipped' || !['not_configured', 'disabled', 'permission_required', 'busy'].includes(data.skip_reason) ||
        data.local_mutation_state !== 'not_applied' || data.synchronization.status !== 'not_run' ||
        data.generation.status !== 'not_run' || !emptySummary(data.synchronization) || !emptySummary(data.generation)) return fail();
  } else return fail();
  return {
    request_id: requestId, status: body.status, tick_status: data.tick_status,
    skip_reason: data.skip_reason, local_mutation_state: data.local_mutation_state,
    synchronization: cloneSummary(data.synchronization), generation: cloneSummary(data.generation),
    messages_sent: 0, external_mutations: 0,
  };
}

// Pure validation of a dispatch response. This function performs no IO.
function normalizeReminderDispatch(input, requestId) {
  const fail = () => { throw new Error('reminder_dispatch_invalid_response'); };
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length &&
    keys.every(key => Object.hasOwn(value, key));
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const counters = ['reminders_considered', 'expired_count', 'cancelled_count', 'blocked_count',
    'accepted_count', 'uncertain_count', 'failed_count', 'transport_attempts'];
  if (typeof requestId !== 'string' || !/^reminder-tick-[A-Za-z0-9][A-Za-z0-9._-]*$/.test(requestId) ||
      requestId.length > 128 || !object(input) || ![200, 503].includes(input.statusCode)) return fail();
  const body = input.body;
  if (!exact(body, ['api_version', 'request_id', 'operation_id', 'status', 'action',
    'mutation_state', 'message', 'data', 'error']) || body.api_version !== '1' ||
    body.request_id !== requestId || body.operation_id !== null || body.action !== 'reminder_dispatch' ||
    typeof body.message !== 'string' || body.message.length === 0 || body.message.length > 2000) return fail();
  const data = body.data;
  const validError = code => exact(body.error, ['code', 'retryable', 'fields']) &&
    body.error.code === code && body.error.retryable === false &&
    Array.isArray(body.error.fields) && body.error.fields.length === 0;
  // An unexpected boundary failure cannot invent counters for a transport that
  // may already have started. It still terminates with a fixed, safe error.
  if (data === null) {
    if (input.statusCode !== 503 || body.status !== 'failed' ||
        !['unknown', 'not_applied'].includes(body.mutation_state) ||
        !validError('reminder_dispatch_failed')) return fail();
    throw new Error('reminder_dispatch_failed');
  }
  if (!exact(data, ['dispatch_status', 'skip_reason', ...counters]) ||
      !counters.every(key => count(data[key])) ||
      data.transport_attempts !== data.accepted_count + data.uncertain_count) return fail();
  const mutationState = data.uncertain_count > 0 || data.transport_attempts > data.accepted_count ? 'unknown' :
    data.transport_attempts === data.accepted_count && data.accepted_count > 0 ? 'confirmed' : 'not_applied';
  if (body.mutation_state !== mutationState) return fail();
  if (input.statusCode === 503) {
    const partial = data.dispatch_status === 'partial';
    if (body.status !== 'failed' || !['failed', 'partial'].includes(data.dispatch_status) ||
        data.skip_reason !== null || data.failed_count + data.uncertain_count === 0 ||
        !validError(partial ? 'reminder_dispatch_partial' : 'reminder_dispatch_failed')) return fail();
    throw new Error(partial ? 'reminder_dispatch_partial' : 'reminder_dispatch_failed');
  }
  if (body.error !== null) return fail();
  if (data.dispatch_status === 'completed') {
    if (body.status !== 'succeeded' || data.skip_reason !== null ||
        data.failed_count !== 0 || data.uncertain_count !== 0) return fail();
  } else if (data.dispatch_status === 'skipped') {
    if (body.status !== 'skipped' || !['not_configured', 'disabled', 'permission_required', 'busy'].includes(data.skip_reason) ||
        counters.some(key => data[key] !== 0)) return fail();
  } else return fail();
  return {request_id: requestId, status: body.status, mutation_state: body.mutation_state,
    dispatch_status: data.dispatch_status, skip_reason: data.skip_reason,
    reminders_considered: data.reminders_considered, expired_count: data.expired_count,
    cancelled_count: data.cancelled_count, blocked_count: data.blocked_count,
    accepted_count: data.accepted_count, uncertain_count: data.uncertain_count,
    failed_count: data.failed_count, transport_attempts: data.transport_attempts};
}

function normalizeReminderRecovery(input, requestId) {
  const fail = () => { throw new Error('reminder_recovery_invalid_response'); };
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length &&
    keys.every(key => Object.hasOwn(value, key));
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const counters = ['reservations_recovered', 'expired_count', 'uncertain_count', 'protected_count'];
  if (typeof requestId !== 'string' || !/^reminder-tick-[A-Za-z0-9][A-Za-z0-9._-]*$/.test(requestId) ||
      requestId.length > 128 || !object(input) || ![200, 503].includes(input.statusCode)) return fail();
  const body = input.body;
  if (!exact(body, ['api_version', 'request_id', 'operation_id', 'status', 'action',
    'mutation_state', 'message', 'data', 'error']) || body.api_version !== '1' ||
    body.request_id !== requestId || body.operation_id !== null || body.action !== 'reminder_recovery' ||
    body.mutation_state !== 'not_applicable' || typeof body.message !== 'string' ||
    body.message.length === 0 || body.message.length > 2000) return fail();
  const validError = () => exact(body.error, ['code', 'retryable', 'fields']) &&
    body.error.code === 'reminder_recovery_failed' && body.error.retryable === false &&
    Array.isArray(body.error.fields) && body.error.fields.length === 0;
  const data = body.data;
  // A boundary failure cannot fabricate counters when the commit is unknown.
  if (data === null) {
    if (input.statusCode !== 503 || body.status !== 'failed' || !validError()) return fail();
    throw new Error('reminder_recovery_failed');
  }
  if (!exact(data, ['recovery_status', 'skip_reason', ...counters, 'messages_sent', 'external_mutations']) ||
      !counters.every(key => count(data[key])) || data.messages_sent !== 0 || data.external_mutations !== 0) return fail();
  if (input.statusCode === 503) {
    if (body.status !== 'failed' || data.recovery_status !== 'failed' || data.skip_reason !== null ||
        !validError()) return fail();
    // Counts already confirmed before a cleanup failure remain observations,
    // but the next phase must not run on an HTTP failure.
    throw new Error('reminder_recovery_failed');
  }
  if (body.error !== null) return fail();
  if (data.recovery_status === 'completed') {
    if (body.status !== 'succeeded' || data.skip_reason !== null) return fail();
  } else if (data.recovery_status === 'skipped') {
    if (body.status !== 'skipped' || !['not_configured', 'disabled', 'permission_required', 'busy'].includes(data.skip_reason) ||
        counters.some(key => data[key] !== 0)) return fail();
  } else return fail();
  return {request_id: requestId, status: body.status, recovery_status: data.recovery_status,
    skip_reason: data.skip_reason, reservations_recovered: data.reservations_recovered,
    expired_count: data.expired_count, uncertain_count: data.uncertain_count,
    protected_count: data.protected_count, messages_sent: 0, external_mutations: 0};
}

module.exports = {prepareReminderTick, normalizeReminderTick, normalizeReminderDispatch, normalizeReminderRecovery};
