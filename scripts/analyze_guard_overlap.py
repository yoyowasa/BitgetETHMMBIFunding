from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path


INPUT_PATH = Path("reports/guard_forward_returns.csv")
DETAILS_PATH = Path("reports/guard_overlap_details.csv")
SUMMARY_PATH = Path("reports/guard_overlap_summary.csv")
TARGET_REASONS = {"quote_fade", "cancel_aggressive", "tfi_fade"}
RETURN_COLUMNS = ("ret_1s_bps", "ret_3s_bps", "ret_5s_bps")
OVERLAP_WINDOW_SEC = 1.0


def _parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _direction(row: dict[str, str]) -> int | None:
    reason = row.get("reason")
    if reason == "cancel_aggressive":
        if row.get("trade_side") == "sell":
            return -1
        if row.get("trade_side") == "buy":
            return 1
        return None
    if reason == "quote_fade":
        mid_move_bps = _parse_float(row.get("mid_move_bps"))
        if mid_move_bps is None or mid_move_bps == 0:
            return None
        return 1 if mid_move_bps > 0 else -1
    if reason == "tfi_fade":
        tfi = _parse_float(row.get("tfi"))
        if tfi is None or tfi == 0:
            return None
        return 1 if tfi > 0 else -1
    return None


def _classify(ret_bps: float | None, direction: int | None) -> tuple[str, float | None]:
    if ret_bps is None or ret_bps == 0 or direction is None:
        return "neutral", None
    directional_ret = ret_bps * direction
    if directional_ret > 0:
        return "success", directional_ret
    if directional_ret < 0:
        return "fail", directional_ret
    return "neutral", directional_ret


def _overlap_group(reasons: set[str]) -> str:
    has_quote = "quote_fade" in reasons
    has_cancel = "cancel_aggressive" in reasons
    has_tfi = "tfi_fade" in reasons

    if has_quote and has_cancel and has_tfi:
        return "overlap_all"
    if has_quote and has_cancel:
        return "overlap_quote_fade_cancel_aggressive"
    if has_quote and has_tfi:
        return "overlap_quote_fade_tfi_fade"
    if has_cancel and has_tfi:
        return "overlap_cancel_aggressive_tfi_fade"
    if has_quote:
        return "single_quote_fade"
    if has_cancel:
        return "single_cancel_aggressive"
    if has_tfi:
        return "single_tfi_fade"
    return "unknown"


def _empty_metrics() -> dict[str, object]:
    return {
        "values": [],
        "success_count": 0,
        "fail_count": 0,
        "neutral_count": 0,
        "directional_returns": [],
    }


def _summarize(metrics: dict[str, object]) -> dict[str, object]:
    values = metrics["values"]
    count = len(values)
    success_count = int(metrics["success_count"])
    fail_count = int(metrics["fail_count"])
    directional_returns = metrics["directional_returns"]

    if count == 0:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "positive_ratio": None,
            "negative_ratio": None,
            "directional_success_ratio": None,
            "directional_fail_ratio": None,
            "mean_directional_ret_bps": None,
        }

    positive = sum(1 for value in values if value > 0)
    negative = sum(1 for value in values if value < 0)
    return {
        "count": count,
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "positive_ratio": positive / count,
        "negative_ratio": negative / count,
        "directional_success_ratio": success_count / count,
        "directional_fail_ratio": fail_count / count,
        "mean_directional_ret_bps": (
            statistics.fmean(directional_returns) if directional_returns else None
        ),
    }


def _load_rows() -> list[dict[str, str]]:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"input not found: {INPUT_PATH}")
    with INPUT_PATH.open(encoding="utf-8", newline="") as f:
        rows = [
            row
            for row in csv.DictReader(f)
            if row.get("reason") in TARGET_REASONS and _parse_float(row.get("ts")) is not None
        ]
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _annotate_overlaps(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    annotated: list[dict[str, object]] = []
    for row in rows:
        ts = float(row["ts"])
        overlap_reasons = {row["reason"]}
        nearest_other_reason = ""
        nearest_other_dt_ms = None

        for other in rows:
            if other is row:
                continue
            dt = abs(float(other["ts"]) - ts)
            if dt > OVERLAP_WINDOW_SEC:
                continue
            if other["reason"] == row["reason"]:
                continue

            overlap_reasons.add(other["reason"])
            dt_ms = dt * 1000.0
            if nearest_other_dt_ms is None or dt_ms < nearest_other_dt_ms:
                nearest_other_dt_ms = dt_ms
                nearest_other_reason = other["reason"]

        annotated.append(
            {
                **row,
                "overlap_group": _overlap_group(overlap_reasons),
                "overlap_reasons": "|".join(sorted(overlap_reasons)),
                "nearest_other_reason": nearest_other_reason,
                "nearest_other_dt_ms": nearest_other_dt_ms,
            }
        )
    return annotated


def _write_details(rows: list[dict[str, object]]) -> None:
    DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ts",
        "reason",
        "trade_side",
        "mid_at_trigger",
        "mid_move_bps",
        "tfi",
        "trade_px",
        "overlap_group",
        "overlap_reasons",
        "nearest_other_reason",
        "nearest_other_dt_ms",
        "ret_1s_bps",
        "ret_3s_bps",
        "ret_5s_bps",
    ]
    with DETAILS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fieldnames} for row in rows)


def _write_summary(rows: list[dict[str, object]]) -> None:
    grouped: dict[tuple[str, str, str], dict[str, dict[str, object]]] = defaultdict(
        lambda: {column: _empty_metrics() for column in RETURN_COLUMNS}
    )

    for row in rows:
        key = (
            str(row.get("overlap_group") or "unknown"),
            str(row.get("reason") or "unknown"),
            str(row.get("trade_side") or ""),
        )
        direction = _direction(row)
        for column in RETURN_COLUMNS:
            value = _parse_float(row.get(column))
            outcome, directional_ret = _classify(value, direction)
            metrics = grouped[key][column]

            if value is not None:
                metrics["values"].append(value)
            if outcome == "success":
                metrics["success_count"] = int(metrics["success_count"]) + 1
            elif outcome == "fail":
                metrics["fail_count"] = int(metrics["fail_count"]) + 1
            else:
                metrics["neutral_count"] = int(metrics["neutral_count"]) + 1
            if directional_ret is not None:
                metrics["directional_returns"].append(directional_ret)

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "overlap_group",
        "reason",
        "trade_side",
        "return_horizon",
        "count",
        "mean",
        "median",
        "positive_ratio",
        "negative_ratio",
        "directional_success_ratio",
        "directional_fail_ratio",
        "mean_directional_ret_bps",
    ]
    with SUMMARY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for (overlap_group, reason, trade_side), metrics_by_horizon in sorted(grouped.items()):
            for column in RETURN_COLUMNS:
                summary = _summarize(metrics_by_horizon[column])
                writer.writerow(
                    {
                        "overlap_group": overlap_group,
                        "reason": reason,
                        "trade_side": trade_side,
                        "return_horizon": column,
                        "count": summary["count"],
                        "mean": summary["mean"],
                        "median": summary["median"],
                        "positive_ratio": summary["positive_ratio"],
                        "negative_ratio": summary["negative_ratio"],
                        "directional_success_ratio": summary["directional_success_ratio"],
                        "directional_fail_ratio": summary["directional_fail_ratio"],
                        "mean_directional_ret_bps": summary["mean_directional_ret_bps"],
                    }
                )


def main() -> int:
    rows = _load_rows()
    annotated = _annotate_overlaps(rows)
    _write_details(annotated)
    _write_summary(annotated)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
