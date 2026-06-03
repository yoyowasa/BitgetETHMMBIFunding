"""Read-only Bitget account state check.

Uses authenticated GET endpoints only. It does not place, cancel, or close orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import pybotters

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.config import apply_env_overrides, load_apis, load_config  # noqa: E402


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("orderList", "entrustedList", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [data]
    return []


def _order_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("orderList", "entrustedList", "list"):
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        return []
    if data.get("orderId") or data.get("ordId") or data.get("id"):
        return [data]
    return []


def _spot_balance(rows: list[dict[str, Any]], coin: str) -> dict[str, float | None]:
    coin = coin.upper()
    available = None
    frozen = None
    for row in rows:
        row_coin = row.get("coin") or row.get("coinName") or row.get("currency") or row.get("asset")
        if row_coin is not None and str(row_coin).upper() != coin:
            continue
        for key in ("available", "availableBalance", "availableAmount", "free", "normalBalance"):
            available = _safe_float(row.get(key))
            if available is not None:
                break
        for key in ("frozen", "frozenBalance", "lock", "locked", "hold"):
            frozen = _safe_float(row.get(key))
            if frozen is not None:
                break
        break
    return {"available": available, "frozen": frozen}


def _perp_position(rows: list[dict[str, Any]], symbol: str) -> float:
    total = 0.0
    for row in rows:
        row_symbol = row.get("symbol") or row.get("instId")
        if row_symbol is not None and str(row_symbol) != symbol:
            continue
        size = None
        for key in ("total", "holdVol", "pos", "size", "quantity"):
            size = _safe_float(row.get(key))
            if size is not None:
                break
        if size is None:
            continue
        side = str(row.get("holdSide") or row.get("posSide") or "").lower()
        if side in {"short", "sell"}:
            size = -abs(size)
        elif side in {"long", "buy"}:
            size = abs(size)
        total += size
    return total


def _order_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": row.get("orderId") or row.get("ordId") or row.get("id"),
        "client_oid": row.get("clientOid") or row.get("clientOrderId") or row.get("clOrdId"),
        "side": row.get("side"),
        "price": row.get("price") or row.get("px"),
        "size": row.get("size") or row.get("sz") or row.get("baseVolume"),
        "status": row.get("status") or row.get("state"),
        "c_time": row.get("cTime") or row.get("createdTime") or row.get("uTime"),
    }


async def _get_json(client: pybotters.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = await client.get(path, params=params)
    body = await resp.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"{path} returned non-dict payload: {body!r}")
    return body


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = parser.parse_args()

    load_dotenv()
    config = load_config(args.config)
    apply_env_overrides(config)
    apis = load_apis(config.exchange)

    spot_symbol = config.symbols.spot.symbol
    perp_symbol = config.symbols.perp.symbol
    product_type = config.symbols.perp.productType or "USDT-FUTURES"
    margin_coin = config.symbols.perp.marginCoin or "USDT"
    base_coin = spot_symbol.removesuffix("USDT")

    async with pybotters.Client(apis=apis, base_url=config.exchange.base_url) as client:
        spot_orders = await _get_json(
            client,
            "/api/v2/spot/trade/unfilled-orders",
            {"symbol": spot_symbol},
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
        assets = await _get_json(
            client,
            "/api/v2/spot/account/assets",
            {"coin": base_coin},
        )

    spot_order_rows = _order_rows(spot_orders)
    futures_order_rows = _order_rows(futures_orders)
    spot_open_orders = len(spot_order_rows)
    futures_open_orders = len(futures_order_rows)
    futures_position = _perp_position(_rows(position), perp_symbol)
    spot = _spot_balance(_rows(assets), base_coin)

    result = {
        "read_only": True,
        "spot_symbol": spot_symbol,
        "perp_symbol": perp_symbol,
        "spot_open_orders": spot_open_orders,
        "futures_open_orders": futures_open_orders,
        "spot_order_details": [_order_summary(row) for row in spot_order_rows[:10]],
        "futures_order_details": [_order_summary(row) for row in futures_order_rows[:10]],
        "futures_position": futures_position,
        "spot_base_coin": base_coin,
        "spot_available": spot["available"],
        "spot_frozen": spot["frozen"],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
