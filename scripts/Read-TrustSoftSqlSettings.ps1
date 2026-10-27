# Data parser only: never dot-source, evaluate or log uploaded preferences.
function Read-TrustSoftSqlSettings {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$DBPreferencesPath,
        [Parameter(Mandatory=$true)][string]$SqlPasswordPath
    )
    $preferencesFile = Get-Item -LiteralPath $DBPreferencesPath -ErrorAction Stop
    $passwordFile = Get-Item -LiteralPath $SqlPasswordPath -ErrorAction Stop
    if ($preferencesFile.PSIsContainer -or $passwordFile.PSIsContainer -or
        $preferencesFile.Length -gt 8192 -or $passwordFile.Length -gt 4096) {
        throw 'invalid_settings_file'
    }
    # TrustSoft's supplied preferences use Windows Arabic (1256). BOMs override it.
    $reader = New-Object IO.StreamReader($preferencesFile.FullName, [Text.Encoding]::GetEncoding(1256), $true)
    try { $text = $reader.ReadToEnd() } finally { $reader.Dispose() }
    $values = @{}
    foreach ($line in ($text -split '\r?\n')) {
        $pair = $line -split '=', 2
        if ($pair.Count -ne 2) { continue }
        $name = $pair[0].Trim()
        if ($name -in @('DB Server Name','DB Database Name','SQL User Name')) {
            if ($values.ContainsKey($name)) { throw 'duplicate_connection_setting' }
            $values[$name] = $pair[1].Trim()
        }
    }
    if ($values['DB Server Name'] -cne 'JAWAFFASVR' -or $values['DB Database Name'] -cne 'Jawafa_DB') {
        throw 'unexpected_sql_target'
    }
    $user = $values['SQL User Name']
    if ([string]::IsNullOrWhiteSpace($user) -or $user -match '[\r\n\x00]') { throw 'invalid_sql_user' }
    $reader = New-Object IO.StreamReader($passwordFile.FullName, [Text.Encoding]::UTF8, $true)
    try { $password = $reader.ReadToEnd().TrimEnd([char[]]"`r`n") } finally { $reader.Dispose() }
    # Preserve significant spaces; remove only the file's final line endings.
    if ([string]::IsNullOrEmpty($password) -or $password -match '[\r\n\x00]') { throw 'invalid_sql_password_file' }
    $secure = ConvertTo-SecureString -String $password -AsPlainText -Force
    $secure.MakeReadOnly()
    $password = $null
    return [pscustomobject]@{ Server='JAWAFFASVR'; Database='Jawafa_DB'; UserName=$user; Password=$secure }
}
