# Outbound-only Windows agent. Fixed read-only probes; no shell supplied by the dashboard.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.AgentId -ne 'JAWAFFASVR' -or $env:COMPUTERNAME -ne 'JAWAFFASVR') { throw 'wrong_agent_host' }
if ($config.Server -notmatch '^[A-Za-z0-9_.\\,-]+$') { throw 'invalid_sql_server' }
if ($config.SqlCredentialPath -and ($config.SqlCredentialPath.Contains('"') -or -not [IO.Path]::IsPathRooted($config.SqlCredentialPath))) { throw 'invalid_sql_credential_path' }
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

function New-AgentFailure([string]$Reason, [int]$HttpStatus = 0) {
    $failure = New-Object InvalidOperationException('agent_request_failed')
    $failure.Data['TrustSoftReason'] = $Reason
    $failure.Data['TrustSoftHttpStatus'] = $HttpStatus
    return $failure
}
function Get-AgentFailureInfo([Exception]$Exception) {
    $allowed = @('http_rejected','transport_failed','tls_failed','request_timeout','invalid_response',
        'invalid_broker_identity','different_broker_rejected','invalid_job','expired_job',
        'outbox_invalid','outbox_too_large','outbox_identity_unavailable','running_state_invalid','unexpected_failure')
    $cause = $Exception
    while ($null -ne $cause) {
        $reason = [string]$cause.Data['TrustSoftReason']
        if ($allowed -contains $reason) {
            $status = 0
            $parsed = 0
            if ([int]::TryParse([string]$cause.Data['TrustSoftHttpStatus'],[ref]$parsed) -and $parsed -ge 100 -and $parsed -le 599) { $status = $parsed }
            return [pscustomobject]@{ reason=$reason; http_status=$status }
        }
        $cause = $cause.InnerException
    }
    return [pscustomobject]@{ reason='unexpected_failure'; http_status=0 }
}
function Write-AgentEvent([string]$Stage, [string]$Reason, [int]$HttpStatus = 0) {
    # All text fields are allowlisted: never persist exception text, URL, token or payload.
    if ($Stage -notin @('hello','broker_identity','outbox','poll','job_validation','probe','result','agent')) { $Stage = 'agent' }
    $safe = Get-AgentFailureInfo (New-AgentFailure $Reason $HttpStatus)
    try {
        $line = @{ observedUtc=[DateTime]::UtcNow.ToString('o'); stage=$Stage; reason=$safe.reason; http_status=$safe.http_status } | ConvertTo-Json -Compress
        [IO.File]::AppendAllText((Join-Path $data 'agent-events.jsonl'), ($line + [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))
    } catch { } # Logging failure must not replace the original connection failure.
}
function Write-AgentFailure([string]$Stage, [Exception]$Exception) {
    $safe = Get-AgentFailureInfo $Exception
    Write-AgentEvent $Stage $safe.reason $safe.http_status
}
function Get-AgentUnixTime {
    # DateTimeOffset.ToUnixTimeSeconds is unavailable on older .NET/PowerShell 5.1 hosts.
    $epoch = New-Object DateTime(1970,1,1,0,0,0,([DateTimeKind]::Utc))
    return [long][Math]::Floor(([DateTime]::UtcNow - $epoch).TotalSeconds)
}
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
        if (-not $response.IsSuccessStatusCode) { throw (New-AgentFailure 'http_rejected' $code) }
        $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        try { return ($raw | ConvertFrom-Json) }
        catch { throw (New-AgentFailure 'invalid_response' $code) }
    } catch {
        if ((Get-AgentFailureInfo $_.Exception).reason -ne 'unexpected_failure') { throw }
        $cause = $_.Exception
        $reason = 'transport_failed'
        while ($null -ne $cause) {
            if ($cause -is [Security.Authentication.AuthenticationException]) { $reason = 'tls_failed'; break }
            if ($cause -is [OperationCanceledException]) { $reason = 'request_timeout' }
            $cause = $cause.InnerException
        }
        throw (New-AgentFailure $reason)
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
function Read-AgentEnvelope([string]$Path, [bool]$RequireOutput = $false) {
    $raw = Get-Content -LiteralPath $Path -Raw
    # ConvertFrom-Json may enumerate a one-element root array into one object.
    if (-not $raw.TrimStart().StartsWith('{')) { throw (New-AgentFailure 'outbox_invalid') }
    $value = $raw | ConvertFrom-Json
    $expected = if ($RequireOutput) { 'id,lease,output' } else { 'id,lease' }
    $fields = ($value.PSObject.Properties.Name | Sort-Object) -join ','
    if ($value -isnot [pscustomobject] -or $fields -cne $expected -or
        $value.id -isnot [string] -or $value.lease -isnot [string] -or
        $value.id -notmatch '^[a-f0-9]{32}$' -or $value.lease -notmatch '^[A-Za-z0-9_-]{32,128}$' -or
        ($RequireOutput -and ($value.output -isnot [pscustomobject] -or $value.output.execution_ok -isnot [bool]))) {
        throw (New-AgentFailure 'outbox_invalid')
    }
    if ($RequireOutput) {
        foreach ($name in @('sql','diagnostics','backups')) {
            if ($value.output.PSObject.Properties.Name -contains $name -and $value.output.$name -isnot [pscustomobject]) { throw (New-AgentFailure 'outbox_invalid') }
        }
        if ($value.output.backups -and $value.output.backups.PSObject.Properties.Name -contains 'files' -and $value.output.backups.files -isnot [Array]) { throw (New-AgentFailure 'outbox_invalid') }
    }
    return $value
}
function Initialize-AgentOutbox {
    if ((Test-Path -LiteralPath $runningPath) -and -not (Test-Path -LiteralPath $pendingPath)) {
        try { $running = Read-AgentEnvelope $runningPath }
        catch {
            Archive-File $runningPath
            Write-AgentEvent 'outbox' 'running_state_invalid'
            return
        }
        Save-Json $pendingPath @{ id=$running.id; lease=$running.lease; output=@{execution_ok=$false;error='agent_interrupted_before_result'} }
    }
}
function Send-PendingAgentResult([string]$Endpoint) {
    Initialize-AgentOutbox
    if (-not (Test-Path -LiteralPath $pendingPath)) { return }
    try { $pending = Read-AgentEnvelope $pendingPath $true }
    catch {
        # Preserve the exact bad input; recover identity only from the separate running record.
        Archive-File $pendingPath
        Write-AgentEvent 'outbox' 'outbox_invalid'
        Initialize-AgentOutbox
        if (-not (Test-Path -LiteralPath $pendingPath)) {
            Write-AgentEvent 'outbox' 'outbox_identity_unavailable'
            return
        }
        $pending = Read-AgentEnvelope $pendingPath $true
        $pending.output = @{execution_ok=$false;error='agent_outbox_invalid'}
        Save-Json $pendingPath $pending
    }
    try { $receipt = Post-Agent $Endpoint '/api/agent/result' $pending }
    catch {
        $safe = Get-AgentFailureInfo $_.Exception
        if ($safe.http_status -ne 413 -or $pending.output.error -eq 'agent_result_exceeded_cloud_limit') { throw }
        # Do not rerun the probe or discard its evidence. Report a small failure with the SAME lease.
        Archive-File $pendingPath
        Write-AgentEvent 'outbox' 'outbox_too_large' 413
        $pending = @{id=$pending.id;lease=$pending.lease;output=@{execution_ok=$false;error='agent_result_exceeded_cloud_limit'}}
        Save-Json $pendingPath $pending
        $receipt = Post-Agent $Endpoint '/api/agent/result' $pending
    }
    Save-Json (Join-Path $history ([Guid]::NewGuid().ToString('N') + '-receipt.json')) @{
        id=$pending.id; accepted=($receipt.accepted -eq $true); rejected=($receipt.rejected -eq $true)
        observedUtc=[DateTime]::UtcNow.ToString('o')
    }
    Archive-File $pendingPath
    Archive-File $runningPath
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
    if ($Kind -eq 'sql' -and $config.SqlCredentialPath) {
        $argument += ' -SqlCredentialPath "' + $config.SqlCredentialPath + '"'
        if ($config.AllowSaReadOnlyQueries -eq $true) { $argument += ' -AllowSaReadOnlyQueries' }
    }
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
# A cycle is separate so recovery can be checked without a real server or SQL process.
function Invoke-AgentCycle([string]$Endpoint) {
    $stage = 'hello'
    try {
        $hello = Post-Agent $Endpoint '/api/agent/hello' @{agent_id=$config.AgentId}
        $stage = 'broker_identity'
        if ($hello.broker_id -notmatch '^[a-f0-9]{32}$') { throw (New-AgentFailure 'invalid_broker_identity') }
        if (Test-Path -LiteralPath $brokerPath) {
            if ([IO.File]::ReadAllText($brokerPath).Trim() -ne $hello.broker_id) { throw (New-AgentFailure 'different_broker_rejected') }
        } else {
            [IO.File]::WriteAllText($brokerPath, $hello.broker_id)
        }
        $stage = 'outbox'
        Send-PendingAgentResult $Endpoint
        $stage = 'poll'
        $answer = Post-Agent $Endpoint '/api/agent/poll' @{agent_id=$config.AgentId}
        if ($null -ne $answer.job) {
            $job = $answer.job
            $stage = 'job_validation'
            if ($job.id -notmatch '^[a-f0-9]{32}$' -or $job.action -notin @('sql_probe','reconnect','server_probe','export_probe','backup_probe')) {
                throw (New-AgentFailure 'invalid_job')
            }
            if ([double]$job.expires -le (Get-AgentUnixTime)) { throw (New-AgentFailure 'expired_job') }
            Save-Json $runningPath @{id=$job.id;lease=$job.lease}
            $stage = 'probe'
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
            $stage = 'result'
            Send-PendingAgentResult $Endpoint
        }
    } catch {
        Write-AgentFailure $stage $_.Exception
        throw
    }
}
$delay = 20
try {
    while ($true) {
        $connected = $false
        foreach ($endpoint in $endpoints) {
            try {
                Invoke-AgentCycle $endpoint
                $connected = $true
                break
            } catch {
                # Authentication and broker identity failures remain closed; no redirects/bypass.
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
