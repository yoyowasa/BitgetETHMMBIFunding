from __future__ import annotations

import asyncio
import time
from typing import Iterable

from ..types import BBO, BookSnapshot, InstType

def _ws_inst_id(raw_symbol: str) -> str:
    # 役割: WS購読用のinstIdに正規化する（例: ETHUSDT_UMCBL -> ETHUSDT）
    return (raw_symbol or "").split("_", 1)[0]

_BOOK_STAT_T0 = time.time()  # 役割: 集計開始時刻（秒）
_BOOK_STAT_N = 0  # 役割: 集計区間の受信メッセージ数
_BOOK_STAT_LEVELS = 0  # 役割: 集計区間の（bids+asks）レベル数合計
_BOOK_READY_EVENTS: dict[tuple[str, str, str], asyncio.Event] = {}  # 役割: (instType, channel, instId)ごとの板ブート到達をラッチする
_BOOK_FIRST_PUSH_SEEN = set()  # 役割: (instType, channel, instId) 単位で最初の板プッシュだけをログ出しするための集合


def _stat_book_msg(logger, msg: dict, *, interval_s: float = 60.0) -> None:
    # 役割: books の負荷（受信頻度/平均レベル数）を一定間隔でログに出し、重さを数字で判断できるようにする
    global _BOOK_STAT_T0, _BOOK_STAT_N, _BOOK_STAT_LEVELS

    _BOOK_STAT_N += 1

    levels = 0
    try:
        data = msg.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            bids = data[0].get("bids") or []
            asks = data[0].get("asks") or []
            if isinstance(bids, list):
                levels += len(bids)
            if isinstance(asks, list):
                levels += len(asks)
    except Exception:
        levels = 0

    _BOOK_STAT_LEVELS += levels

    now = time.time()
    dt = now - _BOOK_STAT_T0
    if dt >= interval_s:
        mps = (_BOOK_STAT_N / dt) if dt > 0 else 0.0
        avg_levels = (_BOOK_STAT_LEVELS / _BOOK_STAT_N) if _BOOK_STAT_N > 0 else 0.0
        if logger is not None:
            if hasattr(logger, "info"):
                logger.info(
                    "book_rx_rate interval_s=%.1f msgs=%d msgs_per_sec=%.3f avg_levels=%.1f",
                    dt,
                    _BOOK_STAT_N,
                    mps,
                    avg_levels,
                )
            elif hasattr(logger, "log"):
                logger.log(
                    {
                        "event": "book_rx_rate",
                        "intent": "SYSTEM",
                        "source": "marketdata",
                        "mode": "RUN",
                        "reason": "book_rx_rate",
                        "leg": "books",
                        "data": {
                            "interval_s": round(dt, 3),
                            "msgs": _BOOK_STAT_N,
                            "msgs_per_sec": mps,
                            "avg_levels": avg_levels,
                        },
                    }
                )
        _BOOK_STAT_T0 = now
        _BOOK_STAT_N = 0
        _BOOK_STAT_LEVELS = 0


def _latch_book_ready(inst_type: str, channel: str, inst_id: str) -> None:
    # 役割: snapshot到着をラッチして、wait開始前に届いても取りこぼさない
    key = (str(inst_type), str(channel), str(inst_id))
    evt = _BOOK_READY_EVENTS.setdefault(key, asyncio.Event())
    evt.set()


async def _wait_for_book_bootstrap(
    logger, inst_type: str, channel: str, inst_id: str, timeout_s: float
) -> bool:
    # 役割: snapshotがwait開始前に到着しても落ちないように、Eventをラッチとして使ってブート完了を判定する
    key = (str(inst_type), str(channel), str(inst_id))  # 役割: 購読単位のキー
    evt = _BOOK_READY_EVENTS.setdefault(key, asyncio.Event())  # 役割: 受信側と同じEventを参照する

    if evt.is_set():
        return True  # 役割: 既に到着済みなら即OK（取りこぼし防止）

    try:
        await asyncio.wait_for(evt.wait(), timeout=timeout_s)  # 役割: 到着を待つ
        return True
    except asyncio.TimeoutError:
        return evt.is_set()  # 役割: タイムアウトでも到着済みに変わっていればOK、未到着ならFalse


def _is_book_push(msg: dict) -> bool:
    # 役割: Bitgetの板プッシュを構造で判定する（action文字列の揺れ/欠落に依存しない）
    arg = msg.get("arg")
    if not isinstance(arg, dict):
        return False
    ch = arg.get("channel")
    if not isinstance(ch, str) or not ch.startswith("books"):
        return False
    data = msg.get("data")
    if not isinstance(data, list) or not data:
        return False
    head = data[0]
    if not isinstance(head, dict):
        return False
    return ("bids" in head) and ("asks" in head)


def _log_first_book_push(logger, msg: dict) -> None:
    # 役割: 最初の板プッシュを1回だけログに出し、未配信か取りこぼしかを確定する
    arg = msg.get("arg") if isinstance(msg.get("arg"), dict) else {}
    inst_type = arg.get("instType", "?")
    channel = arg.get("channel", "?")
    inst_id = arg.get("instId", "?")
    key = (str(inst_type), str(channel), str(inst_id))
    if key in _BOOK_FIRST_PUSH_SEEN:
        return
    _BOOK_FIRST_PUSH_SEEN.add(key)

    action = msg.get("action")
    data0 = msg.get("data")[0] if isinstance(msg.get("data"), list) and msg.get("data") else {}
    data0_keys = sorted(list(data0.keys())) if isinstance(data0, dict) else []
    if logger is not None:
        if hasattr(logger, "info"):
            logger.info(
                "book_first_push instType=%s channel=%s instId=%s action=%s data0_keys=%s",
                inst_type,
                channel,
                inst_id,
                action,
                data0_keys,
            )
        elif hasattr(logger, "log"):
            logger.log(
                {
                    "event": "book_first_push",
                    "intent": "SYSTEM",
                    "source": "marketdata",
                    "mode": "RUN",
                    "reason": "book_first_push",
                    "leg": "books",
                    "data": {
                        "instType": inst_type,
                        "channel": channel,
                        "instId": inst_id,
                        "action": action,
                        "data0_keys": data0_keys,
                    },
                }
            )


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
