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

    log_dir = os.getenv("LOG_DIR") or "log"
    system_logger = JsonlLogger(os.path.join(log_dir, "system.jsonl"))
    orders_logger = JsonlLogger(os.path.join(log_dir, "orders.jsonl"))
    fills_logger = JsonlLogger(os.path.join(log_dir, "fills.jsonl"))
    decision_logger = JsonlLogger(os.path.join(log_dir, "decision.jsonl"))

    apis = {}
    private_enabled = True
    try:
        apis = load_apis(config.exchange)
    except ValueError:
        if config.strategy.dry_run:
            private_enabled = False
            system_logger.log({"event": "private_disabled", "reason": "missing_api_keys"})
        else:
            raise

    async with pybotters.Client(apis=apis) as client:
        store = pybotters.BitgetV2DataStore()
        ws_disconnect_event = asyncio.Event()
        gateway = BitgetGateway(
            client,
            store,
            config,
            logger=system_logger,
            ws_disconnect_event=ws_disconnect_event,
        )
        funding_cache = FundingCache(gateway, logger=system_logger)
        risk = RiskGuards(config.risk)
        oms = OMS(gateway, config, risk, orders_logger, fills_logger)
        strategy = MMFundingStrategy(config, funding_cache, oms, risk, decision_logger)

        try:
            await gateway.load_constraints()
        except Exception as exc:
            system_logger.log({"event": "preflight_failed", "reason": "constraints_error", "error": repr(exc)})
            raise
        if not gateway.constraints.ready():
            system_logger.log({"event": "preflight_failed", "reason": "constraints_not_ready"})
            raise SystemExit("constraints not ready")

        if private_enabled and not config.strategy.dry_run:
            target_pos_mode = os.getenv("TARGET_POS_MODE", "one_way_mode").strip()
            auto_set = os.getenv("AUTO_SET_POS_MODE", "1") == "1"
            current = await gateway.get_pos_mode()
            system_logger.log(
                {
                    "event": "pos_mode",
                    "current": current,
                    "target": target_pos_mode,
                    "auto_set": auto_set,
                }
            )
            if target_pos_mode and current and current != target_pos_mode:
                if auto_set:
                    res = await gateway.set_pos_mode(target_pos_mode)
                    system_logger.log(
                        {
                            "event": "pos_mode_set",
                            "target": target_pos_mode,
                            "res": res,
                        }
                    )
                    current = await gateway.get_pos_mode()
                    system_logger.log(
                        {
                            "event": "pos_mode",
                            "current": current,
                            "target": target_pos_mode,
                            "auto_set": auto_set,
                        }
                    )
                if current != target_pos_mode:
                    raise SystemExit(
                        f"posMode mismatch: current={current} target={target_pos_mode}. "
                        f"Close all futures positions/orders for productType={config.symbols.perp.productType} and retry."
                    )

        try:
            await funding_cache.update_once()
        except Exception as exc:
            system_logger.log({"event": "preflight_failed", "reason": "funding_error", "error": repr(exc)})
            raise
        if funding_cache.last is None and not config.strategy.dry_run:
            system_logger.log({"event": "preflight_failed", "reason": "funding_unavailable"})
            raise SystemExit("funding unavailable")

        ws_tasks = [asyncio.create_task(gateway.run_public_ws())]
        if private_enabled:
            ws_tasks.append(asyncio.create_task(gateway.run_private_ws()))

        constraints_task = asyncio.create_task(gateway.refresh_constraints_loop())

        async def monitor_disconnect() -> None:
            await ws_disconnect_event.wait()
            system_logger.log({"event": "halted", "reason": "ws_disconnect"})
            risk.halt("ws_disconnect")
            await oms.cancel_all(reason="ws_disconnect")

        tasks = [
            asyncio.create_task(funding_cache.run()),
            asyncio.create_task(strategy.run()),
            asyncio.create_task(monitor_disconnect()),
        ]
        if private_enabled:
            tasks.append(asyncio.create_task(oms.monitor_fills()))
            tasks.append(asyncio.create_task(oms.sync_positions()))
        tasks.extend(ws_tasks)
        tasks.append(constraints_task)

        await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
