# Windows DPAPI + the install directory's protected ACL. Never send this file to the cloud.
function Write-TrustSoftProtectedSqlCredential {
    param([Parameter(Mandatory=$true)][string]$Path,
          [Parameter(Mandatory=$true)][Management.Automation.PSCredential]$Credential)
    if ($env:OS -ne 'Windows_NT') { throw 'windows_dpapi_required' }
    if (Test-Path -LiteralPath $Path) { throw 'existing_sql_credential_preserved' }
    Add-Type -AssemblyName System.Security
    $bytes = $null
    $text = $null
    try {
        $text = @{version=1;user=$Credential.UserName;password=$Credential.GetNetworkCredential().Password} | ConvertTo-Json -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($text)
        $protected = [Security.Cryptography.ProtectedData]::Protect($bytes, [Text.Encoding]::UTF8.GetBytes('TrustSoft SQL v1'), [Security.Cryptography.DataProtectionScope]::LocalMachine)
        # Caller creates and ACL-protects the directory BEFORE this write.
        [IO.File]::WriteAllBytes($Path, $protected)
    } finally {
        if ($null -ne $bytes) { [Array]::Clear($bytes,0,$bytes.Length) }
        $text = $null
    }
}
function Read-TrustSoftProtectedSqlCredential {
    param([Parameter(Mandatory=$true)][string]$Path)
    if ($env:OS -ne 'Windows_NT') { throw 'windows_dpapi_required' }
    $file = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($file.PSIsContainer -or $file.Length -gt 32768) { throw 'invalid_sql_credential_file' }
    Add-Type -AssemblyName System.Security
    $bytes = $null
    $text = $null
    $data = $null
    try {
        $bytes = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($file.FullName), [Text.Encoding]::UTF8.GetBytes('TrustSoft SQL v1'), [Security.Cryptography.DataProtectionScope]::LocalMachine)
        $text = [Text.Encoding]::UTF8.GetString($bytes)
        $data = $text | ConvertFrom-Json
        if ($data.version -ne 1 -or [string]::IsNullOrWhiteSpace($data.user) -or [string]::IsNullOrEmpty($data.password)) { throw 'invalid_sql_credential_file' }
        $secure = ConvertTo-SecureString -String $data.password -AsPlainText -Force
        $secure.MakeReadOnly()
        return (New-Object Management.Automation.PSCredential($data.user, $secure))
    } finally {
        if ($null -ne $bytes) { [Array]::Clear($bytes,0,$bytes.Length) }
        $text = $null
        if ($null -ne $data) { $data.password = $null }
    }
}
function Resolve-TrustSoftSqlReadPolicy {
    param([bool]$BroadPermissions, [string]$SqlUserName, [bool]$AllowSaReadOnlyQueries)
    if ($AllowSaReadOnlyQueries -and $SqlUserName -ine 'sa') { throw 'explicit_sa_login_required' }
    if ($BroadPermissions -and -not ($AllowSaReadOnlyQueries -and $SqlUserName -ieq 'sa')) { throw 'read_only_principal_required' }
    return [pscustomobject]@{
        accountReadOnlyEnforced=(-not $BroadPermissions)
        queryPolicy=if ($AllowSaReadOnlyQueries) { 'explicit_sa_fixed_selects_only' } else { 'read_only_principal_fixed_selects' }
    }
}
