import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {randomUUID} from 'node:crypto';

// Exercise the real demo controller with DOM and I/O boundaries replaced. The
// browser exercise separately verifies layout, dialogs, focus and real clicks.
const appSource = await fs.readFile(new URL('../web/app.js', import.meta.url), 'utf8');
const fixtureSource = await fs.readFile(new URL('../web/demo.js', import.meta.url), 'utf8');
const bootstrap = appSource.lastIndexOf('\ninstallIcons();render();');
assert.ok(bootstrap > 0, 'The demo bootstrap must remain separate from the controller.');
const plain = value => JSON.parse(JSON.stringify(value));

function fakeElement() {
  return {
    hidden:false, open:false, textContent:'', innerHTML:'', value:'', dataset:{}, style:{},
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},
    addEventListener(){}, querySelectorAll(){return [];}, querySelector(){return null;},
    contains(){return false;}, focus(){}, scrollIntoView(){}, scrollTop:0,
    close(){this.open=false;}, showModal(){this.open=true;},
    setAttribute(){}, removeAttribute(){}, replaceChildren(){this.innerHTML='';},
    getBoundingClientRect(){return {left:0,right:100,top:0,bottom:100};},
  };
}

function harness() {
  const elements = new Map();
  const documentEvents = new Map();
  const windowEvents = new Map();
  const forbiddenCalls = [];
  const forbidden = name => () => { forbiddenCalls.push(name); throw new Error(`Demo attempted ${name}`); };
  const storage = new Proxy({}, {get(_target, property) {forbiddenCalls.push(`storage.${String(property)}`); throw new Error('Demo accessed browser persistence');}});
  const context = {
    console, crypto:{randomUUID}, CSS:{escape:value=>value},
    location:{hash:''}, navigator:{}, matchMedia:()=>({matches:true}),
    window:{addEventListener(name,callback){windowEvents.set(name,callback);},scrollY:0,scrollTo(){}},
    document:{activeElement:null,
      querySelector(selector){if(!elements.has(selector))elements.set(selector,fakeElement());return elements.get(selector);},
      querySelectorAll(){return [];}, addEventListener(name,callback){documentEvents.set(name,callback);}},
    requestAnimationFrame:callback=>callback(), setTimeout:()=>1, clearTimeout(){},
    fetch:forbidden('fetch'), XMLHttpRequest:forbidden('XMLHttpRequest'),
    WebSocket:forbidden('WebSocket'), EventSource:forbidden('EventSource'),
    localStorage:storage, sessionStorage:storage, indexedDB:storage,
    FormData:class {constructor(form){return Object.entries(form.values);}},
  };
  vm.createContext(context);
  const app = appSource.slice(0,bootstrap).replace(/^import .*;\r?\n/, '');
  vm.runInContext(fixtureSource.replace(/\bexport\s+(?=const|function)/g,'')+'\n'+app+`
    render=()=>{};
    globalThis.demoTest={data,state,checklists,initialData,demoData,addDays,weekDates,monthDays,
      formatDate,humanTime,suggestSlots,eventConflicts,getEvent,selectedChecklist,overview,
      agenda,preparation,reminders,summaryText,renderSlots,eventDetail,editForm,showConfirmation,
      confirmChange,addExampleReminders,reminderDetail,closeSheet,resetDemo,
      getPending(){return pending;},setPending(value){pending=value;}};
  `,context,{filename:'web/app.js'});
  const api = context.demoTest;
  function action(name,attributes={}) {
    const element={dataset:{action:name,...attributes}};
    documentEvents.get('click')({target:{closest(selector){return selector==='[data-action]'?element:null;}},preventDefault(){}});
  }
  function submit(values,{id='',mode='create'}={}) {
    const form={id:'event-form',dataset:{id,mode},values};
    documentEvents.get('submit')({target:form,preventDefault(){}});
  }
  return {api,elements,documentEvents,windowEvents,forbiddenCalls,action,submit};
}

function newEvent(overrides={}) {
  return {id:'demo-new-interview',title:'Entrevista de apresentação',organization:'Exemplo',
    initials:'EX',accent:'violet',kind:'Entrevista',date:'2026-10-29',start:'11:00',end:'11:30',
    location:'Sala fictícia',description:'Informações fornecidas para o exemplo.',...overrides};
}

test('each fresh demo owns independent fixtures, checklist and reminder snapshots',()=>{
  const a=harness(),b=harness();
  const original=plain(b.api.data);
  a.api.data.events[0].title='Título alterado nesta aba';
  a.api.data.reminders[0].eventSnapshot.title='Snapshot alterado nesta aba';
  if(a.api.data.reminders[0].history?.length)a.api.data.reminders[0].history[0].reason='Histórico alterado nesta aba';
  a.api.selectedChecklist()[0].checked=false;
  assert.deepEqual(plain(b.api.data),original);
  assert.equal(b.api.selectedChecklist()[0].checked,true);
  assert.deepEqual(plain(b.api.data),plain(harness().api.data));
});

test('civil date calculations do not follow the host timezone and handle leap boundaries',()=>{
  const {api}=harness();
  assert.equal(api.addDays('2028-02-28',1),'2028-02-29');
  assert.equal(api.addDays('2028-02-29',1),'2028-03-01');
  assert.equal(api.addDays('2026-12-31',1),'2027-01-01');
  assert.deepEqual(plain(api.weekDates('2026-11-01')),['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-10-31','2026-11-01']);
  const days=plain(api.monthDays('2026-11-20'));
  assert.equal(days.length,42);assert.equal(days[0],'2026-10-26');assert.equal(days.at(-1),'2026-12-06');
  assert.match(api.formatDate('2026-10-27'),/27.*outubro.*2026/);
});

test('invalid dates and times fail before becoming a demo event',()=>{
  const {api}=harness();
  for(const date of ['2026-02-29','2026-13-01','27/10/2026','2026-10-32'])assert.throws(()=>api.addDays(date,1));
  for(const time of ['24:00','09:60','9:00','bad'])assert.throws(()=>api.humanTime(time));
  assert.equal(api.humanTime('09:00'),'9h');assert.equal(api.humanTime('14:15'),'14h15');
});

test('conflict checks allow adjacent intervals and exclude only the edited event',()=>{
  const {api}=harness();const ev=newEvent();
  assert.equal(api.eventConflicts([ev],newEvent({id:'next',start:'11:30',end:'12:00'})).length,0);
  assert.equal(api.eventConflicts([ev],newEvent({id:'next',start:'10:30',end:'11:00'})).length,0);
  assert.equal(api.eventConflicts([ev],newEvent({id:'next',start:'11:15',end:'12:00'})).length,1);
  assert.equal(api.eventConflicts([ev],newEvent(),ev.id).length,0);
  assert.throws(()=>api.eventConflicts([ev],newEvent({start:'12:00',end:'11:00'})));
});

test('free slots neither overlap events nor each other and do not reserve anything',()=>{
  const {api}=harness();const events=[newEvent({start:'09:30',end:'10:15'})];
  const before=plain(events);
  const slots=plain(api.suggestSlots(events,{date:'2026-10-29',start:'09:00',end:'12:00',duration:30}));
  assert.deepEqual(slots,[{date:'2026-10-29',start:'09:00',end:'09:30'},{date:'2026-10-29',start:'10:15',end:'10:45'},{date:'2026-10-29',start:'10:45',end:'11:15'}]);
  assert.deepEqual(events,before);
  assert.deepEqual(plain(api.suggestSlots(events,{date:'2026-10-29',start:'09:30',end:'10:00',duration:30})),[]);
});

test('creation preview and cancellation have no effect until explicit confirmation',()=>{
  const {api}=harness();const before=plain(api.data),ev=newEvent();
  api.showConfirmation('create',ev,null);
  assert.deepEqual(plain(api.data),before);
  assert.equal(api.getPending().operation,'create');
  api.closeSheet(true);
  api.confirmChange();
  assert.deepEqual(plain(api.data),before);assert.equal(api.getPending(),null);
});

test('confirmed creation creates one event and two matching fictional reminders only once',()=>{
  const {api}=harness();const initial=api.data.events.length,ev=newEvent();
  api.showConfirmation('create',ev,null);api.confirmChange();api.confirmChange();
  assert.equal(api.data.events.length,initial+1);
  assert.equal(api.data.events.filter(item=>item.id===ev.id).length,1);
  const reminders=api.data.reminders.filter(item=>item.eventId===ev.id);
  assert.equal(reminders.length,2);
  assert.deepEqual(plain(reminders.map(item=>item.offsetMinutes)),[60,15]);
  assert.deepEqual(plain(reminders.map(item=>item.dueTime)),['10:00','10:45']);
  for(const reminder of reminders){assert.equal(reminder.state,'pending');assert.equal(reminder.eventSnapshot.title,ev.title);}
  assert.equal(api.getPending(),null);
});

test('reminders before midnight use the previous civil day rather than the current host date',()=>{
  const {api}=harness();const ev=newEvent({date:'2026-11-01',start:'00:10',end:'00:40'});
  api.addExampleReminders(ev);
  const reminders=api.data.reminders.filter(item=>item.eventId===ev.id);
  assert.deepEqual(plain(reminders.map(item=>[item.dueDate,item.dueTime])),[['2026-10-31','23:10'],['2026-10-31','23:55']]);
});

test('editing refreshes unsent snapshots but preserves accepted historic content',()=>{
  const {api}=harness();const previous={...api.data.events[0]};
  api.addExampleReminders(previous);
  const acceptedBefore=plain(api.data.reminders.filter(item=>item.eventId===previous.id&&item.state==='accepted'));
  const next={...previous,title:'Título revisado',description:'Texto revisado'};
  api.showConfirmation('edit',next,previous);api.confirmChange();
  assert.equal(api.getEvent(previous.id).title,next.title);
  const pending=api.data.reminders.filter(item=>item.eventId===previous.id&&item.state==='pending');
  assert.ok(pending.length);for(const item of pending)assert.equal(item.eventSnapshot.title,next.title);
  assert.deepEqual(plain(api.data.reminders.filter(item=>item.eventId===previous.id&&item.state==='accepted')),acceptedBefore);
});

test('two consecutive reschedules cancel obsolete pending reminders and preserve prior snapshots',()=>{
  const {api}=harness();const id='demo-orbital-tecnica';
  const original={...api.getEvent(id)}, originalIds=api.data.reminders.filter(item=>item.eventId===id).map(item=>item.id);
  const first={...original,date:'2026-10-29',start:'13:00',end:'13:45'};
  api.showConfirmation('reschedule',first,original);api.confirmChange();
  const firstIds=api.data.reminders.filter(item=>item.eventId===id&&item.state==='pending').map(item=>item.id);
  const second={...first,start:'15:00',end:'15:45'};
  api.showConfirmation('reschedule',second,{...api.getEvent(id)});api.confirmChange();
  assert.equal(api.getEvent(id).start,'15:00');
  for(const oldId of [...originalIds,...firstIds])assert.equal(api.data.reminders.find(item=>item.id===oldId).state,'cancelled');
  for(const oldId of originalIds)assert.equal(api.data.reminders.find(item=>item.id===oldId).eventSnapshot.start,original.start);
  for(const oldId of firstIds)assert.equal(api.data.reminders.find(item=>item.id===oldId).eventSnapshot.start,first.start);
  const current=api.data.reminders.filter(item=>item.eventId===id&&item.state==='pending');
  assert.equal(current.length,2);
  assert.deepEqual(plain(current.map(item=>item.dueTime)),['14:00','14:45']);
  for(const item of current)assert.equal(item.eventSnapshot.start,'15:00');
});

test('delete requires confirmation, cancels unsent reminders and retains accepted history',()=>{
  const {api}=harness();const previous={...api.data.events[0]};
  api.addExampleReminders(previous);
  const accepted=plain(api.data.reminders.filter(item=>item.eventId===previous.id&&item.state==='accepted'));
  api.showConfirmation('delete',null,previous);assert.ok(api.getEvent(previous.id));
  api.confirmChange();api.confirmChange();
  assert.equal(api.getEvent(previous.id),undefined);
  assert.deepEqual(plain(api.data.reminders.filter(item=>item.eventId===previous.id&&item.state==='accepted')),accepted);
  assert.equal(api.data.reminders.filter(item=>item.eventId===previous.id&&item.state==='pending').length,0);
  assert.ok(api.data.reminders.some(item=>item.eventId===previous.id&&item.state==='cancelled'));
});

test('a stale delete never removes a different event when its selected event vanished',()=>{
  const {api}=harness();const previous={...api.data.events[0]};
  api.showConfirmation('delete',null,previous);
  api.data.events.splice(0,1);const before=plain(api.data);
  api.confirmChange();
  assert.deepEqual(plain(api.data),before);assert.equal(api.getPending(),null);
});

test('changing an event after preview invalidates that old confirmation',()=>{
  const {api}=harness();const previous={...api.getEvent('demo-orbital-tecnica')};
  api.showConfirmation('reschedule',{...previous,date:'2026-10-29',start:'15:00',end:'15:45'},previous);
  api.getEvent(previous.id).description='Informação atualizada depois da prévia';
  const before=plain(api.data);api.confirmChange();
  assert.deepEqual(plain(api.data),before);assert.equal(api.getPending(),null);
});

test('a newly introduced conflict is rechecked at confirmation rather than overwritten',()=>{
  const {api}=harness();const ev=newEvent();api.showConfirmation('create',ev,null);
  api.data.events.push(newEvent({id:'demo-conflict',start:'11:15',end:'11:45'}));
  const before=plain(api.data);api.confirmChange();
  assert.deepEqual(plain(api.data),before);assert.equal(api.getPending(),null);
});

test('reset discards pending approval, UI filters, checklist progress and mutations',()=>{
  const {api}=harness();const baseline=plain(api.data);
  const created=newEvent();api.showConfirmation('create',created,null);api.confirmChange();
  api.showConfirmation('delete',null,{...created});
  api.state.search='Texto antigo';api.state.view='month';api.state.reminderFilter='accepted';
  api.state.slots=[{date:'2026-10-29',start:'09:00',end:'09:30'}];api.state.summaryReady=true;api.state.scenario='error';
  api.selectedChecklist()[0].checked=false;
  api.resetDemo();api.confirmChange();
  assert.deepEqual(plain(api.data),baseline);assert.equal(api.getPending(),null);
  assert.equal(api.state.search,'');assert.equal(api.state.view,'day');
  assert.equal(api.state.route,'visao-geral');assert.equal(api.state.date,'2026-10-27');
  assert.equal(api.state.reminderFilter,'upcoming');assert.equal(api.state.slots,null);
  assert.equal(api.state.summaryReady,false);assert.equal(api.state.scenario,'normal');
  assert.equal(api.selectedChecklist()[0].checked,true);
});

test('form rejects a conflict without creating a preview or changing data',()=>{
  const {api,elements,submit}=harness();const before=plain(api.data);
  submit({title:'Conflito fictício',date:'2026-10-27',start:'14:40',end:'15:00',location:'',description:''});
  assert.equal(api.getPending(),null);assert.deepEqual(plain(api.data),before);
  assert.match(elements.get('#event-form-error').textContent,/coincide|conflito|ocupado/i);
});

test('invalid civil date from a scripted form is handled as a useful error, not an uncaught exception',()=>{
  const {api,elements,submit}=harness();const before=plain(api.data);
  assert.doesNotThrow(()=>submit({title:'Data inválida',date:'2026-02-30',start:'11:00',end:'11:30'}));
  assert.equal(api.getPending(),null);assert.deepEqual(plain(api.data),before);
  assert.ok(elements.get('#event-form-error').textContent.length>0);
});

test('titles, notes and locations render as escaped text across detail, confirmation and card',()=>{
  const {api,elements}=harness();
  const payload='<img src=x onerror=alert(1)> & "Exemplo"';
  const ev=api.data.events[0];ev.title=payload;ev.location=payload;ev.description=payload;
  api.eventDetail(ev.id);
  let html=elements.get('#sheet-content').innerHTML;
  assert.ok(html.includes('&lt;img'));assert.ok(!html.includes(payload));assert.ok(!html.includes('<img src=x'));
  api.showConfirmation('edit',{...ev},{...ev});
  html=elements.get('#sheet-content').innerHTML;
  assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img src=x'));
  api.addExampleReminders(ev);const reminder=api.data.reminders.findLast(item=>item.eventId===ev.id&&item.format==='card');
  api.reminderDetail(reminder.id);html=elements.get('#sheet-content').innerHTML;
  assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<img src=x'));
});

test('uncertain reminders appear under attention and retain a no-repeat explanation',()=>{
  const {api}=harness();const uncertain=api.data.reminders.find(item=>item.state==='uncertain');
  assert.ok(uncertain,'The tour includes a result whose provider outcome is uncertain.');
  api.state.reminderFilter='attention';const html=api.reminders();
  assert.ok(html.includes(`data-id="${uncertain.id}"`));
  assert.ok(!html.includes(`data-id="${api.data.reminders.find(item=>item.state==='accepted').id}"`));
  assert.match(html,/incerto|Não confirmado/i);
});

test('accepted card and text previews remain demonstrative rather than claiming a real send',()=>{
  const {api,elements}=harness();
  for(const format of ['card','text']){
    const reminder=api.data.reminders.find(item=>item.format===format&&item.state==='accepted');
    api.reminderDetail(reminder.id);const html=elements.get('#sheet-content').innerHTML;
    assert.match(html,/fictíci|simulad|demonstra/i);
    assert.match(html,/entrega.*leitura|leitura.*entrega/i);
    assert.ok(!/data-action="(?:send|dispatch|recover|retry)/i.test(html));
    assert.equal(html.includes('card-preview-image'),format==='card');
  }
});

test('the complete demo exercise does not touch network or browser persistence',()=>{
  const {api,forbiddenCalls,action}=harness();
  api.overview();api.agenda();api.preparation();api.reminders();
  api.state.summaryReady=true;api.summaryText();
  api.state.slots=api.suggestSlots(api.data.events,{date:'2026-10-29',start:'09:00',end:'12:00',duration:30});api.renderSlots();
  const ev=newEvent();api.showConfirmation('create',ev,null);api.confirmChange();
  api.showConfirmation('reschedule',{...ev,start:'12:00',end:'12:30'},{...api.getEvent(ev.id)});api.confirmChange();
  api.reminderDetail(api.data.reminders.find(item=>item.eventId===ev.id).id);
  api.showConfirmation('delete',null,{...api.getEvent(ev.id)});api.confirmChange();
  action('reset-demo');
  assert.deepEqual(forbiddenCalls,[]);
});
