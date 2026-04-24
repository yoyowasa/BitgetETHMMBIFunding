from __future__ import annotations

import time
from dataclasses import dataclass

from .jsonl import JsonlLogger


@dataclass
class QuoteMetrics:
    quote_orders: int = 0
    quote_fills: int = 0
    adverse_fills: int = 0


class PnLAggregator:
    def __init__(self, logger: JsonlLogger):
        self._logger = logger
        self._reset()

    def _reset(self) -> None:
        self.gross_spread_pnl = 0.0
        self.fees_paid = 0.0
        self.funding_received = 0.0
        self.hedge_slip_cost = 0.0
        self.basis_pnl = 0.0
        self.hedge_latency_count = 0
        self.hedge_latency_ms_sum = 0.0
        self.max_unhedged_notional = 0.0
        self.quote_replace_count = 0
        self.reject_streak = 0
        self.quote_orders = 0
        self.quote_fills = 0
        self.adverse_fills = 0

    def record_gross_spread(self, spread_pnl: float) -> None:
        self.gross_spread_pnl += spread_pnl

    def record_fees(self, fee: float) -> None:
        self.fees_paid += fee

    def record_funding(self, funding: float) -> None:
        self.funding_received += funding

    def record_hedge_slip(self, slip: float) -> None:
        self.hedge_slip_cost += slip

    def record_basis(self, basis: float) -> None:
        self.basis_pnl += basis

    def record_hedge_latency(self, latency_ms: float) -> None:
        self.hedge_latency_count += 1
        self.hedge_latency_ms_sum += latency_ms

    def record_quote_replace(self) -> None:
        self.quote_replace_count += 1

    def record_reject_streak(self, streak: int) -> None:
        self.reject_streak = max(self.reject_streak, streak)

    def record_quote_metrics(self, metrics: QuoteMetrics) -> None:
        self.quote_orders += metrics.quote_orders
        self.quote_fills += metrics.quote_fills
        self.adverse_fills += metrics.adverse_fills

    def update_max_unhedged_notional(self, notional: float) -> None:
        self.max_unhedged_notional = max(self.max_unhedged_notional, notional)

    def flush(self) -> None:
        quote_fill_rate = (
            self.quote_fills / self.quote_orders if self.quote_orders > 0 else 0.0
        )
        adverse_fill_rate = (
            self.adverse_fills / self.quote_fills if self.quote_fills > 0 else 0.0
        )
        avg_hedge_latency_ms = (
            self.hedge_latency_ms_sum / self.hedge_latency_count
            if self.hedge_latency_count > 0
            else 0.0
        )
        self._logger.log(
            {
                "ts": time.time(),
                "event": "pnl_1min",
                "intent": "SYSTEM",
                "source": "pnl",
                "mode": "RUN",
                "reason": "pnl_1min",
                "leg": "system",
                "gross_spread_pnl": self.gross_spread_pnl,
                "fees_paid": self.fees_paid,
                "funding_received": self.funding_received,
                "hedge_slip_cost": self.hedge_slip_cost,
                "basis_pnl": self.basis_pnl,
                "net_pnl": (
                    self.gross_spread_pnl
                    - self.fees_paid
                    + self.funding_received
                    - self.hedge_slip_cost
                    + self.basis_pnl
                ),
                "hedge_latency_ms": avg_hedge_latency_ms,
                "max_unhedged_notional": self.max_unhedged_notional,
                "quote_replace_count": self.quote_replace_count,
                "reject_streak": self.reject_streak,
                "quote_fill_rate": quote_fill_rate,
                "adverse_fill_rate": adverse_fill_rate,
            }
        )
        self._reset()
