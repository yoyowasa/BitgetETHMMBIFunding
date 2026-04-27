from __future__ import annotations

import csv
import json
import math
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
ROOT_LIFECYCLE_PATH = Path("reports/quote_lifecycle_details.csv")
FALLBACK_LIFECYCLE_PATH = Path("reports/spread_dryrun_compare/quote_lifecycle_details.csv")
DETAILS_PATH = Path("reports/active_quote_cancel_quality_details.csv")
SUMMARY_PATH = Path("reports/active_quote_cancel_quality_summary.csv")

QUOTE_FADE_WINDOW_SEC = 1.0
VALID_TRADE_AGE_MS = 500.0
VALID_PROXIMITY_BPS = 1.0
END_GUARD_REASONS = {
    "cancel_aggressive",
    "quote_fade",
    "tfi_fade",
    "edge_negative_total",
}

DETAIL_FIELDNAMES = [
    "ts",
    "trade_id",
    "trade_side",
    "trade_px",
    "trade_age_ms",
    "has_active_quote",
    "used_px_source",
    "active_bid_px",
    "active_ask_px",
    "active_bid_qty",
    "active_ask_qty",
    "active_bid_ts",
    "active_ask_ts",
    "proximity_to_active_bid_bps",
    "proximity_to_active_ask_bps",
    "proximity_to_active_quote_bps",
    "proximity_to_best_bps",
    "danger_direction_match",
    "tfi",
    "spread_bps",
    "quote_lifetime_sec",
    "end_reason",
    "quote_fade_nearby",
    "quote_fade_dt_ms",
    "valid_active_cancel_candidate",
]

SUMMARY_FIELDNAMES = [
    "group_type",
    "group_value",
    "count",
    "mean_trade_age_ms",
    "median_trade_age_ms",
    "p90_trade_age_ms",
    "mean_proximity_to_active_quote_bps",
    "median_proximity_to_active_quote_bps",
    "p90_proximity_to_active_quote_bps",
    "mean_proximity_to_best_bps",
    "median_proximity_to_best_bps",
    "danger_match_count",
    "danger_match_ratio",
    "valid_candidate_count",
    "valid_candidate_ratio",
    "quote_fade_nearby_count",
    "quote_fade_nearby_ratio",
    "mean_tfi",
    "median_tfi",
]


def _to_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


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


def _load_log_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for log_file in sorted(LOG_DIR.glob("*.jsonl")):
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _to_float(data.get("ts"))
                if ts is None:
                    continue
                data["ts"] = ts
                rows.append(data)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _is_quote_order(row: dict[str, object]) -> bool:
    if row.get("event") != "order_new":
        return False
    return row.get("reason") == "quote" or str(row.get("intent", "")).startswith("QUOTE_")


def _is_end_event(row: dict[str, object]) -> bool:
    event = row.get("event")
    if event in {"order_cancel", "order_skip"}:
        return True
    return event == "risk" and row.get("reason") in END_GUARD_REASONS


def _end_reason(row: dict[str, object] | None) -> str | None:
    if row is None:
        return None
    event = row.get("event")
    if event in {"order_cancel", "order_skip"}:
        reason = row.get("reason")
        return f"{event}:{reason}" if reason else str(event)
    return str(row.get("reason", event))


def _build_lifecycle_from_logs(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    quotes = [(idx, row) for idx, row in enumerate(rows) if _is_quote_order(row)]
    intervals: list[dict[str, object]] = []
    for quote_idx, quote in quotes:
        quote_ts = float(quote["ts"])
        end_event = next(
            (candidate for candidate in rows[quote_idx + 1 :] if _is_end_event(candidate)),
            None,
        )
        end_ts = _to_float(end_event.get("ts")) if end_event else None
        intervals.append(
            {
                "quote_ts": quote_ts,
                "end_ts": end_ts,
                "quote_lifetime_sec": end_ts - quote_ts if end_ts is not None else None,
                "end_reason": _end_reason(end_event) or "open_or_unknown",
            }
        )
    intervals.sort(key=lambda row: float(row["quote_ts"]))
    return intervals


def _load_lifecycle_from_csv() -> list[dict[str, object]]:
    path = ROOT_LIFECYCLE_PATH if ROOT_LIFECYCLE_PATH.exists() else FALLBACK_LIFECYCLE_PATH
    if not path.exists():
        return []
    intervals: list[dict[str, object]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            quote_ts = _to_float(row.get("quote_ts"))
            end_ts = _to_float(row.get("end_ts"))
            if quote_ts is None:
                continue
            intervals.append(
                {
                    "quote_ts": quote_ts,
                    "end_ts": end_ts,
                    "quote_lifetime_sec": _to_float(row.get("quote_lifetime_sec")),
                    "end_reason": row.get("end_reason") or "open_or_unknown",
                }
            )
    intervals.sort(key=lambda row: float(row["quote_ts"]))
    return intervals


def _find_lifecycle(
    ts: float,
    active_ts_values: list[float],
    intervals: list[dict[str, object]],
) -> dict[str, object] | None:
    if not intervals:
        return None
    idx = max(0, bisect_left(active_ts_values, ts) - 1)
    candidates = intervals[max(0, idx - 3) : idx + 4]
    containing = [
        row
        for row in candidates
        if (end_ts := _to_float(row.get("end_ts"))) is not None
        and float(row["quote_ts"]) <= ts <= end_ts
    ]
    if containing:
        return min(containing, key=lambda row: ts - float(row["quote_ts"]))
    previous = [row for row in candidates if float(row["quote_ts"]) <= ts]
    return max(previous, key=lambda row: float(row["quote_ts"])) if previous else None


def _danger_direction_match(row: dict[str, object]) -> bool | None:
    leg = row.get("leg")
    side = row.get("trade_side")
    if leg == "bid" and side == "sell":
        return True
    if leg == "ask" and side == "buy":
        return True
    if leg in {"bid", "ask"} and side in {"buy", "sell"}:
        return False
    return None


def _nearest_quote_fade(ts: float, quote_fade_ts: list[float]) -> tuple[bool, float | None]:
    if not quote_fade_ts:
        return False, None
    idx = bisect_left(quote_fade_ts, ts)
    candidates = []
    if idx < len(quote_fade_ts):
        candidates.append(quote_fade_ts[idx])
    if idx > 0:
        candidates.append(quote_fade_ts[idx - 1])
    if not candidates:
        return False, None
    nearest = min(candidates, key=lambda value: abs(value - ts))
    dt_ms = (nearest - ts) * 1000.0
    return abs(dt_ms) <= QUOTE_FADE_WINDOW_SEC * 1000.0, dt_ms


def _valid_candidate(row: dict[str, object]) -> bool:
    trade_age = _to_float(row.get("trade_age_ms"))
    proximity = _to_float(row.get("proximity_to_active_quote_bps"))
    return (
        _is_true(row.get("has_active_quote"))
        and row.get("used_px_source") == "active_quote"
        and trade_age is not None
        and trade_age <= VALID_TRADE_AGE_MS
        and proximity is not None
        and proximity <= VALID_PROXIMITY_BPS
        and row.get("danger_direction_match") is True
    )


def _build_details(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    lifecycle = _build_lifecycle_from_logs(rows)
    lifecycle.extend(_load_lifecycle_from_csv())
    lifecycle.sort(key=lambda row: float(row["quote_ts"]))
    lifecycle_ts = [float(row["quote_ts"]) for row in lifecycle]
    quote_fade_ts = [
        float(row["ts"])
        for row in rows
        if row.get("event") == "risk" and row.get("reason") == "quote_fade"
    ]

    details: list[dict[str, object]] = []
    for row in rows:
        if row.get("event") != "risk" or row.get("reason") != "cancel_aggressive":
            continue
        if not (_is_true(row.get("has_active_quote")) or row.get("used_px_source") == "active_quote"):
            continue

        ts = float(row["ts"])
        lifecycle_row = _find_lifecycle(ts, lifecycle_ts, lifecycle)
        quote_fade_nearby, quote_fade_dt_ms = _nearest_quote_fade(ts, quote_fade_ts)
        danger_match = _danger_direction_match(row)
        detail = {
            "ts": ts,
            "trade_id": row.get("trade_id"),
            "trade_side": row.get("trade_side"),
            "trade_px": row.get("trade_px"),
            "trade_age_ms": row.get("trade_age_ms"),
            "has_active_quote": row.get("has_active_quote"),
            "used_px_source": row.get("used_px_source"),
            "active_bid_px": row.get("active_bid_px"),
            "active_ask_px": row.get("active_ask_px"),
            "active_bid_qty": row.get("active_bid_qty"),
            "active_ask_qty": row.get("active_ask_qty"),
            "active_bid_ts": row.get("active_bid_ts"),
            "active_ask_ts": row.get("active_ask_ts"),
            "proximity_to_active_bid_bps": row.get("proximity_to_active_bid_bps"),
            "proximity_to_active_ask_bps": row.get("proximity_to_active_ask_bps"),
            "proximity_to_active_quote_bps": row.get("proximity_to_active_quote_bps"),
            "proximity_to_best_bps": row.get("proximity_to_best_bps"),
            "danger_direction_match": danger_match,
            "tfi": row.get("tfi"),
            "spread_bps": row.get("spread_bps"),
            "quote_lifetime_sec": lifecycle_row.get("quote_lifetime_sec")
            if lifecycle_row
            else None,
            "end_reason": lifecycle_row.get("end_reason") if lifecycle_row else None,
            "quote_fade_nearby": quote_fade_nearby,
            "quote_fade_dt_ms": quote_fade_dt_ms,
            "valid_active_cancel_candidate": False,
        }
        detail["valid_active_cancel_candidate"] = _valid_candidate(detail)
        details.append(detail)
    return details


def _float_values(rows: list[dict[str, object]], key: str) -> list[float]:
    return [value for row in rows if (value := _to_float(row.get(key))) is not None]


def _summary_row(
    group_type: str, group_value: str, rows: list[dict[str, object]]
) -> dict[str, object]:
    count = len(rows)
    trade_age = _float_values(rows, "trade_age_ms")
    active_proximity = _float_values(rows, "proximity_to_active_quote_bps")
    best_proximity = _float_values(rows, "proximity_to_best_bps")
    tfi_values = _float_values(rows, "tfi")
    known_danger_rows = [
        row for row in rows if row.get("danger_direction_match") in {True, False}
    ]
    danger_match_count = sum(
        1 for row in known_danger_rows if row.get("danger_direction_match") is True
    )
    valid_count = sum(
        1 for row in rows if row.get("valid_active_cancel_candidate") is True
    )
    quote_fade_count = sum(1 for row in rows if row.get("quote_fade_nearby") is True)
    return {
        "group_type": group_type,
        "group_value": group_value,
        "count": count,
        "mean_trade_age_ms": _mean(trade_age),
        "median_trade_age_ms": median(trade_age) if trade_age else None,
        "p90_trade_age_ms": _percentile(trade_age, 0.90),
        "mean_proximity_to_active_quote_bps": _mean(active_proximity),
        "median_proximity_to_active_quote_bps": median(active_proximity)
        if active_proximity
        else None,
        "p90_proximity_to_active_quote_bps": _percentile(active_proximity, 0.90),
        "mean_proximity_to_best_bps": _mean(best_proximity),
        "median_proximity_to_best_bps": median(best_proximity)
        if best_proximity
        else None,
        "danger_match_count": danger_match_count,
        "danger_match_ratio": danger_match_count / len(known_danger_rows)
        if known_danger_rows
        else None,
        "valid_candidate_count": valid_count,
        "valid_candidate_ratio": valid_count / count if count else None,
        "quote_fade_nearby_count": quote_fade_count,
        "quote_fade_nearby_ratio": quote_fade_count / count if count else None,
        "mean_tfi": _mean(tfi_values),
        "median_tfi": median(tfi_values) if tfi_values else None,
    }


def _build_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    summary = [_summary_row("overall", "all", details)]
    for group_type, key in [
        ("valid_active_cancel_candidate", "valid_active_cancel_candidate"),
        ("trade_side", "trade_side"),
        ("danger_direction_match", "danger_direction_match"),
        ("quote_fade_nearby", "quote_fade_nearby"),
    ]:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in details:
            grouped[str(row.get(key))].append(row)
        for group_value, rows in sorted(grouped.items()):
            summary.append(_summary_row(group_type, group_value, rows))
    return summary


def main() -> int:
    rows = _load_log_rows()
    details = _build_details(rows)
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

    overall = summary[0] if summary else {}
    print(
        "done "
        f"rows={len(details)} "
        f"valid_candidate_count={overall.get('valid_candidate_count')} "
        f"valid_candidate_ratio={overall.get('valid_candidate_ratio')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
