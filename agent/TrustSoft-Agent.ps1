# Outbound-only Windows agent. Fixed read-only probes; no shell supplied by the dashboard.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.AgentId -ne 'JAWAFFASVR' -or $env:COMPUTERNAME -ne 'JAWAFFASVR') { throw 'wrong_agent_host' }
if ($config.Server -notmatch '^[A-Za-z0-9_.\\,-]+$') { throw 'invalid_sql_server' }
$endpoints = @($config.Endpoints)
if ($endpoints.Count -lt 1 -or $endpoints.Count -gt 2) { throw 'invalid_endpoint_count' }
foreach ($endpoint in $endpoints) {
    $uri = [Uri]$endpoint
    if ($uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) {
        throw 'https_origin_required'
    }
}
$data = $config.DataDirectory
$agentLock = [System.IO.File]::Open((Join-Path $data 'agent.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.AllowAutoRedirect = $false
$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds(12)
$client.DefaultRequestHeaders.UserAgent.ParseAdd('TrustSoft-ReadOnly-Agent/1.0')
$client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $config.Token)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$brokerPath = Join-Path $data 'broker-id.txt'
$pendingPath = Join-Path $data 'outbox.json'
$runningPath = Join-Path $data 'running.json'
$history = Join-Path $data 'history'
if (-not (Test-Path -LiteralPath $history)) { New-Item -ItemType Directory -Path $history | Out-Null }

function Post-Agent([string]$Endpoint, [string]$Route, $Payload) {
    $body = $Payload | ConvertTo-Json -Depth 24 -Compress
    $content = New-Object System.Net.Http.StringContent($body, [Text.Encoding]::UTF8, 'application/json')
    $response = $null
    try {
        $response = $client.PostAsync(($Endpoint.TrimEnd('/') + $Route), $content).GetAwaiter().GetResult()
        $code = [int]$response.StatusCode
        # Deliberately do not follow redirects or forward credentials to a discovered URL.
        if ($code -eq 409 -and $Route -eq '/api/agent/result') {
            return [pscustomobject]@{ rejected = $true }
        }
        if (-not $response.IsSuccessStatusCode) { throw 'endpoint_rejected_request' }
        $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return ($raw | ConvertFrom-Json)
    } finally {
        if ($null -ne $response) { $response.Dispose() }
        $content.Dispose()
    }
}
function Save-Json([string]$Path, $Value) {
    $temp = $Path + '.tmp'
    [IO.File]::WriteAllText($temp, ($Value | ConvertTo-Json -Depth 24 -Compress), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $Path -Force
}
function Archive-File([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $name = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss') + '-' + [Guid]::NewGuid().ToString('N') + '-' + [IO.Path]::GetFileName($Path)
        Move-Item -LiteralPath $Path -Destination (Join-Path $history $name)
    }
}
function Invoke-LocalProbe([string]$Kind, [bool]$IdentityOnly = $false) {
    $script = switch ($Kind) { 'sql' {'probe-windows-sql.ps1'} 'backup' {'probe-backups.ps1'} default {'inspect-server.ps1'} }
    $path = Join-Path (Join-Path $PSScriptRoot 'scripts') $script
    $argument = switch ($Kind) {
        'sql' { '-Server "' + $config.Server + '"' }
        'backup' { '-Directory "' + $config.BackupDirectory + '"' }
        default { '-ExportPath "' + $config.ExportPath + '"' }
    }
    if ($Kind -eq 'sql' -and $IdentityOnly) { $argument += ' -IdentityOnly' }
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = "$PSHOME\powershell.exe"
    $start.Arguments = '-NoLogo -NoProfile -NonInteractive -File "' + $path + '" ' + $argument
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw 'probe_start_failed' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(80000)) {
            # Only the child probe created above is terminated, never SQL or export processes.
            $process.Kill()
            throw 'probe_timeout'
        }
        $raw = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult() # Never log raw driver errors.
        if ([Text.Encoding]::UTF8.GetByteCount($raw) -gt 7MB) { throw 'probe_result_too_large' }
        $parsed = $raw | ConvertFrom-Json
        return [pscustomobject]@{ code=$process.ExitCode; result=$parsed }
    } finally { $process.Dispose() }
}
function Invoke-FixedAction([string]$Action, [bool]$IdentityOnly = $false) {
    switch ($Action) {
        { $_ -in @('sql_probe','reconnect') } {
            $probe = Invoke-LocalProbe 'sql' $IdentityOnly
            return @{ execution_ok=($probe.code -eq 0); sql=$probe.result }
        }
        { $_ -in @('server_probe','export_probe') } {
            $probe = Invoke-LocalProbe 'server'
            return @{ execution_ok=($probe.code -eq 0); diagnostics=$probe.result }
        }
        'backup_probe' {
            $probe = Invoke-LocalProbe 'backup'
            return @{ execution_ok=($probe.code -eq 0); backups=$probe.result }
        }
        default { throw 'action_not_allowed' }
    }
}
# If a probe process was interrupted, report uncertainty instead of replaying it.
if ((Test-Path -LiteralPath $runningPath) -and -not (Test-Path -LiteralPath $pendingPath)) {
    $running = Get-Content -LiteralPath $runningPath -Raw | ConvertFrom-Json
    Save-Json $pendingPath @{ id=$running.id; lease=$running.lease; output=@{execution_ok=$false;error='agent_interrupted_before_result'} }
}
$delay = 20
try {
    while ($true) {
        $connected = $false
        foreach ($endpoint in $endpoints) {
            try {
                $hello = Post-Agent $endpoint '/api/agent/hello' @{agent_id=$config.AgentId}
                if ($hello.broker_id -notmatch '^[a-f0-9]{32}$') { throw 'invalid_broker_identity' }
                if (Test-Path -LiteralPath $brokerPath) {
                    if ([IO.File]::ReadAllText($brokerPath).Trim() -ne $hello.broker_id) { throw 'different_broker_rejected' }
                } else {
                    [IO.File]::WriteAllText($brokerPath, $hello.broker_id)
                }
                if (Test-Path -LiteralPath $pendingPath) {
                    $pending = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
                    $receipt = Post-Agent $endpoint '/api/agent/result' $pending
                    Save-Json (Join-Path $history ([Guid]::NewGuid().ToString('N') + '-receipt.json')) @{
                        id=$pending.id; accepted=($receipt.accepted -eq $true); rejected=($receipt.rejected -eq $true)
                        observedUtc=(Get-Date).ToUniversalTime().ToString('o')
                    }
                    Archive-File $pendingPath
                    Archive-File $runningPath
                }
                $answer = Post-Agent $endpoint '/api/agent/poll' @{agent_id=$config.AgentId}
                $connected = $true
                if ($null -ne $answer.job) {
                    $job = $answer.job
                    # Never execute expired commands even if delivered by a delayed response.
                    if ($job.id -notmatch '^[a-f0-9]{32}$' -or $job.action -notin @('sql_probe','reconnect','server_probe','export_probe','backup_probe')) {
                        throw 'invalid_job'
                    }
                    $now = ([DateTimeOffset]::UtcNow).ToUnixTimeSeconds()
                    if ([double]$job.expires -le $now) { throw 'expired_job' }
                    Save-Json $runningPath @{id=$job.id;lease=$job.lease}
                    try { $output = Invoke-FixedAction $job.action ($job.identity_only -eq $true) }
                    catch { $output = @{execution_ok=$false;error='local_probe_failed';exceptionType=$_.Exception.GetType().FullName} }
                    # Keep full evidence locally; bound cloud traffic on the free tier.
                    if ($output.sql -and ([Text.Encoding]::UTF8.GetByteCount(($output | ConvertTo-Json -Depth 24 -Compress)) -gt 192KB)) {
                        Save-Json (Join-Path $history ([Guid]::NewGuid().ToString('N') + '-full-schema.json')) $output
                        $output.sql | Add-Member -NotePropertyName catalogTotal -NotePropertyValue @($output.sql.catalog).Count -Force
                        $output.sql | Add-Member -NotePropertyName relationTotal -NotePropertyValue @($output.sql.relations).Count -Force
                        $output.sql.catalog = @($output.sql.catalog | Select-Object -First 100)
                        $output.sql.relations = @($output.sql.relations | Select-Object -First 100)
                        $output.sql.metadataMode = 'sample'
                    }
                    Save-Json $pendingPath @{id=$job.id;lease=$job.lease;output=$output}
                    # Send immediately, so slow probes do not consume their remaining lease while sleeping.
                    $pending = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
                    $receipt = Post-Agent $endpoint '/api/agent/result' $pending
                    Save-Json (Join-Path $history ([Guid]::NewGuid().ToString('N') + '-receipt.json')) @{
                        id=$pending.id; accepted=($receipt.accepted -eq $true); rejected=($receipt.rejected -eq $true)
                        observedUtc=(Get-Date).ToUniversalTime().ToString('o')
                    }
                    Archive-File $pendingPath
                    Archive-File $runningPath
                    # Delivery is attempted before the next job, even after a restart.
                }
                break
            } catch {
                # Both configured HTTPS origins must lead to the SAME durable broker.
                # No secrets or raw server responses are written to diagnostics.
                continue
            }
        }
        if ($connected) { $delay = 20 } else { $delay = [Math]::Min(300, $delay * 2) }
        Start-Sleep -Seconds ($delay + (Get-Random -Minimum 0 -Maximum 4))
    }
} finally {
    $client.Dispose()
    $handler.Dispose()
    $agentLock.Dispose()
}
