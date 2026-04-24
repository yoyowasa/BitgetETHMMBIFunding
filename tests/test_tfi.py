from __future__ import annotations

from bot.marketdata.tfi import TFIAccumulator


def test_tfi_window_and_normalization() -> None:
    acc = TFIAccumulator(window_sec=5.0)
    acc.add_trade(0.0, 2.0, "buy")
    acc.add_trade(1.0, 1.0, "sell")
    acc.add_trade(6.0, 3.0, "buy")

    value = acc.value_at(6.0)

    assert -1.0 <= value <= 1.0
    assert round(value, 6) == 0.5
