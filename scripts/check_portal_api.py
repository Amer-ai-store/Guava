"""Real local workerd/D1 tests for the Pages bearer channel; synthetic keys only."""
import base64
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / 'deploy/cloudflare'
ORIGIN = 'https://amer-ai-store.github.io'
PREFIX = 'TrustSoft central browser session v1\n'


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def main():
    cases = []
    with tempfile.TemporaryDirectory(prefix='trustsoft-portal-test-') as folder:
        temp = Path(folder)
        env = dict(os.environ, WRANGLER_SEND_METRICS='false', NODE_USE_ENV_PROXY='1')
        for name in ('CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_API_KEY', 'CLOUDFLARE_EMAIL'):
            env.pop(name, None)
        config = json.loads((PROJECT / 'wrangler.jsonc').read_text())
        config['main'] = str(PROJECT / 'src/worker.mjs')
        config['assets']['directory'] = str(ROOT / 'web')
        config['d1_databases'][0]['migrations_dir'] = str(PROJECT / 'migrations')
        config_path = temp / 'wrangler.json'
        config_path.write_text(json.dumps(config))
        keys = {name: secrets.token_urlsafe(48) for name in
                ('TRUSTSOFT_API_TOKEN', 'TRUSTSOFT_ADMIN_TOKEN', 'TRUSTSOFT_AGENT_TOKEN', 'SESSION_SIGNING_KEY')}
        variables = temp / '.dev.vars'

        def write_keys():
            variables.write_text(''.join(k + '=' + v + '\n' for k, v in keys.items()))
            variables.chmod(0o600)

        write_keys()
        base = [str(PROJECT / 'node_modules/.bin/wrangler'), '--config', str(config_path)]
        migration = subprocess.run(base + ['d1', 'migrations', 'apply', 'DB', '--local',
                                         '--persist-to', str(temp / 'state')],
                                   env=env, capture_output=True, text=True)
        if migration.returncode:
            raise RuntimeError('Local D1 migration failed')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = None
        log = (temp / 'runtime.log').open('w')

        def request(path, method='GET', data=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=15)
            headers = dict(headers or {})
            raw = json.dumps(data) if data is not None else None
            if data is not None:
                headers['Content-Type'] = 'application/json'
            try:
                connection.request(method, path, raw, headers)
                response = connection.getresponse()
                body = response.read()
                result = json.loads(body) if response.getheader('Content-Type', '').startswith('application/json') else body
                return response.status, {k.lower(): v for k, v in response.getheaders()}, result
            finally:
                connection.close()

        def stop():
            nonlocal process
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)

        def start():
            nonlocal process
            process = subprocess.Popen(base + ['dev', '--local', '--ip', '127.0.0.1', '--port', str(port),
                                               '--persist-to', str(temp / 'state'), '--log-level', 'error'],
                                       env=env, stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Local Worker exited during startup')
                try:
                    if request('/healthz')[0] == 200:
                        return
                except OSError:
                    pass
                time.sleep(.1)
            raise RuntimeError('Local Worker startup timeout')

        def central(session=None):
            result = {'Origin': ORIGIN, 'X-TrustSoft-Client': 'central', 'Sec-Fetch-Site': 'cross-site'}
            if session:
                result['Authorization'] = 'Bearer ' + session
            return result

        def login(role, portal=True):
            key = keys['TRUSTSOFT_ADMIN_TOKEN' if role == 'admin' else 'TRUSTSOFT_API_TOKEN']
            status, headers, data = request('/api/admin/login' if role == 'admin' else '/api/login',
                                            'POST', {'token': key}, central() if portal else {})
            assert status == 200, status
            if portal:
                assert set(data) == {'ok', 'session', 'expires_in'} and data['expires_in'] == 900
                assert headers['access-control-allow-origin'] == ORIGIN
                assert 'set-cookie' not in headers and 'access-control-allow-credentials' not in headers
                assert key not in data['session']
                return data['session']
            assert 'session' not in data and 'SameSite=Strict' in headers['set-cookie']
            return headers['set-cookie'].split(';')[0]

        def signed_fixture(role='admin', audience=ORIGIN, expiry=None, prefixed=True):
            key = keys['TRUSTSOFT_ADMIN_TOKEN' if role == 'admin' else 'TRUSTSOFT_API_TOKEN']
            payload = {'role': role, 'aud': audience, 'exp': expiry if expiry is not None else time.time() + 60,
                       'nonce': 'local-test-only', 'ver': b64(hashlib.sha256(key.encode()).digest())}
            body = b64(json.dumps(payload, separators=(',', ':')).encode())
            signature = hmac.new(keys['SESSION_SIGNING_KEY'].encode(), ((PREFIX if prefixed else '') + body).encode(), hashlib.sha256).digest()
            return body + '.' + b64(signature)

        try:
            start()
            preflight = {'Origin': ORIGIN, 'Access-Control-Request-Method': 'POST',
                         'Access-Control-Request-Headers': 'content-type,x-trustsoft-client'}
            status, headers, _ = request('/api/admin/login', 'OPTIONS', headers=preflight)
            assert status == 204 and headers['access-control-allow-origin'] == ORIGIN
            assert 'authorization' in headers['access-control-allow-headers']
            assert 'access-control-allow-credentials' not in headers
            for bad in ({**preflight, 'Origin': 'https://evil.example'},
                        {**preflight, 'Access-Control-Request-Headers': 'content-type'},
                        {**preflight, 'Access-Control-Request-Headers': 'x-trustsoft-client,x-unapproved'}):
                status, headers, _ = request('/api/admin/login', 'OPTIONS', headers=bad)
                assert status == 403 and 'access-control-allow-origin' not in headers
            cases.append('exact_origin_preflight_allowlist_without_cross_site_cookies')

            admin, reader = login('admin'), login('reader')
            assert request('/api/control/state', headers=central(admin))[0] == 200
            assert request('/api/snapshot', headers=central(reader))[0] == 200
            assert request('/api/control/state', headers=central(reader))[0] == 403
            status, headers, data = request('/api/health', headers=central(reader))
            assert status == 503 and data['state'] == 'disconnected'
            assert headers['access-control-allow-origin'] == ORIGIN
            cases.append('short_bearer_roles_and_cors_for_error_responses')

            for bad in ({k: v for k, v in central(admin).items() if k != 'Origin'},
                        {k: v for k, v in central(admin).items() if k != 'X-TrustSoft-Client'},
                        {**central(admin), 'Origin': 'https://evil.example'},
                        {**central(admin), 'X-TrustSoft-Client': 'other'}):
                assert request('/api/control/state', headers=bad)[0] == 401
            for name in ('TRUSTSOFT_ADMIN_TOKEN', 'TRUSTSOFT_API_TOKEN', 'TRUSTSOFT_AGENT_TOKEN'):
                assert request('/api/control/state', headers=central(keys[name]))[0] == 401
            for bad in ({'X-TrustSoft-Client': 'central'},
                        {'Origin': 'https://evil.example', 'X-TrustSoft-Client': 'central'},
                        {'Origin': ORIGIN}):
                assert request('/api/admin/login', 'POST', {'token': keys['TRUSTSOFT_ADMIN_TOKEN']}, bad)[0] == 403
            cases.append('missing_or_wrong_origin_client_and_raw_keys_fail_closed')

            for fixture in (signed_fixture(expiry=time.time() - 1),
                            signed_fixture(audience='https://evil.example'),
                            signed_fixture(expiry=time.time() + 3600),
                            signed_fixture(prefixed=False)):
                assert request('/api/control/state', headers=central(fixture))[0] == 401
            cases.append('expiry_audience_lifetime_and_signature_domain_separation')

            admin_cookie, reader_cookie = login('admin', False), login('reader', False)
            assert request('/api/control/state', headers={'Cookie': admin_cookie})[0] == 200
            assert request('/api/snapshot', headers={'Cookie': reader_cookie})[0] == 200
            assert request('/api/control/state', headers={'Cookie': 'ts_admin=' + admin})[0] == 401
            assert request('/api/control/state', headers=central(admin_cookie.split('=', 1)[1]))[0] == 401
            assert request('/api/control/state', headers={**central(), 'Cookie': admin_cookie})[0] == 401
            cases.append('existing_first_party_cookies_preserved_and_cannot_cross_auth_channels')

            assert request('/api/control/jobs', 'POST', {'action': 'reconnect'}, central(admin))[0] == 403
            assert request('/api/control/jobs', 'POST', {'action': 'reconnect'},
                           {**central(reader), 'X-TrustSoft-Action': '1'})[0] == 403
            status, _, queued = request('/api/control/jobs', 'POST', {'action': 'reconnect'},
                                         {**central(admin), 'X-TrustSoft-Action': '1'})
            assert status == 202
            state = request('/api/control/state', headers=central(admin))[2]
            job = next(item for item in state['jobs'] if item['id'] == queued['id'])
            assert job['status'] == 'queued' and job['completed'] is None
            assert state['online'] is False and state['last_seen'] is None
            cases.append('admin_csrf_action_header_and_queued_job_is_not_sql_success')

            for path, method in (('/api/agent/hello', 'POST'), ('/api/agent/poll', 'POST'),
                                 ('/api/agent/result', 'POST'), ('/healthz', 'GET')):
                status, headers, _ = request(path, 'OPTIONS', headers={**preflight, 'Access-Control-Request-Method': method})
                assert status == 403 and 'access-control-allow-origin' not in headers
            status, headers, _ = request('/api/agent/hello', 'POST', {'agent_id': 'JAWAFFASVR'},
                                         {'Authorization': 'Bearer ' + keys['TRUSTSOFT_AGENT_TOKEN']})
            assert status == 200 and 'access-control-allow-origin' not in headers
            cases.append('windows_agent_auth_preserved_without_browser_cors')

            stop()
            keys['TRUSTSOFT_ADMIN_TOKEN'] = secrets.token_urlsafe(48)
            write_keys()
            start()
            assert request('/api/control/state', headers=central(admin))[0] == 401
            assert request('/api/control/state', headers={'Cookie': admin_cookie})[0] == 401
            assert request('/api/snapshot', headers=central(reader))[0] == 200
            assert request('/api/snapshot', headers={'Cookie': reader_cookie})[0] == 200
            fresh = login('admin')
            assert request('/api/control/state', headers=central(fresh))[0] == 200
            cases.append('key_rotation_revokes_old_admin_bearer_and_cookie_preserving_reader')
            report = {'runtime': 'official local workerd via Wrangler', 'storage': 'local D1',
                      'all_passed': True, 'passed_cases': cases,
                      'central_origin': ORIGIN, 'session_lifetime_seconds': 900,
                      'cloud_resources_created': False, 'production_sql_connected': False,
                      'physical_android_tested': False, 'public_https_deployed': False}
            output = ROOT / 'docs/evidence/portal-api-results.json'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
        finally:
            stop()
            log.close()


if __name__ == '__main__':
    main()
