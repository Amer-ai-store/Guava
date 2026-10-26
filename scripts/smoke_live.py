"""Launch actual service process with no SQL configuration; verify honest degraded state."""
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import time

with socket.socket() as sock:
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
token=secrets.token_urlsafe(40)
runtime=dict(os.environ,TRUSTSOFT_API_TOKEN=token)
runtime.pop('TRUSTSOFT_SQL_CONNECTION',None)
runtime.pop('TRUSTSOFT_MAPPING',None)
process=subprocess.Popen([sys.executable,'-m','trustsoft.server','--port',str(port)],env=runtime,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
try:
    for _ in range(50):
        try:
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=1)
            conn.request('GET','/')
            response=conn.getresponse()
            assert response.status==200 and b'TrustSoft' in response.read()
            conn.close()
            break
        except OSError:
            if process.poll() is not None:
                raise RuntimeError('Service exited')
            time.sleep(.1)
    else:
        raise RuntimeError('Startup timeout')
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    conn.request('POST','/api/login',json.dumps({'token':token}))
    response=conn.getresponse()
    assert response.status==200
    cookie=response.getheader('Set-Cookie').split(';')[0]
    response.read()
    conn.request('GET','/api/snapshot',headers={'Cookie':cookie})
    response=conn.getresponse()
    state=json.loads(response.read())
    assert state['error']=='sql_not_configured' and state['data'] is None and state['stale']
    conn.request('GET','/api/health',headers={'Cookie':cookie})
    response=conn.getresponse()
    assert response.status==503
    response.read()
    conn.close()
    print('PASS: real process startup, HTML response, login, snapshot, degraded health; no SQL success claimed')
finally:
    process.terminate()
    process.wait(timeout=5)
