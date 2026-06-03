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
    assert "$proc.Dispose()" in script
    assert "exit 0" in script


def test_bounded_runner_enables_shutdown_flatten_by_default() -> None:
    script = (ROOT / "scripts" / "run_bot_for_duration.py").read_text(encoding="utf-8")

    assert 'env.setdefault("SHUTDOWN_FLATTEN_POSITIONS", "1")' in script
    assert "env=env" in script


def test_app_supports_shutdown_flatten_positions() -> None:
    app = (ROOT / "bot" / "app.py").read_text(encoding="utf-8")

    assert "SHUTDOWN_FLATTEN_POSITIONS" in app
    assert "shutdown_flatten_positions_start" in app
    assert "shutdown_flatten_positions_done" in app
    assert "shutdown_flatten_spot_bbo_unavailable" in app
