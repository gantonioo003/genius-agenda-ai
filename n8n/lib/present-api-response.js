// Compartilhado pelo export n8n e pelos testes. Sem I/O e sem executar operacoes.
function presentApiResponse(input, context) {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const text = value => typeof value === 'string' && value.trim().length > 0;
  const actions = ['create', 'list', 'get', 'reschedule', 'edit', 'delete'];
  const action = actions.includes(context?.action) ? context.action : null;
  const phase = context?.phase;
  const requestId = typeof context?.request_id === 'string' && /^[A-Za-z0-9._-]{1,128}$/.test(context.request_id)
    ? context.request_id : 'n8n-unknown';
  const mutation = ['create', 'reschedule', 'edit', 'delete'].includes(action);
  const uncertain = phase === 'execute';
  const result = (httpStatus, response, kind, message, nextStep, asks = false) => ({
    http_status: httpStatus, response,
    presentation: {kind, text: message, next_step: nextStep, requires_user_input: asks,
      auto_retry: false, execution_allowed: false},
  });
  const unknownText = 'Não foi possível confirmar se a alteração aconteceu. Confira a agenda antes de enviar o pedido novamente.';
  const invalid = () => result(502, {
    api_version: '1', request_id: requestId, operation_id: null, status: 'failed', action,
    mutation_state: uncertain ? 'unknown' : ['read', 'preview'].includes(phase) ? 'not_applicable' : 'not_applied',
    message: 'A API retornou uma resposta inesperada.', data: null,
    error: {code: 'invalid_api_response', retryable: false, fields: []},
  }, uncertain ? 'uncertain' : 'error', uncertain ? unknownText : 'Não consegui confirmar a resposta da agenda. Tente novamente mais tarde.',
  uncertain ? 'verify_state' : 'check_connection');
  const legalContext = (phase === 'prepare' && (action || context?.action === null)) ||
    (action && ((phase === 'read' && ['list', 'get'].includes(action)) ||
    (phase === 'execute' && mutation) || (phase === 'preview' && action === 'delete')));
  if (!legalContext || !object(input)) return invalid();
  const body = input.body;
  const httpStatus = input.statusCode;
  const keys = ['api_version', 'request_id', 'operation_id', 'status', 'action', 'mutation_state', 'message', 'data', 'error'];
  if (!object(body) || Object.keys(body).length !== keys.length || !keys.every(key => Object.hasOwn(body, key)) ||
      body.api_version !== '1' || !text(body.message) || body.message.length > 2000 ||
      typeof body.request_id !== 'string' || !/^[A-Za-z0-9._-]{1,128}$/.test(body.request_id) ||
      !(body.operation_id === null || (body.action === 'create' && text(body.operation_id) && body.operation_id.length <= 128))) return invalid();
  const infrastructure = body.action === null && body.status === 'failed' && body.mutation_state === 'not_applied';
  if (body.action !== action && !infrastructure) return invalid();
  const noError = body.error === null;
  const error = object(body.error) && text(body.error.code) && body.error.retryable === false &&
    Array.isArray(body.error.fields) && body.error.fields.every(field => typeof field === 'string');
  const data = body.data;
  const completeEvent = value => object(value) && text(value.event_id) && typeof value.title === 'string' &&
    text(value.start) && text(value.end) && typeof value.all_day === 'boolean' &&
    text(value.time_zone) && text(value.status) && (typeof value.html_link === 'string' || value.html_link === null);

  if (body.status === 'succeeded' && noError && object(data)) {
    if (phase === 'read' && httpStatus === 200 && body.mutation_state === 'not_applicable') {
      if (action === 'list' && Array.isArray(data.events) && data.events.every(completeEvent) &&
          text(data.calendar_name) && object(data.period) && text(data.period.start) && text(data.period.end) && text(data.period.time_zone)) {
        const n = data.events.length;
        return result(httpStatus, body, n ? 'success' : 'empty', n ? `Encontrei ${n} compromisso${n === 1 ? '' : 's'} neste período.` : 'Não há compromissos neste período.', 'done');
      }
      if (action === 'get' && completeEvent(data.event)) return result(httpStatus, body, 'success', 'Encontrei o compromisso solicitado.', 'done');
    }
    if (phase === 'preview' && httpStatus === 200 && body.mutation_state === 'not_applied' && data.result === 'already_absent' &&
        data.event === null && data.confirmation_token === null && data.question === null) {
      return result(httpStatus, body, 'success', 'Esse compromisso já não está na agenda. Nenhuma exclusão foi realizada.', 'done');
    }
    if (phase === 'execute' && body.mutation_state === 'confirmed' &&
        (httpStatus === 200 || (httpStatus === 201 && action === 'create'))) {
      if (action === 'create' && text(data.event_id) && text(data.calendar_name) && typeof data.reused === 'boolean' &&
          typeof data.recovered === 'boolean' && (httpStatus === 201 ? !data.reused : data.reused)) {
        return result(httpStatus, body, 'success', data.reused ? 'O compromisso existente foi reutilizado; não foi criada outra entrevista.' :
          data.recovered ? 'A criação foi confirmada após conferir a agenda.' : 'Entrevista criada na agenda.', 'done');
      }
      const allowed = {edit: ['updated', 'unchanged'], reschedule: ['rescheduled', 'unchanged'], delete: ['deleted', 'already_absent']};
      const snapshots = ['edit', 'reschedule'].includes(action) && completeEvent(data.before) && completeEvent(data.after) &&
        data.before.event_id === data.after.event_id && !(data.result === 'unchanged' && data.recovered);
      if (allowed[action]?.includes(data.result) && text(data.calendar_name) && typeof data.recovered === 'boolean' &&
          (snapshots || (action === 'delete' && text(data.event_id)))) {
        const message = data.result === 'unchanged' ? 'O compromisso já estava como solicitado. Nenhuma nova alteração foi necessária.' :
          data.result === 'already_absent' ? 'Esse compromisso já não está na agenda.' :
          data.recovered ? 'A alteração foi confirmada após conferir a agenda.' :
          {edit: 'Informações do compromisso atualizadas.', reschedule: 'Compromisso reagendado.', delete: 'Exclusão do compromisso confirmada.'}[action];
        return result(httpStatus, body, 'success', message, 'done');
      }
    }
    return invalid();
  }
  if (phase === 'prepare' && httpStatus === 200 && noError && body.mutation_state === 'not_applied' && object(data)) {
    if (body.status === 'ready' && action && object(data.parameters)) {
      return result(httpStatus, body, 'prepared', 'Os dados estão preparados. Nenhuma alteração foi executada.', 'await_requested_action');
    }
    if (['needs_clarification', 'needs_correction'].includes(body.status) && data.parameters === null && data.event === null &&
        Array.isArray(data.missing_fields) && Array.isArray(data.invalid_fields) && Array.isArray(data.candidates)) {
      const labels = {action: 'o que deseja fazer', title: 'o título do compromisso', start: 'a data e o horário de início',
        end: 'a data e o horário de término', time_zone: 'o fuso horário', date: 'a data',
        search_title: 'o título do compromisso', event_id: 'qual compromisso deseja', event: 'qual compromisso deseja',
        changes: 'o que deseja alterar', period: 'o período da consulta'};
      const correction = body.status === 'needs_correction';
      const fields = correction ? data.invalid_fields : data.missing_fields;
      const names = [...new Set(fields.filter(field => typeof field === 'string' && Object.hasOwn(labels, field)).map(field => labels[field]))];
      let question = correction ? 'Alguns dados precisam de correção. Confira a data, os horários e o compromisso.' :
        data.candidates.length > 1 ? 'Encontrei mais de um compromisso. Qual deles você quer?' :
        data.reason === 'event_not_found' ? 'Não encontrei esse compromisso. Pode conferir o título e a data?' : 'Pode completar os dados do pedido?';
      if (names.length) question = `${correction ? 'Pode corrigir' : 'Pode informar'} ${names.join(', ')}?`;
      return result(httpStatus, body, correction ? 'correction' : 'question', question, 'provide_details', true);
    }
  }
  if (phase === 'preview' && httpStatus === 200 && body.status === 'needs_confirmation' && noError &&
      body.mutation_state === 'not_applied' && object(data) && data.result === 'needs_confirmation' &&
      text(data.calendar_name) && text(data.question) && completeEvent(data.event) &&
      typeof data.confirmation_token === 'string' && /^[0-9a-f]{64}$/.test(data.confirmation_token)) {
    return result(httpStatus, body, 'confirmation', 'Confirma a exclusão do compromisso selecionado? Nenhuma exclusão foi realizada.', 'confirm_selected_event', true);
  }
  if (phase === 'execute' && httpStatus === 409 && body.status === 'conflict' && body.mutation_state === 'not_applied' &&
      error && body.error.code === 'schedule_conflict' && ['create', 'reschedule'].includes(action) &&
      object(data) && data.status === 'conflict' && text(data.calendar_name) && text(data.start) && text(data.end) && text(data.time_zone) &&
      Array.isArray(data.conflicts) && data.conflicts.length > 0 && data.conflicts.every(completeEvent)) {
    return result(httpStatus, body, 'conflict', 'Esse horário já está ocupado. Qual outro horário você prefere?', 'choose_another_time', true);
  }
  if (phase === 'read' && action === 'get' && httpStatus === 404 && body.status === 'not_found' &&
      body.mutation_state === 'not_applicable' && error && body.error.code === 'event_not_found') {
    return result(httpStatus, body, 'not_found', 'Não encontrei esse compromisso. Pode conferir qual compromisso deseja?', 'identify_event', true);
  }
  if (body.status === 'failed' && error) {
    if (infrastructure && ((httpStatus === 401 && body.error.code === 'unauthorized') ||
        (httpStatus === 503 && body.error.code === 'api_auth_unavailable'))) {
      return result(httpStatus, body, 'error', 'A conexão com a agenda precisa de atenção. Confira a configuração de acesso.', 'check_configuration');
    }
    const prewrite = phase === 'read' ? 'not_applicable' : 'not_applied';
    if (httpStatus === 422 && body.error.code === 'invalid_request' && body.mutation_state === (infrastructure ? 'not_applied' : prewrite)) {
      return result(httpStatus, body, 'correction', 'Confira a data, os horários e os dados do pedido.', 'provide_details', true);
    }
    if (httpStatus === 503 && body.error.code === 'calendar_setup_unavailable' && body.mutation_state === (phase === 'preview' ? 'not_applicable' : prewrite)) {
      return result(httpStatus, body, 'error', 'A conexão com a agenda precisa de atenção. Confira a configuração de acesso.', 'check_configuration');
    }
    if (httpStatus === 502 && uncertain && body.mutation_state === 'unknown') {
      return result(httpStatus, body, 'uncertain', unknownText, 'verify_state');
    }
    if (httpStatus === 502 && body.mutation_state === (phase === 'preview' ? 'not_applicable' : prewrite) && !uncertain) {
      return result(httpStatus, body, 'error', 'Não consegui consultar a agenda agora. Tente novamente mais tarde.', 'check_connection');
    }
  }
  return invalid();
}

function presentTransportFailure(context = {}) {
  const action = context.action;
  const uncertain = context.phase === 'execute' && ['create', 'edit', 'reschedule', 'delete'].includes(action);
  const body = {api_version: '1', request_id: context.request_id, operation_id: null, status: 'failed', action,
    mutation_state: uncertain ? 'unknown' : ['read', 'preview'].includes(context.phase) ? 'not_applicable' : 'not_applied',
    message: 'Não foi possível obter a resposta da API.', data: null,
    error: {code: uncertain ? 'operation_not_confirmed' : 'api_unavailable', retryable: false, fields: []}};
  return presentApiResponse({statusCode: 502, body}, context);
}

if (typeof module !== 'undefined') module.exports = {presentApiResponse, presentTransportFailure};
