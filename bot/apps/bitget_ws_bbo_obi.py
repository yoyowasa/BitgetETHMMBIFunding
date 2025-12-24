from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Iterable

import pybotters
from dotenv import load_dotenv


WS_PUBLIC = "wss://ws.bitget.com/v2/ws/public"


@dataclass
class BBO:
    bid: float | None
    ask: float | None
    ts_ms: int


def _now_ms() -> int:
    return int(time.time() * 1000)


def _best_prices(levels: Iterable[dict]) -> tuple[float | None, float | None]:
    bids = [float(x["price"]) for x in levels if x.get("side") == "bids"]
    asks = [float(x["price"]) for x in levels if x.get("side") == "asks"]
    bid = max(bids) if bids else None
    ask = min(asks) if asks else None
    return bid, ask


def _obi(levels: list[dict], top_n: int = 10) -> float | None:
    bids = sorted(
        (x for x in levels if x.get("side") == "bids"),
        key=lambda d: float(d["price"]),
        reverse=True,
    )[:top_n]
    asks = sorted((x for x in levels if x.get("side") == "asks"), key=lambda d: float(d["price"]))[
        :top_n
    ]

    bid_vol = sum(float(x.get("amount", 0.0)) for x in bids)
    ask_vol = sum(float(x.get("amount", 0.0)) for x in asks)
    denom = bid_vol + ask_vol
    if denom <= 0:
        return None
    return (bid_vol - ask_vol) / denom


async def _subscribe_books(ws, inst_type: str, symbol: str, channel: str = "books1") -> None:
    await ws.send_json(
        {
            "op": "subscribe",
            "args": [
                {
                    "instType": inst_type,
                    "channel": channel,
                    "instId": symbol,
                }
            ],
        }
    )


async def main_async(symbol: str = "ETHUSDT") -> None:
    spot_symbol = os.getenv("SPOT_SYMBOL", os.getenv("SYMBOL", symbol))
    perp_symbol = os.getenv("PERP_SYMBOL", os.getenv("SYMBOL", symbol))

    store = pybotters.BitgetV2DataStore()

    async with pybotters.Client() as client:
        async with client.ws_connect(
            WS_PUBLIC, send_str="ping", hdlr_json=store.onmessage, auth=None
        ) as ws:
            await _subscribe_books(ws, "SPOT", spot_symbol, channel="books1")
            await _subscribe_books(ws, "USDT-FUTURES", perp_symbol, channel="books1")

            await store.book.wait()

            last_print = 0
            while True:
                await asyncio.sleep(0.2)
                now = _now_ms()
                if now - last_print < 1000:
                    continue
                last_print = now

                for inst_type, inst_id in (("SPOT", spot_symbol), ("USDT-FUTURES", perp_symbol)):
                    levels = list(store.book.find({"instType": inst_type, "instId": inst_id}))
                    if not levels:
                        print(f"[{inst_type}] no book yet")
                        continue

                    bid, ask = _best_prices(levels)
                    obi = _obi(levels, top_n=10)
                    mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None

                    print(
                        f"[{inst_type}] {inst_id} "
                        f"bid={bid} ask={ask} mid={mid} "
                        f"OBI10={None if obi is None else round(obi, 4)} "
                        f"levels={len(levels)}"
                    )


def main() -> None:
    load_dotenv()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
