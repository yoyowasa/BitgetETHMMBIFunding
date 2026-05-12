param(
    [int]$GracefulWaitSec = 30,
    [string]$Config = "config.yaml",
    [string]$LogDir = "",
    [switch]$DryRun
)

Set-StrictMode -Version Latest

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path $root "logs\bot.pid"
$runInfoFile = Join-Path $root "logs\bot.run.json"

function Write-StopEvent([string]$name, [hashtable]$data = @{}) {
    $parts = @("[stop_bot]", $name)
    foreach ($key in ($data.Keys | Sort-Object)) {
        $parts += ("{0}={1}" -f $key, $data[$key])
    }
    Write-Host ($parts -join " ")
}

function Get-ProcByPid([int]$pidValue) {
    return Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
}

function Test-BotCommandLine($proc) {
    if (-not $proc -or [string]::IsNullOrWhiteSpace($proc.CommandLine)) { return $false }
    if ($proc.Name -notmatch "^(python|python\.exe)$") { return $false }
    $cmd = $proc.CommandLine
    return (
        $cmd -like "*-m bot.app*" -and
        $cmd -like "*--config*" -and
        $cmd -like "*$Config*"
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

function Find-BotDescendant([int]$pidValue) {
    $proc = Get-ProcByPid $pidValue
    if (Test-BotCommandLine $proc) { return $proc }
    foreach ($child in (Get-Descendants $pidValue)) {
        if (Test-BotCommandLine $child) { return $child }
    }
    return $null
}

function Find-RunningBotProcesses {
    $all = @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { Test-BotCommandLine $_ }
    )
    $repoMatches = @(
        $all | Where-Object {
            ($_.ExecutablePath -and $_.ExecutablePath -like "$root*") -or
            ($_.CommandLine -and $_.CommandLine -like "*$root*")
        }
    )
    if ($repoMatches.Count -gt 0) { return $repoMatches }
    return $all
}

function Get-LatestRunLogDir {
    if (-not [string]::IsNullOrWhiteSpace($LogDir)) {
        $candidate = if ([System.IO.Path]::IsPathRooted($LogDir)) { $LogDir } else { Join-Path $root $LogDir }
        if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
    }
    if (Test-Path $runInfoFile) {
        try {
            $info = Get-Content $runInfoFile -Raw | ConvertFrom-Json
            if ($info.log_dir) {
                $candidate = if ([System.IO.Path]::IsPathRooted([string]$info.log_dir)) {
                    [string]$info.log_dir
                } else {
                    Join-Path $root ([string]$info.log_dir)
                }
                if (Test-Path $candidate) { return (Resolve-Path $candidate).Path }
            }
        } catch {
        }
    }
    return $null
}

function Test-ShutdownCancelAllDone([string]$logDir) {
    if ([string]::IsNullOrWhiteSpace($logDir) -or -not (Test-Path $logDir)) { return $false }
    $matches = Select-String -Path (Join-Path $logDir "*") -Pattern "shutdown_cancel_all_done" -SimpleMatch -ErrorAction SilentlyContinue
    return (($matches | Measure-Object).Count -gt 0)
}

function Add-CtrlBreakNativeType {
    if ("ConsoleSignal.NativeMethods" -as [type]) { return }
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace ConsoleSignal {
  public static class NativeMethods {
    public delegate bool HandlerRoutine(uint dwCtrlType);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool AttachConsole(uint dwProcessId);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool FreeConsole();
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GenerateConsoleCtrlEvent(uint dwCtrlEvent, uint dwProcessGroupId);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool SetConsoleCtrlHandler(HandlerRoutine handler, bool add);
  }
}
"@
}

function Send-GracefulStop([int]$pidValue) {
    if ($DryRun) {
        Write-StopEvent "dry_run_graceful_signal" @{ pid = $pidValue }
        return $true
    }
    try {
        Add-CtrlBreakNativeType
        [ConsoleSignal.NativeMethods]::SetConsoleCtrlHandler($null, $true) | Out-Null
        [ConsoleSignal.NativeMethods]::FreeConsole() | Out-Null
        $attached = [ConsoleSignal.NativeMethods]::AttachConsole([uint32]$pidValue)
        if (-not $attached) {
            Write-StopEvent "graceful_signal_failed" @{
                pid = $pidValue
                reason = "attach_console_failed"
                win32_error = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            }
            return $false
        }
        $sent = [ConsoleSignal.NativeMethods]::GenerateConsoleCtrlEvent(1, 0)
        [ConsoleSignal.NativeMethods]::FreeConsole() | Out-Null
        if (-not $sent) {
            Write-StopEvent "graceful_signal_failed" @{
                pid = $pidValue
                reason = "generate_ctrl_break_failed"
                win32_error = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            }
            return $false
        }
        Write-StopEvent "graceful_signal_sent" @{ pid = $pidValue; signal = "CTRL_BREAK_EVENT" }
        return $true
    } catch {
        Write-StopEvent "graceful_signal_failed" @{ pid = $pidValue; reason = "exception"; error = $_.Exception.Message }
        return $false
    } finally {
        try { [ConsoleSignal.NativeMethods]::SetConsoleCtrlHandler($null, $false) | Out-Null } catch {}
    }
}

function Get-PidFileTarget {
    if (-not (Test-Path $pidFile)) {
        Write-StopEvent "stale_pid_file" @{
            pid_file_path = $pidFile
            pid_from_file = ""
            reason = "missing"
        }
        return $null
    }
    $rawPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $pidText = ([string]$rawPid).Trim()
    $pidValue = 0
    if (-not [int]::TryParse($pidText, [ref]$pidValue)) {
        Write-StopEvent "stale_pid_file" @{
            pid_file_path = $pidFile
            pid_from_file = $pidText
            reason = "invalid_pid"
        }
        return $null
    }
    $proc = Get-ProcByPid $pidValue
    if (-not $proc) {
        Write-StopEvent "stale_pid_file" @{
            pid_file_path = $pidFile
            pid_from_file = $pidValue
            reason = "pid_not_running"
        }
        return $null
    }
    $bot = Find-BotDescendant $pidValue
    if (-not $bot) {
        Write-StopEvent "stale_pid_file" @{
            pid_file_path = $pidFile
            pid_from_file = $pidValue
            reason = "no_bot_app_descendant"
        }
        return $null
    }
    if ($bot.ProcessId -ne $pidValue) {
        Write-StopEvent "pid_file_wrapper_resolved" @{
            pid_file_path = $pidFile
            pid_from_file = $pidValue
            bot_pid = $bot.ProcessId
        }
    }
    return $bot
}

Set-Location $root
$logDirResolved = Get-LatestRunLogDir
$target = Get-PidFileTarget
if (-not $target) {
    $fallback = @(Find-RunningBotProcesses)
    if ($fallback.Count -gt 0) {
        $target = $fallback | Sort-Object ProcessId -Descending | Select-Object -First 1
        Write-StopEvent "fallback_bot_app_detected" @{
            bot_pid = $target.ProcessId
            match_count = $fallback.Count
        }
    }
}

if (-not $target) {
    Write-StopEvent "no_bot_app_process" @{ remaining_count = 0; forced_stop_used = $false }
    Write-StopEvent "expected_readonly_check" @{
        spot_open_orders = "not_checked"
        futures_open_orders = "not_checked"
        futures_position = "not_checked"
        spot_eth_available = "not_checked"
    }
    if (-not $DryRun) {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

$botPid = [int]$target.ProcessId
Write-StopEvent "stopping_bot_app" @{ bot_pid = $botPid; graceful_wait_sec = $GracefulWaitSec; log_dir = $logDirResolved }

$forcedStopUsed = $false
$stoppedPids = New-Object System.Collections.Generic.List[int]
$signalSent = Send-GracefulStop $botPid

if (-not $DryRun) {
    $deadline = (Get-Date).AddSeconds($GracefulWaitSec)
    while ((Get-Date) -lt $deadline) {
        $remaining = Get-ProcByPid $botPid
        $shutdownDone = Test-ShutdownCancelAllDone $logDirResolved
        if (-not $remaining) {
            $stoppedPids.Add($botPid) | Out-Null
            Write-StopEvent "bot_app_stopped" @{
                bot_pid = $botPid
                graceful = $true
                shutdown_cancel_all_done = $shutdownDone
            }
            break
        }
        if ($shutdownDone) {
            Write-StopEvent "shutdown_cancel_all_done_observed" @{ bot_pid = $botPid; log_dir = $logDirResolved }
        }
        Start-Sleep -Seconds 1
    }

    if (Get-ProcByPid $botPid) {
        $forcedStopUsed = $true
        Write-Warning "[stop_bot] forced_stop_used=true pid=$botPid reason=graceful_timeout_or_signal_failed signal_sent=$signalSent"
        Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        if (-not (Get-ProcByPid $botPid)) { $stoppedPids.Add($botPid) | Out-Null }
    }
}

$remainingCount = (@(Find-RunningBotProcesses) | Measure-Object).Count
Write-StopEvent "stop_summary" @{
    bot_app_process_remaining_count = $remainingCount
    stopped_pids = ($stoppedPids -join ",")
    forced_stop_used = $forcedStopUsed
}
Write-StopEvent "expected_readonly_check" @{
    spot_open_orders = "check_readonly_after_stop"
    futures_open_orders = "check_readonly_after_stop"
    futures_position = "check_readonly_after_stop"
    spot_eth_available = "check_readonly_after_stop"
}

if ($remainingCount -eq 0 -and -not $DryRun) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
