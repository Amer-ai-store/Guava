import http.client
import json
import os
import threading
import unittest
from unittest.mock import patch
from trustsoft.server import App, Monitor
from trustsoft.source import SqlSource, SourceError, quote
from trustsoft.investigation import investigate

class RecoveringSource:
    """Synthetic test double only, never enabled by the application."""
    fail = False
    calls = 0
    def snapshot(self):
        self.calls += 1
        if self.fail:
            raise SourceError('test_outage')
        return {'identity': {'database_name':'test'}, 'catalog':[], 'relations':[],
                'sections':{'sales':{'state':'sample','rows':[{'id':7,'amount':'123.45'}]}}}

class MonitorTests(unittest.TestCase):
    def test_outage_retains_stale_data_and_recovers(self):
        source=RecoveringSource()
        monitor=Monitor(source)
        monitor.refresh()
        self.assertEqual(monitor.snapshot()['data']['sections']['sales']['rows'][0]['amount'],'123.45')
        source.fail=True
        monitor.refresh()
        state=monitor.snapshot()
        self.assertEqual(state['state'],'disconnected')
        self.assertTrue(state['stale'])
        self.assertIsNotNone(state['data'])
        source.fail=False
        monitor.refresh()
        self.assertFalse(monitor.snapshot()['stale'])
        self.assertEqual(monitor.snapshot()['events'][-1]['type'],'connection_restored')

    def test_refresh_is_single_flight(self):
        source=RecoveringSource()
        monitor=Monitor(source)
        monitor.refresh_lock.acquire()
        try:
            monitor.refresh()
            self.assertEqual(source.calls,0)
        finally:
            monitor.refresh_lock.release()

    def test_unconfigured_source_does_not_fabricate_data(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SourceError,'sql_not_configured'):
                SqlSource().snapshot()

    def test_quotes_identifiers(self):
        self.assertEqual(quote('x]; DROP TABLE a;--'),'[x]]; DROP TABLE a;--]')

class ReportsTests(unittest.TestCase):
    def test_payment_candidates_require_evidence_and_distinct_ids(self):
        row={'payment_id':1,'party_id':5,'amount':'12.20','currency':'USD','reference':'r1','date':'2026-10-26'}
        rows=[row, dict(row), dict(row,payment_id=2),dict(row,payment_id=3,currency='JOD')]
        report=investigate({'payments':{'rows':rows}})
        self.assertEqual(len(report['findings']),1)
        self.assertEqual(report['missing_vouchers']['state'],'not_assessed')

    def test_incomplete_and_nonfinite_payments_ignored(self):
        self.assertEqual(investigate({'payments':{'rows':[{'amount':'NaN'},{'amount':'0'}]}})['findings'],[])

    def test_attribution_never_invented(self):
        result=investigate({'audit':{'rows':[{'event_id':1,'event_type':'unpost','document_id':6}]}})
        event=result['findings'][0]['evidence']
        self.assertNotIn('user',event)
        self.assertNotIn('device',event)

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=RecoveringSource()
        cls.monitor=Monitor(cls.source)
        cls.monitor.refresh()
        cls.token='t'*40
        cls.app=App(('127.0.0.1',0),cls.monitor,cls.token)
        cls.thread=threading.Thread(target=cls.app.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.app.server_close()
        cls.thread.join()

    def request(self,path,method='GET',body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.app.server_port,timeout=3)
        conn.request(method,path,body,headers or {})
        res=conn.getresponse()
        result=(res.status,dict(res.getheaders()),res.read())
        conn.close()
        return result

    def login(self):
        status,headers,_=self.request('/api/login','POST',json.dumps({'token':self.token}))
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('Secure',headers['Set-Cookie'])
        return {'Cookie':headers['Set-Cookie'].split(';')[0]}

    def test_auth_required(self):
        self.assertEqual(self.request('/api/snapshot')[0],401)
        self.assertEqual(self.request('/api/login','POST','{"token":"bad"}')[0],401)

    def test_snapshot_does_not_requery_and_health_outage_recovers(self):
        cookie=self.login()
        before=self.source.calls
        for _ in range(3):
            status,_,raw=self.request('/api/snapshot',headers=cookie)
            self.assertEqual(status,200)
            self.assertEqual(json.loads(raw)['data']['sections']['sales']['rows'][0]['id'],7)
        self.assertEqual(self.source.calls,before)
        self.source.fail=True
        self.monitor.refresh()
        self.assertEqual(self.request('/api/health',headers=cookie)[0],503)
        self.source.fail=False
        self.monitor.refresh()
        self.assertEqual(self.request('/api/health',headers=cookie)[0],200)

    def test_no_sql_or_write_routes(self):
        cookie=self.login()
        self.assertEqual(self.request('/api/query?sql=DELETE',headers=cookie)[0],404)
        self.assertEqual(self.request('/api/snapshot','POST','{}',cookie)[0],405)

    def test_static_security_and_traversal(self):
        status,headers,body=self.request('/')
        self.assertEqual(status,200)
        self.assertIn('frame-ancestors',headers['Content-Security-Policy'])
        self.assertIn('no-store',headers['Cache-Control'])
        self.assertIn('TrustSoft'.encode(),body)
        self.assertEqual(self.request('/../trustsoft/source.py')[0],404)

    def test_login_input_validation(self):
        self.assertEqual(self.request('/api/login','POST','[]')[0],400)
        self.assertEqual(self.request('/api/login','POST','x'*5000)[0],413)
        self.assertEqual(self.request('/api/login','POST','{}',{'Sec-Fetch-Site':'cross-site'})[0],403)

if __name__=='__main__':
    unittest.main()
