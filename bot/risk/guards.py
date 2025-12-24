from __future__ import annotations

import time

from ..config import RiskConfig


class RiskGuards:
    def __init__(self, config: RiskConfig):
        self._config = config
        self._cooldown_until = 0.0

    def in_cooldown(self, now: float | None = None) -> bool:
        now_ts = now if now is not None else time.time()
        return now_ts < self._cooldown_until

    def set_cooldown(self, now: float | None = None) -> None:
        now_ts = now if now is not None else time.time()
        self._cooldown_until = now_ts + self._config.cooldown_sec

    def stale(self, last_ts: float | None, now: float | None = None) -> bool:
        if last_ts is None:
            return True
        now_ts = now if now is not None else time.time()
        return (now_ts - last_ts) > self._config.stale_sec

    def unhedged_exceeded(self, unhedged_notional: float, unhedged_since: float | None) -> bool:
        if unhedged_notional <= 0:
            return False
        if unhedged_notional >= self._config.max_unhedged_notional:
            return True
        if unhedged_since is None:
            return False
        return (time.time() - unhedged_since) >= self._config.max_unhedged_sec
