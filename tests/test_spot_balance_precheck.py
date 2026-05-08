from __future__ import annotations

import asyncio
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
from bot.oms.oms import OMS
from bot.types import InstType, OrderRequest


class CapturingLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


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
        return qty >= self.min_qty and price > 0


class DummyGateway:
    def __init__(self, available: float | None) -> None:
        self.constraints = DummyConstraintsManager()
        self.available = available
        self.orders: list[OrderRequest] = []

    async def get_spot_available_balance(self, base_coin: str) -> float | None:
        assert base_coin == "ETH"
        return self.available

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
            max_unhedged_notional=200.0,
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
        hedge=HedgeConfig(use_spot_limit_ioc=True, hedge_aggressive_bps=5.0),
        cost=CostConfig(fee_maker_perp_bps=1.4, fee_taker_spot_bps=10.0, slippage_bps=2.0),
    )


def _run_flatten(available: float | None) -> tuple[DummyGateway, CapturingLogger]:
    gateway = DummyGateway(available)
    orders_logger = CapturingLogger()
    oms = OMS(
        gateway,
        _config(),
        risk=None,
        orders_logger=orders_logger,
        fills_logger=CapturingLogger(),
    )
    oms.positions.spot_pos = 0.04
    bbo = SimpleNamespace(bid=2315.0, ask=2315.1)
    asyncio.run(oms.flatten(bbo, cycle_id=123, reason="unhedged_exceeded"))
    return gateway, orders_logger


def _records_by_event(logger: CapturingLogger, event: str) -> list[dict]:
    return [record for record in logger.records if record.get("event") == event]


def test_spot_flatten_precheck_blocks_when_available_zero() -> None:
    gateway, logger = _run_flatten(0.0)

    assert gateway.orders == []
    assert _records_by_event(logger, "order_new") == []
    skips = _records_by_event(logger, "order_skip")
    assert skips[-1]["reason"] == "spot_flatten_insufficient_available_precheck"
    assert skips[-1]["sell_size"] == 0.04
    assert skips[-1]["spot_available"] == 0.0


def test_spot_flatten_precheck_blocks_when_available_below_sell_size() -> None:
    gateway, logger = _run_flatten(0.02)

    assert gateway.orders == []
    assert _records_by_event(logger, "order_new") == []
    skips = _records_by_event(logger, "order_skip")
    assert skips[-1]["reason"] == "spot_flatten_insufficient_available_precheck"
    assert skips[-1]["sell_size"] == 0.04
    assert skips[-1]["spot_available"] == 0.02


def test_spot_flatten_precheck_allows_when_available_covers_sell_size() -> None:
    gateway, logger = _run_flatten(0.05)

    assert len(gateway.orders) == 1
    assert gateway.orders[0].inst_type == InstType.SPOT
    assert gateway.orders[0].size == 0.04
    assert _records_by_event(logger, "order_new")[-1]["intent"] == "FLATTEN"
    assert _records_by_event(logger, "order_skip") == []


def test_spot_flatten_precheck_warns_and_preserves_existing_behavior_when_unavailable() -> None:
    gateway, logger = _run_flatten(None)

    assert len(gateway.orders) == 1
    warnings = [
        record
        for record in logger.records
        if record.get("reason") == "spot_flatten_available_precheck_unavailable"
    ]
    assert warnings
    assert warnings[-1]["spot_available"] is None
    assert _records_by_event(logger, "order_new")[-1]["intent"] == "FLATTEN"
