# One-time launcher for the real HTTPS deployment. No SQL credentials in this file.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT' -or $env:COMPUTERNAME -ne 'JAWAFFASVR') { throw 'Run this installer on JAWAFFASVR.' }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $child = Start-Process -FilePath "$PSHOME\powershell.exe" -Verb RunAs -Wait -PassThru -ArgumentList @('-NoLogo','-NoProfile','-STA','-File',('"' + $PSCommandPath + '"'))
    exit $child.ExitCode
}
$installer = Join-Path $PSScriptRoot 'agent/Install-TrustSoftAgent.ps1'
Add-Type -AssemblyName System.Windows.Forms
function Select-SqlInput([string]$Title) {
    $dialog = New-Object Windows.Forms.OpenFileDialog
    try {
        $dialog.Title = $Title
        $dialog.Filter = 'Text files (*.txt)|*.txt'
        if ($dialog.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { throw 'File selection cancelled.' }
        return $dialog.FileName
    } finally { $dialog.Dispose() }
}
$arguments = @{
    DashboardUrl='https://trustsoft-control.zxzx-mohammad91.workers.dev'
    DBPreferencesPath=(Select-SqlInput 'Select DBPrefernces.txt for the authorized sa login')
    SqlPasswordPath=(Select-SqlInput 'Select SQLPassword_Decrypted text file (never uploaded)')
    AllowSaReadOnlyQueries=$true
}
$keyPath = Join-Path $PSScriptRoot 'AGENT-KEY-PRIVATE.txt'
if (Test-Path -LiteralPath $keyPath -PathType Leaf) { $arguments.AgentTokenFile = $keyPath }
# The authorized sa credential is DPAPI-encrypted locally. Windows task: LocalService.
# SQL account privileges do not change; the agent exposes fixed SELECT probes only.
& $installer @arguments
