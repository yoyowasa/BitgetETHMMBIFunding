from __future__ import annotations

from decimal import Decimal

from bot.exchange.constraints import (
    InstrumentConstraints,
    format_price_for_bitget,
    get_price_tick,
    quantize_perp_price,
)
from bot.types import Side


def _constraints(tick_size: float, price_place: int | None = None) -> InstrumentConstraints:
    return InstrumentConstraints(
        min_qty=0.001,
        qty_step=0.001,
        min_notional=0.0,
        tick_size=tick_size,
        price_place=price_place,
    )


def test_get_price_tick_prefers_price_place() -> None:
    constraints = _constraints(0.0, price_place=2)

    assert get_price_tick(constraints) == Decimal("0.01")


def test_quantize_perp_price_buy_rounds_down_and_sell_rounds_up() -> None:
    cases = [
        (_constraints(0.01, price_place=2), "3000.105", Decimal("3000.10"), Decimal("3000.11")),
        (_constraints(0.1, price_place=1), "3000.15", Decimal("3000.1"), Decimal("3000.2")),
        (_constraints(0.001, price_place=3), "3000.1005", Decimal("3000.100"), Decimal("3000.101")),
    ]

    for constraints, price, expected_buy, expected_sell in cases:
        assert quantize_perp_price(price, Side.BUY, constraints) == expected_buy
        assert quantize_perp_price(price, Side.SELL, constraints) == expected_sell


def test_format_price_for_bitget_avoids_float_artifacts() -> None:
    rounded = quantize_perp_price("3000.1000000001", Side.BUY, _constraints(0.01, price_place=2))

    assert format_price_for_bitget(rounded) == "3000.1"
