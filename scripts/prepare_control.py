"""Create private deployment credentials once; never print, overwrite, or package values."""
import argparse
import os
from pathlib import Path
import re
import secrets

NAMES=('TRUSTSOFT_API_TOKEN','TRUSTSOFT_ADMIN_TOKEN','TRUSTSOFT_AGENT_TOKEN')

def prepare(directory):
    root=Path(directory)
    if root.is_symlink():
        raise ValueError('Configuration directory must not be a symlink')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    targets=[root/'dashboard.env',root/'admin-token.txt',root/'agent-token.txt']
    if any(p.exists() or p.is_symlink() for p in targets):
        raise ValueError('Existing credentials preserved; no files changed')
    values={name:os.getenv(name) or secrets.token_urlsafe(48) for name in NAMES}
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{32,256}',v) for v in values.values()) or len(set(values.values()))!=3:
        raise ValueError('Existing credentials must be distinct URL-safe tokens of 32 to 256 characters')
    contents=[''.join(k+'='+v+'\n' for k,v in values.items()),values[NAMES[1]]+'\n',values[NAMES[2]]+'\n']
    for path,raw in zip(targets,contents):
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as stream:
            stream.write(raw)
    return [str(p) for p in targets]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True)
    args=parser.parse_args()
    paths=prepare(args.directory)
    print('Prepared private credential files (values not printed):')
    for path in paths:
        print(path)

if __name__=='__main__':
    main()
