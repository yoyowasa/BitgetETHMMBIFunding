from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


QUOTE_PLACEMENT_PATH = Path("reports/quote_placement_details.csv")
SPREAD_COMPARE_DIR = Path("reports/spread_dryrun_compare")
DETAILS_PATH = Path("reports/combined_quote_filter_sim_details.csv")
SUMMARY_PATH = Path("reports/combined_quote_filter_sim_summary.csv")
LOOKBACK_SEC = 5.0

POLICIES = (
    "A_current",
    "B_one_sided_tfi_0p7",
    "C_market_tfi_abs_lte_0p6",
    "D_one_sided_0p7_plus_tfi_abs_lte_0p6",
    "E_one_sided_0p7_plus_cancel_density_lte_2p0",
    "F_one_sided_0p7_plus_guard_count_lte_5",
    "G_one_sided_0p7_plus_tfi_abs_lte_0p6_or_cancel_density_lte_2p0",
)

DETAIL_FIELDNAMES = [
    "policy",
    "scenario",
    "ts",
    "leg",
    "side",
    "price",
    "tfi",
    "tfi_abs",
    "recent_cancel_aggressive_density_5s",
    "recent_guard_count_5s",
    "one_sided_suppressed",
    "market_quality_allowed",
    "would_allow",
    "suppress_reason",
    "quote_lifetime_sec",
    "end_reason",
    "cancel_aggressive_count_during_quote",
    "quote_fade_count_during_quote",
    "tfi_fade_count_during_quote",
]

SUMMARY_FIELDNAMES = [
    "policy",
    "leg",
    "quote_count",
    "allowed_count",
    "suppressed_count",
    "allowed_ratio",
    "suppressed_ratio",
    "mean_lifetime_all",
    "median_lifetime_all",
    "mean_lifetime_allowed",
    "median_lifetime_allowed",
    "cancel_aggressive_end_all",
    "cancel_aggressive_end_allowed",
    "quote_fade_end_all",
    "quote_fade_end_allowed",
    "tfi_fade_end_all",
    "tfi_fade_end_allowed",
    "mean_tfi_abs_allowed",
    "median_tfi_abs_allowed",
    "mean_cancel_density_allowed",
    "median_cancel_density_allowed",
    "mean_guard_count_allowed",
    "median_guard_count_allowed",
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


def _load_18bps_quotes() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with QUOTE_PLACEMENT_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("base_half_spread_bps") != "18.0":
                continue
            if row.get("min_half_spread_bps") != "18.0":
                continue
            if row.get("one_sided_quote_policy") != "current":
                continue
            ts = _to_float(row.get("ts"))
            if ts is None:
                continue
            row["ts"] = ts
            rows.append(row)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _load_guard_times() -> dict[str, list[float]]:
    guard_times: dict[str, list[float]] = defaultdict(list)
    # Use the original 18bps DRY_RUN snapshot to avoid mixing other test windows.
    for log_file in (SPREAD_COMPARE_DIR / "18bps" / "logs").glob("*.jsonl"):
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("event") != "risk":
                    continue
                reason = str(data.get("reason"))
                if reason not in {"cancel_aggressive", "quote_fade", "tfi_fade"}:
                    continue
                ts = _to_float(data.get("ts"))
                if ts is None:
                    continue
                guard_times[reason].append(ts)
    for reason in guard_times:
        guard_times[reason].sort()
    return guard_times


def _count_recent(times: list[float], ts: float) -> int:
    start = ts - LOOKBACK_SEC
    return sum(1 for value in times if start <= value < ts)


def _quality(row: dict[str, object], guard_times: dict[str, list[float]]) -> dict[str, object]:
    ts = float(row["ts"])
    tfi = _to_float(row.get("tfi"))
    tfi_abs = abs(tfi) if tfi is not None else None
    cancel_count = _count_recent(guard_times.get("cancel_aggressive", []), ts)
    quote_fade_count = _count_recent(guard_times.get("quote_fade", []), ts)
    tfi_fade_count = _count_recent(guard_times.get("tfi_fade", []), ts)
    return {
        "tfi": tfi,
        "tfi_abs": tfi_abs,
        "cancel_density": cancel_count / LOOKBACK_SEC,
        "guard_count": cancel_count + quote_fade_count + tfi_fade_count,
    }


def _one_sided_suppressed(leg: str, tfi: float | None) -> bool:
    if tfi is None:
        return False
    if leg == "bid" and tfi <= -0.7:
        return True
    if leg == "ask" and tfi >= 0.7:
        return True
    return False


def _policy_decision(
    policy: str, leg: str, quality: dict[str, object]
) -> tuple[bool, bool, bool, str]:
    tfi_abs = _to_float(quality.get("tfi_abs"))
    cancel_density = _to_float(quality.get("cancel_density"))
    guard_count = _to_float(quality.get("guard_count"))
    one_sided = _one_sided_suppressed(leg, _to_float(quality.get("tfi")))
    market_allowed = True
    reasons: list[str] = []

    if policy == "A_current":
        one_sided = False
    elif policy == "B_one_sided_tfi_0p7":
        pass
    elif policy == "C_market_tfi_abs_lte_0p6":
        one_sided = False
        market_allowed = tfi_abs is None or tfi_abs <= 0.6
        if not market_allowed:
            reasons.append("tfi_abs_gt_0p6")
    elif policy == "D_one_sided_0p7_plus_tfi_abs_lte_0p6":
        market_allowed = tfi_abs is None or tfi_abs <= 0.6
        if not market_allowed:
            reasons.append("tfi_abs_gt_0p6")
    elif policy == "E_one_sided_0p7_plus_cancel_density_lte_2p0":
        market_allowed = cancel_density is None or cancel_density <= 2.0
        if not market_allowed:
            reasons.append("cancel_density_gt_2p0")
    elif policy == "F_one_sided_0p7_plus_guard_count_lte_5":
        market_allowed = guard_count is None or guard_count <= 5
        if not market_allowed:
            reasons.append("guard_count_gt_5")
    elif policy == "G_one_sided_0p7_plus_tfi_abs_lte_0p6_or_cancel_density_lte_2p0":
        tfi_ok = tfi_abs is None or tfi_abs <= 0.6
        density_ok = cancel_density is None or cancel_density <= 2.0
        market_allowed = tfi_ok or density_ok
        if not market_allowed:
            reasons.append("tfi_abs_gt_0p6_and_cancel_density_gt_2p0")

    if one_sided:
        reasons.append("one_sided_suppressed")

    would_allow = market_allowed and not one_sided
    return one_sided, market_allowed, would_allow, "+".join(reasons)


def _build_details() -> list[dict[str, object]]:
    quotes = _load_18bps_quotes()
    guard_times = _load_guard_times()
    details: list[dict[str, object]] = []

    for quote in quotes:
        quality = _quality(quote, guard_times)
        for policy in POLICIES:
            one_sided, market_allowed, would_allow, suppress_reason = _policy_decision(
                policy, str(quote.get("leg")), quality
            )
            details.append(
                {
                    "policy": policy,
                    "scenario": "18bps",
                    "ts": quote["ts"],
                    "leg": quote.get("leg"),
                    "side": quote.get("side"),
                    "price": quote.get("price"),
                    "tfi": quality["tfi"],
                    "tfi_abs": quality["tfi_abs"],
                    "recent_cancel_aggressive_density_5s": quality["cancel_density"],
                    "recent_guard_count_5s": quality["guard_count"],
                    "one_sided_suppressed": one_sided,
                    "market_quality_allowed": market_allowed,
                    "would_allow": would_allow,
                    "suppress_reason": suppress_reason,
                    "quote_lifetime_sec": quote.get("quote_lifetime_sec"),
                    "end_reason": quote.get("end_reason"),
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


def _float_values(rows: list[dict[str, object]], key: str) -> list[float]:
    return [value for row in rows if (value := _to_float(row.get(key))) is not None]


def _end_count(rows: list[dict[str, object]], reason: str) -> int:
    return sum(1 for row in rows if row.get("end_reason") == reason)


def _summary_row(policy: str, leg: str, rows: list[dict[str, object]]) -> dict[str, object]:
    allowed_rows = [row for row in rows if str(row.get("would_allow")) == "True"]
    quote_count = len(rows)
    allowed_count = len(allowed_rows)
    lifetime_all = _float_values(rows, "quote_lifetime_sec")
    lifetime_allowed = _float_values(allowed_rows, "quote_lifetime_sec")
    tfi_abs_allowed = _float_values(allowed_rows, "tfi_abs")
    cancel_density_allowed = _float_values(
        allowed_rows, "recent_cancel_aggressive_density_5s"
    )
    guard_count_allowed = _float_values(allowed_rows, "recent_guard_count_5s")
    return {
        "policy": policy,
        "leg": leg,
        "quote_count": quote_count,
        "allowed_count": allowed_count,
        "suppressed_count": quote_count - allowed_count,
        "allowed_ratio": allowed_count / quote_count if quote_count else None,
        "suppressed_ratio": (quote_count - allowed_count) / quote_count
        if quote_count
        else None,
        "mean_lifetime_all": _mean(lifetime_all),
        "median_lifetime_all": median(lifetime_all) if lifetime_all else None,
        "mean_lifetime_allowed": _mean(lifetime_allowed),
        "median_lifetime_allowed": median(lifetime_allowed)
        if lifetime_allowed
        else None,
        "cancel_aggressive_end_all": _end_count(rows, "cancel_aggressive"),
        "cancel_aggressive_end_allowed": _end_count(allowed_rows, "cancel_aggressive"),
        "quote_fade_end_all": _end_count(rows, "quote_fade"),
        "quote_fade_end_allowed": _end_count(allowed_rows, "quote_fade"),
        "tfi_fade_end_all": _end_count(rows, "tfi_fade"),
        "tfi_fade_end_allowed": _end_count(allowed_rows, "tfi_fade"),
        "mean_tfi_abs_allowed": _mean(tfi_abs_allowed),
        "median_tfi_abs_allowed": median(tfi_abs_allowed)
        if tfi_abs_allowed
        else None,
        "mean_cancel_density_allowed": _mean(cancel_density_allowed),
        "median_cancel_density_allowed": median(cancel_density_allowed)
        if cancel_density_allowed
        else None,
        "mean_guard_count_allowed": _mean(guard_count_allowed),
        "median_guard_count_allowed": median(guard_count_allowed)
        if guard_count_allowed
        else None,
    }


def _build_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    grouped_all: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in details:
        policy = str(row["policy"])
        leg = str(row.get("leg") or "")
        grouped[(policy, leg)].append(row)
        grouped_all[policy].append(row)

    summary: list[dict[str, object]] = []
    for policy in POLICIES:
        for leg in ("bid", "ask"):
            summary.append(_summary_row(policy, leg, grouped.get((policy, leg), [])))
        summary.append(_summary_row(policy, "all", grouped_all.get(policy, [])))
    return summary


def main() -> int:
    details = _build_details()
    summary = _build_summary(details)

    DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
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
