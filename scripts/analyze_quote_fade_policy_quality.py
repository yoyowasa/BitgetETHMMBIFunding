from __future__ import annotations

import csv
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from statistics import median


BASE_DIR = Path("reports/quote_fade_policy_compare")
POLICIES = ("current", "threshold_8bps", "threshold_10bps")
DETAILS_PATH = BASE_DIR / "quote_fade_policy_quality_details.csv"
SUMMARY_PATH = BASE_DIR / "quote_fade_policy_quality_summary.csv"
END_REASONS = {"cancel_aggressive", "quote_fade", "tfi_fade", "edge_negative_total"}
HORIZONS = (1, 3, 5)
DETAIL_FIELDNAMES = [
    "policy",
    "quote_ts",
    "end_ts",
    "end_reason",
    "leg",
    "side",
    "price",
    "quote_lifetime_sec",
    "tfi",
    "mid_move_bps",
    "quote_fade_policy",
    "quote_fade_suppressed_nearby",
    "ret_1s_bps",
    "ret_3s_bps",
    "ret_5s_bps",
    "directional_ret_1s_bps",
    "directional_ret_3s_bps",
    "directional_ret_5s_bps",
    "danger_after_suppression_1s",
    "danger_after_suppression_3s",
    "danger_after_suppression_5s",
]
SUMMARY_FIELDNAMES = [
    "policy",
    "end_reason",
    "leg",
    "quote_count",
    "mean_lifetime_sec",
    "median_lifetime_sec",
    "p75_lifetime_sec",
    "p90_lifetime_sec",
    "quote_fade_end_count",
    "order_cancel_quote_count",
    "quote_fade_suppressed_count",
    "mean_directional_ret_1s_bps",
    "mean_directional_ret_3s_bps",
    "mean_directional_ret_5s_bps",
    "danger_after_suppression_ratio_1s",
    "danger_after_suppression_ratio_3s",
    "danger_after_suppression_ratio_5s",
]


def _to_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _load_rows(policy: str) -> list[dict[str, object]]:
    path = BASE_DIR / policy / "logs" / "excerpt.jsonl"
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _to_float(row.get("ts"))
            if ts is None:
                continue
            row["ts"] = ts
            rows.append(row)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _raw_quote_fade_suppressed_count(policy: str) -> int:
    return sum(
        1
        for row in _load_rows(policy)
        if row.get("event") == "risk" and row.get("reason") == "quote_fade_suppressed"
    )


def _is_quote(row: dict[str, object]) -> bool:
    if row.get("event") != "order_new":
        return False
    return row.get("reason") == "quote" or str(row.get("intent", "")).startswith("QUOTE_")


def _is_end(row: dict[str, object]) -> bool:
    if row.get("event") in {"order_cancel", "order_skip"}:
        return True
    return row.get("event") == "risk" and row.get("reason") in END_REASONS


def _end_reason(row: dict[str, object] | None) -> str:
    if row is None:
        return "open_or_unknown"
    if row.get("event") in {"order_cancel", "order_skip"}:
        return f"{row.get('event')}:{row.get('reason')}"
    return str(row.get("reason"))


def _leg(row: dict[str, object]) -> str | None:
    intent = str(row.get("intent", ""))
    if intent == "QUOTE_BID":
        return "bid"
    if intent == "QUOTE_ASK":
        return "ask"
    side = row.get("side")
    if side == "buy":
        return "bid"
    if side == "sell":
        return "ask"
    return None


def _tick_series(rows: list[dict[str, object]]) -> tuple[list[float], list[dict[str, object]]]:
    ticks = [
        row
        for row in rows
        if row.get("event") == "tick" and _to_float(row.get("mid_perp")) is not None
    ]
    ticks.sort(key=lambda row: float(row["ts"]))
    return [float(row["ts"]) for row in ticks], ticks


def _tick_at_or_before(
    times: list[float],
    ticks: list[dict[str, object]],
    ts: float,
) -> dict[str, object] | None:
    idx = bisect_right(times, ts) - 1
    return ticks[idx] if idx >= 0 else None


def _tick_at_or_after(
    times: list[float],
    ticks: list[dict[str, object]],
    ts: float,
) -> dict[str, object] | None:
    idx = bisect_left(times, ts)
    return ticks[idx] if idx < len(ticks) else None


def _ret_bps(mid_start: float | None, mid_end: float | None) -> float | None:
    if mid_start is None or mid_end is None or mid_start <= 0:
        return None
    return (mid_end - mid_start) / mid_start * 10000.0


def _mid_move_bps(row: dict[str, object] | None) -> float | None:
    if row is None:
        return None
    existing = _to_float(row.get("mid_move_bps"))
    if existing is not None:
        return existing
    mid = _to_float(row.get("mid_perp"))
    prev = _to_float(row.get("mid_100ms_ago"))
    if mid is None or prev is None or prev <= 0:
        return None
    return (mid - prev) / prev * 10000.0


def _nearest_quote_fade_suppression(
    ts: float,
    suppressions: list[dict[str, object]],
) -> dict[str, object] | None:
    if not suppressions:
        return None
    times = [float(row["ts"]) for row in suppressions]
    idx = bisect_left(times, ts)
    candidates = []
    if idx < len(suppressions):
        candidates.append(suppressions[idx])
    if idx > 0:
        candidates.append(suppressions[idx - 1])
    if not candidates:
        return None
    nearest = min(candidates, key=lambda row: abs(float(row["ts"]) - ts))
    return nearest if abs(float(nearest["ts"]) - ts) <= 1.0 else None


def _danger_sign(row: dict[str, object] | None) -> int | None:
    move = _mid_move_bps(row)
    if move is None or move == 0:
        return None
    return 1 if move > 0 else -1


def _build_policy_details(policy: str) -> list[dict[str, object]]:
    rows = _load_rows(policy)
    tick_times, ticks = _tick_series(rows)
    suppressions = [
        row
        for row in rows
        if row.get("event") == "risk" and row.get("reason") == "quote_fade_suppressed"
    ]
    suppressions.sort(key=lambda row: float(row["ts"]))
    quotes = [(idx, row) for idx, row in enumerate(rows) if _is_quote(row)]
    details: list[dict[str, object]] = []

    for quote_idx, quote in quotes:
        end_event = next((row for row in rows[quote_idx + 1 :] if _is_end(row)), None)
        quote_ts = float(quote["ts"])
        end_ts = _to_float(end_event.get("ts")) if end_event else None
        end_reason = _end_reason(end_event)
        suppression = _nearest_quote_fade_suppression(
            end_ts if end_ts is not None else quote_ts,
            suppressions,
        )
        signal = suppression or end_event
        end_tick = _tick_at_or_before(tick_times, ticks, end_ts or quote_ts)
        mid_at_end = _to_float(end_event.get("mid_perp")) if isinstance(end_event, dict) else None
        if mid_at_end is None and end_tick is not None:
            mid_at_end = _to_float(end_tick.get("mid_perp"))
        sign = _danger_sign(signal)
        row = {
            "policy": policy,
            "quote_ts": quote_ts,
            "end_ts": end_ts,
            "end_reason": end_reason,
            "leg": _leg(quote),
            "side": quote.get("side"),
            "price": quote.get("price"),
            "quote_lifetime_sec": end_ts - quote_ts if end_ts is not None else None,
            "tfi": signal.get("tfi") if isinstance(signal, dict) else None,
            "mid_move_bps": _mid_move_bps(signal if isinstance(signal, dict) else None),
            "quote_fade_policy": (
                signal.get("quote_fade_policy") if isinstance(signal, dict) else policy
            ),
            "quote_fade_suppressed_nearby": suppression is not None,
        }
        for horizon in HORIZONS:
            after_tick = _tick_at_or_after(tick_times, ticks, (end_ts or quote_ts) + horizon)
            mid_after = _to_float(after_tick.get("mid_perp")) if after_tick else None
            ret = _ret_bps(mid_at_end, mid_after)
            directional = ret * sign if ret is not None and sign is not None else None
            row[f"ret_{horizon}s_bps"] = ret
            row[f"directional_ret_{horizon}s_bps"] = directional
            row[f"danger_after_suppression_{horizon}s"] = (
                directional is not None and directional > 0
            )
        details.append(row)
    return details


def _float_values(rows: list[dict[str, object]], key: str) -> list[float]:
    return [value for row in rows if (value := _to_float(row.get(key))) is not None]


def _summary_row(
    policy: str,
    end_reason: str,
    leg: str,
    rows: list[dict[str, object]],
    policy_all_rows: list[dict[str, object]],
    raw_suppressed_count: int | None = None,
) -> dict[str, object]:
    lifetimes = _float_values(rows, "quote_lifetime_sec")
    quote_fade_end_count = sum(1 for row in rows if row.get("end_reason") == "quote_fade")
    order_cancel_count = sum(
        1 for row in rows if row.get("end_reason") == "order_cancel:quote"
    )
    suppressed_count = sum(
        1 for row in policy_all_rows if row.get("quote_fade_suppressed_nearby") is True
    )
    result = {
        "policy": policy,
        "end_reason": end_reason,
        "leg": leg,
        "quote_count": len(rows),
        "mean_lifetime_sec": _mean(lifetimes),
        "median_lifetime_sec": median(lifetimes) if lifetimes else None,
        "p75_lifetime_sec": _percentile(lifetimes, 0.75),
        "p90_lifetime_sec": _percentile(lifetimes, 0.90),
        "quote_fade_end_count": quote_fade_end_count,
        "order_cancel_quote_count": order_cancel_count,
        "quote_fade_suppressed_count": (
            raw_suppressed_count
            if raw_suppressed_count is not None and end_reason == "all" and leg == "all"
            else suppressed_count
            if end_reason == "all" and leg == "all"
            else None
        ),
    }
    for horizon in HORIZONS:
        directional = _float_values(rows, f"directional_ret_{horizon}s_bps")
        danger_rows = [
            row
            for row in rows
            if row.get("quote_fade_suppressed_nearby") is True
            and row.get(f"danger_after_suppression_{horizon}s") is not None
        ]
        danger_count = sum(
            1 for row in danger_rows if row.get(f"danger_after_suppression_{horizon}s") is True
        )
        result[f"mean_directional_ret_{horizon}s_bps"] = _mean(directional)
        result[f"danger_after_suppression_ratio_{horizon}s"] = (
            danger_count / len(danger_rows) if danger_rows else None
        )
    return result


def _build_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    by_policy: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in details:
        by_policy[str(row["policy"])].append(row)

    for policy in POLICIES:
        rows = by_policy.get(policy, [])
        summary.append(
            _summary_row(
                policy,
                "all",
                "all",
                rows,
                rows,
                raw_suppressed_count=_raw_quote_fade_suppressed_count(policy),
            )
        )
        grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            grouped[(str(row.get("end_reason")), str(row.get("leg")))].append(row)
        for (end_reason, leg), group_rows in sorted(grouped.items()):
            summary.append(_summary_row(policy, end_reason, leg, group_rows, rows))
    return summary


def main() -> int:
    details: list[dict[str, object]] = []
    for policy in POLICIES:
        details.extend(_build_policy_details(policy))
    summary = _build_summary(details)

    with DETAILS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_FIELDNAMES)
        writer.writeheader()
        writer.writerows(details)

    with SUMMARY_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(summary)

    print(f"done details={len(details)} summary={len(summary)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
