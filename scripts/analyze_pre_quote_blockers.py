from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
DETAILS_PATH = Path("reports/pre_quote_blocker_details.csv")
SUMMARY_PATH = Path("reports/pre_quote_blocker_summary.csv")

DETAIL_FIELDNAMES = [
    "ts",
    "symbol",
    "dry_run",
    "base_half_spread_bps",
    "min_half_spread_bps",
    "expected_edge_bps",
    "edge_pass",
    "has_active_quote",
    "active_quote_source",
    "book_stale",
    "funding_stale",
    "inventory_block",
    "unhedged_block",
    "reject_streak_block",
    "quote_fade_triggered",
    "cancel_aggressive_triggered",
    "tfi_fade_triggered",
    "one_sided_suppressed_bid",
    "one_sided_suppressed_ask",
    "final_should_quote_bid",
    "final_should_quote_ask",
    "final_should_quote_any",
    "final_block_reason",
]

SUMMARY_FIELDNAMES = [
    "final_block_reason",
    "count",
    "ratio",
    "mean_expected_edge_bps",
    "median_expected_edge_bps",
    "edge_pass_count",
    "edge_pass_ratio",
    "has_active_quote_count",
    "quote_fade_count",
    "cancel_aggressive_count",
    "tfi_fade_count",
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
                if data.get("reason") != "pre_quote_decision":
                    continue
                rows.append({field: data.get(field) for field in DETAIL_FIELDNAMES})
    rows.sort(key=lambda row: _to_float(row.get("ts")) or 0.0)
    return rows


def _write_details(rows: list[dict[str, object]]) -> None:
    DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DETAILS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _write_summary(rows: list[dict[str, object]]) -> None:
    total = len(rows)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("final_block_reason") or "unknown")].append(row)

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SUMMARY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        for reason, reason_rows in sorted(grouped.items()):
            edges = [
                edge
                for edge in (_to_float(row.get("expected_edge_bps")) for row in reason_rows)
                if edge is not None
            ]
            writer.writerow(
                {
                    "final_block_reason": reason,
                    "count": len(reason_rows),
                    "ratio": len(reason_rows) / total if total else None,
                    "mean_expected_edge_bps": (
                        sum(edges) / len(edges) if edges else None
                    ),
                    "median_expected_edge_bps": median(edges) if edges else None,
                    "edge_pass_count": sum(
                        1 for row in reason_rows if _is_true(row.get("edge_pass"))
                    ),
                    "edge_pass_ratio": (
                        sum(1 for row in reason_rows if _is_true(row.get("edge_pass")))
                        / len(reason_rows)
                        if reason_rows
                        else None
                    ),
                    "has_active_quote_count": sum(
                        1
                        for row in reason_rows
                        if _is_true(row.get("has_active_quote"))
                    ),
                    "quote_fade_count": sum(
                        1
                        for row in reason_rows
                        if _is_true(row.get("quote_fade_triggered"))
                    ),
                    "cancel_aggressive_count": sum(
                        1
                        for row in reason_rows
                        if _is_true(row.get("cancel_aggressive_triggered"))
                    ),
                    "tfi_fade_count": sum(
                        1
                        for row in reason_rows
                        if _is_true(row.get("tfi_fade_triggered"))
                    ),
                }
            )

    totals = {
        "total_rows": total,
        "edge_pass_rows": sum(1 for row in rows if _is_true(row.get("edge_pass"))),
        "final_should_quote_any_rows": sum(
            1 for row in rows if _is_true(row.get("final_should_quote_any"))
        ),
        "quote_fade_triggered_rows": sum(
            1 for row in rows if _is_true(row.get("quote_fade_triggered"))
        ),
        "cancel_aggressive_triggered_rows": sum(
            1 for row in rows if _is_true(row.get("cancel_aggressive_triggered"))
        ),
        "tfi_fade_triggered_rows": sum(
            1 for row in rows if _is_true(row.get("tfi_fade_triggered"))
        ),
    }
    reason_counts = Counter(str(row.get("final_block_reason") or "unknown") for row in rows)
    print(f"done rows={total} totals={totals} reasons={dict(reason_counts)}")


def main() -> None:
    rows = _load_rows()
    _write_details(rows)
    _write_summary(rows)


if __name__ == "__main__":
    main()
