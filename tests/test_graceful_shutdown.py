from __future__ import annotations

import asyncio
import subprocess

from bot.app import _cancel_all_on_shutdown
from scripts import run_bot_for_duration


class DummyLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


class DummyOMS:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.cancel_reasons: list[str] = []

    async def cancel_all(self, reason: str) -> None:
        self.cancel_reasons.append(reason)
        if self.fail:
            raise RuntimeError("cancel failed")


class GracefulProc:
    def __init__(self) -> None:
        self.wait_calls: list[float | None] = []
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        return 0

    def kill(self) -> None:
        self.killed = True


class StuckProc:
    def __init__(self) -> None:
        self.wait_calls: list[float | None] = []
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if timeout is not None:
            raise subprocess.TimeoutExpired("bot", timeout)
        return 1

    def kill(self) -> None:
        self.killed = True


def test_bounded_runner_requests_graceful_shutdown_before_kill(monkeypatch, capsys) -> None:
    calls = []
    proc = GracefulProc()
    monkeypatch.setattr(run_bot_for_duration, "_request_graceful_shutdown", lambda p: calls.append(p))

    code = run_bot_for_duration._stop_after_timeout(
        proc,
        elapsed_sec=90,
        graceful_shutdown_sec=20,
    )

    assert code == 0
    assert calls == [proc]
    assert proc.wait_calls == [20]
    assert proc.killed is False
    output = capsys.readouterr().out
    assert "graceful_shutdown" in output
    assert "terminate" not in output


def test_bounded_runner_kills_after_graceful_shutdown_timeout(monkeypatch, capsys) -> None:
    proc = StuckProc()
    monkeypatch.setattr(run_bot_for_duration, "_request_graceful_shutdown", lambda p: None)

    code = run_bot_for_duration._stop_after_timeout(
        proc,
        elapsed_sec=90,
        graceful_shutdown_sec=1,
    )

    assert code == 1
    assert proc.killed is True
    assert proc.wait_calls == [1, None]
    assert "bounded_graceful_shutdown_timeout" in capsys.readouterr().out


def test_shutdown_cancel_all_logs_done() -> None:
    logger = DummyLogger()
    oms = DummyOMS()

    ok = asyncio.run(_cancel_all_on_shutdown(oms, logger))

    assert ok is True
    assert oms.cancel_reasons == ["shutdown_cancel_all"]
    assert [r["event"] for r in logger.records] == [
        "shutdown_cancel_all_start",
        "shutdown_cancel_all_done",
    ]


def test_shutdown_cancel_all_logs_failed() -> None:
    logger = DummyLogger()
    oms = DummyOMS(fail=True)

    ok = asyncio.run(_cancel_all_on_shutdown(oms, logger))

    assert ok is False
    assert oms.cancel_reasons == ["shutdown_cancel_all"]
    assert [r["event"] for r in logger.records] == [
        "shutdown_cancel_all_start",
        "shutdown_cancel_all_failed",
    ]
