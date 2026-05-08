# Bot 停止スクリプト
# - logs/bot.pid から pid を取得し、graceful 停止 (SIGTERM 相当 = Ctrl-C)
# - bot は SIGINT 受信で cancel_all + flatten 試行する設計
# - 30 秒待っても止まらなければ強制 kill

param(
    [int]$GracefulWaitSec = 30
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "logs\bot.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "[stop_bot] no pid file. bot likely not running."
    exit 0
}

$pidVal = Get-Content $pidFile -ErrorAction SilentlyContinue
if (-not $pidVal) {
    Write-Host "[stop_bot] empty pid file"
    Remove-Item $pidFile -Force
    exit 0
}

$proc = Get-Process -Id $pidVal -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "[stop_bot] pid=$pidVal not running. cleaning pid file"
    Remove-Item $pidFile -Force
    exit 0
}

Write-Host "[stop_bot] stopping pid=$pidVal"

# Windows の Ctrl-C 送信は AttachConsole が必要。ここでは子プロセス含めて停止要求を送る。
$null = & taskkill /PID $pidVal /T 2>&1

# 待機
$deadline = (Get-Date).AddSeconds($GracefulWaitSec)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $pidVal -ErrorAction SilentlyContinue)) {
        Write-Host "[stop_bot] pid=$pidVal stopped gracefully"
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        exit 0
    }
    Start-Sleep -Seconds 1
}

# 強制 kill
Write-Warning "[stop_bot] pid=$pidVal did not stop in ${GracefulWaitSec}s. forcing kill"
& taskkill /PID $pidVal /T /F 2>&1
Start-Sleep -Seconds 2
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "[stop_bot] forced kill done. CHECK BITGET WEB FOR OPEN POSITIONS"
