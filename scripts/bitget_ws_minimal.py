"""
Bitget V2 WS minimal (pybotters)
- public: books5 (SPOT + USDT-FUTURES)
- private: orders / fill / positions
- compute: BBO + OBI (top 5 levels)
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional, Tuple

import pybotters


PUBLIC_WS = "wss://ws.bitget.com/v2/ws/public"
PRIVATE_WS = "wss://ws.bitget.com/v2/ws/private"

SYMBOL = "ETHUSDT"
SPOT = "SPOT"
PERP = "USDT-FUTURES"

BOOK_CHANNEL = "books5"


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _amount(row: Dict[str, Any]) -> float:
    return _f(row.get("amount") or row.get("size") or row.get("qty"))


def _get_book_sorted(
    store: pybotters.BitgetV2DataStore,
    inst_type: str,
    inst_id: str,
) -> Dict[str, Any]:
    book_store = store.book

    if hasattr(book_store, "sorted"):
        return book_store.sorted({"instType": inst_type, "instId": inst_id})

    try:
        rows = list(book_store.find({"instType": inst_type, "instId": inst_id}))
    except Exception:
        rows = []

    bids, asks = [], []
    for row in rows:
        side = str(row.get("side") or "").lower()
        if side in ("bids", "bid", "buy"):
            bids.append(row)
        elif side in ("asks", "ask", "sell"):
            asks.append(row)

    bids.sort(key=lambda r: _f(r.get("price")), reverse=True)
    asks.sort(key=lambda r: _f(r.get("price")))
    return {"bids": bids, "asks": asks}


def bbo_and_obi(
    store: pybotters.BitgetV2DataStore,
    inst_type: str,
    inst_id: str,
    depth: int = 5,
) -> Optional[Tuple[float, float, float, float]]:
    book = _get_book_sorted(store, inst_type, inst_id)
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None

    best_bid = _f(bids[0].get("price"))
    best_ask = _f(asks[0].get("price"))
    mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
    spread_bps = ((best_ask - best_bid) / mid * 1e4) if mid > 0 else 0.0

    depth = min(depth, len(bids), len(asks))
    bid_amt = sum(_amount(bids[i]) for i in range(depth))
    ask_amt = sum(_amount(asks[i]) for i in range(depth))
    denom = bid_amt + ask_amt
    obi = (bid_amt - ask_amt) / denom if denom > 0 else 0.0

    return best_bid, best_ask, spread_bps, obi


async def printer_loop(store: pybotters.BitgetV2DataStore) -> None:
    await store.book.wait()

    while True:
        now = time.strftime("%H:%M:%S")

        for inst_type in (SPOT, PERP):
            res = bbo_and_obi(store, inst_type, SYMBOL, depth=5)
            if res is None:
                continue
            bid, ask, spr_bps, obi = res
            print(
                f"{now} {inst_type:12s} {SYMBOL}  "
                f"bid={bid:.2f} ask={ask:.2f}  spr={spr_bps:.2f}bps  OBI5={obi:+.3f}"
            )

        try:
            pos_list = list(store.positions.find({"instType": PERP}))
        except Exception:
            pos_list = []
        pos = next((p for p in pos_list if p.get("instId") == SYMBOL), None)
        if pos:
            print(
                f"  POS {SYMBOL} holdSide={pos.get('holdSide')} total={pos.get('total')} "
                f"avg={pos.get('openPriceAvg')} uPL={pos.get('unrealizedPL')}"
            )

        await asyncio.sleep(1.0)


async def main() -> None:
    key = os.getenv("BITGET_API_KEY")
    secret = os.getenv("BITGET_API_SECRET")
    passphrase = os.getenv("BITGET_API_PASSPHRASE")
    if not (key and secret and passphrase):
        raise SystemExit("Set BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE")

    # pybotters は [API_KEY, API_SECRET, API_PASSPHRASE] の3要素リストを期待する
    apis = {"bitget": [key, secret, passphrase]}
    store = pybotters.BitgetV2DataStore()

    pub_sub = {
        "op": "subscribe",
        "args": [
            {"instType": SPOT, "channel": BOOK_CHANNEL, "instId": SYMBOL},
            {"instType": PERP, "channel": BOOK_CHANNEL, "instId": SYMBOL},
        ],
    }

    prv_sub = {
        "op": "subscribe",
        "args": [
            {"instType": SPOT, "channel": "orders", "instId": SYMBOL},
            {"instType": SPOT, "channel": "fill", "instId": "default"},
            {"instType": PERP, "channel": "orders", "instId": "default"},
            {"instType": PERP, "channel": "fill", "instId": "default"},
            {"instType": PERP, "channel": "positions", "instId": "default"},
        ],
    }

    async with pybotters.Client(apis=apis) as client:
        ws_pub = await client.ws_connect(
            PUBLIC_WS,
            send_json=pub_sub,
            hdlr_json=store.onmessage,
            auth=None,
        )
        ws_prv = await client.ws_connect(
            PRIVATE_WS,
            send_json=prv_sub,
            hdlr_json=store.onmessage,
        )

        await asyncio.gather(
            ws_pub.wait(),
            ws_prv.wait(),
            printer_loop(store),
        )


if __name__ == "__main__":
    asyncio.run(main())
