$wshell = New-Object -ComObject WScript.Shell
$wshell.AppActivate(12656) | Out-Null
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    "Happ открыт, но VPN не подключён. Нажмите Connect в окне Happ — иначе Telegram-бот не получает сообщения. После подключения бот восстановится сам.",
    "AI-маркетолог — нужен VPN"
)
