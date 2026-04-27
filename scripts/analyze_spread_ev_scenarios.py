from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
REPORT_DIR = Path("reports")
DETAILS_PATH = REPORT_DIR / "spread_ev_scenarios.csv"
SUMMARY_PATH = REPORT_DIR / "spread_ev_summary.csv"
SCENARIO_HALF_BPS = (8.0, 10.0, 12.0, 14.0, 15.0, 16.0, 18.0, 20.0)


DETAIL_FIELDNAMES = [
    "ts",
    "funding_bps",
    "cost_bps",
    "adverse_buffer_bps",
    "original_expected_spread_bps",
    "original_expected_edge_bps",
    "required_half_bps",
    "scenario_half_bps",
    "scenario_expected_spread_bps",
    "scenario_edge_bps",
    "scenario_pass",
]

SUMMARY_FIELDNAMES = [
    "scenario_half_bps",
    "count",
    "pass_count",
    "pass_ratio",
    "mean_edge_bps",
    "median_edge_bps",
    "min_edge_bps",
    "max_edge_bps",
    "mean_required_half_bps",
    "median_required_half_bps",
    "p75_required_half_bps",
    "p90_required_half_bps",
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
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]

    rank = (len(sorted_values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _load_edge_rows() -> list[dict[str, float]]:
    edge_rows: list[dict[str, float]] = []
    for log_file in sorted(LOG_DIR.glob("*.jsonl")):
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("event") != "risk":
                    continue
                if data.get("reason") != "edge_negative_total":
                    continue

                ts = _to_float(data.get("ts"))
                funding_bps = _to_float(data.get("funding_bps"))
                cost_bps = _to_float(data.get("cost_bps"))
                adverse_buffer_bps = _to_float(data.get("adverse_buffer_bps"))
                expected_spread_bps = _to_float(data.get("expected_spread_bps"))
                expected_edge_bps = _to_float(data.get("expected_edge_bps"))
                expected_edge_usdt = _to_float(data.get("expected_edge_usdt"))
                if (
                    ts is None
                    or funding_bps is None
                    or cost_bps is None
                    or adverse_buffer_bps is None
                    or expected_spread_bps is None
                    or expected_edge_bps is None
                ):
                    continue

                edge_rows.append(
                    {
                        "ts": ts,
                        "funding_bps": funding_bps,
                        "cost_bps": cost_bps,
                        "adverse_buffer_bps": adverse_buffer_bps,
                        "expected_spread_bps": expected_spread_bps,
                        "expected_edge_bps": expected_edge_bps,
                        "expected_edge_usdt": expected_edge_usdt
                        if expected_edge_usdt is not None
                        else 0.0,
                    }
                )

    return edge_rows


def _build_detail_rows(edge_rows: list[dict[str, float]]) -> list[dict[str, object]]:
    detail_rows: list[dict[str, object]] = []
    for row in edge_rows:
        required_half_bps = (
            row["cost_bps"] + row["adverse_buffer_bps"] - row["funding_bps"]
        ) / 2.0

        for scenario_half_bps in SCENARIO_HALF_BPS:
            scenario_expected_spread_bps = 2.0 * scenario_half_bps
            scenario_edge_bps = (
                scenario_expected_spread_bps
                + row["funding_bps"]
                - row["cost_bps"]
                - row["adverse_buffer_bps"]
            )
            detail_rows.append(
                {
                    "ts": row["ts"],
                    "funding_bps": row["funding_bps"],
                    "cost_bps": row["cost_bps"],
                    "adverse_buffer_bps": row["adverse_buffer_bps"],
                    "original_expected_spread_bps": row["expected_spread_bps"],
                    "original_expected_edge_bps": row["expected_edge_bps"],
                    "required_half_bps": required_half_bps,
                    "scenario_half_bps": scenario_half_bps,
                    "scenario_expected_spread_bps": scenario_expected_spread_bps,
                    "scenario_edge_bps": scenario_edge_bps,
                    "scenario_pass": scenario_edge_bps >= 0.0,
                }
            )

    return detail_rows


def _build_summary_rows(detail_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[float, list[dict[str, object]]] = defaultdict(list)
    for row in detail_rows:
        grouped[float(row["scenario_half_bps"])].append(row)

    summary_rows: list[dict[str, object]] = []
    for scenario_half_bps in sorted(grouped):
        rows = grouped[scenario_half_bps]
        edge_values = [float(row["scenario_edge_bps"]) for row in rows]
        required_values = [float(row["required_half_bps"]) for row in rows]
        count = len(rows)
        pass_count = sum(1 for row in rows if row["scenario_pass"] is True)

        summary_rows.append(
            {
                "scenario_half_bps": scenario_half_bps,
                "count": count,
                "pass_count": pass_count,
                "pass_ratio": pass_count / count if count else None,
                "mean_edge_bps": _mean(edge_values),
                "median_edge_bps": median(edge_values) if edge_values else None,
                "min_edge_bps": min(edge_values) if edge_values else None,
                "max_edge_bps": max(edge_values) if edge_values else None,
                "mean_required_half_bps": _mean(required_values),
                "median_required_half_bps": median(required_values)
                if required_values
                else None,
                "p75_required_half_bps": _percentile(required_values, 0.75),
                "p90_required_half_bps": _percentile(required_values, 0.90),
            }
        )

    return summary_rows


def main() -> int:
    edge_rows = _load_edge_rows()
    detail_rows = _build_detail_rows(edge_rows)
    summary_rows = _build_summary_rows(detail_rows)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with DETAILS_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDNAMES)
        writer.writeheader()
        writer.writerows(detail_rows)

    with SUMMARY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(summary_rows)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
