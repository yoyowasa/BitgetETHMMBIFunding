from __future__ import annotations

import time
from typing import Iterable

from ..types import BBO, BookSnapshot, InstType


def snapshot_from_store(
    store,
    inst_type: InstType,
    symbol: str,
    levels: int,
    channel: str | None = None,
    *,
    return_meta: bool = False,
) -> BookSnapshot | None | tuple[BookSnapshot | None, bool]:
    used_channel_filter = True
    book_store = getattr(store, "book", None)
    if book_store is not None and hasattr(book_store, "sorted"):
        try:
            limit = levels if levels > 0 else None
            query = {"instType": inst_type.value, "instId": symbol}
            if channel:
                query["channel"] = channel
            book = book_store.sorted(query, limit=limit)
            snap = _snapshot_from_sorted(book, levels)
            if snap is None and channel:
                book = book_store.sorted(
                    {"instType": inst_type.value, "instId": symbol}, limit=limit
                )
                snap = _snapshot_from_sorted(book, levels)
                if snap is not None:
                    used_channel_filter = False
            if snap is not None:
                return (snap, used_channel_filter) if return_meta else snap
        except Exception:
            pass

    rows = _rows_for(store, inst_type, symbol, channel=channel)
    if not rows and channel:
        rows = _rows_for(store, inst_type, symbol, channel=None)
        if rows:
            used_channel_filter = False
    if not rows:
        return (None, used_channel_filter) if return_meta else None

    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    latest_ts: float | None = None

    for row in rows:
        side = _side_from_row(row.get("side"))
        if side is None:
            continue
        price = _to_float(row.get("price") or row.get("px"))
        size = _to_float(row.get("size") or row.get("amount") or row.get("qty") or row.get("sz"))
        if price is None or size is None:
            continue
        if side == "bid":
            bids.append((price, size))
        elif side == "ask":
            asks.append((price, size))

        latest_ts = _update_latest_ts(latest_ts, row)

    if not bids or not asks:
        return None

    bids.sort(key=lambda x: x[0], reverse=True)
    asks.sort(key=lambda x: x[0])

    if levels > 0:
        bids = bids[:levels]
        asks = asks[:levels]

    ts = latest_ts if latest_ts is not None else time.time()
    snapshot = BookSnapshot(bids=bids, asks=asks, ts=ts)
    return (snapshot, used_channel_filter) if return_meta else snapshot


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


def _rows_for(
    store, inst_type: InstType, symbol: str, channel: str | None = None
) -> list[dict]:
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
        if channel:
            row_channel = row.get("channel")
            if row_channel != channel:
                continue
        filtered.append(row)
    return filtered


def _snapshot_from_sorted(book: dict, levels: int) -> BookSnapshot | None:
    bids_raw = book.get("bids") or []
    asks_raw = book.get("asks") or []
    if not bids_raw or not asks_raw:
        return None

    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    latest_ts: float | None = None

    for row in bids_raw:
        parsed = _parse_level(row)
        if parsed:
            bids.append(parsed)
        latest_ts = _update_latest_ts(latest_ts, row)

    for row in asks_raw:
        parsed = _parse_level(row)
        if parsed:
            asks.append(parsed)
        latest_ts = _update_latest_ts(latest_ts, row)

    if not bids or not asks:
        return None

    if levels > 0:
        bids = bids[:levels]
        asks = asks[:levels]

    ts = latest_ts if latest_ts is not None else time.time()
    return BookSnapshot(bids=bids, asks=asks, ts=ts)


def _parse_level(row) -> tuple[float, float] | None:
    if isinstance(row, (list, tuple)) and len(row) >= 2:
        price = _to_float(row[0])
        size = _to_float(row[1])
    elif isinstance(row, dict):
        price = _to_float(row.get("price") or row.get("px"))
        size = _to_float(row.get("size") or row.get("amount") or row.get("qty") or row.get("sz"))
    else:
        return None
    if price is None or size is None:
        return None
    return price, size


def _side_from_row(value) -> str | None:
    if value is None:
        return None
    side = str(value).lower()
    if side in ("buy", "bid", "bids"):
        return "bid"
    if side in ("sell", "ask", "asks"):
        return "ask"
    return None


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


def _update_latest_ts(latest_ts: float | None, row) -> float | None:
    if not isinstance(row, dict):
        return latest_ts
    row_ts = _normalize_ts(row.get("ts") or row.get("timestamp") or row.get("time"))
    if row_ts is None:
        return latest_ts
    return row_ts if latest_ts is None else max(latest_ts, row_ts)
