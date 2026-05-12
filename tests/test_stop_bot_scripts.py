from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_stop_bot_handles_stale_pid_and_fallback_detection() -> None:
    script = (ROOT / "scripts" / "stop_bot.ps1").read_text(encoding="utf-8")

    assert "stale_pid_file" in script
    assert "Find-RunningBotProcesses" in script
    assert "-m bot.app" in script
    assert "--config" in script
    assert "fallback_bot_app_detected" in script


def test_stop_bot_prefers_graceful_before_force() -> None:
    script = (ROOT / "scripts" / "stop_bot.ps1").read_text(encoding="utf-8")

    graceful_index = script.index("Send-GracefulStop")
    force_index = script.index("forced_stop_used=true")
    assert graceful_index < force_index
    assert "CTRL_BREAK_EVENT" in script
    assert "shutdown_cancel_all_done" in script
    assert "Stop-Process -Id $botPid -Force" in script


def test_run_real_logs_records_actual_bot_pid_metadata() -> None:
    script = (ROOT / "scripts" / "run_real_logs.ps1").read_text(encoding="utf-8")

    assert "Find-BotProcess" in script
    assert "Set-Content -Path $pidFile -Value $botProc.ProcessId" in script
    assert "BOT_PID" in script
    assert "bot.run.json" in script
    assert "config_path" in script
    assert "bot_mode" in script
