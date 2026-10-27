"""Small source-only handoff. Uploaded SQL settings and all credentials are excluded."""
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
FILES = (
    'Run-UploadedSqlCheck.cmd', 'Run-UploadedSqlCheck.ps1',
    'Install-LiveTrustSoftAgent.cmd', 'Install-LiveTrustSoftAgent.ps1',
    'agent/Install-TrustSoftAgent.ps1', 'agent/TrustSoft-Agent.ps1',
    'scripts/Read-TrustSoftSqlSettings.ps1', 'scripts/Test-TrustSoftUploadedConnection.ps1',
    'scripts/Protect-TrustSoftSqlCredential.ps1',
    'scripts/probe-windows-sql.ps1', 'scripts/inspect-server.ps1', 'scripts/probe-backups.ps1',
    'scripts/sql/identity.sql', 'scripts/sql/permissions.sql',
    'scripts/sql/catalog.sql', 'scripts/sql/relations.sql', 'docs/UPLOADED-SQL-CHECK.md',
)


def main():
    out = ROOT / 'docs/deliverables/TrustSoft-Windows-Connect.zip'
    out.parent.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        for name in FILES:
            path = ROOT / name
            if path.is_symlink():
                raise ValueError('Unexpected symlink')
            raw = path.read_bytes()
            if path.suffix in ('.ps1', '.sql', '.cmd'):
                if not raw.isascii():
                    raise ValueError('Review Windows script encoding')
                raw = raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            hashes[name] = hashlib.sha256(raw).hexdigest()
            archive.writestr(name, raw)
        archive.writestr('MANIFEST-SHA256.json', json.dumps(hashes, indent=2))
    with ZipFile(out) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(FILES) | {'MANIFEST-SHA256.json'}
        for name, digest in hashes.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    report = {'package': out.name, 'sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
              'files': len(FILES) + 1, 'bytes': out.stat().st_size,
              'integrity_verified': True, 'credentials_included': False,
              'windows_execution_verified': False, 'production_sql_verified': False}
    out.with_suffix('.verification.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
