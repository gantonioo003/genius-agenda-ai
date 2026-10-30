import { DEMO_TODAY, DEMO_TIME, initialData, addDays, weekDates, monthDays, formatDate, humanTime, suggestSlots, eventConflicts } from './demo.js';

const paths = {
  home:'m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',
  calendar:'M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2m3 10h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01',
  sparkles:'m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6ZM20 2v4m-2-2h4',
  bell:'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 13h4',
  arrow:'M5 12h14m-5-5 5 5-5 5', chevron:'m9 5 7 7-7 7', back:'m15 5-7 7 7 7',
  plus:'M12 5v14M5 12h14', x:'m6 6 12 12M6 18 18 6', clock:'M12 8v4l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0m-5 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  video:'m16 8 6-4v16l-6-4M3 5h11a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2',
  check:'m5 12 4 4L19 6', search:'m21 21-4.5-4.5M19 10a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  edit:'m16 3 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14Z', trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  image:'M3 3h18v18H3ZM3 17l6-6 5 5 3-3 4 4M9 7h.01', info:'M12 11v6m0-10h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  alert:'m12 3 10 18H2Zm0 6v4m0 4h.01', refresh:'M3 11a9 9 0 0 1 16-6l2 3m0-6v6h-6M21 13a9 9 0 0 1-16 6l-2-3m0 6v-6h6',
  list:'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01', external:'M14 3h7v7m0-7L10 14M10 3H3v18h18v-7',
  message:'M21 11a9 9 0 0 1-9 9H5l-4 3V11a10 10 0 0 1 20 0M6 10h10M6 14h7', dots:'M5 12h.01M12 12h.01M19 12h.01'
};
const icon = (name, cls='') => `<svg class="icon ${cls}" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.calendar}"/></svg>`;
const e = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const $ = (selector, root=document) => root.querySelector(selector);
const routes = [{id:'visao-geral',label:'Visão geral',icon:'home'},{id:'agenda',label:'Agenda',icon:'calendar'},{id:'preparo',label:'Preparo',icon:'sparkles'},{id:'lembretes',label:'Lembretes',icon:'bell'}];
function demoData(){return initialData();}
const data = demoData();
const state = {route:readRoute(),date:DEMO_TODAY,view:'day',search:'',prepId:data.events.find(v=>v.date===DEMO_TODAY && v.start>'12:00')?.id || data.events[0].id,reminderFilter:'upcoming',scenario:'normal',summary:'week',slots:null,summaryReady:false};
let pending = null;
let toastTimeout;
let savedFooter = '';
const positions = {};
const sheet = $('#sheet');
const page = $('#main-content');
const getEvent = id => data.events.find(v=>v.id===id);
const sortEvents = events => [...events].sort((a,b)=>(a.date+a.start).localeCompare(b.date+b.start));
const dayEvents = date => sortEvents(data.events.filter(v=>v.date===date));
const checklists = new Map();
function selectedChecklist(){if(!checklists.has(state.prepId))checklists.set(state.prepId,data.checklist.map(value=>({...value})));return checklists.get(state.prepId);}
const shortDay = date => new Intl.DateTimeFormat('pt-BR',{weekday:'short',timeZone:'UTC'}).format(new Date(date+'T12:00:00Z')).replace('.','');
const monthLabel = date => new Intl.DateTimeFormat('pt-BR',{month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(date+'T12:00:00Z'));
const dateLabel = date => date===DEMO_TODAY?'Hoje':date===addDays(DEMO_TODAY,1)?'Amanhã':formatDate(date,{short:true});
const interval = event => `${humanTime(event.start)} às ${humanTime(event.end)}`;
const button = (label,action,extra='',kind='secondary') => `<button class="button ${kind}" data-action="${action}" ${extra}>${label}</button>`;
function readRoute(){const value=location.hash.slice(1);return routes.some(v=>v.id===value)?value:'visao-geral';}
function installIcons(root=document){root.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});}
function eventAvatar(event){return `<span class="event-avatar ${e(event.accent)}" aria-hidden="true">${e(event.initials)}</span>`;}
function nav(){
  $('#navigation').innerHTML=routes.map(route=>`<a class="nav-item ${state.route===route.id?'is-active':''}" href="#${route.id}" aria-label="${route.label}" ${state.route===route.id?'aria-current="page"':''}>${icon(route.icon)}<span class="nav-label">${route.label}</span>${state.route===route.id?'<span class="nav-active-dot" aria-hidden="true"></span>':''}</a>`).join('');
  $('#breadcrumb').textContent=routes.find(v=>v.id===state.route).label;
  document.title=`${routes.find(v=>v.id===state.route).label} · Demonstração · Genius Agenda AI`;
}
function go(route,options={}){
  positions[state.route]=window.scrollY;
  Object.assign(state,options);
  if(state.route===route){render();return;}
  location.hash=route;
}
window.addEventListener('hashchange',()=>{
  positions[state.route]=window.scrollY;
  const update=()=>{state.route=readRoute();render();page.focus({preventScroll:true});window.scrollTo({top:positions[state.route]||0,behavior:'instant'});};
  update();
  if(!matchMedia('(prefers-reduced-motion: reduce)').matches)page.animate?.([{opacity:.4,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:200,easing:'cubic-bezier(.22,1,.36,1)'});
});
function header(eyebrow,title,subtitle,actions=''){
  return `<div class="page-header"><div><p class="eyebrow">${eyebrow}</p><h1 class="page-title">${title}</h1>${subtitle?`<p class="page-subtitle">${subtitle}</p>`:''}</div>${actions?`<div class="header-actions">${actions}</div>`:''}</div>`;
}
function newButton(){return button(`${icon('plus')}<span>Novo compromisso</span>`,'new-event','aria-label="Novo compromisso"','primary');}
function guideCard(){return `<section class="demo-guide" aria-label="Explorar demonstração"><div><span class="eyebrow">SEU PRIMEIRO PASSEIO</span><h2>Experimente sem receio.</h2><p>Crie um exemplo, confira seu preparo e veja como seriam os avisos.</p></div><div class="demo-guide-actions">${button(`${icon('calendar')} <span class="desktop-only">Explorar a agenda</span><span class="mobile-only">Agenda</span>`,'demo-guide-agenda','aria-label="Explorar a agenda"','secondary')}${button(`${icon('sparkles')} <span class="desktop-only">Preparar entrevista de exemplo</span><span class="mobile-only">Preparo</span>`,'demo-guide-preparation','aria-label="Preparar entrevista de exemplo"','secondary')}${button(`${icon('image')} <span class="desktop-only">Ver um card de exemplo</span><span class="mobile-only">Card</span>`,'demo-guide-card','aria-label="Ver um card de exemplo"','secondary')}</div></section>`;}
function eventRow(event){
  return `<button class="event-row" data-action="event" data-id="${e(event.id)}" aria-label="Abrir ${e(event.title)}, ${dateLabel(event.date)}, ${interval(event)}"><span class="event-time"><strong>${humanTime(event.start)}</strong><span>${humanTime(event.end)}</span></span>${eventAvatar(event)}<span class="event-info"><span class="event-title">${e(event.title)}</span><span class="event-meta">${e(event.organization)} <span aria-hidden="true">·</span> ${e(event.kind)}</span></span>${icon('chevron','row-arrow')}</button>`;
}
function weekStrip(){return `<div class="week-strip" aria-label="Escolher dia da semana">${weekDates(state.date).map(date=>`<button class="day-chip ${date===state.date?'is-selected':''} ${dayEvents(date).length?'has-events':''}" data-action="select-day" data-date="${date}" aria-pressed="${date===state.date}" aria-label="${shortDay(date)}, ${formatDate(date)}${dayEvents(date).length?`, ${dayEvents(date).length} ${dayEvents(date).length===1?'compromisso':'compromissos'}`:''}"><span>${shortDay(date)}</span><strong>${Number(date.slice(8))}</strong><i aria-hidden="true"></i></button>`).join('')}</div>`;}
function overview(){
  const events=dayEvents(state.date);
  const next=sortEvents(data.events).find(v=>v.date===DEMO_TODAY && v.start>DEMO_TIME) || sortEvents(data.events).find(v=>v.date>DEMO_TODAY);
  const attention=data.reminders.filter(v=>['blocked','uncertain','expired'].includes(v.state));
  const nextReminder=data.reminders.find(v=>v.state==='pending');
  return header('SEU ESPAÇO, NO SEU RITMO','Seu dia, com clareza.','Mais presença nas conversas. Menos preocupação com a agenda.',newButton())+guideCard()+
  `<div class="overview-grid"><div class="content-stack">
    ${next?`<article class="hero-card"><div class="hero-content"><span class="hero-eyebrow"><span class="status-dot"></span> SUA PRÓXIMA ENTREVISTA</span><div class="hero-time">${humanTime(next.start)}</div><h2 class="hero-title">${e(next.title)}</h2><p class="hero-meta">${e(next.organization)} <span>·</span> ${dateLabel(next.date)}, ${interval(next)}</p><div class="hero-actions">${button(`Preparar entrevista ${icon('arrow')}`,'prepare',`data-id="${e(next.id)}"`,'primary')}${button(icon('dots'),'event',`data-id="${e(next.id)}" aria-label="Ver detalhes da próxima entrevista"`,'ghost')}</div></div><div class="hero-visual"><img src="assets/interview-background.png" alt="Mesa iluminada com notebook e plantas, fundo do card de entrevista"><span class="image-label">Seu próximo passo começa aqui.</span></div></article>`:empty('Seu próximo passo está em aberto.','Crie um compromisso para começar.','new-event','Criar compromisso')}
    <section><div class="section-heading"><div><h2>Sua semana</h2><p>${monthLabel(state.date)}</p></div><button class="text-button" data-action="week-agenda">Ver agenda ${icon('arrow')}</button></div>${weekStrip()}</section>
    <section><div class="section-heading"><h2>${dateLabel(state.date)} <span class="count-badge">${events.length}</span></h2><span class="quiet-note">${formatDate(state.date,{short:true})}</span></div><div class="event-list">${events.length?events.map(eventRow).join(''):empty('Um respiro na agenda.','Nenhum compromisso neste dia.','new-event','Agendar algo')}</div></section>
  </div><aside class="aside-panel">
    <section class="mini-card"><div class="mini-card-heading"><span class="eyebrow">UM PASSO À FRENTE</span>${icon('sparkles')}</div><h2>Entre na conversa<br>com confiança.</h2><p>Os detalhes e o preparo da sua próxima entrevista, juntos em um lugar.</p>${button(`Ver meu preparo ${icon('arrow')}`,'prepare',next?`data-id="${e(next.id)}"`:'','secondary')}<div class="prep-preview"><span class="check-circle">${icon('check')}</span><span>Revise suas experiências</span><span class="check-circle">${icon('check')}</span><span>Confira o local e o horário</span></div></section>
    <section class="mini-card reminder-preview"><div class="section-heading"><h2>Lembretes</h2><span class="tag neutral">1h + 15min</span></div>${nextReminder?`<div class="reminder-mini"><span class="reminder-icon">${icon('image')}</span><div><strong>Um card, na hora certa.</strong><p>${dateLabel(nextReminder.dueDate)}, ${humanTime(nextReminder.dueTime)}</p></div></div>`:''}<p class="quiet-note">Depois, um aviso em texto quando estiver perto de começar.</p><button class="text-button" data-action="notifications">Acompanhar avisos ${icon('arrow')}</button></section>
    ${attention.length?`<button class="attention-note" data-action="attention">${icon('info')}<span><strong>${attention.length} ${attention.length===1?'aviso precisa':'avisos precisam'} de atenção</strong><small>Veja o que aconteceu</small></span>${icon('chevron')}</button>`:''}
    <div class="small-signature">Menos ruído.<br><strong>Mais espaço para você.</strong></div>
  </aside></div>`;
}
function agenda(){
  let events=dayEvents(state.date);
  if(state.view==='week'){const days=weekDates(state.date);events=sortEvents(data.events.filter(v=>days.includes(v.date)));}
  if(state.view==='month')events=sortEvents(data.events.filter(v=>v.date.slice(0,7)===state.date.slice(0,7)));
  const filtered=events.filter(v=>(v.title+' '+v.organization).normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase().includes(state.search.normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase()));
  return header('CADA CONVERSA TEM SEU LUGAR','Sua agenda.','O que vem pela frente, no seu ritmo.',newButton())+
    `<section class="agenda-surface"><div class="toolbar"><div class="period-controls"><button class="icon-button" data-action="previous" aria-label="Período anterior">${icon('back')}</button><h2>${state.view==='month'?monthLabel(state.date):state.view==='week'?`Semana de ${formatDate(weekDates(state.date)[0],{short:true})}`:dateLabel(state.date)}</h2><button class="icon-button" data-action="next" aria-label="Próximo período">${icon('chevron')}</button><button class="button ghost small" data-action="today">Hoje</button></div><div class="segmented" aria-label="Visualização da agenda">${[['day','Dia'],['week','Semana'],['month','Mês']].map(([value,label])=>`<button class="${state.view===value?'is-active':''}" data-action="agenda-view" data-value="${value}" aria-pressed="${state.view===value}">${label}</button>`).join('')}</div></div>
    <label class="search-field">${icon('search')}<span class="screen-reader-only">Pesquisar neste período</span><input id="agenda-search" type="search" placeholder="Pesquisar neste período" value="${e(state.search)}" autocomplete="off"></label>
    ${state.view==='day'?weekStrip():''}
    <div id="agenda-results">${state.view==='month'?monthCalendar(filtered):state.view==='week'?weekCalendar(filtered):`<div class="section-heading agenda-list-heading"><span class="eyebrow">${formatDate(state.date)}</span><span class="quiet-note">${filtered.length} ${filtered.length===1?'compromisso':'compromissos'}</span></div><div class="event-list">${filtered.length?filtered.map(eventRow).join(''):empty('Espaço para novos planos.',state.search?'Nenhum título corresponde à sua pesquisa.':'Você não tem compromissos neste dia.','new-event','Novo compromisso')}</div>`}</div></section>`;
}
function monthCalendar(events){const days=monthDays(state.date);return `<div class="calendar-weekdays">${['SEG','TER','QUA','QUI','SEX','SÁB','DOM'].map(v=>`<span>${v}</span>`).join('')}</div><div class="month-grid">${days.map(date=>{const current=events.filter(v=>v.date===date);return `<div class="month-cell ${date.slice(0,7)!==state.date.slice(0,7)?'is-outside':''} ${date===DEMO_TODAY?'is-today':''} ${date===state.date?'is-selected':''}"><button class="month-day" data-action="month-day" data-date="${date}" aria-label="Ver dia ${formatDate(date)}">${Number(date.slice(8))}</button>${current.map(v=>`<button class="calendar-event accent-${e(v.accent)}" data-action="event" data-id="${e(v.id)}"><strong>${humanTime(v.start)}</strong><span>${e(v.title)}</span></button>`).join('')}</div>`;}).join('')}</div><p class="quiet-note mobile-only calendar-hint">Toque em um dia para ver os compromissos.</p>`;}
function weekCalendar(events){return `<div class="week-grid">${weekDates(state.date).map(date=>`<section class="week-column ${date===DEMO_TODAY?'is-today':''}"><div class="week-column-header"><span>${shortDay(date)}</span><strong>${Number(date.slice(8))}</strong></div>${events.filter(v=>v.date===date).map(v=>`<button class="week-event accent-${e(v.accent)}" data-action="event" data-id="${e(v.id)}"><span>${humanTime(v.start)} — ${humanTime(v.end)}</span><strong>${e(v.title)}</strong><small>${e(v.organization)}</small></button>`).join('')||'<span class="week-free">Livre</span>'}</section>`).join('')}</div>`;}
function preparation(){
  const event=getEvent(state.prepId) || sortEvents(data.events)[0];
  if(!event)return header('UM PASSO À FRENTE','Prepare seu próximo passo.','O essencial para chegar com confiança.')+empty('Qual é a próxima conversa?','Crie um compromisso e reúna seu preparo aqui.','new-event','Criar compromisso');
  state.prepId=event.id;
  const completed=selectedChecklist().filter(v=>v.checked).length;
  return header('UM PASSO À FRENTE','Chegue com confiança.','Tudo o que você precisa lembrar, em um lugar.')+
  `<label class="interview-selector">${eventAvatar(event)}<span><span class="eyebrow">PREPARAR PARA</span><select id="prep-selector" aria-label="Escolher entrevista para preparar">${sortEvents(data.events).map(v=>`<option value="${e(v.id)}" ${v.id===event.id?'selected':''}>${e(v.title)} · ${dateLabel(v.date)}</option>`).join('')}</select></span>${icon('chevron')}</label>
  <div class="prep-grid"><div class="content-stack"><section class="briefing-card"><div class="section-heading"><h2>Sobre a entrevista</h2><span class="tag violet">${e(event.kind)}</span></div><h3>${e(event.title)}</h3><div class="briefing-facts"><div class="fact-item">${icon('calendar')}<span>${dateLabel(event.date)}, ${formatDate(event.date,{short:true})}</span></div><div class="fact-item">${icon('clock')}<span>${interval(event)}</span></div><div class="fact-item">${icon('pin')}<span>${e(event.location||'Local ainda não informado')}</span></div></div><p class="briefing-description">${e(event.description||'Adicione uma descrição para reunir mais contexto sobre a conversa.')}</p><button class="text-button" data-action="event" data-id="${e(event.id)}">Ver compromisso ${icon('arrow')}</button></section>
  <section class="checklist-card"><div class="section-heading"><div><h2>Seu checklist</h2><p>Pequenos passos. Mais tranquilidade.</p></div><span class="tag mint" id="checklist-count">${completed} de ${data.checklist.length}</span></div><div class="progress-track" aria-hidden="true"><span id="checklist-progress"></span></div><div class="checklist">${selectedChecklist().map(v=>`<label class="check-item"><input type="checkbox" data-check="${e(v.id)}" ${v.checked?'checked':''}><span>${e(v.label)}</span>${icon('check','check-icon')}</label>`).join('')}</div><p class="quiet-note">Sugestões gerais de preparo. Marcações temporárias nesta prévia.</p></section></div>
  <aside class="content-stack"><section class="mini-card"><div class="mini-card-heading"><span class="eyebrow">OLHE O TODO</span>${icon('sparkles')}</div><h2>Sua agenda,<br>em poucas palavras.</h2><div class="segmented summary-tabs" aria-label="Período do resumo"><button data-action="summary-period" data-value="week" class="${state.summary==='week'?'is-active':''}" aria-pressed="${state.summary==='week'}">Semana</button><button data-action="summary-period" data-value="month" class="${state.summary==='month'?'is-active':''}" aria-pressed="${state.summary==='month'}">Mês</button></div><div class="summary-result">${state.summaryReady?summaryText():`<p>Um panorama dos compromissos para organizar seu tempo com calma.</p>`}</div>${button(state.summaryReady?'Atualizar exemplo':'Ver resumo de exemplo','summary','','secondary')}<p class="quiet-note">Texto demonstrativo, sem chamada de IA.</p></section>
  <section class="mini-card"><div class="mini-card-heading"><span class="eyebrow">ENCONTRE UM RESPIRO</span>${icon('clock')}</div><h2>Qual horário<br>funciona para você?</h2><form id="slots-form" class="slots-form"><label class="field">Dia<input class="input" type="date" name="date" value="${addDays(DEMO_TODAY,1)}" required></label><div class="field-row"><label class="field">Das<input class="input" type="time" name="start" value="09:00" required></label><label class="field">Até<input class="input" type="time" name="end" value="12:00" required></label></div><label class="field">Duração<select class="select" name="duration"><option value="30">30 minutos</option><option value="45">45 minutos</option><option value="60">1 hora</option></select></label><button class="button secondary" type="submit">Buscar opções ${icon('arrow')}</button><p class="form-error" id="slots-error" role="alert"></p></form><div id="slot-results">${renderSlots()}</div></section></aside></div>`;
}
function summaryText(){const days=state.summary==='week'?weekDates(DEMO_TODAY):null;const events=sortEvents(data.events.filter(v=>days?days.includes(v.date):v.date.slice(0,7)===DEMO_TODAY.slice(0,7)));const occupied=new Set(events.map(v=>v.date)).size;return `<div class="summary-number">${events.length}<span>compromissos neste ${state.summary==='week'?'período da semana':'mês'}</span></div><p>${events.length?`Distribuídos em ${occupied} ${occupied===1?'dia':'dias'}. ${events.filter(v=>v.date>=DEMO_TODAY).length} a partir de hoje, no cenário de exemplo.`:'Um período livre para organizar seus próximos passos.'}</p><span class="tag neutral">Resumo calculado dos exemplos · sem IA</span>`;}
function renderSlots(){if(!state.slots)return '';return `<div class="slot-results"><p class="quiet-note">${state.slots.length?'Opções fictícias. Nenhuma reserva criada.':'Nenhuma opção disponível nessa faixa.'}</p>${state.slots.map((v,i)=>`<button class="slot-option" data-action="choose-slot" data-index="${i}"><span><strong>${dateLabel(v.date)}</strong><small>${humanTime(v.start)} às ${humanTime(v.end)}</small></span>${icon('arrow')}</button>`).join('')}</div>`;}
const statusMap={pending:['Pendente','violet'],reserved:['Preparando','violet'],sending:['Tentativa em andamento','violet'],accepted:['Aceito pelo provedor','mint'],blocked:['Bloqueado','amber'],uncertain:['Resultado incerto','amber'],cancelled:['Cancelado','neutral'],expired:['Prazo encerrado','neutral']};
function reminders(){
  const filters=[['upcoming','Pendentes'],['accepted','Envios aceitos'],['attention','Atenção'],['all','Todo o histórico']];
  const entries=data.reminders.filter(v=>state.reminderFilter==='all'||(state.reminderFilter==='upcoming'&&['pending','reserved','sending'].includes(v.state))||(state.reminderFilter==='accepted'&&v.state==='accepted')||(state.reminderFilter==='attention'&&['blocked','uncertain','expired'].includes(v.state)));
  return header('NO TEMPO CERTO','Cada aviso, com clareza.','Explore o próximo aviso e os exemplos de histórico.')+
  `<div class="reminder-rule"><div class="rule-icon">${icon('bell')}</div><div><strong>Um cuidado antes de cada conversa.</strong><p>Um card 1 hora antes. Um texto quando faltarem 15 minutos.</p></div><span class="tag neutral desktop-only">Regra demonstrada</span></div>
  <div class="reminder-filters segmented" aria-label="Filtrar lembretes">${filters.map(([value,label])=>`<button class="${state.reminderFilter===value?'is-active':''}" data-action="reminder-filter" data-value="${value}" aria-pressed="${state.reminderFilter===value}">${label}${value==='attention'?` <span class="filter-count">${data.reminders.filter(v=>['blocked','uncertain','expired'].includes(v.state)).length}</span>`:''}</button>`).join('')}</div>
  <p class="demo-filter-count">${entries.length} ${entries.length===1?'aviso neste filtro':'avisos neste filtro'} · todos fictícios</p><div class="reminder-table"><div class="reminder-table-header"><span>COMPROMISSO</span><span>AVISO PREVISTO</span><span>FORMATO</span><span>SITUAÇÃO</span></div>${entries.length?entries.map(v=>{const ev=v.eventSnapshot||getEvent(v.eventId);const [label,color]=statusMap[v.state];return `<button class="reminder-row" data-action="reminder" data-id="${e(v.id)}" aria-label="Acompanhar ${e(ev?.title||'aviso')}: ${e(label)}"><span class="reminder-event">${ev?eventAvatar(ev):icon('calendar')}<span><strong>${e(ev?.title || 'Compromisso anterior')}</strong><small>${ev?`${dateLabel(ev.date)}, ${humanTime(ev.start)}`:'Registro de exemplo'}</small></span></span><span class="reminder-due"><strong>${dateLabel(v.dueDate)}</strong><small>${humanTime(v.dueTime)}<span class="mobile-only"> · ${v.format==='card'?'Card':'Texto'}</span></small></span><span class="format-pill">${icon(v.format==='card'?'image':'message')} ${v.format==='card'?'Card':'Texto'}</span><span class="tag ${color}">${label}</span>${icon('chevron','row-arrow')}</button>`;}).join(''):empty('Tudo tranquilo por aqui.','Nenhum aviso neste filtro.','all-reminders','Ver histórico')}</div><p class="quiet-note reminder-note">${icon('info')} Todos os estados são exemplos. “Aceito pelo provedor” não comprova entrega ou leitura. Esta demonstração não envia mensagens.</p>`;
}
function empty(title,description,action,label){return `<div class="empty-state"><span class="empty-icon">${icon('calendar')}</span><h3>${title}</h3><p>${description}</p>${action?button(label,action,'','secondary'):''}</div>`;}
function scenario(){
  const title=routes.find(v=>v.id===state.route).label;
  if(state.scenario==='loading')return header('EXEMPLO DE CARREGAMENTO',title,'Buscando os próximos passos…')+`<div class="loading-state" role="status"><span class="screen-reader-only">Carregando conteúdo demonstrativo</span><div class="skeleton skeleton-hero"></div><div class="skeleton skeleton-line"></div>${[1,2,3].map(()=>'<div class="skeleton skeleton-row"></div>').join('')}<button class="button ghost" data-action="restore-view">Voltar ao conteúdo</button></div>`;
  if(state.scenario==='error')return header('EXEMPLO DE FALHA',title,'')+`<div class="error-state">${icon('alert')}<h2>Não foi possível atualizar.</h2><p>Sua agenda continua preservada. Este é um exemplo visual de falha de leitura.</p>${button(`${icon('refresh')} Tentar novamente`,'restore-view','','secondary')}</div>`;
  return header('EXEMPLO SEM RESULTADOS',title,'')+empty('Um espaço para novos planos.','Este é um exemplo de como a tela aparece quando não há resultados.','restore-view','Ver exemplos');
}
function render(){
  const active=document.activeElement;
  let focusSelector=null;
  if(page.contains(active)){
    if(active.id)focusSelector='#'+CSS.escape(active.id);
    else if(active.dataset.action)focusSelector='[data-action="'+CSS.escape(active.dataset.action)+'"]'+['value','date','id'].filter(key=>active.dataset[key]).map(key=>'[data-'+key+'="'+CSS.escape(active.dataset[key])+'"]').join('');
  }
  nav();page.innerHTML=state.scenario!=='normal'?scenario():state.route==='visao-geral'?overview():state.route==='agenda'?agenda():state.route==='preparo'?preparation():reminders();
  if($('#checklist-progress'))$('#checklist-progress').style.width=`${selectedChecklist().filter(v=>v.checked).length/data.checklist.length*100}%`;
  if(focusSelector)($(focusSelector,page)||page).focus({preventScroll:true});
}
function toast(message){clearTimeout(toastTimeout);$('#toast').textContent=message;$('#toast').classList.add('show');toastTimeout=setTimeout(()=>$('#toast').classList.remove('show'),4200);}
function openSheet(kicker,body,footer=''){
  $('#sheet-kicker').textContent=kicker;$('#sheet-content').innerHTML=body;$('#sheet-footer').innerHTML=footer;sheet.dataset.dirty='false';savedFooter=footer;
  if(!sheet.open)sheet.showModal();
  $('#sheet-content').scrollTop=0;
  requestAnimationFrame(()=>$('#sheet-title')?.focus({preventScroll:true}));
}
function closeSheet(force=false){
  if(!force && sheet.dataset.dirty==='true'){$('#sheet-footer').innerHTML=`<p class="discard-note" role="alert">Descartar as alterações desta prévia?</p>${button('Continuar editando','keep-editing','','secondary')}${button('Descartar','discard','','danger')}`;return;}
  pending=null;sheet.close();
}
sheet.addEventListener('cancel',event=>{event.preventDefault();closeSheet();});
sheet.addEventListener('click',event=>{if(event.target===sheet){const r=sheet.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeSheet();}});
$('#info').addEventListener('click',event=>{if(event.target===$('#info')){const r=$('#info').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)$('#info').close();}});
function eventDetail(id){
  const ev=getEvent(id);if(!ev)return toast('Esse compromisso não está mais nesta prévia.');
  const remindersFor=data.reminders.filter(v=>v.eventId===id&&['pending','accepted'].includes(v.state));
  openSheet('SEU COMPROMISSO',`${eventAvatar(ev)}<h2 class="detail-title" id="sheet-title" tabindex="-1">${e(ev.title)}</h2><p class="detail-meta">${e(ev.organization)} <span>·</span> ${e(ev.kind)}</p><div class="detail-facts"><div class="fact-item">${icon('calendar')}<div><strong>${dateLabel(ev.date)}</strong><span>${formatDate(ev.date)}</span></div></div><div class="fact-item">${icon('clock')}<span>${interval(ev)}</span></div><div class="fact-item">${icon('pin')}<span>${e(ev.location || 'Local não informado')}</span></div></div><section class="detail-section"><h3>Sobre a conversa</h3><p>${e(ev.description || 'Nenhuma descrição informada.')}</p></section><section class="detail-section"><h3>Antes de começar</h3><p>Card 1 hora antes e aviso em texto com 15 minutos.</p><span class="tag neutral">${remindersFor.length} registros de exemplo</span></section><div class="detail-actions">${button(`${icon('edit')} Editar`,'edit-event',`data-id="${e(id)}"`,'secondary')}${button(`${icon('calendar')} Reagendar`,'reschedule',`data-id="${e(id)}"`,'secondary')}${button(`${icon('trash')} Excluir`,'delete-preview',`data-id="${e(id)}"`,'danger')}</div>`,button(`Preparar entrevista ${icon('arrow')}`,'prepare',`data-id="${e(id)}"`,'primary'));
}
function editForm(id=null,mode='create',slot=null){
  const ev=getEvent(id);
  if(mode!=='create'&&!ev)return toast('Selecione um compromisso.');
  const values={title:slot?.title??ev?.title??'',date:slot?.date||ev?.date||state.date,start:slot?.start||ev?.start||'14:00',end:slot?.end||ev?.end||'14:30',location:slot?.location??ev?.location??'',description:slot?.description??ev?.description??''};
  const title=mode==='create'?'Um novo encontro.':mode==='reschedule'?'Um horário melhor.':'Ajuste os detalhes.';
  const fields=mode==='reschedule'?`<div class="current-event"><strong>${e(ev.title)}</strong><p>Atual: ${formatDate(ev.date,{short:true})}, ${interval(ev)}</p></div>`:`<label class="field">Título<input class="input" name="title" value="${e(values.title)}" placeholder="Ex.: Entrevista técnica" maxlength="160" required autocomplete="off"></label>`;
  const timeFields=mode==='edit'?'':`<label class="field">Data<input class="input" type="date" name="date" value="${values.date}" required></label><div class="field-row"><label class="field">Início<input class="input" type="time" name="start" value="${values.start}" required></label><label class="field">Término<input class="input" type="time" name="end" value="${values.end}" required></label></div>`;
  const details=mode==='reschedule'?'':`<label class="field">Local ou link <span class="optional">opcional</span><input class="input" name="location" value="${e(values.location)}" maxlength="240" placeholder="Onde será a conversa?"></label><label class="field">Descrição <span class="optional">opcional</span><textarea class="textarea" name="description" rows="4" maxlength="1200" placeholder="O que vale lembrar?">${e(values.description)}</textarea></label>`;
  openSheet('SIMULAÇÃO · DADOS FICTÍCIOS',`<h2 class="detail-title" id="sheet-title" tabindex="-1">${title}</h2><p class="page-subtitle">Confira os dados antes de continuar.</p><form id="event-form" data-id="${e(id||'')}" data-mode="${mode}" class="event-form">${fields}${timeFields}${details}<p id="event-form-error" class="form-error" role="alert"></p></form>`,`${button('Cancelar','close-sheet','','secondary')}<button type="submit" form="event-form" class="button primary">Conferir ${icon('arrow')}</button>`);
}
function showConfirmation(operation,next,previous=null){
  pending={operation,next:next?{...next}:null,previous:previous?{...previous}:null};
  const label=operation==='delete'?'Excluir este compromisso?':operation==='create'?'Tudo certo para agendar?':operation==='reschedule'?'Confirmar o novo horário?':'Confirmar as alterações?';
  const target=next||previous;
  openSheet('CONFIRMAÇÃO DA SIMULAÇÃO',`<h2 class="detail-title" id="sheet-title" tabindex="-1">${label}</h2><div class="confirmation-preview">${eventAvatar(target)}<h3>${e(target.title)}</h3><p>${formatDate(target.date)}<br><strong>${interval(target)}</strong></p>${previous&&operation==='reschedule'?`<div class="change-value"><small>Horário anterior</small><span>${formatDate(previous.date,{short:true})}, ${interval(previous)}</span></div>`:''}${operation==='edit'?`<div class="change-value"><small>Local</small><span>${e(target.location||'Não informado')}</span><small>Descrição</small><span>${e(target.description||'Não informada')}</span></div>`:''}</div><p class="quiet-note">Esta ação modifica apenas os dados fictícios desta aba. Nenhum pedido será enviado à sua agenda.</p>`,`${button('Voltar','back-from-preview','','secondary')}${button(operation==='delete'?'Confirmar exclusão':'Confirmar simulação','confirm-change','',operation==='delete'?'danger':'primary')}`);
}
function confirmChange(){
  if(!pending)return;
  const {operation,next,previous}=pending;pending=null;
  const current=previous?getEvent(previous.id):null;
  const fields=['id','title','organization','initials','accent','date','start','end','location','kind','description'];
  if(!['create','edit','reschedule','delete'].includes(operation)||(operation!=='create'&&(!current||fields.some(key=>current[key]!==previous[key])))||(operation==='create'&&(!next||getEvent(next.id)))){closeSheet(true);render();toast('Este exemplo mudou. Abra o compromisso e confira novamente.');return;}
  if(operation!=='delete'){
    try{if(typeof next?.title!=='string'||!next.title.trim()||eventConflicts(data.events,next,previous?.id).length)throw new RangeError();}
    catch{closeSheet(true);render();toast('Os dados ou a disponibilidade mudaram. Confira o compromisso novamente.');return;}
  }
  if(operation==='delete'){data.events.splice(data.events.findIndex(v=>v.id===previous.id),1);data.reminders.filter(v=>v.eventId===previous.id&&['pending','reserved','blocked'].includes(v.state)).forEach(v=>reminderTransition(v,'cancelled','Evento removido nesta simulação.'));checklists.delete(previous.id);}
  else if(operation==='create'){data.events.push(next);addExampleReminders(next);}
  else {Object.assign(current,next);if(operation==='reschedule')data.reminders.filter(v=>v.eventId===previous.id&&['pending','reserved','blocked'].includes(v.state)).forEach(v=>reminderTransition(v,'cancelled','Horário alterado nesta simulação.'));else data.reminders.filter(v=>v.eventId===previous.id&&['pending','reserved','blocked'].includes(v.state)).forEach(v=>{v.eventSnapshot={...next};});}
  if(operation==='reschedule')addExampleReminders(next);
  state.slots=null;state.summaryReady=false;closeSheet(true);render();toast(operation==='delete'?'Compromisso removido dos exemplos.':'Simulação confirmada. Sua agenda real permanece intacta.');
}
function reminderTransition(reminder,nextState,reason){reminder.state=nextState;reminder.reason=reason;(reminder.history??=[]).push({state:nextState,date:DEMO_TODAY,time:DEMO_TIME,reason});}
function addExampleReminders(event){for(const offset of [60,15]){const due=new Date(new Date(`${event.date}T${event.start}:00Z`).getTime()-offset*60000).toISOString();const reason='Aviso fictício preparado nesta simulação.';data.reminders.push({id:'demo-reminder-'+crypto.randomUUID(),eventId:event.id,eventSnapshot:{...event},dueDate:due.slice(0,10),dueTime:due.slice(11,16),offsetMinutes:offset,format:offset===60?'card':'text',state:'pending',reason,history:[{state:'pending',date:DEMO_TODAY,time:DEMO_TIME,reason}]});}}
function resetDemo(){
  pending=null;clearTimeout(toastTimeout);Object.assign(data,demoData());checklists.clear();Object.keys(positions).forEach(key=>delete positions[key]);
  Object.assign(state,{route:'visao-geral',date:DEMO_TODAY,view:'day',search:'',prepId:data.events.find(v=>v.date===DEMO_TODAY&&v.start>DEMO_TIME)?.id||data.events[0]?.id||null,reminderFilter:'upcoming',scenario:'normal',summary:'week',slots:null,summaryReady:false});
  sheet.dataset.dirty='false';savedFooter='';$('#sheet-content').innerHTML='';$('#sheet-footer').innerHTML='';if(sheet.open)sheet.close();if($('#info').open)$('#info').close();
  if(location.hash!=='#visao-geral')location.hash='visao-geral';render();page.focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});toast('Demonstração restaurada. Tudo aqui continua fictício.');
}
function reminderDetail(id){
  const reminder=data.reminders.find(v=>v.id===id);if(!reminder)return;
  const event=reminder.eventSnapshot||getEvent(reminder.eventId);const [label,color]=statusMap[reminder.state];
  const history=(reminder.history||[]).map(step=>`<li><span class="tag ${statusMap[step.state]?.[1]||'neutral'}">${e(statusMap[step.state]?.[0]||step.state)}</span><p>${e(step.reason)}</p><time>${formatDate(step.date,{short:true})}, ${humanTime(step.time)}</time></li>`).join('');
  openSheet('ACOMPANHAR AVISO',`<span class="tag ${color}">${label}</span><h2 id="sheet-title" tabindex="-1" class="detail-title">${e(event?.title||'Compromisso anterior')}</h2><p class="detail-meta">${reminder.format==='card'?'Card ilustrado':'Mensagem em texto'} · ${reminder.offsetMinutes===60?'1 hora':reminder.offsetMinutes+' minutos'} antes</p><div class="reminder-timeline"><div class="fact-item">${icon('calendar')}<span>Previsto para ${formatDate(reminder.dueDate,{short:true})}, ${humanTime(reminder.dueTime)}</span></div><div class="fact-item">${icon('info')}<span>${e(reminder.reason||'Aguardando o momento previsto para o aviso.')}</span></div></div><section class="demo-reminder-preview"><h3>Prévia do aviso <span class="tag neutral">Não enviada</span></h3>${reminder.format==='card'?`<div class="card-preview"><div class="card-preview-image"><img src="assets/interview-background.png" alt="Fundo ilustrado com uma mesa de trabalho"></div><div class="card-preview-copy"><small>GENIUS AGENDA AI</small><h3>Prévia do compromisso</h3><p>${e(event?.title||'Sua próxima entrevista')}</p><span>${event?formatDate(event.date):'Data ilustrativa'}</span><strong>${event?interval(event):'Horário ilustrativo'}</strong><span>Demonstração • dados fictícios • não enviada</span></div></div>`:`<div class="text-message-preview"><p>Sua entrevista começa em ${reminder.offsetMinutes} minutos.</p><strong>${e(event?.title||'Sua próxima entrevista')}</strong><span>${event?`${formatDate(event.date,{short:true})}, ${humanTime(event.start)}`:''}</span><small>Mensagem fictícia • não enviada</small></div>`}</section><p class="quiet-note">${reminder.state==='accepted'?'Este estado é um exemplo de aceite do provedor. Entrega e leitura não foram confirmadas. A prévia não é comprovante de envio.':reminder.state==='uncertain'?'Exemplo de resultado incerto: uma nova tentativa precisaria ser conferida para evitar duplicação. Esta tela não repete envios.':'O registro e o conteúdo são fictícios; nenhum aviso é enviado por esta demonstração.'}</p><section class="detail-section"><h3>Histórico simulado</h3><ol class="demo-timeline">${history}</ol><p class="quiet-note">Cada etapa deste histórico foi criada para a demonstração.</p></section>`,button('Fechar','close-sheet','','secondary'));
}
document.addEventListener('click',event=>{
  if(event.target.closest('.skip-link')){event.preventDefault();page.focus();page.scrollIntoView({block:'start'});return;}
  const el=event.target.closest('[data-action]');if(!el)return;
  const action=el.dataset.action,id=el.dataset.id;
  if(action==='demo-guide-agenda'){state.scenario='normal';go('agenda',{date:DEMO_TODAY,view:'day',search:''});}
  else if(action==='demo-guide-preparation'){state.scenario='normal';state.prepId=getEvent('demo-orbital-tecnica')?'demo-orbital-tecnica':sortEvents(data.events).find(v=>v.date>=DEMO_TODAY)?.id||data.events[0]?.id||null;go('preparo');}
  else if(action==='demo-guide-card'){state.scenario='normal';const reminder=data.reminders.find(v=>v.format==='card'&&v.state==='pending')||data.reminders.find(v=>v.format==='card');go('lembretes',{reminderFilter:'all'});if(reminder)reminderDetail(reminder.id);}
  else if(action==='new-event')editForm();
  else if(action==='event')eventDetail(id);
  else if(action==='prepare'){if(id)state.prepId=id;closeSheet(true);go('preparo');}
  else if(action==='edit-event')editForm(id,'edit');
  else if(action==='reschedule')editForm(id,'reschedule');
  else if(action==='delete-preview'){const ev=getEvent(id);if(ev)showConfirmation('delete',null,{...ev});}
  else if(action==='confirm-change')confirmChange();
  else if(action==='back-from-preview'){const change=pending;if(change?.operation==='delete')eventDetail(change.previous.id);else if(change)editForm(change.previous?.id||null,change.operation,change.next);pending=null;}
  else if(action==='close-sheet')closeSheet();
  else if(action==='discard')closeSheet(true);
  else if(action==='keep-editing')$('#sheet-footer').innerHTML=savedFooter;
  else if(action==='notifications')go('lembretes');
  else if(action==='attention')go('lembretes',{reminderFilter:'attention'});
  else if(action==='week-agenda')go('agenda',{view:'week'});
  else if(action==='select-day'){state.date=el.dataset.date;render();}
  else if(action==='month-day'){state.date=el.dataset.date;state.view='day';render();}
  else if(action==='agenda-view'){state.view=el.dataset.value;state.search='';render();}
  else if(action==='today'){state.date=DEMO_TODAY;render();}
  else if(action==='previous'||action==='next'){const direction=action==='next'?1:-1;if(state.view==='month'){const current=new Date(state.date+'T12:00:00Z');current.setUTCDate(1);current.setUTCMonth(current.getUTCMonth()+direction);state.date=current.toISOString().slice(0,10);}else state.date=addDays(state.date,direction*(state.view==='week'?7:1));render();}
  else if(action==='summary-period'){state.summary=el.dataset.value;state.summaryReady=false;render();}
  else if(action==='summary'){state.summaryReady=true;render();toast('Resumo ilustrativo preparado com os exemplos desta prévia.');}
  else if(action==='choose-slot'){const slot=state.slots?.[Number(el.dataset.index)];if(slot)editForm(state.prepId,'reschedule',slot);}
  else if(action==='reminder-filter'){state.reminderFilter=el.dataset.value;render();}
  else if(action==='all-reminders'){state.reminderFilter='all';render();}
  else if(action==='reminder')reminderDetail(id);
  else if(action==='info'){$('#preview-scenario').value=state.scenario;$('#info').showModal();}
  else if(action==='close-info')$('#info').close();
  else if(action==='restore-view'){state.scenario='normal';render();}
  else if(action==='reset-demo')resetDemo();
});
document.addEventListener('input',event=>{
  if(event.target.closest('#event-form'))sheet.dataset.dirty='true';
  if(event.target.closest('#slots-form')){state.slots=null;$('#slot-results').innerHTML='';}
  if(event.target.id==='agenda-search'){
    const position=event.target.selectionStart;state.search=event.target.value;render();const input=$('#agenda-search');input.focus({preventScroll:true});try{input.setSelectionRange(position,position);}catch{}
  }
});
document.addEventListener('change',event=>{
  if(event.target.dataset.check){const item=selectedChecklist().find(v=>v.id===event.target.dataset.check);if(item)item.checked=event.target.checked;const done=selectedChecklist().filter(v=>v.checked).length;$('#checklist-count').textContent=`${done} de ${data.checklist.length}`;$('#checklist-progress').style.width=`${done/data.checklist.length*100}%`;}
  else if(event.target.id==='prep-selector'){state.prepId=event.target.value;state.slots=null;render();}
  else if(event.target.closest('#slots-form')){state.slots=null;$('#slot-results').innerHTML='';}
  else if(event.target.id==='preview-scenario'){state.scenario=event.target.value;$('#info').close();render();}
});
document.addEventListener('submit',event=>{
  if(event.target.id==='slots-form'){
    event.preventDefault();const values=Object.fromEntries(new FormData(event.target));
    if(values.end<=values.start){state.slots=null;$('#slot-results').innerHTML='';$('#slots-error').textContent='Escolha um término depois do início.';return;}
    try{state.slots=suggestSlots(data.events,{...values,duration:Number(values.duration)});$('#slots-error').textContent='';$('#slot-results').innerHTML=renderSlots();}catch{state.slots=null;$('#slots-error').textContent='Confira a data, os horários e a duração.';$('#slot-results').innerHTML='';}return;
  }
  if(event.target.id!=='event-form')return;
  event.preventDefault();const form=event.target;const values=Object.fromEntries(new FormData(form));const mode=form.dataset.mode;const previous=getEvent(form.dataset.id);
  const next={...(previous||{id:'demo-'+crypto.randomUUID(),organization:'Compromisso pessoal',initials:'CP',accent:'violet',kind:'Entrevista'}),...values};
  if(!next.title.trim()){$('#event-form-error').textContent='Dê um título ao compromisso.';return;}
  if(!/^\d{4}-\d{2}-\d{2}$/.test(next.date)||next.start>=next.end){$('#event-form-error').textContent='O término precisa ser depois do início, no mesmo dia.';return;}
  let conflicts;try{conflicts=eventConflicts(data.events,next,previous?.id);}catch{$('#event-form-error').textContent='Confira a data e os horários do compromisso.';return;}
  if(mode!=='edit'&&conflicts.length){$('#event-form-error').textContent=`Esse horário coincide com ${conflicts[0].title}. Escolha outro intervalo.`;return;}
  showConfirmation(mode,next,previous?{...previous}:null);
});
installIcons();render();
