from __future__ import annotations

from bot.exchange.bitget_gateway import _parse_spot_constraints


def test_parse_spot_constraints_fallback_min_qty_from_step() -> None:
    row = {
        "minTradeAmount": "0",
        "minTradeUSDT": "1",
        "quantityPrecision": "4",
        "pricePrecision": "2",
    }

    constraints = _parse_spot_constraints(row)

    # minTradeAmount が 0 のときは quantityPrecision 由来のステップへフォールバック
    assert constraints.qty_step == 0.0001
    assert constraints.min_qty == constraints.qty_step
    assert constraints.is_ready()

