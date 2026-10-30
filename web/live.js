/* Connected panel. Server-bound previews and confirmations; no API keys or persistent browser storage. */
const API='/painel/api';
const routes=[{id:'visao-geral',label:'Visão geral',icon:'home'},{id:'agenda',label:'Agenda',icon:'calendar'},{id:'preparo',label:'Preparo',icon:'sparkles'},{id:'lembretes',label:'Lembretes',icon:'bell'}];
const paths={home:'m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',calendar:'M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2m3 10h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01',sparkles:'m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6ZM20 2v4m-2-2h4',bell:'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-8 13h4',arrow:'M5 12h14m-5-5 5 5-5 5',chevron:'m9 5 7 7-7 7',back:'m15 5-7 7 7 7',plus:'M12 5v14M5 12h14',x:'m6 6 12 12M6 18 18 6',clock:'M12 8v4l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0m-5 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',check:'m5 12 4 4L19 6',search:'m21 21-4.5-4.5M19 10a9 9 0 1 1-18 0 9 9 0 0 1 18 0',image:'M3 3h18v18H3ZM3 17l6-6 5 5 3-3 4 4M9 7h.01',info:'M12 11v6m0-10h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',alert:'m12 3 10 18H2Zm0 6v4m0 4h.01',refresh:'M3 11a9 9 0 0 1 16-6l2 3m0-6v6h-6M21 13a9 9 0 0 1-16 6l-2-3m0 6v-6h6',message:'M21 11a9 9 0 0 1-9 9H5l-4 3V11a10 10 0 0 1 20 0M6 10h10M6 14h7',logout:'M9 4H3v16h6m6-16 6 6-6 6m6-6H8',lock:'M6 10V7a6 6 0 0 1 12 0v3M4 10h16v12H4Zm8 5v3'};
const icon=(name,cls='')=>`<svg class="icon ${cls}" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.calendar}"/></svg>`;
const e=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const $=(selector,root=document)=>root.querySelector(selector);
const isoDate=value=>{if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;const date=new Date(value+'T12:00:00Z');return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===value;};
const state={route:readRoute(),authenticated:false,revalidating:false,checking:true,authError:'',pending:null,csrf:null,date:null,today:null,timeZone:null,view:'day',search:'',prepId:null,prepDetail:null,prepStatus:'idle',prepError:'',assist:freshAssist(),reminderFilter:'upcoming',calendar:{status:'idle',events:[],complete:false,error:'',collectedAt:null,name:''},reminders:{status:'idle',items:[],nextCursor:null,total:null,error:'',snapshotAt:null}};
const controllers=new Map();
const requestSequences=new Map();
const positions={};
let pendingPoll=null;
let pendingDeadline=0;
let toastTimer=null;
let detailSequence=0;
let authEpoch=0;
let revalidationPending=null;
let selectedDetail=null;
let selectedReminder=null;
let mutation=null;
let mutationSequence=0;
const page=$('#main-content');
const sheet=$('#sheet');
const statusMap={pending:['Pendente','violet'],reserved:['Preparando','violet'],sending:['Tentativa em andamento','violet'],accepted:['Aceito pelo provedor','mint'],blocked:['Bloqueado','amber'],uncertain:['Resultado incerto','amber'],cancelled:['Cancelado','neutral'],expired:['Prazo encerrado','neutral']};
const filterStates={upcoming:'pending,reserved,sending',accepted:'accepted',attention:'blocked,uncertain,expired',all:''};
const reminderDescriptions={pending:'Aguardando a verificação do sistema.',reserved:'Aviso reservado para preparação.',sending:'Há uma tentativa em andamento; o resultado ainda não está confirmado.',accepted:'O provedor aceitou o envio. Este registro não confirma entrega ou leitura.',blocked:'O envio foi bloqueado. Confira o motivo registrado.',uncertain:'Não há confirmação do resultado. Repetir o envio pode duplicar a mensagem.',cancelled:'Este aviso foi cancelado e não está pendente.',expired:'O prazo deste aviso encerrou. Não há um novo envio programado por este registro.'};
const CARD_BYTES_LIMIT=5*1024*1024;

class PanelError extends Error{constructor(message,status=0,data=null,code=null,fields=[]){super(message);this.status=status;this.data=data;this.code=code;this.fields=fields;}}
function unwrap(value){return value&&typeof value==='object'&&'data'in value?value.data:value;}
function readRoute(){return routes.some(route=>route.id===location.hash.slice(1))?location.hash.slice(1):'visao-geral';}
function installIcons(root=document){root.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});}
function humanTime(value){if(typeof value!=='string'||!/^\d{2}:\d{2}$/.test(value))return 'Horário não informado';const [hour,minute]=value.split(':');return `${Number(hour)}h${minute==='00'?'':minute}`;}
function civil(value){return new Date(value+'T12:00:00Z');}
function addDays(value,number){const date=civil(value);date.setUTCDate(date.getUTCDate()+number);return date.toISOString().slice(0,10);}
function weekDates(value){const day=civil(value).getUTCDay();const monday=addDays(value,-((day+6)%7));return Array.from({length:7},(_,index)=>addDays(monday,index));}
function monthDates(value){const monday=weekDates(value.slice(0,7)+'-01')[0];return Array.from({length:42},(_,index)=>addDays(monday,index));}
function formatDate(value,short=false){if(!isoDate(value))return 'Data não informada';return new Intl.DateTimeFormat('pt-BR',{day:'numeric',month:short?'short':'long',...(short?{}:{year:'numeric'}),timeZone:'UTC'}).format(civil(value)).replace(/\./g,'');}
function dateLabel(value){return value===state.today?'Hoje':state.today&&value===addDays(state.today,1)?'Amanhã':formatDate(value,true);}
function monthLabel(value){return new Intl.DateTimeFormat('pt-BR',{month:'long',year:'numeric',timeZone:'UTC'}).format(civil(value));}
function weekday(value){return new Intl.DateTimeFormat('pt-BR',{weekday:'short',timeZone:'UTC'}).format(civil(value)).replace('.','');}
function interval(event){const endDate=typeof event.end_at==='string'?event.end_at.slice(0,10):event.date;if(event.all_day)return isoDate(endDate)&&endDate>addDays(event.date,1)?`Dia inteiro · até ${formatDate(addDays(endDate,-1),true)}`:'Dia inteiro';return endDate!==event.date&&isoDate(endDate)?`${humanTime(event.start)} até ${formatDate(endDate,true)}, ${humanTime(event.end)}`:`${humanTime(event.start)} às ${humanTime(event.end)}`;}
function timestamp(value,zone=state.timeZone||state.reminders.items[0]?.time_zone){if(typeof value!=='string'||!Number.isFinite(Date.parse(value)))return 'Horário não informado';try{return new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:zone||'UTC'}).format(new Date(value))+(zone?'':' UTC');}catch{return 'Horário não informado';}}
function initials(title){return String(title||'Compromisso').trim().split(/\s+/).slice(0,2).map(word=>word[0]||'').join('').toUpperCase();}
function avatar(event){return `<span class="event-avatar violet" aria-hidden="true">${e(initials(event.title))}</span>`;}
function normalEvent(item){
  if(!item||typeof item!=='object'||typeof (item.id||item.event_id)!=='string'||typeof item.title!=='string'||!isoDate(item.date))throw new PanelError('A agenda retornou dados incompletos. Tente atualizar novamente.');
  return {...item,id:item.id||item.event_id};
}
function abort(name){controllers.get(name)?.abort();controllers.delete(name);requestSequences.set(name,(requestSequences.get(name)||0)+1);}
function stopAll(){for(const controller of controllers.values())controller.abort();controllers.clear();for(const [name,sequence] of requestSequences)requestSequences.set(name,sequence+1);clearTimeout(pendingPoll);pendingPoll=null;}
function clearReminderDetail(){abort('reminder-image');if(selectedReminder){selectedReminder.imageSrc='';selectedReminder=null;$('#sheet-content').replaceChildren();$('#sheet-footer').replaceChildren();}}
async function requestImage(id,signal){
  const epoch=authEpoch;
  const check=()=>{if(epoch!==authEpoch||!state.authenticated||signal?.aborted)throw new DOMException('Prévia substituída.','AbortError');};
  check();
  const response=await fetch(API+'/reminders/'+encodeURIComponent(id)+'/card-preview',{method:'GET',headers:{Accept:'image/png'},credentials:'same-origin',cache:'no-store',redirect:'error',signal});
  check();
  if(response.status===401){clearPrivateState();state.authError='Seu acesso terminou. Autorize este navegador novamente.';renderAccess();throw new PanelError('Seu acesso terminou.',401);}
  if(!response.ok){const raw=await response.text();check();let value;try{value=raw.length<120000?JSON.parse(raw):null;}catch{}const message=value?.message||value?.error?.message;throw new PanelError(typeof message==='string'?message.slice(0,220):'A imagem não está disponível. Os dados em texto continuam abaixo.',response.status);}
  if(response.headers?.get('Content-Type')?.split(';')[0].trim().toLowerCase()!=='image/png')throw new PanelError('O sistema não retornou uma imagem PNG válida.');
  const length=response.headers?.get('Content-Length');
  if(length&&(!/^\d+$/.test(length)||Number(length)>CARD_BYTES_LIMIT))throw new PanelError('A imagem excede o limite de exibição.');
  let bytes;
  if(response.body?.getReader){
    const reader=response.body.getReader(),chunks=[];let total=0;
    try{while(true){const part=await reader.read();check();if(part.done)break;total+=part.value.byteLength;if(total>CARD_BYTES_LIMIT)throw new PanelError('A imagem excede o limite de exibição.');chunks.push(part.value);}bytes=new Uint8Array(total);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}}
    catch(error){await reader.cancel().catch(()=>{});throw error;}
    finally{reader.releaseLock();}
  }else{bytes=new Uint8Array(await response.arrayBuffer());check();}
  if(bytes.length<24||bytes.length>CARD_BYTES_LIMIT||[137,80,78,71,13,10,26,10].some((byte,index)=>bytes[index]!==byte)||String.fromCharCode(...bytes.slice(12,16))!=='IHDR')throw new PanelError('Não foi possível confirmar a imagem. Consulte os dados em texto.');
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),width=view.getUint32(16),height=view.getUint32(20);
  if(!width||!height||width>2400||height>2400)throw new PanelError('As dimensões da imagem excedem o limite de exibição.');
  let binary='';for(let offset=0;offset<bytes.length;offset+=32768)binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
  check();return 'data:image/png;base64,'+btoa(binary);
}
async function request(path,{method='GET',body,signal,sessionRequest=false}={}){
  const epoch=authEpoch;
  const headers={Accept:'application/json'};
  if(body!==undefined)headers['Content-Type']='application/json';
  if(method!=='GET'&&method!=='HEAD'&&state.csrf)headers['X-Panel-CSRF']=state.csrf;
  const response=await fetch(API+path,{method,headers,credentials:'same-origin',cache:'no-store',redirect:'error',signal,...(body===undefined?{}:{body:JSON.stringify(body)})});
  const raw=await response.text();
  if(epoch!==authEpoch)throw new DOMException('Acesso substituído.','AbortError');
  if(raw.length>1200000)throw new PanelError('A consulta retornou dados além do limite de exibição.');
  let value;try{value=JSON.parse(raw);}catch{throw new PanelError('A resposta do sistema não pôde ser confirmada.',response.status);}
  if(epoch!==authEpoch)throw new DOMException('Acesso substituído.','AbortError');
  if(response.status===401&&!sessionRequest){clearPrivateState();state.authError='Seu acesso terminou. Autorize este navegador novamente.';renderAccess();throw new PanelError('Seu acesso terminou.',401);}
  if(!response.ok||value?.status==='failed'||value?.status==='error'){
    const message=typeof value?.message==='string'?value.message:typeof value?.error?.message==='string'?value.error.message:null;
    throw new PanelError(message?.slice(0,220)||'Não foi possível concluir esta consulta. Tente novamente.',response.status,unwrap(value),value?.error?.code,value?.error?.fields||[]);
  }
  const data=unwrap(value);if(!data||typeof data!=='object')throw new PanelError('A resposta do sistema está incompleta.');return data;
}
async function latest(name,path,options={}){
  const epoch=authEpoch;
  abort(name);const controller=new AbortController();controllers.set(name,controller);
  const sequence=(requestSequences.get(name)||0)+1;requestSequences.set(name,sequence);
  let timedOut=false;
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},40000);
  try{const result=await request(path,{...options,signal:controller.signal});if(epoch!==authEpoch||requestSequences.get(name)!==sequence)throw new DOMException('Consulta substituída.','AbortError');return result;}
  catch(error){if(epoch!==authEpoch||requestSequences.get(name)!==sequence)throw new DOMException('Consulta substituída.','AbortError');if(timedOut)throw new PanelError('A consulta demorou mais que o esperado. Tente atualizar novamente.',408);throw error;}
  finally{clearTimeout(timer);if(controllers.get(name)===controller)controllers.delete(name);}
}
function clearPrivateState(){
  clearReminderDetail();mutationSequence++;mutation=null;selectedDetail=null;state.assist=freshAssist();
  authEpoch++;stopAll();detailSequence++;state.authenticated=false;state.revalidating=false;state.csrf=null;state.pending=null;state.date=null;state.today=null;state.timeZone=null;state.prepId=null;state.prepDetail=null;state.prepStatus='idle';state.prepError='';state.search='';state.calendar={status:'idle',events:[],complete:false,error:'',collectedAt:null,name:''};state.reminders={status:'idle',items:[],nextCursor:null,total:null,error:'',snapshotAt:null};if(sheet.open)sheet.close();$('#sheet-content').replaceChildren();page.replaceChildren();$("#sheet-footer").replaceChildren();$("#calendar-name").textContent="Acesso local protegido";$("#toast").textContent="";$("#toast").classList.remove("show");
}
function commandFor(code){return /^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code)?`.\\.venv\\Scripts\\python.exe -m app.panel_access authorize ${code}`:null;}
function renderAccess(){
  $('#app-root').hidden=true;$('#auth-root').hidden=false;
  const pending=state.pending;const command=pending?commandFor(pending.pairing_code||''):null;
  const content=state.checking?`<span class="eyebrow">ACESSO PROTEGIDO</span><h2 id="access-title" tabindex="-1">Conferindo seu acesso.</h2><p>Um instante enquanto verificamos este navegador.</p><div class="pairing-status" role="status"><span class="status-dot"></span>Conectando ao painel…</div>`:pending&&command?`<span class="eyebrow">AUTORIZAR ESTE NAVEGADOR</span><h2 id="access-title" tabindex="-1">Quase lá.</h2><p>No computador do projeto, autorize este código para abrir sua agenda nesta aba.</p><code class="pairing-code">${e(pending.pairing_code)}</code><p>Na pasta do projeto, execute:</p><pre class="pairing-command">${e(command)}</pre><button class="button secondary" data-action="copy-command">Copiar comando</button><div class="pairing-status" role="status"><span class="status-dot"></span>Aguardando sua autorização…</div><p class="access-hint">O código vale por até 5 minutos. Suas chaves de integração continuam no servidor.</p><button class="text-button" data-action="restart-pairing">Conferir autorização novamente</button>`:`<span class="eyebrow">ACESSO PROTEGIDO</span><h2 id="access-title" tabindex="-1">Entre no seu espaço.</h2><p>Autorize este navegador no computador do projeto. Sua agenda só será consultada depois dessa autorização.</p>${state.authError?`<div class="access-error" role="alert">${e(state.authError)}</div>`:''}<button class="button primary" data-action="begin-pairing">${icon('lock')} Acessar minha agenda</button><p class="access-hint">Você também pode explorar a demonstração com exemplos, sem acessar dados reais.</p>`;
  $('#auth-root').innerHTML=`<header class="access-top"><a class="brand" href="./" aria-label="Genius Agenda AI"><img class="brand-mark" src="assets/mark.svg" width="32" height="32" alt=""><span><span class="brand-name">genius<span class="brand-point">.</span></span><span class="brand-sub">AGENDA AI</span></span></a><a class="text-button" href="demo/">Ver demonstração ${icon('arrow')}</a></header><div class="access-layout"><div class="access-intro"><h1>Sua agenda.<br><span>Só sua.</span></h1><p>As mesmas conversas do WhatsApp, com espaço para enxergar o que vem pela frente.</p><div class="access-image"><img src="assets/interview-background.png" alt="Mesa de trabalho com notebook e plantas"></div></div><section class="access-card" aria-labelledby="access-title">${content}</section></div>`;
  document.title='Acesso protegido · Genius Agenda AI';
}
async function session({quiet=false}={}){
  const epoch=authEpoch;
  const data=await latest('session','/session',{sessionRequest:true});
  if(epoch!==authEpoch)return false;
  state.revalidating=false;
  if(data.authenticated===true){
    if(typeof data.csrf_token!=='string'||data.csrf_token.length<16)throw new PanelError('Não foi possível confirmar o acesso protegido. Tente novamente.');
    const first=!state.authenticated;if(first)authEpoch++;state.authenticated=true;state.checking=false;state.authError='';state.pending=null;state.csrf=data.csrf_token;clearTimeout(pendingPoll);pendingPoll=null;$('#auth-root').hidden=true;$('#app-root').hidden=false;
    render();if(first)await Promise.allSettled([loadCalendar(null,'day'),loadReminders()]);return true;
  }
  if(state.authenticated)clearPrivateState();
  state.checking=false;
  if(data.pending===true&&typeof data.pairing_code==='string'&&commandFor(data.pairing_code)){
    const changed=state.pending?.pairing_code!==data.pairing_code;
    state.pending=data;if(!pendingDeadline)pendingDeadline=Math.min(Number.isFinite(Date.parse(data.expires_at))?Date.parse(data.expires_at):Infinity,Date.now()+300000);
    if(!quiet||changed)renderAccess();schedulePairing();
  }else{state.pending=null;pendingDeadline=0;clearTimeout(pendingPoll);pendingPoll=null;renderAccess();}return false;
}
async function revalidateSession({force=false}={}){
  if(revalidationPending)return revalidationPending;
  if(!state.authenticated&&!force)return false;
  const interrupted={calendar:state.calendar.status==='loading',reminders:state.reminders.status==='loading',preparation:state.prepStatus==='loading'};
  clearReminderDetail();suspendAssist();suspendMutation();authEpoch++;stopAll();detailSequence++;if(sheet.open)sheet.close();
  if(interrupted.calendar)state.calendar.status=state.calendar.complete?'ready':'idle';
  if(interrupted.reminders)state.reminders.status=state.reminders.snapshotAt?'ready':'idle';
  if(interrupted.preparation)state.prepStatus=state.prepDetail?'ready':'idle';
  const epoch=authEpoch;state.revalidating=true;state.checking=true;$('#app-root').hidden=true;renderAccess();
  const task=(async()=>{try{const valid=await session();if(valid&&epoch===authEpoch&&state.authenticated)await Promise.allSettled([interrupted.calendar?loadCalendar():null,interrupted.reminders?loadReminders():null,interrupted.preparation&&!interrupted.calendar&&state.prepId?loadPrep(state.prepId):null]);return valid;}catch(error){if(error.name==='AbortError'||epoch!==authEpoch)return false;clearPrivateState();state.checking=false;state.authError='Não foi possível confirmar seu acesso. Tente novamente.';renderAccess();return false;}})();
  revalidationPending=task;
  try{return await task;}finally{if(revalidationPending===task)revalidationPending=null;}
}
function schedulePairing(){clearTimeout(pendingPoll);if(!state.pending)return;if(Date.now()>=pendingDeadline){state.pending=null;state.authError='O código expirou. Gere outro para continuar.';renderAccess();return;}pendingPoll=setTimeout(async()=>{if(document.hidden){schedulePairing();return;}try{await session({quiet:true});}catch(error){if(error.name!=='AbortError'){state.pending=null;state.authError='Não foi possível conferir a autorização. Tente gerar outro código.';renderAccess();}}},2000);}
async function beginPairing(){
  clearPrivateState();state.checking=true;state.authError='';renderAccess();
  try{const data=await latest('session','/auth/start',{method:'POST',body:{},sessionRequest:true});state.checking=false;if(data.authenticated===true){await session();return;}if(!commandFor(data.pairing_code||''))throw new PanelError('Não foi possível preparar o acesso. Tente novamente.');state.pending=data;pendingDeadline=Math.min(Number.isFinite(Date.parse(data.expires_at))?Date.parse(data.expires_at):Infinity,Date.now()+300000);renderAccess();schedulePairing();}
  catch(error){if(error.name==='AbortError')return;state.checking=false;state.pending=null;state.authError=error.message||'Não foi possível preparar o acesso.';renderAccess();}
}
async function logout(){
  try{await latest('logout','/auth/logout',{method:'POST',body:{},sessionRequest:true});clearPrivateState();state.checking=false;state.authError='Você saiu deste navegador.';renderAccess();}
  catch(error){if(error.name!=='AbortError')toast('Não foi possível confirmar a saída. Tente novamente.');}
}

async function loadCalendar(date=state.date,period=state.view){
  if(!state.authenticated)return;const epoch=authEpoch;state.calendar.status='loading';state.calendar.error='';render();
  const query=new URLSearchParams({period});if(date)query.set('date',date);
  try{
    const data=await latest('calendar','/events?'+query);
    if(!state.authenticated||epoch!==authEpoch)return;
    if(!Array.isArray(data.events)||data.complete!==true||!isoDate(data.reference_date)||typeof data.time_zone!=='string')throw new PanelError('A agenda retornou uma consulta incompleta.');
    new Intl.DateTimeFormat('pt-BR',{timeZone:data.time_zone});
    const selectedNow=data.events.find(item=>(item.id||item.event_id)===state.prepId);
    if(state.prepDetail&&selectedNow&&['title','start_at','end_at','all_day','status'].some(key=>state.prepDetail[key]!==selectedNow[key])){invalidateBriefing();abort('preparation');state.prepDetail=null;state.prepStatus='idle';}
    state.calendar={status:'ready',events:data.events.map(normalEvent),complete:data.complete===true,error:'',collectedAt:data.collected_at||null,name:typeof data.calendar_name==='string'?data.calendar_name:''};
    state.date=data.reference_date;if(isoDate(data.today))state.today=data.today;else if(!date&&!state.today)state.today=data.reference_date;state.timeZone=data.time_zone;state.view=period;
    if(!state.prepId||!state.calendar.events.some(item=>item.id===state.prepId)){invalidateBriefing();abort('preparation');state.prepId=state.calendar.events[0]?.id||null;state.prepDetail=null;state.prepStatus='idle';}
    render();if(state.route==='preparo'&&state.prepId&&state.prepStatus==='idle')loadPrep(state.prepId);
  }catch(error){if(error.name==='AbortError')return;if(!state.authenticated||epoch!==authEpoch)return;state.calendar.status='error';state.calendar.error=error.message||'Não foi possível atualizar sua agenda.';render();}
}
async function loadReminders(append=false){
  if(!state.authenticated)return;const epoch=authEpoch;state.reminders.status='loading';state.reminders.error='';render();
  const query=new URLSearchParams({limit:'25'});if(filterStates[state.reminderFilter])query.set('states',filterStates[state.reminderFilter]);if(append&&state.reminders.nextCursor)query.set('cursor',state.reminders.nextCursor);
  try{
    const data=await latest('reminders','/reminders?'+query);if(!state.authenticated||epoch!==authEpoch)return;
    if(!Array.isArray(data.items)||data.items.length>50||data.complete!==true)throw new PanelError('Os avisos retornaram dados incompletos.');
    const pageItems=data.items.map(normalReminder),items=append?[...state.reminders.items,...pageItems]:pageItems;
    if(new Set(items.map(item=>item.id)).size!==items.length)throw new PanelError('A lista mudou durante a consulta. Atualize para começar uma nova consulta.',409,null,'collection_changed');
    const counts=data.counts_scope==='filtered'&&data.counts&&Object.entries(data.counts).every(([key,value])=>key in statusMap&&Number.isSafeInteger(value)&&value>=0)&&Object.values(data.counts).reduce((sum,value)=>sum+value,0)===data.total?data.counts:null;
    if(selectedReminder){const changed=pageItems.find(item=>item.id===selectedReminder.item.id);if(changed&&reminderVersion(changed)!==reminderVersion(selectedReminder.item)||!append&&!pageItems.some(item=>item.id===selectedReminder.item.id)){abort('reminder-image');selectedReminder.imageSrc='';selectedReminder.imageStatus='stale';selectedReminder.stale=true;renderReminderDetail();}}
    state.reminders={status:'ready',items,nextCursor:typeof data.next_cursor==='string'?data.next_cursor:null,total:Number.isSafeInteger(data.total)&&data.total>=0?data.total:null,counts,error:'',snapshotAt:data.snapshot_at||null,complete:true};render();
  }catch(error){if(error.name==='AbortError')return;if(!state.authenticated||epoch!==authEpoch)return;state.reminders.status='error';state.reminders.error=error.code==='collection_changed'?'A lista mudou durante a consulta. Atualize para carregar os registros atuais.':error.message||'Não foi possível atualizar os avisos.';if(error.code==='collection_changed'){state.reminders.nextCursor=null;if(selectedReminder){abort('reminder-image');selectedReminder.imageSrc='';selectedReminder.imageStatus='stale';selectedReminder.stale=true;renderReminderDetail();}}render();}
}
async function loadPrep(id){
  if(!state.authenticated)return;invalidateBriefing();const epoch=authEpoch;state.prepId=id;state.prepDetail=null;state.prepStatus='loading';state.prepError='';render();
  try{const result=await latest('preparation','/events/'+encodeURIComponent(id));if(!state.authenticated||epoch!==authEpoch||state.prepId!==id)return;if(!result.event)throw new PanelError('Não foi possível confirmar este compromisso.');state.prepDetail=normalEvent(result.event);state.prepStatus='ready';render();}
  catch(error){if(error.name==='AbortError')return;if(!state.authenticated||epoch!==authEpoch||state.prepId!==id)return;state.prepStatus='error';state.prepError=error.message||'Não foi possível carregar os detalhes.';render();}
}
function button(label,action,extra='',kind='secondary'){return `<button class="button ${kind}" data-action="${action}" ${extra}>${label}</button>`;}
function header(kicker,title,subtitle,actions=''){return `<div class="page-header"><div><p class="eyebrow">${kicker}</p><h1 class="page-title">${title}</h1><p class="page-subtitle">${subtitle}</p></div>${actions?`<div class="header-actions">${actions}</div>`:''}</div>`;}
function readOnlyNotice(){return `<div class="live-note">${icon('info')}<span>Marque pelo WhatsApp ou por aqui. Os dois caminhos usam a mesma agenda.</span></div>`;}
function disabledNew(){return `<button class="button primary" data-action="new-mutation" aria-label="Novo compromisso">${icon('plus')}<span>Novo compromisso</span></button>`;}
function loading(compact=false){return `<div class="loading-state ${compact?'live-empty-compact':''}" role="status"><span class="screen-reader-only">Consultando dados…</span><div class="skeleton skeleton-line"></div>${[1,2,3].map(()=>'<div class="skeleton skeleton-row"></div>').join('')}</div>`;}
function empty(title,text,action='',label=''){return `<div class="empty-state"><span class="empty-icon">${icon('calendar')}</span><h3>${e(title)}</h3><p>${e(text)}</p>${action?button(label,action):''}</div>`;}
function errorBlock(message,action='refresh'){return `<div class="error-state" role="alert">${icon('alert')}<h2>Não foi possível atualizar.</h2><p>${e(message)}</p>${button(`${icon('refresh')} Tentar novamente`,action)}</div>`;}
function updateNotice(){return state.calendar.collectedAt?`<p class="live-update-note">Consulta da agenda: ${e(timestamp(state.calendar.collectedAt))}</p>`:'';}
function staleNotice(resource){return resource.status==='error'&&resource.items?.length||resource.status==='error'&&resource.events?.length?`<div class="stale-notice">${icon('alert')}<span>${e(resource.error)} Os dados anteriores abaixo podem estar desatualizados.</span>${button('Atualizar','refresh','','small secondary')}</div>`:'';}
function sortedEvents(){return [...state.calendar.events].sort((a,b)=>(a.date+(a.start||'')).localeCompare(b.date+(b.start||'')));}
function occursOn(event,date){const endDate=typeof event.end_at==='string'?event.end_at.slice(0,10):event.date;if(event.all_day)return event.date<=date&&date<endDate;return event.date<=date&&date<=endDate&&!(endDate===date&&event.end==='00:00'&&event.date!==date);}
function dayEvents(date){return sortedEvents().filter(event=>occursOn(event,date));}
function eventRow(event){return `<button class="event-row" data-action="event" data-id="${e(event.id)}" aria-label="Abrir ${e(event.title)}, ${e(dateLabel(event.date))}, ${e(interval(event))}"><span class="event-time"><strong>${event.all_day?'Dia':e(humanTime(event.start))}</strong><span>${event.all_day?'inteiro':e(humanTime(event.end))}</span></span>${avatar(event)}<span class="event-info"><span class="event-title">${e(event.title)}</span><span class="event-meta">${event.status==='tentative'?'Provisório':'Compromisso confirmado'}${event.location?` · ${e(event.location)}`:''}</span></span>${icon('chevron','row-arrow')}</button>`;}
function weekStrip(){if(!state.date)return '';return `<div class="week-strip" aria-label="Escolher dia da semana">${weekDates(state.date).map(date=>`<button class="day-chip ${date===state.date?'is-selected':''}" data-action="select-day" data-date="${date}" aria-pressed="${date===state.date}" aria-label="${e(weekday(date))}, ${e(formatDate(date))}"><span>${e(weekday(date))}</span><strong>${Number(date.slice(8))}</strong><i aria-hidden="true"></i></button>`).join('')}</div>`;}
function calendarContent(){
  const calendar=state.calendar;if(calendar.status==='idle'||calendar.status==='loading'&&!calendar.events.length)return loading();
  if(calendar.status==='error'&&!calendar.events.length)return errorBlock(calendar.error);
  const folded=state.search.normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase();const events=sortedEvents().filter(event=>event.title.normalize('NFD').replace(/\p{Diacritic}/gu,'').toLowerCase().includes(folded));
  if(state.view==='month')return monthCalendar(events);
  if(state.view==='week')return weekCalendar(events);
  return `<div class="section-heading agenda-list-heading"><span class="eyebrow">${e(formatDate(state.date))}</span><span class="quiet-note">${events.length} ${calendar.complete?'compromissos':'carregados'}</span></div><div class="event-list">${events.length?events.map(eventRow).join(''):empty(state.search?'Nenhum título encontrado.':'Um respiro na agenda.',state.search?'Tente outra pesquisa neste período.':'Não há compromissos neste dia.')}</div>`;
}
function agenda(){return header('CADA CONVERSA TEM SEU LUGAR','Sua agenda.','A mesma agenda do WhatsApp, em uma visão mais ampla.',disabledNew())+readOnlyNotice()+staleNotice(state.calendar)+`<section class="agenda-surface"><div class="toolbar"><div class="period-controls"><button class="icon-button" data-action="previous" aria-label="Período anterior" ${state.date?'':'disabled'}>${icon('back')}</button><h2>${state.date?(state.view==='month'?e(monthLabel(state.date)):state.view==='week'?`Semana de ${e(formatDate(weekDates(state.date)[0],true))}`:e(dateLabel(state.date))):'Consultando…'}</h2><button class="icon-button" data-action="next" aria-label="Próximo período" ${state.date?'':'disabled'}>${icon('chevron')}</button>${button('Hoje','today','','ghost small')}</div><div class="segmented" aria-label="Visualização da agenda">${[['day','Dia'],['week','Semana'],['month','Mês']].map(([value,label])=>`<button class="${state.view===value?'is-active':''}" data-action="agenda-view" data-value="${value}" aria-pressed="${state.view===value}">${label}</button>`).join('')}</div></div><label class="search-field">${icon('search')}<span class="screen-reader-only">Pesquisar neste período</span><input id="agenda-search" type="search" placeholder="Pesquisar neste período" value="${e(state.search)}" autocomplete="off"></label>${state.view==='day'?weekStrip():''}<div id="agenda-results">${calendarContent()}</div>${updateNotice()}</section>`;}
function monthCalendar(events){if(!state.date)return loading();return `<div class="calendar-weekdays">${['SEG','TER','QUA','QUI','SEX','SÁB','DOM'].map(day=>`<span>${day}</span>`).join('')}</div><div class="month-grid">${monthDates(state.date).map(date=>`<div class="month-cell ${date.slice(0,7)!==state.date.slice(0,7)?'is-outside':''} ${date===state.today?'is-today':''}"><button class="month-day" data-action="month-day" data-date="${date}" aria-label="Ver ${e(formatDate(date))}">${Number(date.slice(8))}</button>${events.filter(event=>occursOn(event,date)).map(event=>`<button class="calendar-event accent-violet" data-action="event" data-id="${e(event.id)}"><strong>${event.all_day?'Dia inteiro':e(humanTime(event.start))}</strong><span>${e(event.title)}</span></button>`).join('')}</div>`).join('')}</div>`;}
function weekCalendar(events){if(!state.date)return loading();return `<div class="week-grid">${weekDates(state.date).map(date=>`<section class="week-column ${date===state.today?'is-today':''}"><div class="week-column-header"><span>${e(weekday(date))}</span><strong>${Number(date.slice(8))}</strong></div>${events.filter(event=>occursOn(event,date)).map(event=>`<button class="week-event accent-violet" data-action="event" data-id="${e(event.id)}"><span>${e(interval(event))}</span><strong>${e(event.title)}</strong>${event.status==='tentative'?'<small>Provisório</small>':''}</button>`).join('')||'<span class="week-free">Sem eventos</span>'}</section>`).join('')}</div>`;}
function overview(){
  let events=sortedEvents();const daily=state.date?events.filter(event=>occursOn(event,state.date)):events;
  const next=daily.find(event=>state.date>state.today||state.date===state.today&&(event.all_day||Date.parse(event.end_at)>Date.now()));
  let content=state.calendar.status==='loading'&&!events.length||state.calendar.status==='idle'?loading():state.calendar.status==='error'&&!events.length?errorBlock(state.calendar.error):`${staleNotice(state.calendar)}${next?`<article class="hero-card"><div class="hero-content"><span class="hero-eyebrow"><span class="status-dot"></span> PRÓXIMO COMPROMISSO NESTE DIA</span><div class="hero-time">${next.all_day?'Dia inteiro':e(humanTime(next.start))}</div><h2 class="hero-title">${e(next.title)}</h2><p class="hero-meta">${e(dateLabel(next.date))} · ${e(interval(next))}</p><div class="hero-actions">${button(`Ver detalhes ${icon('arrow')}`,'event',`data-id="${e(next.id)}"`,'primary')}${button('Preparar','prepare',`data-id="${e(next.id)}"`,'secondary')}</div></div><div class="hero-visual"><img src="assets/interview-background.png" alt="Mesa de trabalho com notebook e plantas"><span class="image-label">Um espaço para chegar com clareza.</span></div></article>`:empty(daily.length?'Os compromissos deste dia já passaram.':'Seu dia está livre.',daily.length?'Você pode consultar os detalhes ou escolher outra data.':'Consulte outro dia para ver o que vem pela frente.','week-agenda','Ver a semana')}<section><div class="section-heading"><div><h2>Seu calendário</h2><p>${state.date?e(monthLabel(state.date)):''}</p></div><button class="text-button" data-action="week-agenda">Ver agenda ${icon('arrow')}</button></div>${weekStrip()}</section><section><div class="section-heading"><h2>${state.date?e(dateLabel(state.date)):'Compromissos'} <span class="count-badge">${daily.length}</span></h2><span class="quiet-note">${state.date?e(formatDate(state.date,true)):''}</span></div><div class="event-list">${daily.map(eventRow).join('')||'<p class="quiet-note">Nenhum compromisso neste dia.</p>'}</div>${updateNotice()}</section>`;
  return header('SEU ESPAÇO, NO SEU RITMO','Seu dia, com clareza.','As mesmas conversas. Uma nova forma de acompanhar.',disabledNew())+`<div class="overview-grid"><div class="content-stack">${content}</div><aside class="aside-panel"><section class="mini-card"><div class="mini-card-heading"><span class="eyebrow">DOIS CAMINHOS, UMA AGENDA</span>${icon('message')}</div><h2>Continue a conversa<br>pelo WhatsApp.</h2><p>Os compromissos criados pelo assistente aparecem aqui após atualizar a consulta.</p><p class="readonly-caption">Você também pode marcar e alterar pelo painel. Sempre confira a prévia antes de confirmar.</p></section><section class="mini-card reminder-preview"><div class="section-heading"><h2>Lembretes</h2>${icon('bell')}</div><p>Veja os avisos registrados, o estado do envio e os itens que precisam de atenção.</p><button class="text-button" data-action="notifications">Acompanhar avisos ${icon('arrow')}</button>${state.reminders.status==='error'?`<p class="quiet-note">Os avisos não puderam ser atualizados.</p>`:''}</section><div class="small-signature">Menos ruído.<br><strong>Mais espaço para você.</strong></div></aside></div>`;
}

function freshAssist(){return {tab:'briefing',summary:{period:'week',date:'',status:'idle',data:null,error:'',errors:{},version:0},availability:{initialized:false,form:{date_from:'',date_to:'',window_start:'09:00',window_end:'18:00',duration_minutes:'30',buffer_minutes:'0'},status:'idle',data:null,error:'',errors:{},version:0},briefing:{status:'idle',data:null,error:'',errors:{},version:0,notes:Object.create(null),checks:Object.create(null),key:null}};}
function assistDefaults(){const a=state.assist,date=state.date||state.today;if(isoDate(date)){if(!a.summary.date)a.summary.date=date;if(!a.availability.initialized){a.availability.form.date_from=date;a.availability.form.date_to=date;a.availability.initialized=true;}}return a;}
function invalidateBriefing(){const b=state.assist.briefing;b.version++;abort('assist-briefing');b.status='idle';b.data=null;b.error='';b.errors={};b.key=null;}
function suspendAssist(){for(const name of ['summary','availability','briefing']){const resource=state.assist[name];if(resource.status==='loading'){resource.version++;abort('assist-'+name);resource.status='idle';resource.data=null;resource.error='';resource.errors={};}}}
function invalidateAssistResults(){for(const name of ['summary','availability','briefing']){const r=state.assist[name];r.version++;abort('assist-'+name);r.status='idle';r.data=null;r.error='';r.errors={};}state.assist.briefing.key=null;abort('preparation');state.prepDetail=null;state.prepStatus='idle';state.prepError='';}
function currentAssist(resource,version,epoch,root){return state.authenticated&&authEpoch===epoch&&state.assist===root&&resource.version===version;}
function aiInput(kind,field,value){
  const a=assistDefaults();if(kind==='notes'){if(!state.prepId)return;a.briefing.notes[state.prepId]=value;invalidateBriefing();}
  else if(kind==='summary'&&['date','period'].includes(field)){a.summary[field]=value;a.summary.version++;abort('assist-summary');Object.assign(a.summary,{status:'idle',data:null,error:'',errors:{}});}
  else if(kind==='availability'&&Object.hasOwn(a.availability.form,field)){a.availability.form[field]=value;a.availability.version++;abort('assist-availability');Object.assign(a.availability,{status:'idle',data:null,error:'',errors:{}});}
  const active=document.activeElement,id=active?.id,position=active?.selectionStart;render();if(id){const input=$('#'+CSS.escape(id));input?.focus({preventScroll:true});if(Number.isInteger(position))try{input?.setSelectionRange?.(position,position);}catch{}}
}
function assistError(resource,error){resource.status='error';resource.data=null;resource.error=error.message||'Não foi possível concluir. Tente novamente.';resource.errors={};for(const field of error.fields||[]){if(typeof field==='string')resource.errors[field]=resource.error;}}
async function generateSummary(){
  if(!state.authenticated)return;const root=assistDefaults(),r=root.summary;if(r.status==='loading')return;
  r.errors={};if(!isoDate(r.date))r.errors.date='Escolha uma data válida.';if(!['week','month'].includes(r.period))r.errors.period='Escolha semana ou mês.';
  if(Object.keys(r.errors).length){r.error='Confira o período do resumo.';r.status='error';render();return;}
  const body={period:r.period,date:r.date},version=++r.version,epoch=authEpoch;r.status='loading';r.data=null;r.error='';render();
  try{const data=await latest('assist-summary','/agenda/summary',{method:'POST',body});if(!currentAssist(r,version,epoch,root))return;
    if(!data?.summary||!['ai_organized','factual'].includes(data.summary.mode)||typeof data.summary.text!=='string'||data.summary.text.length>4000||!data.period||data.period.kind!==body.period||data.period.reference_date!==body.date||!Number.isSafeInteger(data.totals?.events)||data.totals.events<0||data.snapshot?.complete!==true||data.execution_allowed!==false)throw new PanelError('O resumo retornou dados incompletos. Tente novamente.');
    r.data=data;r.status='ready';render();
  }catch(error){if(error.name==='AbortError'||!currentAssist(r,version,epoch,root))return;assistError(r,error);render();}
}
async function findAvailability(){
  if(!state.authenticated)return;const root=assistDefaults(),r=root.availability;if(r.status==='loading')return;const f=r.form;r.errors={};
  if(!isoDate(f.date_from))r.errors.date_from='Escolha uma data válida.';if(!isoDate(f.date_to)||f.date_to<f.date_from)r.errors.date_to='O fim precisa ser na mesma data ou depois.';
  if(isoDate(f.date_from)&&isoDate(f.date_to)&&(civil(f.date_to)-civil(f.date_from))/86400000>30)r.errors.date_to='Consulte até 31 dias por vez.';
  if(!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(f.window_start))r.errors.window_start='Informe um horário válido.';
  if(!/^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$/.test(f.window_end)||f.window_end<=f.window_start)r.errors.window_end='O fim precisa ser depois do início.';
  const duration=Number(f.duration_minutes),buffer=Number(f.buffer_minutes);if(!/^\d+$/.test(f.duration_minutes)||!Number.isSafeInteger(duration)||duration<1||duration>1440)r.errors.duration_minutes='Use de 1 a 1.440 minutos.';
  if(!/^\d+$/.test(f.buffer_minutes)||!Number.isSafeInteger(buffer)||buffer<0||buffer>240)r.errors.buffer_minutes='Use de 0 a 240 minutos.';
  if(Object.keys(r.errors).length){r.status='error';r.data=null;r.error='Confira os campos destacados.';render();return;}
  const body={...f,duration_minutes:duration,buffer_minutes:buffer},version=++r.version,epoch=authEpoch;r.status='loading';r.data=null;r.error='';render();
  try{const data=await latest('assist-availability','/agenda/availability',{method:'POST',body});if(!currentAssist(r,version,epoch,root))return;
    if(!Array.isArray(data?.slots)||data.slots.length>3||data.count!==data.slots.length||data.snapshot?.complete!==true||data.reservation_created!==false||data.execution_allowed!==false||!data.request||Object.keys(body).some(key=>data.request[key]!==body[key])||data.slots.some(slot=>typeof slot.start!=='string'||typeof slot.end!=='string'||!Number.isFinite(Date.parse(slot.start))||Date.parse(slot.end)<=Date.parse(slot.start)))throw new PanelError('Não foi possível confirmar os horários livres. Tente novamente.');
    r.data=data;r.status='ready';render();
  }catch(error){if(error.name==='AbortError'||!currentAssist(r,version,epoch,root))return;assistError(r,error);render();}
}
async function generateBriefing(){
  if(!state.authenticated||!state.prepId)return;const root=assistDefaults(),r=root.briefing;if(r.status==='loading')return;const id=state.prepId,notes=r.notes[id]||'';
  if(notes.length>1200){r.errors={user_context:'Use até 1.200 caracteres nas suas notas.'};r.error='Suas notas estão longas. Resuma os pontos principais.';r.status='error';render();return;}
  const version=++r.version,epoch=authEpoch;r.status='loading';r.data=null;r.error='';r.errors={};render();
  try{const data=await latest('assist-briefing','/preparation/briefing',{method:'POST',body:{event_id:id,user_context:notes}});if(!currentAssist(r,version,epoch,root)||state.prepId!==id||(r.notes[id]||'')!==notes)return;
    if(data?.status==='needs_clarification'){r.status='clarification';r.data=null;r.error=typeof data.question==='string'?data.question:'Escolha uma entrevista atual para continuar.';render();return;}
    if(data?.status!=='ready'||data.mode!=='factual_template'||data.facts?.event_id!==id||typeof data.facts.title!=='string'||typeof data.facts.start!=='string'||typeof data.facts.end!=='string'||!isoDate(data.facts.start.slice(0,10))||!Array.isArray(data.checklist)||data.checklist.length>20||data.checklist.some(item=>typeof item!=='string'||item.length>1000)||data.execution_allowed!==false||data.reservation_created!==false||!data.provided_context)throw new PanelError('Não foi possível confirmar o preparo desta entrevista. Tente novamente.');
    r.data=data;r.status='ready';r.key=JSON.stringify([id,notes,data.facts,data.provided_context]);if(!Object.hasOwn(r.checks,r.key))r.checks[r.key]=[];render();
  }catch(error){if(error.name==='AbortError'||!currentAssist(r,version,epoch,root)||state.prepId!==id)return;assistError(r,error);render();}
}
function togglePrepCheck(index,checked){const r=state.assist.briefing;if(r.status!=='ready'||!r.key||!Number.isSafeInteger(index)||index<0||index>=r.data.checklist.length)return;const set=new Set(r.checks[r.key]||[]);if(checked)set.add(index);else set.delete(index);r.checks[r.key]=[...set];render();}
function prepTime(data){if(!data?.start)return '';const start=data.start.slice(0,10),end=data.end?.slice(0,10);if(!isoDate(start))return '';if(data.all_day)return formatDate(start)+' · Dia inteiro';return formatDate(start,true)+' · '+humanTime(data.start.slice(11,16))+' às '+(end!==start?formatDate(end,true)+', ':'')+humanTime(data.end.slice(11,16));}
function otherZone(zone){return zone&&zone!=='America/Recife'?`<p class="assist-footnote">Horários de ${e(zone.split('/').pop().replace(/_/g,' '))}.</p>`:'';}
function aiField(kind,key,label,value,type='text',extra=''){const r=state.assist[kind],id='assist-'+kind+'-'+key,error=r.errors[key];return `<div class="field"><label for="${id}">${e(label)}</label><input class="input" id="${id}" data-assist-kind="${kind}" data-assist-field="${key}" type="${type}" value="${e(value)}" ${extra} ${error?'aria-invalid="true" aria-describedby="'+id+'-error"':''}><small id="${id}-error" class="assist-field-error">${e(error||'')}</small></div>`;}
function resourceError(resource){return resource.error?`<div class="assist-error" role="alert">${icon('alert')}<p>${e(resource.error)}</p></div>`:'';}
function summaryLine(value){return e(value).replace(/\*([^*\n]+)\*/g,'<strong>$1</strong>');}
function summaryPanel(){const r=assistDefaults().summary;let result='';
  if(r.status==='loading')result=`<div class="assist-working" role="status">${icon('sparkles')} Conferindo sua agenda e organizando o resumo…</div>${loading(true)}`;
  else if(r.status==='ready'){const data=r.data,factual=data.summary.mode==='factual',emptyPeriod=data.totals.events===0;const text=data.summary.text.split('\n').map(line=>line.trim()).filter(Boolean);
    result=`<div class="assist-summary-output"><div class="section-heading"><h3>${emptyPeriod?'Mais espaço no seu período.':r.period==='week'?'Sua semana, em perspectiva.':'Seu mês, em perspectiva.'}</h3><span class="tag ${factual?'neutral':'violet'}">${factual?'Resumo factual':'Organizado com IA'}</span></div><div class="assist-metrics"><div><strong>${data.totals.events}</strong><span>compromissos</span></div><div><strong>${Number.isSafeInteger(data.totals.upcoming)?data.totals.upcoming:0}</strong><span>ainda por vir</span></div><div><strong>${Number.isSafeInteger(data.totals.tentative)?data.totals.tentative:0}</strong><span>provisórios</span></div></div>${factual?`<p class="assist-fallback">${emptyPeriod?'Nenhum compromisso neste período; não foi necessário usar IA.':data.summary.reason==='context_budget_exceeded'?'O período foi resumido diretamente pelos dados da agenda.':'A organização por IA não ficou disponível. Confira abaixo o resumo factual da agenda.'}</p>`:''}<div class="assist-summary-text">${text.map(line=>`<p>${summaryLine(line)}</p>`).join('')}</div>${data.details?.omitted?`<p class="assist-footnote">${data.details.shown} compromissos detalhados de ${data.details.total}. Consulte a agenda para ver os demais.</p>`:''}<p class="assist-footnote">Consulta feita em ${e(timestamp(data.snapshot.collected_at_utc,data.period.time_zone))}. Nenhum compromisso foi alterado.</p></div>`;
  }
  return `<section class="assist-card"><div class="section-heading"><div><span class="eyebrow">UMA VISÃO MAIS LEVE</span><h2>Seu período, sem ruído.</h2></div>${icon('sparkles')}</div><p class="assist-description">A IA organiza fatos conferidos da agenda. Ela não cria compromissos nem preenche informações ausentes.</p><form id="assist-summary-form" class="assist-form" novalidate><div class="summary-tabs segmented" aria-label="Período do resumo">${[['week','Semana'],['month','Mês']].map(([value,label])=>`<button type="button" data-action="summary-period" data-value="${value}" class="${r.period===value?'is-active':''}" aria-pressed="${r.period===value}">${label}</button>`).join('')}</div>${aiField('summary','date','Data de referência',r.date,'date')}<p class="assist-footnote">${r.period==='week'?'A semana que contém essa data, de segunda a domingo.':'O mês que contém essa data.'}</p>${resourceError(r)}<button class="button primary" type="submit" ${r.status==='loading'?'disabled':''}>${icon('sparkles')} ${r.status==='loading'?'Organizando…':'Gerar resumo'}</button></form><div id="summary-results" aria-live="polite">${result}</div></section>`;
}
function availabilityPanel(){const r=assistDefaults().availability,f=r.form;let result='';if(r.status==='loading')result=`<div class="assist-working" role="status">${icon('clock')} Conferindo a ocupação da sua agenda…</div>${loading(true)}`;
  else if(r.status==='ready'){const data=r.data;result=`<div class="assist-slot-results"><div class="section-heading"><h3>${data.slots.length?'Algumas possibilidades para você.':'Nenhum horário nessa faixa.'}</h3><span class="tag neutral">Cálculo da agenda</span></div>${data.slots.length?`<ol class="assist-slots">${data.slots.map((slot,index)=>`<li><span class="assist-slot-number">${index+1}</span><div><strong>${e(dateLabel(slot.start.slice(0,10)))}</strong><span>${e(humanTime(slot.start.slice(11,16)))} às ${e(humanTime(slot.end.slice(11,16)))}</span></div>${icon('clock')}</li>`).join('')}</ol>`:'<p class="assist-description">Tente outra data, uma faixa mais ampla ou uma duração menor.</p>'}<p class="assist-footnote">São alternativas, que podem se sobrepor. Nenhuma reserva foi criada. A disponibilidade pode mudar.</p>${data.more_available?'<p class="assist-footnote">Há mais possibilidades além destas três opções.</p>':''}${otherZone(data.period?.time_zone)}<p class="assist-footnote">Conferido em ${e(timestamp(data.snapshot.observed_at_utc,data.period?.time_zone))}.</p></div>`;}
  return `<section class="assist-card"><div class="section-heading"><div><span class="eyebrow">ENCONTRE UM RESPIRO</span><h2>Qual horário funciona?</h2></div>${icon('clock')}</div><p class="assist-description">Veja até três opções livres, considerando duração e conflitos. Este cálculo não usa IA nem reserva horários.</p><form id="assist-availability-form" class="assist-form" novalidate><div class="field-row">${aiField('availability','date_from','A partir de',f.date_from,'date')}${aiField('availability','date_to','Até',f.date_to,'date')}</div><div class="field-row">${aiField('availability','window_start','Entre',f.window_start,'time')}${aiField('availability','window_end','E',f.window_end,'time')}</div><div class="field-row">${aiField('availability','duration_minutes','Duração em minutos',f.duration_minutes,'number','min="1" max="1440" step="1"')}${aiField('availability','buffer_minutes','Intervalo entre compromissos',f.buffer_minutes,'number','min="0" max="240" step="1"')}</div><p class="assist-footnote">O intervalo é uma margem em minutos antes e depois de cada compromisso existente.</p>${resourceError(r)}<button class="button primary" type="submit" ${r.status==='loading'?'disabled':''}>${icon('search')} ${r.status==='loading'?'Conferindo…':'Encontrar horários'}</button></form><div id="availability-results" aria-live="polite">${result}</div></section>`;
}
function briefingPanel(){const a=assistDefaults(),r=a.briefing,events=sortedEvents(),selected=state.prepDetail,notes=r.notes[state.prepId]||'';let result='';
  if(r.status==='loading')result=`<div class="assist-working" role="status">${icon('sparkles')} Reunindo os dados da entrevista…</div>${loading(true)}`;
  else if(r.status==='ready'){const d=r.data,c=d.provided_context,checks=r.checks[r.key]||[];result=`<div class="assist-brief-output"><div class="section-heading"><h3>Seu briefing.</h3><span class="tag neutral">Dados fornecidos</span></div><h3 class="assist-event-title">${e(d.facts.title)}</h3><p class="assist-timing">${e(prepTime(d.facts))}</p><p class="assist-description"><strong>Local ou link</strong><br>${e(d.facts.location||'Ainda não informado.')}</p><div class="assist-context"><h4>Contexto da entrevista</h4><p>${e(c.event_description||'Nenhuma descrição cadastrada no evento.')}</p>${c.user_context?`<h4>Suas notas</h4><p>${e(c.user_context)}</p>`:''}</div><p class="assist-footnote">Empresa, cargo e etapa não são deduzidos do título. Confira essas informações no material recebido.</p>${d.missing_fields?.includes('location')?'<p class="assist-attention">Falta confirmar o local ou link da conversa.</p>':''}${c.truncated_fields?.length?'<p class="assist-footnote">Alguns textos foram abreviados. Consulte o evento e suas notas para o conteúdo completo.</p>':''}<section class="assist-checklist" aria-labelledby="assist-check-title"><div class="section-heading"><h3 id="assist-check-title">Checklist sugerido</h3><span class="quiet-note">${checks.length} de ${d.checklist.length}</span></div><progress class="assist-progress" value="${checks.length}" max="${d.checklist.length||1}" aria-label="Itens preparados"></progress><div class="checklist">${d.checklist.map((item,index)=>`<div class="check-item"><input type="checkbox" id="prep-check-${index}" data-prep-check="${index}" ${checks.includes(index)?'checked':''}><label for="prep-check-${index}">${e(item)}</label></div>`).join('')}</div><p class="assist-footnote">Sugestões gerais de preparo, sem pesquisa externa. Suas marcações ficam somente nesta sessão do painel.</p></section>${otherZone(d.facts.time_zone)}</div>`;}
  else if(r.status==='clarification')result=`<div class="assist-error" role="status">${icon('info')}<p>${e(r.error)}</p></div>`;
  const choose=`<div class="assist-calendar-controls"><div class="field"><label for="prep-reference-date">Buscar entrevistas em</label><input class="input" type="date" id="prep-reference-date" value="${e(state.date||state.today||'')}"></div>${button('Ver semana','prep-week','','secondary small')}</div>`;
  let body=state.calendar.status==='loading'&&!events.length?loading():state.calendar.status==='error'&&!events.length?errorBlock(state.calendar.error):!events.length?empty('Qual é a próxima conversa?','Escolha uma data ou consulte a semana para selecionar sua entrevista.','prep-week','Consultar a semana'):`<label class="interview-selector"><span class="event-avatar violet" aria-hidden="true">${e(initials(selected?.title||events.find(event=>event.id===state.prepId)?.title||'Entrevista'))}</span><span><span class="eyebrow">PREPARAR ENTREVISTA</span><select id="prep-selector" aria-label="Escolher entrevista para preparar">${events.map(event=>`<option value="${e(event.id)}" ${event.id===state.prepId?'selected':''}>${e(event.title)} · ${e(dateLabel(event.date))}</option>`).join('')}</select></span>${icon('chevron')}</label>${state.prepStatus==='loading'||state.prepStatus==='idle'?loading(true):state.prepStatus==='error'?errorBlock(state.prepError,'retry-prep'):selected?`<section class="assist-selected-event"><h3>${e(selected.title)}</h3><p>${e(formatDate(selected.date,true))} · ${e(interval(selected))}</p><button class="text-button" data-action="event" data-id="${e(selected.id)}">Ver compromisso ${icon('arrow')}</button></section><form id="assist-briefing-form" class="assist-form" novalidate><div class="field"><label for="assist-briefing-notes">O que mais você quer lembrar? <span class="quiet-note">Opcional</span></label><textarea class="textarea" id="assist-briefing-notes" data-assist-kind="notes" data-assist-field="notes" maxlength="1200" rows="3" placeholder="Ex.: revisar o projeto que vou apresentar.">${e(notes)}</textarea><small>Somente informações que você recebeu ou deseja acrescentar.</small>${r.errors.user_context?`<small class="assist-field-error">${e(r.errors.user_context)}</small>`:''}</div>${r.status==='error'?resourceError(r):''}<button class="button primary" type="submit" ${r.status==='loading'?'disabled':''}>${icon('sparkles')} ${r.status==='loading'?'Preparando…':r.status==='ready'?'Atualizar preparo':'Preparar entrevista'}</button></form><div id="briefing-results" aria-live="polite">${result}</div>`:''}`;
  return `<section class="assist-card"><div class="section-heading"><div><span class="eyebrow">CLAREZA ANTES DA CONVERSA</span><h2>Seu preparo começa aqui.</h2></div>${icon('sparkles')}</div><p class="assist-description">Um briefing com os dados da entrevista e um checklist para você se organizar.</p>${choose}${body}</section>`;
}
function preparation(){const a=assistDefaults();return header('UM PASSO À FRENTE','Chegue com confiança.','Organize a agenda e prepare sua próxima conversa.')+`<div class="assist-tabs" role="tablist" aria-label="Recursos de preparo">${[['briefing','Entrevista','sparkles'],['summary','Resumo','calendar'],['availability','Horários livres','clock']].map(([value,label,ico])=>`<button type="button" role="tab" id="assist-tab-${value}" aria-controls="assist-panel" aria-selected="${a.tab===value}" class="${a.tab===value?'is-active':''}" data-action="assist-tab" data-value="${value}">${icon(ico)}<span>${label}</span></button>`).join('')}</div><div id="assist-panel" role="tabpanel" aria-labelledby="assist-tab-${a.tab}" class="assist-layout"><div>${a.tab==='summary'?summaryPanel():a.tab==='availability'?availabilityPanel():briefingPanel()}</div><aside class="assist-side-note"><span class="eyebrow">CADA RECURSO, SEU PAPEL</span><h2>Mais contexto.<br>Menos correria.</h2><p>O resumo usa IA para organizar fatos da agenda. Horários livres são calculados com base na ocupação. Briefing e checklist usam as informações fornecidas.</p><p>Nenhum desses pedidos cria, altera ou reserva compromissos.</p>${button('Ver minha agenda','prep-agenda','','secondary small')}</aside></div>`;}
function reminderLabel(item){return statusMap[item.state]||['Estado não informado','neutral'];}
function normalReminder(item){
  if(!item||typeof item.id!=='string'||!item.id||item.id.length>128||typeof item.title!=='string'||typeof item.state!=='string'||!Number.isFinite(Date.parse(item.start_at))||!Number.isFinite(Date.parse(item.due_at))||typeof item.time_zone!=='string')throw new PanelError('Os avisos retornaram dados incompletos.');
  new Intl.DateTimeFormat('pt-BR',{timeZone:item.time_zone});return item;
}
function reminderVersion(item){return JSON.stringify([item.id,item.event_id,item.title,item.start_at,item.due_at,item.time_zone,item.offset_minutes,item.format,item.format_is_planned,item.state,item.reason,item.updated_at]);}
function reminderOverdue(item){return ['pending','reserved','sending'].includes(item.state)&&Number.isFinite(Date.parse(state.reminders.snapshotAt))&&Date.parse(item.due_at)<Date.parse(state.reminders.snapshotAt);}
function reminderAdvance(item){return item.offset_minutes===60?'1 hora antes':Number.isSafeInteger(item.offset_minutes)?`${item.offset_minutes} minutos antes`:'Antecedência não informada';}
function reminderFormat(item){return item.format==='card'?'Card ilustrado':item.format==='text'?'Mensagem em texto':'Formato não informado';}
function reminders(){
  const data=state.reminders,filters=[['upcoming','Pendentes'],['accepted','Envios aceitos'],['attention','Atenção'],['all','Todo o histórico']];
  const messages={upcoming:['Nenhum aviso pendente.','Os próximos avisos aparecem aqui quando forem registrados pelo sistema.'],accepted:['Nenhum envio aceito neste filtro.','O aceite aparecerá quando o provedor confirmar uma tentativa.'],attention:['Nenhum aviso requer atenção neste filtro.','Bloqueios, resultados incertos e prazos encerrados aparecem aqui.'],all:['Ainda não há lembretes registrados.','Quando o sistema registrar um aviso, você poderá acompanhar cada mudança por aqui.']};
  const [emptyTitle,emptyText]=messages[state.reminderFilter]||messages.all;
  let content=data.status==='loading'&&!data.items.length||data.status==='idle'?loading():data.status==='error'&&!data.items.length?errorBlock(data.error,'refresh-reminders'):`${data.status==='error'?`<div class="stale-notice">${icon('alert')}<span>${e(data.error)} Os registros abaixo são da consulta anterior.</span>${button('Atualizar lista','refresh-reminders','','small secondary')}</div>`:''}<div class="reminder-summary"><div><strong>${data.total===null?data.items.length:data.total}</strong><span>${data.total===1?'aviso neste filtro':'avisos neste filtro'}${data.status==='error'?' · consulta anterior':''}</span></div><p>${data.snapshotAt?`Consulta: ${e(timestamp(data.snapshotAt))}`:'Horário da consulta não informado'}</p></div>${data.counts?`<div class="reminder-state-counts" aria-label="Estados neste filtro">${Object.entries(statusMap).filter(([key])=>data.counts[key]>0).map(([key,[label,color]])=>`<span class="tag ${color}">${e(label)} <strong>${data.counts[key]}</strong></span>`).join('')}</div>`:''}<div class="reminder-table"><div class="reminder-table-header"><span>COMPROMISSO</span><span>AVISO PREVISTO</span><span>FORMATO PREVISTO</span><span>SITUAÇÃO</span><span></span></div>${data.items.length?data.items.map(item=>{const [label,color]=reminderLabel(item),overdue=reminderOverdue(item);return `<button class="reminder-row" data-action="reminder" data-id="${e(item.id)}" aria-label="Acompanhar ${e(item.title)}: ${e(label)}"><span class="reminder-event">${avatar(item)}<span><strong>${e(item.title)}</strong><small>Compromisso · ${e(timestamp(item.start_at,item.time_zone))}</small></span></span><span class="reminder-due"><strong>${e(timestamp(item.due_at,item.time_zone))}</strong><small>${e(reminderAdvance(item))}</small>${overdue?'<small class="reminder-overdue">Horário previsto já passou</small>':''}</span><span class="format-pill">${icon(item.format==='card'?'image':'message')} ${item.format==='card'?'Card':item.format==='text'?'Texto':'Aviso'}</span><span class="reminder-status"><span class="tag ${color}">${e(label)}</span><small>${e(item.reason||reminderDescriptions[item.state]||'Confira os detalhes.')}</small></span>${icon('chevron','row-arrow')}</button>`;}).join(''):empty(emptyTitle,emptyText)}</div>${data.nextCursor?`<div class="load-more-row">${button(data.status==='loading'?'Carregando…':'Carregar mais','more-reminders',data.status==='loading'?'disabled':'')}</div>`:''}<p class="pagination-note">${data.items.length} ${data.items.length===1?'registro carregado':'registros carregados'}${data.total!==null?` de ${data.total} neste filtro`:''}.</p>`;
  return header('NO TEMPO CERTO','Cada aviso, com clareza.','Próximos lembretes, tentativas e histórico em um só lugar.',button(`${icon('refresh')} Atualizar`,'refresh-reminders','','secondary'))+`<div class="reminder-rule"><div class="rule-icon">${icon('bell')}</div><div><strong>Um cuidado a mais antes da conversa.</strong><p>Horário e formato são previstos conforme a configuração de cada aviso. Abra um registro para ver o histórico e a prévia disponível.</p></div></div><div class="reminder-filters segmented" aria-label="Filtrar lembretes">${filters.map(([value,label])=>`<button class="${state.reminderFilter===value?'is-active':''}" data-action="reminder-filter" data-value="${value}" aria-pressed="${state.reminderFilter===value}">${label}</button>`).join('')}</div>${content}<p class="quiet-note reminder-note">${icon('info')} “Aceito pelo provedor” confirma o aceite do envio. Não comprova entrega ou leitura no WhatsApp. Este painel não repete envios.</p>`;
}
function render(){
  if(!state.authenticated||state.revalidating)return;
  const active=document.activeElement;let selector=null;if(page.contains(active)){if(active.id)selector='#'+CSS.escape(active.id);else if(active.dataset.action)selector='[data-action="'+CSS.escape(active.dataset.action)+'"]'+['value','date','id'].filter(key=>active.dataset[key]).map(key=>'[data-'+key+'="'+CSS.escape(active.dataset[key])+'"]').join('');}
  $('#auth-root').hidden=true;$('#app-root').hidden=false;
  $('#navigation').innerHTML=routes.map(route=>`<a class="nav-item ${route.id===state.route?'is-active':''}" href="#${route.id}" aria-label="${route.label}" ${route.id===state.route?'aria-current="page"':''}>${icon(route.icon)}<span class="nav-label">${route.label}</span>${route.id===state.route?'<span class="nav-active-dot" aria-hidden="true"></span>':''}</a>`).join('');
  $('#breadcrumb').textContent=routes.find(route=>route.id===state.route).label;$('#calendar-name').textContent=state.calendar.name||'Acesso local protegido';const badge=$('#connection-badge');badge.textContent=state.calendar.status==='ready'?'Agenda atualizada':state.calendar.status==='loading'?'Consultando agenda':'Acesso autorizado';badge.classList.toggle('is-current',state.calendar.status==='ready');
  page.innerHTML=mutationRecoveryNotice()+(state.route==='visao-geral'?overview():state.route==='agenda'?agenda():state.route==='preparo'?preparation():reminders());document.title=`${routes.find(route=>route.id===state.route).label} · Genius Agenda AI`;
  if(selector)($(selector,page)||page).focus({preventScroll:true});
}
function go(route){if(state.route===route){render();return;}location.hash=route;}
window.addEventListener('hashchange',()=>{if(!state.authenticated)return;positions[state.route]=window.scrollY;state.route=readRoute();render();page.focus({preventScroll:true});window.scrollTo({top:positions[state.route]||0,behavior:'instant'});if(!matchMedia('(prefers-reduced-motion: reduce)').matches)page.animate?.([{opacity:.4,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:200,easing:'cubic-bezier(.22,1,.36,1)'});if(state.route==='preparo'&&state.prepId&&state.prepStatus==='idle')loadPrep(state.prepId);});
function toast(message){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').classList.add('show');toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),4500);}
function openSheet(kicker,html,footer=''){if(!state.authenticated)return;$('#sheet-kicker').textContent=kicker;$('#sheet-content').innerHTML=html;$('#sheet-footer').innerHTML=footer;if(!sheet.open)sheet.showModal();$('#sheet-content').scrollTop=0;requestAnimationFrame(()=>$('#sheet-title')?.focus({preventScroll:true}));}

sheet.addEventListener('cancel',event=>{event.preventDefault();closeSheet();});
sheet.addEventListener('click',event=>{if(event.target===sheet){const rect=sheet.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeSheet();}});
const MUTATION_STATES=new Set(['pending','succeeded','failed','conflict','cancelled','expired','invalidated','unknown','preparing','confirming']);
const UUID_PATTERN=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const mutationNames={create:['Novo compromisso','Confirmar criação','Compromisso criado.'],edit:['Editar compromisso','Confirmar alteração','Compromisso atualizado.'],reschedule:['Reagendar compromisso','Confirmar novo horário','Compromisso reagendado.'],delete:['Excluir compromisso','Sim, excluir compromisso','Compromisso excluído.']};
function closeSheet(){
  if(mutation){
    if(mutation.busy){toast('Aguarde a conferência em andamento.');return;}
    if(mutation.phase==='leave'){mutation.phase=mutation.previousPhase||'form';renderMutation();return;}
    if(mutation.phase==='form'&&mutation.dirty||mutation.phase==='preview'&&mutation.result?.state==='pending'){mutation.previousPhase=mutation.phase;mutation.phase='leave';renderMutation();return;}
    if(mutation.phase==='unknown'||mutation.phase==='preview-error'){mutation.closed=true;sheet.close();render();return;}
    mutationSequence++;mutation=null;
  }
  clearReminderDetail();detailSequence++;abort('detail');selectedDetail=null;sheet.close();$('#sheet-content').replaceChildren();$('#sheet-footer').replaceChildren();
}
function uuid(){return crypto.randomUUID();}
function localDateTime(event,end=false){const value=end?event?.end_at:event?.start_at;if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value))return '';return value.slice(0,16);}
function durationText(start,end){if(!start||!end)return 'Informe o início e o término.';const delta=(Date.parse(end+'Z')-Date.parse(start+'Z'))/60000;if(!Number.isFinite(delta)||delta<=0)return 'O término precisa ser depois do início.';const hours=Math.floor(delta/60),minutes=delta%60;return 'Duração: '+[hours?`${hours}h`:'',minutes?`${minutes}min`:''].filter(Boolean).join(' ');}
function mutationRecoveryNotice(){return mutation?.closed?`<div class="mutation-recovery" role="status">${icon('info')}<span>Há uma alteração para conferir antes de começar outra.</span>${button('Conferir','resume-mutation','','small secondary')}</div>`:'';}
function suspendMutation(){
  if(!mutation)return;
  if(mutation.busy){mutation.busy=false;mutation.phase=mutation.result?.operation_id?'unknown':'preview-error';mutation.error='A conferência foi interrompida. Consulte o resultado antes de continuar.';}
  mutation.closed=true;mutationSequence++;
}
function beginMutation(action,event=selectedDetail){
  if(!state.authenticated||state.revalidating)return;
  if(mutation&&(mutation.busy||['preview','unknown','preview-error'].includes(mutation.phase))){mutation.closed=false;renderMutation();toast('Conclua ou cancele esta alteração antes de começar outra.');return;}
  if(!mutationNames[action]||action!=='create'&&!event)return;
  if(action==='reschedule'&&event.all_day){toast('Para mudar a data de um evento de dia inteiro, use o Google Calendar.');return;}
  detailSequence++;abort('detail');mutationSequence++;
  const date=state.date||state.today||'';
  mutation={action,event:event?{...event}:null,phase:'form',busy:false,dirty:false,error:'',errors:{},result:null,payload:null,closed:false,values:{title:action==='create'?'':event.title,description:action==='create'?'':event.description||'',location:action==='create'?'':event.location||'',start:action==='create'?(date?date+'T09:00':''):localDateTime(event),end:action==='create'?(date?date+'T09:30':''):localDateTime(event,true)}};
  if(action==='delete'){previewMutation();return;}
  renderMutation();
}
function mutationField(name,label,type='text',options={}){
  const current=mutation;const error=current.errors[name];const value=current.values[name]||'';
  return `<div class="field"><label for="mutation-${name}">${label}</label>${type==='textarea'?`<textarea class="textarea" id="mutation-${name}" name="${name}" data-mutation-field="${name}" maxlength="${options.max||4000}" ${error?'aria-invalid="true"':''} aria-describedby="mutation-${name}-error">${e(value)}</textarea>`:`<input class="input" id="mutation-${name}" name="${name}" data-mutation-field="${name}" type="${type}" value="${e(value)}" ${options.required?'required':''} ${options.max?`maxlength="${options.max}"`:''} ${error?'aria-invalid="true"':''} aria-describedby="mutation-${name}-error" ${type==='datetime-local'?'step="60"':''}>`}<small id="mutation-${name}-error" class="mutation-field-error">${e(error||'')}</small></div>`;
}
function mutationSnapshot(value,label){
  if(!value)return '';
  const start=value.start_at||value.start,end=value.end_at||value.end;
  const startDate=typeof start==='string'?start.slice(0,10):null,endDate=typeof end==='string'?end.slice(0,10):null;
  let when='Horário não informado';
  if(isoDate(startDate)){when=formatDate(startDate,true);if(value.all_day)when+=' · Dia inteiro';else if(typeof start==='string'&&typeof end==='string'){when+=` · ${humanTime(start.slice(11,16))} às ${humanTime(end.slice(11,16))}`;if(endDate!==startDate&&isoDate(endDate))when+=` de ${formatDate(endDate,true)}`;}}
  return `<section class="mutation-snapshot"><span class="eyebrow">${label}</span><h3>${e(value.title||'Compromisso')}</h3><p>${e(when)}</p>${typeof value.description==='string'?`<dl><dt>Descrição</dt><dd>${e(value.description||'Sem descrição.')}</dd></dl>`:''}${typeof value.location==='string'?`<dl><dt>Local</dt><dd>${e(value.location||'Não informado.')}</dd></dl>`:''}</section>`;
}
function renderMutation(){
  const current=mutation;if(!current||!state.authenticated||state.revalidating||current.closed)return;
  const names=mutationNames[current.action];let html='',footer='';
  if(current.phase==='leave'){
    html=`<h2 id="sheet-title" class="detail-title" tabindex="-1">${current.result?.state==='pending'?'Cancelar esta alteração?':'Descartar este rascunho?'}</h2><p class="page-subtitle">O compromisso permanece como está. Você pode voltar e continuar.</p>`;
    footer=button('Continuar editando','keep-mutation')+button(current.result?.state==='pending'?'Cancelar alteração':'Descartar rascunho','discard-mutation','','danger');
  }else if(current.phase==='form'){
    html=`<h2 id="sheet-title" class="detail-title" tabindex="-1">${names[0]}</h2><p class="page-subtitle">${current.action==='create'?'Reserve um espaço para sua próxima conversa.':`Você está alterando “${e(current.event.title)}”.`}</p>${current.error?`<div class="access-error" role="alert">${e(current.error)}</div>`:''}<form id="mutation-form" class="mutation-form" novalidate>${current.action==='create'||current.action==='edit'?mutationField('title','Título','text',{required:true,max:400}):''}${current.action==='create'||current.action==='reschedule'?`<div class="field-row">${mutationField('start','Começa em','datetime-local',{required:true})}${mutationField('end','Termina em','datetime-local',{required:true})}</div><p id="mutation-duration" class="mutation-duration" role="status">${e(durationText(current.values.start,current.values.end))}</p>`:''}${current.action==='create'||current.action==='edit'?mutationField('description','Descrição (opcional)','textarea'):''}${current.action==='edit'?mutationField('location','Local ou link (opcional)','text',{max:4000}):''}<p class="quiet-note">Você vai conferir os dados antes de confirmar.</p><button class="screen-reader-only" type="submit" tabindex="-1">Conferir dados</button></form>`;
    footer=button('Voltar','close-sheet')+button('Conferir dados','preview-mutation','','primary');
  }else if(current.busy){
    html=`<h2 id="sheet-title" class="detail-title" tabindex="-1">${current.phase==='confirming'?'Salvando na sua agenda.':current.phase==='cancelling'?'Cancelando a alteração.':'Conferindo os dados.'}</h2>${loading(true)}<p class="quiet-note">Aguarde a confirmação. Não é preciso enviar de novo.</p>`;
    footer=button('Aguarde…','noop','disabled','primary');
  }else if(current.phase==='unknown'||current.phase==='preview-error'){
    html=`<span class="tag amber">Conferência pendente</span><h2 id="sheet-title" class="detail-title" tabindex="-1">Vamos conferir o que aconteceu.</h2><p class="page-subtitle">${e(current.error||'Não foi possível confirmar o resultado da alteração. Não repita o pedido.')}</p>${mutationSnapshot(current.result?.after||current.result?.before,'COMPROMISSO')}<p class="quiet-note">${current.result?.operation_id?'A próxima consulta verifica o resultado desta mesma alteração. Ela não repete a gravação.':'Conferir a prévia reutiliza este rascunho. Nenhuma gravação é feita na prévia.'}</p>`;
    footer=button('Fechar','close-sheet')+button(current.result?.operation_id?'Consultar resultado':'Conferir prévia',current.result?.operation_id?'check-mutation':'preview-mutation','','primary');
  }else{
    const result=current.result;const pending=result?.state==='pending',success=result?.state==='succeeded',cancelled=result?.state==='cancelled';
    const titles={pending:current.action==='delete'?'Excluir este compromisso?':'Está tudo certo?',succeeded:names[2],conflict:'Este horário já está ocupado.',expired:'Esta confirmação expirou.',invalidated:'O compromisso mudou.',cancelled:'Alteração cancelada.',failed:'Não foi possível concluir.',preparing:'Ainda estamos conferindo.',confirming:'A alteração está em andamento.'};
    html=`<span class="tag ${success?'mint':pending?'violet':cancelled?'neutral':'amber'}">${success?'Confirmado':pending?'CONFIRA ANTES DE CONFIRMAR':cancelled?'Sem alteração':'Confira os dados'}</span><h2 id="sheet-title" class="detail-title" tabindex="-1">${e(titles[result?.state]||'Confira o resultado.')}</h2>${current.error?`<div class="access-error" role="alert">${e(current.error)}</div>`:''}${result?.message?`<p class="page-subtitle">${e(result.message)}</p>`:''}${current.action==='create'?mutationSnapshot(result?.after,'NOVO COMPROMISSO'):current.action==='delete'?mutationSnapshot(result?.before,'COMPROMISSO SELECIONADO'):`<div class="mutation-comparison">${mutationSnapshot(result?.before,'COMO ESTÁ')}${mutationSnapshot(result?.after,'COMO FICA')}</div>`}${result?.conflicts?.length?`<section class="mutation-conflicts"><h3>Compromissos neste intervalo</h3>${result.conflicts.map(item=>mutationSnapshot(item,'JÁ NA AGENDA')).join('')}</section>`:''}${pending?'<p class="quiet-note">A alteração só será aplicada depois da sua confirmação.</p>':''}`;
    if(pending)footer=button('Cancelar','discard-mutation')+button(names[1],'confirm-mutation','',current.action==='delete'?'danger':'primary');
    else if(success||cancelled)footer=button('Concluir','close-sheet','','primary');
    else if(result&&['unknown','confirming','preparing'].includes(result.state))footer=button('Consultar resultado','check-mutation','','primary');
    else footer=button('Fechar','close-sheet')+(current.action==='delete'?button('Atualizar compromisso','reload-mutation-event'):button('Revisar dados','revise-mutation','','primary'));
  }
  openSheet(current.action==='delete'?'EXCLUIR COMPROMISSO':'SUA AGENDA',html,footer);
}
function collectMutationPayload(){
  const current=mutation;const values=current.values;const errors={};const event={};
  if(current.action==='create'||current.action==='edit'){
    if(!values.title.trim())errors.title='Informe um título.';
    else if(values.title.length>400)errors.title='Use até 400 caracteres.';
    if(values.description.length>4000)errors.description='Use até 4.000 caracteres.';
    if(current.action==='create'||values.title!==current.event.title)event.title=values.title;
    if(current.action==='create'||values.description!==(current.event.description||''))event.description=values.description;
    if(current.action==='edit'&&values.location.length>4000)errors.location='Use até 4.000 caracteres.';
    if(current.action==='edit'&&values.location!==(current.event.location||''))event.location=values.location;
  }
  if(current.action==='create'||current.action==='reschedule'){
    for(const name of ['start','end'])if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(values[name])||!isoDate(values[name].slice(0,10)))errors[name]='Informe a data e o horário.';
    if(!errors.start&&!errors.end&&Date.parse(values.end+'Z')<=Date.parse(values.start+'Z'))errors.end='O término precisa ser depois do início.';
    event.start=values.start;event.end=values.end;
  }
  if(current.action==='edit'&&!Object.keys(event).length)errors.title='Altere pelo menos uma informação.';
  current.errors=errors;if(Object.keys(errors).length)return null;
  return {request_id:uuid(),action:current.action,...(current.action==='create'?{}:{event_id:current.event.id}),...(Object.keys(event).length?{event}:{})};
}
function normalMutationResult(data){
  if(!data||typeof data!=='object'||!UUID_PATTERN.test(data.operation_id||'')||!MUTATION_STATES.has(data.state)||!mutationNames[data.action])throw new PanelError('O resultado não pôde ser confirmado. Consulte antes de repetir.');
  return data;
}
function mutationIsCurrent(current,sequence,epoch){return mutation===current&&mutationSequence===sequence&&authEpoch===epoch&&state.authenticated;}
function acceptMutationResult(current,data){
  const result=normalMutationResult(data);if(result.action!==current.action)throw new PanelError('O resultado não corresponde à alteração pedida.');
  current.result=result;current.busy=false;current.error='';
  current.phase=['unknown','preparing','confirming'].includes(result.state)?'unknown':'preview';
  if(result.state==='failed'&&Array.isArray(result.error?.fields)){
    const fields=result.error.fields.map(value=>String(value).split('.').pop()).filter(value=>['title','description','location','start','end'].includes(value));
    if(fields.length&&current.action!=='delete'){current.error=result.message||'Confira os dados destacados.';current.errors=Object.fromEntries(fields.map(value=>[value,current.error]));current.phase='form';current.result=null;current.payload=null;}
  }
  if(result.state==='succeeded'){invalidateAssistResults();if(['create','reschedule'].includes(current.action)&&isoDate(result.after?.start?.slice(0,10)))state.date=result.after.start.slice(0,10);current.phase='done';current.dirty=false;toast(mutationNames[current.action][2]);refresh();}
  renderMutation();render();
}
async function previewMutation(){
  const current=mutation;if(!current||current.busy||!state.authenticated)return;
  if(!current.payload){current.payload=collectMutationPayload();if(!current.payload){renderMutation();const field=Object.keys(current.errors)[0];$('#mutation-'+field)?.focus();return;}}
  current.busy=true;current.phase='previewing';current.error='';renderMutation();
  const sequence=++mutationSequence,epoch=authEpoch;
  try{const data=await latest('mutation','/agenda/preview',{method:'POST',body:current.payload});if(mutationIsCurrent(current,sequence,epoch))acceptMutationResult(current,data);}
  catch(error){if(!mutationIsCurrent(current,sequence,epoch))return;current.busy=false;if(error.data?.operation_id){try{acceptMutationResult(current,error.data);return;}catch{}}current.error=error.message||'Não foi possível conferir a prévia.';current.phase=error.status>=400&&error.status<500&&error.status!==408?'form':'preview-error';if(current.phase==='form')current.payload=null;for(const field of error.fields||[]){const name=String(field).split('.').pop();if(['title','description','location','start','end'].includes(name))current.errors[name]=current.error;}renderMutation();}
}
async function confirmMutation(){
  const current=mutation;if(!current||current.busy||current.result?.state!=='pending'||!state.authenticated)return;
  const id=current.result.operation_id;current.busy=true;current.phase='confirming';renderMutation();
  const sequence=++mutationSequence,epoch=authEpoch;
  try{const data=await latest('mutation','/agenda/'+encodeURIComponent(id)+'/confirm',{method:'POST',body:{}});if(mutationIsCurrent(current,sequence,epoch))acceptMutationResult(current,data);}
  catch(error){if(!mutationIsCurrent(current,sequence,epoch))return;current.busy=false;if(error.data?.operation_id){try{acceptMutationResult(current,error.data);return;}catch{}}current.error=error.message||'A conexão foi interrompida. Consulte o resultado antes de continuar.';current.phase='unknown';renderMutation();}
}
async function checkMutation(){
  const current=mutation;if(!current||current.busy||!current.result?.operation_id||!state.authenticated)return;
  current.busy=true;current.phase='checking';renderMutation();const sequence=++mutationSequence,epoch=authEpoch;
  try{const data=await latest('mutation','/agenda/'+encodeURIComponent(current.result.operation_id));if(mutationIsCurrent(current,sequence,epoch))acceptMutationResult(current,data);}
  catch(error){if(!mutationIsCurrent(current,sequence,epoch))return;current.busy=false;if(error.data?.operation_id){try{acceptMutationResult(current,error.data);return;}catch{}}current.error=error.message||'Ainda não foi possível conferir. Não repita a alteração.';current.phase='unknown';renderMutation();}
}
async function discardMutation(){
  const current=mutation;if(!current||current.busy)return;
  if(!current.result?.operation_id){mutationSequence++;mutation=null;sheet.close();render();return;}
  if(current.result.state!=='pending'){closeSheet();return;}
  current.busy=true;current.phase='cancelling';renderMutation();const sequence=++mutationSequence,epoch=authEpoch;
  try{const data=await latest('mutation','/agenda/'+encodeURIComponent(current.result.operation_id)+'/cancel',{method:'POST',body:{}});if(mutationIsCurrent(current,sequence,epoch))acceptMutationResult(current,data);}
  catch(error){if(!mutationIsCurrent(current,sequence,epoch))return;current.busy=false;if(error.data?.operation_id){try{acceptMutationResult(current,error.data);return;}catch{}}current.error='Não foi possível confirmar o cancelamento. Consulte o resultado antes de continuar.';current.phase='unknown';renderMutation();}
}
function reviseMutation(){
  const current=mutation;if(!current||current.busy||current.phase==='unknown')return;
  if(current.result?.state==='pending'){toast('Cancele esta prévia antes de mudar os dados.');return;}
  current.result=null;current.payload=null;current.phase='form';current.error='';current.errors={};current.dirty=true;mutationSequence++;renderMutation();
}
function mutationInput(field,value){
  if(!mutation||mutation.phase!=='form'||mutation.busy)return;mutation.values[field]=value;mutation.dirty=true;mutation.payload=null;mutation.errors[field]='';
  const duration=$('#mutation-duration');if(duration)duration.textContent=durationText(mutation.values.start,mutation.values.end);
}

async function eventDetail(id){
  clearReminderDetail();const sequence=++detailSequence;openSheet('SEU COMPROMISSO',`<h2 id="sheet-title" class="detail-title" tabindex="-1">Consultando os detalhes.</h2>${loading(true)}`);
  try{const result=await latest('detail','/events/'+encodeURIComponent(id));if(sequence!==detailSequence||!state.authenticated)return;const event=normalEvent(result.event);selectedDetail=event;openSheet('SEU COMPROMISSO',`${avatar(event)}<h2 id="sheet-title" class="detail-title" tabindex="-1">${e(event.title)}</h2><p class="detail-meta">${event.status==='tentative'?'Compromisso provisório':'Dados conferidos na agenda'}</p><div class="detail-facts"><div class="fact-item">${icon('calendar')}<span>${e(formatDate(event.date))}</span></div><div class="fact-item">${icon('clock')}<span>${e(interval(event))}</span></div><div class="fact-item">${icon('pin')}<span>${e(event.location||'Local não informado')}</span></div></div><section class="detail-section"><h3>Sobre a conversa</h3><p>${e(event.description||'Nenhuma descrição informada.')}</p></section><div class="readonly-detail-actions"><button class="button secondary" data-action="edit-mutation">Editar</button><button class="button secondary" data-action="reschedule-mutation" ${event.all_day?'disabled':''}>Reagendar</button><button class="button danger" data-action="delete-mutation">Excluir</button></div>${event.all_day?'<p class="readonly-caption">Para mudar a data deste evento de dia inteiro, use o Google Calendar.</p>':''}`,button(`Ver contexto para preparo ${icon('arrow')}`,'prepare',`data-id="${e(event.id)}"`,'primary'));}
  catch(error){if(error.name==='AbortError'||sequence!==detailSequence||!state.authenticated)return;openSheet('CONSULTA NÃO CONCLUÍDA',`<h2 id="sheet-title" class="detail-title" tabindex="-1">Não foi possível abrir.</h2><p class="page-subtitle">${e(error.message)}</p>`,button('Tentar novamente','event',`data-id="${e(id)}"`));}
}
async function reminderDetail(id){
  clearReminderDetail();selectedDetail=null;const sequence=++detailSequence;openSheet('ACOMPANHAR AVISO',`<h2 id="sheet-title" class="detail-title" tabindex="-1">Consultando o aviso.</h2>${loading(true)}`);
  try{const result=await latest('detail','/reminders/'+encodeURIComponent(id));if(sequence!==detailSequence||!state.authenticated)return;const item=normalReminder(result.item);if(!Array.isArray(result.history)||result.history.length>1000||result.history.some(entry=>!entry||typeof entry.to_state!=='string'||!Number.isFinite(Date.parse(entry.at))))throw new PanelError('Não foi possível confirmar o histórico deste aviso.');selectedReminder={item,history:result.history,sequence,imageStatus:'idle',imageSrc:'',imageError:'',stale:false};renderReminderDetail();}
  catch(error){if(error.name==='AbortError'||sequence!==detailSequence||!state.authenticated)return;openSheet('CONSULTA NÃO CONCLUÍDA',`<h2 id="sheet-title" class="detail-title" tabindex="-1">Não foi possível abrir o aviso.</h2><p class="page-subtitle">${e(error.message)}</p>`,button('Tentar novamente','reminder',`data-id="${e(id)}"`));}
}
function renderReminderDetail(){
  const current=selectedReminder;if(!current||current.sequence!==detailSequence||!state.authenticated||!sheet.open)return;
  const item=current.item,[label,color]=reminderLabel(item),canPreview=item.format==='card'&&item.format_is_planned===true;
  const preview=current.stale?`<div class="reminder-preview-error" role="status">${icon('alert')}<p>Este registro mudou ou saiu do filtro consultado. Atualize os detalhes para conferir os dados atuais.</p>${button('Atualizar detalhes','reminder',`data-id="${e(item.id)}"`,'secondary small')}</div>`:current.imageStatus==='loading'?`<div class="card-preview-loading" role="status">${icon('image')}<span>Preparando uma prévia privada…</span></div>`:current.imageStatus==='ready'?`<figure class="reminder-card-figure"><img data-reminder-image="true" src="${e(current.imageSrc)}" alt="Prévia não enviada do card de ${e(item.title)}" width="1080" height="1080"><figcaption>Prévia • não enviada. Reconstrução com os dados atuais; não é comprovante nem cópia de uma imagem enviada.</figcaption></figure>`:current.imageStatus==='error'?`<div class="reminder-preview-error" role="status">${icon('image')}<p>${e(current.imageError)}<br>Os dados do aviso continuam disponíveis em texto.</p></div>`:canPreview?`${button(`${icon('image')} Visualizar card`,'preview-reminder-card','','secondary')}<p class="quiet-note">A prévia usa os dados atuais da agenda. Não envia mensagens.</p>`:`<p class="quiet-note">${item.format==='text'?'Este registro prevê uma mensagem em texto. Não há card para visualizar.':'Não há prévia disponível para este formato.'}</p>`;
  const history=[...current.history].sort((a,b)=>Date.parse(a.at)-Date.parse(b.at));
  openSheet('ACOMPANHAR AVISO',`<span class="tag ${color}">${e(label)}</span><h2 id="sheet-title" class="detail-title" tabindex="-1">${e(item.title)}</h2><p class="reminder-state-description">${e(reminderDescriptions[item.state]||'Consulte o estado registrado abaixo.')}</p><div class="detail-facts"><div class="fact-item">${icon('calendar')}<span><small>Compromisso</small>${e(timestamp(item.start_at,item.time_zone))}</span></div><div class="fact-item">${icon('clock')}<span><small>Aviso previsto · ${e(reminderAdvance(item))}</small>${e(timestamp(item.due_at,item.time_zone))}${reminderOverdue(item)?'<small class="reminder-overdue">Horário previsto já passou</small>':''}</span></div><div class="fact-item">${icon(item.format==='card'?'image':'message')}<span><small>Formato previsto</small>${e(reminderFormat(item))}</span></div></div><section class="reminder-reason"><h3>Último registro</h3><p>${e(item.reason||'Sem motivo adicional informado.')}</p><small>${item.updated_at?`Atualizado em ${e(timestamp(item.updated_at,item.time_zone))}`:'Horário da atualização não informado'}</small></section><section class="detail-section reminder-preview"><div class="section-heading"><h3>Visual do aviso</h3>${canPreview?'<span class="tag violet">Prévia • não enviada</span>':''}</div>${preview}</section><section class="detail-section"><h3>Histórico do aviso</h3>${history.length?`<ol class="reminder-timeline">${history.map(entry=>`<li class="history-entry"><div><strong>${e((statusMap[entry.to_state]||['Estado registrado'])[0])}</strong><p>${e(entry.reason||'Registro atualizado.')}</p><time datetime="${e(entry.at)}">${e(timestamp(entry.at,item.time_zone))}</time></div></li>`).join('')}</ol>`:'<p>Nenhuma transição disponível nesta consulta.</p>'}</section><p class="quiet-note reminder-note">${icon('info')} Os estados acompanham cada tentativa. Nenhuma ação nesta tela repete o envio.</p>`,button('Fechar','close-sheet'));
}
async function previewReminderCard(){
  const current=selectedReminder;if(!current||current.stale||current.imageStatus==='loading'||current.item.format!=='card'||current.item.format_is_planned!==true||!state.authenticated)return;
  const epoch=authEpoch,sequence=detailSequence;abort('reminder-image');const controller=new AbortController();controllers.set('reminder-image',controller);current.imageStatus='loading';current.imageSrc='';current.imageError='';renderReminderDetail();
  const valid=()=>state.authenticated&&epoch===authEpoch&&selectedReminder===current&&sequence===detailSequence&&!controller.signal.aborted&&sheet.open;
  let timedOut=false;const timer=setTimeout(()=>{timedOut=true;controller.abort();},30000);
  try{const src=await requestImage(current.item.id,controller.signal);if(!valid())return;current.imageSrc=src;current.imageStatus='ready';renderReminderDetail();}
  catch(error){if(!state.authenticated||epoch!==authEpoch||selectedReminder!==current||sequence!==detailSequence||!sheet.open)return;if(error.name==='AbortError'&&!timedOut)return;current.imageSrc='';current.imageStatus='error';current.imageError=timedOut?'A prévia demorou mais que o esperado.':error.message||'Não foi possível carregar a imagem.';renderReminderDetail();}
  finally{clearTimeout(timer);if(controllers.get('reminder-image')===controller)controllers.delete('reminder-image');}
}
function reminderImageFailed(target){
  const current=selectedReminder;if(!current||current.sequence!==detailSequence||!state.authenticated||current.imageStatus!=='ready'||target?.dataset?.reminderImage!=='true'||target!==$('[data-reminder-image="true"]')||target.getAttribute('src')!==current.imageSrc)return;
  current.imageSrc='';current.imageStatus='error';current.imageError='O navegador não conseguiu exibir a imagem.';renderReminderDetail();
}
document.addEventListener('error',event=>reminderImageFailed(event.target),true);
async function refresh(){if(!state.authenticated||state.revalidating)return;await Promise.allSettled([loadCalendar(),loadReminders()]);}
document.addEventListener('click',async event=>{
  if(event.target.closest('.skip-link')){event.preventDefault();if(state.authenticated){page.focus();page.scrollIntoView({block:'start'});}else $('#access-title')?.focus();return;}
  const control=event.target.closest('[data-action]');if(!control||control.disabled)return;const action=control.dataset.action;
  if(action==='begin-pairing'||action==='restart-pairing'){beginPairing();return;}
  if(action==='copy-command'){const command=commandFor(state.pending?.pairing_code||'');if(command)try{await navigator.clipboard.writeText(command);toast('Comando copiado. Execute na pasta do projeto.');}catch{toast('Selecione e copie o comando mostrado acima.');}return;}
  if(!state.authenticated)return;
  if(action==='logout'){logout();return;}
  if(action==='refresh'){refresh();return;}
  if(action==='refresh-reminders'){loadReminders();return;}
  if(action==='new-mutation'){beginMutation('create');return;}
  if(action==='edit-mutation'){beginMutation('edit');return;}
  if(action==='reschedule-mutation'){beginMutation('reschedule');return;}
  if(action==='delete-mutation'){beginMutation('delete');return;}
  if(action==='preview-mutation'){previewMutation();return;}
  if(action==='confirm-mutation'){confirmMutation();return;}
  if(action==='check-mutation'){checkMutation();return;}
  if(action==='discard-mutation'){discardMutation();return;}
  if(action==='revise-mutation'){reviseMutation();return;}
  if(action==='keep-mutation'){if(mutation){mutation.phase=mutation.previousPhase||'form';renderMutation();}return;}
  if(action==='resume-mutation'){if(mutation){mutation.closed=false;renderMutation();}return;}
  if(action==='reload-mutation-event'){const id=mutation?.event?.id;mutation=null;mutationSequence++;if(id)eventDetail(id);return;}
  if(action==='event'){eventDetail(control.dataset.id);return;}
  if(action==='reminder'){reminderDetail(control.dataset.id);return;}
  if(action==='preview-reminder-card'){previewReminderCard();return;}
  if(action==='close-sheet'){closeSheet();return;}
  if(action==='prepare'){state.assist.tab='briefing';state.prepId=control.dataset.id;closeSheet();go('preparo');loadPrep(state.prepId);return;}
  if(action==='retry-prep'){if(state.prepId)loadPrep(state.prepId);return;}
  if(action==='assist-tab'){if(['briefing','summary','availability'].includes(control.dataset.value)){state.assist.tab=control.dataset.value;render();}return;}
  if(action==='summary-period'){aiInput('summary','period',control.dataset.value);return;}
  if(action==='prep-week'){state.view='week';loadCalendar(state.date||state.today,'week');return;}
  if(action==='prep-agenda'){go('agenda');return;}
  if(action==='notifications'){go('lembretes');loadReminders();return;}
  if(action==='week-agenda'){go('agenda');state.view='week';state.search='';loadCalendar(state.date,'week');return;}
  if(action==='agenda-view'){state.view=control.dataset.value;state.search='';loadCalendar(state.date,state.view);return;}
  if(action==='select-day'||action==='month-day'){state.date=control.dataset.date;state.view='day';state.search='';loadCalendar(state.date,'day');return;}
  if(action==='today'){state.date=state.today;loadCalendar(state.today,state.view);return;}
  if(action==='previous'||action==='next'){if(!state.date)return;const direction=action==='next'?1:-1;if(state.view==='month'){const date=civil(state.date);date.setUTCDate(1);date.setUTCMonth(date.getUTCMonth()+direction);state.date=date.toISOString().slice(0,10);}else state.date=addDays(state.date,direction*(state.view==='week'?7:1));loadCalendar(state.date,state.view);return;}
  if(action==='reminder-filter'){if(!(control.dataset.value in filterStates))return;state.reminderFilter=control.dataset.value;state.reminders={status:'idle',items:[],nextCursor:null,total:null,counts:null,error:'',snapshotAt:null};loadReminders();return;}
  if(action==='more-reminders'){if(state.reminders.nextCursor)loadReminders(true);return;}
});
document.addEventListener('submit',event=>{if(event.target.id==='assist-summary-form'){event.preventDefault();generateSummary();return;}if(event.target.id==='assist-availability-form'){event.preventDefault();findAvailability();return;}if(event.target.id==='assist-briefing-form'){event.preventDefault();generateBriefing();return;}if(event.target.id==='mutation-form'){event.preventDefault();previewMutation();}});
document.addEventListener('input',event=>{if(event.target.dataset?.assistKind){aiInput(event.target.dataset.assistKind,event.target.dataset.assistField,event.target.value);return;}if(event.target.dataset?.mutationField){mutationInput(event.target.dataset.mutationField,event.target.value);return;}if(event.target.id==='agenda-search'){const value=event.target.value;state.search=value;render();const field=$('#agenda-search');field?.focus({preventScroll:true});}});
document.addEventListener('keydown',event=>{const tab=event.target.closest?.('[data-action="assist-tab"]');if(!tab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const ids=['briefing','summary','availability'],index=ids.indexOf(state.assist.tab);state.assist.tab=event.key==='Home'?ids[0]:event.key==='End'?ids[2]:ids[(index+(event.key==='ArrowRight'?1:2))%3];render();$('#assist-tab-'+state.assist.tab)?.focus();});
document.addEventListener('change',event=>{if(event.target.id==='prep-selector')loadPrep(event.target.value);if(event.target.id==='prep-reference-date'&&isoDate(event.target.value)){state.view='day';loadCalendar(event.target.value,'day');}if(event.target.dataset?.prepCheck!==undefined)togglePrepCheck(Number(event.target.dataset.prepCheck),event.target.checked);});
document.addEventListener('visibilitychange',()=>{if(document.hidden){if(state.authenticated){clearReminderDetail();detailSequence++;abort('detail');$('#app-root').hidden=true;if(sheet.open)sheet.close();$('#sheet-content').replaceChildren();if(mutation)mutation.closed=true;}return;}if(state.authenticated)return revalidateSession();if(state.pending){clearTimeout(pendingPoll);return session({quiet:true}).catch(()=>schedulePairing());}});
window.addEventListener('pagehide',event=>{if(event.persisted){revalidationPending=null;clearPrivateState();state.checking=true;$('#app-root').hidden=true;renderAccess();}});
window.addEventListener('pageshow',event=>{if(event.persisted)return revalidateSession({force:true});});
setInterval(()=>{if(state.authenticated&&!state.revalidating&&!document.hidden&&!sheet.open&&state.calendar.status!=='loading'&&state.reminders.status!=='loading')refresh();},45000);
installIcons();renderAccess();
session().catch(error=>{if(error.name!=='AbortError'){state.checking=false;state.authError=error.message||'O painel não está disponível neste momento.';renderAccess();}});
