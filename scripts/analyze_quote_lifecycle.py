from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


BASE_DIR = Path("reports/spread_dryrun_compare")
SCENARIOS = ("15bps", "18bps")
DETAILS_PATH = BASE_DIR / "quote_lifecycle_details.csv"
SUMMARY_PATH = BASE_DIR / "quote_lifecycle_summary.csv"
END_GUARD_REASONS = {
    "cancel_aggressive",
    "quote_fade",
    "tfi_fade",
    "edge_negative_total",
}

DETAIL_FIELDNAMES = [
    "scenario",
    "quote_ts",
    "leg",
    "price",
    "qty",
    "end_ts",
    "end_reason",
    "quote_lifetime_sec",
    "cancel_aggressive_count_during_quote",
    "quote_fade_count_during_quote",
    "tfi_fade_count_during_quote",
]

SUMMARY_FIELDNAMES = [
    "scenario",
    "quote_count",
    "mean_lifetime_sec",
    "median_lifetime_sec",
    "p25_lifetime_sec",
    "p75_lifetime_sec",
    "min_lifetime_sec",
    "max_lifetime_sec",
    "cancel_aggressive_end_count",
    "quote_fade_end_count",
    "tfi_fade_end_count",
    "other_end_count",
]


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _is_quote_order(data: dict[str, object]) -> bool:
    if data.get("event") != "order_new":
        return False
    if data.get("reason") == "quote":
        return True
    return str(data.get("intent", "")).startswith("QUOTE_")


def _leg(data: dict[str, object]) -> object:
    intent = str(data.get("intent", ""))
    if intent == "QUOTE_BID":
        return "bid"
    if intent == "QUOTE_ASK":
        return "ask"
    side = data.get("side")
    if side == "buy":
        return "bid"
    if side == "sell":
        return "ask"
    return data.get("leg")


def _is_end_event(data: dict[str, object]) -> bool:
    event = data.get("event")
    if event in {"order_cancel", "order_skip"}:
        return True
    return event == "risk" and data.get("reason") in END_GUARD_REASONS


def _end_reason(data: dict[str, object]) -> str:
    event = data.get("event")
    if event in {"order_cancel", "order_skip"}:
        reason = data.get("reason")
        return f"{event}:{reason}" if reason else str(event)
    return str(data.get("reason", event))


def _load_scenario_rows(scenario: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    log_dir = BASE_DIR / scenario / "logs"
    for log_file in sorted(log_dir.glob("*.jsonl")):
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


def _build_detail_rows() -> list[dict[str, object]]:
    details: list[dict[str, object]] = []

    for scenario in SCENARIOS:
        rows = _load_scenario_rows(scenario)
        quotes = [(idx, row) for idx, row in enumerate(rows) if _is_quote_order(row)]

        for quote_idx, quote in quotes:
            quote_ts = float(quote["ts"])
            end_event: dict[str, object] | None = None
            guard_counts: Counter[str] = Counter()

            for candidate in rows[quote_idx + 1 :]:
                if candidate.get("event") == "risk":
                    reason = str(candidate.get("reason"))
                    if reason in END_GUARD_REASONS:
                        guard_counts[reason] += 1

                if _is_end_event(candidate):
                    end_event = candidate
                    break

            end_ts = _to_float(end_event.get("ts")) if end_event else None
            lifetime = end_ts - quote_ts if end_ts is not None else None

            details.append(
                {
                    "scenario": scenario,
                    "quote_ts": quote_ts,
                    "leg": _leg(quote),
                    "price": quote.get("price"),
                    "qty": quote.get("size"),
                    "end_ts": end_ts,
                    "end_reason": _end_reason(end_event) if end_event else "open_or_unknown",
                    "quote_lifetime_sec": lifetime,
                    "cancel_aggressive_count_during_quote": guard_counts.get(
                        "cancel_aggressive", 0
                    ),
                    "quote_fade_count_during_quote": guard_counts.get("quote_fade", 0),
                    "tfi_fade_count_during_quote": guard_counts.get("tfi_fade", 0),
                }
            )

    return details


def _build_summary_rows(details: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in details:
        grouped[str(row["scenario"])].append(row)

    summaries: list[dict[str, object]] = []
    for scenario in SCENARIOS:
        rows = grouped.get(scenario, [])
        lifetimes = [
            value
            for row in rows
            if (value := _to_float(row.get("quote_lifetime_sec"))) is not None
        ]
        end_counts = Counter(str(row.get("end_reason")) for row in rows)
        guard_end_count = sum(
            count
            for reason, count in end_counts.items()
            if reason
            not in {
                "cancel_aggressive",
                "quote_fade",
                "tfi_fade",
            }
        )

        summaries.append(
            {
                "scenario": scenario,
                "quote_count": len(rows),
                "mean_lifetime_sec": _mean(lifetimes),
                "median_lifetime_sec": median(lifetimes) if lifetimes else None,
                "p25_lifetime_sec": _percentile(lifetimes, 0.25),
                "p75_lifetime_sec": _percentile(lifetimes, 0.75),
                "min_lifetime_sec": min(lifetimes) if lifetimes else None,
                "max_lifetime_sec": max(lifetimes) if lifetimes else None,
                "cancel_aggressive_end_count": end_counts.get("cancel_aggressive", 0),
                "quote_fade_end_count": end_counts.get("quote_fade", 0),
                "tfi_fade_end_count": end_counts.get("tfi_fade", 0),
                "other_end_count": guard_end_count,
            }
        )

    return summaries


def main() -> int:
    details = _build_detail_rows()
    summaries = _build_summary_rows(details)

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with DETAILS_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDNAMES)
        writer.writeheader()
        writer.writerows(details)

    with SUMMARY_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(summaries)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
