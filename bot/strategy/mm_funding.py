from __future__ import annotations

import asyncio
import time
from enum import Enum

from ..config import AppConfig
from ..log.jsonl import JsonlLogger
from ..marketdata import book as book_md
from ..marketdata.funding import FundingCache
from ..oms.oms import OMS
from ..risk.guards import RiskGuards
from ..types import InstType


class StrategyState(str, Enum):
    STOPPED = "STOPPED"
    QUOTING = "QUOTING"
    HEDGING = "HEDGING"
    FLATTENING = "FLATTENING"
    COOLDOWN = "COOLDOWN"


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
        spot_snapshot = book_md.snapshot_from_store(
            self._oms.gateway.store,
            InstType.SPOT,
            self._config.symbols.spot.symbol,
            self._config.strategy.obi_levels,
        )
        perp_snapshot = book_md.snapshot_from_store(
            self._oms.gateway.store,
            InstType.USDT_FUTURES,
            self._config.symbols.perp.symbol,
            self._config.strategy.obi_levels,
        )

        action = "idle"
        if spot_snapshot is None or perp_snapshot is None:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="no_book")
            self._log_decision(now, None, None, None, None, None, None, None, action)
            return

        spot_bbo = book_md.bbo_from_snapshot(spot_snapshot)
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

        mid_spot = book_md.calc_mid(spot_bbo)
        mid_perp = book_md.calc_mid(perp_bbo)
        obi_spot = book_md.calc_obi(spot_snapshot)
        obi_perp = book_md.calc_obi(perp_snapshot)
        basis = mid_perp - mid_spot

        target_q = self._config.strategy.target_notional / mid_perp
        target_perp = -target_q
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
            )
            return

        alpha_px = mid_perp * (self._config.strategy.alpha_obi_bps / 10000.0) * obi_perp
        inv_ratio = 0.0 if target_q == 0 else (perp_pos - target_perp) / target_q
        gamma_px = mid_perp * (self._config.strategy.gamma_inventory_bps / 10000.0) * inv_ratio
        reservation = mid_perp + alpha_px - gamma_px

        half_bps = self._config.strategy.base_half_spread_bps
        if abs(self._oms.unhedged_qty) > 0 or abs(delta) > self._config.strategy.delta_tolerance:
            half_bps += self._config.strategy.base_half_spread_bps
            self._state = StrategyState.HEDGING
        else:
            self._state = StrategyState.QUOTING

        bid_px = reservation * (1 - half_bps / 10000.0)
        ask_px = reservation * (1 + half_bps / 10000.0)

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
    ) -> None:
        self._decision_logger.log(
            {
                "ts": ts,
                "cycle_id": self._cycle_id,
                "state": self._state.value,
                "funding_rate": funding_rate,
                "basis": basis,
                "obi_spot": obi_spot,
                "obi_perp": obi_perp,
                "mid_spot": None if spot_bbo is None else book_md.calc_mid(spot_bbo),
                "mid_perp": None if perp_bbo is None else book_md.calc_mid(perp_bbo),
                "target_q": target_q,
                "pos_spot": self._oms.positions.spot_pos,
                "pos_perp": self._oms.positions.perp_pos,
                "delta": self._oms.positions.spot_pos + self._oms.positions.perp_pos,
                "action": action,
            }
        )
