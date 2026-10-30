/** Fictional, memory-only fixtures for the demonstration. No provider access. */
export const DEMO_TODAY = '2026-10-27';
export const DEMO_TIME = '12:00';

const EVENTS = [
  {
    id: 'demo-ponto-trajetoria', title: 'Conversa sobre trajetória', organization: 'Ponto Oficina',
    initials: 'PO', accent: 'blue', date: '2026-10-26', start: '16:30', end: '17:00',
    location: 'Chamada de vídeo', kind: 'Trajetória',
    description: 'Encontro anterior do cenário, usado para ilustrar avisos com resultado incerto ou prazo encerrado. Dados inteiramente fictícios.',
  },
  {
    id: 'demo-aurora-rh', title: 'Conversa com o time', organization: 'Aurora Studio',
    initials: 'AS', accent: 'coral', date: '2026-10-27', start: '09:30', end: '10:00',
    location: 'Google Meet', kind: 'Cultura e pessoas',
    description: 'Conversa inicial sobre trajetória, interesses e a rotina do time. Cenário fictício para demonstração.',
  },
  {
    id: 'demo-orbital-tecnica', title: 'Entrevista técnica', organization: 'Orbital Labs',
    initials: 'OL', accent: 'violet', date: '2026-10-27', start: '14:30', end: '15:15',
    location: 'Google Meet', kind: 'Técnica',
    description: 'Apresentar um projeto de automação com Python e conversar sobre APIs, testes e decisões de implementação. Cenário fictício para demonstração.',
  },
  {
    id: 'demo-nuvem-produto', title: 'Conhecendo o produto', organization: 'Nuvem Atelier',
    initials: 'NA', accent: 'mint', date: '2026-10-27', start: '17:00', end: '17:30',
    location: 'Chamada de vídeo', kind: 'Produto',
    description: 'Conversa com a equipe sobre o produto e as possibilidades de contribuição. Levar perguntas sobre o trabalho. Cenário fictício para demonstração.',
  },
  {
    id: 'demo-vertice-portfolio', title: 'Apresentação do portfólio', organization: 'Vértice Digital',
    initials: 'VD', accent: 'blue', date: '2026-10-28', start: '10:00', end: '10:45',
    location: 'Google Meet', kind: 'Portfólio',
    description: 'Demonstração de um projeto pessoal: problema, solução, arquitetura e aprendizados. Cenário fictício para demonstração.',
  },
  {
    id: 'demo-aurora-feedback', title: 'Conversa de acompanhamento', organization: 'Aurora Studio',
    initials: 'AS', accent: 'coral', date: '2026-10-28', start: '16:00', end: '16:30',
    location: 'Chamada de vídeo', kind: 'Acompanhamento',
    description: 'Espaço para perguntas e alinhamento dos próximos passos do processo. Cenário fictício para demonstração.',
  },
  {
    id: 'demo-margem-revisao', title: 'Revisão do projeto', organization: 'Margem Coletivo',
    initials: 'MC', accent: 'mint', date: '2026-10-31', start: '11:00', end: '11:45',
    location: 'Sala virtual', kind: 'Projeto',
    description: 'Revisão conjunta de uma solução de agenda e de seus pontos de melhoria. Cenário fictício para demonstração.',
  },
  {
    id: 'demo-orbital-proximos', title: 'Próximos passos', organization: 'Orbital Labs',
    initials: 'OL', accent: 'violet', date: '2026-11-02', start: '14:00', end: '14:30',
    location: 'Google Meet', kind: 'Acompanhamento',
    description: 'Conversa de acompanhamento do processo e dúvidas sobre a oportunidade. Cenário fictício para demonstração.',
  },
];

const REMINDERS = [
  { id: 'demo-reminder-1', eventId: 'demo-aurora-rh', dueDate: '2026-10-27', dueTime: '08:30', offsetMinutes: 60, format: 'card', state: 'accepted', reason: 'Envio aceito pelo provedor. A aceitação não confirma entrega ou leitura.' },
  { id: 'demo-reminder-2', eventId: 'demo-aurora-rh', dueDate: '2026-10-27', dueTime: '09:15', offsetMinutes: 15, format: 'text', state: 'accepted', reason: 'Envio aceito pelo provedor. A aceitação não confirma entrega ou leitura.' },
  { id: 'demo-reminder-3', eventId: 'demo-orbital-tecnica', dueDate: '2026-10-27', dueTime: '13:30', offsetMinutes: 60, format: 'card', state: 'pending', reason: 'O card será preparado no horário do aviso.' },
  { id: 'demo-reminder-4', eventId: 'demo-orbital-tecnica', dueDate: '2026-10-27', dueTime: '14:15', offsetMinutes: 15, format: 'text', state: 'pending', reason: 'Aguardando o horário do aviso.' },
  { id: 'demo-reminder-5', eventId: 'demo-nuvem-produto', dueDate: '2026-10-27', dueTime: '16:00', offsetMinutes: 60, format: 'card', state: 'blocked', reason: 'A janela de conversa do canal precisa ser conferida antes do envio.' },
  { id: 'demo-reminder-6', eventId: 'demo-vertice-portfolio', dueDate: '2026-10-28', dueTime: '09:00', offsetMinutes: 60, format: 'card', state: 'pending', reason: 'Aguardando o horário do aviso.' },
  { id: 'demo-reminder-7', eventId: 'demo-aurora-feedback', dueDate: '2026-10-28', dueTime: '15:45', offsetMinutes: 15, format: 'text', state: 'pending', reason: 'Aguardando o horário do aviso.' },
  { id: 'demo-reminder-8', eventId: 'demo-orbital-proximos', dueDate: '2026-11-02', dueTime: '12:00', offsetMinutes: 60, format: 'card', state: 'cancelled', reason: 'Aviso anterior cancelado após o reagendamento do compromisso fictício.' },
  { id: 'demo-reminder-9', eventId: 'demo-ponto-trajetoria', dueDate: '2026-10-26', dueTime: '15:30', offsetMinutes: 60, format: 'card', state: 'uncertain', reason: 'Resultado simulado sem confirmação. Repetir um envio nessas condições poderia duplicar a mensagem.' },
  { id: 'demo-reminder-10', eventId: 'demo-ponto-trajetoria', dueDate: '2026-10-26', dueTime: '16:15', offsetMinutes: 15, format: 'text', state: 'expired', reason: 'O prazo deste aviso fictício terminou. Nenhuma mensagem será enviada.' },
];

const CHECKLIST = [
  { id: 'demo-check-1', label: 'Revisar o contexto e a descrição da entrevista', checked: true },
  { id: 'demo-check-2', label: 'Separar um projeto para apresentar', checked: true },
  { id: 'demo-check-3', label: 'Preparar exemplos de decisões e aprendizados', checked: false },
  { id: 'demo-check-4', label: 'Anotar perguntas para a equipe', checked: false },
  { id: 'demo-check-5', label: 'Testar câmera, microfone e conexão', checked: false },
  { id: 'demo-check-6', label: 'Conferir o horário e o acesso à chamada', checked: false },
];

/** Each call returns independent objects; changes disappear on page reload. */
export function initialData() {
  const events = EVENTS.map(item => ({ ...item }));
  const reminders = REMINDERS.map(item => {
    const eventSnapshot = { ...events.find(event => event.id === item.eventId) };
    if (item.id === 'demo-reminder-8') Object.assign(eventSnapshot, { start: '13:00', end: '13:30' });
    const due = new Date(`${item.dueDate}T${item.dueTime}:00Z`);
    const at = new Date(Math.min(due.getTime(), new Date(`${DEMO_TODAY}T${DEMO_TIME}:00Z`).getTime()));
    const stamp = at.toISOString();
    const registered = new Date(at.getTime() - 60 * 60000).toISOString();
    const history = [{ state: 'pending', date: registered.slice(0, 10), time: registered.slice(11, 16), reason: 'Aviso registrado neste cenário fictício.' }];
    if (['accepted', 'uncertain'].includes(item.state)) {
      history.push({ state: 'reserved', date: stamp.slice(0, 10), time: stamp.slice(11, 16), reason: 'Tentativa fictícia reservada.' });
      history.push({ state: 'sending', date: stamp.slice(0, 10), time: stamp.slice(11, 16), reason: 'Início simulado da tentativa de envio.' });
    }
    if (item.state !== 'pending') history.push({ state: item.state, date: stamp.slice(0, 10), time: stamp.slice(11, 16), reason: item.reason });
    return { ...item, eventSnapshot, history };
  });
  return { events, reminders, checklist: CHECKLIST.map(item => ({ ...item })) };
}

function utcDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new RangeError('Informe uma data válida.');
  const parsed = new Date(`${value}T12:00:00.000Z`);
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) throw new RangeError('Informe uma data válida.');
  return parsed;
}

/** ISO civil dates; all arithmetic is UTC and independent from host timezone. */
export function addDays(date, number) {
  if (!Number.isInteger(number)) throw new RangeError('Informe um número inteiro de dias.');
  const current = utcDate(date);
  current.setUTCDate(current.getUTCDate() + number);
  return current.toISOString().slice(0, 10);
}

/** Seven ISO dates, Monday through Sunday. */
export function weekDates(date) {
  const weekday = utcDate(date).getUTCDay();
  const monday = addDays(date, -((weekday + 6) % 7));
  return Array.from({ length: 7 }, (_, index) => addDays(monday, index));
}

/** Six rows (42 ISO dates), including the prior/next month's padding days. */
export function monthDays(date) {
  utcDate(date);
  const first = `${date.slice(0, 7)}-01`;
  const monday = weekDates(first)[0];
  return Array.from({ length: 42 }, (_, index) => addDays(monday, index));
}

export function formatDate(date, { short = false } = {}) {
  const options = short ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'long', year: 'numeric' };
  return new Intl.DateTimeFormat('pt-BR', { ...options, timeZone: 'UTC' }).format(utcDate(date)).replace(/ de /g, short ? ' ' : ' de ').replace(/\./g, '');
}

function minuteOfDay(value) {
  if (typeof value !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) throw new RangeError('Informe um horário válido.');
  const [hour, minute] = value.split(':').map(Number);
  return hour * 60 + minute;
}

function clockTime(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

export function humanTime(value) {
  minuteOfDay(value);
  const [hour, minute] = value.split(':');
  return `${Number(hour)}h${minute === '00' ? '' : minute}`;
}

/** Adjacent intervals are allowed. The supplied event must have date/start/end. */
export function eventConflicts(events, event, excludeId = null) {
  utcDate(event.date);
  const start = minuteOfDay(event.start);
  const end = minuteOfDay(event.end);
  if (end <= start) throw new RangeError('O término deve ser depois do início.');
  return events.filter(item => item.id !== excludeId && item.date === event.date && minuteOfDay(item.start) < end && start < minuteOfDay(item.end));
}

/** At most three fictional alternatives; this never creates a reservation. */
export function suggestSlots(events, { date, start, end, duration }) {
  utcDate(date);
  const first = minuteOfDay(start);
  const last = minuteOfDay(end);
  if (!Number.isInteger(duration) || duration < 1 || duration > 1440 || first >= last) throw new RangeError('Confira a duração e a faixa de horário.');
  const slots = [];
  for (let cursor = first; cursor + duration <= last && slots.length < 3; cursor += 15) {
    const option = { date, start: clockTime(cursor), end: clockTime(cursor + duration) };
    const overlapsPrevious = slots.some(item => minuteOfDay(item.start) < cursor + duration && cursor < minuteOfDay(item.end));
    if (!overlapsPrevious && eventConflicts(events, option).length === 0) slots.push(option);
  }
  return slots;
}
