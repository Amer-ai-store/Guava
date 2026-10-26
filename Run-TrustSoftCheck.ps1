# Windows PowerShell 5.1. Creates only independent diagnostic reports beside this kit.
[CmdletBinding()]
param(
    [string]$Server = 'JAWAFFASVR',
    [string]$ExportPath = 'C:\Export\hourly\20261026-1640'
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($PSScriptRoot)
if ($root -match '^C:\\Export(?:\\|$)') {
    throw 'Extract the kit outside C:\Export, for example C:\TrustSoftCheck.'
}
if ($env:OS -ne 'Windows_NT') { throw 'Run this kit on Windows.' }
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$reportRoot = Join-Path $root 'reports'
$reportFolder = Join-Path $reportRoot ('TrustSoft-' + $stamp)
New-Item -ItemType Directory -Path $reportFolder | Out-Null
$summary = [ordered]@{
    observedUtc = (Get-Date).ToUniversalTime().ToString('o')
    targetServer = $Server
    targetDatabase = 'Jawafa_DB'
    windowsComputer = $env:COMPUTERNAME
    originalDataChanged = $false
    productionReadVerified = $false
    note = 'Read-only diagnostics. Export completeness and business totals are not verified.'
}
function Invoke-Probe([string]$Name, [string]$Script, [string[]]$ExtraArguments) {
    $outputFile = Join-Path $reportFolder ($Name + '.json')
    $errorFile = Join-Path $reportFolder ($Name + '-stderr.txt')
    $path = Join-Path (Join-Path $root 'scripts') $Script
    try {
        # Separate process: an exit in the SQL probe cannot terminate report packaging.
        & "$PSHOME\powershell.exe" -NoLogo -NoProfile -NonInteractive -File $path @ExtraArguments 2> $errorFile |
            Out-File -LiteralPath $outputFile -Encoding UTF8
        $code = $LASTEXITCODE
        $parsed = $null
        try { $parsed = Get-Content -LiteralPath $outputFile -Raw | ConvertFrom-Json } catch {}
        return [pscustomobject]@{ exitCode=$code; jsonParsed=($null -ne $parsed); result=$parsed }
    } catch {
        # Preserve independently completed checks. Do not log raw connection errors.
        return [pscustomobject]@{
            exitCode = -1
            jsonParsed = $false
            exceptionType = $_.Exception.GetType().FullName
        }
    }
}
Write-Host '1/2: SQL read-only check (Windows identity, trusted TLS)...'
$sql = Invoke-Probe 'sql' 'probe-windows-sql.ps1' @('-Server', $Server)
$summary.sql = $sql
if ($sql.exitCode -eq 0 -and $sql.jsonParsed -and $sql.result.state -eq 'verified_read') {
    $summary.productionReadVerified = $true
}
Write-Host '2/2: Services, bridge listener, export and scheduler metadata...'
$summary.diagnostics = Invoke-Probe 'server' 'inspect-server.ps1' @('-ExportPath', $ExportPath)
$summary | ConvertTo-Json -Depth 12 | Out-File -LiteralPath (Join-Path $reportFolder 'summary.json') -Encoding UTF8
$archive = $reportFolder + '.zip'
try {
    Compress-Archive -LiteralPath $reportFolder -DestinationPath $archive -CompressionLevel Optimal
    Write-Host ('REPORT ZIP: ' + $archive)
} catch {
    Write-Host ('ZIP unavailable. Report files are preserved in: ' + $reportFolder)
}
if ($summary.productionReadVerified) {
    Write-Host 'SQL READ VERIFIED. Business mappings and dashboard data remain unverified.'
    exit 0
}
Write-Host 'SQL READ NOT VERIFIED. Send the report ZIP for diagnosis; do not start another export.'
exit 1
