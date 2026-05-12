Set-StrictMode -Version Latest

function Die([string]$msg) {
  # 役割: エラー理由を表示して即終了する
  Write-Error "[run_real_logs] ERROR: $msg"
  exit 1
}

function Require-Env([string]$name) {
  # 役割: 必須の環境変数が空なら止める
  $v = [string]([Environment]::GetEnvironmentVariable($name))
  if ([string]::IsNullOrWhiteSpace($v)) { Die "env '$name' is required" }
}

function Get-DefaultBotCommand {
  # 役割: REAL_LOG_CMD 未設定でも標準の bot 起動コマンドで動けるようにする
  return ".\.venv\Scripts\python.exe -m bot.app --config config.yaml"
}

function Get-GitSha {
  # 役割: git sha を取得する（取得できなければ unknown）
  try {
    $sha = (& git rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($sha)) { return $sha.Trim() }
  } catch {
  }
  return "unknown"
}

function Write-Meta([string]$path, [hashtable]$obj) {
  # 役割: 実行メタ情報をjsonで保存する（追跡と監査のため）
  $json = ($obj | ConvertTo-Json -Compress)
  Set-Content -Path $path -Value $json -Encoding utf8
}

function Get-RepoRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Test-BotProcess($proc, [string]$root, [string]$configPath) {
  if (-not $proc -or [string]::IsNullOrWhiteSpace($proc.CommandLine)) { return $false }
  if ($proc.Name -notmatch "^(python|python\.exe)$") { return $false }
  $cmd = $proc.CommandLine
  if ($cmd -notlike "*-m bot.app*" -or $cmd -notlike "*--config*" -or $cmd -notlike "*$configPath*") {
    return $false
  }
  return (
    ($proc.ExecutablePath -and $proc.ExecutablePath -like "$root*") -or
    ($cmd -like "*$root*") -or
    ($cmd -like "*\.venv\Scripts\python.exe*")
  )
}

function Get-ChildProcesses([int]$parentPid) {
  return @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
      Where-Object { $_.ParentProcessId -eq $parentPid }
  )
}

function Get-Descendants([int]$parentPid) {
  $result = New-Object System.Collections.Generic.List[object]
  $queue = New-Object System.Collections.Queue
  $queue.Enqueue($parentPid)
  while ($queue.Count -gt 0) {
    $pidCurrent = [int]$queue.Dequeue()
    foreach ($child in (Get-ChildProcesses $pidCurrent)) {
      $result.Add($child)
      $queue.Enqueue([int]$child.ProcessId)
    }
  }
  return @($result.ToArray())
}

function Find-BotProcess([int]$rootPid, [string]$repoRoot, [string]$configPath) {
  $rootProc = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue
  if (Test-BotProcess $rootProc $repoRoot $configPath) { return $rootProc }
  foreach ($child in (Get-Descendants $rootPid)) {
    if (Test-BotProcess $child $repoRoot $configPath) { return $child }
  }
  return $null
}

function Get-ConfigPathFromCommand([string]$cmdString) {
  $match = [Regex]::Match($cmdString, "--config\s+([^\s`"']+)")
  if ($match.Success) { return $match.Groups[1].Value }
  return "config.yaml"
}

function Get-ExistingPidFileStatus([string]$pidFile, [string]$repoRoot, [string]$configPath) {
  if (-not (Test-Path $pidFile)) { return @{ stale = $true; reason = "missing"; pid = "" } }
  $rawPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  $pidText = ([string]$rawPid).Trim()
  $pidValue = 0
  if (-not [int]::TryParse($pidText, [ref]$pidValue)) {
    return @{ stale = $true; reason = "invalid_pid"; pid = $pidText }
  }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
  if (-not $proc) { return @{ stale = $true; reason = "pid_not_running"; pid = $pidValue } }
  $bot = Find-BotProcess $pidValue $repoRoot $configPath
  if (-not $bot) { return @{ stale = $true; reason = "no_bot_app_descendant"; pid = $pidValue } }
  return @{ stale = $false; reason = "running"; pid = $pidValue; bot_pid = $bot.ProcessId }
}

function Assert-SingleInstance([string]$cmd) {
  # 役割: 同一コマンドの多重起動を検出したら起動を拒否して終了する（誤爆防止）
  $pattern = [Regex]::Escape($cmd)
  $selfPid = $PID
  $parentPid = $null
  try {
    $selfProc = Get-CimInstance Win32_Process -Filter "ProcessId = $selfPid"
    $parentPid = $selfProc.ParentProcessId
  } catch {
  }
  $procs = @(
    Get-CimInstance Win32_Process |
      Where-Object {
        $_.CommandLine -match $pattern -and
        $_.ProcessId -ne $selfPid -and
        ($null -eq $parentPid -or $_.ProcessId -ne $parentPid)
      } |
      Select-Object -ExpandProperty ProcessId
  )
  if ($procs.Count -gt 0) { Die "already running: $cmd (pids=$($procs -join ','))" }
}

$repoRoot = Get-RepoRoot
$logsDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$pidFile = Join-Path $logsDir "bot.pid"
$runInfo = Join-Path $logsDir "bot.run.json"

$logDir = [Environment]::GetEnvironmentVariable("LOG_DIR")
if ([string]::IsNullOrWhiteSpace($logDir)) { $logDir = [Environment]::GetEnvironmentVariable("LOG_PATH") }
if ([string]::IsNullOrWhiteSpace($logDir)) { $logDir = "logs" }
New-Item -ItemType Directory -Force -Path $logDir | Out-Null  # 役割: ログ保存先を必ず作る

$dryRun = [Environment]::GetEnvironmentVariable("DRY_RUN")
if ([string]::IsNullOrWhiteSpace($dryRun)) { $dryRun = "1" }  # 役割: 既定は安全側

if ($dryRun -eq "0") {
  # 役割: 実運用誤爆防止（明示許可が無いと止める）
  $ok = [Environment]::GetEnvironmentVariable("REAL_RUN_OK")
  if ($ok -ne "1") { Die "DRY_RUN=0 detected. Set REAL_RUN_OK=1 to allow real run." }
}

$tsUtc = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")  # 役割: 実行時刻（UTC）
$runId = [Environment]::GetEnvironmentVariable("RUN_ID")
if ([string]::IsNullOrWhiteSpace($runId)) { $runId = "real_${tsUtc}_$PID" }  # 役割: 実行ID（上書き可）

$outLog = Join-Path $logDir ("real_run_{0}.log" -f $runId)  # 役割: 実行ログ
$metaJson = Join-Path $logDir ("real_run_{0}.meta.json" -f $runId)  # 役割: メタ情報

$cmdString = ""
if ($args.Count -gt 0) {
  # 役割: 引数があればそれをコマンドとして扱う
  $cmdString = ($args -join " ")
} else {
  # 役割: 引数が無ければ REAL_LOG_CMD、未設定なら標準 bot 起動コマンドを使う
  $cmdString = [Environment]::GetEnvironmentVariable("REAL_LOG_CMD")
  if ([string]::IsNullOrWhiteSpace($cmdString)) { $cmdString = Get-DefaultBotCommand }
}

$configPath = Get-ConfigPathFromCommand $cmdString
$pidStatus = Get-ExistingPidFileStatus $pidFile $repoRoot $configPath
if ($pidStatus.stale) {
  "[run_real_logs] stale_pid_file pid_file_path=$pidFile pid_from_file=$($pidStatus.pid) reason=$($pidStatus.reason)" | Write-Host
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
} else {
  Die "bot already running from pid file: pid=$($pidStatus.pid) bot_pid=$($pidStatus.bot_pid)"
}

Assert-SingleInstance $cmdString  # 役割: 実行前に多重起動をブロックする

$gitSha = Get-GitSha  # 役割: git sha を取得する

# 役割: bot本体の system.jsonl でも wrapper と同じ実行ID/sha/cmd を記録できるようにする
[Environment]::SetEnvironmentVariable("RUN_ID", $runId, "Process")
[Environment]::SetEnvironmentVariable("GIT_SHA", $gitSha, "Process")
[Environment]::SetEnvironmentVariable("REAL_LOG_EFFECTIVE_CMD", $cmdString, "Process")

Write-Meta $metaJson @{
  run_id = $runId
  ts_utc = $tsUtc
  log_dir = $logDir
  cmd = $cmdString
  git_sha = $gitSha
  config_path = $configPath
  dry_run = $dryRun
  bot_mode = [Environment]::GetEnvironmentVariable("BOT_MODE")
}  # 役割: メタ情報を書き出す

"[run_real_logs] LOG_DIR=$logDir" | Tee-Object -FilePath $outLog -Append  # 役割: 実行環境をログに残す
"[run_real_logs] RUN_ID=$runId" | Tee-Object -FilePath $outLog -Append  # 役割: 実行IDをログに残す
"[run_real_logs] DRY_RUN=$dryRun" | Tee-Object -FilePath $outLog -Append  # 役割: DRY_RUNをログに残す
"[run_real_logs] CMD=$cmdString" | Tee-Object -FilePath $outLog -Append  # 役割: 実行コマンドをログに残す

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "cmd.exe"
$psi.Arguments = "/c $cmdString 2>&1"
$psi.WorkingDirectory = $repoRoot
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $false
$psi.CreateNoWindow = $false
$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$null = $proc.Start()

$botPidWritten = $false
while (-not $proc.HasExited) {
  $line = $proc.StandardOutput.ReadLine()
  if ($null -ne $line) {
    $line | Tee-Object -FilePath $outLog -Append
  }
  if (-not $botPidWritten) {
    $botProc = Find-BotProcess $proc.Id $repoRoot $configPath
    if ($botProc) {
      Set-Content -Path $pidFile -Value $botProc.ProcessId -Encoding ascii
      Write-Meta $runInfo @{
        pid = $botProc.ProcessId
        wrapper_pid = $PID
        command_pid = $proc.Id
        run_id = $runId
        started_at_utc = $tsUtc
        log_dir = $logDir
        git_sha = $gitSha
        config_path = $configPath
        dry_run = $dryRun
        bot_mode = [Environment]::GetEnvironmentVariable("BOT_MODE")
        cmd = $cmdString
      }
      Write-Meta $metaJson @{
        run_id = $runId
        ts_utc = $tsUtc
        log_dir = $logDir
        cmd = $cmdString
        git_sha = $gitSha
        config_path = $configPath
        dry_run = $dryRun
        bot_mode = [Environment]::GetEnvironmentVariable("BOT_MODE")
        bot_pid = $botProc.ProcessId
        command_pid = $proc.Id
      }
      "[run_real_logs] BOT_PID=$($botProc.ProcessId)" | Tee-Object -FilePath $outLog -Append
      $botPidWritten = $true
    }
  }
}
while (-not $proc.StandardOutput.EndOfStream) {
  $line = $proc.StandardOutput.ReadLine()
  if ($null -ne $line) { $line | Tee-Object -FilePath $outLog -Append }
}
$exitCode = $proc.ExitCode
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
if ($exitCode -ne 0) { exit $exitCode }  # 役割: 失敗時は同じ終了コードで落とす

