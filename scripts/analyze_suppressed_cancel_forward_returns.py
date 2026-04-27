from __future__ import annotations

import csv
import json
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
DETAILS_PATH = Path("reports/suppressed_cancel_forward_returns.csv")
SUMMARY_PATH = Path("reports/suppressed_cancel_forward_summary.csv")
HORIZONS_SEC = (1.0, 3.0, 5.0)


DETAIL_FIELDNAMES = [
    "ts",
    "reason",
    "cancel_aggressive_policy",
    "policy_enabled",
    "trade_side",
    "trade_px",
    "mid_at_trigger",
    "bid_px",
    "ask_px",
    "spread_bps",
    "tfi",
    "last_quote_fade_age_ms",
    "ret_1s_bps",
    "ret_3s_bps",
    "ret_5s_bps",
    "directional_ret_1s_bps",
    "directional_ret_3s_bps",
    "directional_ret_5s_bps",
    "suppression_result_1s",
    "suppression_result_3s",
    "suppression_result_5s",
]

SUMMARY_FIELDNAMES = [
    "cancel_aggressive_policy",
    "trade_side",
    "return_horizon",
    "count",
    "safe_count",
    "unsafe_count",
    "neutral_count",
    "safe_ratio",
    "unsafe_ratio",
    "neutral_ratio",
    "mean_directional_ret_bps",
    "median_directional_ret_bps",
]


def _to_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bps_return(base: object, future: object) -> float | None:
    base_f = _to_float(base)
    future_f = _to_float(future)
    if base_f is None or future_f is None or base_f == 0:
        return None
    return (future_f - base_f) / base_f * 10000.0


def _direction(trade_side: object) -> int | None:
    if trade_side == "sell":
        return -1
    if trade_side == "buy":
        return 1
    return None


def _directional_ret(ret_bps: object, trade_side: object) -> float | None:
    ret_f = _to_float(ret_bps)
    direction = _direction(trade_side)
    if ret_f is None or direction is None:
        return None
    return ret_f * direction


def _suppression_result(directional_ret: object) -> str:
    directional_ret_f = _to_float(directional_ret)
    if directional_ret_f is None or directional_ret_f == 0:
        return "neutral"
    if directional_ret_f > 0:
        return "unsafe_suppression"
    return "safe_suppression"


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _load_logs() -> tuple[list[tuple[float, float]], list[dict[str, object]]]:
    ticks: list[tuple[float, float]] = []
    triggers: list[dict[str, object]] = []

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

                if data.get("event") == "tick":
                    mid_perp = _to_float(data.get("mid_perp"))
                    if mid_perp is not None:
                        ticks.append((ts, mid_perp))
                    continue

                if data.get("event") != "risk":
                    continue
                if data.get("reason") != "cancel_aggressive_suppressed":
                    continue

                trigger = dict(data)
                trigger["ts"] = ts
                triggers.append(trigger)

    ticks.sort(key=lambda item: item[0])
    triggers.sort(key=lambda item: float(item["ts"]))
    return ticks, triggers


def _build_detail_rows(
    ticks: list[tuple[float, float]], triggers: list[dict[str, object]]
) -> list[dict[str, object]]:
    tick_times = [item[0] for item in ticks]
    rows: list[dict[str, object]] = []

    for trigger in triggers:
        trigger_ts = float(trigger["ts"])
        trade_side = trigger.get("trade_side")
        mid_at_trigger = trigger.get("mid_perp")

        row: dict[str, object] = {
            "ts": trigger_ts,
            "reason": trigger.get("reason"),
            "cancel_aggressive_policy": trigger.get("cancel_aggressive_policy"),
            "policy_enabled": trigger.get("policy_enabled"),
            "trade_side": trade_side,
            "trade_px": trigger.get("trade_px"),
            "mid_at_trigger": mid_at_trigger,
            "bid_px": trigger.get("bid_px"),
            "ask_px": trigger.get("ask_px"),
            "spread_bps": trigger.get("spread_bps"),
            "tfi": trigger.get("tfi"),
            "last_quote_fade_age_ms": trigger.get("last_quote_fade_age_ms"),
            "ret_1s_bps": None,
            "ret_3s_bps": None,
            "ret_5s_bps": None,
            "directional_ret_1s_bps": None,
            "directional_ret_3s_bps": None,
            "directional_ret_5s_bps": None,
            "suppression_result_1s": "neutral",
            "suppression_result_3s": "neutral",
            "suppression_result_5s": "neutral",
        }

        for horizon in HORIZONS_SEC:
            label = str(int(horizon))
            idx = bisect_left(tick_times, trigger_ts + horizon)
            if idx >= len(ticks):
                continue

            ret_bps = _bps_return(mid_at_trigger, ticks[idx][1])
            directional = _directional_ret(ret_bps, trade_side)
            row[f"ret_{label}s_bps"] = ret_bps
            row[f"directional_ret_{label}s_bps"] = directional
            row[f"suppression_result_{label}s"] = _suppression_result(directional)

        rows.append(row)

    return rows


def _build_summary_rows(detail_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[object, object, str], list[dict[str, object]]] = defaultdict(list)
    for row in detail_rows:
        for horizon in ("1s", "3s", "5s"):
            key = (row["cancel_aggressive_policy"], row["trade_side"], horizon)
            grouped[key].append(row)

    summary_rows: list[dict[str, object]] = []
    for (policy, trade_side, horizon), rows in sorted(
        grouped.items(), key=lambda item: (str(item[0][0]), str(item[0][1]), item[0][2])
    ):
        result_key = f"suppression_result_{horizon}"
        directional_key = f"directional_ret_{horizon}_bps"
        count = len(rows)
        safe_count = sum(1 for row in rows if row[result_key] == "safe_suppression")
        unsafe_count = sum(1 for row in rows if row[result_key] == "unsafe_suppression")
        neutral_count = sum(1 for row in rows if row[result_key] == "neutral")
        directional_values = [
            value
            for row in rows
            if (value := _to_float(row[directional_key])) is not None
        ]

        summary_rows.append(
            {
                "cancel_aggressive_policy": policy,
                "trade_side": trade_side,
                "return_horizon": horizon,
                "count": count,
                "safe_count": safe_count,
                "unsafe_count": unsafe_count,
                "neutral_count": neutral_count,
                "safe_ratio": safe_count / count if count else None,
                "unsafe_ratio": unsafe_count / count if count else None,
                "neutral_ratio": neutral_count / count if count else None,
                "mean_directional_ret_bps": _mean(directional_values),
                "median_directional_ret_bps": median(directional_values)
                if directional_values
                else None,
            }
        )

    return summary_rows


def main() -> int:
    ticks, triggers = _load_logs()
    detail_rows = _build_detail_rows(ticks, triggers)
    summary_rows = _build_summary_rows(detail_rows)

    DETAILS_PATH.parent.mkdir(parents=True, exist_ok=True)
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
