from __future__ import annotations

import json

from bot.log.jsonl import JsonlLogger
from bot.log.pnl_logger import PnLAggregator, QuoteMetrics


def test_pnl_logger_flush(tmp_path) -> None:
    path = tmp_path / "pnl.jsonl"
    logger = JsonlLogger(str(path))
    agg = PnLAggregator(logger)

    agg.record_gross_spread(5.0)
    agg.record_fees(1.0)
    agg.record_funding(0.5)
    agg.record_hedge_slip(0.25)
    agg.record_basis(0.75)
    agg.record_hedge_latency(120.0)
    agg.record_quote_replace()
    agg.record_reject_streak(2)
    agg.record_quote_metrics(QuoteMetrics(quote_orders=10, quote_fills=4, adverse_fills=1))
    agg.update_max_unhedged_notional(123.0)
    agg.flush()

    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["event"] == "pnl_1min"
    assert record["net_pnl"] == 5.0
    assert record["quote_fill_rate"] == 0.4
    assert record["adverse_fill_rate"] == 0.25
    assert record["hedge_latency_ms"] == 120.0
