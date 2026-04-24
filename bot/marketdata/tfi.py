from __future__ import annotations

from collections import deque


class TFIAccumulator:
    def __init__(self, window_sec: float = 5.0):
        self._window_sec = window_sec
        self._trades: deque[tuple[float, float, str]] = deque()

    def add_trade(self, ts: float, size: float, side: str) -> None:
        self._trades.append((ts, size, side))
        self._evict(ts)

    def value_at(self, now: float) -> float:
        self._evict(now)
        buy_vol = sum(size for _, size, side in self._trades if side == "buy")
        sell_vol = sum(size for _, size, side in self._trades if side == "sell")
        total = buy_vol + sell_vol
        if total <= 0:
            return 0.0
        return (buy_vol - sell_vol) / total

    def get_tfi(self) -> float:
        if not self._trades:
            return 0.0
        return self.value_at(self._trades[-1][0])

    def _evict(self, now: float) -> None:
        cutoff = now - self._window_sec
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()
