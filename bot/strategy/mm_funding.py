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


def _as_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
        self._last_quote_fade_ts: float | None = None
        self._last_open_delta_alert_ts = 0.0
        self._last_open_delta_alert_key: tuple[float, float] | None = None

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
        pre_quote_edge = self._pre_quote_edge_fields(funding.funding_rate)
        mid_100ms_ago = self._oms.gateway.mid_100ms_ago(now)
        mid_move_bps = self._mid_move_bps(mid_perp, mid_100ms_ago)

        if check_fast_mid_move(
            mid_perp,
            mid_100ms_ago,
            fade_vol_bps=self._config.strategy.fade_vol_bps,
        ):
            quote_fade_policy = self._config.strategy.quote_fade_policy
            quote_fade_enabled, threshold_used = self._quote_fade_policy_enabled(
                quote_fade_policy,
                mid_move_bps,
            )
            spread_bps = (perp_bbo.ask - perp_bbo.bid) / mid_perp * 10000.0
            if not quote_fade_enabled:
                self._decision_logger.log(
                    {
                        "ts": now,
                        "event": "risk",
                        "intent": "quote",
                        "source": "strategy",
                        "mode": self._state.value,
                        "reason": "quote_fade_suppressed",
                        "leg": "both",
                        "cycle_id": self._cycle_id,
                        "quote_fade_policy": quote_fade_policy,
                        "policy_enabled": False,
                        "mid_move_bps": mid_move_bps,
                        "threshold_used": threshold_used,
                        "mid_perp": mid_perp,
                        "mid_100ms_ago": mid_100ms_ago,
                        "tfi": tfi,
                        "spread_bps": spread_bps,
                    }
                )
            else:
                self._log_pre_quote_decision(
                    now,
                    final_block_reason="quote_fade",
                    expected_edge_fields=pre_quote_edge,
                    book_stale=False,
                    funding_stale=False,
                    quote_fade_triggered=True,
                    cancel_aggressive_triggered=False,
                    tfi_fade_triggered=False,
                    one_sided_suppressed_bid=False,
                    one_sided_suppressed_ask=False,
                    final_should_quote_bid=False,
                    final_should_quote_ask=False,
                )
                self._last_quote_fade_ts = now
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
                        "quote_fade_policy": quote_fade_policy,
                        "policy_enabled": True,
                        "mid_move_bps": mid_move_bps,
                        "threshold_used": threshold_used,
                        "mid_perp": mid_perp,
                        "mid_100ms_ago": mid_100ms_ago,
                        "tfi": tfi,
                        "spread_bps": spread_bps,
                    }
                )
                self._log_decision(
                    now,
                    spot_bbo,
                    perp_bbo,
                    funding.funding_rate,
                    basis,
                    obi_spot,
                    obi_perp,
                    None,
                    "quote_fade",
                    tfi,
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
            spread_bps = (perp_bbo.ask - perp_bbo.bid) / mid_perp * 10000.0
            diagnostic = self._cancel_aggressive_diagnostic(
                now,
                last_trade,
                perp_bbo.bid,
                perp_bbo.ask,
                aggressive_leg,
            )
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "cancel_aggressive_diagnostic",
                    "leg": aggressive_leg,
                    "cycle_id": self._cycle_id,
                    "mid_perp": mid_perp,
                    "bid_px": perp_bbo.bid,
                    "ask_px": perp_bbo.ask,
                    "spread_bps": spread_bps,
                    "tfi": tfi,
                    **diagnostic,
                }
            )
            policy = self._config.strategy.cancel_aggressive_policy
            scope = self._config.strategy.cancel_aggressive_scope
            quality_filter = self._config.strategy.cancel_aggressive_quality_filter
            last_quote_fade_age_ms = (
                None
                if self._last_quote_fade_ts is None
                else max(0.0, (now - self._last_quote_fade_ts) * 1000.0)
            )
            quote_fade_recent = (
                last_quote_fade_age_ms is not None and last_quote_fade_age_ms <= 1000.0
            )
            strong_tfi = abs(tfi) >= 0.7
            if policy == "current":
                policy_enabled = True
            elif policy == "overlap_quote_fade_only":
                policy_enabled = quote_fade_recent
            elif policy == "overlap_or_strong_tfi":
                policy_enabled = quote_fade_recent or strong_tfi
            else:
                policy_enabled = True

            scope_suppressed = (
                scope == "active_quote_only" and not diagnostic["has_active_quote"]
            )
            quality_filter_pass = self._cancel_aggressive_quality_filter_pass(
                quality_filter,
                diagnostic,
                aggressive_leg,
            )
            if not policy_enabled:
                self._decision_logger.log(
                    {
                        "ts": now,
                        "event": "risk",
                        "intent": "quote",
                        "source": "strategy",
                        "mode": self._state.value,
                        "reason": "cancel_aggressive_suppressed",
                        "leg": aggressive_leg,
                        "cycle_id": self._cycle_id,
                        "cancel_aggressive_policy": policy,
                        "cancel_aggressive_scope": scope,
                        "cancel_aggressive_quality_filter": quality_filter,
                        "policy_enabled": False,
                        "cancel_aggressive_scope_suppressed": False,
                        "quality_filter_pass": quality_filter_pass,
                        "last_quote_fade_age_ms": last_quote_fade_age_ms,
                        "mid_perp": mid_perp,
                        "bid_px": perp_bbo.bid,
                        "ask_px": perp_bbo.ask,
                        "spread_bps": spread_bps,
                        "trade_px": last_trade["price"],
                        "trade_side": last_trade["side"],
                        "tfi": tfi,
                        **diagnostic,
                    }
                )
            elif scope_suppressed:
                self._log_pre_quote_decision(
                    now,
                    final_block_reason="none",
                    expected_edge_fields=pre_quote_edge,
                    book_stale=False,
                    funding_stale=False,
                    quote_fade_triggered=False,
                    cancel_aggressive_triggered=True,
                    cancel_aggressive_scope_suppressed=True,
                    tfi_fade_triggered=False,
                    one_sided_suppressed_bid=False,
                    one_sided_suppressed_ask=False,
                    final_should_quote_bid=True,
                    final_should_quote_ask=True,
                )
                self._decision_logger.log(
                    {
                        "ts": now,
                        "event": "risk",
                        "intent": "quote",
                        "source": "strategy",
                        "mode": self._state.value,
                        "reason": "cancel_aggressive_scope_suppressed",
                        "leg": aggressive_leg,
                        "cycle_id": self._cycle_id,
                        "cancel_aggressive_policy": policy,
                        "cancel_aggressive_scope": scope,
                        "cancel_aggressive_quality_filter": quality_filter,
                        "policy_enabled": True,
                        "cancel_aggressive_scope_suppressed": True,
                        "quality_filter_pass": quality_filter_pass,
                        "last_quote_fade_age_ms": last_quote_fade_age_ms,
                        "mid_perp": mid_perp,
                        "bid_px": perp_bbo.bid,
                        "ask_px": perp_bbo.ask,
                        "spread_bps": spread_bps,
                        "trade_px": last_trade["price"],
                        "trade_side": last_trade["side"],
                        "tfi": tfi,
                        **diagnostic,
                    }
                )
            elif not quality_filter_pass:
                self._log_pre_quote_decision(
                    now,
                    final_block_reason="none",
                    expected_edge_fields=pre_quote_edge,
                    book_stale=False,
                    funding_stale=False,
                    quote_fade_triggered=False,
                    cancel_aggressive_triggered=True,
                    cancel_aggressive_scope_suppressed=False,
                    cancel_aggressive_quality_suppressed=True,
                    tfi_fade_triggered=False,
                    one_sided_suppressed_bid=False,
                    one_sided_suppressed_ask=False,
                    final_should_quote_bid=True,
                    final_should_quote_ask=True,
                )
                self._decision_logger.log(
                    {
                        "ts": now,
                        "event": "risk",
                        "intent": "quote",
                        "source": "strategy",
                        "mode": self._state.value,
                        "reason": "cancel_aggressive_quality_suppressed",
                        "leg": aggressive_leg,
                        "cycle_id": self._cycle_id,
                        "cancel_aggressive_policy": policy,
                        "cancel_aggressive_scope": scope,
                        "cancel_aggressive_quality_filter": quality_filter,
                        "policy_enabled": True,
                        "cancel_aggressive_scope_suppressed": False,
                        "quality_filter_pass": False,
                        "quote_fade_nearby": quote_fade_recent,
                        "last_quote_fade_age_ms": last_quote_fade_age_ms,
                        "mid_perp": mid_perp,
                        "bid_px": perp_bbo.bid,
                        "ask_px": perp_bbo.ask,
                        "spread_bps": spread_bps,
                        "trade_px": last_trade["price"],
                        "trade_side": last_trade["side"],
                        "danger_direction_match": self._cancel_aggressive_danger_match(
                            aggressive_leg,
                            last_trade["side"],
                        ),
                        "tfi": tfi,
                        **diagnostic,
                    }
                )
            else:
                self._log_pre_quote_decision(
                    now,
                    final_block_reason="cancel_aggressive",
                    expected_edge_fields=pre_quote_edge,
                    book_stale=False,
                    funding_stale=False,
                    quote_fade_triggered=False,
                    cancel_aggressive_triggered=True,
                    cancel_aggressive_scope_suppressed=False,
                    cancel_aggressive_quality_suppressed=False,
                    tfi_fade_triggered=False,
                    one_sided_suppressed_bid=False,
                    one_sided_suppressed_ask=False,
                    final_should_quote_bid=False,
                    final_should_quote_ask=False,
                )
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
                        "cancel_aggressive_policy": policy,
                        "cancel_aggressive_scope": scope,
                        "cancel_aggressive_quality_filter": quality_filter,
                        "policy_enabled": True,
                        "cancel_aggressive_scope_suppressed": False,
                        "quality_filter_pass": True,
                        "last_quote_fade_age_ms": last_quote_fade_age_ms,
                        "mid_perp": mid_perp,
                        "bid_px": perp_bbo.bid,
                        "ask_px": perp_bbo.ask,
                        "spread_bps": spread_bps,
                        "trade_px": last_trade["price"],
                        "trade_side": last_trade["side"],
                        "danger_direction_match": self._cancel_aggressive_danger_match(
                            aggressive_leg,
                            last_trade["side"],
                        ),
                        "tfi": tfi,
                        **diagnostic,
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

        if self._config.strategy.enable_only_positive_funding and funding.funding_rate < self._config.strategy.min_funding_rate:
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="funding_below_min")
            self.check_open_delta_while_stopped(
                now=now,
                funding_rate=funding.funding_rate,
                reason="funding_off",
            )
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

        unhedged_notional = abs(self._oms.unhedged_qty) * mid_spot
        if self._risk.unhedged_exceeded(unhedged_notional, self._oms.unhedged_since):
            hedge_ticket = self._oms.open_hedge_ticket_snapshot(now=now)
            if self._oms.should_defer_flatten_for_hedge_ticket(now=now):
                self._state = StrategyState.HEDGING
                await self._oms.cancel_all(reason="unhedged_exceeded_deferred_for_hedge_ticket")
                self._log_unhedged_exceeded(
                    now=now,
                    unhedged_notional=unhedged_notional,
                    hedge_ticket=hedge_ticket,
                    action_taken="defer_flatten_cancel_quotes",
                    reason="unhedged_exceeded_deferred_for_hedge_ticket",
                    spot_pos=spot_pos,
                    perp_pos=perp_pos,
                    delta=delta,
                )
                self._log_decision(
                    now,
                    spot_bbo,
                    perp_bbo,
                    funding.funding_rate,
                    basis,
                    obi_spot,
                    obi_perp,
                    target_q,
                    "unhedged_exceeded_deferred_for_hedge_ticket",
                    tfi,
                )
                return
            self._log_unhedged_exceeded(
                now=now,
                unhedged_notional=unhedged_notional,
                hedge_ticket=hedge_ticket,
                action_taken="flatten",
                reason="unhedged_exceeded",
                spot_pos=spot_pos,
                perp_pos=perp_pos,
                delta=delta,
            )
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

        cost_bps = (
            2 * self._config.cost.fee_maker_perp_bps
            + 2 * (self._config.cost.fee_taker_spot_bps + self._config.cost.slippage_bps)
        )
        expected_spread_bps = 2 * half_bps
        funding_bps = funding.funding_rate * 10000.0
        adverse_buffer_bps = self._config.strategy.adverse_buffer_bps
        expected_edge_bps = (
            expected_spread_bps
            + funding_bps
            - cost_bps
            - adverse_buffer_bps
        )
        expected_edge_usdt = (
            self._config.strategy.target_notional * expected_edge_bps / 10000.0
        )
        if expected_edge_bps < 0:
            self._log_pre_quote_decision(
                now,
                final_block_reason="edge_negative_total",
                expected_edge_fields={
                    "expected_edge_bps": expected_edge_bps,
                    "expected_spread_bps": expected_spread_bps,
                    "funding_bps": funding_bps,
                    "cost_bps": cost_bps,
                    "adverse_buffer_bps": adverse_buffer_bps,
                },
                book_stale=False,
                funding_stale=False,
                quote_fade_triggered=False,
                cancel_aggressive_triggered=False,
                tfi_fade_triggered=False,
                one_sided_suppressed_bid=False,
                one_sided_suppressed_ask=False,
                final_should_quote_bid=False,
                final_should_quote_ask=False,
            )
            self._state = StrategyState.STOPPED
            await self._oms.cancel_all(reason="edge_negative_total")
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "edge_negative_total",
                    "leg": None,
                    "cycle_id": self._cycle_id,
                    "expected_spread_bps": expected_spread_bps,
                    "funding_bps": funding_bps,
                    "cost_bps": cost_bps,
                    "adverse_buffer_bps": adverse_buffer_bps,
                    "expected_edge_bps": expected_edge_bps,
                    "expected_edge_usdt": expected_edge_usdt,
                }
            )
            self._log_decision(
                now,
                spot_bbo,
                perp_bbo,
                funding.funding_rate,
                basis,
                obi_spot,
                obi_perp,
                target_q,
                "edge_negative_total",
                tfi,
            )
            return

        bid_px = reservation * (1 - (half_bps + bid_funding_adjust) / 10000.0)
        ask_px = reservation * (1 + (half_bps + ask_funding_adjust) / 10000.0)
        tfi_fade_leg = check_tfi_fade(
            tfi, threshold=self._config.strategy.tfi_fade_threshold
        )
        tfi_fade_policy = self._config.strategy.tfi_fade_policy
        tfi_policy_enabled, tfi_threshold_used = self._tfi_fade_policy_enabled(
            tfi_fade_policy,
            tfi,
        )
        if tfi_fade_leg is not None and not tfi_policy_enabled:
            spread_bps = (perp_bbo.ask - perp_bbo.bid) / mid_perp * 10000.0
            self._decision_logger.log(
                {
                    "ts": now,
                    "event": "risk",
                    "intent": "quote",
                    "source": "strategy",
                    "mode": self._state.value,
                    "reason": "tfi_fade_suppressed",
                    "leg": tfi_fade_leg,
                    "cycle_id": self._cycle_id,
                    "tfi_fade_policy": tfi_fade_policy,
                    "policy_enabled": False,
                    "tfi": tfi,
                    "threshold_used": tfi_threshold_used,
                    "mid_perp": mid_perp,
                    "bid_px": perp_bbo.bid,
                    "ask_px": perp_bbo.ask,
                    "spread_bps": spread_bps,
                }
            )
            tfi_fade_leg = None
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
                    "tfi_fade_policy": tfi_fade_policy,
                    "policy_enabled": True,
                    "tfi": tfi,
                    "threshold_used": tfi_threshold_used,
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
                    "tfi_fade_policy": tfi_fade_policy,
                    "policy_enabled": True,
                    "tfi": tfi,
                    "threshold_used": tfi_threshold_used,
                }
            )

        base_size = max(target_q, 0.0)
        size_bid = base_size
        size_ask = base_size
        if perp_pos > target_perp:
            size_ask *= 1.2
        elif perp_pos < target_perp:
            size_bid *= 1.2

        one_sided_policy = self._config.strategy.one_sided_quote_policy
        suppress_bid, suppress_ask = self._one_sided_quote_suppression(
            one_sided_policy, tfi
        )
        spread_bps = (perp_bbo.ask - perp_bbo.bid) / mid_perp * 10000.0
        if suppress_bid:
            size_bid = 0.0
            self._log_one_sided_quote_suppressed(
                now,
                one_sided_policy,
                "bid",
                tfi,
                mid_perp,
                perp_bbo.bid,
                perp_bbo.ask,
                spread_bps,
            )
        if suppress_ask:
            size_ask = 0.0
            self._log_one_sided_quote_suppressed(
                now,
                one_sided_policy,
                "ask",
                tfi,
                mid_perp,
                perp_bbo.bid,
                perp_bbo.ask,
                spread_bps,
            )

        self._log_pre_quote_decision(
            now,
            final_block_reason=(
                "one_sided_quote_suppressed"
                if size_bid <= 0 and size_ask <= 0
                else "none"
            ),
            expected_edge_fields={
                "expected_edge_bps": expected_edge_bps,
                "expected_spread_bps": expected_spread_bps,
                "funding_bps": funding_bps,
                "cost_bps": cost_bps,
                "adverse_buffer_bps": adverse_buffer_bps,
            },
            book_stale=False,
            funding_stale=False,
            quote_fade_triggered=False,
            cancel_aggressive_triggered=False,
            tfi_fade_triggered=tfi_fade_leg is not None,
            one_sided_suppressed_bid=suppress_bid,
            one_sided_suppressed_ask=suppress_ask,
            final_should_quote_bid=size_bid > 0,
            final_should_quote_ask=size_ask > 0,
        )
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

    def _log_unhedged_exceeded(
        self,
        *,
        now: float,
        unhedged_notional: float,
        hedge_ticket,
        action_taken: str,
        reason: str,
        spot_pos: float,
        perp_pos: float,
        delta: float,
    ) -> None:
        self._decision_logger.log(
            {
                "ts": now,
                "event": "risk",
                "intent": "RISK",
                "source": "strategy",
                "mode": self._state.value,
                "reason": reason,
                "leg": "both",
                "cycle_id": self._cycle_id,
                "unhedged_qty": self._oms.unhedged_qty,
                "unhedged_notional": unhedged_notional,
                "unhedged_since": self._oms.unhedged_since,
                "has_open_hedge_ticket": hedge_ticket is not None,
                "hedge_ticket_id": None if hedge_ticket is None else hedge_ticket.ticket_id,
                "hedge_ticket_remain": None if hedge_ticket is None else hedge_ticket.remain,
                "hedge_ticket_deadline_ts": None if hedge_ticket is None else hedge_ticket.deadline_ts,
                "hedge_ticket_tries": None if hedge_ticket is None else hedge_ticket.tries,
                "action_taken": action_taken,
                "spot_pos": spot_pos,
                "perp_pos": perp_pos,
                "delta": delta,
            }
        )

    def check_open_delta_while_stopped(
        self,
        *,
        now: float,
        funding_rate: float | None,
        reason: str,
    ) -> bool:
        spot_pos = self._oms.positions.spot_pos
        perp_pos = self._oms.positions.perp_pos
        delta = spot_pos + perp_pos
        if abs(delta) <= self._config.strategy.delta_tolerance:
            return False
        self.log_open_delta_alert(
            now=now,
            spot_pos=spot_pos,
            perp_pos=perp_pos,
            delta=delta,
            funding_rate=funding_rate,
            reason=reason,
        )
        return True

    def log_open_delta_alert(
        self,
        *,
        now: float,
        spot_pos: float,
        perp_pos: float,
        delta: float,
        funding_rate: float | None,
        reason: str,
    ) -> None:
        alert_key = (round(spot_pos, 12), round(perp_pos, 12))
        if (
            self._last_open_delta_alert_key == alert_key
            and now - self._last_open_delta_alert_ts < 60.0
        ):
            return
        self._last_open_delta_alert_key = alert_key
        self._last_open_delta_alert_ts = now
        self._decision_logger.log(
            {
                "ts": now,
                "event": "risk",
                "intent": "SYSTEM",
                "source": "strategy",
                "mode": self._state.value,
                "reason": "funding_off_open_delta"
                if reason == "funding_off"
                else "stopped_with_open_delta",
                "leg": "positions",
                "cycle_id": self._cycle_id,
                "state": self._state.value,
                "spot_pos": spot_pos,
                "perp_pos": perp_pos,
                "delta": delta,
                "delta_tolerance": self._config.strategy.delta_tolerance,
                "funding_rate": funding_rate,
                "trigger_reason": reason,
                "action": "alert_only",
            }
        )

    def _pre_quote_edge_fields(self, funding_rate: float | None) -> dict[str, float | None]:
        if funding_rate is None:
            return {
                "expected_edge_bps": None,
                "expected_spread_bps": None,
                "funding_bps": None,
                "cost_bps": None,
                "adverse_buffer_bps": self._config.strategy.adverse_buffer_bps,
            }
        half_bps = max(
            self._config.strategy.base_half_spread_bps,
            self._config.strategy.min_half_spread_bps,
        )
        cost_bps = (
            2 * self._config.cost.fee_maker_perp_bps
            + 2 * (self._config.cost.fee_taker_spot_bps + self._config.cost.slippage_bps)
        )
        expected_spread_bps = 2 * half_bps
        funding_bps = funding_rate * 10000.0
        adverse_buffer_bps = self._config.strategy.adverse_buffer_bps
        return {
            "expected_edge_bps": expected_spread_bps
            + funding_bps
            - cost_bps
            - adverse_buffer_bps,
            "expected_spread_bps": expected_spread_bps,
            "funding_bps": funding_bps,
            "cost_bps": cost_bps,
            "adverse_buffer_bps": adverse_buffer_bps,
        }

    @staticmethod
    def _mid_move_bps(mid_now: float, mid_prev: float | None) -> float | None:
        if mid_prev is None or mid_prev <= 0:
            return None
        return (mid_now - mid_prev) / mid_prev * 10000.0

    def _quote_fade_policy_enabled(
        self,
        policy: str,
        mid_move_bps: float | None,
    ) -> tuple[bool, float]:
        current_threshold = self._config.strategy.fade_vol_bps
        if policy == "disabled":
            return False, current_threshold
        thresholds = {
            "threshold_5bps": 5.0,
            "threshold_8bps": 8.0,
            "threshold_10bps": 10.0,
        }
        threshold = thresholds.get(policy)
        if threshold is None:
            return True, current_threshold
        if mid_move_bps is None:
            return False, threshold
        return abs(mid_move_bps) >= threshold, threshold

    def _tfi_fade_policy_enabled(self, policy: str, tfi: float) -> tuple[bool, float]:
        if policy == "disabled":
            return False, self._config.strategy.tfi_fade_threshold
        if policy == "threshold_0p7":
            return abs(tfi) >= 0.7, 0.7
        if policy == "threshold_0p8":
            return abs(tfi) >= 0.8, 0.8
        return True, self._config.strategy.tfi_fade_threshold

    def _log_pre_quote_decision(
        self,
        ts: float,
        *,
        final_block_reason: str,
        expected_edge_fields: dict[str, float | None],
        book_stale: bool,
        funding_stale: bool,
        quote_fade_triggered: bool,
        cancel_aggressive_triggered: bool,
        tfi_fade_triggered: bool,
        one_sided_suppressed_bid: bool,
        one_sided_suppressed_ask: bool,
        final_should_quote_bid: bool,
        final_should_quote_ask: bool,
        cancel_aggressive_scope_suppressed: bool = False,
        cancel_aggressive_quality_suppressed: bool = False,
    ) -> None:
        active_quotes = self._active_quote_snapshot()
        final_should_quote_any = final_should_quote_bid or final_should_quote_ask
        self._decision_logger.log(
            {
                "ts": ts,
                "event": "risk",
                "intent": "quote",
                "source": "strategy",
                "mode": self._state.value,
                "reason": "pre_quote_decision",
                "cycle_id": self._cycle_id,
                "symbol": self._config.symbols.perp.symbol,
                "dry_run": self._config.strategy.dry_run,
                "base_half_spread_bps": self._config.strategy.base_half_spread_bps,
                "min_half_spread_bps": self._config.strategy.min_half_spread_bps,
                "expected_edge_bps": expected_edge_fields.get("expected_edge_bps"),
                "expected_spread_bps": expected_edge_fields.get("expected_spread_bps"),
                "funding_bps": expected_edge_fields.get("funding_bps"),
                "cost_bps": expected_edge_fields.get("cost_bps"),
                "adverse_buffer_bps": expected_edge_fields.get("adverse_buffer_bps"),
                "edge_pass": (
                    None
                    if expected_edge_fields.get("expected_edge_bps") is None
                    else expected_edge_fields["expected_edge_bps"] >= 0
                ),
                "has_active_quote": active_quotes["has_active_quote"],
                "active_quote_source": active_quotes["source"],
                "book_stale": book_stale,
                "funding_stale": funding_stale,
                "inventory_block": False,
                "max_inventory_block": final_block_reason == "inventory_block",
                "unhedged_block": final_block_reason == "unhedged_block",
                "reject_streak_block": False,
                "quote_fade_triggered": quote_fade_triggered,
                "cancel_aggressive_triggered": cancel_aggressive_triggered,
                "cancel_aggressive_policy": self._config.strategy.cancel_aggressive_policy,
                "cancel_aggressive_scope": self._config.strategy.cancel_aggressive_scope,
                "cancel_aggressive_scope_suppressed": cancel_aggressive_scope_suppressed,
                "cancel_aggressive_quality_filter": self._config.strategy.cancel_aggressive_quality_filter,
                "cancel_aggressive_quality_suppressed": cancel_aggressive_quality_suppressed,
                "tfi_fade_triggered": tfi_fade_triggered,
                "one_sided_suppressed_bid": one_sided_suppressed_bid,
                "one_sided_suppressed_ask": one_sided_suppressed_ask,
                "final_should_quote_bid": final_should_quote_bid,
                "final_should_quote_ask": final_should_quote_ask,
                "final_should_quote_any": final_should_quote_any,
                "final_block_reason": final_block_reason,
            }
        )

    def _cancel_aggressive_diagnostic(
        self,
        now: float,
        last_trade: dict,
        best_bid_px: float,
        best_ask_px: float,
        aggressive_leg: str,
    ) -> dict:
        active_quotes = self._active_quote_snapshot()
        active_bid = active_quotes.get("active_bid")
        active_ask = active_quotes.get("active_ask")
        active_bid_px = _as_float(active_quotes.get("active_bid_px"))
        active_ask_px = _as_float(active_quotes.get("active_ask_px"))
        trade_px = float(last_trade["price"])
        trade_ts = last_trade.get("ts")
        trade_age_ms = None if trade_ts is None else max(0.0, (now - float(trade_ts)) * 1000.0)
        proximity_to_active_bid_bps = self._proximity_bps(trade_px, active_bid_px)
        proximity_to_active_ask_bps = self._proximity_bps(trade_px, active_ask_px)
        proximity_to_best_bid_bps = self._proximity_bps(trade_px, best_bid_px)
        proximity_to_best_ask_bps = self._proximity_bps(trade_px, best_ask_px)
        proximity_to_active_quote_bps = (
            proximity_to_active_bid_bps
            if aggressive_leg == "bid"
            else proximity_to_active_ask_bps
        )
        proximity_to_best_bps = (
            proximity_to_best_bid_bps
            if aggressive_leg == "bid"
            else proximity_to_best_ask_bps
        )
        return {
            "has_active_quote": active_quotes["has_active_quote"],
            "active_bid_px": active_bid_px,
            "active_ask_px": active_ask_px,
            "active_bid_order_id": active_quotes["active_bid_order_id"],
            "active_ask_order_id": active_quotes["active_ask_order_id"],
            "active_bid_client_oid": active_quotes["active_bid_client_oid"],
            "active_ask_client_oid": active_quotes["active_ask_client_oid"],
            "active_bid_qty": active_quotes["active_bid_qty"],
            "active_ask_qty": active_quotes["active_ask_qty"],
            "active_bid_ts": active_quotes["active_bid_ts"],
            "active_ask_ts": active_quotes["active_ask_ts"],
            "best_bid_px": best_bid_px,
            "best_ask_px": best_ask_px,
            "trade_px": trade_px,
            "trade_side": last_trade["side"],
            "trade_ts": trade_ts,
            "trade_id": last_trade.get("trade_id"),
            "trade_age_ms": trade_age_ms,
            "used_bid_px": active_bid_px if active_bid is not None else best_bid_px,
            "used_ask_px": active_ask_px if active_ask is not None else best_ask_px,
            "used_px_source": (
                "active_quote" if active_quotes["has_active_quote"] else "best_bid_ask"
            ),
            "active_quote_source": active_quotes["source"],
            "proximity_to_active_bid_bps": proximity_to_active_bid_bps,
            "proximity_to_active_ask_bps": proximity_to_active_ask_bps,
            "proximity_to_best_bid_bps": proximity_to_best_bid_bps,
            "proximity_to_best_ask_bps": proximity_to_best_ask_bps,
            "proximity_to_active_quote_bps": proximity_to_active_quote_bps,
            "proximity_to_best_bps": proximity_to_best_bps,
        }

    def _cancel_aggressive_quality_filter_pass(
        self,
        quality_filter: str,
        diagnostic: dict,
        aggressive_leg: str,
    ) -> bool:
        if quality_filter != "fresh_active_quote_proximity":
            return True
        trade_age_ms = _as_float(diagnostic.get("trade_age_ms"))
        proximity = _as_float(diagnostic.get("proximity_to_active_quote_bps"))
        return (
            diagnostic.get("has_active_quote") is True
            and diagnostic.get("used_px_source") == "active_quote"
            and trade_age_ms is not None
            and trade_age_ms <= self._config.strategy.cancel_aggressive_max_trade_age_ms
            and proximity is not None
            and proximity <= self._config.strategy.cancel_aggressive_active_proximity_bps
            and self._cancel_aggressive_danger_match(
                aggressive_leg,
                diagnostic.get("trade_side"),
            )
            is True
        )

    @staticmethod
    def _cancel_aggressive_danger_match(
        aggressive_leg: str,
        trade_side: object,
    ) -> bool | None:
        if aggressive_leg == "bid" and trade_side == "sell":
            return True
        if aggressive_leg == "ask" and trade_side == "buy":
            return True
        if aggressive_leg in {"bid", "ask"} and trade_side in {"buy", "sell"}:
            return False
        return None

    def _active_quote_snapshot(self) -> dict[str, object]:
        snapshot = getattr(self._oms, "active_quote_snapshot", None)
        if callable(snapshot):
            return snapshot(self._config.symbols.perp.symbol)
        return {
            "has_active_quote": False,
            "active_bid": None,
            "active_ask": None,
            "active_bid_px": None,
            "active_ask_px": None,
            "active_bid_order_id": None,
            "active_ask_order_id": None,
            "active_bid_client_oid": None,
            "active_ask_client_oid": None,
            "active_bid_qty": None,
            "active_ask_qty": None,
            "active_bid_ts": None,
            "active_ask_ts": None,
            "source": "none",
        }

    @staticmethod
    def _proximity_bps(px_a: float | None, px_b: float | None) -> float | None:
        if px_a is None or px_b is None or px_b <= 0:
            return None
        return abs(px_a - px_b) / px_b * 10000.0

    @staticmethod
    def _one_sided_quote_suppression(policy: str, tfi: float) -> tuple[bool, bool]:
        thresholds = {
            "tfi_0p6": 0.6,
            "tfi_0p7": 0.7,
            "tfi_0p8": 0.8,
        }
        threshold = thresholds.get(policy)
        if threshold is None:
            return False, False
        return tfi <= -threshold, tfi >= threshold

    def _log_one_sided_quote_suppressed(
        self,
        ts: float,
        policy: str,
        suppressed_leg: str,
        tfi: float,
        mid_perp: float,
        bid_px: float,
        ask_px: float,
        spread_bps: float,
    ) -> None:
        self._decision_logger.log(
            {
                "ts": ts,
                "event": "risk",
                "intent": "quote",
                "source": "strategy",
                "mode": self._state.value,
                "reason": "one_sided_quote_suppressed",
                "leg": suppressed_leg,
                "cycle_id": self._cycle_id,
                "one_sided_quote_policy": policy,
                "suppressed_leg": suppressed_leg,
                "tfi": tfi,
                "mid_perp": mid_perp,
                "bid_px": bid_px,
                "ask_px": ask_px,
                "spread_bps": spread_bps,
            }
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
