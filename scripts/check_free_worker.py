"""Exercise real workerd and local D1 via Wrangler, without cloud credentials or billing."""
import concurrent.futures
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

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'deploy/cloudflare'

def main():
    cases=[]
    with tempfile.TemporaryDirectory(prefix='trustsoft-worker-test-') as folder:
        temp=Path(folder)
        env=dict(os.environ,WRANGLER_SEND_METRICS='false',NODE_USE_ENV_PROXY='1')
        for name in ('CLOUDFLARE_API_TOKEN','CLOUDFLARE_API_KEY','CLOUDFLARE_EMAIL'):
            env.pop(name,None)
        config=json.loads((PROJECT/'wrangler.jsonc').read_text())
        config['main']=str(PROJECT/'src/worker.mjs')
        config['assets']['directory']=str(ROOT/'web')
        config['d1_databases'][0]['migrations_dir']=str(PROJECT/'migrations')
        config_path=temp/'wrangler.json'
        config_path.write_text(json.dumps(config))
        tokens={name:secrets.token_urlsafe(48) for name in
                ('TRUSTSOFT_API_TOKEN','TRUSTSOFT_ADMIN_TOKEN','TRUSTSOFT_AGENT_TOKEN','SESSION_SIGNING_KEY')}
        variables=temp/'.dev.vars'
        variables.write_text(''.join(k+'='+v+'\n' for k,v in tokens.items()))
        variables.chmod(0o600)
        wrangler=str(PROJECT/'node_modules/.bin/wrangler')
        base=[wrangler,'--config',str(config_path)]
        result=subprocess.run(base+['d1','migrations','apply','DB','--local','--persist-to',str(temp/'state')],
                              env=env,capture_output=True,text=True)
        if result.returncode:
            raise RuntimeError('Local D1 migration failed')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=None
        log=(temp/'runtime.log').open('w')
        def request(path,method='GET',data=None,headers=None):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
            headers=dict(headers or {})
            raw=json.dumps(data) if data is not None else None
            if data is not None:headers['Content-Type']='application/json'
            conn.request(method,path,raw,headers)
            response=conn.getresponse()
            body=response.read()
            parsed=json.loads(body) if response.getheader('Content-Type','').startswith('application/json') else body
            result=(response.status,dict(response.getheaders()),parsed)
            conn.close()
            return result
        def stop():
            nonlocal process
            if process and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                process.wait(timeout=10)
        def start():
            nonlocal process
            process=subprocess.Popen(base+['dev','--local','--ip','127.0.0.1','--port',str(port),
                '--persist-to',str(temp/'state'),'--log-level','error'],env=env,stdout=log,stderr=log,start_new_session=True)
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Local Worker exited during startup')
                try:
                    if request('/healthz')[0]==200:return
                except OSError:pass
                time.sleep(.1)
            raise RuntimeError('Local Worker startup timeout')
        def login(role):
            key='TRUSTSOFT_ADMIN_TOKEN' if role=='admin' else 'TRUSTSOFT_API_TOKEN'
            status,headers,_=request('/api/admin/login' if role=='admin' else '/api/login','POST',{'token':tokens[key]})
            assert status==200, status
            cookie=headers.get('Set-Cookie') or headers.get('set-cookie')
            assert 'Secure' in cookie and 'HttpOnly' in cookie
            return {'Cookie':cookie.split(';')[0],'X-TrustSoft-Action':'1'}
        agent={'Authorization':'Bearer '+tokens['TRUSTSOFT_AGENT_TOKEN']}
        try:
            start()
            assert request('/')[0]==200 and b'data-start-section="control"' in request('/')[2]
            assert request('/api/control/state')[0]==401
            cases.append('real_worker_assets_and_protected_api')
            admin,reader=login('admin'),login('reader')
            assert request('/api/control/jobs','POST',{'action':'reconnect'},reader)[0]==403
            assert request('/api/control/jobs','POST',{'action':'reconnect'},{'Cookie':admin['Cookie']})[0]==403
            assert request('/api/control/jobs','POST',{'action':'cmd.exe'},admin)[0]==409
            cases.append('separate_roles_csrf_and_fixed_action_allowlist')
            hello=request('/api/agent/hello','POST',{'agent_id':'JAWAFFASVR'},agent)
            assert hello[0]==200,hello[2]
            broker_id=hello[2]['broker_id']
            assert request('/api/agent/poll','POST',{'agent_id':'OTHER'},agent)[0]==409
            target=request('/api/control/jobs','POST',{'action':'reconnect'},admin)[2]['id']
            stop();start()
            assert request('/api/agent/hello','POST',{'agent_id':'JAWAFFASVR'},agent)[2]['broker_id']==broker_id
            assert any(j['id']==target for j in request('/api/control/state',headers=admin)[2]['jobs'])
            cases.append('broker_queue_audit_and_signed_session_survive_runtime_restart')
            poll=lambda _:request('/api/agent/poll','POST',{'agent_id':'JAWAFFASVR'},agent)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                answers=list(pool.map(poll,range(2)))
            assert all(a[0]==200 for a in answers),answers
            jobs=[a[2]['job'] for a in answers if a[2]['job']]
            assert len(jobs)==1,jobs
            job=jobs[0]
            assert job['id']==target
            cases.append('atomic_single_lease_under_parallel_polls')
            result={'execution_ok':True,'sql':{'state':'verified_read',
                'identity':{'server_name':'JAWAFFASVR','database_name':'Jawafa_DB'},
                'catalog':[{'schema_name':'dbo','object_name':'TEST_ONLY','column_name':'Id'}],
                'relations':[],'metadataMode':'sample','catalogTotal':1024}}
            assert request('/api/agent/result','POST',{'id':job['id'],'lease':'bad','output':result},agent)[0]==409
            assert request('/api/agent/result','POST',{'id':job['id'],'lease':job['lease'],'output':result},agent)[0]==200
            assert request('/api/agent/result','POST',{'id':job['id'],'lease':job['lease'],'output':result},agent)[0]==409
            assert request('/api/health',headers=reader)[0]==200
            snapshot=request('/api/snapshot',headers=reader)[2]
            assert snapshot['data']['schema_state']=='sample'
            assert snapshot['data']['sections']['sales']['state']=='unmapped'
            cases.append('agent_result_integrity_replay_rejection_and_honest_schema_scope')
            # Advance only the local test fixture's scheduling timestamp; never a remote database.
            advance=subprocess.run(base+['d1','execute','DB','--local','--persist-to',str(temp/'state'),
                '--command',"UPDATE jobs SET created=created-61 WHERE action='reconnect'"],
                env=env,capture_output=True,text=True)
            assert advance.returncode==0
            # Autonomous SQL is identity-only; diagnostics remain separate serial jobs.
            identity_seen=False
            for _ in range(5):
                job=poll(0)[2]['job']
                if not job:break
                if job['action']=='sql_probe':
                    assert job['identity_only'] is True
                    bad={'execution_ok':True,'sql':{'state':'verified_read','identity':{'server_name':'JAWAFFASVR','database_name':'WRONG'},'metadataMode':'identity_only'}}
                    request('/api/agent/result','POST',{'id':job['id'],'lease':job['lease'],'output':bad},agent)
                    identity_seen=True
                else:
                    request('/api/agent/result','POST',{'id':job['id'],'lease':job['lease'],'output':
                        {'execution_ok':True,'backups':{'state':'inventory_only','files':[{'Name':'TEST_ONLY.bak','Length':0}]}}
                        if job['action']=='backup_probe' else {'execution_ok':True}},agent)
            assert identity_seen
            assert request('/api/health',headers=reader)[0]==503
            cases.append('lightweight_autonomous_sql_and_wrong_database_fail_closed')
            backups=request('/api/control/state',headers=admin)[2]['backups']
            assert backups['source']=='agent_inventory' and backups['files'][0]['Name']=='TEST_ONLY.bak'
            assert backups['restorability']=='not_verified'
            cases.append('persistent_backup_inventory_is_not_a_false_restore_claim')
            assert request('/api/agent/result','POST',{'id':'x','lease':'x','output':{'execution_ok':True,'data':'x'*600000}},agent)[0]==413
            cases.append('bounded_upload_no_unlimited_schema_payload')
            from playwright.sync_api import sync_playwright,expect
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True)
                context=browser.new_context(**p.devices['Pixel 5'])
                page=context.new_page()
                page.goto('http://127.0.0.1:'+str(port)+'/control')
                page.locator('#token').fill(tokens['TRUSTSOFT_ADMIN_TOKEN'])
                page.locator('#login-submit').click()
                page.locator('#workspace').wait_for(state='visible')
                expect(page.locator('#control-message')).to_contain_text('الوكيل متصل')
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                browser.close()
            cases.append('android_browser_login_against_real_workerd_d1')
            tokens['TRUSTSOFT_ADMIN_TOKEN']=secrets.token_urlsafe(48)
            variables.write_text(''.join(k+'='+v+'\n' for k,v in tokens.items()))
            stop();start()
            assert request('/api/control/state',headers=admin)[0]==401
            assert request('/api/snapshot',headers=reader)[0]==200
            rotated=login('admin')
            assert request('/api/control/state',headers=rotated)[0]==200
            cases.append('admin_key_rotation_revokes_only_prior_admin_sessions')

            report={'runtime':'official workerd via Wrangler 4.153.0','storage':'local persistent D1',
                    'passed_cases':cases,'all_passed':True,'physical_android_tested':False,
                    'cloud_resources_created':False,'production_sql_connected':False,'public_https_deployed':False}
            output=ROOT/'docs/evidence/free-worker-results.json'
            output.write_text(json.dumps(report,indent=2))
            print(json.dumps(report,indent=2))
        finally:
            stop();log.close()

if __name__=='__main__':
    main()
