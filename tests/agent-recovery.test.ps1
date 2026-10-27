# In-memory HTTP handler + temporary synthetic state; no network, SQL or real credentials.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'agent/TrustSoft-Agent.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Agent did not parse.' }
foreach ($function in $ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false)) {
    . ([scriptblock]::Create($function.Extent.Text))
}
Add-Type -AssemblyName System.Net.Http
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
public class TrustSoftMemoryReply {
    public int Status; public string Body; public bool ThrowTransport;
    public TrustSoftMemoryReply(int status, string body, bool fail) { Status=status; Body=body; ThrowTransport=fail; }
}
public class TrustSoftMemoryHandler : HttpMessageHandler {
    public Queue<TrustSoftMemoryReply> Replies = new Queue<TrustSoftMemoryReply>();
    public List<string> Paths = new List<string>();
    public List<string> Bodies = new List<string>();
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellation) {
        Paths.Add(request.RequestUri.AbsolutePath);
        Bodies.Add(request.Content.ReadAsStringAsync().GetAwaiter().GetResult());
        if (Replies.Count == 0) throw new InvalidOperationException("No in-memory reply configured");
        TrustSoftMemoryReply reply=Replies.Dequeue();
        if (reply.ThrowTransport) throw new InvalidOperationException(reply.Body);
        HttpResponseMessage response=new HttpResponseMessage((HttpStatusCode)reply.Status);
        response.Content=new StringContent(reply.Body);
        return Task.FromResult(response);
    }
}
'@
$temp = Join-Path ([IO.Path]::GetTempPath()) ('trustsoft-recovery-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
$script:checks = 0
$script:probeCalls = 0
function Assert-True([bool]$Condition,[string]$Name) {
    if (-not $Condition) { throw ('Recovery test failed: ' + $Name) }
    $script:checks++
}
function Invoke-FixedAction { $script:probeCalls++; throw 'A pending result must never replay its probe.' }
function Reset-Case([string]$Name) {
    $script:data = Join-Path $temp $Name
    New-Item -ItemType Directory -Path $script:data | Out-Null
    $script:history = Join-Path $script:data 'history'
    New-Item -ItemType Directory -Path $script:history | Out-Null
    $script:pendingPath = Join-Path $script:data 'outbox.json'
    $script:runningPath = Join-Path $script:data 'running.json'
    $script:brokerPath = Join-Path $script:data 'broker-id.txt'
    $script:config = [pscustomobject]@{AgentId='JAWAFFASVR'}
    if ($script:client) { $script:client.Dispose() }
    $script:handler = New-Object TrustSoftMemoryHandler
    $script:client = New-Object Net.Http.HttpClient($script:handler)
}
function Add-Reply([int]$Status,[string]$Body,[bool]$Fail=$false) {
    $script:handler.Replies.Enqueue((New-Object TrustSoftMemoryReply($Status,$Body,$Fail)))
}
function Save-Pending {
    Save-Json $script:runningPath @{id=('a' * 32);lease=('b' * 64)}
    Save-Json $script:pendingPath @{id=('a' * 32);lease=('b' * 64);output=@{execution_ok=$true;diagnostics=@{evidence='synthetic-evidence'}}}
}
$endpoint = 'https://synthetic.invalid'
$hello = '{"broker_id":"cccccccccccccccccccccccccccccccc"}'
try {
    Reset-Case 'oversized'
    Save-Pending
    Add-Reply 200 $hello
    Add-Reply 413 'synthetic-private-response'
    Add-Reply 200 '{"accepted":true}'
    Add-Reply 200 '{"job":null}'
    Invoke-AgentCycle $endpoint
    Assert-True (($handler.Paths -join ',') -eq '/api/agent/hello,/api/agent/result,/api/agent/result,/api/agent/poll') '413 recovery delivers failure before heartbeat poll'
    $small = $handler.Bodies[2] | ConvertFrom-Json
    Assert-True ($small.id -eq ('a' * 32) -and $small.lease -eq ('b' * 64)) 'failure keeps original id and lease'
    Assert-True ($small.output.execution_ok -eq $false -and $small.output.error -eq 'agent_result_exceeded_cloud_limit') 'small failure reports oversized evidence truthfully'
    Assert-True ([Text.Encoding]::UTF8.GetByteCount($handler.Bodies[2]) -lt 1024) 'failure is small'
    $archives = @(Get-ChildItem -LiteralPath $history -Filter '*-outbox.json')
    Assert-True ($archives.Count -eq 2) 'original and replacement result both preserved'
    Assert-True (@($archives | Where-Object { [IO.File]::ReadAllText($_.FullName).Contains('synthetic-evidence') }).Count -eq 1) 'original evidence not discarded'
    Assert-True (-not (Test-Path -LiteralPath $pendingPath) -and -not (Test-Path -LiteralPath $runningPath)) 'acknowledged state archived'
    Assert-True ($script:probeCalls -eq 0) 'oversized result never replays probe'

    Reset-Case 'corrupt-json'
    Save-Pending
    $bad = '{"unclosed":"synthetic-secret-in-bad-json'
    [IO.File]::WriteAllText($pendingPath,$bad)
    Add-Reply 200 $hello
    Add-Reply 200 '{"accepted":true}'
    Add-Reply 200 '{"job":null}'
    Invoke-AgentCycle $endpoint
    $small = $handler.Bodies[1] | ConvertFrom-Json
    Assert-True ($small.output.error -eq 'agent_outbox_invalid' -and $small.id -eq ('a' * 32) -and $small.lease -eq ('b' * 64)) 'corrupt outbox recovers identity from running record'
    Assert-True (@(Get-ChildItem -LiteralPath $history -Filter '*-outbox.json' | Where-Object { [IO.File]::ReadAllText($_.FullName) -ceq $bad }).Count -eq 1) 'exact corrupt bytes preserved'
    Assert-True ($handler.Paths[2] -eq '/api/agent/poll' -and $script:probeCalls -eq 0) 'poll resumes without replay after corrupt JSON'
    Assert-True (-not [IO.File]::ReadAllText((Join-Path $data 'agent-events.jsonl')).Contains('synthetic-secret')) 'corrupt content not leaked to events'

    Reset-Case 'missing-running-identity'
    [IO.File]::WriteAllText($pendingPath,'not-json')
    Add-Reply 200 $hello
    Add-Reply 200 '{"job":null}'
    Invoke-AgentCycle $endpoint
    Assert-True (($handler.Paths -join ',') -eq '/api/agent/hello,/api/agent/poll') 'missing identity is preserved without inventing result credentials'

    Reset-Case 'invalid-output-schema'
    Save-Pending
    Save-Json $pendingPath @{id=('a' * 32);lease=('b' * 64);output='invalid-output'}
    Add-Reply 200 $hello
    Add-Reply 409 '{}'
    Add-Reply 200 '{"job":null}'
    Invoke-AgentCycle $endpoint
    $small = $handler.Bodies[1] | ConvertFrom-Json
    Assert-True ($small.output.error -eq 'agent_outbox_invalid') 'semantically invalid envelope is repaired before delivery'
    Assert-True (($handler.Paths -join ',') -eq '/api/agent/hello,/api/agent/result,/api/agent/poll' -and -not (Test-Path -LiteralPath $pendingPath)) 'expired lease rejection archives evidence and resumes poll without replay'

    $validEnvelope = @{id=('a' * 32);lease=('b' * 64);output=@{execution_ok=$true}} | ConvertTo-Json -Compress
    $invalidCases = @(
        ('[' + $validEnvelope + ']'),
        (@{id=('a' * 32);lease=('b' * 64);output=@{execution_ok=$true};extra='unexpected'} | ConvertTo-Json -Compress),
        (@{id=('a' * 32);lease=('b' * 64);output=@{execution_ok='true'}} | ConvertTo-Json -Compress),
        (@{id=123;lease=('b' * 64);output=@{execution_ok=$true}} | ConvertTo-Json -Compress),
        (@{id=('a' * 32);lease=123;output=@{execution_ok=$true}} | ConvertTo-Json -Compress),
        (@{id=('a' * 32);lease=('b' * 64);output=@{execution_ok=$true;diagnostics='invalid'}} | ConvertTo-Json -Compress)
    )
    $number = 0
    foreach ($invalid in $invalidCases) {
        Reset-Case ('invalid-envelope-' + $number++)
        Save-Pending
        [IO.File]::WriteAllText($pendingPath,$invalid)
        Add-Reply 200 $hello
        Add-Reply 200 '{"accepted":true}'
        Add-Reply 200 '{"job":null}'
        Invoke-AgentCycle $endpoint
        $small = $handler.Bodies[1] | ConvertFrom-Json
        Assert-True ($small.output.error -eq 'agent_outbox_invalid' -and $small.id -eq ('a' * 32)) 'invalid root/type is repaired using separate valid running identity'
        Assert-True ($handler.Paths[2] -eq '/api/agent/poll') 'invalid root/type cannot trap heartbeat behind HTTP400'
    }
    Reset-Case 'invalid-running-envelope'
    [IO.File]::WriteAllText($pendingPath,'not-json')
    Save-Json $runningPath @{id=('a' * 32);lease=('b' * 64);extra='invalid'}
    Add-Reply 200 $hello
    Add-Reply 200 '{"job":null}'
    Invoke-AgentCycle $endpoint
    Assert-True (($handler.Paths -join ',') -eq '/api/agent/hello,/api/agent/poll') 'invalid running root is preserved without inventing identity or replaying probe'
    Assert-True (@(Get-ChildItem -LiteralPath $history -Filter '*-running.json').Count -eq 1) 'invalid running evidence retained'

    foreach ($status in @(401,403)) {
        Reset-Case ('auth-' + $status)
        Save-Pending
        Add-Reply 200 $hello
        Add-Reply $status 'synthetic-sensitive-error-body'
        $rejected = $false
        try { Invoke-AgentCycle $endpoint } catch { $rejected = $true }
        Assert-True ($rejected -and $handler.Paths.Count -eq 2 -and (Test-Path -LiteralPath $pendingPath)) 'auth rejection retains pending result and does not proceed to poll'
        $event = Get-Content -LiteralPath (Join-Path $data 'agent-events.jsonl') | Select-Object -Last 1 | ConvertFrom-Json
        Assert-True ($event.reason -eq 'http_rejected' -and $event.http_status -eq $status) 'auth status recorded without raw response'
    }

    Reset-Case 'broker-mismatch'
    Save-Pending
    [IO.File]::WriteAllText($brokerPath,('d' * 32))
    Add-Reply 200 $hello
    $rejected = $false
    try { Invoke-AgentCycle $endpoint } catch { $rejected = $true }
    Assert-True ($rejected -and $handler.Paths.Count -eq 1 -and (Test-Path -LiteralPath $pendingPath)) 'different broker never receives pending result or poll'

    Reset-Case 'redaction'
    $secret = 'synthetic-token-password-https://private.example/secret'
    Add-Reply 0 $secret $true
    try { Invoke-AgentCycle $endpoint } catch { }
    Write-AgentFailure $secret (New-Object InvalidOperationException($secret))
    $malicious = New-AgentFailure $secret 401
    Write-AgentFailure 'hello' $malicious
    $log = [IO.File]::ReadAllText((Join-Path $data 'agent-events.jsonl'))
    Assert-True (-not $log.Contains($secret) -and -not $log.Contains('private.example')) 'exception message, stage, and unrecognized reason cannot leak secrets'
    foreach ($line in ($log.Trim() -split '\r?\n')) {
        $record = $line | ConvertFrom-Json
        Assert-True (($record.PSObject.Properties.Name | Sort-Object) -join ',' -eq 'http_status,observedUtc,reason,stage') 'only bounded diagnostic fields logged'
    }
    Assert-True ($script:probeCalls -eq 0) 'all recovery cases leave probes untouched'
    Assert-True ([Math]::Abs((Get-AgentUnixTime) - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()) -le 1) 'legacy-compatible Unix time matches current runtime'
    [ordered]@{passed=$true;assertions=$script:checks;in_memory_http_only=$true;actual_agent_functions_tested=$true;
        windows_execution_verified=$false;production_sql_verified=$false} | ConvertTo-Json
} finally {
    if ($script:client) { $script:client.Dispose() }
    Remove-Item -LiteralPath $temp -Recurse -Force
}
