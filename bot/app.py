from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

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


async def _loop_lag_probe(logger, *, interval_s: float = 1.0, warn_ms: float = 200.0) -> None:
    # 役割: イベントループ遅延（処理落ち）を計測し、注文/ガードの遅延リスクを可視化する
    last = time.perf_counter()
    while True:
        await asyncio.sleep(interval_s)
        now = time.perf_counter()
        lag_ms = max(0.0, (now - last - interval_s) * 1000.0)
        if lag_ms >= warn_ms:
            if hasattr(logger, "warning"):
                logger.warning("loop_lag lag_ms=%.1f interval_s=%.2f", lag_ms, interval_s)
            elif hasattr(logger, "log"):
                logger.log(
                    {
                        "event": "loop_lag",
                        "intent": "SYSTEM",
                        "source": "runtime",
                        "mode": "RUN",
                        "reason": "loop_lag",
                        "leg": "system",
                        "data": {
                            "lag_ms": lag_ms,
                            "interval_s": interval_s,
                        },
                    }
                )
        last = now


def _log_startup_flags(logger, *, stage: str, private_enabled=None, dry_run=None) -> None:
    # 役割: 起動直後の状態を必ずログへ出し、cancel_allが走らない理由を切り分ける
    env_dry_run = os.environ.get("DRY_RUN")
    if hasattr(logger, "warning"):
        logger.warning(
            "startup_flags stage=%s env_DRY_RUN=%s private_enabled=%s dry_run=%s",
            stage,
            env_dry_run,
            private_enabled,
            dry_run,
        )
    elif hasattr(logger, "log"):
        logger.log(
            {
                "event": "startup_flags",
                "intent": "SYSTEM",
                "source": "startup",
                "mode": "INIT",
                "reason": "startup_flags",
                "leg": "system",
                "data": {
                    "stage": stage,
                    "env_DRY_RUN": env_dry_run,
                    "private_enabled": private_enabled,
                    "dry_run": dry_run,
                },
            }
        )
    print(
        f"[startup_flags] stage={stage} env_DRY_RUN={env_dry_run} "
        f"private_enabled={private_enabled} dry_run={dry_run}",
        flush=True,
    )


async def _cancel_all_on_startup(oms, logger) -> None:
    # 役割: 起動直後に取引所側の未約定注文を全キャンセルし、「残骸ゼロ」を運用前提にする（失敗したら安全側に停止）
    reason = "startup_cancel_all"
    if hasattr(logger, "warning"):
        logger.warning("startup_cancel_all_begin reason=%s", reason)
    elif hasattr(logger, "log"):
        logger.log(
            {
                "event": "startup_cancel_all_begin",
                "intent": "SYSTEM",
                "source": "startup",
                "mode": "INIT",
                "reason": reason,
                "leg": "orders",
                "data": {"reason": reason},
            }
        )
    try:
        await oms.cancel_all(reason=reason)
    except Exception as e:
        if hasattr(logger, "exception"):
            logger.exception("startup_cancel_all_failed reason=%s err=%s", reason, e)
        elif hasattr(logger, "log"):
            logger.log(
                {
                    "event": "startup_cancel_all_failed",
                    "intent": "SYSTEM",
                    "source": "startup",
                    "mode": "INIT",
                    "reason": reason,
                    "leg": "orders",
                    "data": {"reason": reason, "error": repr(e)},
                }
            )
        raise
    if hasattr(logger, "warning"):
        logger.warning("startup_cancel_all_done reason=%s", reason)
    elif hasattr(logger, "log"):
        logger.log(
            {
                "event": "startup_cancel_all_done",
                "intent": "SYSTEM",
                "source": "startup",
                "mode": "INIT",
                "reason": reason,
                "leg": "orders",
                "data": {"reason": reason},
            }
        )


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

    log_dir = Path(os.environ.get("LOG_DIR") or os.environ.get("LOG_PATH") or "logs")  # LOG_PATHは旧env名として互換維持し、LOG_DIRを優先する
    system_logger = JsonlLogger(os.path.join(log_dir, "system.jsonl"))
    orders_logger = JsonlLogger(os.path.join(log_dir, "orders.jsonl"))
    fills_logger = JsonlLogger(os.path.join(log_dir, "fills.jsonl"))
    decision_logger = JsonlLogger(os.path.join(log_dir, "decision.jsonl"))
    _log_startup_flags(system_logger, stage="run_enter")
    env_dry_run = os.environ.get("DRY_RUN")  # 役割: envのDRY_RUNを最優先にし、config由来のdry_runを上書きする
    if env_dry_run in ("0", "1"):  # 役割: 想定値(0/1)のときだけ強制上書きする
        dry_run = (env_dry_run == "1")  # 役割: DRY_RUN=0なら実発注、DRY_RUN=1なら疑似運用に確定する
        config.strategy.dry_run = dry_run
    _log_startup_flags(
        system_logger,
        stage="after_dry_run",
        private_enabled=None,
        dry_run=config.strategy.dry_run,
    )
    loop_lag_task = asyncio.create_task(_loop_lag_probe(system_logger))

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
    _log_startup_flags(
        system_logger,
        stage="after_private_enabled",
        private_enabled=private_enabled,
        dry_run=config.strategy.dry_run,
    )

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
        if private_enabled:  # 役割: dry_runでも残骸注文は事故源なので、privateが有効なら起動時に必ず全キャンセルする
            await _cancel_all_on_startup(oms, system_logger)
            await asyncio.sleep(5)  # 役割: WS/制約/残高の初期化を待つウォームアップ時間を確保し、起動直後の誤発注を防ぐ
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
            loop_lag_task,
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
