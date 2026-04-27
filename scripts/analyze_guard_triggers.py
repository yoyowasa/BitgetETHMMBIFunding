from __future__ import annotations

import csv
import json
from pathlib import Path


LOG_DIR = Path("logs")
OUTPUT_PATH = Path("reports/guard_trigger_details.csv")
TARGET_REASONS = {"cancel_aggressive", "quote_fade", "tfi_fade"}


def _safe_bps(move: object, base: object) -> float | None:
    try:
        move_f = float(move)
        base_f = float(base)
    except (TypeError, ValueError):
        return None
    if base_f == 0:
        return None
    return move_f / base_f * 10000.0


def main() -> int:
    rows: list[dict[str, object]] = []

    for log_file in LOG_DIR.glob("*.jsonl"):
        with log_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("event") != "risk":
                    continue

                reason = data.get("reason")
                if reason not in TARGET_REASONS:
                    continue

                mid = data.get("mid")
                if mid is None:
                    mid = data.get("mid_perp")

                mid_prev = data.get("mid_prev")
                if mid_prev is None:
                    mid_prev = data.get("mid_100ms_ago")

                mid_move_bps = data.get("mid_move_bps")
                if mid_move_bps is None and mid is not None and mid_prev is not None:
                    try:
                        mid_move_bps = _safe_bps(float(mid) - float(mid_prev), mid_prev)
                    except (TypeError, ValueError):
                        mid_move_bps = None

                bid_px = data.get("bid_px")
                ask_px = data.get("ask_px")
                spread_bps = data.get("spread_bps")
                if spread_bps is None and bid_px is not None and ask_px is not None:
                    try:
                        bid_f = float(bid_px)
                        ask_f = float(ask_px)
                        spread_bps = _safe_bps(ask_f - bid_f, (ask_f + bid_f) / 2.0)
                    except (TypeError, ValueError):
                        spread_bps = None

                rows.append(
                    {
                        "ts": data.get("ts"),
                        "reason": reason,
                        "mid": mid,
                        "mid_prev": mid_prev,
                        "mid_move_bps": mid_move_bps,
                        "tfi": data.get("tfi"),
                        "bid_px": bid_px,
                        "ask_px": ask_px,
                        "spread_bps": spread_bps,
                        "trade_px": data.get("trade_px"),
                        "trade_side": data.get("trade_side"),
                    }
                )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ts",
        "reason",
        "mid",
        "mid_prev",
        "mid_move_bps",
        "tfi",
        "bid_px",
        "ask_px",
        "spread_bps",
        "trade_px",
        "trade_side",
    ]

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
