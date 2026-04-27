from __future__ import annotations

import asyncio

from bot.oms.oms import OMS
from bot.types import OrderIntent
from tests.test_oms_lock import DummyGateway, DummyLogger, _config


def test_dryrun_quote_registers_virtual_active_quote() -> None:
    async def runner() -> None:
        config = _config()
        config.strategy.dry_run = True
        oms = OMS(
            DummyGateway(),
            config,
            risk=None,
            orders_logger=DummyLogger(),
            fills_logger=DummyLogger(),
        )

        await oms.update_quotes(
            bid_px=100.0,
            ask_px=100.2,
            bid_size=1.0,
            ask_size=1.0,
            cycle_id=1,
            reason="quote",
        )

        snapshot = oms.active_quote_snapshot(config.symbols.perp.symbol)
        assert snapshot["has_active_quote"] is True
        assert snapshot["source"] == "dry_run_virtual"
        assert snapshot["active_bid_px"] == 100.0
        assert snapshot["active_ask_px"] == 100.2
        assert snapshot["active_bid_qty"] == 1.0
        assert snapshot["active_ask_qty"] == 1.0
        assert str(snapshot["active_bid_order_id"]).startswith("dryrun:")
        assert str(snapshot["active_ask_order_id"]).startswith("dryrun:")

    asyncio.run(runner())


def test_dryrun_virtual_quote_clears_on_cancel_all() -> None:
    async def runner() -> None:
        config = _config()
        config.strategy.dry_run = True
        oms = OMS(
            DummyGateway(),
            config,
            risk=None,
            orders_logger=DummyLogger(),
            fills_logger=DummyLogger(),
        )
        await oms.update_quotes(
            bid_px=100.0,
            ask_px=100.2,
            bid_size=1.0,
            ask_size=1.0,
            cycle_id=1,
            reason="quote",
        )

        await oms.cancel_all(reason="test_cancel")

        snapshot = oms.active_quote_snapshot(config.symbols.perp.symbol)
        assert snapshot["has_active_quote"] is False
        assert snapshot["source"] == "none"
        assert snapshot["active_bid_px"] is None
        assert snapshot["active_ask_px"] is None

    asyncio.run(runner())


def test_live_quote_snapshot_keeps_live_order_source() -> None:
    async def runner() -> None:
        config = _config()
        config.strategy.dry_run = False
        oms = OMS(
            DummyGateway(),
            config,
            risk=None,
            orders_logger=DummyLogger(),
            fills_logger=DummyLogger(),
        )

        async def fake_submit(req, reason: str):
            return f"live-{req.intent.value}"

        oms._submit_order = fake_submit  # type: ignore[method-assign]
        await oms.update_quotes(
            bid_px=100.0,
            ask_px=100.2,
            bid_size=1.0,
            ask_size=1.0,
            cycle_id=1,
            reason="quote",
        )

        snapshot = oms.active_quote_snapshot(config.symbols.perp.symbol)
        assert snapshot["has_active_quote"] is True
        assert snapshot["source"] == "live_order"
        assert snapshot["active_bid_order_id"] == f"live-{OrderIntent.QUOTE_BID.value}"
        assert snapshot["active_ask_order_id"] == f"live-{OrderIntent.QUOTE_ASK.value}"

    asyncio.run(runner())
