from __future__ import annotations

import asyncio

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
from bot.risk.guards import RiskGuards


class CapturingLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


class DummyGateway:
    def __init__(self, available: float | None) -> None:
        self.available = available

    async def get_spot_available_balance(self, base_coin: str) -> float | None:
        assert base_coin == "ETH"
        return self.available


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


def _oms(available: float | None, *, dry_run: bool = False) -> tuple[OMS, RiskGuards, CapturingLogger]:
    config = _config(dry_run=dry_run)
    risk = RiskGuards(config.risk)
    logger = CapturingLogger()
    return (
        OMS(
            DummyGateway(available),
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
