from __future__ import annotations

import time
from typing import Iterable

from ..types import BBO, BookSnapshot, InstType


def snapshot_from_store(store, inst_type: InstType, symbol: str, levels: int) -> BookSnapshot | None:
    rows = _rows_for(store, inst_type, symbol)
    if not rows:
        return None

    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    latest_ts: float | None = None

    for row in rows:
        side = str(row.get("side", "")).lower()
        price = _to_float(row.get("price"))
        size = _to_float(row.get("size"))
        if price is None or size is None:
            continue
        if side in ("buy", "bid"):
            bids.append((price, size))
        elif side in ("sell", "ask"):
            asks.append((price, size))

        row_ts = _normalize_ts(row.get("ts") or row.get("timestamp") or row.get("time"))
        if row_ts is not None:
            latest_ts = row_ts if latest_ts is None else max(latest_ts, row_ts)

    if not bids or not asks:
        return None

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    if levels > 0:
        bids = bids[:levels]
        asks = asks[:levels]

    ts = latest_ts if latest_ts is not None else time.time()
    return BookSnapshot(bids=bids, asks=asks, ts=ts)


def bbo_from_snapshot(snapshot: BookSnapshot) -> BBO:
    bid_px, bid_sz = snapshot.bids[0]
    ask_px, ask_sz = snapshot.asks[0]
    return BBO(bid=bid_px, ask=ask_px, bid_size=bid_sz, ask_size=ask_sz, ts=snapshot.ts)


def calc_mid(bbo: BBO) -> float:
    return (bbo.bid + bbo.ask) / 2.0


def calc_obi(snapshot: BookSnapshot) -> float:
    bid_qty = sum(size for _, size in snapshot.bids)
    ask_qty = sum(size for _, size in snapshot.asks)
    denom = bid_qty + ask_qty
    if denom <= 0:
        return 0.0
    return (bid_qty - ask_qty) / denom


def calc_microprice(bbo: BBO) -> float:
    denom = bbo.bid_size + bbo.ask_size
    if denom <= 0:
        return (bbo.bid + bbo.ask) / 2.0
    return (bbo.ask * bbo.bid_size + bbo.bid * bbo.ask_size) / denom


def _rows_for(store, inst_type: InstType, symbol: str) -> list[dict]:
    try:
        rows = list(store.book.find())
    except Exception:
        return []

    filtered: list[dict] = []
    for row in rows:
        row_symbol = row.get("instId") or row.get("symbol")
        if symbol and row_symbol != symbol:
            continue
        row_inst_type = row.get("instType")
        if row_inst_type and row_inst_type != inst_type.value:
            continue
        filtered.append(row)
    return filtered


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_ts(value) -> float | None:
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        return ts / 1000.0
    return ts
