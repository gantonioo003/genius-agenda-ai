'use strict';
// Execute the Code nodes and follow the connections of the unchanged export.
// HTTP is a fixture boundary; this is not an execution inside the n8n engine.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const workflow = JSON.parse(fs.readFileSync(path.join(__dirname,'../workflows/lembretes-periodicos.json'),'utf8'));
const PRIVATE = 'PRIVATE_PROVIDER_DETAIL_AND_MESSAGE';
const clone = value => JSON.parse(JSON.stringify(value));
const sync = () => ({status:'synchronized',events_checked:0,tracked_reminders:0,reminders_considered:0,
  cancelled_count:0,replanned_count:0,unchanged_count:0,state_counts:{}});
const generation = () => ({status:'planned',events_seen:0,eligible_events:0,skipped_count:0,
  reminders_considered:0,state_counts:{}});
function response(operation, requestId) {
  const data = operation === 'recover' ? {recovery_status:'completed',skip_reason:null,
    reservations_recovered:0,expired_count:0,uncertain_count:0,protected_count:0,messages_sent:0,external_mutations:0} :
    operation === 'tick' ? {tick_status:'completed',skip_reason:null,local_mutation_state:'applied',
      synchronization:sync(),generation:generation(),messages_sent:0,external_mutations:0} :
    {dispatch_status:'completed',skip_reason:null,reminders_considered:1,expired_count:0,cancelled_count:0,
      blocked_count:0,accepted_count:1,uncertain_count:0,failed_count:0,transport_attempts:1};
  return {statusCode:200,headers:{authorization:PRIVATE},body:{api_version:'1',request_id:requestId,
    operation_id:null,status:'succeeded',action:operation === 'recover' ? 'reminder_recovery' :
      operation === 'tick' ? 'reminder_tick' : 'reminder_dispatch',
    mutation_state:operation === 'dispatch' ? 'confirmed' : 'not_applicable',message:PRIVATE,data,error:null}};
}
function skipped(operation, id, reason='disabled') {
  const value=response(operation,id);value.body.status='skipped';
  const data=value.body.data;
  if (operation === 'recover') data.recovery_status='skipped';
  else if (operation === 'tick') {
    Object.assign(data,{tick_status:'skipped',local_mutation_state:'not_applied'});
    data.synchronization.status='not_run';data.generation.status='not_run';
  } else {
    Object.assign(data,{dispatch_status:'skipped',reminders_considered:0,accepted_count:0,transport_attempts:0});
    value.body.mutation_state='not_applied';
  }
  data.skip_reason=reason;return value;
}
function failed(operation, id, {partial=false, unknown=false, accepted=false}={}) {
  const value=response(operation,id);
  value.statusCode=503;value.body.status='failed';
  const prefix=operation === 'recover' ? 'reminder_recovery' : operation === 'tick' ? 'reminder_tick' : 'reminder_dispatch';
  value.body.error={code:prefix+(partial?'_partial':'_failed'),retryable:false,fields:[]};
  if (unknown) {
    value.body.data=null;
    if (operation==='dispatch') value.body.mutation_state='unknown';
  } else if (operation==='recover') {
    Object.assign(value.body.data,{recovery_status:'failed',reservations_recovered:1});
  } else if (operation==='tick') {
    const data=value.body.data;
    Object.assign(data,{tick_status:partial?'partial':'failed',local_mutation_state:partial?'partial':'not_applied'});
    data.synchronization.status=partial?'synchronized':'failed';
    data.generation.status=partial?'failed':'not_run';
  } else {
    Object.assign(value.body.data,{dispatch_status:'partial',accepted_count:accepted?1:0,
      uncertain_count:accepted?0:1,failed_count:accepted?1:0});
    value.body.mutation_state=accepted?'confirmed':'unknown';
    value.body.error.code='reminder_dispatch_partial';
  }
  return value;
}
function runGraph({executionId='system-42',responses={},transportError}={}) {
  const nodes = new Map(workflow.nodes.map(node=>[node.name,node]));
  const outputs = new Map();const trace={visited:[],http:[],outputs:[],result:null,error:null};
  let node=workflow.nodes.find(value=>value.type==='n8n-nodes-base.scheduleTrigger');
  let input={};
  try {
    for (let count=0;node;count+=1) {
      if (count>=workflow.nodes.length) throw new Error('test_graph_cycle');
      trace.visited.push(node.name);
      let branch=0;
      if (node.type==='n8n-nodes-base.code') {
        const result=vm.runInNewContext('(function(){\n'+node.parameters.jsCode+'\n})()',{
          $execution:{id:executionId},$input:{first:()=>({json:input})},
          $:name=>({first:()=>({json:outputs.get(name)})}),
        },{timeout:1000});
        assert.equal(result.length,1);input=clone(result[0].json);
        trace.outputs.push({name:node.name,json:input});
      } else if (node.type==='n8n-nodes-base.httpRequest') {
        const operation=node.parameters.url.split('/').at(-1);
        assert.ok(['recover','tick','dispatch'].includes(operation));
        assert.equal(node.parameters.url,'http://host.docker.internal:8000/api/v1/reminders/'+operation);
        assert.equal(node.parameters.method,'POST');assert.equal(node.parameters.jsonBody,'{}');
        assert.equal(node.retryOnFail,false);assert.equal(node.onError,'continueErrorOutput');
        assert.equal(node.parameters.options.redirect.redirect.followRedirects,false);
        const requestId=input.request_id;
        trace.http.push({operation,request_id:requestId,body:{}});
        if (transportError===operation) {
          input={error:{message:PRIVATE}};branch=1;
        } else {
          const factory=responses[operation] || response;
          input=clone(factory(operation,requestId));
        }
      }
      outputs.set(node.name,input);
      const links=workflow.connections[node.name];
      if (!links) {trace.result=clone(input);break;}
      const edges=links.main[branch];assert.equal(edges.length,1);
      node=nodes.get(edges[0].node);assert.ok(node);
    }
  } catch(error) {trace.error=error.message;}
  // Only the projected outputs and safe fixed errors leave the simulated graph.
  assert.equal(JSON.stringify({outputs:trace.outputs,result:trace.result,error:trace.error}).includes(PRIVATE),false);
  return trace;
}

test('export integral percorre oito nos e tres POSTs com correlacao unica e resultado projetado',()=>{
  const trace=runGraph();assert.equal(trace.error,null);
  assert.equal(trace.visited.length,8);
  assert.deepEqual(trace.http.map(row=>row.operation),['recover','tick','dispatch']);
  assert.deepEqual(new Set(trace.http.map(row=>row.request_id)),new Set(['reminder-tick-system-42']));
  assert.equal(trace.result.accepted_count,1);assert.equal(trace.result.mutation_state,'confirmed');
});
test('trio de skips validos nao fabrica aceite nem transporte',()=>{
  const trace=runGraph({responses:{recover:skipped,tick:skipped,dispatch:skipped}});
  assert.equal(trace.error,null);assert.equal(trace.result.dispatch_status,'skipped');
  assert.equal(trace.result.transport_attempts,0);assert.equal(trace.result.accepted_count,0);
});
test('busy atravessa a mesma sequencia apenas como skip, sem fingir conclusao de trabalho',()=>{
  const busy=(operation,id)=>skipped(operation,id,'busy');
  const trace=runGraph({responses:{recover:busy,tick:busy,dispatch:busy}});
  assert.equal(trace.error,null);assert.equal(trace.result.skip_reason,'busy');assert.equal(trace.result.transport_attempts,0);
});
for (const [label,factory] of [
  ['commit conhecido com cleanup falho',(op,id)=>failed(op,id)],
  ['commit de resultado desconhecido',(op,id)=>failed(op,id,{unknown:true})],
  ['DTO malformado',()=>({statusCode:200,body:{message:PRIVATE}})],
  ['correlacao divergente',(op,id)=>response(op,id+'-wrong')],
]) test('recovery interrompe grafo antes de tick e dispatch: '+label,()=>{
  const trace=runGraph({responses:{recover:factory}});
  assert.match(trace.error,/^reminder_recovery_(failed|invalid_response)$/);
  assert.deepEqual(trace.http.map(row=>row.operation),['recover']);assert.equal(trace.result,null);
});
for (const [label,factory] of [
  ['falha de sincronizacao',(op,id)=>failed(op,id)],
  ['falha parcial depois de sincronizar',(op,id)=>failed(op,id,{partial:true})],
  ['DTO malformado',()=>({statusCode:200,body:{message:PRIVATE}})],
  ['correlacao divergente',(op,id)=>response(op,id+'-wrong')],
]) test('tick interrompe grafo antes do dispatch: '+label,()=>{
  const trace=runGraph({responses:{tick:factory}});
  assert.match(trace.error,/^reminder_tick_(failed|partial|invalid_response)$/);
  assert.deepEqual(trace.http.map(row=>row.operation),['recover','tick']);assert.equal(trace.result,null);
});
for (const operation of ['recover','tick','dispatch']) test('erro de transporte vai ao normalizador sem repetir HTTP: '+operation,()=>{
  const trace=runGraph({transportError:operation});
  assert.match(trace.error,/^reminder_(recovery|tick|dispatch)_invalid_response$/);
  assert.equal(trace.http.filter(row=>row.operation===operation).length,1);
  assert.deepEqual(trace.http.map(row=>row.operation),['recover','tick','dispatch'].slice(0,['recover','tick','dispatch'].indexOf(operation)+1));
});
for (const accepted of [false,true]) test('dispatch com '+(accepted?'aceite persistido e cleanup falho':'resultado incerto')+' termina sem retry',()=>{
  const trace=runGraph({responses:{dispatch:(op,id)=>failed(op,id,{accepted})}});
  assert.equal(trace.error,'reminder_dispatch_partial');
  assert.equal(trace.http.length,3);assert.equal(trace.http.filter(row=>row.operation==='dispatch').length,1);
});
test('janela bloqueada conclui sem afirmar aceite ou entrega',()=>{
  const trace=runGraph({responses:{dispatch:(op,id)=>{
    const value=response(op,id);value.body.mutation_state='not_applied';
    Object.assign(value.body.data,{blocked_count:1,accepted_count:0,transport_attempts:0});return value;
  }}});
  assert.equal(trace.error,null);assert.equal(trace.result.blocked_count,1);assert.equal(trace.result.accepted_count,0);
});
test('ID de execucao invalido falha na preparacao antes de chamar qualquer API',()=>{
  const trace=runGraph({executionId:'\r\nPRIVATE'});
  assert.equal(trace.error,'reminder_tick_invalid_request_id');assert.deepEqual(trace.http,[]);
});

test('envelopes reais do ensaio TCP atravessam os Code nodes e conexoes do export',
  {skip:!process.env.REMINDER_SYSTEM_REPORT_PATH},()=>{
    const report=JSON.parse(fs.readFileSync(process.env.REMINDER_SYSTEM_REPORT_PATH,'utf8'));
    assert.ok(Array.isArray(report.scenarios));
    const cycles=report.scenarios.flatMap(scenario=>scenario.cycles || []);
    assert.ok(cycles.length>0);
    for (const cycle of cycles) {
      assert.equal(cycle.request_id,'reminder-tick-'+cycle.execution_id);
      assert.deepEqual(cycle.responses.map(row=>row.operation),['recover','tick','dispatch']);
      const responses=Object.fromEntries(cycle.responses.map(row=>[row.operation,(_operation,id)=>{
        assert.equal(id,cycle.request_id);
        assert.equal(row.body.request_id,cycle.request_id);
        return {statusCode:row.http_status,body:row.body};
      }]));
      const trace=runGraph({executionId:cycle.execution_id,responses});
      assert.equal(trace.error,null);
      assert.equal(trace.http.length,3);
      assert.equal(trace.result.accepted_count,cycle.responses.at(-1).body.data.accepted_count);
      assert.equal(trace.result.transport_attempts,cycle.responses.at(-1).body.data.transport_attempts);
    }
  });
