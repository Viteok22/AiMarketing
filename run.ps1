$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$usePyLauncher = $false
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 --version *> $null
    if ($LASTEXITCODE -eq 0) {
        $usePyLauncher = $true
    }
}

if (-not (Test-Path ".venv")) {
    Write-Host "Создаю виртуальное окружение..." -ForegroundColor Cyan
    if ($usePyLauncher) {
        & py -3.12 -m venv .venv
    } else {
        & python -m venv .venv
    }
    .\.venv\Scripts\pip install -r requirements.txt
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Создан .env из .env.example." -ForegroundColor Yellow
    Write-Host "Заполните TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, ADMIN_TELEGRAM_ID и ADMIN_PASSWORD." -ForegroundColor Yellow
    Write-Host "Затем запустите .\run.ps1 снова." -ForegroundColor Yellow
    exit 1
}

Write-Host "Запускаю AI-маркетолога на http://127.0.0.1:8000" -ForegroundColor Green
.\.venv\Scripts\python -m app.main
