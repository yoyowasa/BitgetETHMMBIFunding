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
    def __init__(self, *, has_active_quote: bool) -> None:
        now = time.time()
        self.gateway = SimpleNamespace(
            book_ready=True,
            public_book_channel="books5",
            store=object(),
            tfi=0.0,
            last_public_trade={
                "price": 100.0,
                "side": "sell",
                "ts": now,
                "trade_id": "t-1",
            },
            mid_100ms_ago=lambda now=None: None,
        )
        self.positions = SimpleNamespace(spot_pos=0.0, perp_pos=0.0)
        self.unhedged_qty = 0.0
        self.unhedged_since = None
        self.has_active_quote = has_active_quote
        self.cancel_reasons: list[str] = []
        self.last_update_quotes: dict | None = None

    async def process_hedge_tickets(self, spot_bbo) -> None:
        return None

    async def cancel_all(self, reason: str) -> None:
        self.cancel_reasons.append(reason)

    async def flatten(self, spot_bbo, cycle_id: int, reason: str) -> None:
        return None

    def fail_open_tickets(self, reason: str) -> None:
        return None

    async def update_quotes(self, **kwargs) -> None:
        self.last_update_quotes = kwargs

    def active_quote_snapshot(self, symbol: str) -> dict[str, object]:
        active_bid = object() if self.has_active_quote else None
        return {
            "has_active_quote": self.has_active_quote,
            "source": "test",
            "active_bid": active_bid,
            "active_ask": None,
            "active_bid_px": 100.0 if self.has_active_quote else None,
            "active_ask_px": None,
            "active_bid_order_id": "bid-1" if self.has_active_quote else None,
            "active_ask_order_id": None,
            "active_bid_client_oid": "cbid-1" if self.has_active_quote else None,
            "active_ask_client_oid": None,
            "active_bid_qty": 1.0 if self.has_active_quote else None,
            "active_ask_qty": None,
            "active_bid_ts": time.time() if self.has_active_quote else None,
            "active_ask_ts": None,
        }


class DummyRisk:
    def is_halted(self) -> bool:
        return False

    def stale(self, snapshot_ts: float, now: float) -> bool:
        return False

    def in_cooldown(self, now: float) -> bool:
        return False

    def unhedged_exceeded(self, unhedged_notional: float, unhedged_since) -> bool:
        return False


def _config(scope: str | None = None) -> AppConfig:
    kwargs = {}
    if scope is not None:
        kwargs["cancel_aggressive_scope"] = scope
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


def _run(scope: str, has_active_quote: bool, monkeypatch) -> tuple[DummyOMS, DummyLogger]:
    _patch_book(monkeypatch)
    funding_cache = SimpleNamespace(
        last=FundingInfo(
            funding_rate=0.0001,
            next_update_time=None,
            interval_sec=None,
            ts=time.time(),
        )
    )
    oms = DummyOMS(has_active_quote=has_active_quote)
    logger = DummyLogger()
    strategy = MMFundingStrategy(_config(scope), funding_cache, oms, DummyRisk(), logger)

    asyncio.run(strategy.step())

    return oms, logger


def test_cancel_aggressive_scope_default_is_pre_quote_and_active() -> None:
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
    ).cancel_aggressive_scope == "pre_quote_and_active"


def test_cancel_aggressive_scope_loads_active_quote_only(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
exchange:
  name: bitget
  base_url: ""
  ws_public: ""
  ws_private: ""
symbols:
  spot: { instType: "SPOT", symbol: "ETHUSDT" }
  perp: { instType: "USDT-FUTURES", symbol: "ETHUSDT" }
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
  cancel_aggressive_scope: active_quote_only
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

    assert load_config(str(path)).strategy.cancel_aggressive_scope == "active_quote_only"


def test_active_quote_only_without_active_quote_does_not_pre_quote_block(monkeypatch) -> None:
    oms, logger = _run("active_quote_only", has_active_quote=False, monkeypatch=monkeypatch)

    assert oms.cancel_reasons == []
    assert oms.last_update_quotes is not None
    suppress_logs = [
        record
        for record in logger.records
        if record.get("reason") == "cancel_aggressive_scope_suppressed"
    ]
    assert suppress_logs
    assert suppress_logs[0]["has_active_quote"] is False
    assert suppress_logs[0]["cancel_aggressive_scope"] == "active_quote_only"
    pre_quote_logs = [
        record for record in logger.records if record.get("reason") == "pre_quote_decision"
    ]
    assert pre_quote_logs[0]["final_block_reason"] == "none"
    assert pre_quote_logs[0]["cancel_aggressive_scope_suppressed"] is True


def test_pre_quote_and_active_keeps_current_pre_quote_blocker(monkeypatch) -> None:
    oms, logger = _run("pre_quote_and_active", has_active_quote=False, monkeypatch=monkeypatch)

    assert oms.cancel_reasons == ["cancel_aggressive"]
    assert oms.last_update_quotes is None
    pre_quote_logs = [
        record for record in logger.records if record.get("reason") == "pre_quote_decision"
    ]
    assert pre_quote_logs[0]["final_block_reason"] == "cancel_aggressive"
    assert pre_quote_logs[0]["cancel_aggressive_scope"] == "pre_quote_and_active"


def test_active_quote_only_with_active_quote_keeps_cancel_aggressive(monkeypatch) -> None:
    oms, logger = _run("active_quote_only", has_active_quote=True, monkeypatch=monkeypatch)

    assert oms.cancel_reasons == ["cancel_aggressive"]
    assert oms.last_update_quotes is None
    pre_quote_logs = [
        record for record in logger.records if record.get("reason") == "pre_quote_decision"
    ]
    assert pre_quote_logs[0]["final_block_reason"] == "cancel_aggressive"
    assert pre_quote_logs[0]["has_active_quote"] is True
