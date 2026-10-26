"""Sequential CSV audit, stdout report only; no SQL connection or source changes."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

def audit(root, manifest=None):
    root=Path(root)
    if not root.is_dir():
        raise ValueError('Export directory not found')
    result=[]
    for path in sorted(root.glob('*.csv')):
        item={'file':path.name,'bytes':path.stat().st_size}
        before=path.stat()
        try:
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(1024*1024),b''):
                    digest.update(block)
            with path.open('r',encoding='utf-8-sig',newline='') as stream:
                reader=csv.reader(stream,strict=True)
                header=next(reader,None)
                rows=0
                malformed=0
                for row in reader:
                    rows+=1
                    if len(row)!=len(header):
                        malformed+=1
            after=path.stat()
            item.update(sha256=digest.hexdigest(),rows=rows,columns=len(header or []),
                        malformed_rows=malformed,
                        stable_during_read=(before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),
                        parse_ok=bool(header) and not malformed)
        except (OSError,UnicodeError,csv.Error) as exc:
            item.update(parse_ok=False,error=type(exc).__name__)
        result.append(item)
    expected={v['file']:v for v in manifest} if manifest is not None else {}
    actual={v['file']:v for v in result}
    matched=manifest is not None and bool(expected) and len(expected)==len(manifest) and set(expected)==set(actual)
    if matched:
        matched=all(v.get('parse_ok') and v.get('stable_during_read') and
            expected[k].get('rows')==v.get('rows') and expected[k].get('sha256')==v.get('sha256')
            for k,v in actual.items())
    return {'files':result,'count':len(result),'manifest_match':matched,
            'completeness':'manifest_matched_only' if matched else 'unverified',
            'transactional_consistency':'not_verified',
            'warning':'File match is not SQL snapshot consistency. Do not audit active export files.'}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('directory')
    parser.add_argument('--manifest')
    args=parser.parse_args()
    expected=json.loads(Path(args.manifest).read_text(encoding='utf-8')) if args.manifest else None
    print(json.dumps(audit(args.directory,expected),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
