from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


QUOTE_PLACEMENT_PATH = Path("reports/quote_placement_details.csv")
SPREAD_COMPARE_DIR = Path("reports/spread_dryrun_compare")
DETAILS_PATH = Path("reports/market_quality_filter_sim_details.csv")
SUMMARY_PATH = Path("reports/market_quality_filter_sim_summary.csv")
LOOKBACK_SEC = 5.0

POLICIES = (
    "A_current",
    "B_tfi_abs_lte_0p5",
    "C_tfi_abs_lte_0p6",
    "D_cancel_density_lte_1p0",
    "E_cancel_density_lte_2p0",
    "F_guard_count_lte_5",
    "G_tfi_abs_lte_0p6_and_cancel_density_lte_2p0",
    "H_tfi_abs_lte_0p6_and_guard_count_lte_5",
)

DETAIL_FIELDNAMES = [
    "policy",
    "ts",
    "leg",
    "side",
    "price",
    "tfi",
    "tfi_abs",
    "recent_cancel_aggressive_count_5s",
    "recent_cancel_aggressive_density_5s",
    "recent_quote_fade_count_5s",
    "recent_tfi_fade_count_5s",
    "recent_guard_count_5s",
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


def _load_quote_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with QUOTE_PLACEMENT_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ts = _to_float(row.get("ts"))
            if ts is None:
                continue
            row["ts"] = ts
            rows.append(row)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _load_guard_times() -> dict[str, list[float]]:
    guard_times: dict[str, list[float]] = defaultdict(list)
    for log_file in SPREAD_COMPARE_DIR.glob("*/logs/*.jsonl"):
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


def _quality_metrics(
    row: dict[str, object], guard_times: dict[str, list[float]]
) -> dict[str, object]:
    ts = float(row["ts"])
    tfi = _to_float(row.get("tfi"))
    tfi_abs = abs(tfi) if tfi is not None else None
    cancel_count = _count_recent(guard_times.get("cancel_aggressive", []), ts)
    quote_fade_count = _count_recent(guard_times.get("quote_fade", []), ts)
    tfi_fade_count = _count_recent(guard_times.get("tfi_fade", []), ts)
    return {
        "tfi_abs": tfi_abs,
        "recent_cancel_aggressive_count_5s": cancel_count,
        "recent_cancel_aggressive_density_5s": cancel_count / LOOKBACK_SEC,
        "recent_quote_fade_count_5s": quote_fade_count,
        "recent_tfi_fade_count_5s": tfi_fade_count,
        "recent_guard_count_5s": cancel_count + quote_fade_count + tfi_fade_count,
    }


def _policy_decision(policy: str, metrics: dict[str, object]) -> tuple[bool, str]:
    tfi_abs = _to_float(metrics.get("tfi_abs"))
    cancel_density = _to_float(metrics.get("recent_cancel_aggressive_density_5s"))
    guard_count = _to_float(metrics.get("recent_guard_count_5s"))

    if policy == "A_current":
        return True, ""
    if policy == "B_tfi_abs_lte_0p5":
        if tfi_abs is None or tfi_abs <= 0.5:
            return True, ""
        return False, "tfi_abs_gt_0p5"
    if policy == "C_tfi_abs_lte_0p6":
        if tfi_abs is None or tfi_abs <= 0.6:
            return True, ""
        return False, "tfi_abs_gt_0p6"
    if policy == "D_cancel_density_lte_1p0":
        if cancel_density is None or cancel_density <= 1.0:
            return True, ""
        return False, "cancel_density_gt_1p0"
    if policy == "E_cancel_density_lte_2p0":
        if cancel_density is None or cancel_density <= 2.0:
            return True, ""
        return False, "cancel_density_gt_2p0"
    if policy == "F_guard_count_lte_5":
        if guard_count is None or guard_count <= 5:
            return True, ""
        return False, "guard_count_gt_5"
    if policy == "G_tfi_abs_lte_0p6_and_cancel_density_lte_2p0":
        reasons = []
        if tfi_abs is not None and tfi_abs > 0.6:
            reasons.append("tfi_abs_gt_0p6")
        if cancel_density is not None and cancel_density > 2.0:
            reasons.append("cancel_density_gt_2p0")
        return not reasons, "+".join(reasons)
    if policy == "H_tfi_abs_lte_0p6_and_guard_count_lte_5":
        reasons = []
        if tfi_abs is not None and tfi_abs > 0.6:
            reasons.append("tfi_abs_gt_0p6")
        if guard_count is not None and guard_count > 5:
            reasons.append("guard_count_gt_5")
        return not reasons, "+".join(reasons)
    return True, ""


def _build_details() -> list[dict[str, object]]:
    quote_rows = _load_quote_rows()
    guard_times = _load_guard_times()
    details: list[dict[str, object]] = []

    for quote in quote_rows:
        metrics = _quality_metrics(quote, guard_times)
        for policy in POLICIES:
            would_allow, suppress_reason = _policy_decision(policy, metrics)
            details.append(
                {
                    "policy": policy,
                    "ts": quote["ts"],
                    "leg": quote.get("leg"),
                    "side": quote.get("side"),
                    "price": quote.get("price"),
                    "tfi": quote.get("tfi"),
                    "tfi_abs": metrics["tfi_abs"],
                    "recent_cancel_aggressive_count_5s": metrics[
                        "recent_cancel_aggressive_count_5s"
                    ],
                    "recent_cancel_aggressive_density_5s": metrics[
                        "recent_cancel_aggressive_density_5s"
                    ],
                    "recent_quote_fade_count_5s": metrics[
                        "recent_quote_fade_count_5s"
                    ],
                    "recent_tfi_fade_count_5s": metrics["recent_tfi_fade_count_5s"],
                    "recent_guard_count_5s": metrics["recent_guard_count_5s"],
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
