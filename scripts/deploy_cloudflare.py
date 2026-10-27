"""Deploy the real control plane to Workers + D1 without enabling a paid plan.
Provider token stays in the scoped environment; generated app credentials stay private.
"""
import argparse
import datetime
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'deploy/cloudflare'
API='https://api.cloudflare.com/client/v4'
APP_KEYS=('TRUSTSOFT_API_TOKEN','TRUSTSOFT_ADMIN_TOKEN','TRUSTSOFT_AGENT_TOKEN','SESSION_SIGNING_KEY')

class DeploymentError(Exception):
    pass

class CloudflareAPIError(DeploymentError):
    def __init__(self,status,codes):
        self.status=status
        self.codes=codes
        super().__init__('Cloudflare HTTP '+str(status)+'; error codes '+str(codes))

def private_write(path,value):
    if path.is_symlink():
        raise DeploymentError('Existing symlink was preserved without writing')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,indent=2)

def api(path,method='GET',data=None):
    token=os.getenv('CLOUDFLARE_API_TOKEN')
    if not token:
        raise DeploymentError('CLOUDFLARE_API_TOKEN is not bound in the running environment')
    payload=json.dumps(data).encode() if data is not None else None
    request=urllib.request.Request(API+path,payload,method=method,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            result=json.load(response)
    except urllib.error.HTTPError as exc:
        raw=exc.read(100000)
        try:
            failure=json.loads(raw)
            codes=[e.get('code') for e in failure.get('errors',[])]
        except ValueError:
            codes=[]
        raise CloudflareAPIError(exc.code,codes) from None
    if result.get('success') is not True:
        raise DeploymentError('Cloudflare operation failed; error codes '+str([e.get('code') for e in result.get('errors',[])]))
    return result

def app_keys(path):
    if path.is_symlink():
        raise DeploymentError('Existing credential symlink was preserved')
    if path.exists():
        data=json.loads(path.read_text())
        if set(data)!=set(APP_KEYS) or not all(isinstance(v,str) and len(v)>=32 for v in data.values()) or len(set(data.values()))!=4:
            raise DeploymentError('Existing private app credentials are invalid; preserved without replacement')
        return data
    values={name:secrets.token_urlsafe(48) for name in APP_KEYS}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(values,stream)
    return values

def make_config(account,database):
    if not re.fullmatch('[a-f0-9]{32}',account):
        raise DeploymentError('Invalid account id')
    if not re.fullmatch('[a-f0-9-]{36}',database):
        raise DeploymentError('Invalid database id')
    result=json.loads((PROJECT/'wrangler.jsonc').read_text())
    result['account_id']=account
    result['keep_vars']=True
    result['main']=str(PROJECT/'src/worker.mjs')
    result['assets']['directory']=str(ROOT/'web')
    result['d1_databases'][0]['database_id']=database
    result['d1_databases'][0]['migrations_dir']=str(PROJECT/'migrations')
    # No subscription, paid Workers plan, paid storage or custom DNS is requested.
    assert 'limits' not in result  # Free plan enforces its own default CPU limit.
    assert result['workers_dev'] is True
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--account-id',default=os.getenv('CLOUDFLARE_ACCOUNT_ID'))
    parser.add_argument('--private-dir',default=str(ROOT/'local/cloudflare'))
    args=parser.parse_args()
    private=Path(args.private_dir)
    if private.is_symlink():
        raise DeploymentError('Private deployment directory must not be a symlink')
    private.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(private,0o700)
    manifest_path=private/'deployment.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    accounts=api('/accounts?per_page=50')['result']
    if args.account_id:
        matches=[a for a in accounts if a['id']==args.account_id]
        if len(matches)!=1:raise DeploymentError('Requested account is not available to this token')
        account=matches[0]
    elif len(accounts)==1:
        account=accounts[0]
    else:
        raise DeploymentError('Select one authorized account using --account-id; no resources were created')
    account_id=account['id']
    if manifest.get('account_id') not in (None,account_id):
        raise DeploymentError('Existing deployment belongs to another account and was preserved')
    name='trustsoft-control'
    try:
        scripts=api('/accounts/'+account_id+'/workers/scripts')['result']
    except CloudflareAPIError as exc:
        if 10007 not in exc.codes:
            raise
        scripts=[]
    if any(s.get('id')==name for s in scripts) and not manifest.get('worker_owned'):
        raise DeploymentError('A pre-existing Worker with this name was preserved; ownership must be reviewed')
    # Inspect all existing named resources before any mutation.
    databases=api('/accounts/'+account_id+'/d1/database?per_page=100')['result']
    matches=[d for d in databases if d.get('name')=='trustsoft-control']
    if matches and manifest.get('database_id') not in [d.get('uuid') for d in matches]:
        raise DeploymentError('Existing D1 database with this name was preserved; no schema was changed')
    try:
        domain=api('/accounts/'+account_id+'/workers/subdomain')['result'] or {}
    except CloudflareAPIError as exc:
        if exc.status!=404 and 10007 not in exc.codes:
            raise
        domain={}
    if not domain.get('subdomain'):
        api('/accounts/'+account_id+'/workers/subdomain','PUT',{'subdomain':'trustsoft-'+secrets.token_hex(4)})
    manifest.update(account_id=account_id,worker_name=name,resources_paid_plan_requested=False)
    if matches:
        database_id=matches[0]['uuid']
    else:
        created=api('/accounts/'+account_id+'/d1/database','POST',{'name':'trustsoft-control'})['result']
        database_id=created['uuid']
        manifest['database_id']=database_id
        private_write(manifest_path,manifest)
    config=make_config(account_id,database_id)
    config_path=private/'wrangler.json'
    private_write(config_path,config)
    secrets_path=private/'app-secrets.json'
    if manifest.get('secrets_installed') and not secrets_path.exists():
        raise DeploymentError('Existing Worker credentials were preserved; the local secret file is missing and was not regenerated')
    values=app_keys(secrets_path)
    executable=PROJECT/'node_modules/.bin/wrangler'
    if not executable.exists():
        raise DeploymentError('Install the pinned deploy tool with npm ci in deploy/cloudflare')
    environment=dict(os.environ,NODE_USE_ENV_PROXY='1',WRANGLER_SEND_METRICS='false',CLOUDFLARE_ACCOUNT_ID=account_id)
    def command(label,arguments):
        result=subprocess.run([str(executable),'--config',str(config_path),*arguments],
                              env=environment,capture_output=True,text=True)
        safe=result.stdout+'\n'+result.stderr
        for value in (*values.values(),os.getenv('CLOUDFLARE_API_TOKEN','')):
            if value:safe=safe.replace(value,'[REDACTED]')
        safe=re.sub(r'Bearer\s+[A-Za-z0-9._=-]+','Bearer [REDACTED]',safe)
        log=private/(label+'.log')
        fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(safe)
        if result.returncode:
            raise DeploymentError(label+' failed; sanitized details are in the protected deployment directory')
        print(label+': passed')
        return safe
    command('d1-migrate',['d1','migrations','apply','DB','--remote'])
    # Mark ownership before first deployment so a failed upload can be resumed safely.
    manifest['worker_owned']=True
    private_write(manifest_path,manifest)
    deployed=command('worker-deploy',['deploy'])
    urls=re.findall(r'https://[a-z0-9.-]+\.workers\.dev',deployed)
    if not urls:raise DeploymentError('Deployment command returned no public workers.dev URL; do not invent one')
    url=urls[-1]
    if not manifest.get('secrets_installed'):
        existing=api('/accounts/'+account_id+'/workers/scripts/'+name+'/secrets')['result']
        if any(item.get('name') in APP_KEYS for item in existing):
            raise DeploymentError('Existing Worker credentials were preserved; reconcile the protected deployment manifest before retrying')
        command('worker-secrets',['secret','bulk',str(secrets_path)])
        manifest['secrets_installed']=True
        private_write(manifest_path,manifest)
    cookies=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    def fetch(path,body=None):
        data=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(url+path,data,headers={'Content-Type':'application/json','User-Agent':'TrustSoft-Control/1.0','Accept':'application/json'})
        with opener.open(req,timeout=30) as response:
            return response.status,response.read(),response.headers.get('Content-Type','')
    status,raw,_=fetch('/healthz')
    if status!=200 or json.loads(raw).get('storage')!='d1':
        raise DeploymentError('Public liveness did not verify the deployed D1-backed service')
    status,raw,_=fetch('/control')
    if status!=200 or b'data-start-section="control"' not in raw:
        raise DeploymentError('Public web control page did not verify')
    status,raw,_=fetch('/api/admin/login',{'token':values['TRUSTSOFT_ADMIN_TOKEN']})
    if status!=200 or not json.loads(raw).get('ok'):
        raise DeploymentError('Public admin login did not verify')
    status,raw,_=fetch('/api/control/state')
    state=json.loads(raw)
    if status!=200 or not state.get('broker_id') or state.get('hosting',{}).get('storage')!='persistent_d1':
        raise DeploymentError('Authenticated public control state did not verify')
    manifest.update(database_id=database_id,url=url,public_web_verified=True,admin_login_verified=True,
                    production_sql_connected=False,observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    private_write(manifest_path,manifest)
    # Do not print any app or provider credentials.
    print(json.dumps({k:manifest[k] for k in ('url','public_web_verified','admin_login_verified','production_sql_connected')},indent=2))

if __name__=='__main__':
    try:
        main()
    except DeploymentError as exc:
        print('Deployment blocked: '+str(exc))
        raise SystemExit(1)
