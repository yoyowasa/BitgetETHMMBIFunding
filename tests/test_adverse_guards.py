from __future__ import annotations

from bot.risk.guards import (
    check_aggressive_trade,
    check_fast_mid_move,
    check_tfi_fade,
)


def test_check_fast_mid_move() -> None:
    assert check_fast_mid_move(100.05, 100.0, fade_vol_bps=3.0)
    assert not check_fast_mid_move(100.01, 100.0, fade_vol_bps=3.0)


def test_check_aggressive_trade() -> None:
    assert check_aggressive_trade(100.0, "sell", 100.0, 100.2, proximity_bps=1.0) == "bid"
    assert check_aggressive_trade(100.2, "buy", 100.0, 100.2, proximity_bps=1.0) == "ask"
    assert check_aggressive_trade(99.0, "sell", 100.0, 100.2, proximity_bps=1.0) is None


def test_check_tfi_fade() -> None:
    assert check_tfi_fade(0.7, threshold=0.6) == "ask"
    assert check_tfi_fade(-0.7, threshold=0.6) == "bid"
    assert check_tfi_fade(0.1, threshold=0.6) is None
