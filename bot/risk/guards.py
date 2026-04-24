from __future__ import annotations

import time
from typing import Optional

from ..config import RiskConfig


class RiskGuards:
    def __init__(self, config: RiskConfig):
        self._config = config
        self._cooldown_until = 0.0
        self._halted = False
        self._halt_reason: str | None = None
        self._halt_ts: float | None = None
        self._reject_streak = 0

    def in_cooldown(self, now: float | None = None) -> bool:
        now_ts = now if now is not None else time.time()
        return now_ts < self._cooldown_until

    def set_cooldown(self, now: float | None = None) -> None:
        now_ts = now if now is not None else time.time()
        self._cooldown_until = now_ts + self._config.cooldown_sec

    def halt(self, reason: str, now: float | None = None) -> None:
        now_ts = now if now is not None else time.time()
        self._halted = True
        self._halt_reason = reason
        self._halt_ts = now_ts

    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    @property
    def halt_ts(self) -> float | None:
        return self._halt_ts

    @property
    def reject_streak(self) -> int:
        return self._reject_streak

    def record_order_result(self, ok: bool, now: float | None = None) -> int:
        if ok:
            self._reject_streak = 0
            return 0
        self._reject_streak += 1
        if self._reject_streak >= self._config.reject_streak_limit:
            self.halt("reject_streak", now=now)
        return self._reject_streak

    def stale(self, last_ts: float | None, now: float | None = None) -> bool:
        if last_ts is None:
            return True
        now_ts = now if now is not None else time.time()
        stale_sec = (
            self._config.book_stale_sec
            if self._config.book_stale_sec is not None
            else self._config.stale_sec
        )
        return (now_ts - last_ts) > stale_sec

    def unhedged_exceeded(self, unhedged_notional: float, unhedged_since: float | None) -> bool:
        if unhedged_notional <= 0:
            return False
        if unhedged_notional >= self._config.max_unhedged_notional:
            return True
        if unhedged_since is None:
            return False
        return (time.time() - unhedged_since) >= self._config.max_unhedged_sec


def check_fast_mid_move(
    mid_now: float,
    mid_100ms_ago: Optional[float],
    fade_vol_bps: float = 3.0,
) -> bool:
    if mid_100ms_ago is None or mid_100ms_ago <= 0:
        return False
    return abs(mid_now - mid_100ms_ago) / mid_100ms_ago * 10000.0 > fade_vol_bps


def check_aggressive_trade(
    trade_px: float,
    trade_side: str,
    bid_px: float,
    ask_px: float,
    proximity_bps: float = 1.0,
) -> str | None:
    if trade_side == "buy" and ask_px > 0:
        if abs(trade_px - ask_px) / ask_px * 10000.0 < proximity_bps:
            return "ask"
    if trade_side == "sell" and bid_px > 0:
        if abs(trade_px - bid_px) / bid_px * 10000.0 < proximity_bps:
            return "bid"
    return None


def check_tfi_fade(tfi: float, threshold: float = 0.6) -> str | None:
    if tfi > threshold:
        return "ask"
    if tfi < -threshold:
        return "bid"
    return None
