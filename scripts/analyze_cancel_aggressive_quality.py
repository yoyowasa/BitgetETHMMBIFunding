from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
LIFECYCLE_PATH = Path("reports/spread_dryrun_compare/quote_lifecycle_details.csv")
DETAILS_PATH = Path("reports/cancel_aggressive_quality_details.csv")
SUMMARY_PATH = Path("reports/cancel_aggressive_quality_summary.csv")

DETAIL_FIELDNAMES = [
    "ts",
    "in_quote",
    "quote_ts",
    "quote_leg",
    "quote_price",
    "quote_lifetime_sec",
    "trade_side",
    "trade_px",
    "mid_perp",
    "bid_px",
    "ask_px",
    "spread_bps",
    "tfi",
    "proximity_to_quote_bps",
    "proximity_to_best_bps",
    "danger_direction_match",
    "trade_reuse_key",
    "duplicate_trade_signal",
    "time_since_quote_ms",
    "time_to_quote_end_ms",
]

SUMMARY_FIELDNAMES = [
    "group_type",
    "group_value",
    "count",
    "mean_proximity_to_quote_bps",
    "median_proximity_to_quote_bps",
    "p75_proximity_to_quote_bps",
    "p90_proximity_to_quote_bps",
    "mean_proximity_to_best_bps",
    "median_proximity_to_best_bps",
    "duplicate_count",
    "duplicate_ratio",
    "danger_match_count",
    "danger_match_ratio",
    "mean_tfi",
    "median_tfi",
]


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * percentile
    lower = math.floor(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _load_lifecycle() -> list[dict[str, object]]:
    intervals: list[dict[str, object]] = []
    with LIFECYCLE_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            quote_ts = _to_float(row.get("quote_ts"))
            end_ts = _to_float(row.get("end_ts"))
            quote_price = _to_float(row.get("price"))
            quote_lifetime = _to_float(row.get("quote_lifetime_sec"))
            if quote_ts is None or end_ts is None or quote_price is None:
                continue
            intervals.append(
                {
                    "quote_ts": quote_ts,
                    "end_ts": end_ts,
                    "quote_leg": row.get("leg"),
                    "quote_price": quote_price,
                    "quote_lifetime_sec": quote_lifetime,
                }
            )
    intervals.sort(key=lambda row: float(row["quote_ts"]))
    return intervals


def _find_quote_interval(
    intervals: list[dict[str, object]], ts: float
) -> dict[str, object] | None:
    matches = [
        row
        for row in intervals
        if float(row["quote_ts"]) <= ts <= float(row["end_ts"])
    ]
    if not matches:
        return None
    return min(matches, key=lambda row: ts - float(row["quote_ts"]))


def _load_cancel_aggressive_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for log_file in sorted(LOG_DIR.glob("*.jsonl")):
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("event") != "risk":
                    continue
                if data.get("reason") != "cancel_aggressive":
                    continue
                ts = _to_float(data.get("ts"))
                if ts is None:
                    continue
                data["ts"] = ts
                rows.append(data)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _proximity_to_quote_bps(
    trade_px: float | None, quote_price: float | None
) -> float | None:
    if trade_px is None or quote_price is None or quote_price == 0:
        return None
    return abs(trade_px - quote_price) / quote_price * 10000.0


def _proximity_to_best_bps(
    trade_side: object,
    trade_px: float | None,
    bid_px: float | None,
    ask_px: float | None,
) -> float | None:
    if trade_px is None:
        return None
    if trade_side == "sell" and bid_px is not None and bid_px != 0:
        return abs(trade_px - bid_px) / bid_px * 10000.0
    if trade_side == "buy" and ask_px is not None and ask_px != 0:
        return abs(trade_px - ask_px) / ask_px * 10000.0
    return None


def _danger_match(quote_leg: object, trade_side: object) -> str:
    if quote_leg not in {"bid", "ask"}:
        return "unknown"
    if quote_leg == "bid" and trade_side == "sell":
        return "true"
    if quote_leg == "ask" and trade_side == "buy":
        return "true"
    return "false"


def _trade_reuse_key(row: dict[str, object]) -> str:
    trade_id = row.get("trade_id")
    if trade_id:
        return str(trade_id)
    ts = float(row["ts"])
    bucket = math.floor(ts * 10.0) / 10.0
    return f"{row.get('trade_side')}:{row.get('trade_px')}:{bucket:.1f}"


def _build_details() -> list[dict[str, object]]:
    intervals = _load_lifecycle()
    rows = _load_cancel_aggressive_rows()
    lifecycle_start = min(float(row["quote_ts"]) for row in intervals) if intervals else None
    lifecycle_end = max(float(row["end_ts"]) for row in intervals) if intervals else None
    if lifecycle_start is not None and lifecycle_end is not None:
        rows = [
            row
            for row in rows
            if lifecycle_start <= float(row["ts"]) <= lifecycle_end
        ]
    key_counts = Counter(_trade_reuse_key(row) for row in rows)

    details: list[dict[str, object]] = []
    for row in rows:
        ts = float(row["ts"])
        interval = _find_quote_interval(intervals, ts)
        quote_ts = _to_float(interval.get("quote_ts")) if interval else None
        quote_end = _to_float(interval.get("end_ts")) if interval else None
        quote_price = _to_float(interval.get("quote_price")) if interval else None
        trade_px = _to_float(row.get("trade_px"))
        bid_px = _to_float(row.get("bid_px"))
        ask_px = _to_float(row.get("ask_px"))
        key = _trade_reuse_key(row)

        details.append(
            {
                "ts": ts,
                "in_quote": interval is not None,
                "quote_ts": quote_ts,
                "quote_leg": interval.get("quote_leg") if interval else None,
                "quote_price": quote_price,
                "quote_lifetime_sec": interval.get("quote_lifetime_sec")
                if interval
                else None,
                "trade_side": row.get("trade_side"),
                "trade_px": trade_px,
                "mid_perp": row.get("mid_perp"),
                "bid_px": bid_px,
                "ask_px": ask_px,
                "spread_bps": row.get("spread_bps"),
                "tfi": row.get("tfi"),
                "proximity_to_quote_bps": _proximity_to_quote_bps(
                    trade_px, quote_price
                ),
                "proximity_to_best_bps": _proximity_to_best_bps(
                    row.get("trade_side"), trade_px, bid_px, ask_px
                ),
                "danger_direction_match": _danger_match(
                    interval.get("quote_leg") if interval else None,
                    row.get("trade_side"),
                ),
                "trade_reuse_key": key,
                "duplicate_trade_signal": key_counts[key] > 1,
                "time_since_quote_ms": (ts - quote_ts) * 1000.0
                if quote_ts is not None
                else None,
                "time_to_quote_end_ms": (quote_end - ts) * 1000.0
                if quote_end is not None
                else None,
            }
        )
    return details


def _float_values(rows: list[dict[str, object]], key: str) -> list[float]:
    return [value for row in rows if (value := _to_float(row.get(key))) is not None]


def _summary_row(
    group_type: str, group_value: str, rows: list[dict[str, object]]
) -> dict[str, object]:
    proximity_quote = _float_values(rows, "proximity_to_quote_bps")
    proximity_best = _float_values(rows, "proximity_to_best_bps")
    tfi_values = _float_values(rows, "tfi")
    count = len(rows)
    duplicate_count = sum(1 for row in rows if row.get("duplicate_trade_signal") is True)
    known_danger_rows = [
        row for row in rows if row.get("danger_direction_match") in {"true", "false"}
    ]
    danger_match_count = sum(
        1 for row in known_danger_rows if row.get("danger_direction_match") == "true"
    )
    return {
        "group_type": group_type,
        "group_value": group_value,
        "count": count,
        "mean_proximity_to_quote_bps": _mean(proximity_quote),
        "median_proximity_to_quote_bps": median(proximity_quote)
        if proximity_quote
        else None,
        "p75_proximity_to_quote_bps": _percentile(proximity_quote, 0.75),
        "p90_proximity_to_quote_bps": _percentile(proximity_quote, 0.90),
        "mean_proximity_to_best_bps": _mean(proximity_best),
        "median_proximity_to_best_bps": median(proximity_best)
        if proximity_best
        else None,
        "duplicate_count": duplicate_count,
        "duplicate_ratio": duplicate_count / count if count else None,
        "danger_match_count": danger_match_count,
        "danger_match_ratio": danger_match_count / len(known_danger_rows)
        if known_danger_rows
        else None,
        "mean_tfi": _mean(tfi_values),
        "median_tfi": median(tfi_values) if tfi_values else None,
    }


def _build_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    summary = [_summary_row("overall", "all", details)]

    for group_type, key in [
        ("in_quote", "in_quote"),
        ("quote_leg", "quote_leg"),
        ("trade_side", "trade_side"),
        ("danger_direction_match", "danger_direction_match"),
        ("duplicate_trade_signal", "duplicate_trade_signal"),
    ]:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in details:
            grouped[str(row.get(key))].append(row)
        for group_value, rows in sorted(grouped.items()):
            summary.append(_summary_row(group_type, group_value, rows))

    in_quote_rows = [row for row in details if row.get("in_quote") is True]
    duplicate_count = sum(
        1 for row in details if row.get("duplicate_trade_signal") is True
    )
    known_danger_rows = [
        row
        for row in in_quote_rows
        if row.get("danger_direction_match") in {"true", "false"}
    ]
    danger_match_count = sum(
        1 for row in known_danger_rows if row.get("danger_direction_match") == "true"
    )
    prox = _float_values(in_quote_rows, "proximity_to_quote_bps")
    summary.append(
        {
            "group_type": "overall_metrics",
            "group_value": "all",
            "count": len(details),
            "mean_proximity_to_quote_bps": None,
            "median_proximity_to_quote_bps": median(prox) if prox else None,
            "p75_proximity_to_quote_bps": _percentile(prox, 0.75),
            "p90_proximity_to_quote_bps": _percentile(prox, 0.90),
            "mean_proximity_to_best_bps": None,
            "median_proximity_to_best_bps": None,
            "duplicate_count": duplicate_count,
            "duplicate_ratio": duplicate_count / len(details) if details else None,
            "danger_match_count": danger_match_count,
            "danger_match_ratio": danger_match_count / len(known_danger_rows)
            if known_danger_rows
            else None,
            "mean_tfi": None,
            "median_tfi": None,
        }
    )
    return summary


def main() -> int:
    details = _build_details()
    summary = _build_summary(details)

    DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DETAILS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDNAMES)
        writer.writeheader()
        writer.writerows(details)

    with SUMMARY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(summary)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
