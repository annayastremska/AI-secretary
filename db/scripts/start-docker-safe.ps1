# Обхід відомого, невиправленого бага Docker Desktop на Windows
# (docker/desktop-feedback #460, #342): будь-яке "неохайне" завершення
# backend-процесу лишає AF_UNIX socket-файли (dockerInference,
# docker-secrets-engine\engine.sock), заблоковані ядром Windows
# (afd.sys) -- і НАСТУПНИЙ запуск валиться з "The filename, directory
# name, or volume label syntax is incorrect.", навіть якщо офіційні
# налаштування (EnableInference тощо) вимкнені -- вони не запобігають
# цьому. Єдиний робочий спосіб без перезавантаження ПК -- перейменувати
# (не видалити -- видалення падає, поки файл заблокований) теку, щоб
# Docker Desktop створив її заново з нуля.
#
# Використання: замість прямого запуску "Docker Desktop.exe" викликати
# цей скрипт -- він сам чистить сирітські файли (якщо є) і чекає
# готовності Docker.

$ErrorActionPreference = "SilentlyContinue"

Write-Host "Зупиняю запущені Docker-процеси..."
Get-Process "Docker Desktop", "com.docker.backend", "docker-ai" -ErrorAction SilentlyContinue | Stop-Process -Force
wsl --shutdown
Start-Sleep -Seconds 2

$staleDirs = @(
    "$env:LOCALAPPDATA\Docker\run",
    "$env:LOCALAPPDATA\docker-secrets-engine"
)
foreach ($dir in $staleDirs) {
    if (Test-Path $dir) {
        $newName = "$(Split-Path $dir -Leaf)_broken_$(Get-Random)"
        try {
            Rename-Item -Path $dir -NewName $newName -ErrorAction Stop
            Write-Host "Перейменував застарілий $dir -> $newName"
        } catch {
            Write-Host "Не вдалось перейменувати $dir ($($_.Exception.Message)) -- можливо, вже чисто."
        }
    }
}

# Прибирання старого сміття від попередніх запусків цього ж скрипта
# (безпечно -- ці теки вже відв'язані від Docker Desktop і ніде не
# використовуються, лишились тільки як історія перейменувань).
Get-ChildItem "$env:LOCALAPPDATA" -Directory -Filter "*_broken_*" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Запускаю Docker Desktop..."
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

$deadline = (Get-Date).AddSeconds(120)
$ready = $false
do {
    Start-Sleep -Seconds 5
    docker version *> $null
    $ready = ($LASTEXITCODE -eq 0)
} until ($ready -or (Get-Date) -gt $deadline)

if ($ready) {
    Write-Host "Docker готовий."
} else {
    Write-Host "Docker не піднявся за 120с -- можливо, потрібен ще один прохід або ручна перевірка."
}
