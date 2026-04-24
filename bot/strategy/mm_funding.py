from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from enum import Enum

from ..config import AppConfig
from ..log.jsonl import JsonlLogger
from ..marketdata import book as book_md
from ..marketdata.funding import FundingCache
from ..oms.oms import OMS
from ..risk.guards import (
    RiskGuards,
    check_aggressive_trade,
    check_fast_mid_move,
    check_tfi_fade,
)
from ..types import InstType


class StrategyState(str, Enum):
    STOPPED = "STOPPED"
    QUOTING = "QUOTING"
    HEDGING = "HEDGING"
    FLATTENING = "FLATTENING"
    COOLDOWN = "COOLDOWN"
    HALTED = "HALTED"


class MMFundingStrategy:
    def __init__(
        self,
        config: AppConfig,
        funding_cache: FundingCache,
        oms: OMS,
        risk: RiskGuards,
        decision_logger: JsonlLogger,
    ):
        self._config = config
        self._funding = funding_cache
        self._oms = oms
        self._risk = risk
        self._decision_logger = decision_logger
        self._state = StrategyState.STOPPED
        self._cycle_id = 0

    async def run(self) -> None:
        interval = self._config.strategy.quote_refresh_ms / 1000.0
        while True:
            await self.step()
            await asyncio.sleep(interval)

    async def step(self) -> None:
        self._cycle_id += 1
        now = time.time()

        if self._risk.is_halted():
            if self._state != StrategyState.HALTED:
                self._state = StrategyState.HALTED
                self._oms.fail_open_tickets("halt")
                await self._oms.cancel_all(reason="halted")
                self._log_decision(
                    now,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "halted",
                )
            return
        if not self._oms.gateway.book_ready:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="book_not_ready")
            self._log_decision(
                now,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "book_not_ready",
            )
            return
        channel = self._oms.gateway.public_book_channel
        spot_snapshot, spot_filtered = book_md.snapshot_from_store(
            self._oms.gateway.store,
            InstType.SPOT,
            self._config.symbols.spot.symbol,
            self._config.strategy.obi_levels,
            channel=channel,
            return_meta=True,
        )
        if spot_snapshot is not None and not spot_filtered:
            self._oms.gateway.note_book_channel_filter_unavailable(
                InstType.SPOT,
                self._config.symbols.spot.symbol,
                channel,
            )
        perp_snapshot, perp_filtered = book_md.snapshot_from_store(
            self._oms.gateway.store,
            InstType.USDT_FUTURES,
            self._config.symbols.perp.symbol,
            self._config.strategy.obi_levels,
            channel=channel,
            return_meta=True,
        )
        if perp_snapshot is not None and not perp_filtered:
            self._oms.gateway.note_book_channel_filter_unavailable(
                InstType.USDT_FUTURES,
                self._config.symbols.perp.symbol,
                channel,
            )
        spot_bbo = book_md.bbo_from_snapshot(spot_snapshot) if spot_snapshot else None
        await self._oms.process_hedge_tickets(spot_bbo)

        action = "idle"
        if spot_snapshot is None or perp_snapshot is None:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="no_book")
            self._log_decision(now, None, None, None, None, None, None, None, action)
            return

        perp_bbo = book_md.bbo_from_snapshot(perp_snapshot)
        if self._risk.stale(spot_snapshot.ts, now) or self._risk.stale(
            perp_snapshot.ts, now
        ):
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="stale_book")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                None,
                None,
                None,
                None,
                None,
                "stale",
            )
            return

        if self._risk.in_cooldown(now):
            self._state = StrategyState.COOLDOWN
            await self._oms.cancel_all(reason="cooldown")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                None,
                None,
                None,
                None,
                None,
                "cooldown",
            )
            return

        funding = self._funding.last
        if funding is None:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="no_funding")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                None,
                None,
                None,
                None,
                None,
                "no_funding",
            )
            return
        if (now - funding.ts) > self._config.risk.funding_stale_sec:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="funding_stale")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                funding.funding_rate,
                None,
                None,
                None,
                None,
                "funding_stale",
            )
            return

        mid_spot = book_md.calc_mid(spot_bbo)
        mid_perp = book_md.calc_mid(perp_bbo)
        micro_price = book_md.calc_microprice(perp_bbo)
        obi_spot = book_md.calc_obi(spot_snapshot)
        obi_perp = book_md.calc_obi(perp_snapshot)
        basis = mid_perp - mid_spot
        tfi = self._oms.gateway.tfi

        if check_fast_mid_move(
            mid_perp,
            self._oms.gateway.mid_100ms_ago(now),
            fade_vol_bps=self._config.strategy.fade_vol_bps,
        ):
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="quote_fade")
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "quote_fade",
                    "leg": "both",
                    "cycle_id": self._cycle_id,
                    "mid_perp": mid_perp,
                    "mid_100ms_ago": self._oms.gateway.mid_100ms_ago(now),
                    "tfi": tfi,
                }
            )
            self._log_decision(
                now, spot_bbo, perp_bbo, funding.funding_rate, basis, obi_spot, obi_perp, None, "quote_fade", tfi
            )
            return

        last_trade = self._oms.gateway.last_public_trade
        aggressive_leg = None
        if last_trade is not None:
            aggressive_leg = check_aggressive_trade(
                float(last_trade["price"]),
                str(last_trade["side"]),
                perp_bbo.bid,
                perp_bbo.ask,
                proximity_bps=self._config.strategy.aggressive_trade_proximity_bps,
            )
        if aggressive_leg is not None:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="cancel_aggressive")
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "cancel_aggressive",
                    "leg": aggressive_leg,
                    "cycle_id": self._cycle_id,
                    "trade_px": last_trade["price"],
                    "trade_side": last_trade["side"],
                    "tfi": tfi,
                }
            )
            self._log_decision(
                now, spot_bbo, perp_bbo, funding.funding_rate, basis, obi_spot, obi_perp, None, "cancel_aggressive", tfi
            )
            return

        target_q = self._config.strategy.target_notional / mid_perp
        target_perp = self._target_perp_inventory(target_q, funding.funding_rate, now)
        spot_pos = self._oms.positions.spot_pos
        perp_pos = self._oms.positions.perp_pos
        delta = spot_pos + perp_pos
        if (
            abs(spot_pos) * mid_spot > self._config.risk.max_position_notional
            or abs(perp_pos) * mid_perp > self._config.risk.max_position_notional
        ):
            self._state = StrategyState.FLATTENING
            await self._oms.flatten(spot_bbo, self._cycle_id, reason="max_position")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                funding.funding_rate,
                basis,
                obi_spot,
                obi_perp,
                target_q,
                "max_position",
                tfi,
            )
            return

        expected_cost_bps = (
            2 * self._config.cost.fee_maker_perp_bps
            + 2 * (self._config.cost.fee_taker_spot_bps + self._config.cost.slippage_bps)
        )
        expected_cost = self._config.strategy.target_notional * (expected_cost_bps / 10000.0)
        expected_funding = self._config.strategy.target_notional * funding.funding_rate
        edge = expected_funding - expected_cost

        if self._config.strategy.enable_only_positive_funding and funding.funding_rate < self._config.strategy.min_funding_rate:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="funding_below_min")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                funding.funding_rate,
                basis,
                obi_spot,
                obi_perp,
                target_q,
                "funding_off",
                tfi,
            )
            return

        if edge <= 0:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="edge_negative")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                funding.funding_rate,
                basis,
                obi_spot,
                obi_perp,
                target_q,
                "edge_negative",
                tfi,
            )
            return

        unhedged_notional = abs(self._oms.unhedged_qty) * mid_spot
        if self._risk.unhedged_exceeded(unhedged_notional, self._oms.unhedged_since):
            self._state = StrategyState.FLATTENING
            await self._oms.flatten(spot_bbo, self._cycle_id, reason="unhedged_exceeded")
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                funding.funding_rate,
                basis,
                obi_spot,
                obi_perp,
                target_q,
                "flatten",
                tfi,
            )
            return

        alpha_px = micro_price * (self._config.strategy.alpha_obi_bps / 10000.0) * obi_perp
        tfi_px = micro_price * (self._config.strategy.k_tfi_bps / 10000.0) * tfi
        inv_ratio = 0.0 if target_q == 0 else (perp_pos - target_perp) / target_q
        gamma_px = micro_price * (self._config.strategy.gamma_inventory_bps / 10000.0) * inv_ratio
        reservation = micro_price + alpha_px + tfi_px - gamma_px

        half_bps = self._config.strategy.base_half_spread_bps
        funding_skew_bps = funding.funding_rate * self._config.strategy.funding_skew_bps_per_rate
        if funding.funding_rate > 0:
            bid_funding_adjust = max(0.0, funding_skew_bps)
            ask_funding_adjust = -max(0.0, funding_skew_bps)
        else:
            bid_funding_adjust = min(0.0, funding_skew_bps)
            ask_funding_adjust = -min(0.0, funding_skew_bps)
        if abs(self._oms.unhedged_qty) > 0 or abs(delta) > self._config.strategy.delta_tolerance:
            half_bps += self._config.strategy.base_half_spread_bps
            self._state = StrategyState.HEDGING
        else:
            self._state = StrategyState.QUOTING
        raw_half_bps = half_bps
        half_bps = max(half_bps, self._config.strategy.min_half_spread_bps)
        if raw_half_bps < self._config.strategy.min_half_spread_bps:
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "spread_below_min",
                    "leg": None,
                    "cycle_id": self._cycle_id,
                    "h_raw": raw_half_bps,
                    "h": half_bps,
                }
            )

        bid_px = reservation * (1 - (half_bps + bid_funding_adjust) / 10000.0)
        ask_px = reservation * (1 + (half_bps + ask_funding_adjust) / 10000.0)
        tfi_fade_leg = check_tfi_fade(
            tfi, threshold=self._config.strategy.tfi_fade_threshold
        )
        if tfi_fade_leg == "ask":
            ask_px *= 1 + self._config.strategy.min_half_spread_bps / 10000.0
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "tfi_fade",
                    "leg": "ask",
                    "cycle_id": self._cycle_id,
                    "tfi": tfi,
                }
            )
        elif tfi_fade_leg == "bid":
            bid_px *= 1 - self._config.strategy.min_half_spread_bps / 10000.0
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "tfi_fade",
                    "leg": "bid",
                    "cycle_id": self._cycle_id,
                    "tfi": tfi,
                }
            )

        base_size = max(target_q, 0.0)
        size_bid = base_size
        size_ask = base_size
        if perp_pos > target_perp:
            size_ask *= 1.2
        elif perp_pos < target_perp:
            size_bid *= 1.2

        await self._oms.update_quotes(
            bid_px=bid_px,
            ask_px=ask_px,
            bid_size=size_bid,
            ask_size=size_ask,
            cycle_id=self._cycle_id,
            reason="quote",
        )
        action = "quote"
        self._log_decision(
            now,
            spot_bbo,
            perp_bbo,
            funding.funding_rate,
            basis,
            obi_spot,
            obi_perp,
            target_q,
            action,
            tfi,
        )

    def _log_decision(
        self,
        ts: float,
        spot_bbo: book_md.BBO | None,
        perp_bbo: book_md.BBO | None,
        funding_rate: float | None,
        basis: float | None,
        obi_spot: float | None,
        obi_perp: float | None,
        target_q: float | None,
        action: str,
        tfi: float | None = None,
    ) -> None:
        intent = "quote" if action == "quote" else None
        self._decision_logger.log(
            {
                "ts": ts,
                "event": "tick",
                "intent": intent,
                "source": "strategy",
                "mode": self._state.value,
                "reason": action,
                "leg": None,
                "cycle_id": self._cycle_id,
                "state": self._state.value,
                "funding_rate": funding_rate,
                "basis": basis,
                "obi_spot": obi_spot,
                "obi_perp": obi_perp,
                "tfi": tfi,
                "mid_spot": None if spot_bbo is None else book_md.calc_mid(spot_bbo),
                "mid_perp": None if perp_bbo is None else book_md.calc_mid(perp_bbo),
                "micro_price": None if perp_bbo is None else book_md.calc_microprice(perp_bbo),
                "target_q": target_q,
                "pos_spot": self._oms.positions.spot_pos,
                "pos_perp": self._oms.positions.perp_pos,
                "delta": self._oms.positions.spot_pos + self._oms.positions.perp_pos,
                "action": action,
                "book_channel": self._oms.gateway.public_book_channel,
            }
        )

    def _target_perp_inventory(
        self, target_q: float, funding_rate: float, now: float
    ) -> float:
        base_target = 0.0
        max_ratio = self._config.strategy.target_inventory_max_ratio
        if funding_rate > 0:
            base_target = -target_q * max_ratio
        elif funding_rate < 0:
            base_target = target_q * max_ratio
        if self._in_funding_window(now):
            return base_target
        return base_target * 0.5

    def _in_funding_window(self, now: float) -> bool:
        dt = datetime.fromtimestamp(now, tz=timezone.utc)
        sec_of_day = dt.hour * 3600 + dt.minute * 60 + dt.second
        for settle_hour in (0, 8, 16):
            settle_sec = settle_hour * 3600
            if abs(sec_of_day - settle_sec) <= self._config.strategy.funding_window_sec:
                return True
        return False
