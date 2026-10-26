// --sandbox gera somente a ponte do agente, sem modificar os outros exports.
if (process.argv.includes('--sandbox')) {
  const fs = require('node:fs');
  const path = require('node:path');
  const lib = require('./lib/whatsapp-sandbox.cjs');
  const file = path.join(__dirname, 'workflows/whatsapp-sandbox.json');
  const workflow = JSON.parse(fs.readFileSync(file, 'utf8'));
  const names = {'Verificar API': 'Enfileirar agente', 'Normalizar status': 'Confirmar fila'};
  for (const node of workflow.nodes) node.name = names[node.name] || node.name;
  const connections = {};
  for (const [name, connection] of Object.entries(workflow.connections)) {
    connections[names[name] || name] = connection;
    for (const output of connection.main) for (const link of output) link.node = names[link.node] || link.node;
  }
  workflow.connections = connections;
  const nodes = Object.fromEntries(workflow.nodes.map(node => [node.name, node]));
  workflow.name = 'Genius Agenda AI - WhatsApp com agente';
  nodes['Enfileirar agente'].parameters.url = 'http://host.docker.internal:8000/api/v1/whatsapp/sandbox/agent';
  nodes['Validar metadados'].parameters.jsCode = lib.validateSandboxMetadata.toString() +
    '\nreturn [{json: validateSandboxMetadata($input.first().json)}];\n';
  nodes['Confirmar fila'].parameters.jsCode = lib.sandboxApiFailure.toString() + '\n' +
    lib.normalizeSandboxAgentStatus.toString() +
    "\nconst metadata = $('Validar metadados').first().json;\n" +
    'return [{json: normalizeSandboxAgentStatus($input.first().json, metadata.request_id, metadata.should_reply)}];\n';
  nodes['Falha segura'].parameters.jsCode = lib.sandboxApiFailure.toString() +
    "\nreturn [{json: sandboxApiFailure($('Validar metadados').first().json.request_id)}];\n";
  fs.writeFileSync(file, JSON.stringify(workflow, null, 2) + '\n');
  console.log('Export do sandbox atualizado para a fila do agente; somente metadados.');
} else {
// Gera o Code node a partir da mesma biblioteca exercitada pelos testes.
const fs = require('node:fs');
const path = require('node:path');
const file = path.join(__dirname, 'workflows/consulta-eventos.json');
const source = fs.readFileSync(path.join(__dirname, 'lib/present-api-response.js'), 'utf8')
  .replace(/\nif \(typeof module[^\n]*\n?$/, '\n');
const workflow = JSON.parse(fs.readFileSync(file, 'utf8'));
const nodes = Object.fromEntries(workflow.nodes.map(node => [node.name, node]));
nodes['Normalizar resposta'].parameters.jsCode = source + `
const input = $input.first().json;
const response = presentApiResponse({
  statusCode: Object.hasOwn(input, 'statusCode') ? input.statusCode : input.http_status,
  body: Object.hasOwn(input, 'body') ? input.body : input.response,
}, {action: 'list', phase: 'read', request_id: \`n8n-\${$execution.id}\`});
return [{json: response}];
`;
const link = name => ({node: name, type: 'main', index: 0});
workflow.connections['Consulta valida?'].main[1] = [link('Normalizar resposta')];
workflow.connections['Falha de conexao'].main[0] = [link('Normalizar resposta')];
nodes['Responder pedido'].parameters.responseBody = '={{ { ...$json.response, presentation: $json.presentation } }}';
fs.writeFileSync(file, JSON.stringify(workflow, null, 2) + '\n');
console.log('Export atualizado com o apresentador compartilhado; alvo GET preservado.');

}
