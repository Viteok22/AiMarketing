$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*app.main*" }

foreach ($process in $processes) {
    Write-Host "Останавливаю старый процесс $($process.ProcessId)..." -ForegroundColor Yellow
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1
& "$PSScriptRoot\run.ps1"
