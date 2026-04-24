from __future__ import annotations

import time
from types import SimpleNamespace

from bot.config import AppConfig, CostConfig, ExchangeConfig, HedgeConfig, RiskConfig, StrategyConfig, SymbolConfig, SymbolsConfig
from bot.strategy.mm_funding import MMFundingStrategy
from bot.types import FundingInfo, InstType


class DummyLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


class DummyOMS:
    def __init__(self) -> None:
        self.gateway = SimpleNamespace(
            book_ready=True,
            public_book_channel="books5",
            store=object(),
            tfi=0.8,
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


def _config() -> AppConfig:
    return AppConfig(
        exchange=ExchangeConfig(name="bitget", base_url="", ws_public="", ws_private=""),
        symbols=SymbolsConfig(
            spot=SymbolConfig(instType="SPOT", symbol="ETHUSDT"),
            perp=SymbolConfig(instType="USDT-FUTURES", symbol="ETHUSDT", productType="USDT-FUTURES", marginMode="isolated", marginCoin="USDT"),
        ),
        risk=RiskConfig(stale_sec=2.0, max_unhedged_sec=2.0, max_unhedged_notional=200.0, max_position_notional=2000.0, cooldown_sec=30.0, funding_stale_sec=120.0),
        strategy=StrategyConfig(
            enable_only_positive_funding=True,
            min_funding_rate=0.00002,
            target_notional=500.0,
            delta_tolerance=0.01,
            obi_levels=5,
            alpha_obi_bps=1.0,
            gamma_inventory_bps=2.0,
            base_half_spread_bps=8.0,
            quote_refresh_ms=250,
            k_tfi_bps=2.0,
            funding_skew_bps_per_rate=1000.0,
            target_inventory_max_ratio=1.0,
            funding_window_sec=300.0,
            min_half_spread_bps=8.0,
            dry_run=True,
        ),
        hedge=HedgeConfig(use_spot_limit_ioc=True, hedge_aggressive_bps=5.0),
        cost=CostConfig(fee_maker_perp_bps=2.0, fee_taker_spot_bps=10.0, slippage_bps=2.0),
    )


def _snapshot_from_store(store, inst_type: InstType, symbol: str, levels: int, channel=None, return_meta=False):
    if inst_type == InstType.USDT_FUTURES:
        snapshot = SimpleNamespace(bids=[(100.0, 3.0)], asks=[(100.2, 1.0)], ts=time.time())
    else:
        snapshot = SimpleNamespace(bids=[(100.0, 1.0)], asks=[(100.2, 1.0)], ts=time.time())
    return (snapshot, True) if return_meta else snapshot


def test_phase_d_microprice_and_tfi(monkeypatch) -> None:
    from bot.strategy import mm_funding as module

    monkeypatch.setattr(module.book_md, "snapshot_from_store", _snapshot_from_store)
    monkeypatch.setattr(module.book_md, "bbo_from_snapshot", lambda snapshot: SimpleNamespace(bid=snapshot.bids[0][0], ask=snapshot.asks[0][0], bid_size=snapshot.bids[0][1], ask_size=snapshot.asks[0][1], ts=snapshot.ts))
    monkeypatch.setattr(module.book_md, "calc_mid", lambda bbo: (bbo.bid + bbo.ask) / 2.0)
    monkeypatch.setattr(module.book_md, "calc_microprice", lambda bbo: (bbo.ask * bbo.bid_size + bbo.bid * bbo.ask_size) / (bbo.bid_size + bbo.ask_size))
    monkeypatch.setattr(module.book_md, "calc_obi", lambda snapshot: 0.0)

    funding_cache = SimpleNamespace(last=FundingInfo(funding_rate=0.01, next_update_time=None, interval_sec=None, ts=time.time()))
    oms = DummyOMS()
    logger = DummyLogger()
    strategy = MMFundingStrategy(_config(), funding_cache, oms, DummyRisk(), logger)

    import asyncio
    asyncio.run(strategy.step())

    assert oms.last_update_quotes is not None
    assert oms.last_update_quotes["ask_px"] > oms.last_update_quotes["bid_px"]
    assert any("micro_price" in record for record in logger.records if record.get("event") == "tick")
