"""Package only reviewed support/source files. Secrets and runtime state are excluded."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

def main():
    files=[Path(p) for p in ('README.md','START-REMOTE-CONTROL.txt','requirements-sql.txt','.gitignore','render.yaml')]
    for directory,pattern in [('trustsoft','*.py'),('web','*'),('agent','*.ps1'),('deploy','*'),
                              ('scripts','*.py'),('scripts','*.ps1'),('scripts/sql','*.sql'),
                              ('tests','*.py'),('tests','*.cjs'),('docs','*.md'),
                              ('docs/evidence','*.json'),('docs/evidence','*.txt'),('docs/evidence','*.png')]:
        files.extend(sorted(Path(directory).glob(pattern)))
    files.append(Path('docs/mapping.example.json'))
    files=sorted(set(files))
    out=Path('docs/deliverables/TrustSoft-Control-and-Agent.zip')
    out.parent.mkdir(parents=True,exist_ok=True)
    hashes={}
    with ZipFile(out,'w',ZIP_DEFLATED) as package:
        for path in files:
            if path.is_symlink() or not path.is_file() or path.suffix.lower() in ('.pfx','.p12','.key','.pem','.sqlite'):
                raise ValueError('Unexpected package member')
            raw=path.read_bytes()
            if path.suffix in ('.ps1','.sql'):
                if not raw.isascii():
                    raise ValueError('Windows script requires explicit encoding review')
                raw=raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
            if path.name=='START-REMOTE-CONTROL.txt':
                raw=b'\xef\xbb\xbf'+raw
            hashes[path.as_posix()]=hashlib.sha256(raw).hexdigest()
            package.writestr(path.as_posix(),raw)
        package.writestr('MANIFEST-SHA256.json',json.dumps(hashes,indent=2))
    with ZipFile(out) as package:
        assert package.testzip() is None
        assert set(package.namelist())==set(hashes)|{'MANIFEST-SHA256.json'}
        for name,digest in hashes.items():
            assert hashlib.sha256(package.read(name)).hexdigest()==digest
    result={'package':out.name,'files':len(hashes)+1,'bytes':out.stat().st_size,
            'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
            'archive_integrity_passed':True,'pfx_or_private_keys_included':False,
            'windows_execution_verified':False,'public_https_deployed':False}
    out.with_suffix('.sha256.txt').write_text(result['sha256']+'  '+out.name+'\n')
    # Keep package evidence outside its own archive to avoid a recursive digest.
    Path('docs/deliverables/control-package-verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    main()
