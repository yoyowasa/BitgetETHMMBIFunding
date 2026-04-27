from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path


INPUT_PATH = Path("reports/guard_forward_returns.csv")
OUTPUT_PATH = Path("reports/guard_forward_summary.csv")
RETURN_COLUMNS = ("ret_1s_bps", "ret_3s_bps", "ret_5s_bps")


def _parse_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _summarize(values: list[float]) -> dict[str, object]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "positive_ratio": None,
            "negative_ratio": None,
        }

    count = len(values)
    positive = sum(1 for value in values if value > 0)
    negative = sum(1 for value in values if value < 0)
    return {
        "count": count,
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "positive_ratio": positive / count,
        "negative_ratio": negative / count,
    }


def main() -> int:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"input not found: {INPUT_PATH}")

    grouped: dict[tuple[str, str | None], dict[str, list[float]]] = defaultdict(
        lambda: {column: [] for column in RETURN_COLUMNS}
    )

    with INPUT_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reason = row.get("reason") or "unknown"
            trade_side = row.get("trade_side") or ""
            keys = [
                (reason, None),
                (reason, trade_side),
            ]
            for column in RETURN_COLUMNS:
                value = _parse_float(row.get(column))
                if value is None:
                    continue
                for key in keys:
                    grouped[key][column].append(value)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "group_type",
        "reason",
        "trade_side",
        "return_horizon",
        "count",
        "mean",
        "median",
        "positive_ratio",
        "negative_ratio",
    ]
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for (reason, trade_side), metrics in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], "" if item[0][1] is None else item[0][1]),
        ):
            group_type = "reason_trade_side" if trade_side is not None else "reason"
            normalized_trade_side = "" if trade_side is None else trade_side
            for column in RETURN_COLUMNS:
                summary = _summarize(metrics[column])
                writer.writerow(
                    {
                        "group_type": group_type,
                        "reason": reason,
                        "trade_side": normalized_trade_side,
                        "return_horizon": column,
                        "count": summary["count"],
                        "mean": summary["mean"],
                        "median": summary["median"],
                        "positive_ratio": summary["positive_ratio"],
                        "negative_ratio": summary["negative_ratio"],
                    }
                )

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
