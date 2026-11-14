$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$envPath = Join-Path (Get-Location) ".env"
if (-not (Test-Path $envPath)) {
    Copy-Item ".env.example" ".env"
}

$lines = [System.Collections.Generic.List[string]]::new()
Get-Content $envPath -Encoding UTF8 | ForEach-Object { $lines.Add($_) }

function Get-EnvValue([string]$name) {
    foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($name))=(.*)$") {
            return $Matches[1]
        }
    }
    return ""
}

function Set-EnvValue([string]$name, [string]$value) {
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($name))=") {
            $lines[$i] = "$name=$value"
            $found = $true
            break
        }
    }
    if (-not $found) {
        $lines.Add("$name=$value")
    }
}

Write-Host "=== Настройка AI-маркетолога ===" -ForegroundColor Cyan
Write-Host "Секреты сохраняются только в .env (файл в .gitignore)." -ForegroundColor DarkGray

$base = Read-Host "DeepSeek base URL [$((Get-EnvValue 'DEEPSEEK_BASE_URL'))]"
if ([string]::IsNullOrWhiteSpace($base)) { $base = Get-EnvValue 'DEEPSEEK_BASE_URL' }
if ([string]::IsNullOrWhiteSpace($base)) { $base = "https://api.deepseek.com/v1" }

$model = Read-Host "DeepSeek model [$((Get-EnvValue 'DEEPSEEK_MODEL'))]"
if ([string]::IsNullOrWhiteSpace($model)) { $model = Get-EnvValue 'DEEPSEEK_MODEL' }
if ([string]::IsNullOrWhiteSpace($model)) { $model = "deepseek-chat" }

$secureKey = Read-Host "DeepSeek API key (ввод скрыт, Enter — оставить текущий)" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
)
if ([string]::IsNullOrWhiteSpace($key)) { $key = Get-EnvValue 'DEEPSEEK_API_KEY' }

$imageBase = Read-Host "anyModel base URL [$((Get-EnvValue 'ANYMODEL_BASE_URL'))]"
if ([string]::IsNullOrWhiteSpace($imageBase)) { $imageBase = Get-EnvValue 'ANYMODEL_BASE_URL' }
if ([string]::IsNullOrWhiteSpace($imageBase)) { $imageBase = "https://anymodel.org/v1" }

$imageModel = Read-Host "Модель изображений anymodel [$((Get-EnvValue 'ANYMODEL_IMAGE_MODEL'))]"
if ([string]::IsNullOrWhiteSpace($imageModel)) { $imageModel = Get-EnvValue 'ANYMODEL_IMAGE_MODEL' }
if ([string]::IsNullOrWhiteSpace($imageModel)) { $imageModel = "gpt-image-2.5" }

$imageQuality = Read-Host "Качество изображения low/medium/high [$((Get-EnvValue 'ANYMODEL_IMAGE_QUALITY'))]"
if ([string]::IsNullOrWhiteSpace($imageQuality)) { $imageQuality = Get-EnvValue 'ANYMODEL_IMAGE_QUALITY' }
if ([string]::IsNullOrWhiteSpace($imageQuality)) { $imageQuality = "low" }

$imageSecureKey = Read-Host "anyModel API key (ввод скрыт, Enter — оставить текущий)" -AsSecureString
$imageKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($imageSecureKey)
)
if ([string]::IsNullOrWhiteSpace($imageKey)) { $imageKey = Get-EnvValue 'ANYMODEL_API_KEY' }

$channel = Read-Host "Telegram channel (@username или -100...) [$((Get-EnvValue 'TELEGRAM_CHANNEL_ID'))]"
if ([string]::IsNullOrWhiteSpace($channel)) { $channel = Get-EnvValue 'TELEGRAM_CHANNEL_ID' }

$adminId = Read-Host "Ваш Telegram user ID (необязательно) [$((Get-EnvValue 'ADMIN_TELEGRAM_ID'))]"
if ([string]::IsNullOrWhiteSpace($adminId)) { $adminId = Get-EnvValue 'ADMIN_TELEGRAM_ID' }

$password = Read-Host "Пароль веб-панели (Enter — оставить текущий)" -AsSecureString
$passwordPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
)
if ([string]::IsNullOrWhiteSpace($passwordPlain)) { $passwordPlain = Get-EnvValue 'ADMIN_PASSWORD' }

Set-EnvValue "DEEPSEEK_BASE_URL" $base.Trim()
Set-EnvValue "DEEPSEEK_MODEL" $model.Trim()
Set-EnvValue "DEEPSEEK_API_KEY" $key.Trim()
Set-EnvValue "ANYMODEL_BASE_URL" $imageBase.Trim()
Set-EnvValue "ANYMODEL_IMAGE_MODEL" $imageModel.Trim()
Set-EnvValue "ANYMODEL_IMAGE_QUALITY" $imageQuality.Trim()
Set-EnvValue "ANYMODEL_API_KEY" $imageKey.Trim()
Set-EnvValue "TELEGRAM_CHANNEL_ID" $channel.Trim()
if (-not [string]::IsNullOrWhiteSpace($adminId)) { Set-EnvValue "ADMIN_TELEGRAM_ID" $adminId.Trim() }
if (-not [string]::IsNullOrWhiteSpace($passwordPlain)) { Set-EnvValue "ADMIN_PASSWORD" $passwordPlain }

[System.IO.File]::WriteAllLines($envPath, $lines, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "Готово. Ключи не выводились." -ForegroundColor Green
Write-Host "Проверьте текст: .\.venv\Scripts\python scripts\check_deepseek.py" -ForegroundColor Green
Write-Host "Проверьте изображения: .\.venv\Scripts\python scripts\check_image.py" -ForegroundColor Green
Write-Host "Проверьте Telegram: .\.venv\Scripts\python scripts\check_telegram.py" -ForegroundColor Green
Write-Host "Перезапустите: .\restart.ps1" -ForegroundColor Green
