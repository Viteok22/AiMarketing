$wshell = New-Object -ComObject WScript.Shell
$wshell.AppActivate(16448) | Out-Null
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    "Войдите в ChatGPT в открытом окне Chrome. Это отдельный профиль для прототипа, чтобы автоматизация не мешала вашему основному браузеру. После входа окно закроется автоматически.",
    "AI-маркетолог — вход в ChatGPT"
)
