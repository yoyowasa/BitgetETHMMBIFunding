"""Bitget V2 trade-rate / VIP fee-rate checker.

Fetches the user's actual maker/taker fee rate via /api/v2/common/trade-rate
for both spot and futures (USDT-FUTURES), and the public spot VIP fee tier list,
then compares against config.yaml cost values.

Read-only: only GET endpoints, no order placement.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
import pybotters

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.config import load_apis, load_config  # noqa: E402


def _bps(rate_str: str | float | None) -> float | None:
    if rate_str is None or rate_str == "":
        return None
    try:
        return float(rate_str) * 10000.0
    except (TypeError, ValueError):
        return None


async def main() -> None:
    load_dotenv()
    config = load_config(str(ROOT / "config.yaml"))
    apis = load_apis(config.exchange)
    base_url = config.exchange.base_url

    spot_symbol = config.symbols.spot.symbol
    perp_symbol = config.symbols.perp.symbol
    perp_product = config.symbols.perp.productType or "USDT-FUTURES"

    print("=== Bitget Trade Rate / VIP Fee Check ===")
    print(f"base_url={base_url}")
    print(f"spot_symbol={spot_symbol}  perp_symbol={perp_symbol}  productType={perp_product}")
    print()

    async with pybotters.Client(apis=apis, base_url=base_url) as client:
        # 1. /api/v2/common/trade-rate (auth) - try several businessType candidates
        for business_type in ["spot", "USDT-FUTURES", "mix", "umcbl"]:
            symbol = spot_symbol if business_type == "spot" else perp_symbol
            params = {"symbol": symbol, "businessType": business_type}
            try:
                resp = await client.get("/api/v2/common/trade-rate", params=params)
                body = await resp.json()
            except Exception as exc:
                print(f"[trade-rate businessType={business_type}] EXCEPTION: {exc!r}")
                continue
            print(f"[trade-rate businessType={business_type}] params={params}")
            print(f"  status={resp.status}")
            print(f"  body={body}")
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                m = _bps(data.get("makerFeeRate"))
                t = _bps(data.get("takerFeeRate"))
                print(f"  -> makerFeeRate_bps={m}  takerFeeRate_bps={t}")
            elif isinstance(data, list):
                for row in data:
                    m = _bps(row.get("makerFeeRate"))
                    t = _bps(row.get("takerFeeRate"))
                    print(f"  -> row={row}  maker_bps={m}  taker_bps={t}")
            print()

        # 2. /api/v2/spot/market/vip-fee-rate (public spot VIP tiers)
        try:
            resp = await client.get("/api/v2/spot/market/vip-fee-rate")
            body = await resp.json()
            print(f"[spot vip-fee-rate] status={resp.status}")
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, list):
                print(f"  tiers={len(data)}")
                for row in data[:6]:
                    print(f"    {row}")
            else:
                print(f"  body={body}")
        except Exception as exc:
            print(f"[spot vip-fee-rate] EXCEPTION: {exc!r}")
        print()

        # 3. /api/v2/mix/market/vip-fee-rate (public futures VIP tiers, may not exist)
        try:
            resp = await client.get(
                "/api/v2/mix/market/vip-fee-rate",
                params={"productType": perp_product},
            )
            body = await resp.json()
            print(f"[mix vip-fee-rate] status={resp.status}")
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, list):
                print(f"  tiers={len(data)}")
                for row in data[:6]:
                    print(f"    {row}")
            else:
                print(f"  body={body}")
        except Exception as exc:
            print(f"[mix vip-fee-rate] EXCEPTION: {exc!r}")
        print()

    print("=== config cost (current) ===")
    print(f"fee_maker_perp_bps = {config.cost.fee_maker_perp_bps}")
    print(f"fee_taker_spot_bps = {config.cost.fee_taker_spot_bps}")
    print(f"slippage_bps       = {config.cost.slippage_bps}")
    print()
    print("Note: makerFeeRate / takerFeeRate are decimal rates. _bps = rate * 10000.")


if __name__ == "__main__":
    asyncio.run(main())
