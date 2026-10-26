# Inventory only. No backup creation, restore, download or source file modification.
param([AllowEmptyString()][string]$Directory)
$ErrorActionPreference = 'Stop'
try {
    if (-not $Directory) {
        @{state='not_configured';files=@();restorability='not_verified'} | ConvertTo-Json
        exit 0
    }
    $files = @(Get-ChildItem -LiteralPath $Directory -File |
        Where-Object { $_.Extension -in @('.bak','.zip','.csv','.json') } |
        Select-Object -First 200 Name,Length,LastWriteTimeUtc)
    @{state='inventory_only_max_200';files=$files;restorability='not_verified'} | ConvertTo-Json -Depth 5
    exit 0
} catch {
    @{state='unavailable';files=@();restorability='not_verified';exceptionType=$_.Exception.GetType().FullName} | ConvertTo-Json
    exit 1
}
