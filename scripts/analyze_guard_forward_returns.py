from __future__ import annotations

import csv
import json
from bisect import bisect_left
from pathlib import Path


LOG_DIR = Path("logs")
OUTPUT_PATH = Path("reports/guard_forward_returns.csv")
TARGET_REASONS = {"quote_fade", "cancel_aggressive", "tfi_fade"}
HORIZONS_SEC = (1.0, 3.0, 5.0)


def _bps_return(base: object, future: object) -> float | None:
    try:
        base_f = float(base)
        future_f = float(future)
    except (TypeError, ValueError):
        return None
    if base_f == 0:
        return None
    return (future_f - base_f) / base_f * 10000.0


def _mid_move_bps(data: dict[str, object]) -> float | None:
    value = data.get("mid_move_bps")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    mid = data.get("mid_perp")
    prev = data.get("mid_100ms_ago")
    if mid is None or prev is None:
        return None
    return _bps_return(prev, mid)


def main() -> int:
    ticks: list[tuple[float, float]] = []
    triggers: list[dict[str, object]] = []

    for log_file in LOG_DIR.glob("*.jsonl"):
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event = data.get("event")
                ts = data.get("ts")
                if ts is None:
                    continue

                try:
                    ts_f = float(ts)
                except (TypeError, ValueError):
                    continue

                if event == "tick":
                    mid_perp = data.get("mid_perp")
                    try:
                        if mid_perp is not None:
                            ticks.append((ts_f, float(mid_perp)))
                    except (TypeError, ValueError):
                        continue
                    continue

                if event != "risk":
                    continue

                reason = data.get("reason")
                if reason not in TARGET_REASONS:
                    continue

                trigger_mid = data.get("mid_perp")
                if trigger_mid is None:
                    trigger_mid = data.get("mid")

                triggers.append(
                    {
                        "ts": ts_f,
                        "reason": reason,
                        "mid_at_trigger": trigger_mid,
                        "tfi": data.get("tfi"),
                        "mid_move_bps": _mid_move_bps(data),
                        "trade_side": data.get("trade_side"),
                        "trade_px": data.get("trade_px"),
                    }
                )

    ticks.sort(key=lambda item: item[0])
    tick_times = [item[0] for item in ticks]

    rows: list[dict[str, object]] = []
    for trigger in triggers:
        row = {
            "ts": trigger["ts"],
            "reason": trigger["reason"],
            "mid_at_trigger": trigger["mid_at_trigger"],
            "mid_after_1s": None,
            "ret_1s_bps": None,
            "mid_after_3s": None,
            "ret_3s_bps": None,
            "mid_after_5s": None,
            "ret_5s_bps": None,
            "tfi": trigger["tfi"],
            "mid_move_bps": trigger["mid_move_bps"],
            "trade_side": trigger["trade_side"],
            "trade_px": trigger["trade_px"],
        }

        for horizon in HORIZONS_SEC:
            target_ts = float(trigger["ts"]) + horizon
            idx = bisect_left(tick_times, target_ts)
            if idx >= len(ticks):
                continue
            future_mid = ticks[idx][1]
            if horizon == 1.0:
                row["mid_after_1s"] = future_mid
                row["ret_1s_bps"] = _bps_return(trigger["mid_at_trigger"], future_mid)
            elif horizon == 3.0:
                row["mid_after_3s"] = future_mid
                row["ret_3s_bps"] = _bps_return(trigger["mid_at_trigger"], future_mid)
            elif horizon == 5.0:
                row["mid_after_5s"] = future_mid
                row["ret_5s_bps"] = _bps_return(trigger["mid_at_trigger"], future_mid)

        rows.append(row)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ts",
        "reason",
        "mid_at_trigger",
        "mid_after_1s",
        "ret_1s_bps",
        "mid_after_3s",
        "ret_3s_bps",
        "mid_after_5s",
        "ret_5s_bps",
        "tfi",
        "mid_move_bps",
        "trade_side",
        "trade_px",
    ]
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
