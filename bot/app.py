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
from .marketdata import book as book_md
from .marketdata.funding import FundingCache
from .oms.oms import OMS
from .risk.guards import RiskGuards
from .strategy.mm_funding import MMFundingStrategy
from .types import ExecutionEvent, InstType, OrderIntent, Side


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


def _sim_fill_sides(raw: str) -> list[Side]:
    mode = (raw or "").strip().lower()
    if mode == "buy":
        return [Side.BUY]
    if mode == "sell":
        return [Side.SELL]
    return [Side.BUY, Side.SELL]


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


async def _simulate_fills_loop(
    *,
    config,
    gateway,
    oms,
    logger,
    interval_sec: float,
    fill_qty: float,
    fill_side: str,
    simulate_hedge_success: bool,
) -> None:
    if interval_sec <= 0:
        interval_sec = 5.0
    if fill_qty <= 0:
        fill_qty = 0.01

    seq = 0
    while True:
        await asyncio.sleep(interval_sec)
        try:
            channel = gateway.public_book_channel
            perp_snapshot = book_md.snapshot_from_store(
                gateway.store,
                InstType.USDT_FUTURES,
                config.symbols.perp.symbol,
                levels=1,
                channel=channel,
            )
            spot_snapshot = book_md.snapshot_from_store(
                gateway.store,
                InstType.SPOT,
                config.symbols.spot.symbol,
                levels=1,
                channel=channel,
            )
            if perp_snapshot is None:
                continue
            perp_bbo = book_md.bbo_from_snapshot(perp_snapshot)
            spot_bbo = book_md.bbo_from_snapshot(spot_snapshot) if spot_snapshot is not None else None

            for side in _sim_fill_sides(fill_side):
                seq += 1
                ts = time.time()
                perp_px = perp_bbo.ask if side == Side.BUY else perp_bbo.bid
                intent_prefix = OrderIntent.QUOTE_BID.value if side == Side.BUY else OrderIntent.QUOTE_ASK.value
                perp_fill = ExecutionEvent(
                    inst_type=InstType.USDT_FUTURES,
                    symbol=config.symbols.perp.symbol,
                    order_id=f"SIM-PERP-ORDER-{seq}",
                    client_oid=f"{intent_prefix}-SIM-{int(ts * 1000)}-{seq}",
                    fill_id=f"SIM-PERP-FILL-{int(ts * 1000)}-{seq}",
                    side=side,
                    price=perp_px,
                    size=fill_qty,
                    fee=0.0,
                    ts=ts,
                )
                await oms.ingest_fill(perp_fill, simulated=True, source="sim_fill")

                if not simulate_hedge_success or spot_bbo is None:
                    continue

                hedge_side = Side.SELL if side == Side.BUY else Side.BUY
                spot_px = spot_bbo.ask if hedge_side == Side.BUY else spot_bbo.bid
                ticket_id = oms.latest_open_ticket_id() or f"HEDGE-SIM-{int(ts * 1000)}-{seq}"
                spot_fill = ExecutionEvent(
                    inst_type=InstType.SPOT,
                    symbol=config.symbols.spot.symbol,
                    order_id=f"SIM-SPOT-ORDER-{seq}",
                    client_oid=ticket_id,
                    fill_id=f"SIM-SPOT-FILL-{int(ts * 1000)}-{seq}",
                    side=hedge_side,
                    price=spot_px,
                    size=fill_qty,
                    fee=0.0,
                    ts=ts + 0.001,
                )
                await oms.ingest_fill(spot_fill, simulated=True, source="sim_fill")

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.log(
                {
                    "event": "sim_fill_error",
                    "intent": "SYSTEM",
                    "source": "runtime",
                    "mode": "RUN",
                    "reason": "sim_fill_error",
                    "leg": "sim",
                    "data": {"error": repr(exc)},
                    "simulated": True,
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
    force_private_off = os.environ.get("FORCE_PRIVATE_OFF", "0") == "1"
    private_enabled = not force_private_off
    if force_private_off:
        system_logger.log({"event": "private_disabled", "reason": "force_private_off"})
    else:
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

        sim_fills_enabled = config.strategy.dry_run and (os.getenv("SIMULATE_FILLS", "0") == "1")
        sim_fill_interval_sec = _env_float("SIM_FILL_INTERVAL_SEC", 5.0)
        sim_fill_qty = _env_float("SIM_FILL_QTY", 0.01)
        sim_fill_side = os.getenv("SIM_FILL_SIDE", "both")
        simulate_hedge_success = os.getenv("SIMULATE_HEDGE_SUCCESS", "0") == "1"
        if sim_fills_enabled:
            system_logger.log(
                {
                    "event": "sim_fill_enabled",
                    "intent": "SYSTEM",
                    "source": "runtime",
                    "mode": "RUN",
                    "reason": "sim_fill_enabled",
                    "leg": "sim",
                    "data": {
                        "interval_sec": sim_fill_interval_sec,
                        "fill_qty": sim_fill_qty,
                        "fill_side": sim_fill_side,
                        "simulate_hedge_success": simulate_hedge_success,
                    },
                    "simulated": True,
                }
            )

        tasks = [
            asyncio.create_task(funding_cache.run()),
            asyncio.create_task(strategy.run()),
            asyncio.create_task(monitor_disconnect()),
            loop_lag_task,
        ]
        if private_enabled:
            tasks.append(asyncio.create_task(oms.monitor_fills()))
            tasks.append(asyncio.create_task(oms.sync_positions()))
        if sim_fills_enabled:
            tasks.append(
                asyncio.create_task(
                    _simulate_fills_loop(
                        config=config,
                        gateway=gateway,
                        oms=oms,
                        logger=system_logger,
                        interval_sec=sim_fill_interval_sec,
                        fill_qty=sim_fill_qty,
                        fill_side=sim_fill_side,
                        simulate_hedge_success=simulate_hedge_success,
                    )
                )
            )
        tasks.extend(ws_tasks)
        tasks.append(constraints_task)

        await asyncio.gather(*tasks)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
