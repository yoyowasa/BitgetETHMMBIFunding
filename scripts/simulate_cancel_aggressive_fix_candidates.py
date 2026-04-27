from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
DETAILS_PATH = Path("reports/cancel_aggressive_fix_candidate_details.csv")
SUMMARY_PATH = Path("reports/cancel_aggressive_fix_candidate_summary.csv")

CANDIDATES = (
    "A_current",
    "B_require_active_quote",
    "C_require_active_quote_and_fresh_trade",
    "D_require_active_quote_fresh_and_active_proximity",
    "E_require_active_quote_fresh_active_proximity_and_danger_match",
)

DETAIL_FIELDNAMES = [
    "candidate",
    "ts",
    "would_trigger",
    "has_active_quote",
    "used_px_source",
    "trade_id",
    "trade_side",
    "trade_px",
    "trade_ts",
    "trade_age_ms",
    "active_bid_px",
    "active_ask_px",
    "best_bid_px",
    "best_ask_px",
    "proximity_to_active_quote_bps",
    "proximity_to_best_bps",
    "tfi",
    "reason_blocked",
]

SUMMARY_FIELDNAMES = [
    "candidate",
    "total_rows",
    "would_trigger_count",
    "would_trigger_ratio",
    "blocked_count",
    "blocked_ratio",
    "no_active_quote_count",
    "stale_trade_count",
    "far_from_active_quote_count",
    "danger_direction_mismatch_count",
    "median_trade_age_ms_triggered",
    "p90_trade_age_ms_triggered",
    "median_proximity_to_active_quote_bps_triggered",
    "p90_proximity_to_active_quote_bps_triggered",
    "median_proximity_to_best_bps_triggered",
    "p90_proximity_to_best_bps_triggered",
]


def _to_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


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


def _load_rows() -> list[dict[str, object]]:
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
                if data.get("reason") != "cancel_aggressive_diagnostic":
                    continue
                ts = _to_float(data.get("ts"))
                if ts is None:
                    continue
                data["ts"] = ts
                rows.append(data)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _danger_direction_match(row: dict[str, object]) -> bool:
    trade_side = row.get("trade_side")
    active_bid_px = _to_float(row.get("active_bid_px"))
    active_ask_px = _to_float(row.get("active_ask_px"))
    if trade_side == "sell" and active_bid_px is not None:
        return True
    if trade_side == "buy" and active_ask_px is not None:
        return True
    return False


def _blocked_reason(candidate: str, row: dict[str, object]) -> str:
    has_active_quote = _to_bool(row.get("has_active_quote"))
    trade_age_ms = _to_float(row.get("trade_age_ms"))
    proximity = _to_float(row.get("proximity_to_active_quote_bps"))

    if candidate == "A_current":
        return "none"
    if not has_active_quote:
        return "no_active_quote"
    if candidate == "B_require_active_quote":
        return "none"
    if trade_age_ms is None or trade_age_ms > 500.0:
        return "stale_trade"
    if candidate == "C_require_active_quote_and_fresh_trade":
        return "none"
    if proximity is None or proximity > 1.0:
        return "far_from_active_quote"
    if candidate == "D_require_active_quote_fresh_and_active_proximity":
        return "none"
    if not _danger_direction_match(row):
        return "danger_direction_mismatch"
    if candidate == "E_require_active_quote_fresh_active_proximity_and_danger_match":
        return "none"
    raise ValueError(f"unknown candidate: {candidate}")


def _detail_row(candidate: str, row: dict[str, object]) -> dict[str, object]:
    reason_blocked = _blocked_reason(candidate, row)
    return {
        "candidate": candidate,
        "ts": row.get("ts"),
        "would_trigger": str(reason_blocked == "none").lower(),
        "has_active_quote": str(_to_bool(row.get("has_active_quote"))).lower(),
        "used_px_source": row.get("used_px_source"),
        "trade_id": row.get("trade_id"),
        "trade_side": row.get("trade_side"),
        "trade_px": row.get("trade_px"),
        "trade_ts": row.get("trade_ts"),
        "trade_age_ms": row.get("trade_age_ms"),
        "active_bid_px": row.get("active_bid_px"),
        "active_ask_px": row.get("active_ask_px"),
        "best_bid_px": row.get("best_bid_px"),
        "best_ask_px": row.get("best_ask_px"),
        "proximity_to_active_quote_bps": row.get("proximity_to_active_quote_bps"),
        "proximity_to_best_bps": row.get("proximity_to_best_bps"),
        "tfi": row.get("tfi"),
        "reason_blocked": reason_blocked,
    }


def _write_details(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
    details: list[dict[str, object]] = []
    with DETAILS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            for candidate in CANDIDATES:
                detail = _detail_row(candidate, row)
                details.append(detail)
                writer.writerow(detail)
    return details


def _triggered_values(
    details: list[dict[str, object]], candidate: str, column: str
) -> list[float]:
    values: list[float] = []
    for row in details:
        if row.get("candidate") != candidate:
            continue
        if row.get("would_trigger") != "true":
            continue
        value = _to_float(row.get(column))
        if value is not None:
            values.append(value)
    return values


def _write_summary(details: list[dict[str, object]]) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    if CANDIDATES:
        total_rows = sum(1 for row in details if row.get("candidate") == CANDIDATES[0])

    with SUMMARY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        for candidate in CANDIDATES:
            candidate_rows = [row for row in details if row.get("candidate") == candidate]
            trigger_rows = [
                row for row in candidate_rows if row.get("would_trigger") == "true"
            ]
            blocked_rows = [
                row for row in candidate_rows if row.get("would_trigger") != "true"
            ]
            trade_ages = _triggered_values(details, candidate, "trade_age_ms")
            active_proximities = _triggered_values(
                details, candidate, "proximity_to_active_quote_bps"
            )
            best_proximities = _triggered_values(
                details, candidate, "proximity_to_best_bps"
            )
            writer.writerow(
                {
                    "candidate": candidate,
                    "total_rows": total_rows,
                    "would_trigger_count": len(trigger_rows),
                    "would_trigger_ratio": (
                        len(trigger_rows) / total_rows if total_rows else None
                    ),
                    "blocked_count": len(blocked_rows),
                    "blocked_ratio": (
                        len(blocked_rows) / total_rows if total_rows else None
                    ),
                    "no_active_quote_count": sum(
                        1
                        for row in candidate_rows
                        if row.get("reason_blocked") == "no_active_quote"
                    ),
                    "stale_trade_count": sum(
                        1
                        for row in candidate_rows
                        if row.get("reason_blocked") == "stale_trade"
                    ),
                    "far_from_active_quote_count": sum(
                        1
                        for row in candidate_rows
                        if row.get("reason_blocked") == "far_from_active_quote"
                    ),
                    "danger_direction_mismatch_count": sum(
                        1
                        for row in candidate_rows
                        if row.get("reason_blocked")
                        == "danger_direction_mismatch"
                    ),
                    "median_trade_age_ms_triggered": (
                        median(trade_ages) if trade_ages else None
                    ),
                    "p90_trade_age_ms_triggered": _percentile(trade_ages, 0.9),
                    "median_proximity_to_active_quote_bps_triggered": (
                        median(active_proximities) if active_proximities else None
                    ),
                    "p90_proximity_to_active_quote_bps_triggered": _percentile(
                        active_proximities, 0.9
                    ),
                    "median_proximity_to_best_bps_triggered": (
                        median(best_proximities) if best_proximities else None
                    ),
                    "p90_proximity_to_best_bps_triggered": _percentile(
                        best_proximities, 0.9
                    ),
                }
            )


def main() -> None:
    rows = _load_rows()
    details = _write_details(rows)
    _write_summary(details)
    print(f"done rows={len(rows)} details={len(details)}")


if __name__ == "__main__":
    main()
