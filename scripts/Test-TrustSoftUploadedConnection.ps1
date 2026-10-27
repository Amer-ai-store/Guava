# One-shot SQL-auth diagnostic; no SQL changes, credentials stored or agent enrollment.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$DBPreferencesPath,
    [Parameter(Mandatory=$true)][string]$SqlPasswordPath
)
$ErrorActionPreference = 'Stop'
$connection = $null
$settings = $null
$stage = 'settings'
$exitCode = 1
try {
    if ($env:OS -ne 'Windows_NT') { throw 'windows_required' }
    . (Join-Path $PSScriptRoot 'Read-TrustSoftSqlSettings.ps1')
    $settings = Read-TrustSoftSqlSettings -DBPreferencesPath $DBPreferencesPath -SqlPasswordPath $SqlPasswordPath
    Add-Type -AssemblyName System.Data
    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    $builder.DataSource = $settings.Server
    $builder.InitialCatalog = $settings.Database
    $builder.Encrypt = $true
    $builder.TrustServerCertificate = $false
    $builder.ConnectTimeout = 5
    $builder.Pooling = $false
    $builder.ApplicationName = 'TrustSoft OneShot Identity Check'
    $builder['ApplicationIntent'] = 'ReadOnly'
    # SqlCredential keeps the password out of the connection string and arguments.
    $credential = New-Object System.Data.SqlClient.SqlCredential($settings.UserName, $settings.Password)
    $connection = New-Object System.Data.SqlClient.SqlConnection($builder.ConnectionString, $credential)
    $stage = 'trusted_tls_and_sql_auth'
    $connection.Open()
    $stage = 'fixed_identity_select'
    $command = $connection.CreateCommand()
    $command.CommandTimeout = 8
    $command.CommandText = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'sql/identity.sql'))
    $reader = $null
    try {
        $reader = $command.ExecuteReader()
        if (-not $reader.Read()) { throw 'identity_not_verified' }
        $serverName = [string]$reader['server_name']
        $databaseName = [string]$reader['database_name']
        $serverUtc = $reader['server_utc']
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        $command.Dispose()
    }
    if ((($serverName -split '\\')[0] -split '\.')[0] -ne 'JAWAFFASVR' -or $databaseName -ne 'Jawafa_DB') {
        throw 'unexpected_sql_identity'
    }
    $stage = 'fixed_permission_select'
    $command = $connection.CreateCommand()
    $command.CommandTimeout = 8
    $command.CommandText = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'sql/permissions.sql'))
    $reader = $null
    try {
        $reader = $command.ExecuteReader()
        $broadPermissions = $reader.Read()
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        $command.Dispose()
    }
    [ordered]@{
        state='verified_identity_read'
        observedUtc=(Get-Date).ToUniversalTime().ToString('o')
        server=$serverName
        database=$databaseName
        serverUtc=$serverUtc.ToString('o')
        sqlReadQuerySucceeded=$true
        detectedWriteOrElevatedPermissions=$broadPermissions
        dedicatedReadOnlyAccountRecommended=$broadPermissions
        agentInstalled=$false
        dashboardSqlConnected=$false
        originalDataChanged=$false
        note='One-shot identity SELECT only. No financial samples or Windows-agent connection verified. DBA permission review remains required.'
    } | ConvertTo-Json -Depth 4
    $exitCode = 0
} catch {
    $allowed = @('windows_required','invalid_settings_file','duplicate_connection_setting','unexpected_sql_target',
        'invalid_sql_user','invalid_sql_password_file','identity_not_verified','unexpected_sql_identity')
    $reason = 'connection_or_query_failed'
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    $numbers = @()
    $cause = $_.Exception
    while ($null -ne $cause) {
        if ($cause -is [System.Data.SqlClient.SqlException]) {
            $numbers = @($cause.Errors | ForEach-Object { [int]$_.Number })
            break
        }
        $cause = $cause.InnerException
    }
    [ordered]@{ state='blocked'; stage=$stage; reason=$reason; sqlErrorNumbers=$numbers;
        sqlReadQuerySucceeded=$false; agentInstalled=$false; dashboardSqlConnected=$false;
        originalDataChanged=$false } | ConvertTo-Json -Depth 4
} finally {
    if ($null -ne $connection) { $connection.Dispose() }
    if ($null -ne $settings) { $settings.Password.Dispose() }
}
exit $exitCode
