from __future__ import annotations

import argparse
import asyncio
import os

import pybotters
from dotenv import load_dotenv

from .config import apply_env_overrides, load_apis, load_config
from .exchange.bitget_gateway import BitgetGateway
from .log.jsonl import JsonlLogger
from .marketdata.funding import FundingCache
from .oms.oms import OMS
from .risk.guards import RiskGuards
from .strategy.mm_funding import MMFundingStrategy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bitget ETH spot/perp MM funding bot")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config YAML",
    )
    return parser.parse_args()


async def _run() -> None:
    args = _parse_args()
    load_dotenv()
    config = load_config(args.config)
    apply_env_overrides(config)

    bot_mode = os.getenv("BOT_MODE", "").strip().lower()
    if bot_mode == "dry":
        config.strategy.dry_run = True
    elif bot_mode == "live":
        config.strategy.dry_run = False

    apis = {}
    private_enabled = True
    try:
        apis = load_apis(config.exchange)
    except ValueError:
        if config.strategy.dry_run:
            private_enabled = False
        else:
            raise

    log_dir = os.getenv("LOG_DIR") or "log"
    orders_logger = JsonlLogger(os.path.join(log_dir, "orders.jsonl"))
    fills_logger = JsonlLogger(os.path.join(log_dir, "fills.jsonl"))
    decision_logger = JsonlLogger(os.path.join(log_dir, "decision.jsonl"))

    async with pybotters.Client(apis=apis) as client:
        store = pybotters.BitgetV2DataStore()
        gateway = BitgetGateway(client, store, config)

        try:
            await gateway.load_constraints()
        except Exception:
            pass

        await gateway.start_public_ws()
        if private_enabled:
            await gateway.start_private_ws()

        funding_cache = FundingCache(gateway)
        risk = RiskGuards(config.risk)
        oms = OMS(gateway, config, orders_logger, fills_logger)
        strategy = MMFundingStrategy(config, funding_cache, oms, risk, decision_logger)

        tasks = [
            asyncio.create_task(funding_cache.run()),
            asyncio.create_task(strategy.run()),
        ]
        if private_enabled:
            tasks.append(asyncio.create_task(oms.monitor_fills()))

        await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
