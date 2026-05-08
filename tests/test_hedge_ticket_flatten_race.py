from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from bot.config import (
    AppConfig,
    CostConfig,
    ExchangeConfig,
    HedgeConfig,
    RiskConfig,
    StrategyConfig,
    SymbolConfig,
    SymbolsConfig,
)
from bot.oms.oms import HedgeTicket, OMS
from bot.strategy.mm_funding import MMFundingStrategy
from bot.types import FundingInfo, InstType, OrderRequest, Side


class CapturingLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


class DummyRisk:
    def is_halted(self) -> bool:
        return False

    def stale(self, snapshot_ts: float, now: float) -> bool:
        return False

    def in_cooldown(self, now: float) -> bool:
        return False

    def unhedged_exceeded(self, unhedged_notional: float, unhedged_since) -> bool:
        return True


class StrategyOMS:
    def __init__(self, *, defer_flatten: bool) -> None:
        self.gateway = SimpleNamespace(
            book_ready=True,
            public_book_channel="books5",
            store=object(),
            tfi=0.0,
            last_public_trade=None,
            mid_100ms_ago=lambda now=None: None,
        )
        self.positions = SimpleNamespace(spot_pos=0.0, perp_pos=-0.02)
        self.unhedged_qty = 0.02
        self.unhedged_since = time.time() - 3.0
        self.cancel_reasons: list[str] = []
        self.flatten_calls: list[dict] = []
        self.update_quote_calls: list[dict] = []
        self._defer_flatten = defer_flatten
        deadline = time.time() + (10.0 if defer_flatten else -1.0)
        self._ticket = SimpleNamespace(
            ticket_id="ticket-1",
            remain=0.02,
            deadline_ts=deadline,
            tries=1,
            expired=not defer_flatten,
        )

    async def process_hedge_tickets(self, spot_bbo) -> None:
        return None

    async def cancel_all(self, reason: str) -> None:
        self.cancel_reasons.append(reason)

    async def flatten(self, spot_bbo, cycle_id: int, reason: str) -> None:
        self.flatten_calls.append({"cycle_id": cycle_id, "reason": reason})

    def fail_open_tickets(self, reason: str) -> None:
        return None

    def open_hedge_ticket_snapshot(self, now: float | None = None):
        return self._ticket

    def should_defer_flatten_for_hedge_ticket(self, now: float | None = None) -> bool:
        return self._defer_flatten

    async def update_quotes(self, **kwargs) -> None:
        self.update_quote_calls.append(kwargs)


class DummyConstraintsManager:
    def ready(self) -> bool:
        return True

    def get(self, inst_type: InstType):
        return DummyInstrumentConstraints()


class DummyInstrumentConstraints:
    tick_size = 0.01
    qty_step = 0.0001
    min_qty = 0.0001
    min_notional = 0.0
    price_place = 2

    def is_ready(self) -> bool:
        return True

    def adjust_price(self, price: float) -> float:
        return price

    def adjust_qty(self, qty: float) -> float:
        return qty

    def validate(self, price: float, qty: float) -> bool:
        return price > 0 and qty >= self.min_qty


class OMSGateway:
    def __init__(self) -> None:
        self.constraints = DummyConstraintsManager()
        self.orders: list[OrderRequest] = []

    async def place_order(self, req: OrderRequest) -> dict:
        self.orders.append(req)
        return {"code": "00000", "data": {"orderId": "order-1"}}


def _config() -> AppConfig:
    return AppConfig(
        exchange=ExchangeConfig(name="bitget", base_url="", ws_public="", ws_private=""),
        symbols=SymbolsConfig(
            spot=SymbolConfig(instType="SPOT", symbol="ETHUSDT"),
            perp=SymbolConfig(
                instType="USDT-FUTURES",
                symbol="ETHUSDT",
                productType="USDT-FUTURES",
                marginMode="isolated",
                marginCoin="USDT",
            ),
        ),
        risk=RiskConfig(
            stale_sec=2.0,
            max_unhedged_sec=2.0,
            max_unhedged_notional=20.0,
            max_position_notional=2000.0,
            cooldown_sec=30.0,
            funding_stale_sec=120.0,
        ),
        strategy=StrategyConfig(
            enable_only_positive_funding=True,
            min_funding_rate=0.0,
            target_notional=50.0,
            delta_tolerance=0.01,
            obi_levels=5,
            alpha_obi_bps=1.0,
            gamma_inventory_bps=2.0,
            base_half_spread_bps=14.0,
            min_half_spread_bps=14.0,
            quote_refresh_ms=250,
            dry_run=False,
        ),
        hedge=HedgeConfig(
            use_spot_limit_ioc=True,
            hedge_aggressive_bps=5.0,
            hedge_deadline_sec=2.0,
            hedge_max_tries=2,
        ),
        cost=CostConfig(fee_maker_perp_bps=1.4, fee_taker_spot_bps=10.0, slippage_bps=2.0),
    )


def _snapshot_from_store(store, inst_type: InstType, symbol: str, levels: int, channel=None, return_meta=False):
    snapshot = SimpleNamespace(
        bids=[(100.0, 3.0)],
        asks=[(100.2, 3.0)],
        ts=time.time(),
    )
    return (snapshot, True) if return_meta else snapshot


def _run_strategy_step(monkeypatch, oms: StrategyOMS) -> CapturingLogger:
    from bot.strategy import mm_funding as module

    monkeypatch.setattr(module.book_md, "snapshot_from_store", _snapshot_from_store)
    monkeypatch.setattr(
        module.book_md,
        "bbo_from_snapshot",
        lambda snapshot: SimpleNamespace(
            bid=snapshot.bids[0][0],
            ask=snapshot.asks[0][0],
            bid_size=snapshot.bids[0][1],
            ask_size=snapshot.asks[0][1],
            ts=snapshot.ts,
        ),
    )
    monkeypatch.setattr(module.book_md, "calc_mid", lambda bbo: (bbo.bid + bbo.ask) / 2.0)
    monkeypatch.setattr(module.book_md, "calc_microprice", lambda bbo: (bbo.ask + bbo.bid) / 2.0)
    monkeypatch.setattr(module.book_md, "calc_obi", lambda snapshot: 0.0)

    logger = CapturingLogger()
    funding_cache = SimpleNamespace(
        last=FundingInfo(
            funding_rate=0.0001,
            next_update_time=None,
            interval_sec=None,
            ts=time.time(),
        )
    )
    strategy = MMFundingStrategy(_config(), funding_cache, oms, DummyRisk(), logger)
    asyncio.run(strategy.step())
    return logger


def test_unhedged_exceeded_defers_flatten_before_hedge_deadline(monkeypatch) -> None:
    oms = StrategyOMS(defer_flatten=True)
    logger = _run_strategy_step(monkeypatch, oms)

    assert oms.flatten_calls == []
    assert oms.cancel_reasons == ["unhedged_exceeded_deferred_for_hedge_ticket"]
    assert oms.update_quote_calls == []
    risks = [
        record
        for record in logger.records
        if record.get("reason") == "unhedged_exceeded_deferred_for_hedge_ticket"
        and record.get("event") == "risk"
    ]
    assert risks
    assert risks[-1]["has_open_hedge_ticket"] is True
    assert risks[-1]["hedge_ticket_id"] == "ticket-1"
    assert risks[-1]["hedge_ticket_remain"] == 0.02
    assert risks[-1]["action_taken"] == "defer_flatten_cancel_quotes"


def test_unhedged_exceeded_flattens_after_hedge_deadline(monkeypatch) -> None:
    oms = StrategyOMS(defer_flatten=False)
    logger = _run_strategy_step(monkeypatch, oms)

    assert oms.cancel_reasons == []
    assert oms.flatten_calls == [{"cycle_id": 1, "reason": "unhedged_exceeded"}]
    risks = [record for record in logger.records if record.get("reason") == "unhedged_exceeded"]
    assert risks
    assert risks[-1]["has_open_hedge_ticket"] is True
    assert risks[-1]["action_taken"] == "flatten"


def test_flatten_fails_open_hedge_ticket_and_stops_chase() -> None:
    gateway = OMSGateway()
    orders_logger = CapturingLogger()
    oms = OMS(
        gateway,
        _config(),
        risk=None,
        orders_logger=orders_logger,
        fills_logger=CapturingLogger(),
    )
    now = time.time()
    oms._hedge_tickets["ticket-1"] = HedgeTicket(
        ticket_id="ticket-1",
        symbol="ETHUSDT",
        side=Side.BUY,
        want_qty=0.02,
        filled_qty=0.0,
        created_ts=now - 10.0,
        deadline_ts=now - 5.0,
        tries=1,
        status="OPEN",
        reason="perp_fill",
        perp_fill_ts=now - 10.0,
        perp_fill_price=100.0,
    )

    asyncio.run(oms.flatten(None, cycle_id=10, reason="unhedged_exceeded"))
    asyncio.run(
        oms.process_hedge_tickets(
            SimpleNamespace(bid=100.0, ask=100.2, bid_size=1.0, ask_size=1.0)
        )
    )

    failed = [record for record in orders_logger.records if record.get("reason") == "ticket_failed"]
    assert failed
    assert failed[-1]["fail_reason"] == "flatten_started"
    assert oms.has_open_hedge_ticket() is False
    assert [
        record
        for record in orders_logger.records
        if record.get("reason") == "hedge_chase"
    ] == []
    assert [
        record
        for record in orders_logger.records
        if record.get("event") == "order_new" and record.get("intent") == "HEDGE"
    ] == []
