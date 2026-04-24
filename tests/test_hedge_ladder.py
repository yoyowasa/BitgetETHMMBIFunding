from __future__ import annotations

import time

from bot.oms.oms import HedgeTicket, OMS
from bot.types import Side
from tests.test_oms_lock import DummyGateway, DummyLogger, _config


def test_hedge_ladder_post_only_then_ioc() -> None:
    oms = OMS(DummyGateway(), _config(), risk=None, orders_logger=DummyLogger(), fills_logger=DummyLogger())
    spot_bbo = type("BBO", (), {"bid": 100.0, "ask": 100.2})()
    ticket = HedgeTicket(
        ticket_id="t1",
        symbol="ETHUSDT",
        side=Side.BUY,
        want_qty=1.0,
        filled_qty=0.0,
        created_ts=time.time(),
        deadline_ts=time.time() + 2.0,
        tries=0,
        status="OPEN",
        reason="test",
        perp_fill_ts=time.time(),
        perp_fill_price=100.0,
    )
    order_type, force, price = oms._spot_hedge_order_plan(ticket, spot_bbo, 5.0)
    assert force.value == "post_only"
    ticket.created_ts = time.time() - 2.0
    order_type, force, price = oms._spot_hedge_order_plan(ticket, spot_bbo, 5.0)
    assert force.value == "ioc"
