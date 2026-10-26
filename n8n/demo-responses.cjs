// Demonstracao local com dados ficticios. Nao acessa nem altera a agenda.
const {presentApiResponse} = require('./lib/present-api-response.js');
const fixtures = require('./tests/fixtures/response-cases.json');
const cases = Array.isArray(fixtures) ? fixtures : fixtures.cases;
const steps = {provide_details: 'aguardar os dados', choose_another_time: 'aguardar outro horário',
  confirm_selected_event: 'aguardar confirmação', verify_state: 'conferir a agenda',
  await_requested_action: 'aguardar a ação solicitada', done: 'encerrar'};
console.log('DEMONSTRAÇÃO FICTÍCIA — sem HTTP, Google ou alterações na agenda.\n');
for (const item of cases) {
  const result = presentApiResponse(item.input, item.context);
  console.log(`${item.name} [${result.presentation.kind}]`);
  console.log(result.presentation.text);
  console.log(`Próximo passo: ${steps[result.presentation.next_step] ?? 'conferir o pedido'}; repetição automática: não.\n`);
}
