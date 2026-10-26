# Read-only diagnostics; failures are isolated so partial evidence is retained.
param([string]$ExportPath = 'C:\Export\hourly\20261026-1640')
$ErrorActionPreference = 'Stop'
$result = [ordered]@{
    observedUtc = (Get-Date).ToUniversalTime().ToString('o')
    computer = $env:COMPUTERNAME
    coverage = 'Best effort for current account. Empty results do not prove absence.'
}
function Invoke-Check([string]$Name, [scriptblock]$Action) {
    try {
        $result[$Name] = [ordered]@{ state = 'observed'; items = @(& $Action) }
    } catch {
        $result[$Name] = [ordered]@{
            state = 'unavailable'
            exceptionType = $_.Exception.GetType().FullName
        }
    }
}
Invoke-Check 'services' {
    Get-Service | Where-Object { $_.Name -match '^(MSSQL|SQLSERVERAGENT|SQLAgent)' } |
        Select-Object Name,DisplayName,@{Name='Status';Expression={$_.Status.ToString()}}
}
Invoke-Check 'bridgeListeners' {
    # Enumerate listeners rather than treating a no-match error as a failed check.
    Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq 3456 |
        Select-Object LocalAddress,LocalPort,OwningProcess
}
Invoke-Check 'exportProcesses' {
    # Arguments are inspected only for the script name and never included in the report.
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
        Where-Object { $_.CommandLine -match 'hourly-export-v[23]\.ps1' } |
        Select-Object ProcessId,CreationDate,Name
}
Invoke-Check 'exportTasks' {
    Get-ScheduledTask |
        Where-Object { ($_.Actions.Arguments -join ' ') -match 'hourly-export-v[23]\.ps1' } |
        ForEach-Object {
            $info = $_ | Get-ScheduledTaskInfo
            [pscustomobject]@{
                TaskName = $_.TaskName
                State = $_.State.ToString()
                LastRunTime = $info.LastRunTime
                LastTaskResult = $info.LastTaskResult
                ScriptVersion = if (($_.Actions.Arguments -join ' ') -match 'hourly-export-v3\.ps1') { 'v3' } else { 'v2' }
            }
        }
}
Invoke-Check 'export' {
    if (-not (Test-Path -LiteralPath $ExportPath -PathType Container)) {
        throw 'export_directory_unavailable'
    }
    $files = @(Get-ChildItem -LiteralPath $ExportPath -Filter '*.csv' -File)
    [pscustomobject]@{
        path = $ExportPath
        count = $files.Count
        bytes = ($files | Measure-Object Length -Sum).Sum
        newestUtc = ($files | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).LastWriteTimeUtc
        zeroLength = @($files | Where-Object Length -eq 0 | Select-Object Name)
        completeness = 'UNVERIFIED: file count is not proof of complete export'
    }
}
Invoke-Check 'log' {
    # Metadata only; no potentially secret log contents are copied.
    Get-Item -LiteralPath 'C:\Export\hourly\run.log' | Select-Object Length,LastWriteTimeUtc
}
$result | ConvertTo-Json -Depth 8
