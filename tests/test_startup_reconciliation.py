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
from bot.exchange.constraints import ConstraintsRegistry, InstrumentConstraints
from bot.exchange.bitget_gateway import _perp_position_from_rows
from bot.oms.oms import OMS
from bot.risk.guards import RiskGuards


class CapturingLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


class DummyGateway:
    def __init__(
        self,
        available: float | None,
        *,
        perp_position: float | None = None,
        spot_min_qty: float = 0.0001,
        spot_min_notional: float = 0.0,
        spot_last_price: float | None = None,
    ) -> None:
        self.available = available
        self.perp_position = perp_position
        self.spot_last_price = spot_last_price
        self.store = SimpleNamespace(positions=SimpleNamespace(find=lambda: []))
        self.constraints = ConstraintsRegistry(
            spot=InstrumentConstraints(
                min_qty=spot_min_qty,
                qty_step=0.0001,
                min_notional=spot_min_notional,
                tick_size=0.01,
            )
        )

    async def get_spot_available_balance(self, base_coin: str) -> float | None:
        assert base_coin == "ETH"
        return self.available

    async def get_spot_last_price(self, symbol: str) -> float | None:
        assert symbol == "ETHUSDT"
        return self.spot_last_price

    async def get_perp_position(self) -> float | None:
        return self.perp_position


def _config(*, dry_run: bool = False) -> AppConfig:
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
            dry_run=dry_run,
        ),
        hedge=HedgeConfig(use_spot_limit_ioc=True, hedge_aggressive_bps=5.0),
        cost=CostConfig(fee_maker_perp_bps=1.4, fee_taker_spot_bps=10.0, slippage_bps=2.0),
    )


def _oms(
    available: float | None,
    *,
    dry_run: bool = False,
    perp_position: float | None = None,
    spot_min_qty: float = 0.0001,
    spot_min_notional: float = 0.0,
    spot_last_price: float | None = None,
) -> tuple[OMS, RiskGuards, CapturingLogger]:
    config = _config(dry_run=dry_run)
    risk = RiskGuards(config.risk)
    logger = CapturingLogger()
    return (
        OMS(
            DummyGateway(
                available,
                perp_position=perp_position,
                spot_min_qty=spot_min_qty,
                spot_min_notional=spot_min_notional,
                spot_last_price=spot_last_price,
            ),
            config,
            risk=risk,
            orders_logger=logger,
            fills_logger=CapturingLogger(),
        ),
        risk,
        logger,
    )


def test_startup_reconciliation_halts_live_when_open_spot_balance_exists() -> None:
    oms, risk, logger = _oms(0.03994, dry_run=False)

    ok = asyncio.run(
        oms.reconcile_startup_spot_balance(tolerance=0.01, dry_run=False)
    )

    assert ok is False
    assert risk.is_halted()
    assert risk.halt_reason == "startup_open_spot_balance_detected"
    assert logger.records[-1]["reason"] == "startup_open_spot_balance_detected"
    assert logger.records[-1]["internal_spot_pos"] == 0.0
    assert logger.records[-1]["actual_spot_available"] == 0.03994
    assert logger.records[-1]["action_taken"] == "halted"


def test_startup_reconciliation_warns_only_in_dry_run() -> None:
    oms, risk, logger = _oms(0.03994, dry_run=True)

    ok = asyncio.run(
        oms.reconcile_startup_spot_balance(tolerance=0.01, dry_run=True)
    )

    assert ok is True
    assert not risk.is_halted()
    assert logger.records[-1]["reason"] == "startup_open_spot_balance_detected"
    assert logger.records[-1]["action_taken"] == "warn_only"


def test_startup_reconciliation_passes_when_actual_matches_internal_within_tolerance() -> None:
    oms, risk, logger = _oms(0.039940000718, dry_run=False)
    oms.positions.spot_pos = 0.04

    ok = asyncio.run(
        oms.reconcile_startup_spot_balance(tolerance=0.01, dry_run=False)
    )

    assert ok is True
    assert not risk.is_halted()
    assert logger.records[-1]["reason"] == "startup_spot_balance_reconciled"
    assert abs(logger.records[-1]["diff"] - -0.000059999282) < 1e-12


def test_startup_reconciliation_ignores_spot_dust_below_min_trade() -> None:
    oms, risk, logger = _oms(0.858, dry_run=False, spot_min_qty=1.0)

    ok = asyncio.run(
        oms.reconcile_startup_spot_balance(tolerance=0.01, dry_run=False)
    )

    assert ok is True
    assert not risk.is_halted()
    assert logger.records[-1]["reason"] == "startup_spot_balance_reconciled"
    assert logger.records[-1]["actual_is_dust"] is True
    assert logger.records[-1]["actual_dust_reason"] == "below_min_qty"


def test_startup_reconciliation_ignores_spot_dust_below_min_notional() -> None:
    oms, risk, logger = _oms(
        0.858,
        dry_run=False,
        spot_min_qty=0.0001,
        spot_min_notional=5.0,
        spot_last_price=0.4,
    )

    ok = asyncio.run(
        oms.reconcile_startup_spot_balance(tolerance=0.01, dry_run=False)
    )

    assert ok is True
    assert not risk.is_halted()
    assert logger.records[-1]["reason"] == "startup_spot_balance_reconciled"
    assert logger.records[-1]["actual_is_dust"] is True
    assert logger.records[-1]["actual_dust_reason"] == "below_min_notional"
    assert logger.records[-1]["spot_dust_price"] == 0.4


def test_positions_sync_uses_rest_fallback_when_ws_store_empty() -> None:
    oms, _, logger = _oms(0.0, dry_run=False, perp_position=-46.0)

    asyncio.run(oms._sync_positions_once(timeout_sec=0.01))

    assert oms.positions.perp_pos == -46.0
    assert logger.records[-1]["reason"] == "positions_rest_fallback"
    assert logger.records[-1]["positions_empty"] is True


def test_empty_rest_position_rows_mean_flat() -> None:
    assert _perp_position_from_rows([], "XRPUSDT") == 0.0
