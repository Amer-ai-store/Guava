# Windows PowerShell 5.1; no password, no export, no changes to SQL or scheduled tasks.
# Requires an authorized Windows identity with read-only database permissions.
[CmdletBinding()]
param(
    [string]$Server = 'JAWAFFASVR'
)
$ErrorActionPreference = 'Stop'
$connection = $null
$stage = 'initialization'
$exitCode = 1
try {
    Add-Type -AssemblyName System.Data
    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    $builder.DataSource = $Server
    $builder.InitialCatalog = 'Jawafa_DB'
    $builder.IntegratedSecurity = $true
    $builder.Encrypt = $true
    $builder.TrustServerCertificate = $false
    $builder.ConnectTimeout = 5
    $builder.Pooling = $false
    $builder.ApplicationName = 'TrustSoft ReadOnly Probe'
    # ReadOnly is a routing hint, NOT a security boundary; permission checks follow.
    $builder['ApplicationIntent'] = 'ReadOnly'
    $connection = New-Object System.Data.SqlClient.SqlConnection($builder.ConnectionString)
    $stage = 'tls_and_windows_auth'
    $connection.Open()

    function Read-Query([string]$Name, [int]$Limit = 20000) {
        $queryPath = Join-Path (Join-Path $PSScriptRoot 'sql') ($Name + '.sql')
        $command = $connection.CreateCommand()
        $command.CommandText = [System.IO.File]::ReadAllText($queryPath)
        $command.CommandTimeout = 8
        $reader = $null
        try {
            $reader = $command.ExecuteReader()
            $rows = New-Object 'System.Collections.Generic.List[object]'
            while ($reader.Read()) {
                if ($rows.Count -ge $Limit) { throw 'metadata_limit_exceeded' }
                $item = [ordered]@{}
                for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                    $value = $null
                    if (-not $reader.IsDBNull($i)) { $value = $reader.GetValue($i) }
                    $item[$reader.GetName($i)] = $value
                }
                $rows.Add([pscustomobject]$item)
            }
            return $rows.ToArray()
        } finally {
            if ($null -ne $reader) { $reader.Dispose() }
            $command.Dispose()
        }
    }

    $stage = 'read_only_permission_check'
    $permissions = @(Read-Query 'permissions')
    if ($permissions.Count -gt 0) {
        throw 'read_only_principal_required'
    }
    $stage = 'identity'
    $identityRows = @(Read-Query 'identity')
    if ($identityRows.Count -ne 1) { throw 'identity_not_verified' }
    $identity = $identityRows[0]
    if ($identity.database_name -ne 'Jawafa_DB') { throw 'wrong_database' }
    $actualHost = (($identity.server_name -split '\\')[0] -split '\.')[0]
    if ($actualHost -ne 'JAWAFFASVR') { throw 'wrong_server' }
    $stage = 'metadata'
    $catalog = @(Read-Query 'catalog')
    $relations = @(Read-Query 'relations')
    [ordered]@{
        state = 'verified_read'
        observedUtc = (Get-Date).ToUniversalTime().ToString('o')
        identity = $identity
        catalog = $catalog
        relations = $relations
        permissionScope = 'Additional DBA review of inherited and server-level permissions remains required.'
        businessSamplesVerified = $false
        originalDataChanged = $false
    } | ConvertTo-Json -Depth 8
    $exitCode = 0
} catch {
    # Do not print connection strings, raw driver messages, or credentials.
    $reason = 'connection_or_query_failed'
    $allowed = @('read_only_principal_required','wrong_database','wrong_server','identity_not_verified','metadata_limit_exceeded')
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    $sqlErrors = @()
    $cause = $_.Exception
    while ($null -ne $cause) {
        if ($cause -is [System.Data.SqlClient.SqlException]) {
            $sqlErrors = @($cause.Errors | ForEach-Object {
                [pscustomobject]@{ number=$_.Number; state=$_.State; severity=$_.Class }
            })
            break
        }
        $cause = $cause.InnerException
    }
    [ordered]@{
        state = 'blocked'
        stage = $stage
        reason = $reason
        exceptionType = $_.Exception.GetType().FullName
        sqlErrors = $sqlErrors
        originalDataChanged = $false
    } | ConvertTo-Json -Depth 4
} finally {
    if ($null -ne $connection) { $connection.Dispose() }
}
exit $exitCode
