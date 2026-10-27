# Uses synthetic secrets only. PowerShell 7 execution is not Windows/SQL evidence.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $root 'scripts/Read-TrustSoftSqlSettings.ps1')
. (Join-Path $root 'scripts/Protect-TrustSoftSqlCredential.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('trustsoft-settings-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
$pref = Join-Path $temp 'DBPrefernces.txt'
$pass = Join-Path $temp 'SQLPassword_Decrypted (1).txt'
$script:checks = 0
function Assert-True([bool]$Condition, [string]$Label) {
    if (-not $Condition) { throw ('Test failed: ' + $Label) }
    $script:checks++
}
function Write-Preferences([string]$Text) {
    [IO.File]::WriteAllText($pref, $Text, [Text.Encoding]::GetEncoding(1256))
}
function Assert-Rejected([string]$Expected) {
    $message = $null
    try { $null = Read-TrustSoftSqlSettings -DBPreferencesPath $pref -SqlPasswordPath $pass }
    catch { $message = $_.Exception.Message }
    Assert-True ($message -eq $Expected) $Expected
}
try {
    $valid = "DB Server Name=JAWAFFASVR`r`nDB Database Name=Jawafa_DB`r`nDB User Name=" + [char]0x0645 + "`r`nSQL User Name=sa`r`n"
    Write-Preferences $valid
    [IO.File]::WriteAllText($pass, "  synthetic password  `r`n", (New-Object Text.UTF8Encoding($false)))
    $settings = Read-TrustSoftSqlSettings -DBPreferencesPath $pref -SqlPasswordPath $pass
    try {
        Assert-True ($settings.Server -eq 'JAWAFFASVR' -and $settings.Database -eq 'Jawafa_DB') 'correct target from CP1256'
        Assert-True ($settings.UserName -eq 'sa') 'SQL User Name selected, not application user'
        Assert-True ($settings.Password -is [Security.SecureString] -and $settings.Password.IsReadOnly()) 'read-only SecureString'
        Assert-True ($settings.Password.Length -eq 22) 'significant password spaces preserved'
    } finally { $settings.Password.Dispose() }
    Write-Preferences ($valid + "SQL User Name=other`n")
    Assert-Rejected 'duplicate_connection_setting'
    Write-Preferences ($valid.Replace('JAWAFFASVR','OTHERHOST'))
    Assert-Rejected 'unexpected_sql_target'
    Write-Preferences ($valid.Replace('Jawafa_DB','master'))
    Assert-Rejected 'unexpected_sql_target'
    Write-Preferences ($valid.Replace('SQL User Name=sa','SQL User Name='))
    Assert-Rejected 'invalid_sql_user'
    Write-Preferences $valid
    [IO.File]::WriteAllText($pass, "line1`nline2")
    Assert-Rejected 'invalid_sql_password_file'
    [IO.File]::WriteAllText($pass, '')
    Assert-Rejected 'invalid_sql_password_file'
    [IO.File]::WriteAllText($pass, ('x' * 4097))
    Assert-Rejected 'invalid_settings_file'
    [IO.File]::WriteAllText($pass, 'synthetic')
    [IO.File]::WriteAllText($pref, $valid, (New-Object Text.UTF8Encoding($true)))
    $settings = Read-TrustSoftSqlSettings -DBPreferencesPath $pref -SqlPasswordPath $pass
    Assert-True ($settings.UserName -eq 'sa') 'UTF8 BOM respected'
    $settings.Password.Dispose()
    $policy = Resolve-TrustSoftSqlReadPolicy -BroadPermissions $true -SqlUserName 'sa' -AllowSaReadOnlyQueries $true
    Assert-True ($policy.accountReadOnlyEnforced -eq $false -and $policy.queryPolicy -eq 'explicit_sa_fixed_selects_only') 'explicit sa reads never claim a read-only account'
    foreach ($case in @(
        @{broad=$true;user='sa';allow=$false;error='read_only_principal_required'},
        @{broad=$true;user='other';allow=$true;error='explicit_sa_login_required'},
        @{broad=$true;user='';allow=$false;error='read_only_principal_required'}
    )) {
        $message = $null
        try { $null = Resolve-TrustSoftSqlReadPolicy -BroadPermissions $case.broad -SqlUserName $case.user -AllowSaReadOnlyQueries $case.allow }
        catch { $message = $_.Exception.Message }
        Assert-True ($message -eq $case.error) 'no implicit permission bypass'
    }
    $policy = Resolve-TrustSoftSqlReadPolicy -BroadPermissions $false -SqlUserName 'reader' -AllowSaReadOnlyQueries $false
    Assert-True ($policy.queryPolicy -eq 'read_only_principal_fixed_selects') 'existing reader mode preserved'
    $dpapiVerified = $false
    if ($env:OS -eq 'Windows_NT') {
        $protectedPath = Join-Path $temp 'synthetic.dpapi'
        $secure = ConvertTo-SecureString 'synthetic-secret-for-dpapi-test' -AsPlainText -Force
        $secure.MakeReadOnly()
        $original = New-Object Management.Automation.PSCredential('sa',$secure)
        try {
            Write-TrustSoftProtectedSqlCredential -Path $protectedPath -Credential $original
            $decoded = Read-TrustSoftProtectedSqlCredential -Path $protectedPath
            try {
                Assert-True ($decoded.UserName -eq 'sa' -and $decoded.GetNetworkCredential().Password -eq 'synthetic-secret-for-dpapi-test') 'native Windows DPAPI round trip'
                Assert-True (-not [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($protectedPath)).Contains('synthetic-secret-for-dpapi-test')) 'no plaintext password stored'
            } finally { $decoded.Password.Dispose() }
            $message = $null
            try { Write-TrustSoftProtectedSqlCredential -Path $protectedPath -Credential $original }
            catch { $message = $_.Exception.Message }
            Assert-True ($message -eq 'existing_sql_credential_preserved') 'never overwrite existing credential'
            $dpapiVerified = $true
        } finally { $secure.Dispose() }
    }
    # Every shipped PowerShell file must parse, including the two launchers.
    foreach ($file in (Get-ChildItem -LiteralPath $root -Recurse -Filter '*.ps1' | Where-Object { $_.FullName -notmatch '[\\/](node_modules|local|docs[\\/]deliverables)[\\/]' })) {
        $tokens = $null
        $errors = $null
        $null = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
        Assert-True ($errors.Count -eq 0) ('PowerShell parse: ' + $file.Name)
    }
    [ordered]@{ passed=$true; assertions=$script:checks; runtime=$PSVersionTable.PSVersion.ToString();
        synthetic_credentials_only=$true; native_windows_dpapi_verified=$dpapiVerified;
        windows_execution_verified=$false; production_sql_verified=$false } | ConvertTo-Json
} finally {
    # Only this test's new temporary directory is removed; never user exports/logs.
    Remove-Item -LiteralPath $temp -Recurse -Force
}
