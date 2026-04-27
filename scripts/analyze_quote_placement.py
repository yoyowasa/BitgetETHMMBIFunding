from __future__ import annotations

import csv
import json
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median


LOG_DIR = Path("logs")
LIFECYCLE_PATH = Path("reports/spread_dryrun_compare/quote_lifecycle_details.csv")
DETAILS_PATH = Path("reports/quote_placement_details.csv")
SUMMARY_PATH = Path("reports/quote_placement_summary.csv")

DETAIL_FIELDNAMES = [
    "ts",
    "leg",
    "side",
    "price",
    "qty",
    "mid_perp",
    "micro_price",
    "bid_px",
    "ask_px",
    "spread_bps",
    "tfi",
    "obi",
    "base_half_spread_bps",
    "min_half_spread_bps",
    "one_sided_quote_policy",
    "cancel_aggressive_policy",
    "quote_distance_from_mid_bps",
    "quote_distance_from_micro_bps",
    "quote_distance_from_best_bps",
    "aggressive_vs_best",
    "directional_alignment",
    "quote_lifetime_sec",
    "end_reason",
    "cancel_aggressive_count_during_quote",
    "quote_fade_count_during_quote",
    "tfi_fade_count_during_quote",
]

SUMMARY_FIELDNAMES = [
    "leg",
    "aggressive_vs_best",
    "directional_alignment",
    "end_reason",
    "count",
    "mean_quote_distance_from_mid_bps",
    "median_quote_distance_from_mid_bps",
    "mean_quote_distance_from_micro_bps",
    "median_quote_distance_from_micro_bps",
    "mean_quote_distance_from_best_bps",
    "median_quote_distance_from_best_bps",
    "mean_tfi",
    "median_tfi",
    "mean_obi",
    "median_obi",
    "mean_lifetime_sec",
    "median_lifetime_sec",
    "cancel_aggressive_end_count",
    "quote_fade_end_count",
    "tfi_fade_end_count",
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


def _leg(data: dict[str, object]) -> str:
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
    return str(data.get("leg") or "")


def _is_quote_order(data: dict[str, object]) -> bool:
    if data.get("event") != "order_new":
        return False
    if str(data.get("intent", "")).startswith("QUOTE_"):
        return True
    return data.get("reason") == "quote"


def _bps(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator * 10000.0


def _load_logs() -> tuple[
    list[dict[str, object]],
    dict[int, list[dict[str, object]]],
    list[tuple[float, dict[str, object]]],
]:
    quote_orders: list[dict[str, object]] = []
    ticks_by_cycle: dict[int, list[dict[str, object]]] = defaultdict(list)
    bbo_rows: list[tuple[float, dict[str, object]]] = []

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

                if _is_quote_order(data):
                    quote_orders.append(data)
                    continue

                if data.get("event") == "tick" and data.get("cycle_id") is not None:
                    try:
                        cycle_id = int(data["cycle_id"])
                    except (TypeError, ValueError):
                        continue
                    if data.get("mid_perp") is not None:
                        ticks_by_cycle[cycle_id].append(data)
                    continue

                if (
                    data.get("event") == "risk"
                    and data.get("bid_px") is not None
                    and data.get("ask_px") is not None
                ):
                    bbo_rows.append((ts, data))

    quote_orders.sort(key=lambda item: float(item["ts"]))
    for rows in ticks_by_cycle.values():
        rows.sort(key=lambda item: float(item["ts"]))
    bbo_rows.sort(key=lambda item: item[0])
    return quote_orders, ticks_by_cycle, bbo_rows


def _nearest_tick(
    ticks_by_cycle: dict[int, list[dict[str, object]]], cycle_id: int, ts: float
) -> dict[str, object]:
    rows = ticks_by_cycle.get(cycle_id, [])
    if not rows:
        return {}
    times = [float(row["ts"]) for row in rows]
    idx = bisect_left(times, ts)
    candidates = []
    if idx < len(rows):
        candidates.append(rows[idx])
    if idx > 0:
        candidates.append(rows[idx - 1])
    if not candidates:
        return {}
    return min(candidates, key=lambda row: abs(float(row["ts"]) - ts))


def _nearest_bbo(
    bbo_rows: list[tuple[float, dict[str, object]]], ts: float
) -> dict[str, object] | None:
    if not bbo_rows:
        return None
    times = [item[0] for item in bbo_rows]
    idx = bisect_left(times, ts)
    candidates = []
    if idx < len(bbo_rows):
        candidates.append(bbo_rows[idx])
    if idx > 0:
        candidates.append(bbo_rows[idx - 1])
    if not candidates:
        return None
    best_ts, best_row = min(candidates, key=lambda item: abs(item[0] - ts))
    if abs(best_ts - ts) > 1.0:
        return None
    return best_row


def _load_lifecycle() -> list[dict[str, object]]:
    if not LIFECYCLE_PATH.exists():
        return []
    rows: list[dict[str, object]] = []
    with LIFECYCLE_PATH.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            quote_ts = _to_float(row.get("quote_ts"))
            price = _to_float(row.get("price"))
            if quote_ts is None or price is None:
                continue
            row["quote_ts"] = quote_ts
            row["price"] = price
            rows.append(row)
    return rows


def _match_lifecycle(
    lifecycle_rows: list[dict[str, object]], ts: float, leg: str, price: float
) -> dict[str, object] | None:
    matches = [
        row
        for row in lifecycle_rows
        if row.get("leg") == leg
        and abs(float(row["quote_ts"]) - ts) <= 0.01
        and abs(float(row["price"]) - price) <= 0.01
    ]
    if not matches:
        return None
    return min(matches, key=lambda row: abs(float(row["quote_ts"]) - ts))


def _scenario_config(ts: float) -> tuple[object, object, object, object]:
    # Known DRY_RUN windows from saved comparison artifacts.
    windows = [
        (1777017765.5553362, 1777018378.0015707, 15.0, 15.0, "current", "current"),
        (1777018476.130068, 1777019091.2308633, 18.0, 18.0, "current", "current"),
        (1777023949.2383971, 1777024562.1205263, 18.0, 18.0, "tfi_0p7", "current"),
    ]
    for start, end, base_half, min_half, one_sided, cancel_policy in windows:
        if start <= ts <= end:
            return base_half, min_half, one_sided, cancel_policy
    return "", "", "", ""


def _distance_from_mid(leg: str, price: float, mid: float | None) -> float | None:
    if mid is None:
        return None
    if leg == "bid":
        return _bps(mid - price, mid)
    if leg == "ask":
        return _bps(price - mid, mid)
    return None


def _distance_from_best(
    leg: str, price: float, bid_px: float | None, ask_px: float | None
) -> float | None:
    if leg == "bid" and bid_px is not None:
        return _bps(bid_px - price, bid_px)
    if leg == "ask" and ask_px is not None:
        return _bps(price - ask_px, ask_px)
    return None


def _aggressive_vs_best(
    leg: str, price: float, bid_px: float | None, ask_px: float | None
) -> str:
    if leg == "bid" and bid_px is not None:
        return "aggressive_or_at_best" if price >= bid_px else "passive_inside_book"
    if leg == "ask" and ask_px is not None:
        return "aggressive_or_at_best" if price <= ask_px else "passive_inside_book"
    return "unknown"


def _directional_alignment(leg: str, tfi: float | None) -> str:
    if tfi is None:
        return "unknown"
    if leg == "bid" and tfi < -0.6:
        return "against_flow"
    if leg == "ask" and tfi > 0.6:
        return "against_flow"
    if leg == "bid" and tfi > 0.6:
        return "with_flow"
    if leg == "ask" and tfi < -0.6:
        return "with_flow"
    return "neutral"


def _build_details() -> list[dict[str, object]]:
    quote_orders, ticks_by_cycle, bbo_rows = _load_logs()
    lifecycle_rows = _load_lifecycle()
    details: list[dict[str, object]] = []

    for order in quote_orders:
        ts = float(order["ts"])
        leg = _leg(order)
        price = _to_float(order.get("price"))
        if price is None:
            continue
        try:
            cycle_id = int(order["cycle_id"])
        except (TypeError, ValueError):
            cycle_id = -1
        tick = _nearest_tick(ticks_by_cycle, cycle_id, ts)
        bbo = _nearest_bbo(bbo_rows, ts) or {}

        mid_perp = _to_float(tick.get("mid_perp"))
        micro_price = _to_float(tick.get("micro_price"))
        bid_px = _to_float(bbo.get("bid_px"))
        ask_px = _to_float(bbo.get("ask_px"))
        spread_bps = _to_float(bbo.get("spread_bps"))
        tfi = _to_float(tick.get("tfi"))
        obi = _to_float(tick.get("obi_perp"))
        lifecycle = _match_lifecycle(lifecycle_rows, ts, leg, price) or {}
        base_half, min_half, one_sided, cancel_policy = _scenario_config(ts)

        details.append(
            {
                "ts": ts,
                "leg": leg,
                "side": order.get("side"),
                "price": price,
                "qty": order.get("size"),
                "mid_perp": mid_perp,
                "micro_price": micro_price,
                "bid_px": bid_px,
                "ask_px": ask_px,
                "spread_bps": spread_bps,
                "tfi": tfi,
                "obi": obi,
                "base_half_spread_bps": base_half,
                "min_half_spread_bps": min_half,
                "one_sided_quote_policy": one_sided,
                "cancel_aggressive_policy": cancel_policy,
                "quote_distance_from_mid_bps": _distance_from_mid(leg, price, mid_perp),
                "quote_distance_from_micro_bps": _distance_from_mid(
                    leg, price, micro_price
                ),
                "quote_distance_from_best_bps": _distance_from_best(
                    leg, price, bid_px, ask_px
                ),
                "aggressive_vs_best": _aggressive_vs_best(leg, price, bid_px, ask_px),
                "directional_alignment": _directional_alignment(leg, tfi),
                "quote_lifetime_sec": lifecycle.get("quote_lifetime_sec"),
                "end_reason": lifecycle.get("end_reason"),
                "cancel_aggressive_count_during_quote": lifecycle.get(
                    "cancel_aggressive_count_during_quote"
                ),
                "quote_fade_count_during_quote": lifecycle.get(
                    "quote_fade_count_during_quote"
                ),
                "tfi_fade_count_during_quote": lifecycle.get(
                    "tfi_fade_count_during_quote"
                ),
            }
        )

    return details


def _float_values(rows: list[dict[str, object]], key: str) -> list[float]:
    return [value for row in rows if (value := _to_float(row.get(key))) is not None]


def _summary_row(
    leg: str,
    aggressive_vs_best: str,
    directional_alignment: str,
    end_reason: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    end_counts = Counter(str(row.get("end_reason") or "") for row in rows)
    mid_values = _float_values(rows, "quote_distance_from_mid_bps")
    micro_values = _float_values(rows, "quote_distance_from_micro_bps")
    best_values = _float_values(rows, "quote_distance_from_best_bps")
    tfi_values = _float_values(rows, "tfi")
    obi_values = _float_values(rows, "obi")
    lifetime_values = _float_values(rows, "quote_lifetime_sec")
    return {
        "leg": leg,
        "aggressive_vs_best": aggressive_vs_best,
        "directional_alignment": directional_alignment,
        "end_reason": end_reason,
        "count": len(rows),
        "mean_quote_distance_from_mid_bps": _mean(mid_values),
        "median_quote_distance_from_mid_bps": median(mid_values) if mid_values else None,
        "mean_quote_distance_from_micro_bps": _mean(micro_values),
        "median_quote_distance_from_micro_bps": median(micro_values)
        if micro_values
        else None,
        "mean_quote_distance_from_best_bps": _mean(best_values),
        "median_quote_distance_from_best_bps": median(best_values) if best_values else None,
        "mean_tfi": _mean(tfi_values),
        "median_tfi": median(tfi_values) if tfi_values else None,
        "mean_obi": _mean(obi_values),
        "median_obi": median(obi_values) if obi_values else None,
        "mean_lifetime_sec": _mean(lifetime_values),
        "median_lifetime_sec": median(lifetime_values) if lifetime_values else None,
        "cancel_aggressive_end_count": end_counts.get("cancel_aggressive", 0),
        "quote_fade_end_count": end_counts.get("quote_fade", 0),
        "tfi_fade_end_count": end_counts.get("tfi_fade", 0),
    }


def _build_summary(details: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in details:
        key = (
            str(row.get("leg") or ""),
            str(row.get("aggressive_vs_best") or ""),
            str(row.get("directional_alignment") or ""),
            str(row.get("end_reason") or ""),
        )
        grouped[key].append(row)
    return [
        _summary_row(*key, rows)
        for key, rows in sorted(grouped.items(), key=lambda item: item[0])
    ]


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
