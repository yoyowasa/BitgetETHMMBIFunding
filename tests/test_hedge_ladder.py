from __future__ import annotations

import time

from bot.oms.oms import HedgeTicket, OMS
from bot.types import Side
from tests.test_oms_lock import DummyGateway, DummyLogger, _config


class RecordingLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


def _ticket(ticket_id: str = "t1") -> HedgeTicket:
    return HedgeTicket(
        ticket_id=ticket_id,
        symbol="ETHUSDT",
        side=Side.BUY,
        want_qty=1.0,
        filled_qty=0.25,
        created_ts=time.time(),
        deadline_ts=time.time() + 2.0,
        tries=1,
        status="OPEN",
        reason="test",
        perp_fill_ts=time.time(),
        perp_fill_price=100.0,
    )


def test_hedge_uses_ioc_immediately_when_configured() -> None:
    oms = OMS(DummyGateway(), _config(), risk=None, orders_logger=DummyLogger(), fills_logger=DummyLogger())
    spot_bbo = type("BBO", (), {"bid": 100.0, "ask": 100.2})()
    ticket = _ticket()
    order_type, force, price = oms._spot_hedge_order_plan(ticket, spot_bbo, 5.0)
    assert force.value == "ioc"
    assert price > spot_bbo.ask


def test_hedge_ladder_post_only_then_ioc_when_ioc_disabled() -> None:
    config = _config()
    config.hedge.use_spot_limit_ioc = False
    oms = OMS(DummyGateway(), config, risk=None, orders_logger=DummyLogger(), fills_logger=DummyLogger())
    spot_bbo = type("BBO", (), {"bid": 100.0, "ask": 100.2})()
    ticket = _ticket()
    order_type, force, price = oms._spot_hedge_order_plan(ticket, spot_bbo, 5.0)
    assert force.value == "post_only"
    ticket.created_ts = time.time() - 2.0
    order_type, force, price = oms._spot_hedge_order_plan(ticket, spot_bbo, 5.0)
    assert force.value == "ioc"


def test_supersede_open_tickets_logs_separate_from_failures() -> None:
    orders_logger = RecordingLogger()
    oms = OMS(
        DummyGateway(),
        _config(),
        risk=None,
        orders_logger=orders_logger,
        fills_logger=DummyLogger(),
    )
    ticket = _ticket("superseded")
    oms._hedge_tickets[ticket.ticket_id] = ticket

    oms.supersede_open_tickets("flatten_started")

    assert ticket.ticket_id not in oms._hedge_tickets
    assert orders_logger.records[-1]["reason"] == "ticket_superseded"
    assert orders_logger.records[-1]["supersede_reason"] == "flatten_started"
    assert "fail_reason" not in orders_logger.records[-1]


def test_fail_open_tickets_keeps_failure_reason() -> None:
    orders_logger = RecordingLogger()
    oms = OMS(
        DummyGateway(),
        _config(),
        risk=None,
        orders_logger=orders_logger,
        fills_logger=DummyLogger(),
    )
    ticket = _ticket("failed")
    oms._hedge_tickets[ticket.ticket_id] = ticket

    oms.fail_open_tickets("halt")

    assert ticket.ticket_id not in oms._hedge_tickets
    assert orders_logger.records[-1]["reason"] == "ticket_failed"
    assert orders_logger.records[-1]["fail_reason"] == "halt"
