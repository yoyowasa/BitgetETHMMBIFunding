param(
  [int]$DurationSec = 180,
  [string]$Config = "config.yaml",
  [string]$RuntimeRoot = "runtime_logs",
  [string]$ArtifactRoot = "artifacts",
  [ValidateSet("auto", "private", "public")]
  [string]$PrivateMode = "auto",
  [double]$ControlledGraceSec = 4,
  [int]$HedgeMaxTries = 2,
  [switch]$EnableFillSimulation,
  [double]$SimFillIntervalSec = 5,
  [double]$SimFillQty = 0.01,
  [ValidateSet("buy", "sell", "both")]
  [string]$SimFillSide = "both",
  [bool]$SimulateHedgeSuccess = $true,
  [int]$MinFills = 1,
  [switch]$RequirePnl
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Die([string]$msg) {
  Write-Error "[near_live] ERROR: $msg"
  exit 1
}

function Set-Or-RemoveEnv([string]$name, [AllowNull()][string]$value) {
  if ($null -eq $value) {
    Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue
  } else {
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$configPath = Join-Path $repoRoot $Config
$runRealLogsScript = Join-Path $repoRoot "scripts\run_real_logs.ps1"
$validator = Join-Path $repoRoot "tools\validate_logs.py"
$timestampUtc = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$runId = "near_${timestampUtc}_$PID"
$logDir = Join-Path (Join-Path $repoRoot $RuntimeRoot) $runId
$artifactDir = Join-Path $repoRoot $ArtifactRoot
$reportPath = Join-Path $artifactDir ("near_live_validate_{0}.json" -f $runId)

if (-not (Test-Path $pythonExe)) { Die "missing python executable: $pythonExe" }
if (-not (Test-Path $configPath)) { Die "missing config: $configPath" }
if (-not (Test-Path $runRealLogsScript)) { Die "missing script: $runRealLogsScript" }
if (-not (Test-Path $validator)) { Die "missing validator: $validator" }
if ($DurationSec -le 0) { Die "DurationSec must be > 0" }

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null

$existingBotPids = @(
  Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match '(^|\s)-m\s+bot\.app(\s|$)' } |
    Select-Object -ExpandProperty ProcessId
)

$effectivePrivateMode = $PrivateMode
if ($effectivePrivateMode -eq "auto") {
  if ($existingBotPids.Count -gt 0) {
    $effectivePrivateMode = "public"
  } else {
    $effectivePrivateMode = "private"
  }
}
if ($effectivePrivateMode -eq "private" -and $existingBotPids.Count -gt 0) {
  Die ("existing bot.app detected (pids={0}). stop them or use -PrivateMode public" -f ($existingBotPids -join ","))
}

$runCmd = ".\\.venv\\Scripts\\python.exe scripts\\run_bot_for_duration.py --duration-sec $DurationSec --config `"$Config`""

$savedEnv = @{
  "RUN_ID" = [Environment]::GetEnvironmentVariable("RUN_ID", "Process")
  "LOG_DIR" = [Environment]::GetEnvironmentVariable("LOG_DIR", "Process")
  "REAL_LOG_CMD" = [Environment]::GetEnvironmentVariable("REAL_LOG_CMD", "Process")
  "DRY_RUN" = [Environment]::GetEnvironmentVariable("DRY_RUN", "Process")
  "BOT_MODE" = [Environment]::GetEnvironmentVariable("BOT_MODE", "Process")
  "FORCE_PRIVATE_OFF" = [Environment]::GetEnvironmentVariable("FORCE_PRIVATE_OFF", "Process")
  "SIMULATE_FILLS" = [Environment]::GetEnvironmentVariable("SIMULATE_FILLS", "Process")
  "SIM_FILL_INTERVAL_SEC" = [Environment]::GetEnvironmentVariable("SIM_FILL_INTERVAL_SEC", "Process")
  "SIM_FILL_QTY" = [Environment]::GetEnvironmentVariable("SIM_FILL_QTY", "Process")
  "SIM_FILL_SIDE" = [Environment]::GetEnvironmentVariable("SIM_FILL_SIDE", "Process")
  "SIMULATE_HEDGE_SUCCESS" = [Environment]::GetEnvironmentVariable("SIMULATE_HEDGE_SUCCESS", "Process")
  "BITGET_API_KEY" = [Environment]::GetEnvironmentVariable("BITGET_API_KEY", "Process")
  "BITGET_API_SECRET" = [Environment]::GetEnvironmentVariable("BITGET_API_SECRET", "Process")
  "BITGET_API_PASSPHRASE" = [Environment]::GetEnvironmentVariable("BITGET_API_PASSPHRASE", "Process")
}

try {
  [Environment]::SetEnvironmentVariable("RUN_ID", $runId, "Process")
  [Environment]::SetEnvironmentVariable("LOG_DIR", $logDir, "Process")
  [Environment]::SetEnvironmentVariable("REAL_LOG_CMD", $runCmd, "Process")
  [Environment]::SetEnvironmentVariable("DRY_RUN", "1", "Process")
  [Environment]::SetEnvironmentVariable("BOT_MODE", "live", "Process")
  [Environment]::SetEnvironmentVariable("FORCE_PRIVATE_OFF", "0", "Process")
  [Environment]::SetEnvironmentVariable("SIMULATE_FILLS", "0", "Process")
  [Environment]::SetEnvironmentVariable("SIM_FILL_INTERVAL_SEC", "", "Process")
  [Environment]::SetEnvironmentVariable("SIM_FILL_QTY", "", "Process")
  [Environment]::SetEnvironmentVariable("SIM_FILL_SIDE", "", "Process")
  [Environment]::SetEnvironmentVariable("SIMULATE_HEDGE_SUCCESS", "", "Process")

  if ($EnableFillSimulation) {
    $simHedgeSuccess = if ($SimulateHedgeSuccess) { "1" } else { "0" }
    [Environment]::SetEnvironmentVariable("SIMULATE_FILLS", "1", "Process")
    [Environment]::SetEnvironmentVariable("SIM_FILL_INTERVAL_SEC", [string]$SimFillIntervalSec, "Process")
    [Environment]::SetEnvironmentVariable("SIM_FILL_QTY", [string]$SimFillQty, "Process")
    [Environment]::SetEnvironmentVariable("SIM_FILL_SIDE", $SimFillSide, "Process")
    [Environment]::SetEnvironmentVariable("SIMULATE_HEDGE_SUCCESS", $simHedgeSuccess, "Process")
  }

  if ($effectivePrivateMode -eq "public") {
    [Environment]::SetEnvironmentVariable("FORCE_PRIVATE_OFF", "1", "Process")
    [Environment]::SetEnvironmentVariable("BITGET_API_KEY", "", "Process")
    [Environment]::SetEnvironmentVariable("BITGET_API_SECRET", "", "Process")
    [Environment]::SetEnvironmentVariable("BITGET_API_PASSPHRASE", "", "Process")
  }

  Write-Host "[near_live] RUN_ID=$runId"
  Write-Host "[near_live] LOG_DIR=$logDir"
  Write-Host "[near_live] PrivateMode=$effectivePrivateMode"
  Write-Host "[near_live] FillSimulation=$EnableFillSimulation"
  Write-Host "[near_live] REAL_LOG_CMD=$runCmd"

  $runner = Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runRealLogsScript) `
    -Wait -PassThru -NoNewWindow -WorkingDirectory $repoRoot
  $runExit = $runner.ExitCode

  $validateArgs = @(
    $validator,
    $logDir,
    "--json-out",
    $reportPath,
    "--require-files",
    "system*.jsonl,orders*.jsonl,fills*.jsonl,mm_*.jsonl",
    "--require-ticket-events",
    "--halt-strict",
    "--controlled-grace-sec",
    [string]$ControlledGraceSec,
    "--hedge-max-tries",
    [string]$HedgeMaxTries
  )
  if ($EnableFillSimulation) {
    $validateArgs += @("--require-fills", "--min-fills", [string]$MinFills)
  }
  if ($RequirePnl) {
    $validateArgs += @("--require-pnl")
  }
  & $pythonExe @validateArgs
  $validateExit = $LASTEXITCODE

  if (-not (Test-Path $reportPath)) { Die "missing validation report: $reportPath" }

  $report = Get-Content -Path $reportPath -Raw | ConvertFrom-Json
  $errorList = @()
  if ($null -ne $report.errors) {
    $errorList = @($report.errors)
  }
  $errorsJoined = if ($errorList.Count -gt 0) { ($errorList -join ", ") } else { "none" }

  Write-Host ("[near_live] run_exit={0} validate_exit={1} status={2}" -f $runExit, $validateExit, $report.status)
  Write-Host ("[near_live] report={0}" -f $reportPath)
  Write-Host ("[near_live] errors={0}" -f $errorsJoined)

  if ($runExit -ne 0) { exit $runExit }
  if ($validateExit -ne 0) { exit $validateExit }
  if ($report.status -ne "PASS") { exit 1 }
  exit 0
}
finally {
  foreach ($name in $savedEnv.Keys) {
    Set-Or-RemoveEnv $name $savedEnv[$name]
  }
}
