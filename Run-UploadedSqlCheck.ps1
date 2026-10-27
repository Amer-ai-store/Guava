# Windows-only file picker. Saves a new sanitized report, never the input secrets.
[CmdletBinding()]
param([string]$DBPreferencesPath, [string]$SqlPasswordPath)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Run on JAWAFFASVR or an authorized internal Windows computer.' }
if ([IO.Path]::GetFullPath($PSScriptRoot) -match '^C:\\Export(?:\\|$)') { throw 'Extract outside C:\Export.' }
Add-Type -AssemblyName System.Windows.Forms
function Select-InputFile([string]$Title) {
    $dialog = New-Object Windows.Forms.OpenFileDialog
    try {
        $dialog.Title = $Title
        $dialog.Filter = 'Text files (*.txt)|*.txt'
        if ($dialog.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { throw 'File selection cancelled.' }
        return $dialog.FileName
    } finally { $dialog.Dispose() }
}
if (-not $DBPreferencesPath) { $DBPreferencesPath = Select-InputFile 'Select DBPrefernces.txt (connection preferences)' }
if (-not $SqlPasswordPath) { $SqlPasswordPath = Select-InputFile 'Select SQLPassword_Decrypted text file (kept local)' }
$path = Join-Path $PSScriptRoot 'scripts/Test-TrustSoftUploadedConnection.ps1'
# Only paths cross the process boundary, never the password or SQL login.
$raw = & "$PSHOME\powershell.exe" -NoLogo -NoProfile -NonInteractive -File $path -DBPreferencesPath $DBPreferencesPath -SqlPasswordPath $SqlPasswordPath
$code = $LASTEXITCODE
$result = ($raw -join "`n") | ConvertFrom-Json
$reports = Join-Path $PSScriptRoot 'reports'
New-Item -ItemType Directory -Path $reports -Force | Out-Null
$file = Join-Path $reports ('UploadedSQL-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N') + '.json')
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $file -Encoding UTF8
Write-Host ('Report: ' + $file)
Write-Host ('SQL read query succeeded: ' + $result.sqlReadQuerySucceeded)
if ($result.detectedWriteOrElevatedPermissions) { Write-Host 'This login has elevated/write privileges. This check executed fixed SELECTs only; SQL permissions were not changed.' }
if ($code -ne 0) { Write-Host ('Blocked at: ' + $result.stage + '; reason: ' + $result.reason) }
Write-Host 'The password stays on this computer. This diagnostic does not install the agent.'
exit $code
