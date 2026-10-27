import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import deploy_cloudflare as deploy

class CloudflareDeploymentTests(unittest.TestCase):
    def test_free_config_and_credentials_are_private_and_stable(self):
        config=deploy.make_config('a'*32,'12345678-1234-1234-1234-123456789abc')
        self.assertEqual(config['limits']['cpu_ms'],10)
        self.assertTrue(config['keep_vars'])
        self.assertTrue(config['workers_dev'])
        self.assertNotIn('paid',json.dumps(config).lower())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'keys.json')
            first=deploy.app_keys(path)
            self.assertEqual(deploy.app_keys(path),first)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(len(set(first.values())),4)

    def test_other_worker_is_preserved_without_remote_mutation(self):
        calls=[]
        def api(path,method='GET',data=None):
            calls.append((path,method))
            if path.startswith('/accounts?'):
                return {'result':[{'id':'a'*32}]}
            if path.endswith('/workers/scripts'):
                return {'result':[{'id':'trustsoft-control'}]}
            self.fail('Unexpected provider call')
        with tempfile.TemporaryDirectory() as folder,patch.object(deploy,'api',side_effect=api),patch('sys.argv',['deploy','--private-dir',folder]):
            with self.assertRaisesRegex(deploy.DeploymentError,'pre-existing Worker'):
                deploy.main()
        self.assertTrue(all(method=='GET' for _,method in calls))

    def test_unowned_database_is_preserved_without_migration(self):
        calls=[]
        def api(path,method='GET',data=None):
            calls.append((path,method))
            if path.startswith('/accounts?'):return {'result':[{'id':'a'*32}]}
            if path.endswith('/workers/scripts'):return {'result':[]}
            if path.endswith('/workers/subdomain'):return {'result':{'subdomain':'owned'}}
            if '/d1/database?' in path:return {'result':[{'name':'trustsoft-control','uuid':'12345678-1234-1234-1234-123456789abc'}]}
            self.fail('Unexpected provider call')
        with tempfile.TemporaryDirectory() as folder,patch.object(deploy,'api',side_effect=api),patch('sys.argv',['deploy','--private-dir',folder]):
            with self.assertRaisesRegex(deploy.DeploymentError,'Existing D1'):
                deploy.main()
        self.assertTrue(all(method=='GET' for _,method in calls))

    def test_multiple_accounts_require_explicit_selection(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(deploy,'api',return_value={'result':[{'id':'a'*32},{'id':'b'*32}]}),patch('sys.argv',['deploy','--private-dir',folder]):
            with self.assertRaisesRegex(deploy.DeploymentError,'Select one'):
                deploy.main()

    def test_missing_local_keys_do_not_regenerate_existing_worker_credentials(self):
        account='a'*32
        database='12345678-1234-1234-1234-123456789abc'
        def api(path,method='GET',data=None):
            self.assertEqual(method,'GET')
            if path.startswith('/accounts?'):return {'result':[{'id':account}]}
            if path.endswith('/workers/scripts'):return {'result':[{'id':'trustsoft-control'}]}
            if '/d1/database?' in path:return {'result':[{'name':'trustsoft-control','uuid':database}]}
            if path.endswith('/workers/subdomain'):return {'result':{'subdomain':'owned'}}
            self.fail('Unexpected provider call')
        with tempfile.TemporaryDirectory() as folder,patch.object(deploy,'api',side_effect=api),patch('sys.argv',['deploy','--private-dir',folder]):
            Path(folder,'deployment.json').write_text(json.dumps({'account_id':account,'worker_owned':True,'database_id':database,'secrets_installed':True}))
            with self.assertRaisesRegex(deploy.DeploymentError,'not regenerated'):
                deploy.main()
            self.assertFalse(Path(folder,'app-secrets.json').exists())
