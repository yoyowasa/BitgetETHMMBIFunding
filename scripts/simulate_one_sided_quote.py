from __future__ import annotations

import csv
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from statistics import median


BASE_DIR = Path("reports/spread_dryrun_compare")
QUOTE_LIFECYCLE_PATH = BASE_DIR / "quote_lifecycle_details.csv"
DENSITY_DETAILS_PATH = BASE_DIR / "cancel_aggressive_density_details.csv"
DETAILS_PATH = BASE_DIR / "one_sided_quote_sim_details.csv"
SUMMARY_PATH = BASE_DIR / "one_sided_quote_sim_summary.csv"

POLICIES = {
    "A_current": None,
    "B_tfi_0p6": 0.6,
    "C_tfi_0p7": 0.7,
    "D_tfi_0p8": 0.8,
}

DETAIL_FIELDNAMES = [
    "scenario",
    "policy",
    "quote_ts",
    "leg",
    "tfi_at_quote",
    "would_suppress",
    "suppress_reason",
    "original_end_reason",
    "quote_lifetime_sec",
    "cancel_aggressive_count_during_quote",
    "quote_fade_count_during_quote",
    "tfi_fade_count_during_quote",
]

SUMMARY_FIELDNAMES = [
    "scenario",
    "policy",
    "leg",
    "quote_count",
    "would_suppress_count",
    "would_suppress_ratio",
    "kept_quote_count",
    "kept_quote_ratio",
    "mean_lifetime_sec_all",
    "median_lifetime_sec_all",
    "mean_lifetime_sec_kept",
    "median_lifetime_sec_kept",
    "cancel_aggressive_end_all",
    "cancel_aggressive_end_kept",
    "quote_fade_end_all",
    "quote_fade_end_kept",
    "tfi_fade_end_all",
    "tfi_fade_end_kept",
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


def _load_tfi_series() -> dict[str, list[tuple[float, float]]]:
    series: dict[str, list[tuple[float, float]]] = defaultdict(list)
    with DENSITY_DETAILS_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ts = _to_float(row.get("ts"))
            tfi = _to_float(row.get("tfi"))
            if ts is None or tfi is None:
                continue
            series[row["scenario"]].append((ts, tfi))

    for scenario in series:
        series[scenario].sort(key=lambda item: item[0])
    return series


def _nearest_tfi(
    series_by_scenario: dict[str, list[tuple[float, float]]],
    scenario: str,
    quote_ts: float,
) -> float | None:
    series = series_by_scenario.get(scenario, [])
    if not series:
        return None

    times = [item[0] for item in series]
    idx = bisect_right(times, quote_ts) - 1
    if idx >= 0:
        return series[idx][1]

    # If no prior sample exists, use the nearest future sample as a fallback.
    return series[0][1]


def _suppression(leg: str, tfi: float | None, threshold: float | None) -> tuple[bool, str]:
    if threshold is None:
        return False, ""
    if tfi is None:
        return False, "unknown_tfi"
    if leg == "bid" and tfi <= -threshold:
        return True, f"tfi<={-threshold}"
    if leg == "ask" and tfi >= threshold:
        return True, f"tfi>={threshold}"
    return False, ""


def _load_quote_rows() -> list[dict[str, str]]:
    with QUOTE_LIFECYCLE_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _build_details() -> list[dict[str, object]]:
    tfi_series = _load_tfi_series()
    quote_rows = _load_quote_rows()
    details: list[dict[str, object]] = []

    for quote in quote_rows:
        scenario = quote["scenario"]
        quote_ts = _to_float(quote.get("quote_ts"))
        if quote_ts is None:
            continue
        leg = quote.get("leg", "")
        tfi_at_quote = _nearest_tfi(tfi_series, scenario, quote_ts)

        for policy, threshold in POLICIES.items():
            would_suppress, suppress_reason = _suppression(leg, tfi_at_quote, threshold)
            details.append(
                {
                    "scenario": scenario,
                    "policy": policy,
                    "quote_ts": quote_ts,
                    "leg": leg,
                    "tfi_at_quote": tfi_at_quote,
                    "would_suppress": would_suppress,
                    "suppress_reason": suppress_reason,
                    "original_end_reason": quote.get("end_reason"),
                    "quote_lifetime_sec": quote.get("quote_lifetime_sec"),
                    "cancel_aggressive_count_during_quote": quote.get(
                        "cancel_aggressive_count_during_quote"
                    ),
                    "quote_fade_count_during_quote": quote.get(
                        "quote_fade_count_during_quote"
                    ),
                    "tfi_fade_count_during_quote": quote.get(
                        "tfi_fade_count_during_quote"
                    ),
                }
            )

    return details


def _end_count(rows: list[dict[str, object]], reason: str) -> int:
    return sum(1 for row in rows if row.get("original_end_reason") == reason)


def _summary_row(
    scenario: str, policy: str, leg: str, rows: list[dict[str, object]]
) -> dict[str, object]:
    kept_rows = [row for row in rows if str(row.get("would_suppress")) != "True"]
    all_lifetimes = [
        value
        for row in rows
        if (value := _to_float(row.get("quote_lifetime_sec"))) is not None
    ]
    kept_lifetimes = [
        value
        for row in kept_rows
        if (value := _to_float(row.get("quote_lifetime_sec"))) is not None
    ]
    quote_count = len(rows)
    would_suppress_count = quote_count - len(kept_rows)
    return {
        "scenario": scenario,
        "policy": policy,
        "leg": leg,
        "quote_count": quote_count,
        "would_suppress_count": would_suppress_count,
        "would_suppress_ratio": would_suppress_count / quote_count
        if quote_count
        else None,
        "kept_quote_count": len(kept_rows),
        "kept_quote_ratio": len(kept_rows) / quote_count if quote_count else None,
        "mean_lifetime_sec_all": _mean(all_lifetimes),
        "median_lifetime_sec_all": median(all_lifetimes) if all_lifetimes else None,
        "mean_lifetime_sec_kept": _mean(kept_lifetimes),
        "median_lifetime_sec_kept": median(kept_lifetimes)
        if kept_lifetimes
        else None,
        "cancel_aggressive_end_all": _end_count(rows, "cancel_aggressive"),
        "cancel_aggressive_end_kept": _end_count(kept_rows, "cancel_aggressive"),
        "quote_fade_end_all": _end_count(rows, "quote_fade"),
        "quote_fade_end_kept": _end_count(kept_rows, "quote_fade"),
        "tfi_fade_end_all": _end_count(rows, "tfi_fade"),
        "tfi_fade_end_kept": _end_count(kept_rows, "tfi_fade"),
    }


def _build_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    grouped_all: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for row in details:
        scenario = str(row["scenario"])
        policy = str(row["policy"])
        leg = str(row["leg"])
        grouped[(scenario, policy, leg)].append(row)
        grouped_all[(scenario, policy)].append(row)

    summary: list[dict[str, object]] = []
    scenarios = sorted({str(row["scenario"]) for row in details})
    for scenario in scenarios:
        for policy in POLICIES:
            for leg in ("bid", "ask"):
                summary.append(
                    _summary_row(
                        scenario,
                        policy,
                        leg,
                        grouped.get((scenario, policy, leg), []),
                    )
                )
            summary.append(
                _summary_row(
                    scenario,
                    policy,
                    "all",
                    grouped_all.get((scenario, policy), []),
                )
            )

    return summary


def main() -> int:
    details = _build_details()
    summary = _build_summary(details)

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
