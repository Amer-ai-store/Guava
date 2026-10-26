"""Capture checks with individual exit codes; production checks stay explicitly separate."""
import datetime
import json
from pathlib import Path
import subprocess
import sys

def main():
    directory=Path('docs/evidence')
    directory.mkdir(parents=True,exist_ok=True)
    checks=[
        ('unit-tests.txt',[sys.executable,'-m','unittest','discover','-s','tests','-v']),
        ('control-ui-tests.txt',['node','--test','tests/control-ui.test.cjs']),
        ('live-smoke.txt',[sys.executable,'-m','scripts.smoke_live']),
        ('frontend-syntax.txt',['node','--check','web/app.js'])]
    results=[]
    for name,command in checks:
        with (directory/name).open('w') as stream:
            result=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT)
        results.append({'command':command,'exit_code':result.returncode,'evidence':name})
    report={'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'checks':results,'all_local_checks_passed':all(r['exit_code']==0 for r in results),
            'windows_agent_executed':False,'production_sql_connected':False,'public_https_deployed':False}
    (directory/'verification-summary.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return 0 if report['all_local_checks_passed'] else 1

if __name__=='__main__':
    raise SystemExit(main())
