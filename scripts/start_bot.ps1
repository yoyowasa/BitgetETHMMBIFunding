# Bot 常駐起動スクリプト
# - bot.app を別ウィンドウで起動
# - クラッシュ時 30 秒後に自動再起動
# - Ctrl-C でループ抜ける（次回起動はしない、現在の bot は引き続き動作）
# - 完全停止は scripts/stop_bot.ps1

param(
    [string]$Config = "config.yaml",
    [int]$RestartDelaySec = 30,
    [string]$PythonExe = ".\.venv\Scripts\python.exe",
    [string]$LogDir = "logs"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Set-Location $root

if (-not (Test-Path $PythonExe)) {
    Write-Error "python not found: $PythonExe"
    exit 1
}
if (-not (Test-Path $Config)) {
    Write-Error "config not found: $Config"
    exit 1
}

function Test-BotProcess {
    param([string]$PidValue)
    if (-not $PidValue) { return $false }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$PidValue" -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    return ($proc.CommandLine -like "*$root*" -and $proc.CommandLine -like "*-m bot.app*")
}

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# PID ファイル（重複起動防止）
$pidFile = Join-Path $root "$LogDir\bot.pid"
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($oldPid -and (Test-BotProcess -PidValue $oldPid)) {
        Write-Error "bot already running (pid=$oldPid). stop first via scripts/stop_bot.ps1"
        exit 1
    }
    Remove-Item $pidFile -Force
}

Write-Host "[start_bot] root=$root config=$Config restart_delay=${RestartDelaySec}s"

while ($true) {
    $startTime = Get-Date
    $runId = $startTime.ToString("yyyyMMdd-HHmmss")
    $stdoutLog = Join-Path $LogDir "bot.$runId.stdout.log"
    $stderrLog = Join-Path $LogDir "bot.$runId.stderr.log"
    $runInfo = Join-Path $LogDir "bot.run.json"
    Write-Host "[start_bot] launching bot.app at $startTime"

    $proc = Start-Process -FilePath $PythonExe `
        -ArgumentList "-m","bot.app","--config",$Config `
        -PassThru -NoNewWindow -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

    Set-Content -Path $pidFile -Value $proc.Id
    @{
        pid = $proc.Id
        run_id = $runId
        started_at = $startTime.ToString("o")
        config = $Config
        stdout = $stdoutLog
        stderr = $stderrLog
        command = "$PythonExe -m bot.app --config $Config"
    } | ConvertTo-Json | Set-Content -Path $runInfo -Encoding UTF8
    Write-Host "[start_bot] pid=$($proc.Id) started"

    Wait-Process -Id $proc.Id
    $exitCode = $proc.ExitCode
    $duration = (Get-Date) - $startTime
    Write-Host "[start_bot] bot exited code=$exitCode after $($duration.TotalSeconds.ToString('F0'))s"

    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue

    # 即座にクラッシュした場合は警告（設定ミスの可能性）
    if ($duration.TotalSeconds -lt 10) {
        Write-Warning "[start_bot] bot exited within 10s. check logs\bot.stderr.log"
    }

    Write-Host "[start_bot] sleeping ${RestartDelaySec}s before restart..."
    Start-Sleep -Seconds $RestartDelaySec
}
