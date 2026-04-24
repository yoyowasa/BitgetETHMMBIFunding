from __future__ import annotations

from bot.exchange.constraints import InstrumentConstraints
from bot.oms.oms import ActiveOrder, OMS
from bot.types import OrderIntent, Side
from tests.test_oms_lock import DummyGateway, DummyLogger, _config


def test_reprice_threshold_bps_blocks_small_move() -> None:
    config = _config()
    config.strategy.reprice_threshold_bps = 1.0
    oms = OMS(DummyGateway(), config, risk=None, orders_logger=DummyLogger(), fills_logger=DummyLogger())
    existing = ActiveOrder(
        order_id="1",
        client_oid="QUOTE_BID-1-x",
        price=100.0,
        size=1.0,
        side=Side.BUY,
        intent=OrderIntent.QUOTE_BID,
        ts=0.0,
    )
    constraints = InstrumentConstraints(min_qty=0.01, qty_step=0.01, min_notional=0.0, tick_size=0.01)

    assert not oms._should_replace(existing, 100.005, 1.0, constraints)
    assert oms._should_replace(existing, 100.02, 1.0, constraints)
