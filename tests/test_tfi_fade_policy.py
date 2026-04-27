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
    load_config,
)
from bot.strategy.mm_funding import MMFundingStrategy
from bot.types import FundingInfo, InstType


class DummyLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


class DummyOMS:
    def __init__(self, tfi: float) -> None:
        self.gateway = SimpleNamespace(
            book_ready=True,
            public_book_channel="books5",
            store=object(),
            tfi=tfi,
            last_public_trade=None,
            mid_100ms_ago=lambda now=None: None,
        )
        self.positions = SimpleNamespace(spot_pos=0.0, perp_pos=0.0)
        self.unhedged_qty = 0.0
        self.unhedged_since = None
        self.last_update_quotes: dict | None = None

    async def process_hedge_tickets(self, spot_bbo) -> None:
        return None

    async def cancel_all(self, reason: str) -> None:
        return None

    async def flatten(self, spot_bbo, cycle_id: int, reason: str) -> None:
        return None

    def fail_open_tickets(self, reason: str) -> None:
        return None

    async def update_quotes(self, **kwargs) -> None:
        self.last_update_quotes = kwargs


class DummyRisk:
    def is_halted(self) -> bool:
        return False

    def stale(self, snapshot_ts: float, now: float) -> bool:
        return False

    def in_cooldown(self, now: float) -> bool:
        return False

    def unhedged_exceeded(self, unhedged_notional: float, unhedged_since) -> bool:
        return False


def _config(policy: str | None = None) -> AppConfig:
    kwargs = {}
    if policy is not None:
        kwargs["tfi_fade_policy"] = policy
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
            target_notional=500.0,
            delta_tolerance=0.01,
            obi_levels=5,
            alpha_obi_bps=1.0,
            gamma_inventory_bps=2.0,
            base_half_spread_bps=18.0,
            quote_refresh_ms=250,
            adverse_buffer_bps=2.0,
            min_half_spread_bps=18.0,
            dry_run=True,
            **kwargs,
        ),
        hedge=HedgeConfig(use_spot_limit_ioc=True, hedge_aggressive_bps=5.0),
        cost=CostConfig(
            fee_maker_perp_bps=2.0,
            fee_taker_spot_bps=10.0,
            slippage_bps=2.0,
        ),
    )


def _snapshot_from_store(
    store,
    inst_type: InstType,
    symbol: str,
    levels: int,
    channel=None,
    return_meta=False,
):
    snapshot = SimpleNamespace(
        bids=[(100.0, 1.0)],
        asks=[(100.2, 1.0)],
        ts=time.time(),
    )
    return (snapshot, True) if return_meta else snapshot


def _patch_book(monkeypatch) -> None:
    from bot.strategy import mm_funding as module

    monkeypatch.setattr(module.book_md, "snapshot_from_store", _snapshot_from_store)
    monkeypatch.setattr(
        module.book_md,
        "bbo_from_snapshot",
        lambda snapshot: SimpleNamespace(
            bid=100.0,
            ask=100.2,
            bid_size=1.0,
            ask_size=1.0,
            ts=snapshot.ts,
        ),
    )
    monkeypatch.setattr(module.book_md, "calc_mid", lambda bbo: (bbo.bid + bbo.ask) / 2.0)
    monkeypatch.setattr(
        module.book_md,
        "calc_microprice",
        lambda bbo: (bbo.ask * bbo.bid_size + bbo.bid * bbo.ask_size)
        / (bbo.bid_size + bbo.ask_size),
    )
    monkeypatch.setattr(module.book_md, "calc_obi", lambda snapshot: 0.0)


def _run(monkeypatch, *, policy: str, tfi: float) -> tuple[DummyOMS, DummyLogger]:
    _patch_book(monkeypatch)
    funding_cache = SimpleNamespace(
        last=FundingInfo(
            funding_rate=0.0001,
            next_update_time=None,
            interval_sec=None,
            ts=time.time(),
        )
    )
    oms = DummyOMS(tfi=tfi)
    logger = DummyLogger()
    strategy = MMFundingStrategy(_config(policy), funding_cache, oms, DummyRisk(), logger)

    asyncio.run(strategy.step())

    return oms, logger


def _risk_logs(logger: DummyLogger, reason: str) -> list[dict]:
    return [record for record in logger.records if record.get("reason") == reason]


def test_tfi_fade_policy_default_is_current() -> None:
    assert StrategyConfig(
        enable_only_positive_funding=True,
        min_funding_rate=0.0,
        target_notional=500.0,
        delta_tolerance=0.01,
        obi_levels=5,
        alpha_obi_bps=1.0,
        gamma_inventory_bps=2.0,
        base_half_spread_bps=18.0,
        quote_refresh_ms=250,
    ).tfi_fade_policy == "current"


def test_tfi_fade_policy_loads_disabled_and_thresholds(tmp_path) -> None:
    for policy in ("disabled", "threshold_0p7", "threshold_0p8"):
        path = tmp_path / f"{policy}.yaml"
        path.write_text(
            f"""
exchange:
  name: bitget
  base_url: ""
  ws_public: ""
  ws_private: ""
symbols:
  spot: {{ instType: "SPOT", symbol: "ETHUSDT" }}
  perp: {{ instType: "USDT-FUTURES", symbol: "ETHUSDT" }}
risk:
  stale_sec: 2.0
  max_unhedged_sec: 2.0
  max_unhedged_notional: 200
  max_position_notional: 2000
  cooldown_sec: 30
strategy:
  enable_only_positive_funding: true
  min_funding_rate: 0.0
  target_notional: 500
  delta_tolerance: 0.01
  obi_levels: 5
  alpha_obi_bps: 1.0
  gamma_inventory_bps: 2.0
  base_half_spread_bps: 18.0
  quote_refresh_ms: 250
  tfi_fade_policy: {policy}
hedge:
  use_spot_limit_ioc: true
  hedge_aggressive_bps: 5.0
cost:
  fee_maker_perp_bps: 2.0
  fee_taker_spot_bps: 10.0
  slippage_bps: 2.0
""",
            encoding="utf-8",
        )
        assert load_config(str(path)).strategy.tfi_fade_policy == policy


def test_current_keeps_existing_tfi_fade(monkeypatch) -> None:
    oms, logger = _run(monkeypatch, policy="current", tfi=-0.65)

    assert _risk_logs(logger, "tfi_fade")
    assert not _risk_logs(logger, "tfi_fade_suppressed")
    assert oms.last_update_quotes is not None
    assert oms.last_update_quotes["bid_px"] < 99.75


def test_disabled_suppresses_tfi_fade(monkeypatch) -> None:
    oms, logger = _run(monkeypatch, policy="disabled", tfi=-0.9)

    assert not _risk_logs(logger, "tfi_fade")
    suppressed = _risk_logs(logger, "tfi_fade_suppressed")
    assert suppressed
    assert suppressed[0]["tfi_fade_policy"] == "disabled"
    assert oms.last_update_quotes is not None
    assert oms.last_update_quotes["bid_px"] > 99.75


def test_threshold_0p7_suppresses_below_and_passes_at_threshold(monkeypatch) -> None:
    oms_low, logger_low = _run(monkeypatch, policy="threshold_0p7", tfi=-0.65)
    assert _risk_logs(logger_low, "tfi_fade_suppressed")
    assert oms_low.last_update_quotes is not None
    assert oms_low.last_update_quotes["bid_px"] > 99.75

    oms_high, logger_high = _run(monkeypatch, policy="threshold_0p7", tfi=-0.7)
    assert _risk_logs(logger_high, "tfi_fade")
    assert oms_high.last_update_quotes is not None
    assert oms_high.last_update_quotes["bid_px"] < 99.75


def test_threshold_0p8_suppresses_below_and_passes_at_threshold(monkeypatch) -> None:
    oms_low, logger_low = _run(monkeypatch, policy="threshold_0p8", tfi=-0.75)
    assert _risk_logs(logger_low, "tfi_fade_suppressed")
    assert oms_low.last_update_quotes is not None
    assert oms_low.last_update_quotes["bid_px"] > 99.75

    oms_high, logger_high = _run(monkeypatch, policy="threshold_0p8", tfi=-0.8)
    assert _risk_logs(logger_high, "tfi_fade")
    assert oms_high.last_update_quotes is not None
    assert oms_high.last_update_quotes["bid_px"] < 99.75
