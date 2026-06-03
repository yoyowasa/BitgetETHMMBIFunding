"""Flatten current Bitget spot/perp account state for the configured symbol.

Default is plan-only. Real orders require both --execute and FLATTEN_ACCOUNT_OK=1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import pybotters

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.config import apply_env_overrides, load_apis, load_config  # noqa: E402
from bot.exchange.bitget_gateway import BitgetGateway  # noqa: E402
from bot.types import Force, InstType, OrderIntent, OrderRequest, OrderType, Side  # noqa: E402

from scripts.check_readonly_account_state import (  # noqa: E402
    _get_json,
    _order_rows,
    _perp_position,
    _rows,
    _spot_balance,
)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _best_bid(payload: dict[str, Any]) -> float | None:
    data = payload.get("data")
    if isinstance(data, dict):
        bids = data.get("bids") or data.get("bidList")
    else:
        bids = None
    if not bids:
        return None
    first = bids[0]
    if isinstance(first, (list, tuple)) and first:
        return _safe_float(first[0])
    if isinstance(first, dict):
        return _safe_float(first.get("price") or first.get("px"))
    return None


async def _state(client: pybotters.Client, config) -> dict[str, Any]:
    spot_symbol = config.symbols.spot.symbol
    perp_symbol = config.symbols.perp.symbol
    product_type = config.symbols.perp.productType or "USDT-FUTURES"
    margin_coin = config.symbols.perp.marginCoin or "USDT"
    base_coin = spot_symbol.removesuffix("USDT")
    spot_orders = await _get_json(
        client, "/api/v2/spot/trade/unfilled-orders", {"symbol": spot_symbol}
    )
    futures_orders = await _get_json(
        client,
        "/api/v2/mix/order/orders-pending",
        {"symbol": perp_symbol, "productType": product_type},
    )
    position = await _get_json(
        client,
        "/api/v2/mix/position/single-position",
        {"symbol": perp_symbol, "productType": product_type, "marginCoin": margin_coin},
    )
    assets = await _get_json(client, "/api/v2/spot/account/assets", {"coin": base_coin})
    spot = _spot_balance(_rows(assets), base_coin)
    return {
        "spot_open_orders": len(_order_rows(spot_orders)),
        "futures_open_orders": len(_order_rows(futures_orders)),
        "futures_position": _perp_position(_rows(position), perp_symbol),
        "spot_available": spot["available"] or 0.0,
        "spot_frozen": spot["frozen"] or 0.0,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--spot-slip-bps", type=float, default=20.0)
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    apply_env_overrides(config)
    apis = load_apis(config.exchange)

    async with pybotters.Client(apis=apis, base_url=config.exchange.base_url) as client:
        store = pybotters.BitgetV2DataStore()
        gateway = BitgetGateway(client, store, config)
        await gateway.load_constraints()
        before = await _state(client, config)
        plan: list[dict[str, Any]] = []
        if before["futures_position"]:
            side = Side.BUY if before["futures_position"] < 0 else Side.SELL
            plan.append(
                {
                    "leg": "futures",
                    "side": side.value,
                    "size": abs(before["futures_position"]),
                    "order_type": "market",
                    "reduce_only": True,
                }
            )
        if before["spot_available"] > 0:
            book = await _get_json(
                client,
                "/api/v2/spot/market/orderbook",
                {"symbol": config.symbols.spot.symbol, "limit": "5"},
            )
            bid = _best_bid(book)
            if bid is None:
                raise RuntimeError("spot best bid unavailable")
            plan.append(
                {
                    "leg": "spot",
                    "side": "sell",
                    "size": before["spot_available"],
                    "order_type": "limit_ioc",
                    "price": bid * (1 - args.spot_slip_bps / 10000.0),
                }
            )

        result: dict[str, Any] = {
            "symbol": config.symbols.perp.symbol,
            "execute": args.execute,
            "before": before,
            "plan": plan,
            "responses": [],
        }
        if args.execute:
            if os.getenv("FLATTEN_ACCOUNT_OK") != "1":
                raise SystemExit("FLATTEN_ACCOUNT_OK=1 is required with --execute")
            if before["spot_open_orders"] or before["futures_open_orders"]:
                raise SystemExit(f"open orders exist, refusing to flatten: {before}")
            if before["futures_position"]:
                side = Side.BUY if before["futures_position"] < 0 else Side.SELL
                req = OrderRequest(
                    inst_type=InstType.USDT_FUTURES,
                    symbol=config.symbols.perp.symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    size=abs(before["futures_position"]),
                    force=Force.IOC,
                    client_oid=f"MANUAL_FLATTEN_FUT-{int(time.time() * 1000)}",
                    intent=OrderIntent.FLATTEN,
                    cycle_id=-1,
                    reduce_only=True,
                )
                result["responses"].append({"leg": "futures", "response": await gateway.place_order(req)})
                await asyncio.sleep(1.0)
            spot_available = (await _state(client, config))["spot_available"]
            if spot_available > 0:
                book = await _get_json(
                    client,
                    "/api/v2/spot/market/orderbook",
                    {"symbol": config.symbols.spot.symbol, "limit": "5"},
                )
                bid = _best_bid(book)
                if bid is None:
                    raise RuntimeError("spot best bid unavailable")
                req = OrderRequest(
                    inst_type=InstType.SPOT,
                    symbol=config.symbols.spot.symbol,
                    side=Side.SELL,
                    order_type=OrderType.LIMIT,
                    size=spot_available,
                    force=Force.IOC,
                    client_oid=f"MANUAL_FLATTEN_SPOT-{int(time.time() * 1000)}",
                    intent=OrderIntent.FLATTEN,
                    cycle_id=-1,
                    price=bid * (1 - args.spot_slip_bps / 10000.0),
                )
                result["responses"].append({"leg": "spot", "response": await gateway.place_order(req)})
                await asyncio.sleep(2.0)
            result["after"] = await _state(client, config)

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
