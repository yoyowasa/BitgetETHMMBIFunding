from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from bot.app import _flatten_positions_on_shutdown, _shutdown_position_snapshot
from bot.exchange.constraints import InstrumentConstraints
from bot.types import InstType


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
    assert "shutdown_fill_drain_done" in app
    assert "drain_fills_once" in app


def test_app_keeps_fill_monitor_alive_until_shutdown_flatten() -> None:
    app = (ROOT / "bot" / "app.py").read_text(encoding="utf-8")

    finally_index = app.index("finally:")
    flatten_index = app.index("_flatten_positions_on_shutdown", finally_index)
    cancel_tasks_index = app.index("task.cancel()", flatten_index)
    assert flatten_index < cancel_tasks_index
    assert 'risk.halt("shutdown")' in app


def test_shutdown_position_snapshot_treats_below_min_notional_spot_as_flat(monkeypatch) -> None:
    from bot import app as app_module

    class Logger:
        def __init__(self) -> None:
            self.records: list[dict] = []

        def log(self, record: dict) -> None:
            self.records.append(record)

    class Constraints:
        def get(self, inst_type: InstType):
            if inst_type == InstType.SPOT:
                return InstrumentConstraints(
                    min_qty=0.01,
                    qty_step=0.01,
                    min_notional=1.0,
                    tick_size=0.0001,
                )
            return None

    class Gateway:
        constraints = Constraints()

        async def get_spot_available_balance(self, base_coin: str) -> float:
            assert base_coin == "WLD"
            return 0.89504

        async def get_perp_position(self) -> float:
            return 0.0

    monkeypatch.setattr(
        app_module,
        "_spot_bbo_from_store",
        lambda gateway, config: SimpleNamespace(bid=0.5212, ask=0.5214),
    )
    monkeypatch.setattr(
        app_module.book_md,
        "calc_mid",
        lambda bbo: (bbo.bid + bbo.ask) / 2.0,
    )
    oms = SimpleNamespace(positions=SimpleNamespace(spot_pos=0.0, perp_pos=0.0))
    config = SimpleNamespace(
        symbols=SimpleNamespace(spot=SimpleNamespace(symbol="WLDUSDT")),
        strategy=SimpleNamespace(delta_tolerance_notional=0.2),
    )
    logger = Logger()

    snapshot = asyncio.run(
        _shutdown_position_snapshot(oms, Gateway(), config, logger)
    )

    assert snapshot["spot_notional"] < 1.0
    assert snapshot["spot_flat_notional_threshold"] == 1.0
    assert snapshot["flat"] is True
    assert oms.positions.spot_pos == 0.89504
    assert oms.positions.perp_pos == 0.0


def test_shutdown_flatten_skips_flat_dust_without_order(monkeypatch) -> None:
    from bot import app as app_module

    class Logger:
        def __init__(self) -> None:
            self.records: list[dict] = []

        def log(self, record: dict) -> None:
            self.records.append(record)

    class Constraints:
        def get(self, inst_type: InstType):
            if inst_type == InstType.SPOT:
                return InstrumentConstraints(
                    min_qty=0.0001,
                    qty_step=0.0001,
                    min_notional=1.0,
                    tick_size=0.01,
                )
            return None

    class Gateway:
        constraints = Constraints()

        async def get_spot_available_balance(self, base_coin: str) -> float:
            assert base_coin == "ETH"
            return 0.00012

        async def get_perp_position(self) -> float:
            return 0.0

    class OMS:
        def __init__(self) -> None:
            self.positions = SimpleNamespace(spot_pos=0.0, perp_pos=0.0)
            self.cancel_calls: list[str] = []
            self.flatten_calls: list[dict] = []

        async def cancel_all(self, reason: str) -> None:
            self.cancel_calls.append(reason)

        async def flatten(self, spot_bbo, cycle_id: int, reason: str) -> None:
            self.flatten_calls.append({"cycle_id": cycle_id, "reason": reason})

        async def drain_fills_once(self, source: str) -> int:
            return 0

    monkeypatch.setenv("SHUTDOWN_FLATTEN_POSITIONS", "1")
    monkeypatch.setattr(
        app_module,
        "_spot_bbo_from_store",
        lambda gateway, config: SimpleNamespace(bid=1675.0, ask=1675.1),
    )
    monkeypatch.setattr(
        app_module.book_md,
        "calc_mid",
        lambda bbo: (bbo.bid + bbo.ask) / 2.0,
    )
    oms = OMS()
    config = SimpleNamespace(
        symbols=SimpleNamespace(spot=SimpleNamespace(symbol="ETHUSDT")),
        strategy=SimpleNamespace(delta_tolerance_notional=0.2),
    )
    logger = Logger()

    ok = asyncio.run(_flatten_positions_on_shutdown(oms, Gateway(), config, logger))

    assert ok is True
    assert oms.cancel_calls == ["shutdown_flatten_positions"]
    assert oms.flatten_calls == []
    skips = [
        record
        for record in logger.records
        if record.get("event") == "shutdown_flatten_positions_skip_flat"
    ]
    assert skips
    assert skips[-1]["flat"] is True
    assert skips[-1]["spot_notional"] < skips[-1]["spot_flat_notional_threshold"]
