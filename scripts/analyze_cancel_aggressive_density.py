from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


BASE_DIR = Path("reports/spread_dryrun_compare")
LIFECYCLE_PATH = BASE_DIR / "quote_lifecycle_details.csv"
DETAILS_PATH = BASE_DIR / "cancel_aggressive_density_details.csv"
SUMMARY_PATH = BASE_DIR / "cancel_aggressive_density_summary.csv"
SCENARIOS = ("15bps", "18bps")


DETAIL_FIELDNAMES = [
    "scenario",
    "ts",
    "reason",
    "trade_side",
    "trade_px",
    "mid_perp",
    "bid_px",
    "ask_px",
    "spread_bps",
    "tfi",
    "in_quote",
    "quote_leg",
    "quote_lifetime_sec",
]

SUMMARY_FIELDNAMES = [
    "scenario",
    "in_quote",
    "trade_side",
    "event_count",
    "duration_sec",
    "events_per_sec",
    "mean_tfi",
    "median_tfi",
    "mean_spread_bps",
    "median_spread_bps",
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


def _load_quote_intervals() -> dict[str, list[dict[str, object]]]:
    intervals: dict[str, list[dict[str, object]]] = defaultdict(list)
    with LIFECYCLE_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            quote_ts = _to_float(row.get("quote_ts"))
            end_ts = _to_float(row.get("end_ts"))
            if quote_ts is None or end_ts is None or end_ts < quote_ts:
                continue
            intervals[row["scenario"]].append(
                {
                    "start": quote_ts,
                    "end": end_ts,
                    "leg": row.get("leg"),
                    "lifetime": _to_float(row.get("quote_lifetime_sec")),
                }
            )

    for scenario in intervals:
        intervals[scenario].sort(key=lambda item: float(item["start"]))

    return intervals


def _load_scenario_rows(scenario: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for log_file in sorted((BASE_DIR / scenario / "logs").glob("*.jsonl")):
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
    rows.sort(key=lambda item: float(item["ts"]))
    return rows


def _interval_duration(intervals: list[dict[str, object]]) -> float:
    # Merge overlapping bid/ask quote intervals so paired quotes do not double count time.
    merged: list[list[float]] = []
    for interval in intervals:
        start = float(interval["start"])
        end = float(interval["end"])
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
            continue
        merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _find_interval(
    intervals: list[dict[str, object]], ts: float
) -> dict[str, object] | None:
    for interval in intervals:
        if float(interval["start"]) <= ts <= float(interval["end"]):
            return interval
        if float(interval["start"]) > ts:
            break
    return None


def _build_details(
    intervals_by_scenario: dict[str, list[dict[str, object]]]
) -> tuple[list[dict[str, object]], dict[str, dict[str, float]]]:
    details: list[dict[str, object]] = []
    durations: dict[str, dict[str, float]] = {}

    for scenario in SCENARIOS:
        rows = _load_scenario_rows(scenario)
        intervals = intervals_by_scenario.get(scenario, [])
        all_ts = [float(row["ts"]) for row in rows]
        total_duration = max(all_ts) - min(all_ts) if len(all_ts) >= 2 else 0.0
        quote_duration = _interval_duration(intervals)
        non_quote_duration = max(total_duration - quote_duration, 0.0)
        durations[scenario] = {
            "true": quote_duration,
            "false": non_quote_duration,
            "all_quote": quote_duration,
            "all_non_quote": non_quote_duration,
        }

        for row in rows:
            if row.get("event") != "risk" or row.get("reason") != "cancel_aggressive":
                continue

            ts = float(row["ts"])
            interval = _find_interval(intervals, ts)
            details.append(
                {
                    "scenario": scenario,
                    "ts": ts,
                    "reason": row.get("reason"),
                    "trade_side": row.get("trade_side"),
                    "trade_px": row.get("trade_px"),
                    "mid_perp": row.get("mid_perp"),
                    "bid_px": row.get("bid_px"),
                    "ask_px": row.get("ask_px"),
                    "spread_bps": row.get("spread_bps"),
                    "tfi": row.get("tfi"),
                    "in_quote": interval is not None,
                    "quote_leg": interval.get("leg") if interval else None,
                    "quote_lifetime_sec": interval.get("lifetime") if interval else None,
                }
            )

    return details, durations


def _summary_row(
    scenario: str,
    in_quote: str,
    trade_side: str,
    rows: list[dict[str, object]],
    duration_sec: float,
) -> dict[str, object]:
    tfi_values = [
        value for row in rows if (value := _to_float(row.get("tfi"))) is not None
    ]
    spread_values = [
        value
        for row in rows
        if (value := _to_float(row.get("spread_bps"))) is not None
    ]
    event_count = len(rows)
    return {
        "scenario": scenario,
        "in_quote": in_quote,
        "trade_side": trade_side,
        "event_count": event_count,
        "duration_sec": duration_sec,
        "events_per_sec": event_count / duration_sec if duration_sec > 0 else None,
        "mean_tfi": _mean(tfi_values),
        "median_tfi": median(tfi_values) if tfi_values else None,
        "mean_spread_bps": _mean(spread_values),
        "median_spread_bps": median(spread_values) if spread_values else None,
    }


def _build_summary(
    details: list[dict[str, object]], durations: dict[str, dict[str, float]]
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    all_grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for row in details:
        scenario = str(row["scenario"])
        in_quote = str(row["in_quote"]).lower()
        trade_side = str(row.get("trade_side") or "")
        grouped[(scenario, in_quote, trade_side)].append(row)
        all_grouped[(scenario, in_quote)].append(row)

    summary: list[dict[str, object]] = []
    for scenario in SCENARIOS:
        for in_quote in ("true", "false"):
            for trade_side in sorted(
                {
                    key[2]
                    for key in grouped
                    if key[0] == scenario and key[1] == in_quote
                }
            ):
                summary.append(
                    _summary_row(
                        scenario,
                        in_quote,
                        trade_side,
                        grouped[(scenario, in_quote, trade_side)],
                        durations[scenario][in_quote],
                    )
                )

        summary.append(
            _summary_row(
                scenario,
                "all_quote",
                "all",
                all_grouped[(scenario, "true")],
                durations[scenario]["all_quote"],
            )
        )
        summary.append(
            _summary_row(
                scenario,
                "all_non_quote",
                "all",
                all_grouped[(scenario, "false")],
                durations[scenario]["all_non_quote"],
            )
        )

    return summary


def main() -> int:
    intervals_by_scenario = _load_quote_intervals()
    details, durations = _build_details(intervals_by_scenario)
    summary = _build_summary(details, durations)

    BASE_DIR.mkdir(parents=True, exist_ok=True)
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
