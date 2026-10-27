# Windows PowerShell 5.1; fixed SELECTs only, no exports or SQL/task changes.
# Integrated auth by default; explicit sa mode requires a local DPAPI credential.
[CmdletBinding()]
param(
    [string]$Server = 'JAWAFFASVR',
    [switch]$IdentityOnly,
    [string]$SqlCredentialPath,
    [switch]$AllowSaReadOnlyQueries
)
$ErrorActionPreference = 'Stop'
$connection = $null
$storedCredential = $null
$stage = 'initialization'
$exitCode = 1
try {
    Add-Type -AssemblyName System.Data
    . (Join-Path $PSScriptRoot 'Protect-TrustSoftSqlCredential.ps1')
    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    $builder.DataSource = $Server
    $builder.InitialCatalog = 'Jawafa_DB'
    $builder.IntegratedSecurity = -not [bool]$SqlCredentialPath
    $builder.Encrypt = $true
    $builder.TrustServerCertificate = $false
    $builder.ConnectTimeout = 5
    $builder.Pooling = $false
    $builder.ApplicationName = 'TrustSoft ReadOnly Probe'
    # ReadOnly is a routing hint, NOT a security boundary; permission checks follow.
    $builder['ApplicationIntent'] = 'ReadOnly'
    if ($SqlCredentialPath) {
        $stage = 'local_dpapi_credential'
        $storedCredential = Read-TrustSoftProtectedSqlCredential -Path $SqlCredentialPath
        if ($AllowSaReadOnlyQueries -and $storedCredential.UserName -ine 'sa') { throw 'explicit_sa_login_required' }
        $sqlCredential = New-Object System.Data.SqlClient.SqlCredential($storedCredential.UserName,$storedCredential.Password)
        $connection = New-Object System.Data.SqlClient.SqlConnection($builder.ConnectionString,$sqlCredential)
        $stage = 'tls_and_sql_auth'
    } else {
        if ($AllowSaReadOnlyQueries) { throw 'explicit_sa_login_required' }
        $connection = New-Object System.Data.SqlClient.SqlConnection($builder.ConnectionString)
        $stage = 'tls_and_windows_auth'
    }
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
    $sqlUser = if ($storedCredential) { $storedCredential.UserName } else { '' }
    $policy = Resolve-TrustSoftSqlReadPolicy -BroadPermissions ($permissions.Count -gt 0) -SqlUserName $sqlUser -AllowSaReadOnlyQueries ([bool]$AllowSaReadOnlyQueries)
    $stage = 'identity'
    $identityRows = @(Read-Query 'identity')
    if ($identityRows.Count -ne 1) { throw 'identity_not_verified' }
    $identity = $identityRows[0]
    if ($identity.database_name -ne 'Jawafa_DB') { throw 'wrong_database' }
    $actualHost = (($identity.server_name -split '\\')[0] -split '\.')[0]
    if ($actualHost -ne 'JAWAFFASVR') { throw 'wrong_server' }
    $stage = 'metadata'
    $catalog = @()
    $relations = @()
    if (-not $IdentityOnly) {
        $catalog = @(Read-Query 'catalog')
        $relations = @(Read-Query 'relations')
    }
    [ordered]@{
        state = 'verified_read'
        observedUtc = (Get-Date).ToUniversalTime().ToString('o')
        identity = $identity
        metadataMode = if ($IdentityOnly) { 'identity_only' } else { 'discovered' }
        catalog = $catalog
        relations = $relations
        authentication = if ($SqlCredentialPath) { 'sql' } else { 'windows' }
        accountReadOnlyEnforced = $policy.accountReadOnlyEnforced
        queryPolicy = $policy.queryPolicy
        permissionScope = 'Fixed SELECTs only. Explicit sa mode does not make the SQL account read-only. DBA review remains required.'
        businessSamplesVerified = $false
        originalDataChanged = $false
    } | ConvertTo-Json -Depth 8
    $exitCode = 0
} catch {
    # Do not print connection strings, raw driver messages, or credentials.
    $reason = 'connection_or_query_failed'
    $allowed = @('read_only_principal_required','wrong_database','wrong_server','identity_not_verified','metadata_limit_exceeded','explicit_sa_login_required','invalid_sql_credential_file','windows_dpapi_required')
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
    if ($null -ne $storedCredential) { $storedCredential.Password.Dispose() }
}
exit $exitCode
