import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from trustsoft.control import ControlStore, ControlError, backup_inventory, export_progress
from trustsoft.agent_source import AgentSource
from trustsoft.source import SourceError
from trustsoft.server import App, Monitor

def sql_result(verified=True):
    return {'execution_ok':verified,'sql':{'state':'verified_read' if verified else 'blocked',
        'identity':{'server_name':'JAWAFFASVR','database_name':'Jawafa_DB'},
        'catalog':[],'relations':[]}}

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.0
        self.store=ControlStore(':memory:',clock=lambda:self.now)
    def tearDown(self):
        self.store.close()
    def execute(self, action='sql_probe', output=None):
        target=self.store.enqueue(action)['id']
        # Drain any autonomous metadata jobs using failure results.
        for _ in range(6):
            job=self.store.poll('JAWAFFASVR')
            if job['id']==target:
                self.store.complete(job['id'],job['lease'],output or sql_result())
                return job
            self.store.complete(job['id'],job['lease'],{'execution_ok':True})
        self.fail('Selected job was not leased')

    def test_allowlist_dedup_single_flight_and_lease(self):
        a=self.store.enqueue('reconnect')
        self.assertEqual(self.store.enqueue('reconnect')['id'],a['id'])
        with self.assertRaisesRegex(ControlError,'not_allowed'):
            self.store.enqueue('Start-Process powershell')
        job=self.store.poll('JAWAFFASVR')
        self.assertIsNone(self.store.poll('JAWAFFASVR'))
        with self.assertRaisesRegex(ControlError,'lease'):
            self.store.complete(job['id'],'wrong',sql_result())
        self.store.complete(job['id'],job['lease'],sql_result())
        with self.assertRaisesRegex(ControlError,'lease'):
            self.store.complete(job['id'],job['lease'],sql_result())

    def test_expiry_and_no_automatic_replay(self):
        job=self.store.poll('JAWAFFASVR')
        self.now+=121
        with self.assertRaisesRegex(ControlError,'lease'):
            self.store.complete(job['id'],job['lease'],sql_result())
        status=self.store.snapshot()
        old=next(j for j in status['jobs'] if j['id']==job['id'])
        self.assertEqual(old['status'],'expired')
        self.assertFalse(status['online'])

    def test_realistic_source_disconnect_and_recovery_without_db_mutation(self):
        self.execute()
        source=AgentSource(self.store)
        self.assertEqual(source.snapshot()['identity']['database_name'],'Jawafa_DB')
        self.now+=16
        self.execute('reconnect',sql_result(False))
        with self.assertRaises(SourceError):
            source.snapshot()
        self.now+=16
        self.execute('reconnect',sql_result())
        self.assertEqual(source.snapshot()['sections']['sales']['state'],'unmapped')
        self.now+=91
        with self.assertRaises(SourceError):
            source.snapshot()

    def test_wrong_db_is_not_trusted(self):
        output=sql_result()
        output['sql']['identity']['database_name']='other'
        self.execute(output=output)
        self.assertIsNone(self.store.sql_snapshot())

    def test_durable_queue_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'control.sqlite')
            first=ControlStore(path,clock=lambda:self.now)
            job_id=first.enqueue('export_probe')['id']
            first.close()
            second=ControlStore(path,clock=lambda:self.now)
            self.assertIn(job_id,[j['id'] for j in second.snapshot()['jobs']])
            self.assertTrue(second.snapshot()['audit'])
            second.close()

    def test_inventory_never_claims_backup_restorable_or_serves_it_as_live(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'copy.bak').write_bytes(b'not a real backup')
            data=backup_inventory(folder)
            self.assertEqual(data['restorability'],'not_verified')
            self.assertEqual(data['files'][0]['name'],'copy.bak')
            self.assertEqual(backup_inventory(None)['state'],'not_configured')

    def test_export_samples_show_change_without_claiming_completion(self):
        def sample(at,count):
            return {'completed':at,'output':{'diagnostics':{'export':{'state':'observed','items':[{'count':count,'bytes':count*100}]}}}}
        progress=export_progress([sample(100,10),sample(400,11)])
        self.assertEqual(progress['state'],'change_observed')
        self.assertEqual(progress['count_delta'],1)
        self.assertEqual(progress['completeness'],'unverified')
        self.assertEqual(export_progress([sample(100,10),sample(400,10)])['state'],'unchanged_samples')

    def test_independent_broker_connections_cannot_lease_two_jobs(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'shared.sqlite')
            first=ControlStore(path)
            second=ControlStore(path)
            self.assertEqual(first.broker_id,second.broker_id)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda store:store.poll('JAWAFFASVR'),[first,second]))
            self.assertEqual(sum(job is not None for job in results),1)
            first.close()
            second.close()

class ControlHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=ControlStore(Path(self.temp.name,'control.sqlite'))
        self.env=patch.dict(os.environ,{'TRUSTSOFT_ADMIN_TOKEN':'admin-'+'a'*40,
                                      'TRUSTSOFT_AGENT_TOKEN':'agent-'+'b'*40})
        self.env.start()
        self.monitor=Monitor(type('Offline',(),{'snapshot':lambda _: (_ for _ in ()).throw(SourceError('sql_not_configured'))})())
        self.apps=[]
        self.threads=[]
        self.app=self.start_app()
    def start_app(self):
        app=App(('127.0.0.1',0),self.monitor,'reader-'+'c'*40,control=self.store)
        thread=threading.Thread(target=app.serve_forever,daemon=True)
        thread.start()
        self.apps.append(app)
        self.threads.append(thread)
        return app
    def tearDown(self):
        for app in self.apps:
            app.shutdown()
            app.server_close()
        for thread in self.threads:
            thread.join()
        self.store.close()
        self.env.stop()
        self.temp.cleanup()
    def request(self,path,body=None,headers=None,app=None):
        conn=http.client.HTTPConnection('127.0.0.1',(app or self.app).server_port,timeout=3)
        headers=dict(headers or {})
        if body is not None:
            body=json.dumps(body)
            headers['Content-Type']='application/json'
        conn.request('GET' if body is None else 'POST',path,body,headers)
        res=conn.getresponse()
        out=(res.status,dict(res.getheaders()),json.loads(res.read()))
        conn.close()
        return out
    def login(self,role):
        route='/api/admin/login' if role=='admin' else '/api/login'
        token='admin-'+'a'*40 if role=='admin' else 'reader-'+'c'*40
        status,headers,_=self.request(route,{'token':token})
        self.assertEqual(status,200)
        return {'Cookie':headers['Set-Cookie'].split(';')[0],'X-TrustSoft-Action':'1'}
    def agent_headers(self):
        return {'Authorization':'Bearer agent-'+'b'*40}

    def test_roles_csrf_arbitrary_shell_and_cookie_do_not_authorize_agent(self):
        reader=self.login('reader')
        self.assertEqual(self.request('/api/control/jobs',{'action':'reconnect'},reader)[0],403)
        admin=self.login('admin')
        missing={'Cookie':admin['Cookie']}
        self.assertEqual(self.request('/api/control/jobs',{'action':'reconnect'},missing)[0],403)
        self.assertEqual(self.request('/api/control/jobs',{'action':'cmd.exe'},admin)[0],409)
        self.assertEqual(self.request('/api/agent/poll',{'agent_id':'JAWAFFASVR'},admin)[0],401)
        cross=dict(admin,**{'Sec-Fetch-Site':'cross-site'})
        self.assertEqual(self.request('/api/control/jobs',{'action':'reconnect'},cross)[0],403)

    def test_dashboard_job_to_agent_result_over_real_http(self):
        admin=self.login('admin')
        status,_,queued=self.request('/api/control/jobs',{'action':'reconnect'},admin)
        self.assertEqual(status,202)
        for _ in range(6):
            status,_,answer=self.request('/api/agent/poll',{'agent_id':'JAWAFFASVR'},self.agent_headers())
            self.assertEqual(status,200)
            job=answer['job']
            output=sql_result() if job['id']==queued['id'] else {'execution_ok':True}
            self.assertEqual(self.request('/api/agent/result',{'id':job['id'],'lease':job['lease'],'output':output},self.agent_headers())[0],200)
            if job['id']==queued['id']:
                break
        state=self.request('/api/control/state',headers=admin)[2]
        self.assertTrue(state['online'])
        self.assertEqual(next(j for j in state['jobs'] if j['id']==queued['id'])['status'],'completed')
        self.assertIsNotNone(self.store.sql_snapshot())

    def test_secondary_http_endpoint_accepts_same_durable_lease(self):
        secondary=self.start_app()
        job=self.request('/api/agent/poll',{'agent_id':'JAWAFFASVR'},self.agent_headers())[2]['job']
        # Stop the primary listener; deliver the pending read result through the secondary.
        self.app.shutdown()
        self.app.server_close()
        self.assertEqual(self.request('/api/agent/result',{'id':job['id'],'lease':job['lease'],'output':sql_result()},self.agent_headers(),app=secondary)[0],200)
        state=self.store.snapshot()
        self.assertEqual(next(j for j in state['jobs'] if j['id']==job['id'])['status'],'completed')

    def test_agent_identity_and_tampered_result_rejected(self):
        self.assertEqual(self.request('/api/agent/poll',{'agent_id':'OTHER'},self.agent_headers())[0],409)
        self.assertEqual(self.request('/api/agent/result',{'id':'missing','lease':'bad','output':sql_result()},self.agent_headers())[0],409)

    def test_enrollment_hello_identifies_same_broker_without_leasing_a_job(self):
        status,_,hello=self.request('/api/agent/hello',{'agent_id':'JAWAFFASVR'},self.agent_headers())
        self.assertEqual(status,200)
        self.assertEqual(hello['broker_id'],self.store.broker_id)
        self.assertEqual(self.store.snapshot()['jobs'],[])

    def test_dedicated_control_page_is_served_without_embedded_credentials(self):
        conn=http.client.HTTPConnection('127.0.0.1',self.app.server_port,timeout=3)
        conn.request('GET','/control')
        response=conn.getresponse()
        self.assertEqual(response.status,200)
        page=response.read().decode()
        conn.close()
        self.assertIn('data-start-section="control"',page)
        self.assertNotIn('admin-'+'a'*40,page)

    def test_hosting_liveness_does_not_expose_or_require_database_readiness(self):
        status,_,body=self.request('/healthz')
        self.assertEqual(status,200)
        self.assertEqual(body,{'process':'running'})
        self.assertEqual(self.request('/api/health')[0],401)
