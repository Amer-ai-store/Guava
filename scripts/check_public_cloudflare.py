"""Verify the published service without fabricating a production SQL/agent result."""
import datetime
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent.parent

def main():
    private=ROOT/'local/cloudflare'
    manifest=json.loads((private/'deployment.json').read_text())
    url=manifest['url']
    keys=json.loads((private/'app-secrets.json').read_text())
    def client():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def call(opener,path,data=None,agent=False):
        headers={'User-Agent':'TrustSoft-ReadOnly-Agent/1.0' if agent else 'TrustSoft-Control/1.0',
                 'Accept':'application/json','Content-Type':'application/json'}
        if agent:headers['Authorization']='Bearer '+keys['TRUSTSOFT_AGENT_TOKEN']
        if path=='/api/control/jobs':headers['X-TrustSoft-Action']='1'
        body=json.dumps(data).encode() if data is not None else None
        request=urllib.request.Request(url+path,body,headers=headers)
        try:
            with opener.open(request,timeout=30) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code,None
    anonymous=client()
    assert call(anonymous,'/healthz')[0]==200
    assert call(anonymous,'/api/control/state')[0]==401
    reader=client()
    assert call(reader,'/api/login',{'token':keys['TRUSTSOFT_API_TOKEN']})[0]==200
    assert call(reader,'/api/control/state')[0]==403
    admin=client()
    assert call(admin,'/api/admin/login',{'token':keys['TRUSTSOFT_ADMIN_TOKEN']})[0]==200
    status,before=call(admin,'/api/control/state')
    assert status==200
    status,queued=call(admin,'/api/control/jobs',{'action':'server_probe'})
    assert status==202
    status,after=call(admin,'/api/control/state')
    job=next(j for j in after['jobs'] if j['id']==queued['id'])
    assert job['status'] in ('queued','expired')
    # Hello validates the enrollment route but deliberately does not mark a server online.
    status,hello=call(anonymous,'/api/agent/hello',{'agent_id':'JAWAFFASVR'},agent=True)
    assert status==200 and hello['broker_id']==before['broker_id']
    status,final=call(admin,'/api/control/state')
    assert final['last_seen']==before['last_seen']
    assert call(anonymous,'/local/cloudflare/app-secrets.json')[0]==404
    report={'url':url,'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'public_https_verified':True,'anonymous_api_blocked':True,'reader_admin_separation_verified':True,
        'admin_login_verified':True,'job_saved_in_remote_d1':True,'test_job_id':queued['id'],
        'test_job_status':job['status'],'test_job_executed_on_server':False,
        'agent_enrollment_route_verified':True,'agent_presence_not_fabricated':True,
        'private_config_not_public':True,'production_sql_verified':False}
    (ROOT/'docs/evidence/public-cloudflare-api.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
