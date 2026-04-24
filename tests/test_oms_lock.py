from __future__ import annotations

import asyncio

from bot.config import AppConfig, CostConfig, ExchangeConfig, HedgeConfig, RiskConfig, StrategyConfig, SymbolConfig, SymbolsConfig
from bot.oms.oms import OMS
from bot.types import InstType, OrderIntent, OrderRequest


class DummyLogger:
    def log(self, record: dict) -> None:
        return None


class DummyConstraintsManager:
    def ready(self) -> bool:
        return True

    def get(self, inst_type: InstType):
        return DummyInstrumentConstraints()


class DummyInstrumentConstraints:
    tick_size = 0.1
    qty_step = 0.01
    min_qty = 0.01

    def is_ready(self) -> bool:
        return True

    def adjust_price(self, price: float) -> float:
        return price

    def adjust_qty(self, qty: float) -> float:
        return qty

    def validate(self, price: float, qty: float) -> bool:
        return True


class DummyGateway:
    def __init__(self) -> None:
        self.constraints = DummyConstraintsManager()


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
            min_funding_rate=0.00002,
            target_notional=500.0,
            delta_tolerance=0.01,
            obi_levels=5,
            alpha_obi_bps=1.0,
            gamma_inventory_bps=2.0,
            base_half_spread_bps=8.0,
            min_half_spread_bps=8.0,
            quote_refresh_ms=250,
            dry_run=True,
        ),
        hedge=HedgeConfig(use_spot_limit_ioc=True, hedge_aggressive_bps=5.0),
        cost=CostConfig(fee_maker_perp_bps=2.0, fee_taker_spot_bps=10.0, slippage_bps=2.0),
    )


def test_symbol_lock_serializes_same_symbol() -> None:
    oms = OMS(DummyGateway(), _config(), risk=None, orders_logger=DummyLogger(), fills_logger=DummyLogger())
    enter_order: list[str] = []
    release = asyncio.Event()

    async def fake_submit(req: OrderRequest, reason: str):
        enter_order.append(req.intent.value)
        if req.intent == OrderIntent.FLATTEN:
            await release.wait()
        return "oid"

    async def runner() -> None:
        oms._submit_order = fake_submit  # type: ignore[method-assign]
        oms._positions.perp_pos = 1.0
        task_flatten = asyncio.create_task(oms.flatten(None, 1, "test"))
        await asyncio.sleep(0)
        task_quote = asyncio.create_task(
            oms.update_quotes(
                bid_px=100.0,
                ask_px=100.2,
                bid_size=1.0,
                ask_size=1.0,
                cycle_id=2,
                reason="test",
            )
        )
        await asyncio.sleep(0.05)
        assert enter_order == [OrderIntent.FLATTEN.value]
        release.set()
        await asyncio.gather(task_flatten, task_quote)

    asyncio.run(runner())
