from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path


INPUT_PATH = Path("reports/guard_forward_returns.csv")
OUTPUT_PATH = Path("reports/guard_directional_success_recent.csv")
RETURN_COLUMNS = ("ret_1s_bps", "ret_3s_bps", "ret_5s_bps")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize directional guard success using recent guard forward returns."
    )
    parser.add_argument(
        "--start-ts",
        type=float,
        default=None,
        help="Only include rows with ts greater than or equal to this value.",
    )
    return parser.parse_args()


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
        trade_side = row.get("trade_side")
        if trade_side == "sell":
            return -1
        if trade_side == "buy":
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
    }


def _include_row(row: dict[str, str], start_ts: float | None) -> bool:
    if start_ts is not None:
        ts = _parse_float(row.get("ts"))
        return ts is not None and ts >= start_ts

    return row.get("mid_at_trigger") not in (None, "")


def main() -> int:
    args = _parse_args()
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"input not found: {INPUT_PATH}")

    grouped: dict[tuple[str, str | None], dict[str, dict[str, object]]] = defaultdict(
        lambda: {column: _empty_metrics() for column in RETURN_COLUMNS}
    )

    with INPUT_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not _include_row(row, args.start_ts):
                continue

            reason = row.get("reason") or "unknown"
            trade_side = row.get("trade_side") or ""
            direction = _direction(row)
            keys = [
                (reason, None),
                (reason, trade_side),
            ]

            for column in RETURN_COLUMNS:
                ret_bps = _parse_float(row.get(column))
                outcome, directional_ret = _classify(ret_bps, direction)

                for key in keys:
                    metrics = grouped[key][column]
                    metrics["count"] = int(metrics["count"]) + 1
                    if outcome == "success":
                        metrics["success_count"] = int(metrics["success_count"]) + 1
                    elif outcome == "fail":
                        metrics["fail_count"] = int(metrics["fail_count"]) + 1
                    else:
                        metrics["neutral_count"] = int(metrics["neutral_count"]) + 1

                    if directional_ret is not None:
                        metrics["directional_returns"].append(directional_ret)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "group_type",
        "reason",
        "trade_side",
        "return_horizon",
        "count",
        "success_count",
        "fail_count",
        "neutral_count",
        "success_ratio",
        "fail_ratio",
        "neutral_ratio",
        "mean_directional_ret_bps",
    ]
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for (reason, trade_side), metrics_by_horizon in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], "" if item[0][1] is None else item[0][1]),
        ):
            group_type = "reason_trade_side" if trade_side is not None else "reason"
            normalized_trade_side = "" if trade_side is None else trade_side
            for column in RETURN_COLUMNS:
                summary = _summarize(metrics_by_horizon[column])
                writer.writerow(
                    {
                        "group_type": group_type,
                        "reason": reason,
                        "trade_side": normalized_trade_side,
                        "return_horizon": column,
                        "count": summary["count"],
                        "success_count": summary["success_count"],
                        "fail_count": summary["fail_count"],
                        "neutral_count": summary["neutral_count"],
                        "success_ratio": summary["success_ratio"],
                        "fail_ratio": summary["fail_ratio"],
                        "neutral_ratio": summary["neutral_ratio"],
                        "mean_directional_ret_bps": summary["mean_directional_ret_bps"],
                    }
                )

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
