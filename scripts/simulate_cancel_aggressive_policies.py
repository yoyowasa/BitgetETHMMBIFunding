from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path


INPUT_PATH = Path("reports/guard_overlap_details.csv")
DETAILS_PATH = Path("reports/cancel_aggressive_policy_sim_details.csv")
SUMMARY_PATH = Path("reports/cancel_aggressive_policy_sim_summary.csv")
RETURN_COLUMNS = ("ret_1s_bps", "ret_3s_bps", "ret_5s_bps")
POLICIES = (
    "A_current",
    "B_overlap_quote_fade_only",
    "C_overlap_or_strong_tfi",
)


def _parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _enabled(policy: str, row: dict[str, str]) -> bool:
    overlap_group = row.get("overlap_group")
    if policy == "A_current":
        return True
    if policy == "B_overlap_quote_fade_only":
        return overlap_group in {
            "overlap_quote_fade_cancel_aggressive",
            "overlap_all",
        }
    if policy == "C_overlap_or_strong_tfi":
        tfi = _parse_float(row.get("tfi"))
        return (
            overlap_group
            in {
                "overlap_quote_fade_cancel_aggressive",
                "overlap_all",
            }
            or (tfi is not None and abs(tfi) >= 0.7)
        )
    raise ValueError(f"unknown policy: {policy}")


def _direction(row: dict[str, str]) -> int | None:
    trade_side = row.get("trade_side")
    if trade_side == "sell":
        return -1
    if trade_side == "buy":
        return 1
    return None


def _directional_ret(row: dict[str, str], column: str) -> float | None:
    ret_bps = _parse_float(row.get(column))
    direction = _direction(row)
    if ret_bps is None or direction is None:
        return None
    return ret_bps * direction


def _classify(value: float | None) -> str:
    if value is None or value == 0:
        return "neutral"
    if value > 0:
        return "success"
    return "fail"


def _load_rows() -> list[dict[str, str]]:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"input not found: {INPUT_PATH}")
    with INPUT_PATH.open(encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("reason") == "cancel_aggressive"]


def _write_details(rows: list[dict[str, str]]) -> None:
    DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "policy",
        "enabled",
        "ts",
        "reason",
        "trade_side",
        "overlap_group",
        "overlap_reasons",
        "tfi",
        "mid_at_trigger",
        "trade_px",
        "ret_1s_bps",
        "ret_3s_bps",
        "ret_5s_bps",
        "directional_ret_1s_bps",
        "directional_ret_3s_bps",
        "directional_ret_5s_bps",
    ]
    with DETAILS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            for policy in POLICIES:
                writer.writerow(
                    {
                        "policy": policy,
                        "enabled": str(_enabled(policy, row)).lower(),
                        "ts": row.get("ts"),
                        "reason": row.get("reason"),
                        "trade_side": row.get("trade_side"),
                        "overlap_group": row.get("overlap_group"),
                        "overlap_reasons": row.get("overlap_reasons"),
                        "tfi": row.get("tfi"),
                        "mid_at_trigger": row.get("mid_at_trigger"),
                        "trade_px": row.get("trade_px"),
                        "ret_1s_bps": row.get("ret_1s_bps"),
                        "ret_3s_bps": row.get("ret_3s_bps"),
                        "ret_5s_bps": row.get("ret_5s_bps"),
                        "directional_ret_1s_bps": _directional_ret(row, "ret_1s_bps"),
                        "directional_ret_3s_bps": _directional_ret(row, "ret_3s_bps"),
                        "directional_ret_5s_bps": _directional_ret(row, "ret_5s_bps"),
                    }
                )


def _empty_metrics() -> dict[str, object]:
    return {
        "count": 0,
        "success_count": 0,
        "fail_count": 0,
        "neutral_count": 0,
        "directional_returns": [],
    }


def _summarize(metrics: dict[str, object]) -> dict[str, object]:
    count = int(metrics["count"])
    success_count = int(metrics["success_count"])
    fail_count = int(metrics["fail_count"])
    neutral_count = int(metrics["neutral_count"])
    directional_returns = metrics["directional_returns"]

    if count == 0:
        return {
            "count": 0,
            "success_count": 0,
            "fail_count": 0,
            "neutral_count": 0,
            "success_ratio": None,
            "fail_ratio": None,
            "neutral_ratio": None,
            "mean_directional_ret_bps": None,
            "median_directional_ret_bps": None,
        }

    return {
        "count": count,
        "success_count": success_count,
        "fail_count": fail_count,
        "neutral_count": neutral_count,
        "success_ratio": success_count / count,
        "fail_ratio": fail_count / count,
        "neutral_ratio": neutral_count / count,
        "mean_directional_ret_bps": (
            statistics.fmean(directional_returns) if directional_returns else None
        ),
        "median_directional_ret_bps": (
            statistics.median(directional_returns) if directional_returns else None
        ),
    }


def _write_summary(rows: list[dict[str, str]]) -> None:
    grouped: dict[tuple[str, bool, str], dict[str, object]] = defaultdict(_empty_metrics)

    for row in rows:
        for policy in POLICIES:
            enabled = _enabled(policy, row)
            for column in RETURN_COLUMNS:
                directional_ret = _directional_ret(row, column)
                outcome = _classify(directional_ret)
                key = (policy, enabled, column)
                metrics = grouped[key]
                metrics["count"] = int(metrics["count"]) + 1
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
        "policy",
        "enabled",
        "return_horizon",
        "count",
        "success_count",
        "fail_count",
        "neutral_count",
        "success_ratio",
        "fail_ratio",
        "neutral_ratio",
        "mean_directional_ret_bps",
        "median_directional_ret_bps",
    ]
    with SUMMARY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for (policy, enabled, column), metrics in sorted(grouped.items()):
            summary = _summarize(metrics)
            writer.writerow(
                {
                    "policy": policy,
                    "enabled": str(enabled).lower(),
                    "return_horizon": column,
                    "count": summary["count"],
                    "success_count": summary["success_count"],
                    "fail_count": summary["fail_count"],
                    "neutral_count": summary["neutral_count"],
                    "success_ratio": summary["success_ratio"],
                    "fail_ratio": summary["fail_ratio"],
                    "neutral_ratio": summary["neutral_ratio"],
                    "mean_directional_ret_bps": summary["mean_directional_ret_bps"],
                    "median_directional_ret_bps": summary["median_directional_ret_bps"],
                }
            )


def main() -> int:
    rows = _load_rows()
    _write_details(rows)
    _write_summary(rows)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
