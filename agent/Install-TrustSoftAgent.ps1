# One-time elevated setup on JAWAFFASVR. No production services or SQL data are modified.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DashboardUrl,
    [string]$SecondaryDashboardUrl,
    [string]$Server = 'JAWAFFASVR',
    [string]$ExportPath = 'C:\Export\hourly\20261026-1640',
    [string]$BackupDirectory,
    [string]$AgentTokenFile,
    [string]$DBPreferencesPath,
    [string]$SqlPasswordPath,
    [switch]$AllowSaReadOnlyQueries
)
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ne 'JAWAFFASVR') { throw 'Run on JAWAFFASVR.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Installation requires an elevated Windows PowerShell.' }
if ($Server -notmatch '^[A-Za-z0-9_.\\,-]+$' -or ($ExportPath.Contains('"') -or ($BackupDirectory -and $BackupDirectory.Contains('"')))) { throw 'Invalid server or export path.' }
$endpoints = @($DashboardUrl)
if ($SecondaryDashboardUrl) { $endpoints += $SecondaryDashboardUrl }
foreach ($endpoint in $endpoints) {
    $uri = [Uri]$endpoint
    if ($uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) {
        throw 'Use approved HTTPS origins only, without paths or credentials.'
    }
}
$probeSource = Join-Path $PSScriptRoot 'scripts'
if (-not (Test-Path -LiteralPath $probeSource)) { $probeSource = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts' }
foreach ($relative in @('probe-windows-sql.ps1','inspect-server.ps1','probe-backups.ps1','Read-TrustSoftSqlSettings.ps1','Protect-TrustSoftSqlCredential.ps1','sql/permissions.sql','sql/identity.sql','sql/catalog.sql','sql/relations.sql')) {
    if (-not (Test-Path -LiteralPath (Join-Path $probeSource $relative))) { throw 'Required probe files missing; installation not started.' }
}
$install = Join-Path $env:ProgramData 'TrustSoftAgent'
$taskName = 'TrustSoftReadOnlyAgent'
if ((Test-Path -LiteralPath $install) -or (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    throw 'Existing installation preserved. Review it before making an upgrade.'
}
$useSqlAuthentication = [bool]($DBPreferencesPath -or $SqlPasswordPath)
if ($useSqlAuthentication) {
    if (-not $DBPreferencesPath -or -not $SqlPasswordPath -or $Server -cne 'JAWAFFASVR') { throw 'Both SQL input files and the approved server are required.' }
    . (Join-Path $probeSource 'Read-TrustSoftSqlSettings.ps1')
    . (Join-Path $probeSource 'Protect-TrustSoftSqlCredential.ps1')
    $sqlSettings = Read-TrustSoftSqlSettings -DBPreferencesPath $DBPreferencesPath -SqlPasswordPath $SqlPasswordPath
    if ($AllowSaReadOnlyQueries -and $sqlSettings.UserName -ine 'sa') { $sqlSettings.Password.Dispose(); throw 'Explicit sa mode requires the sa login.' }
    if ($sqlSettings.UserName -ieq 'sa' -and -not $AllowSaReadOnlyQueries) { $sqlSettings.Password.Dispose(); throw 'sa requires explicit -AllowSaReadOnlyQueries authorization.' }
    # SQL auth needs no Windows SQL grants or stored Windows account password.
    $userSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-19')
} else {
    if ($AllowSaReadOnlyQueries) { throw 'sa mode requires SQL credential input files.' }
    $account = Get-Credential -Message 'Dedicated existing Windows account authorized for read-only Jawafa_DB access and scheduled-task logon.'
    if ($null -eq $account) { throw 'A dedicated task account is required.' }
    $userSid = (New-Object Security.Principal.NTAccount($account.UserName)).Translate([Security.Principal.SecurityIdentifier])
}
if ($AgentTokenFile) {
    $token = [IO.File]::ReadAllText($AgentTokenFile).Trim()
} else {
    $secure = Read-Host 'Agent token from the protected dashboard service configuration' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}
if ($token.Length -lt 32 -or $token -match '[\r\n]') { throw 'Agent token must be at least 32 characters, without line breaks.' }
function Protect-Directory([string]$Path, [bool]$Writable) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {
        $id = New-Object Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($id,'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
    }
    $rights = if ($Writable) { 'Modify' } else { 'ReadAndExecute' }
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($userSid,$rights,'ContainerInherit,ObjectInherit','None','Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
}
New-Item -ItemType Directory -Path $install | Out-Null
Protect-Directory $install $false
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'TrustSoft-Agent.ps1') -Destination $install
Copy-Item -LiteralPath $probeSource -Destination (Join-Path $install 'scripts') -Recurse
$data = Join-Path $install 'data'
New-Item -ItemType Directory -Path $data | Out-Null
Protect-Directory $data $true
$configPath = Join-Path $install 'config.json'
$sqlCredentialPath = $null
if ($useSqlAuthentication) {
    $sqlCredentialPath = Join-Path $install 'sql-credential.dpapi'
    try {
        $credential = New-Object Management.Automation.PSCredential($sqlSettings.UserName,$sqlSettings.Password)
        Write-TrustSoftProtectedSqlCredential -Path $sqlCredentialPath -Credential $credential
    } finally { $sqlSettings.Password.Dispose() }
}
@{ AgentId='JAWAFFASVR'; Endpoints=$endpoints; Server=$Server; Token=$token;
   SqlCredentialPath=$sqlCredentialPath; AllowSaReadOnlyQueries=[bool]$AllowSaReadOnlyQueries;
   ExportPath=$ExportPath; BackupDirectory=$BackupDirectory; DataDirectory=$data } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $configPath -Encoding UTF8
$token = $null
if ($useSqlAuthentication) {
    # Real local SELECT before starting the agent; task/HTTPS presence is separate.
    $preflightArguments = @('-NoLogo','-NoProfile','-NonInteractive','-File',
        (Join-Path $install 'scripts/probe-windows-sql.ps1'),'-Server',$Server,
        '-SqlCredentialPath',$sqlCredentialPath,'-IdentityOnly')
    if ($AllowSaReadOnlyQueries) { $preflightArguments += '-AllowSaReadOnlyQueries' }
    try {
        $raw = & "$PSHOME\powershell.exe" @preflightArguments 2>$null
        $preflightCode = $LASTEXITCODE
        $preflight = ($raw -join "`n") | ConvertFrom-Json
        $preflight | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $data 'install-sql-check.json') -Encoding UTF8
        if ($preflightCode -eq 0 -and $preflight.state -eq 'verified_read') {
            Write-Host 'SQL identity SELECT succeeded from the installer. Agent task and dashboard heartbeat still require verification.'
        } else { Write-Host 'SQL read not verified. Sanitized evidence: data\install-sql-check.json. Agent will retry safely.' }
    } catch {
        @{state='blocked';reason='installer_probe_failed';originalDataChanged=$false} |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $data 'install-sql-check.json') -Encoding UTF8
        Write-Host 'SQL read not verified; installer probe failed. Agent will retry safely.'
    }
}
$arguments = '-NoLogo -NoProfile -NonInteractive -File "' + (Join-Path $install 'TrustSoft-Agent.ps1') + '" -ConfigPath "' + $configPath + '"'
$action = New-ScheduledTaskAction -Execute "$PSHOME\powershell.exe" -Argument $arguments
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if ($useSqlAuthentication) {
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings -Principal $taskPrincipal | Out-Null
} else {
    # Windows manages this task credential; it is not written to script/config/args.
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings -User $account.UserName -Password ($account.GetNetworkCredential().Password) -RunLevel Limited | Out-Null
}
Start-ScheduledTask -TaskName $taskName
Write-Host 'Agent task registered and start requested. Verify heartbeat and SQL in the dashboard; this message is not proof of connectivity.'
