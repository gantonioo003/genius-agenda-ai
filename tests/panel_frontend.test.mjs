import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {randomUUID} from 'node:crypto';

const source=await fs.readFile(new URL('../web/live.js',import.meta.url),'utf8');
const bootstrap=source.lastIndexOf('\ninstallIcons();renderAccess();');
assert.ok(bootstrap>0,'The application bootstrap must be isolated for mocked I/O tests.');

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function fakeElement(){return {hidden:false,open:false,textContent:'',innerHTML:'',classList:{add(){},remove(){},toggle(){}},addEventListener(){},querySelectorAll(){return []},replaceChildren(){this.innerHTML='';this.textContent=''},contains(){return false},focus(){},scrollIntoView(){},close(){this.open=false},showModal(){this.open=true},getBoundingClientRect(){return {left:0,right:100,top:0,bottom:100}}};}
function harness(){
  const elements=new Map();const fetches=[];const windowEvents=new Map(),documentEvents=new Map();
  const context={console,crypto:{randomUUID},AbortController,DOMException,URLSearchParams,btoa:value=>Buffer.from(value,'binary').toString('base64'),CSS:{escape:value=>value},location:{hash:''},window:{addEventListener(name,callback){windowEvents.set(name,callback)},scrollY:0,scrollTo(){}},document:{hidden:false,activeElement:null,querySelector(selector){if(!elements.has(selector))elements.set(selector,fakeElement());return elements.get(selector)},querySelectorAll(){return []},addEventListener(name,callback){documentEvents.set(name,callback)}},navigator:{},matchMedia:()=>({matches:true}),setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,requestAnimationFrame:fn=>fn(),fetch(url,options){const job=deferred();fetches.push({url,options,...job});return job.promise}};
  vm.createContext(context);
  vm.runInContext(source.slice(0,bootstrap)+`\nrender=()=>{};renderAccess=()=>{};refresh=()=>Promise.resolve();globalThis.panelTest={state,request,requestImage,latest,stopAll,clearPrivateState,loadCalendar,loadReminders,loadPrep,session,beginMutation,mutationInput,previewMutation,confirmMutation,checkMutation,discardMutation,reviseMutation,closeSheet,suspendMutation,normalMutationResult,PanelError,assistDefaults,generateSummary,findAvailability,generateBriefing,aiInput,togglePrepCheck,preparation,summaryPanel,availabilityPanel,briefingPanel,suspendAssist,reminders,reminderDetail,previewReminderCard,reminderImageFailed,getReminder(){return selectedReminder},getMutation(){return mutation},replaceRefresh(fn){refresh=fn},replaceLatest(fn){latest=fn}};`,context,{filename:'web/live.js'});
  return {api:context.panelTest,fetches,elements,windowEvents,documentEvents,document:context.document};
}
const event=id=>({id,title:'Compromisso '+id,date:'2026-10-27',start:'14:00',end:'14:30',start_at:'2026-10-27T14:00:00-03:00',end_at:'2026-10-27T14:30:00-03:00',all_day:false,time_zone:'America/Recife',status:'confirmed'});
const calendar=id=>({events:[event(id)],complete:true,reference_date:'2026-10-27',today:'2026-10-27',time_zone:'America/Recife',calendar_name:'Agenda de teste'});
const response=(data,status=200)=>({ok:status>=200&&status<300,status,async text(){return JSON.stringify(data)}});

test('CRUD: the new appointment button keeps its accessible name when mobile CSS hides its text',()=>{
  const markup=source.match(/function disabledNew\(\)\{return `([^`]+)`;/)?.[1];
  assert.ok(markup,'New appointment control is present.');
  assert.match(markup,/<button\b[^>]*data-action="new-mutation"[^>]*aria-label="Novo compromisso"/);
});

for(const loader of ['calendar','reminders','preparation']){
  test(`${loader}: response already received cannot repopulate a new session`,async()=>{
    const {api}=harness();const pending=deferred();api.state.authenticated=true;api.replaceLatest(()=>pending.promise);
    const request=loader==='calendar'?api.loadCalendar():loader==='reminders'?api.loadReminders():api.loadPrep('A');
    api.clearPrivateState();api.state.authenticated=true;
    if(loader==='calendar')pending.resolve(calendar('old'));
    if(loader==='reminders')pending.resolve({items:[{id:'old',title:'Old private reminder'}],complete:true,total:1});
    if(loader==='preparation')pending.resolve({event:event('A')});
    await request;
    assert.equal(api.state.calendar.events.length,0);
    assert.equal(api.state.reminders.items.length,0);
    assert.equal(api.state.prepDetail,null);
    assert.equal(api.state.calendar.status,'idle');
    assert.equal(api.state.reminders.status,'idle');
    assert.equal(api.state.prepStatus,'idle');
  });
}

test('an old 401 response cannot revoke a newer authenticated session',async()=>{
  const {api,fetches}=harness();api.state.authenticated=true;
  const pending=api.request('/events');
  api.clearPrivateState();api.state.authenticated=true;api.state.authError='';
  fetches[0].resolve(response({status:'error',message:'Expired old session'},401));
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(api.state.authenticated,true);
  assert.equal(api.state.authError,'');
});

test('stopAll invalidates successful transport results even when fetch ignores abort',async()=>{
  const {api,fetches}=harness();
  const pending=api.latest('example','/session',{sessionRequest:true});
  api.stopAll();fetches[0].resolve(response({authenticated:false}));
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(fetches[0].options.signal.aborted,true);
});

test('old network failures are cancellation after a session change',async()=>{
  const {api,fetches}=harness();api.state.authenticated=true;
  const pending=api.latest('session','/session',{sessionRequest:true});
  api.clearPrivateState();api.state.authenticated=true;
  fetches[0].reject(new Error('Old network error'));
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(api.state.authenticated,true);
});

test('old malformed responses are discarded before parsing in a newer session',async()=>{
  const {api,fetches}=harness();api.state.authenticated=true;
  const pending=api.request('/session',{sessionRequest:true});
  api.clearPrivateState();api.state.authenticated=true;
  fetches[0].resolve({ok:false,status:503,async text(){return 'not JSON'}});
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(api.state.authenticated,true);
});

test('an old authenticated session check cannot sign in after local state is cleared',async()=>{
  const {api}=harness();const pending=deferred();api.replaceLatest(()=>pending.promise);
  const checking=api.session();api.clearPrivateState();
  pending.resolve({authenticated:true,csrf_token:'a'.repeat(43)});
  assert.equal(await checking,false);
  assert.equal(api.state.authenticated,false);
  assert.equal(api.state.csrf,null);
});

test('preparation A cannot replace B after B has been selected',async()=>{
  const {api}=harness();api.state.authenticated=true;const old=deferred(),current=deferred();
  api.replaceLatest((_name,path)=>path.endsWith('/A')?old.promise:current.promise);
  const pendingA=api.loadPrep('A'),pendingB=api.loadPrep('B');
  current.resolve({event:event('B')});await pendingB;
  old.resolve({event:event('A')});await pendingA;
  assert.equal(api.state.prepId,'B');assert.equal(api.state.prepDetail.id,'B');assert.equal(api.state.prepStatus,'ready');
});

test('a late error for preparation A cannot override the successful context of B',async()=>{
  const {api}=harness();api.state.authenticated=true;const old=deferred(),current=deferred();
  api.replaceLatest((_name,path)=>path.endsWith('/A')?old.promise:current.promise);
  const pendingA=api.loadPrep('A'),pendingB=api.loadPrep('B');
  current.resolve({event:event('B')});await pendingB;
  old.reject(new Error('Old failure'));await pendingA;
  assert.equal(api.state.prepDetail.id,'B');assert.equal(api.state.prepStatus,'ready');assert.equal(api.state.prepError,'');
});

test('a period change invalidates the previous preparation request and selection',async()=>{
  const {api}=harness();api.state.authenticated=true;const old=deferred();
  api.replaceLatest((name)=>name==='preparation'?old.promise:Promise.resolve(calendar('B')));
  const pendingA=api.loadPrep('A');await api.loadCalendar('2026-10-27','day');
  old.resolve({event:event('A')});await pendingA;
  assert.equal(api.state.prepId,'B');assert.equal(api.state.prepDetail,null);assert.equal(api.state.prepStatus,'idle');
});

test('returning to a visible tab hides private data until a valid session is confirmed, without provider refetch',async()=>{
  const {api,document,documentEvents,elements}=harness();const pending=deferred(),calls=[];
  api.state.authenticated=true;api.state.csrf='old';api.state.calendar.events=[event('private')];api.state.calendar.status='ready';api.state.calendar.complete=true;
  api.replaceLatest(name=>{calls.push(name);return pending.promise});
  document.hidden=true;documentEvents.get('visibilitychange')();
  assert.equal(elements.get('#app-root').hidden,true);
  document.hidden=false;const checking=documentEvents.get('visibilitychange')();
  assert.equal(api.state.revalidating,true);assert.equal(elements.get('#app-root').hidden,true);
  pending.resolve({authenticated:true,csrf_token:'a'.repeat(43)});await checking;
  assert.equal(api.state.authenticated,true);assert.equal(api.state.revalidating,false);assert.equal(elements.get('#app-root').hidden,false);
  assert.equal(api.state.calendar.events[0].id,'private');assert.deepEqual(calls,['session']);
});

test('returning to a visible tab clears cached private data when the session has expired elsewhere',async()=>{
  const {api,documentEvents,elements}=harness();const pending=deferred();
  api.state.authenticated=true;api.state.csrf='old';api.state.calendar.events=[event('private')];api.replaceLatest(()=>pending.promise);
  const checking=documentEvents.get('visibilitychange')();assert.equal(elements.get('#app-root').hidden,true);
  pending.resolve({authenticated:false,pending:false});await checking;
  assert.equal(api.state.authenticated,false);assert.equal(api.state.csrf,null);assert.equal(api.state.calendar.events.length,0);assert.equal(elements.get('#app-root').hidden,true);
});

test('a failed visibility revalidation keeps private data hidden and clears it',async()=>{
  const {api,documentEvents,elements}=harness();const pending=deferred();
  api.state.authenticated=true;api.state.calendar.events=[event('private')];api.replaceLatest(()=>pending.promise);
  const checking=documentEvents.get('visibilitychange')();pending.reject(new Error('Offline'));await checking;
  assert.equal(api.state.authenticated,false);assert.equal(api.state.calendar.events.length,0);assert.equal(elements.get('#app-root').hidden,true);assert.match(api.state.authError,/confirmar seu acesso/);
});

test('bfcache snapshots clear private DOM before freezing and recheck access before loading data',async()=>{
  const {api,windowEvents,elements}=harness();const pending=deferred(),calls=[];
  api.state.authenticated=true;api.state.csrf='old';api.state.calendar.events=[event('private')];elements.get('#main-content').innerHTML='private';
  api.replaceLatest(name=>{calls.push(name);if(name==='session')return pending.promise;if(name==='calendar')return Promise.resolve(calendar('fresh'));return Promise.resolve({items:[],complete:true,total:0})});
  windowEvents.get('pagehide')({persisted:true});
  assert.equal(elements.get('#main-content').innerHTML,'');assert.equal(api.state.calendar.events.length,0);assert.equal(api.state.csrf,null);assert.equal(elements.get('#app-root').hidden,true);
  const checking=windowEvents.get('pageshow')({persisted:true});assert.deepEqual(calls,['session']);assert.equal(elements.get('#app-root').hidden,true);
  pending.resolve({authenticated:true,csrf_token:'a'.repeat(43)});await checking;
  assert.equal(api.state.calendar.events[0].id,'fresh');assert.deepEqual(calls,['session','calendar','reminders']);assert.equal(elements.get('#app-root').hidden,false);
});

test('concurrent visibility and persisted restore events share one session revalidation',async()=>{
  const {api,windowEvents,documentEvents}=harness();const pending=deferred(),calls=[];
  api.state.authenticated=true;api.replaceLatest(name=>{calls.push(name);return pending.promise});
  const visible=documentEvents.get('visibilitychange')(),restored=windowEvents.get('pageshow')({persisted:true});
  assert.deepEqual(calls,['session']);pending.resolve({authenticated:true,csrf_token:'a'.repeat(43)});await Promise.all([visible,restored]);
  assert.deepEqual(calls,['session']);assert.equal(api.state.revalidating,false);
});

test('a query interrupted by session revalidation resumes after valid access instead of remaining loading',async()=>{
  const {api,documentEvents}=harness();const pending=deferred(),calls=[];
  api.state.authenticated=true;api.state.calendar.status='loading';api.state.date='2026-10-27';
  api.replaceLatest(name=>{calls.push(name);return name==='session'?pending.promise:Promise.resolve(calendar('resumed'))});
  const checking=documentEvents.get('visibilitychange')();assert.equal(api.state.calendar.status,'idle');assert.deepEqual(calls,['session']);
  pending.resolve({authenticated:true,csrf_token:'a'.repeat(43)});await checking;
  assert.equal(api.state.calendar.status,'ready');assert.equal(api.state.calendar.events[0].id,'resumed');assert.deepEqual(calls,['session','calendar']);
});
function crudSetup(){
  const h=harness();h.api.state.authenticated=true;h.api.state.csrf='csrf-for-tests';h.api.state.today='2026-10-27';h.api.state.date='2026-10-27';h.api.beginMutation('create');h.api.mutationInput('title','Entrevista teste');return h;
}
function operation(action='create',state='pending',extra={}){
  return {operation_id:'da24a717-d688-49d2-a110-c70ef3f39c92',action,state,mutation_state:state==='succeeded'?'applied':'not_applied',before:null,after:{event_id:'event-real',title:'Entrevista teste',start:'2026-10-27T09:00:00-03:00',end:'2026-10-27T09:30:00-03:00',all_day:false,time_zone:'America/Recife',description:''},message:'Dados conferidos.',conflicts:[],...extra};
}
async function pendingPreview(h,action='create'){
  const promise=h.api.previewMutation();h.fetches.at(-1).resolve(response({data:operation(action)}));await promise;
}
test("CRUD: invalid fields stop before preview and identify the affected fields",async function invalidForm(){
  const h=crudSetup();h.api.mutationInput('title',' ');h.api.mutationInput('end','2026-10-27T08:30');
  await h.api.previewMutation();assert.equal(h.fetches.length,0);assert.match(h.api.getMutation().errors.title,/título/);assert.match(h.api.getMutation().errors.end,/depois/);
});
test("CRUD: preview contains the exact draft, local times and CSRF, without executing a write",async function previewDoesNotWrite(){
  const h=crudSetup();await pendingPreview(h);
  assert.equal(h.fetches.length,1);assert.equal(h.fetches[0].url,'/painel/api/agenda/preview');assert.equal(h.fetches[0].options.method,'POST');
  const payload=JSON.parse(h.fetches[0].options.body);assert.match(payload.request_id,/^[0-9a-f-]{36}$/);assert.equal(payload.event.title,'Entrevista teste');assert.equal(payload.event.start,'2026-10-27T09:00');assert.equal('location' in payload.event,false);assert.equal('time_zone' in payload,false);
  assert.equal(h.api.getMutation().result.state,'pending');assert.match(h.elements.get('#sheet-footer').innerHTML,/Confirmar criação/);assert.equal(h.fetches[0].options.headers['X-Panel-CSRF'],'csrf-for-tests');assert.equal('Authorization' in h.fetches[0].options.headers,false);
});
test("CRUD: double confirmation sends one POST and refreshes only after verified success",async function doubleConfirmation(){
  const h=crudSetup();let refreshes=0;h.api.replaceRefresh(()=>{refreshes++;return Promise.resolve()});await pendingPreview(h);
  const one=h.api.confirmMutation(),two=h.api.confirmMutation();assert.equal(h.fetches.length,2);
  assert.equal(h.fetches[1].url,'/painel/api/agenda/'+operation().operation_id+'/confirm');assert.equal(h.fetches[1].options.body,'{}');
  h.fetches[1].resolve(response({data:operation('create','succeeded')}));await Promise.all([one,two]);assert.equal(h.api.getMutation().phase,'done');assert.equal(refreshes,1);
  await h.api.confirmMutation();assert.equal(h.fetches.length,2);
});
test("CRUD: uncertain confirmation permits a status GET and never retries the write automatically",async function confirmationNetworkFailure(){
  const h=crudSetup();await pendingPreview(h);const sending=h.api.confirmMutation();h.fetches[1].reject(new Error('Network lost'));await sending;
  assert.equal(h.api.getMutation().phase,'unknown');assert.equal(h.fetches.length,2);assert.doesNotMatch(h.elements.get('#sheet-footer').innerHTML,/data-action="confirm-mutation"/);
  const checking=h.api.checkMutation();assert.equal(h.fetches[2].options.method,'GET');assert.equal(h.fetches[2].url,'/painel/api/agenda/'+operation().operation_id);
  h.fetches[2].resolve(response({data:operation('create','succeeded')}));await checking;assert.equal(h.api.getMutation().phase,'done');assert.equal(h.fetches.filter(x=>x.url.endsWith('/confirm')).length,1);
});
test("CRUD: manually checking a lost preview reuses the same request id and exact payload",async function stablePreviewRetry(){
  const h=crudSetup();const first=h.api.previewMutation();h.fetches[0].reject(new Error('offline'));await first;
  const body=h.fetches[0].options.body;assert.equal(h.api.getMutation().phase,'preview-error');
  const retry=h.api.previewMutation();assert.equal(h.fetches[1].options.body,body);h.fetches[1].resolve(response({data:operation()}));await retry;assert.equal(h.api.getMutation().result.state,'pending');
});
test("CRUD: a late preview cannot restore private state after logout",async function oldPreviewAfterLogout(){
  const h=crudSetup();const preview=h.api.previewMutation();h.api.clearPrivateState();h.api.state.authenticated=true;
  h.fetches[0].resolve(response({data:operation()}));await preview;assert.equal(h.api.getMutation(),null);assert.equal(h.elements.get('#sheet-content').innerHTML,'');
});
test("CRUD: cancellation in progress and cancelled previews cannot be confirmed",async function cancellationBlocksConfirmation(){
  const h=crudSetup();await pendingPreview(h);const cancelling=h.api.discardMutation();await h.api.confirmMutation();
  assert.equal(h.fetches.length,2);assert.ok(h.fetches[1].url.endsWith('/cancel'));h.fetches[1].resolve(response({data:operation('create','cancelled')}));await cancelling;await h.api.confirmMutation();assert.equal(h.fetches.length,2);assert.equal(h.api.getMutation().result.state,'cancelled');
});
test("CRUD: HTTP 409 preserves structured conflicts and does not offer confirmation",async function structuredConflict(){
  const h=crudSetup();const preview=h.api.previewMutation();const conflicting=operation('create','conflict',{conflicts:[{title:'Outro compromisso',start:'2026-10-27T09:00:00-03:00',end:'2026-10-27T10:00:00-03:00'}]});
  h.fetches[0].resolve(response({status:'failed',message:'Há conflito.',error:{code:'calendar_conflict',fields:['start']},data:conflicting},409));await preview;
  assert.equal(h.api.getMutation().result.state,'conflict');assert.equal(h.api.getMutation().result.conflicts.length,1);assert.match(h.elements.get('#sheet-content').innerHTML,/Outro compromisso/);assert.doesNotMatch(h.elements.get('#sheet-footer').innerHTML,/data-action="confirm-mutation"/);
});
test("CRUD: editing retains the selected event id and sends only modified details",async function editExactPatch(){
  const h=harness();h.api.state.authenticated=true;const selected={...event('selected-id'),description:'Old',location:'Office'};h.api.beginMutation('edit',selected);h.api.mutationInput('description','New');h.api.mutationInput('location','Video');
  const preview=h.api.previewMutation();const body=JSON.parse(h.fetches[0].options.body);assert.equal(body.event_id,'selected-id');assert.deepEqual(body.event,{description:'New',location:'Video'});h.fetches[0].resolve(response({data:operation('edit')}));await preview;
});
test("CRUD: selecting delete only prepares a preview and requires explicit deletion approval",async function deleteNeedsApproval(){
  const h=harness();h.api.state.authenticated=true;h.api.beginMutation('delete',event('selected-id'));assert.equal(h.fetches.length,1);assert.equal(h.fetches[0].url,'/painel/api/agenda/preview');
  assert.deepEqual(JSON.parse(h.fetches[0].options.body).event_id,'selected-id');h.fetches[0].resolve(response({data:operation('delete','pending',{before:operation().after,after:null})}));await new Promise(resolve=>setImmediate(resolve));
  assert.match(h.elements.get('#sheet-footer').innerHTML,/Sim, excluir compromisso/);assert.equal(h.fetches.filter(x=>x.url.endsWith('/confirm')).length,0);
});
test("CRUD: suspending an in-flight write keeps a recoverable operation and discards its late response",async function suspendedWrite(){
  const h=crudSetup();await pendingPreview(h);const confirm=h.api.confirmMutation();h.api.suspendMutation();h.api.stopAll();h.fetches[1].resolve(response({data:operation('create','succeeded')}));await confirm;
  assert.equal(h.api.getMutation().phase,'unknown');assert.equal(h.api.getMutation().closed,true);assert.equal(h.api.getMutation().busy,false);assert.equal(h.api.getMutation().result.state,'pending');assert.equal(h.fetches.length,2);
});
test("CRUD: revised data gets a new preview id, and a pending preview cannot be silently edited",async function freshIdAfterRevision(){
  const h=crudSetup();const preview=h.api.previewMutation();const old=JSON.parse(h.fetches[0].options.body).request_id;h.fetches[0].resolve(response({data:operation('create','conflict')}));await preview;
  h.api.reviseMutation();h.api.mutationInput('start','2026-10-27T11:00');h.api.mutationInput('end','2026-10-27T11:30');const changed=h.api.previewMutation();const body=JSON.parse(h.fetches[1].options.body);assert.notEqual(body.request_id,old);assert.equal(body.event.start,'2026-10-27T11:00');h.fetches[1].resolve(response({data:operation()}));await changed;
  const result=h.api.getMutation().result;h.api.reviseMutation();assert.equal(h.api.getMutation().result,result);assert.equal(h.api.getMutation().phase,'preview');
});
test("CRUD: closing a dirty draft asks before discarding and performs no API write",async function closeDirtyDraft(){
  const h=crudSetup();h.api.closeSheet();assert.equal(h.api.getMutation().phase,'leave');assert.match(h.elements.get('#sheet-content').innerHTML,/Descartar este rascunho/);await h.api.discardMutation();assert.equal(h.api.getMutation(),null);assert.equal(h.fetches.length,0);
});
test("CRUD: a stale confirmation requires a new preview and cannot execute again",async function staleStateNoConfirm(){
  const h=crudSetup();await pendingPreview(h);const confirming=h.api.confirmMutation();h.fetches[1].resolve(response({status:'failed',data:operation('create','invalidated'),message:'O compromisso mudou.'},409));await confirming;
  assert.equal(h.api.getMutation().result.state,'invalidated');assert.doesNotMatch(h.elements.get('#sheet-footer').innerHTML,/data-action="confirm-mutation"/);await h.api.confirmMutation();assert.equal(h.fetches.length,2);
});
test("CRUD: a previous session confirmation cannot replace a new draft",async function newSessionDiscardsOldConfirmation(){
  const h=crudSetup();await pendingPreview(h);const sending=h.api.confirmMutation();h.api.clearPrivateState();h.api.state.authenticated=true;h.api.state.date='2026-10-27';h.api.beginMutation('create');h.api.mutationInput('title','New session');
  h.fetches[1].resolve(response({data:operation('create','succeeded')}));await sending;assert.equal(h.api.getMutation().values.title,'New session');assert.equal(h.api.getMutation().result,null);assert.equal(h.api.getMutation().phase,'form');
});
test("CRUD: server validation fields appear beside the corresponding form inputs",async function validationFieldsNearInputs(){
  const h=crudSetup();const preview=h.api.previewMutation();h.fetches[0].resolve(response({status:'failed',message:'Confira o horário de término.',error:{code:'invalid_request',fields:['end']},data:null},422));await preview;
  assert.equal(h.api.getMutation().phase,'form');assert.equal(h.api.getMutation().errors.end,'Confira o horário de término.');assert.match(h.elements.get('#sheet-content').innerHTML,/id="mutation-end-error"/);assert.match(h.elements.get('#sheet-content').innerHTML,/aria-invalid="true"/);
});
test("CRUD: all-day event details remain editable without adding timing changes",async function allDayDetailsRemainEditable(){
  const h=harness();h.api.state.authenticated=true;const selected={...event('all-day'),all_day:true,start:null,end:null,start_at:'2026-10-27',end_at:'2026-10-28',description:'Old'};h.api.beginMutation('edit',selected);h.api.mutationInput('description','New');const preview=h.api.previewMutation();assert.equal(JSON.parse(h.fetches[0].options.body).event.description,'New');h.fetches[0].resolve(response({data:operation('edit')}));await preview;assert.equal(h.api.getMutation().action,'edit');
});
test("CRUD: all-day rescheduling is clearly unavailable and never sends a request",async function allDayRescheduleNotExposed(){
  const h=harness();h.api.state.authenticated=true;h.api.beginMutation('reschedule',{...event('all-day'),all_day:true});assert.equal(h.api.getMutation(),null);assert.equal(h.fetches.length,0);assert.match(h.elements.get('#toast').textContent,/dia inteiro/);
});
test("CRUD: form limits match the server before preview",async function formLimitsAligned(){
  const h=crudSetup();h.api.mutationInput('title','a'.repeat(401));h.api.mutationInput('description','a'.repeat(4001));await h.api.previewMutation();assert.equal(h.fetches.length,0);assert.match(h.api.getMutation().errors.title,/400/);assert.match(h.api.getMutation().errors.description,/4.000/);
});
test("CRUD: successful creation updates the displayed date to the verified event date",async function afterCreateFocusesNewDate(){
  const h=crudSetup();await pendingPreview(h);const confirming=h.api.confirmMutation();h.fetches[1].resolve(response({data:operation('create','succeeded',{after:{...operation().after,start:'2026-11-02T09:00:00-03:00',end:'2026-11-02T09:30:00-03:00'}})}));await confirming;assert.equal(h.api.state.date,'2026-11-02');
});
test('CRUD: a failed server preparation returns affected fields to the editable form',async function preparedValidationFieldsNearInputs(){
  const h=crudSetup();const preview=h.api.previewMutation();h.fetches[0].resolve(response({status:'failed',message:'Confira o início.',data:operation('create','failed',{message:'Confira o início.',error:{code:'invalid_request',fields:['event.start']}})},422));await preview;
  assert.equal(h.api.getMutation().phase,'form');assert.equal(h.api.getMutation().result,null);assert.equal(h.api.getMutation().payload,null);assert.match(h.api.getMutation().errors.start,/início/);
});

function assistSetup(){const h=harness();h.api.state.authenticated=true;h.api.state.csrf='s'.repeat(43);h.api.state.date='2026-10-27';h.api.state.today='2026-10-27';h.api.state.timeZone='America/Recife';h.api.state.calendar={status:'ready',events:[event('A')],complete:true};h.api.state.prepId='A';h.api.state.prepDetail=event('A');h.api.state.prepStatus='ready';h.api.assistDefaults();return h;}
const summaryReply=(mode='ai_organized',overrides={})=>({period:{kind:'week',reference_date:'2026-10-27',time_zone:'America/Recife'},snapshot:{complete:true,collected_at_utc:'2026-10-27T12:00:00Z'},totals:{events:1,upcoming:1,tentative:0},details:{shown:1,total:1,omitted:0},summary:{text:'Sua semana tem um compromisso.',mode,reason:mode==='factual'?'llm_unavailable':null},execution_allowed:false,...overrides});
const freeReply=(slots=[{ref:'S1',start:'2026-10-27T09:00:00-03:00',end:'2026-10-27T09:30:00-03:00',time_zone:'America/Recife'}])=>({request:{date_from:'2026-10-27',date_to:'2026-10-27',window_start:'09:00',window_end:'18:00',duration_minutes:30,buffer_minutes:0},period:{time_zone:'America/Recife'},snapshot:{complete:true,observed_at_utc:'2026-10-27T12:00:00Z'},slots,count:slots.length,more_available:false,reservation_created:false,execution_allowed:false});
const briefReply=(id='A',description='Contexto informado')=>({status:'ready',mode:'factual_template',facts:{event_id:id,title:'Entrevista '+id,start:'2026-10-27T14:00:00-03:00',end:'2026-10-27T14:30:00-03:00',all_day:false,time_zone:'America/Recife',location:null},provided_context:{event_description:description,user_context:null,verified_externally:false,truncated_fields:[]},checklist:['Revise suas experiências.','Confirme o local ou link.'],missing_fields:['location'],execution_allowed:false,reservation_created:false});

test('preparation: showing tabs and selecting an event never generates a model request automatically',()=>{const h=assistSetup();const markup=h.api.preparation();assert.match(markup,/Entrevista/);assert.match(markup,/Resumo/);assert.match(markup,/Horários livres/);assert.match(markup,/Preparar entrevista/);assert.equal(h.fetches.length,0);});
test('summary: explicit generation posts the exact period with CSRF, and duplicate clicks share one request',async()=>{const h=assistSetup();const first=h.api.generateSummary(),second=h.api.generateSummary();assert.equal(h.fetches.length,1);assert.equal(h.fetches[0].url,'/painel/api/agenda/summary');assert.equal(h.fetches[0].options.headers['X-Panel-CSRF'],'s'.repeat(43));assert.deepEqual(JSON.parse(h.fetches[0].options.body),{period:'week',date:'2026-10-27'});h.fetches[0].resolve(response({data:summaryReply()}));await Promise.all([first,second]);assert.equal(h.api.state.assist.summary.status,'ready');});
test('summary: a late week result cannot replace a newly selected month',async()=>{const h=assistSetup(),old=deferred(),current=deferred();let n=0;h.api.replaceLatest(()=>++n===1?old.promise:current.promise);const first=h.api.generateSummary();h.api.aiInput('summary','period','month');const second=h.api.generateSummary();current.resolve(summaryReply('ai_organized',{period:{kind:'month',reference_date:'2026-10-27'}}));await second;old.resolve(summaryReply());await first;assert.equal(h.api.state.assist.summary.period,'month');assert.equal(h.api.state.assist.summary.data.period.kind,'month');});
test('summary: logout clears summaries and a late success cannot expose them in a new session',async()=>{const h=assistSetup(),job=deferred();h.api.replaceLatest(()=>job.promise);const request=h.api.generateSummary();h.api.clearPrivateState();h.api.state.authenticated=true;job.resolve(summaryReply());await request;assert.equal(h.api.state.assist.summary.data,null);assert.equal(h.api.state.assist.summary.status,'idle');});
test('summary: factual fallback is identified and unsafe output is escaped',async()=>{const h=assistSetup();const job=h.api.generateSummary();h.fetches[0].resolve(response({data:summaryReply('factual',{summary:{text:'<script>secret()</script>',mode:'factual',reason:'llm_unavailable'}})}));await job;const markup=h.api.summaryPanel();assert.match(markup,/Resumo factual/);assert.match(markup,/IA não ficou disponível/);assert.match(markup,/&lt;script&gt;/);assert.doesNotMatch(markup,/<script>/);});
test('summary: a calendar collection failure never becomes an empty successful summary',async()=>{const h=assistSetup();const job=h.api.generateSummary();h.fetches[0].resolve(response({status:'failed',message:'Não foi possível confirmar os fatos.',error:{code:'agenda_collection_failed',fields:[]}},502));await job;assert.equal(h.api.state.assist.summary.status,'error');assert.equal(h.api.state.assist.summary.data,null);assert.doesNotMatch(h.api.summaryPanel(),/Mais espaço no seu período/);});
test('summary: a mismatched server period is rejected before showing success',async()=>{const h=assistSetup();const job=h.api.generateSummary();h.fetches[0].resolve(response({data:summaryReply('factual',{period:{kind:'month',reference_date:'2026-10-27'}})}));await job;assert.equal(h.api.state.assist.summary.status,'error');});
test('summary: an empty period states that IA was unnecessary',async()=>{const h=assistSetup();const job=h.api.generateSummary();h.fetches[0].resolve(response({data:summaryReply('factual',{totals:{events:0,upcoming:0,tentative:0},summary:{text:'Nenhum compromisso.',mode:'factual',reason:'empty_period'}})}));await job;assert.match(h.api.summaryPanel(),/não foi necessário usar IA/);});
test('availability: invalid windows and durations stop before any request',async()=>{const h=assistSetup();h.api.aiInput('availability','window_end','08:00');h.api.aiInput('availability','duration_minutes','0');await h.api.findAvailability();assert.equal(h.fetches.length,0);assert.ok(h.api.state.assist.availability.errors.window_end);assert.ok(h.api.state.assist.availability.errors.duration_minutes);});
test('availability: requests are read-only with numeric duration and results use friendly times',async()=>{const h=assistSetup();const job=h.api.findAvailability();assert.equal(h.fetches[0].url,'/painel/api/agenda/availability');assert.equal(JSON.parse(h.fetches[0].options.body).duration_minutes,30);h.fetches[0].resolve(response({data:freeReply()}));await job;const markup=h.api.availabilityPanel();assert.match(markup,/9h às 9h30/);assert.match(markup,/Nenhuma reserva foi criada/);assert.doesNotMatch(markup,/2026-10-27T09/);assert.doesNotMatch(markup,/America\/Recife/);});
test('availability: changing a field discards pending results even if transport ignores abort',async()=>{const h=assistSetup(),old=deferred();h.api.replaceLatest(()=>old.promise);const job=h.api.findAvailability();h.api.aiInput('availability','date_from','2026-10-28');old.resolve(freeReply());await job;assert.equal(h.api.state.assist.availability.data,null);assert.equal(h.api.state.assist.availability.status,'idle');});
test('availability: empty results offer another search without claiming a reservation',async()=>{const h=assistSetup();const job=h.api.findAvailability();h.fetches[0].resolve(response({data:freeReply([])}));await job;assert.match(h.api.availabilityPanel(),/Nenhum horário nessa faixa/);assert.match(h.api.availabilityPanel(),/Nenhuma reserva foi criada/);});
test('briefing: selected identity and notes go to a single explicit read-only request',async()=>{const h=assistSetup();h.api.aiInput('notes','notes','Revisar meu projeto.');const first=h.api.generateBriefing(),second=h.api.generateBriefing();assert.equal(h.fetches.length,1);assert.deepEqual(JSON.parse(h.fetches[0].options.body),{event_id:'A',user_context:'Revisar meu projeto.'});h.fetches[0].resolve(response({data:briefReply()}));await Promise.all([first,second]);assert.equal(h.api.state.assist.briefing.status,'ready');assert.match(h.api.briefingPanel(),/Checklist sugerido/);});
test('briefing: selecting B invalidates A and its late result cannot restore the previous interview',async()=>{const h=assistSetup(),old=deferred();h.api.replaceLatest((name)=>name==='assist-briefing'?old.promise:Promise.resolve({event:event('B')}));const job=h.api.generateBriefing();await h.api.loadPrep('B');old.resolve(briefReply());await job;assert.equal(h.api.state.prepId,'B');assert.equal(h.api.state.assist.briefing.data,null);});
test('briefing: notes edited during generation invalidate both data and checklist',async()=>{const h=assistSetup(),old=deferred();h.api.replaceLatest(()=>old.promise);const job=h.api.generateBriefing();h.api.aiInput('notes','notes','Novo contexto');old.resolve(briefReply());await job;assert.equal(h.api.state.assist.briefing.status,'idle');assert.equal(h.api.state.assist.briefing.key,null);});
test('checklist: progress belongs to the interview and exact context, and clears on logout',async()=>{const h=assistSetup();let job=h.api.generateBriefing();h.fetches[0].resolve(response({data:briefReply()}));await job;h.api.togglePrepCheck(0,true);const firstKey=h.api.state.assist.briefing.key;assert.deepEqual(Array.from(h.api.state.assist.briefing.checks[firstKey]),[0]);h.api.aiInput('notes','notes','Outro contexto');job=h.api.generateBriefing();h.fetches[1].resolve(response({data:briefReply()}));await job;assert.notEqual(h.api.state.assist.briefing.key,firstKey);assert.equal(h.api.state.assist.briefing.checks[h.api.state.assist.briefing.key].length,0);h.api.clearPrivateState();assert.equal(Object.keys(h.api.state.assist.briefing.checks).length,0);});
test('briefing: unavailable event asks for a current selection and never shows a fabricated checklist',async()=>{const h=assistSetup();const job=h.api.generateBriefing();h.fetches[0].resolve(response({data:{status:'needs_clarification',question:'Selecione uma entrevista atual.'}}));await job;assert.equal(h.api.state.assist.briefing.status,'clarification');assert.equal(h.api.state.assist.briefing.data,null);assert.doesNotMatch(h.api.briefingPanel(),/id="assist-check-title"/);});
test('preparation: a visibility recheck aborts model work without automatically retrying a POST',async()=>{const h=assistSetup(),old=deferred();h.api.replaceLatest(()=>old.promise);const job=h.api.generateSummary();h.api.suspendAssist();old.resolve(summaryReply());await job;assert.equal(h.api.state.assist.summary.status,'idle');assert.equal(h.api.state.assist.summary.data,null);});
test('preparation: verified CRUD clears outdated briefing, summary and slots without generating again',async()=>{const h=crudSetup();h.api.state.prepId='A';h.api.state.prepDetail=event('A');h.api.state.prepStatus='ready';for(const name of ['summary','availability','briefing']){h.api.state.assist[name].status='ready';h.api.state.assist[name].data={old:true};}await pendingPreview(h);const confirm=h.api.confirmMutation();h.fetches[1].resolve(response({data:operation('create','succeeded')}));await confirm;for(const name of ['summary','availability','briefing'])assert.equal(h.api.state.assist[name].data,null);assert.equal(h.api.state.prepDetail,null);assert.equal(h.api.state.prepStatus,'idle');assert.equal(h.fetches.length,2);});
test('preparation: an external change to the same event invalidates its briefing before showing updated details',async()=>{const h=assistSetup();h.api.state.assist.briefing.status='ready';h.api.state.assist.briefing.data=briefReply();const changed=calendar('A');changed.events[0].start_at='2026-10-27T15:00:00-03:00';changed.events[0].start='15:00';h.api.replaceLatest(()=>Promise.resolve(changed));await h.api.loadCalendar();assert.equal(h.api.state.prepId,'A');assert.equal(h.api.state.prepDetail,null);assert.equal(h.api.state.prepStatus,'idle');assert.equal(h.api.state.assist.briefing.data,null);});
test('preparation: repeated successful calendar reads do not regenerate summaries or briefing',async()=>{const h=assistSetup();h.api.state.assist.summary.status='ready';h.api.state.assist.summary.data=summaryReply();h.api.state.assist.briefing.status='ready';h.api.state.assist.briefing.data=briefReply();const calls=[];h.api.replaceLatest((name)=>{calls.push(name);return Promise.resolve(calendar('A'))});await h.api.loadCalendar();assert.deepEqual(calls,['calendar']);assert.equal(h.api.state.assist.summary.status,'ready');assert.equal(h.api.state.assist.briefing.status,'ready');});
test('summary: renderer emphasis becomes safe bold without literal WhatsApp markers or HTML injection',async()=>{const h=assistSetup();const job=h.api.generateSummary();h.fetches[0].resolve(response({data:summaryReply('factual',{summary:{text:'📅 *Resumo da semana*\n• *14h às 14h30* — <img src=x onerror=secret()>',mode:'factual',reason:'llm_unavailable'}})}));await job;const markup=h.api.summaryPanel();assert.match(markup,/<strong>Resumo da semana<\/strong>/);assert.match(markup,/<strong>14h às 14h30<\/strong>/);assert.doesNotMatch(markup,/\*Resumo|\*14h/);assert.match(markup,/&lt;img src=x onerror=secret\(\)&gt;/);assert.doesNotMatch(markup,/<img src=x/);});

const reminder=(id='a'.repeat(64),state='pending',extra={})=>({id,event_id:'event-A',title:'Entrevista de teste',start_at:'2026-10-28T18:00:00Z',due_at:'2026-10-28T17:00:00Z',time_zone:'America/Recife',offset_minutes:60,format:'card',format_is_planned:true,state,reason:'Motivo confirmado pelo registro.',updated_at:'2026-10-28T16:59:00Z',...extra});
const reminderPage=(items,extra={})=>({items,total:items.length,count:items.length,counts:{pending:items.length},counts_scope:'filtered',snapshot_at:'2026-10-28T17:30:00Z',next_cursor:null,complete:true,...extra});
const reminderReply=(item=reminder(),history=[{from_state:null,to_state:'pending',reason:'Aviso registrado.',at:'2026-10-28T16:00:00Z'}])=>({item,history,complete:true});
const tinyPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGEcAAAAASUVORK5CYII=','base64');
const pngResponse=(bytes=tinyPng,extra={})=>({ok:true,status:200,headers:{get(name){return name==='Content-Type'?'image/png':name==='Content-Length'?String(bytes.length):null}},async arrayBuffer(){return Uint8Array.from(bytes).buffer},...extra});
async function reminderSetup(item=reminder()){
  const h=harness();h.api.state.authenticated=true;h.api.state.timeZone='America/Recife';h.api.state.reminders={status:'ready',...reminderPage([item]),snapshotAt:'2026-10-28T17:30:00Z',nextCursor:null,error:''};
  const job=h.api.reminderDetail(item.id);h.fetches[0].resolve(response({data:reminderReply(item)}));await job;return h;
}

test('reminders: counts describe the complete filter and overdue labels use the server snapshot',async()=>{
  const h=harness();h.api.state.authenticated=true;const job=h.api.loadReminders();h.fetches[0].resolve(response({data:reminderPage([reminder()],{total:80,counts:{pending:80},next_cursor:'page-2'})}));await job;
  const markup=h.api.reminders();assert.match(markup,/80<\/strong><span>avisos neste filtro/);assert.match(markup,/1 registro carregado de 80 neste filtro/);assert.match(markup,/Horário previsto já passou/);assert.match(markup,/Consulta:/);assert.doesNotMatch(markup,/80 entregues|80 enviados/);
  h.api.state.reminders.snapshotAt='2026-10-28T16:30:00Z';assert.doesNotMatch(h.api.reminders(),/Horário previsto já passou/);
});

test('reminders: provider acceptance is never presented as delivery and all history includes cancelled records',()=>{
  const h=harness();h.api.state.reminderFilter='all';h.api.state.reminders={status:'ready',items:[reminder('a','accepted'),reminder('b','cancelled'),reminder('c','uncertain')],total:3,nextCursor:null,snapshotAt:'2026-10-28T18:00:00Z',counts:null,error:''};
  const markup=h.api.reminders();assert.match(markup,/Aceito pelo provedor/);assert.match(markup,/Não comprova entrega ou leitura/);assert.match(markup,/Cancelado/);assert.match(markup,/Resultado incerto/);assert.doesNotMatch(markup,/>Entregue<|data-action=".*(?:resend|retry-send)/);
});

test('reminders: a changed pagination snapshot preserves stale records and requires explicit refresh',async()=>{
  const h=harness();h.api.state.authenticated=true;h.api.state.reminders={status:'ready',items:[reminder()],nextCursor:'old',total:40,snapshotAt:'2026-10-28T17:00:00Z'};
  const job=h.api.loadReminders(true);h.fetches[0].resolve(response({status:'error',error:{code:'collection_changed',message:'Changed'}},409));await job;
  assert.equal(h.api.state.reminders.status,'error');assert.equal(h.api.state.reminders.items.length,1);assert.equal(h.api.state.reminders.nextCursor,null);assert.match(h.api.reminders(),/Atualizar lista/);assert.match(h.api.reminders(),/consulta anterior/);assert.equal(h.fetches.length,1);
});

test('reminders: repeated IDs across pages cannot silently duplicate a ledger row',async()=>{
  const h=harness();h.api.state.authenticated=true;h.api.state.reminders={status:'ready',items:[reminder()],nextCursor:'old'};const job=h.api.loadReminders(true);h.fetches[0].resolve(response({data:reminderPage([reminder()])}));await job;assert.equal(h.api.state.reminders.status,'error');assert.equal(h.api.state.reminders.items.length,1);assert.equal(h.api.state.reminders.nextCursor,null);
});

test('reminder history is chronological, escaped and separate from the current planned format',async()=>{
  const h=harness();h.api.state.authenticated=true;const job=h.api.reminderDetail('a');h.fetches[0].resolve(response({data:reminderReply(reminder('a','accepted'),[{to_state:'accepted',reason:'<script>secret()</script>',at:'2026-10-28T17:00:00Z'},{to_state:'pending',reason:'Primeiro registro',at:'2026-10-28T16:00:00Z'}])}));await job;
  const markup=h.elements.get('#sheet-content').innerHTML;assert.ok(markup.indexOf('Primeiro registro')<markup.indexOf('&lt;script&gt;'));assert.match(markup,/Formato previsto/);assert.match(markup,/não confirma entrega ou leitura/);assert.match(markup,/Prévia • não enviada/);assert.doesNotMatch(markup,/<script>/);
});

test('card preview: explicitly fetches private same-origin PNG without CSRF, API keys or sends',async()=>{
  const h=await reminderSetup();assert.equal(h.fetches.length,1);const job=h.api.previewReminderCard();const fetch=h.fetches[1];assert.equal(fetch.url,'/painel/api/reminders/'+'a'.repeat(64)+'/card-preview');assert.equal(fetch.options.method,'GET');assert.equal(fetch.options.credentials,'same-origin');assert.equal(fetch.options.redirect,'error');assert.equal(fetch.options.cache,'no-store');assert.equal(fetch.options.headers.Accept,'image/png');assert.equal(Object.keys(fetch.options.headers).length,1);
  fetch.resolve(pngResponse());await job;assert.match(h.api.getReminder().imageSrc,/^data:image\/png;base64,/);const markup=h.elements.get('#sheet-content').innerHTML;assert.match(markup,/data-reminder-image="true"/);assert.match(markup,/dados atuais/);assert.match(markup,/não é comprovante nem cópia/);assert.equal(h.api.getReminder().imageStatus,'ready');
});

test('card preview: JSON unavailability keeps meaningful text and history without a fake image',async()=>{
  const h=await reminderSetup();const job=h.api.previewReminderCard();h.fetches[1].resolve(response({status:'error',message:'O compromisso mudou; atualize o registro.'},409));await job;assert.equal(h.api.getReminder().imageStatus,'error');const markup=h.elements.get('#sheet-content').innerHTML;assert.match(markup,/O compromisso mudou/);assert.match(markup,/dados do aviso continuam disponíveis em texto/);assert.match(markup,/Histórico do aviso/);assert.doesNotMatch(markup,/data-reminder-image/);assert.equal(h.fetches.length,2);
});

test('card preview: a current 401 removes private image state, sheet DOM and calendar data',async()=>{
  const h=await reminderSetup();h.api.state.calendar.events=[event('private')];const job=h.api.previewReminderCard();h.fetches[1].resolve(response({status:'error'},401));await job;assert.equal(h.api.state.authenticated,false);assert.equal(h.api.getReminder(),null);assert.equal(h.elements.get('#sheet-content').innerHTML,'');assert.equal(h.api.state.calendar.events.length,0);
});

test('card preview: an old 401 cannot revoke a new authenticated session',async()=>{
  const h=await reminderSetup();const job=h.api.previewReminderCard();h.api.clearPrivateState();h.api.state.authenticated=true;h.api.state.authError='';h.fetches[1].resolve(response({status:'error'},401));await job;assert.equal(h.api.state.authenticated,true);assert.equal(h.api.state.authError,'');assert.equal(h.api.getReminder(),null);assert.equal(h.elements.get('#sheet-content').innerHTML,'');
});

test('card preview: a late body cannot restore private image data after logout',async()=>{
  const h=await reminderSetup(),body=deferred();const job=h.api.previewReminderCard();h.fetches[1].resolve(pngResponse(tinyPng,{arrayBuffer:()=>body.promise}));await Promise.resolve();await Promise.resolve();h.api.clearPrivateState();h.api.state.authenticated=true;body.resolve(Uint8Array.from(tinyPng).buffer);await job;assert.equal(h.api.getReminder(),null);assert.equal(h.elements.get('#sheet-content').innerHTML,'');
});

test('card preview: selecting another reminder aborts the image and ignores late success',async()=>{
  const h=await reminderSetup();const old=h.api.previewReminderCard();const next=h.api.reminderDetail('b');assert.equal(h.fetches[1].options.signal.aborted,true);h.fetches[2].resolve(response({data:reminderReply(reminder('b','blocked'))}));await next;h.fetches[1].resolve(pngResponse());await old;assert.equal(h.api.getReminder().item.id,'b');assert.equal(h.api.getReminder().imageSrc,'');assert.equal(h.api.getReminder().imageStatus,'idle');
});

test('card preview: closing details clears image memory and ignored abort cannot reopen it',async()=>{
  const h=await reminderSetup();const job=h.api.previewReminderCard();h.api.closeSheet();assert.equal(h.fetches[1].options.signal.aborted,true);assert.equal(h.elements.get('#sheet-content').innerHTML,'');h.fetches[1].resolve(pngResponse());await job;assert.equal(h.api.getReminder(),null);assert.equal(h.elements.get('#sheet').open,false);
});

test('card preview: refreshed changed records discard the image and ask to update details',async()=>{
  const h=await reminderSetup();let job=h.api.previewReminderCard();h.fetches[1].resolve(pngResponse());await job;job=h.api.loadReminders();h.fetches[2].resolve(response({data:reminderPage([reminder(undefined,'cancelled')])}));await job;assert.equal(h.api.getReminder().imageSrc,'');assert.equal(h.api.getReminder().stale,true);assert.match(h.elements.get('#sheet-content').innerHTML,/Atualizar detalhes/);await h.api.previewReminderCard();assert.equal(h.fetches.length,3);
});

test('card preview: native decode failure becomes text and an old image error cannot replace another detail',async()=>{
  const h=await reminderSetup();const job=h.api.previewReminderCard();h.fetches[1].resolve(pngResponse());await job;const old=h.api.getReminder().imageSrc,image={dataset:{reminderImage:'true'},getAttribute:()=>old};h.elements.set('[data-reminder-image="true"]',image);h.api.reminderImageFailed(image);assert.equal(h.api.getReminder().imageStatus,'error');assert.equal(h.api.getReminder().imageSrc,'');assert.match(h.elements.get('#sheet-content').innerHTML,/navegador não conseguiu exibir/);assert.doesNotMatch(h.elements.get('#sheet-content').innerHTML,/data-reminder-image/);
  h.api.reminderImageFailed({dataset:{reminderImage:'true'},getAttribute:()=>old});assert.equal(h.api.getReminder().imageStatus,'error');
});

test('card preview: text reminders and unverified formats never request an image',async()=>{
  for(const item of [reminder(undefined,'pending',{format:'text',offset_minutes:15}),reminder(undefined,'pending',{format_is_planned:false})]){const h=await reminderSetup(item);await h.api.previewReminderCard();assert.equal(h.fetches.length,1);assert.doesNotMatch(h.elements.get('#sheet-content').innerHTML,/data-action="preview-reminder-card"/);}
});

for(const [name,reply] of [
  ['wrong MIME',()=>pngResponse(tinyPng,{headers:{get:()=> 'text/html'}})],
  ['oversize declaration',()=>pngResponse(tinyPng,{headers:{get:name=>name==='Content-Type'?'image/png':String(6*1024*1024)}})],
  ['invalid signature',()=>pngResponse(Buffer.alloc(30))],
  ['unsafe dimensions',()=>{const bytes=Buffer.from(tinyPng);bytes.writeUInt32BE(9000,16);return pngResponse(bytes)}]
])test(`card preview: ${name} falls back to text`,async()=>{const h=await reminderSetup();const job=h.api.previewReminderCard();h.fetches[1].resolve(reply());await job;assert.equal(h.api.getReminder().imageStatus,'error');assert.equal(h.api.getReminder().imageSrc,'');assert.doesNotMatch(h.elements.get('#sheet-content').innerHTML,/data-reminder-image/);});

test('card preview: oversized streamed bytes are cancelled before another read',async()=>{
  const h=await reminderSetup();let reads=0,cancelled=false,released=false;const reader={async read(){reads++;return {done:false,value:new Uint8Array(5*1024*1024+1)}},async cancel(){cancelled=true},releaseLock(){released=true}};const job=h.api.previewReminderCard();h.fetches[1].resolve(pngResponse(tinyPng,{headers:{get:name=>name==='Content-Type'?'image/png':null},body:{getReader:()=>reader}}));await job;assert.equal(reads,1);assert.equal(cancelled,true);assert.equal(released,true);assert.equal(h.api.getReminder().imageStatus,'error');
});

test('card preview: hiding the page immediately clears the image and blocks a late render',async()=>{
  const h=await reminderSetup();const job=h.api.previewReminderCard();h.document.hidden=true;h.documentEvents.get('visibilitychange')();assert.equal(h.api.getReminder(),null);assert.equal(h.elements.get('#sheet-content').innerHTML,'');h.fetches[1].resolve(pngResponse());await job;assert.equal(h.api.getReminder(),null);
});

test('card preview: a detached image error is ignored even when the next preview has identical PNG bytes',async()=>{
  const h=await reminderSetup();let job=h.api.previewReminderCard();h.fetches[1].resolve(pngResponse());await job;const oldSrc=h.api.getReminder().imageSrc;const oldImage={dataset:{reminderImage:'true'},getAttribute:()=>oldSrc};
  job=h.api.reminderDetail('b');h.fetches[2].resolve(response({data:reminderReply(reminder('b'))}));await job;job=h.api.previewReminderCard();h.fetches[3].resolve(pngResponse());await job;h.elements.set('[data-reminder-image="true"]',{dataset:{reminderImage:'true'},getAttribute:()=>h.api.getReminder().imageSrc});
  h.api.reminderImageFailed(oldImage);assert.equal(h.api.getReminder().item.id,'b');assert.equal(h.api.getReminder().imageStatus,'ready');
});

test('reminders: hiding the page invalidates an unfinished detail instead of restoring private state',async()=>{
  const h=harness();h.api.state.authenticated=true;const job=h.api.reminderDetail('a');h.document.hidden=true;h.documentEvents.get('visibilitychange')();h.fetches[0].resolve(response({data:reminderReply(reminder('a'))}));await job;assert.equal(h.api.getReminder(),null);assert.equal(h.elements.get('#sheet-content').innerHTML,'');
});
