"""Ensaio n8n/HTTP real com Google em memoria e um disparo natural agendado."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

CONTAINER = 'genius-agenda-ai-n8n'
ZONE = 'America/Recife'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Publicar temporariamente os dois fluxos de teste; reinicia o n8n')
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--artifacts-root', type=Path, help='Pasta dos artefatos; padrao: raiz do projeto')
    args = parser.parse_args()
    if not args.apply:
        parser.error('Use --apply para executar o ensaio local e sua limpeza automatica.')
    root = args.project_root.resolve()
    artifacts = (args.artifacts_root or root).resolve()
    docker = shutil.which('docker') or str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Docker/Docker/resources/bin/docker.exe')
    local = json.loads((root / 'secrets/n8n-local.json').read_text(encoding='utf-8'))
    runs = root / 'data/n8n-integration-runs'
    runs.mkdir(parents=True, exist_ok=True)
    lock = runs / 'running.lock'
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(lock_fd)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(3)
    folder = runs / run_id
    folder.mkdir()
    report = {'run_id': run_id, 'scope': 'n8n e HTTP reais; API/servicos/SDK/SQLite reais; somente HTTP Google em memoria. Agendamento consulta status da API normal8000.',
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'cases': [], 'google_real_mutations': 0, 'schedule': None, 'cleanup': None}
    fixture_key, hook_key = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    worker = None
    published = False
    ids = None

    def command(*parts, payload=None):
        result = subprocess.run([docker, *parts], input=payload, text=True, encoding='utf-8', capture_output=True, timeout=90)
        if result.returncode or 'an error occurred' in result.stdout.lower():
            raise RuntimeError('Docker/n8n recusou uma operacao do ensaio; nenhuma chave foi exibida.')
        return result.stdout

    def http(url, body=None, headers=None, timeout=35):
        request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers or {}, method='POST' if body is not None else 'GET')
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read().decode('utf-8')
            try: value = json.loads(raw)
            except ValueError: value = {'native_response': raw}
            return response.status, value

    def ready(port):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if http(f'http://127.0.0.1:{port}/health' if port == 8001 else 'http://127.0.0.1:5678/healthz/readiness', timeout=2)[0] == 200:
                    return
            except OSError: pass
            time.sleep(.3)
        raise RuntimeError('Um servico local nao ficou pronto no prazo do ensaio.')

    def import_json(kind, data):
        path = '/tmp/genius-test-' + secrets.token_hex(8) + '.json'
        try:
            command('exec', '-i', '-u', 'node', CONTAINER, 'node', '-e',
                    "const f=require('fs');f.writeFileSync(process.argv[1],f.readFileSync(0),{mode:0o600,flag:'wx'});", path, payload=json.dumps(data))
            output = command('exec', '-u', 'node', CONTAINER, 'n8n', 'import:' + kind, '--input=' + path)
            if 'Successfully imported' not in output: raise RuntimeError('Importacao do ensaio nao foi confirmada.')
        finally:
            command('exec', '-u', 'node', CONTAINER, 'node', '-e', "require('fs').rmSync(process.argv[1],{force:true})", path)

    def metadata():
        script = '''const {DatabaseSync}=require('node:sqlite');const d=new DatabaseSync('/home/node/.n8n/database.sqlite',{readOnly:true});
const ids=JSON.parse(process.argv[1]);const flows=ids.map(id=>{const r=d.prepare('SELECT id,name,active,staticData FROM workflow_entity WHERE id=?').get(id);if(r)r.staticData=r.staticData?JSON.parse(r.staticData):null;return r});
const executions=d.prepare('SELECT id,mode,startedAt FROM execution_entity WHERE workflowId=? ORDER BY id').all(ids[1]);d.close();console.log(JSON.stringify({flows,scheduled_executions:executions}));'''
        return json.loads(command('exec', '-u', 'node', CONTAINER, 'node', '-e', script, json.dumps([ids['workflow_id'], ids['schedule_id']])))

    def admin(route, body=None):
        status, data = http('http://127.0.0.1:8001/__test/' + route, body, {'Authorization': 'Bearer ' + fixture_key, 'Content-Type': 'application/json'})
        assert status == 200, 'Controle isolado recusado'
        return data

    def sanitize(value):
        if isinstance(value, dict): return {k: '[omitido no registro]' if k == 'confirmation_token' and v else sanitize(v) for k, v in value.items()}
        if isinstance(value, list): return [sanitize(v) for v in value]
        return value

    def check(name, action, *, body=None, query=None, event_id=None, expected=200, kind=None, delta=0, authorized=True):
        before = admin('snapshot')['counts']['writes']
        data = {'action': action}
        if body is not None: data['body'] = body
        if query is not None: data['query'] = query
        if event_id is not None: data['event_id'] = event_id
        headers = {'Content-Type': 'application/json'}
        if authorized: headers['X-Genius-Webhook-Key'] = hook_key
        status, result = http('http://127.0.0.1:5678/webhook/genius-agenda/_integration/operacoes', data, headers)
        report['last_observed'] = {'name': name, 'http_status': status, 'response': sanitize(result), 'snapshot': admin('snapshot')}
        assert status == expected, name + ': HTTP inesperado ' + str(status)
        if authorized:
            assert result['presentation']['auto_retry'] is False and result['presentation']['execution_allowed'] is False, name
            if kind: assert result['presentation']['kind'] == kind, name + ': apresentacao inesperada'
        after = admin('snapshot')['counts']['writes']
        assert after - before == delta, name + ': quantidade inesperada de escritas simuladas'
        report['cases'].append({'name': name, 'http_status': status, 'google_fake_write_delta': delta, 'response': sanitize(result)})
        print(f'{name}: HTTP{status}, escritas simuladas +{delta}', flush=True)
        return result

    def create_body(operation, hour, title):
        return {'operation_id': operation, 'event': {'title': title, 'start': f'2030-06-17T{hour}:00:00-03:00',
                'end': f'2030-06-17T{hour}:30:00-03:00', 'time_zone': ZONE}}

    def remove_fixture(event_id, prefix):
        preview = check(prefix + '_previa', 'preview', event_id=event_id, body={'time_zone': ZONE}, kind='confirmation')
        return check(prefix + '_excluir', 'delete', event_id=event_id,
                     body={'time_zone': ZONE, 'confirmation_token': preview['data']['confirmation_token']}, kind='success', delta=1)

    try:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 8001)) == 0: raise RuntimeError('A porta8001 ja esta ocupada; o ensaio nao substitui servicos existentes.')
        assert http('http://127.0.0.1:8000/health', timeout=3)[0] == 200
        real_headers = {'Content-Type': 'application/json', 'X-Genius-Webhook-Key': local['webhook_token']}
        original_status, original = http('http://127.0.0.1:5678/webhook/genius-agenda/consultar', {'date': '2026-10-23'}, real_headers)
        assert original_status == 200
        report['real_calendar_before'] = original['data']['events']
        state_file = root / 'data/n8n-integration-fixtures.json'
        ids = json.loads(state_file.read_text()) if state_file.exists() else {name: secrets.token_hex(8) for name in ('workflow_id', 'schedule_id', 'api_credential_id', 'webhook_credential_id')}
        assert set(ids) == {'workflow_id', 'schedule_id', 'api_credential_id', 'webhook_credential_id'}
        assert all(isinstance(value, str) and len(value) == 16 and all(c in '0123456789abcdef' for c in value) for value in ids.values())
        assert len(set(ids.values())) == 4
        assert not set(ids.values()).intersection({local['workflow_id'], local['api_credential_id'], local['webhook_credential_id']})
        state_file.write_text(json.dumps(ids, indent=2) + '\n')
        log = open(folder / 'worker.log', 'wb')
        worker = subprocess.Popen([sys.executable, '-u', str(artifacts / 'scripts/n8n_integration_worker.py')], stdin=subprocess.PIPE,
                    stdout=log, stderr=log, cwd=root, text=True, encoding='utf-8',
                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        log.close()
        worker.stdin.write(json.dumps({'project_root': str(root), 'port': 8001, 'api_token': fixture_key,
             'database_path': str(folder / 'agenda.sqlite3'), 'report_path': str(folder / 'backend-report.json')}))
        worker.stdin.close()
        ready(8001)
        credentials = [{'id': ids['api_credential_id'], 'name': 'Genius Fixture API - isolada', 'type': 'httpBearerAuth', 'data': {'token': fixture_key}},
                       {'id': ids['webhook_credential_id'], 'name': 'Genius Fixture Webhook - isolado', 'type': 'httpHeaderAuth', 'data': {'name': 'X-Genius-Webhook-Key', 'value': hook_key}}]
        import_json('credentials', credentials)
        for file, identity in [('integracao-isolada-teste.json', 'workflow_id'), ('disparo-agendado-teste.json', 'schedule_id')]:
            workflow = json.loads((artifacts / 'n8n/workflows' / file).read_text(encoding='utf-8'))
            assert workflow['active'] is False
            workflow.update({'id': ids[identity], 'staticData': {}})
            for node in workflow['nodes']:
                for kind, reference in node.get('credentials', {}).items():
                    reference['id'] = local['api_credential_id'] if identity == 'schedule_id' else ids['api_credential_id' if kind == 'httpBearerAuth' else 'webhook_credential_id']
            import_json('workflow', workflow)
        published = True  # cleanup tambem se houver falha parcial de publicacao
        for identity in ('workflow_id', 'schedule_id'):
            command('exec', '-u', 'node', CONTAINER, 'n8n', 'publish:workflow', '--id=' + ids[identity])
        command('restart', CONTAINER)
        ready(5678)
        report['initial_fixture_snapshot'] = admin('snapshot')

        check('consulta_vazia', 'list', query={'date': '2030-06-17'}, kind='empty')
        check('webhook_sem_chave', 'list', query={'date': '2030-06-17'}, expected=403, authorized=False)
        admin('control', {'lose_response_once': ['POST'], 'fail_reads_after_write': 3})
        uncertain = create_body('uncertain-original', '08', 'Entrevista ficticia incerta')
        result = check('criacao_resultado_incerto', 'create', body=uncertain, expected=502, kind='uncertain', delta=1)
        assert result['mutation_state'] == 'unknown'
        admin('control', {'reset': True})
        reused = check('recuperar_mesma_operacao_sem_novo_POST', 'create', body=uncertain, kind='success')
        assert reused['data']['reused'] is True
        remove_fixture(reused['data']['event_id'], 'limpar_fixture_incerta')

        payload = create_body('lifecycle-original', '10', 'Entrevista ficticia de integracao')
        created = check('criar', 'create', body=payload, expected=201, kind='success', delta=1)
        event_id = created['data']['event_id']
        assert check('repetir_criacao', 'create', body=payload)['data']['event_id'] == event_id
        alias = deepcopy(payload); alias['operation_id'] = 'lifecycle-alias'
        assert check('mesmo_conteudo_outra_operacao', 'create', body=alias)['data']['event_id'] == event_id
        check('consultar_ID', 'get', event_id=event_id, kind='success')
        for period in ('day', 'week', 'month'):
            check('consultar_' + period, 'list', query={'date': '2030-06-17', 'period': period}, kind='success')
        details = {'changes': {'title': 'Entrevista atualizada ficticia', 'description': 'Preparar portfolio', 'location': 'Sala virtual'}, 'time_zone': ZONE}
        check('editar', 'edit', event_id=event_id, body=details, kind='success', delta=1)
        assert check('repetir_edicao', 'edit', event_id=event_id, body=details)['data']['result'] == 'unchanged'
        schedule = {'start': '2030-06-17T11:00:00-03:00', 'end': '2030-06-17T11:30:00-03:00', 'time_zone': ZONE}
        check('reagendar', 'reschedule', event_id=event_id, body=schedule, kind='success', delta=1)
        assert check('repetir_reagendamento', 'reschedule', event_id=event_id, body=schedule)['data']['result'] == 'unchanged'
        conflict = create_body('conflicting-operation', '11', 'Outro compromisso ficticio')
        check('horario_conflitante', 'create', body=conflict, expected=409, kind='conflict')
        check('exclusao_sem_codigo', 'delete', event_id=event_id, body={'time_zone': ZONE}, expected=422, kind='correction')
        check('exclusao_codigo_incorreto', 'delete', event_id=event_id, body={'time_zone': ZONE, 'confirmation_token': '0' * 64}, expected=502, kind='uncertain')
        preview = check('previa_para_versao', 'preview', event_id=event_id, body={'time_zone': ZONE}, kind='confirmation')
        check('mudar_versao', 'edit', event_id=event_id, body={'changes': {'location': 'Outra sala'}, 'time_zone': ZONE}, delta=1)
        check('codigo_obsoleto', 'delete', event_id=event_id, body={'time_zone': ZONE, 'confirmation_token': preview['data']['confirmation_token']}, expected=502, kind='uncertain')
        remove_fixture(event_id, 'lifecycle')
        check('ausencia_confirmada', 'get', event_id=event_id, expected=404, kind='not_found')
        check('exclusao_ja_ausente', 'delete', event_id=event_id, body={'time_zone': ZONE, 'confirmation_token': 'a' * 64}, kind='success')
        invalid = create_body('invalid-interval', '12', 'Intervalo invalido'); invalid['event']['end'] = '2030-06-17T11:30:00-03:00'
        check('intervalo_invalido', 'create', body=invalid, expected=422, kind='correction')
        admin('control', {'fail_next_read': True})
        check('falha_de_leitura', 'list', query={'date': '2030-06-17'}, expected=502, kind='error')
        check('leitura_recuperada', 'list', query={'date': '2030-06-17'}, kind='empty')
        admin('control', {'lose_response_once': ['POST']})
        recovered = check('criacao_resposta_perdida', 'create', body=create_body('recover-create', '14', 'Recuperacao ficticia'), expected=201, kind='success', delta=1)
        assert recovered['data']['recovered'] is True
        recovered_id = recovered['data']['event_id']
        admin('control', {'lose_response_once': ['PATCH']})
        assert check('edicao_resposta_perdida', 'edit', event_id=recovered_id, body=details, kind='success', delta=1)['data']['recovered'] is True
        fresh = check('previa_recuperacao', 'preview', event_id=recovered_id, body={'time_zone': ZONE}, kind='confirmation')
        admin('control', {'lose_response_once': ['DELETE']})
        assert check('exclusao_resposta_perdida', 'delete', event_id=recovered_id, body={'time_zone': ZONE, 'confirmation_token': fresh['data']['confirmation_token']}, kind='success', delta=1)['data']['recovered'] is True
        report['final_fixture_snapshot'] = admin('snapshot')
        assert report['final_fixture_snapshot']['saved_events'] == []
        print('CRUD/falhas aprovados. Aguardando disparo natural do Schedule Trigger...', flush=True)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            observed = metadata()
            summary = ((observed['flows'][1] or {}).get('staticData') or {}).get('global', {}).get('schedule_smoke_test_v1')
            if summary and summary.get('successes', 0) >= 1:
                assert summary['last_mode'] == 'production' and summary['last_http_status'] == 200
                assert datetime.fromisoformat(summary['last_run_at'].replace('Z', '+00:00')) >= datetime.fromisoformat(report['started_at_utc'])
                assert any(row['mode'] == 'trigger' for row in observed['scheduled_executions'])
                report['schedule'] = observed
                print('Disparo agendado natural comprovado: HTTP200, modo production/trigger.', flush=True)
                break
            time.sleep(5)
        else: raise RuntimeError('O disparo agendado nao foi confirmado no prazo.')
        report['passed'] = len(report['cases'])
        report.pop('last_observed', None)
    finally:
        cleanup_errors = []
        if published and ids:
            for identity in ('workflow_id', 'schedule_id'):
                try: command('exec', '-u', 'node', CONTAINER, 'n8n', 'unpublish:workflow', '--id=' + ids[identity])
                except Exception: cleanup_errors.append('Nao foi possivel despublicar um fluxo de teste.')
            try:
                command('restart', CONTAINER); ready(5678)
                observed = metadata()
                assert all(flow and not flow['active'] for flow in observed['flows'])
                report['cleanup'] = {'fixture_flows_unpublished': True, 'workflow_metadata': observed}
            except Exception: cleanup_errors.append('Nao foi possivel confirmar os fluxos despublicados.')
        if worker and worker.poll() is None:
            worker.terminate()
            try: worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill(); worker.wait(timeout=10)
        report['fixture_server_stopped'] = worker is not None and worker.poll() is not None
        if not cleanup_errors:
            try:
                status, final = http('http://127.0.0.1:5678/webhook/genius-agenda/consultar', {'date': '2026-10-23'},
                                     {'Content-Type': 'application/json', 'X-Genius-Webhook-Key': local['webhook_token']})
                assert status == 200 and final['data']['events'] == report['real_calendar_before']
                report['real_calendar_after'] = final['data']['events']; report['real_events_preserved'] = True
            except Exception: cleanup_errors.append('Nao foi possivel confirmar a consulta normal final.')
        report['cleanup_errors'] = cleanup_errors
        (folder / 'integration-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        lock.unlink(missing_ok=True)
        print('Relatorio local: ' + str(folder / 'integration-report.json'), flush=True)
        if cleanup_errors: raise RuntimeError('A limpeza do ensaio requer revisao; confira o relatorio local.')
    print(f'{report["passed"]} verificacoes aprovadas; disparo natural confirmado; fluxos de teste despublicados.', flush=True)


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, AssertionError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, RuntimeError) else 'Ensaio interrompido. Confira o relatorio local; detalhes sensiveis foram omitidos.', file=sys.stderr)
        raise SystemExit(1) from None
