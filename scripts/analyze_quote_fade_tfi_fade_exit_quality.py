from __future__ import annotations

import csv
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
ROOT_LIFECYCLE_PATH = Path("reports/quote_lifecycle_details.csv")
FALLBACK_LIFECYCLE_PATH = Path("reports/spread_dryrun_compare/quote_lifecycle_details.csv")
DETAILS_PATH = Path("reports/quote_fade_tfi_fade_exit_quality_details.csv")
SUMMARY_PATH = Path("reports/quote_fade_tfi_fade_exit_quality_summary.csv")

TARGET_END_REASONS = {"quote_fade", "tfi_fade"}
END_GUARD_REASONS = {
    "cancel_aggressive",
    "quote_fade",
    "tfi_fade",
    "edge_negative_total",
}
HORIZONS = (1, 3, 5)

DETAIL_FIELDNAMES = [
    "scenario",
    "quote_ts",
    "end_ts",
    "end_reason",
    "leg",
    "side",
    "price",
    "quote_lifetime_sec",
    "mid_at_end",
    "mid_after_1s",
    "ret_1s_bps",
    "mid_after_3s",
    "ret_3s_bps",
    "mid_after_5s",
    "ret_5s_bps",
    "tfi",
    "obi",
    "mid_move_bps",
    "directional_ret_1s_bps",
    "directional_ret_3s_bps",
    "directional_ret_5s_bps",
    "exit_success_1s",
    "exit_success_3s",
    "exit_success_5s",
]

SUMMARY_FIELDNAMES = [
    "group_type",
    "end_reason",
    "leg",
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
    "mean_lifetime_sec",
    "median_lifetime_sec",
    "total_quote_fade_exits",
    "total_tfi_fade_exits",
    "quote_fade_success_ratio_3s",
    "tfi_fade_success_ratio_3s",
    "quote_fade_mean_directional_ret_3s",
    "tfi_fade_mean_directional_ret_3s",
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


def _load_log_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for log_file in sorted(LOG_DIR.glob("*.jsonl")):
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
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _is_quote_order(row: dict[str, object]) -> bool:
    if row.get("event") != "order_new":
        return False
    return row.get("reason") == "quote" or str(row.get("intent", "")).startswith("QUOTE_")


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
    leg = row.get("leg")
    return str(leg) if leg else None


def _is_end_event(row: dict[str, object]) -> bool:
    event = row.get("event")
    if event in {"order_cancel", "order_skip"}:
        return True
    return event == "risk" and row.get("reason") in END_GUARD_REASONS


def _end_reason(row: dict[str, object] | None) -> str:
    if row is None:
        return "open_or_unknown"
    event = row.get("event")
    if event in {"order_cancel", "order_skip"}:
        reason = row.get("reason")
        return f"{event}:{reason}" if reason else str(event)
    return str(row.get("reason", event))


def _scenario_for_quote(quote: dict[str, object], pre_quote_rows: list[dict[str, object]]) -> str:
    quote_ts = float(quote["ts"])
    idx = bisect_right([float(row["ts"]) for row in pre_quote_rows], quote_ts) - 1
    if idx < 0:
        return "logs"
    row = pre_quote_rows[idx]
    if quote_ts - float(row["ts"]) > 2.0:
        return "logs"
    base = row.get("base_half_spread_bps")
    scope = row.get("cancel_aggressive_scope")
    quality_filter = row.get("cancel_aggressive_quality_filter")
    parts = [f"{base}bps" if base is not None else "logs"]
    if scope:
        parts.append(str(scope))
    if quality_filter:
        parts.append(str(quality_filter))
    return "_".join(parts)


def _build_lifecycle_from_logs(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    pre_quote_rows = [
        row
        for row in rows
        if row.get("event") == "risk" and row.get("reason") == "pre_quote_decision"
    ]
    quotes = [(idx, row) for idx, row in enumerate(rows) if _is_quote_order(row)]
    details: list[dict[str, object]] = []
    for quote_idx, quote in quotes:
        end_event = next(
            (candidate for candidate in rows[quote_idx + 1 :] if _is_end_event(candidate)),
            None,
        )
        end_ts = _to_float(end_event.get("ts")) if end_event else None
        quote_ts = float(quote["ts"])
        details.append(
            {
                "scenario": _scenario_for_quote(quote, pre_quote_rows),
                "quote_ts": quote_ts,
                "end_ts": end_ts,
                "end_reason": _end_reason(end_event),
                "leg": _leg(quote),
                "side": quote.get("side"),
                "price": quote.get("price"),
                "quote_lifetime_sec": end_ts - quote_ts if end_ts is not None else None,
                "end_event": end_event,
            }
        )
    return details


def _load_lifecycle_csv() -> list[dict[str, object]]:
    path = ROOT_LIFECYCLE_PATH if ROOT_LIFECYCLE_PATH.exists() else FALLBACK_LIFECYCLE_PATH
    if not path.exists():
        return []
    details: list[dict[str, object]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            quote_ts = _to_float(row.get("quote_ts"))
            end_ts = _to_float(row.get("end_ts"))
            if quote_ts is None:
                continue
            leg = row.get("leg")
            side = "buy" if leg == "bid" else "sell" if leg == "ask" else None
            details.append(
                {
                    "scenario": row.get("scenario") or "csv",
                    "quote_ts": quote_ts,
                    "end_ts": end_ts,
                    "end_reason": row.get("end_reason") or "open_or_unknown",
                    "leg": leg,
                    "side": side,
                    "price": row.get("price"),
                    "quote_lifetime_sec": _to_float(row.get("quote_lifetime_sec")),
                    "end_event": None,
                }
            )
    return details


def _dedupe_lifecycle(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: dict[tuple[float, str, str], dict[str, object]] = {}
    for row in rows:
        quote_ts = _to_float(row.get("quote_ts"))
        if quote_ts is None:
            continue
        key = (round(quote_ts, 6), str(row.get("leg")), str(row.get("end_reason")))
        if key not in deduped or row.get("end_event") is not None:
            deduped[key] = row
    return sorted(deduped.values(), key=lambda row: float(row["quote_ts"]))


def _tick_series(rows: list[dict[str, object]]) -> tuple[list[float], list[dict[str, object]]]:
    ticks = [
        row
        for row in rows
        if row.get("event") == "tick" and _to_float(row.get("mid_perp")) is not None
    ]
    ticks.sort(key=lambda row: float(row["ts"]))
    return [float(row["ts"]) for row in ticks], ticks


def _risk_rows_by_reason(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row.get("event") == "risk":
            grouped[str(row.get("reason"))].append(row)
    for reason_rows in grouped.values():
        reason_rows.sort(key=lambda row: float(row["ts"]))
    return grouped


def _nearest_risk_event(
    grouped: dict[str, list[dict[str, object]]],
    reason: str,
    ts: float,
) -> dict[str, object] | None:
    rows = grouped.get(reason, [])
    if not rows:
        return None
    times = [float(row["ts"]) for row in rows]
    idx = bisect_left(times, ts)
    candidates = []
    if idx < len(rows):
        candidates.append(rows[idx])
    if idx > 0:
        candidates.append(rows[idx - 1])
    if not candidates:
        return None
    nearest = min(candidates, key=lambda row: abs(float(row["ts"]) - ts))
    return nearest if abs(float(nearest["ts"]) - ts) <= 0.01 else None


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


def _ret_bps(mid_at_end: float | None, mid_after: float | None) -> float | None:
    if mid_at_end is None or mid_after is None or mid_at_end <= 0:
        return None
    return (mid_after - mid_at_end) / mid_at_end * 10000.0


def _mid_move_bps(end_event: dict[str, object] | None) -> float | None:
    if end_event is None:
        return None
    existing = _to_float(end_event.get("mid_move_bps"))
    if existing is not None:
        return existing
    mid = _to_float(end_event.get("mid_perp"))
    prev = _to_float(end_event.get("mid_100ms_ago"))
    if mid is None or prev is None or prev <= 0:
        return None
    return (mid - prev) / prev * 10000.0


def _danger_sign(end_reason: str, tfi: float | None, mid_move_bps: float | None) -> int | None:
    if end_reason == "tfi_fade":
        if tfi is None or tfi == 0:
            return None
        return 1 if tfi > 0 else -1
    if end_reason == "quote_fade":
        if mid_move_bps is None or mid_move_bps == 0:
            return None
        return 1 if mid_move_bps > 0 else -1
    return None


def _exit_success(directional_ret: float | None) -> str:
    if directional_ret is None or directional_ret == 0:
        return "neutral"
    return "success" if directional_ret > 0 else "fail"


def _build_details() -> list[dict[str, object]]:
    rows = _load_log_rows()
    tick_times, ticks = _tick_series(rows)
    risk_grouped = _risk_rows_by_reason(rows)
    lifecycle = _dedupe_lifecycle(_build_lifecycle_from_logs(rows) + _load_lifecycle_csv())
    details: list[dict[str, object]] = []

    for quote in lifecycle:
        end_reason = str(quote.get("end_reason"))
        if end_reason not in TARGET_END_REASONS:
            continue
        end_ts = _to_float(quote.get("end_ts"))
        if end_ts is None:
            continue
        end_event = quote.get("end_event")
        if not isinstance(end_event, dict):
            end_event = _nearest_risk_event(risk_grouped, end_reason, end_ts)
        end_tick = _tick_at_or_before(tick_times, ticks, end_ts)
        mid_at_end = _to_float(end_event.get("mid_perp")) if isinstance(end_event, dict) else None
        if mid_at_end is None and end_tick is not None:
            mid_at_end = _to_float(end_tick.get("mid_perp"))
        tfi = _to_float(end_event.get("tfi")) if isinstance(end_event, dict) else None
        if tfi is None and end_tick is not None:
            tfi = _to_float(end_tick.get("tfi"))
        obi = _to_float(end_tick.get("obi_perp")) if end_tick is not None else None
        move_bps = _mid_move_bps(end_event) if isinstance(end_event, dict) else None
        sign = _danger_sign(end_reason, tfi, move_bps)

        row = {
            "scenario": quote.get("scenario"),
            "quote_ts": quote.get("quote_ts"),
            "end_ts": end_ts,
            "end_reason": end_reason,
            "leg": quote.get("leg"),
            "side": quote.get("side"),
            "price": quote.get("price"),
            "quote_lifetime_sec": quote.get("quote_lifetime_sec"),
            "mid_at_end": mid_at_end,
            "tfi": tfi,
            "obi": obi,
            "mid_move_bps": move_bps,
        }
        for horizon in HORIZONS:
            after_tick = _tick_at_or_after(tick_times, ticks, end_ts + horizon)
            mid_after = _to_float(after_tick.get("mid_perp")) if after_tick is not None else None
            ret = _ret_bps(mid_at_end, mid_after)
            directional_ret = ret * sign if ret is not None and sign is not None else None
            row[f"mid_after_{horizon}s"] = mid_after
            row[f"ret_{horizon}s_bps"] = ret
            row[f"directional_ret_{horizon}s_bps"] = directional_ret
            row[f"exit_success_{horizon}s"] = _exit_success(directional_ret)
        details.append(row)
    return details


def _float_values(rows: list[dict[str, object]], key: str) -> list[float]:
    return [value for row in rows if (value := _to_float(row.get(key))) is not None]


def _summary_row(
    group_type: str,
    end_reason: str,
    leg: str,
    horizon: int,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    statuses = [str(row.get(f"exit_success_{horizon}s")) for row in rows]
    success_count = statuses.count("success")
    fail_count = statuses.count("fail")
    neutral_count = statuses.count("neutral")
    count = len(rows)
    directional = _float_values(rows, f"directional_ret_{horizon}s_bps")
    lifetimes = _float_values(rows, "quote_lifetime_sec")
    return {
        "group_type": group_type,
        "end_reason": end_reason,
        "leg": leg,
        "return_horizon": f"{horizon}s",
        "count": count,
        "success_count": success_count,
        "fail_count": fail_count,
        "neutral_count": neutral_count,
        "success_ratio": success_count / count if count else None,
        "fail_ratio": fail_count / count if count else None,
        "neutral_ratio": neutral_count / count if count else None,
        "mean_directional_ret_bps": _mean(directional),
        "median_directional_ret_bps": median(directional) if directional else None,
        "mean_lifetime_sec": _mean(lifetimes),
        "median_lifetime_sec": median(lifetimes) if lifetimes else None,
        "total_quote_fade_exits": None,
        "total_tfi_fade_exits": None,
        "quote_fade_success_ratio_3s": None,
        "tfi_fade_success_ratio_3s": None,
        "quote_fade_mean_directional_ret_3s": None,
        "tfi_fade_mean_directional_ret_3s": None,
    }


def _grouped_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    group_specs = [
        ("end_reason", lambda row: (str(row.get("end_reason")), "all")),
        ("leg", lambda row: ("all", str(row.get("leg")))),
        ("end_reason_leg", lambda row: (str(row.get("end_reason")), str(row.get("leg")))),
    ]
    for group_type, key_fn in group_specs:
        grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        for row in details:
            grouped[key_fn(row)].append(row)
        for (end_reason, leg), rows in sorted(grouped.items()):
            for horizon in HORIZONS:
                summaries.append(_summary_row(group_type, end_reason, leg, horizon, rows))
    return summaries


def _overall_metrics(details: list[dict[str, object]]) -> dict[str, object]:
    quote_fade_rows = [row for row in details if row.get("end_reason") == "quote_fade"]
    tfi_fade_rows = [row for row in details if row.get("end_reason") == "tfi_fade"]

    def success_ratio(rows: list[dict[str, object]], horizon: int) -> float | None:
        if not rows:
            return None
        return sum(1 for row in rows if row.get(f"exit_success_{horizon}s") == "success") / len(rows)

    return {
        "group_type": "overall_metrics",
        "end_reason": "all",
        "leg": "all",
        "return_horizon": "3s",
        "count": len(details),
        "success_count": None,
        "fail_count": None,
        "neutral_count": None,
        "success_ratio": None,
        "fail_ratio": None,
        "neutral_ratio": None,
        "mean_directional_ret_bps": None,
        "median_directional_ret_bps": None,
        "mean_lifetime_sec": None,
        "median_lifetime_sec": None,
        "total_quote_fade_exits": len(quote_fade_rows),
        "total_tfi_fade_exits": len(tfi_fade_rows),
        "quote_fade_success_ratio_3s": success_ratio(quote_fade_rows, 3),
        "tfi_fade_success_ratio_3s": success_ratio(tfi_fade_rows, 3),
        "quote_fade_mean_directional_ret_3s": _mean(
            _float_values(quote_fade_rows, "directional_ret_3s_bps")
        ),
        "tfi_fade_mean_directional_ret_3s": _mean(
            _float_values(tfi_fade_rows, "directional_ret_3s_bps")
        ),
    }


def _build_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    return [_overall_metrics(details), *_grouped_summary(details)]


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

    overall = summary[0]
    print(
        "done "
        f"rows={len(details)} "
        f"quote_fade={overall['total_quote_fade_exits']} "
        f"tfi_fade={overall['total_tfi_fade_exits']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
