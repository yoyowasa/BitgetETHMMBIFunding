from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


def _iter_jsonl(path: Path) -> Iterable[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _iter_records(log_dir: Path) -> Iterable[dict]:
    for path in sorted(log_dir.glob("*.jsonl")) + sorted(log_dir.glob("*.jsonl.gz")):
        yield from _iter_jsonl(path)


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalized_ts(value: object) -> float | None:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    return ts / 1000.0 if ts > 10_000_000_000 else ts


def _rough_pair_pnl(fills: list[dict]) -> list[dict]:
    pairs: list[dict] = []
    fills = sorted(fills, key=lambda row: _float(row.get("ts")))
    for idx, fill in enumerate(fills):
        if fill.get("intent") not in ("QUOTE_BID", "QUOTE_ASK"):
            continue
        if idx + 1 >= len(fills):
            continue
        hedge = fills[idx + 1]
        if hedge.get("intent") not in ("HEDGE", "UNWIND"):
            continue
        quote_px = _float(fill.get("price"))
        hedge_px = _float(hedge.get("price"))
        qty = min(_float(fill.get("size")), _float(hedge.get("size")))
        if fill.get("side") == "buy":
            gross = (hedge_px - quote_px) * qty
        else:
            gross = (quote_px - hedge_px) * qty
        quote_fee_usdt = abs(_float(fill.get("fee")))
        hedge_fee = _float(hedge.get("fee"))
        hedge_fee_usdt = abs(hedge_fee) if hedge.get("fee_coin") == "USDT" else 0.0
        pairs.append(
            {
                "quote_intent": fill.get("intent"),
                "quote_side": fill.get("side"),
                "quote_px": quote_px,
                "hedge_intent": hedge.get("intent"),
                "hedge_inst_type": hedge.get("inst_type"),
                "hedge_side": hedge.get("side"),
                "hedge_px": hedge_px,
                "qty": qty,
                "dt_sec": _float(hedge.get("ts")) - _float(fill.get("ts")),
                "gross_usdt": gross,
                "fee_usdt_known": quote_fee_usdt + hedge_fee_usdt,
                "net_usdt_known": gross - quote_fee_usdt - hedge_fee_usdt,
                "hedge_fee_coin": hedge.get("fee_coin"),
                "hedge_fee_raw": hedge_fee,
            }
        )
    return pairs


def analyze(log_dir: Path) -> dict:
    event_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    block_counts: Counter[str] = Counter()
    resp_counts: Counter[str] = Counter()
    fills: list[dict] = []
    pnl_rows: list[dict] = []
    first_ts: float | None = None
    last_ts: float | None = None

    for record in _iter_records(log_dir):
        ts = _normalized_ts(record.get("ts"))
        if ts is not None:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        event = str(record.get("event"))
        reason = record.get("reason")
        event_counts[event] += 1
        if reason:
            reason_counts[str(reason)] += 1
        if event == "risk" and reason == "pre_quote_decision":
            block_counts[str(record.get("final_block_reason"))] += 1
        if event in ("order_new", "order_cancel"):
            resp_counts[str(record.get("resp_code"))] += 1
        if event == "fill":
            fills.append(record)
        if event == "pnl_1min":
            pnl_rows.append(record)

    fill_by_intent: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "buy_qty": 0.0, "sell_qty": 0.0, "fee_sum": 0.0}
    )
    for fill in fills:
        key = f"{fill.get('inst_type')}:{fill.get('intent')}"
        bucket = fill_by_intent[key]
        bucket["count"] += 1
        size = _float(fill.get("size"))
        if fill.get("side") == "buy":
            bucket["buy_qty"] += size
        elif fill.get("side") == "sell":
            bucket["sell_qty"] += size
        bucket["fee_sum"] += _float(fill.get("fee"))

    pair_rows = _rough_pair_pnl(fills)
    nonzero_pnl = [
        row
        for row in pnl_rows
        if any(
            abs(_float(row.get(key))) > 1e-12
            for key in (
                "gross_spread_pnl",
                "fees_paid",
                "funding_received",
                "hedge_slip_cost",
                "basis_pnl",
                "net_pnl",
            )
        )
    ]
    return {
        "log_dir": str(log_dir),
        "duration_sec": None if first_ts is None or last_ts is None else last_ts - first_ts,
        "event_counts": dict(event_counts.most_common(20)),
        "top_reasons": dict(reason_counts.most_common(20)),
        "pre_quote_blocks": dict(block_counts.most_common()),
        "order_resp_codes": dict(resp_counts),
        "fill_count": len(fills),
        "fill_by_intent": dict(fill_by_intent),
        "rough_pairs": pair_rows,
        "rough_pair_net_usdt_known": sum(row["net_usdt_known"] for row in pair_rows),
        "pnl_rows": len(pnl_rows),
        "pnl_nonzero_rows": len(nonzero_pnl),
        "pnl_net_sum": sum(_float(row.get("net_pnl")) for row in pnl_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze live profitability from bot JSONL logs.")
    parser.add_argument("log_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.log_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
