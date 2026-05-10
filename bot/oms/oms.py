from __future__ import annotations

import asyncio
import json
from decimal import Decimal, InvalidOperation
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from ..config import AppConfig, HedgeConfig
from ..exchange.bitget_gateway import BitgetGateway
from ..exchange.constraints import (
    InstrumentConstraints,
    format_price_for_bitget,
    get_price_tick,
    quantize_perp_price,
)
from ..log.jsonl import JsonlLogger
from ..log.pnl_logger import PnLAggregator, QuoteMetrics
from ..marketdata import book as book_md
from ..risk.guards import RiskGuards
from ..types import ExecutionEvent, Force, InstType, OrderIntent, OrderRequest, OrderType, Side


@dataclass
class ActiveOrder:
    order_id: str
    client_oid: str
    price: float
    size: float
    side: Side
    intent: OrderIntent
    ts: float
    symbol: str = ""
    dry_run: bool = False
    source: str = "live_order"


@dataclass
class HedgeTicket:
    ticket_id: str
    symbol: str
    side: Side
    want_qty: float
    filled_qty: float
    created_ts: float
    deadline_ts: float
    tries: int
    status: str
    reason: str
    perp_fill_ts: float
    perp_fill_price: float

    @property
    def remain(self) -> float:
        return max(0.0, self.want_qty - self.filled_qty)


@dataclass(frozen=True)
class HedgeTicketSnapshot:
    ticket_id: str
    remain: float
    deadline_ts: float
    tries: int
    expired: bool


class LRUSet:
    def __init__(self, maxlen: int = 10000):
        self._maxlen = maxlen
        self._data: dict[str, float] = {}

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def add(self, key: str) -> None:
        self._data[key] = time.time()
        if len(self._data) > self._maxlen:
            oldest_key = min(self._data.items(), key=lambda kv: kv[1])[0]
            self._data.pop(oldest_key, None)


class PositionTracker:
    def __init__(self):
        self.spot_pos = 0.0
        self.perp_pos = 0.0

    def apply_fill(self, event: ExecutionEvent) -> None:
        if event.inst_type == InstType.SPOT:
            self.spot_pos += _spot_position_delta_after_fee(event)
        elif event.inst_type == InstType.USDT_FUTURES:
            delta = event.size if event.side == Side.BUY else -event.size
            self.perp_pos += delta


class OMS:
    def __init__(
        self,
        gateway: BitgetGateway,
        config: AppConfig,
        risk: RiskGuards,
        orders_logger: JsonlLogger,
        fills_logger: JsonlLogger,
        pnl_aggregator: PnLAggregator | None = None,
    ):
        self._gateway = gateway
        self._config = config
        self._hedge_cfg: HedgeConfig = config.hedge
        self._risk = risk
        self._orders_logger = orders_logger
        self._fills_logger = fills_logger
        self._pnl = pnl_aggregator
        self._positions = PositionTracker()
        self._seen_fills = LRUSet()
        self._active_quotes: dict[OrderIntent, Optional[ActiveOrder]] = {
            OrderIntent.QUOTE_BID: None,
            OrderIntent.QUOTE_ASK: None,
        }
        self._order_client_map: dict[str, str] = {}
        self._spot_client_ticket: dict[str, str] = {}
        self._spot_order_ticket: dict[str, str] = {}
        self._hedge_tickets: dict[str, HedgeTicket] = {}
        self._symbol_locks: dict[str, asyncio.Lock] = {}
        self._unhedged_qty = 0.0
        self._unhedged_since: Optional[float] = None
        self._dry_run = config.strategy.dry_run
        self._quote_orders = 0
        self._quote_fills = 0
        self._adverse_fills = 0
        self._last_position_log_ts = 0.0
        self._last_logged_spot_pos: float | None = None
        self._last_logged_perp_pos: float | None = None
        self._positions_sync_authoritative = False

    @property
    def positions(self) -> PositionTracker:
        return self._positions

    @property
    def gateway(self) -> BitgetGateway:
        return self._gateway

    @property
    def unhedged_qty(self) -> float:
        return self._unhedged_qty

    @property
    def unhedged_since(self) -> Optional[float]:
        return self._unhedged_since

    def active_quote_snapshot(self, symbol: str | None = None) -> dict[str, object]:
        if symbol is not None and symbol != self._config.symbols.perp.symbol:
            bid = None
            ask = None
        else:
            bid = self._active_quotes.get(OrderIntent.QUOTE_BID)
            ask = self._active_quotes.get(OrderIntent.QUOTE_ASK)
        sources = {order.source for order in (bid, ask) if order is not None}
        source = "none"
        if "live_order" in sources:
            source = "live_order"
        elif "dry_run_virtual" in sources:
            source = "dry_run_virtual"
        return {
            "has_active_quote": bid is not None or ask is not None,
            "active_bid": bid,
            "active_ask": ask,
            "active_bid_px": None if bid is None else bid.price,
            "active_ask_px": None if ask is None else ask.price,
            "active_bid_order_id": None if bid is None else bid.order_id,
            "active_ask_order_id": None if ask is None else ask.order_id,
            "active_bid_client_oid": None if bid is None else bid.client_oid,
            "active_ask_client_oid": None if ask is None else ask.client_oid,
            "active_bid_qty": None if bid is None else bid.size,
            "active_ask_qty": None if ask is None else ask.size,
            "active_bid_ts": None if bid is None else bid.ts,
            "active_ask_ts": None if ask is None else ask.ts,
            "source": source,
        }

    def latest_open_ticket_id(self) -> Optional[str]:
        latest: Optional[HedgeTicket] = None
        for ticket in self._hedge_tickets.values():
            if ticket.status != "OPEN":
                continue
            if latest is None or ticket.created_ts > latest.created_ts:
                latest = ticket
        return None if latest is None else latest.ticket_id

    def has_open_hedge_ticket(self) -> bool:
        return self.open_hedge_ticket_snapshot() is not None

    def open_hedge_ticket_snapshot(self, now: float | None = None) -> HedgeTicketSnapshot | None:
        latest: Optional[HedgeTicket] = None
        for ticket in self._hedge_tickets.values():
            if ticket.status != "OPEN" or ticket.remain <= 1e-9:
                continue
            if latest is None or ticket.created_ts > latest.created_ts:
                latest = ticket
        if latest is None:
            return None
        ts = time.time() if now is None else now
        return HedgeTicketSnapshot(
            ticket_id=latest.ticket_id,
            remain=latest.remain,
            deadline_ts=latest.deadline_ts,
            tries=latest.tries,
            expired=ts >= latest.deadline_ts,
        )

    def should_defer_flatten_for_hedge_ticket(self, now: float | None = None) -> bool:
        snapshot = self.open_hedge_ticket_snapshot(now=now)
        return snapshot is not None and not snapshot.expired

    def drain_quote_metrics(self) -> QuoteMetrics:
        metrics = QuoteMetrics(
            quote_orders=self._quote_orders,
            quote_fills=self._quote_fills,
            adverse_fills=self._adverse_fills,
        )
        self._quote_orders = 0
        self._quote_fills = 0
        self._adverse_fills = 0
        return metrics

    async def ingest_fill(
        self,
        event: ExecutionEvent,
        *,
        simulated: bool = False,
        source: str = "exchange",
    ) -> None:
        await self._handle_fill(event, simulated=simulated, source=source)

    async def update_quotes(
        self,
        bid_px: float,
        ask_px: float,
        bid_size: float,
        ask_size: float,
        cycle_id: int,
        reason: str,
    ) -> None:
        if not self._gateway.constraints.ready():
            self._orders_logger.log(
                {
                    "event": "order_skip",
                    "cycle_id": cycle_id,
                    "intent": "QUOTE_SKIP",
                    "reason": "constraints_not_ready",
                    "state": "blocked",
                }
            )
            return
        async with self._get_symbol_lock(self._config.symbols.perp.symbol):
            await self._upsert_quote(
                OrderIntent.QUOTE_BID, Side.BUY, bid_px, bid_size, cycle_id, reason
            )
            await self._upsert_quote(
                OrderIntent.QUOTE_ASK, Side.SELL, ask_px, ask_size, cycle_id, reason
            )

    async def cancel_all(self, reason: str) -> None:
        async with self._get_symbol_lock(self._config.symbols.perp.symbol):
            for intent, order in list(self._active_quotes.items()):
                if order is None:
                    continue
                await self._cancel_order(
                    InstType.USDT_FUTURES, order, reason=reason, state="cancel"
                )
                self._active_quotes[intent] = None

    async def flatten(self, spot_bbo: Optional[book_md.BBO], cycle_id: int, reason: str) -> None:
        async with self._get_symbol_lock(self._config.symbols.perp.symbol):
            self.fail_open_tickets("flatten_started")
            await self._cancel_all_quotes_unlocked(reason=reason)
            if not self._gateway.constraints.ready():
                return
            perp_pos_before_sync = self._positions.perp_pos
            await self._sync_positions_once(timeout_sec=0.2)
            if self._positions.perp_pos != 0:
                side = Side.BUY if self._positions.perp_pos < 0 else Side.SELL
                size = abs(self._positions.perp_pos)
                await self._submit_order(
                    OrderRequest(
                        inst_type=InstType.USDT_FUTURES,
                        symbol=self._config.symbols.perp.symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        size=size,
                        force=Force.IOC,
                        client_oid=self._new_client_oid(OrderIntent.FLATTEN, cycle_id),
                        intent=OrderIntent.FLATTEN,
                        cycle_id=cycle_id,
                        reduce_only=True,
                    ),
                    reason=reason,
                )
            elif self._positions_sync_authoritative and abs(perp_pos_before_sync) > 1e-12:
                self._log_futures_flatten_skip_no_position(
                    cycle_id=cycle_id,
                    reason=reason,
                    side=None,
                    size=0.0,
                )
        if spot_bbo and self._positions.spot_pos != 0:
            side = Side.SELL if self._positions.spot_pos > 0 else Side.BUY
            size = abs(self._positions.spot_pos)
            price = spot_bbo.ask if side == Side.BUY else spot_bbo.bid
            client_oid = self._new_client_oid(OrderIntent.FLATTEN, cycle_id)
            if side == Side.SELL:
                allowed = await self._precheck_spot_flatten_available(
                    cycle_id=cycle_id,
                    sell_size=size,
                    symbol=self._config.symbols.spot.symbol,
                    side=side,
                    client_oid=client_oid,
                )
                if not allowed:
                    return
            await self._submit_order(
                OrderRequest(
                    inst_type=InstType.SPOT,
                    symbol=self._config.symbols.spot.symbol,
                    side=side,
                    order_type=OrderType.LIMIT,
                    size=size,
                    force=Force.IOC,
                    client_oid=client_oid,
                    intent=OrderIntent.FLATTEN,
                    cycle_id=cycle_id,
                    price=price,
                ),
                reason=reason,
            )

    async def sync_positions(self, timeout_sec: float = 5.0, poll_interval: float = 5.0) -> None:
        while True:
            await self._sync_positions_once(timeout_sec=timeout_sec)
            await asyncio.sleep(poll_interval)

    async def _sync_positions_once(self, timeout_sec: float = 5.0) -> None:
        store = getattr(self._gateway, "store", None)
        positions_store = getattr(store, "positions", None)
        if positions_store is None:
            return
        wait = getattr(positions_store, "wait", None)
        if callable(wait):
            try:
                await asyncio.wait_for(wait(), timeout=timeout_sec)
            except Exception:
                pass
        perp_pos = self._perp_pos_from_store(positions_store)
        if perp_pos is None:
            return
        self._positions.perp_pos = perp_pos
        self._positions_sync_authoritative = True
        now = time.time()
        changed = (
            self._last_logged_spot_pos != self._positions.spot_pos
            or self._last_logged_perp_pos != self._positions.perp_pos
        )
        heartbeat_due = (now - self._last_position_log_ts) >= 60.0
        if not changed and not heartbeat_due:
            return
        self._last_logged_spot_pos = self._positions.spot_pos
        self._last_logged_perp_pos = self._positions.perp_pos
        self._last_position_log_ts = now
        self._orders_logger.log(
            {
                "ts": now,
                "event": "state",
                "intent": "SYSTEM",
                "source": "oms",
                "mode": "RUN",
                "reason": "positions_sync",
                "leg": "positions",
                "state": "positions_sync",
                "spot_pos": self._positions.spot_pos,
                "perp_pos": self._positions.perp_pos,
                "delta": self._positions.spot_pos + self._positions.perp_pos,
                "unhedged_qty": self._unhedged_qty,
                "unhedged_since": self._unhedged_since,
            }
        )

    async def monitor_fills(self, poll_interval: float = 0.2) -> None:
        while True:
            try:
                rows = list(self._gateway.store.fill.find())
            except Exception:
                rows = []
            for row in rows:
                event = self._parse_fill(row)
                if event is None:
                    continue
                dedupe_key = f"{event.inst_type.value}:{event.fill_id}"
                if dedupe_key in self._seen_fills:
                    continue
                self._seen_fills.add(dedupe_key)
                await self.ingest_fill(event, simulated=False, source="ws_private_fill")
            await asyncio.sleep(poll_interval)

    async def process_hedge_tickets(
        self,
        spot_bbo: Optional[book_md.BBO],
    ) -> None:
        if not self._hedge_tickets:
            return
        now = time.time()
        for ticket in list(self._hedge_tickets.values()):
            if ticket.status != "OPEN":
                self._cleanup_ticket(ticket.ticket_id)
                continue
            if ticket.remain <= 1e-9:
                ticket.status = "DONE"
                self._cleanup_ticket(ticket.ticket_id)
                continue
            if now < ticket.deadline_ts:
                continue
            if ticket.tries < self._hedge_cfg.hedge_max_tries:
                if spot_bbo is None:
                    continue
                slip_bps = (
                    self._hedge_cfg.hedge_aggressive_bps
                    + (ticket.tries * self._hedge_cfg.hedge_chase_slip_bps)
                )
                self._orders_logger.log(
                    {
                        "event": "risk",
                        "intent": OrderIntent.HEDGE.value,
                        "source": "oms",
                        "mode": "HEDGING",
                        "reason": "hedge_chase",
                        "leg": "spot_ioc",
                        "cycle_id": None,
                        "ticket_id": ticket.ticket_id,
                        "remain": ticket.remain,
                        "tries": ticket.tries,
                    }
                )
                await self._send_spot_hedge_order(
                    ticket,
                    spot_bbo,
                    slip_bps=slip_bps,
                    reason="hedge_chase",
                    use_ticket_client=False,
                )
                continue
            await self._unwind_ticket(ticket, reason="hedge_unwind")
            ticket.status = "FAILED"
            self._cleanup_ticket(ticket.ticket_id)

    def fail_open_tickets(self, reason: str) -> None:
        for ticket_id, ticket in list(self._hedge_tickets.items()):
            if ticket.status != "OPEN":
                self._cleanup_ticket(ticket_id)
                continue
            ticket.status = "FAILED"
            self._log_ticket_failed(ticket, reason)
            self._cleanup_ticket(ticket_id)

    async def _handle_fill(
        self,
        event: ExecutionEvent,
        *,
        simulated: bool = False,
        source: str = "exchange",
    ) -> None:
        if event.order_id and event.client_oid:
            self._order_client_map[event.order_id] = event.client_oid
        ticket_id = self._ticket_id_from_event(event)
        intent = self._intent_from_client_oid(event.client_oid)
        self._fills_logger.log(
            {
                "ts": event.ts,
                "event": "fill",
                "inst_type": event.inst_type.value,
                "symbol": event.symbol,
                "client_oid": event.client_oid,
                "order_id": event.order_id,
                "fill_id": event.fill_id,
                "side": event.side.value,
                "price": event.price,
                "size": event.size,
                "fee": event.fee,
                "fee_coin": event.fee_coin,
                "intent": None if intent is None else intent.value,
                "ticket_id": ticket_id,
                "hedge_latency_ms": self._hedge_latency_ms(event, ticket_id),
                "source": source,
                "simulated": simulated,
            }
        )
        perp_pos_before = self._positions.perp_pos
        positions_sync_authoritative = self._positions_sync_authoritative and not self._dry_run
        futures_position_accounting_skipped = (
            event.inst_type == InstType.USDT_FUTURES and positions_sync_authoritative
        )
        if futures_position_accounting_skipped:
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "risk",
                    "intent": "SYSTEM",
                    "source": "oms",
                    "mode": "RUN",
                    "reason": "futures_fill_position_accounting_skipped_positions_sync_authoritative",
                    "leg": "fill",
                    "inst_type": event.inst_type.value,
                    "symbol": event.symbol,
                    "side": event.side.value,
                    "size": event.size,
                    "order_id": event.order_id,
                    "client_oid": event.client_oid,
                    "fill_id": event.fill_id,
                    "perp_pos_before": perp_pos_before,
                    "perp_pos_after": self._positions.perp_pos,
                    "positions_sync_authoritative": positions_sync_authoritative,
                }
            )
        else:
            self._positions.apply_fill(event)
            if event.inst_type == InstType.USDT_FUTURES:
                self._orders_logger.log(
                    {
                        "ts": time.time(),
                        "event": "risk",
                        "intent": "SYSTEM",
                        "source": "oms",
                        "mode": "RUN",
                        "reason": "futures_fill_position_accounting_applied",
                        "leg": "fill",
                        "inst_type": event.inst_type.value,
                        "symbol": event.symbol,
                        "side": event.side.value,
                        "size": event.size,
                        "order_id": event.order_id,
                        "client_oid": event.client_oid,
                        "fill_id": event.fill_id,
                        "perp_pos_before": perp_pos_before,
                        "perp_pos_after": self._positions.perp_pos,
                        "positions_sync_authoritative": positions_sync_authoritative,
                    }
                )
        if (
            event.inst_type == InstType.SPOT
            and event.fee_coin
            and event.fee_coin.upper() == self._spot_base_coin().upper()
            and event.fee != 0
        ):
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "risk",
                    "intent": "SYSTEM",
                    "source": "oms",
                    "mode": "RUN",
                    "reason": "spot_position_fee_adjusted",
                    "leg": "fill",
                    "inst_type": event.inst_type.value,
                    "symbol": event.symbol,
                    "side": event.side.value,
                    "size": event.size,
                    "fee": event.fee,
                    "fee_coin": event.fee_coin,
                    "position_delta": _spot_position_delta_after_fee(event),
                    "spot_pos_internal": self._positions.spot_pos,
                }
            )
        if self._pnl is not None:
            self._pnl.record_fees(abs(event.fee))
        if intent == OrderIntent.HEDGE or ticket_id is not None:
            self._apply_hedge_fill(event, ticket_id)
            return
        if intent == OrderIntent.UNWIND:
            self._apply_unwind_fill(event)
            return
        if intent == OrderIntent.FLATTEN:
            return
        if event.inst_type == InstType.USDT_FUTURES:
            if intent in (OrderIntent.QUOTE_BID, OrderIntent.QUOTE_ASK):
                self._quote_fills += 1
                if self._is_adverse_quote_fill(event):
                    self._adverse_fills += 1
            await self._hedge_perp_fill(event)

    def _open_hedge_ticket(self, event: ExecutionEvent, hedge_side: Side, reason: str) -> HedgeTicket:
        now = time.time()
        ticket_id = self._new_client_oid(OrderIntent.HEDGE, int(now * 1000))
        ticket = HedgeTicket(
            ticket_id=ticket_id,
            symbol=self._config.symbols.spot.symbol,
            side=hedge_side,
            want_qty=event.size,
            filled_qty=0.0,
            created_ts=now,
            deadline_ts=now + self._hedge_cfg.hedge_deadline_sec,
            tries=0,
            status="OPEN",
            reason=reason,
            perp_fill_ts=event.ts,
            perp_fill_price=event.price,
        )
        self._hedge_tickets[ticket_id] = ticket
        self._orders_logger.log(
            {
                "event": "state",
                "intent": OrderIntent.HEDGE.value,
                "source": "oms",
                "mode": "HEDGING",
                "reason": "ticket_open",
                "leg": "spot_ioc",
                "cycle_id": int(event.ts * 1000),
                "ticket_id": ticket_id,
                "want_qty": ticket.want_qty,
                "filled_qty": ticket.filled_qty,
                "remain": ticket.remain,
                "deadline_ts": ticket.deadline_ts,
            }
        )
        return ticket

    async def _send_spot_hedge_order(
        self,
        ticket: HedgeTicket,
        spot_bbo: book_md.BBO,
        slip_bps: float,
        reason: str,
        use_ticket_client: bool,
    ) -> None:
        if ticket.remain <= 0:
            return
        if ticket.side == Side.SELL:
            allowed = await self._precheck_spot_hedge_sell_available(ticket)
            if not allowed:
                await self._unwind_ticket(ticket, reason="spot_hedge_insufficient_available_precheck")
                ticket.status = "FAILED"
                self._log_ticket_failed(ticket, "spot_hedge_insufficient_available_precheck")
                self._cleanup_ticket(ticket.ticket_id)
                return
        order_type, force, price = self._spot_hedge_order_plan(ticket, spot_bbo, slip_bps)
        client_oid = (
            ticket.ticket_id
            if use_ticket_client
            else self._new_client_oid(OrderIntent.HEDGE, int(time.time() * 1000))
        )
        self._spot_client_ticket[client_oid] = ticket.ticket_id
        req = OrderRequest(
            inst_type=InstType.SPOT,
            symbol=self._config.symbols.spot.symbol,
            side=ticket.side,
            order_type=order_type,
            size=ticket.remain,
            force=force,
            client_oid=client_oid,
            intent=OrderIntent.HEDGE,
            cycle_id=int(time.time() * 1000),
            price=price,
        )
        order_id = await self._submit_order(req, reason=reason)
        ticket.tries += 1
        ticket.deadline_ts = time.time() + self._hedge_cfg.hedge_deadline_sec
        self._orders_logger.log(
            {
                "event": "state",
                "intent": OrderIntent.HEDGE.value,
                "source": "oms",
                "mode": "HEDGING",
                "reason": "ticket_order",
                "leg": "spot_ioc",
                "cycle_id": req.cycle_id,
                "ticket_id": ticket.ticket_id,
                "client_oid": client_oid,
                "order_id": order_id,
                "tries": ticket.tries,
                "remain": ticket.remain,
            }
        )
        if order_id:
            self._spot_order_ticket[order_id] = ticket.ticket_id

    async def _unwind_ticket(self, ticket: HedgeTicket, reason: str) -> None:
        if not self._hedge_cfg.unwind_enable:
            return
        if ticket.remain <= 0:
            return
        await self.cancel_all(reason=reason)
        req = OrderRequest(
            inst_type=InstType.USDT_FUTURES,
            symbol=self._config.symbols.perp.symbol,
            side=ticket.side,
            order_type=OrderType.MARKET,
            size=ticket.remain,
            force=Force.IOC,
            client_oid=self._new_client_oid(OrderIntent.UNWIND, int(time.time() * 1000)),
            intent=OrderIntent.UNWIND,
            cycle_id=int(time.time() * 1000),
            reduce_only=True,
        )
        self._orders_logger.log(
            {
                "event": "risk",
                "intent": OrderIntent.UNWIND.value,
                "source": "oms",
                "mode": "HEDGING",
                "reason": reason,
                "leg": "perp_unwind",
                "cycle_id": None,
                "ticket_id": ticket.ticket_id,
                "remain": ticket.remain,
                "tries": ticket.tries,
            }
        )
        await self._submit_order(req, reason=reason)

    async def _hedge_perp_fill(self, event: ExecutionEvent) -> None:
        if not self._gateway.constraints.ready():
            return
        hedge_side = Side.BUY if event.side == Side.SELL else Side.SELL
        ticket = self._open_hedge_ticket(event, hedge_side, reason="perp_fill")
        if not self._gateway.book_ready:
            self._add_unhedged(event)
            return
        channel = self._gateway.public_book_channel
        snapshot, spot_filtered = book_md.snapshot_from_store(
            self._gateway.store,
            InstType.SPOT,
            self._config.symbols.spot.symbol,
            levels=1,
            channel=channel,
            return_meta=True,
        )
        if snapshot is not None and not spot_filtered:
            self._gateway.note_book_channel_filter_unavailable(
                InstType.SPOT,
                self._config.symbols.spot.symbol,
                channel,
            )
        if snapshot is None:
            self._add_unhedged(event)
            return

        spot_bbo = book_md.bbo_from_snapshot(snapshot)
        self._add_unhedged(event)
        await self._send_spot_hedge_order(
            ticket,
            spot_bbo,
            slip_bps=self._hedge_cfg.hedge_aggressive_bps,
            reason="hedge",
            use_ticket_client=True,
        )

    def _apply_hedge_fill(self, event: ExecutionEvent, ticket_id: Optional[str]) -> None:
        delta = event.size if event.side == Side.BUY else -event.size
        self._unhedged_qty -= delta
        if abs(self._unhedged_qty) <= 1e-9:
            self._unhedged_qty = 0.0
            self._unhedged_since = None
        if ticket_id is None:
            return
        ticket = self._hedge_tickets.get(ticket_id)
        if ticket is None:
            return
        if self._pnl is not None:
            latency_ms = max(0.0, (event.ts - ticket.perp_fill_ts) * 1000.0)
            self._pnl.record_hedge_latency(latency_ms)
            spot_mid = self._spot_mid()
            if spot_mid is not None:
                slip = abs(event.price - spot_mid) * event.size
                self._pnl.record_hedge_slip(slip)
        ticket.filled_qty += event.size
        if ticket.remain <= 1e-9:
            ticket.status = "DONE"
            if self._pnl is not None:
                perp_signed = ticket.want_qty if ticket.side == Side.SELL else -ticket.want_qty
                spread_pnl = (event.price - ticket.perp_fill_price) * perp_signed
                self._pnl.record_gross_spread(spread_pnl)
            self._orders_logger.log(
                {
                    "event": "state",
                    "intent": OrderIntent.HEDGE.value,
                    "source": "oms",
                    "mode": "HEDGING",
                    "reason": "ticket_done",
                    "leg": "spot_ioc",
                    "cycle_id": None,
                    "ticket_id": ticket.ticket_id,
                    "want_qty": ticket.want_qty,
                    "filled_qty": ticket.filled_qty,
                    "remain": ticket.remain,
                }
            )
            self._cleanup_ticket(ticket_id)

    def _ticket_id_from_event(self, event: ExecutionEvent) -> Optional[str]:
        if event.client_oid:
            ticket_id = self._spot_client_ticket.get(event.client_oid)
            if ticket_id:
                return ticket_id
        if event.order_id:
            return self._spot_order_ticket.get(event.order_id)
        return None

    def _cleanup_ticket(self, ticket_id: str) -> None:
        self._hedge_tickets.pop(ticket_id, None)
        for mapping in (self._spot_client_ticket, self._spot_order_ticket):
            keys = [key for key, value in mapping.items() if value == ticket_id]
            for key in keys:
                mapping.pop(key, None)

    def _log_ticket_failed(self, ticket: HedgeTicket, reason: str) -> None:
        self._orders_logger.log(
            {
                "event": "state",
                "intent": OrderIntent.HEDGE.value,
                "source": "oms",
                "mode": "HEDGING",
                "reason": "ticket_failed",
                "leg": "spot_ioc",
                "cycle_id": None,
                "ticket_id": ticket.ticket_id,
                "want_qty": ticket.want_qty,
                "filled_qty": ticket.filled_qty,
                "remain": ticket.remain,
                "tries": ticket.tries,
                "fail_reason": reason,
            }
        )

    def _add_unhedged(self, event: ExecutionEvent) -> None:
        delta = event.size if event.side == Side.SELL else -event.size
        self._unhedged_qty += delta
        if self._unhedged_since is None:
            self._unhedged_since = time.time()
        if self._pnl is not None:
            spot_mid = self._spot_mid()
            if spot_mid is not None:
                self._pnl.update_max_unhedged_notional(abs(self._unhedged_qty) * spot_mid)

    def _apply_unwind_fill(self, event: ExecutionEvent) -> None:
        if event.inst_type != InstType.USDT_FUTURES:
            return
        before = self._unhedged_qty
        delta = event.size if event.side == Side.SELL else -event.size
        self._unhedged_qty += delta
        if abs(self._unhedged_qty) <= 1e-9:
            self._unhedged_qty = 0.0
            self._unhedged_since = None
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "risk",
                "intent": OrderIntent.UNWIND.value,
                "source": "oms",
                "mode": "RUN",
                "reason": "unwind_fill_unhedged_qty_reconciled",
                "leg": "perp_unwind",
                "inst_type": event.inst_type.value,
                "symbol": event.symbol,
                "side": event.side.value,
                "size": event.size,
                "order_id": event.order_id,
                "client_oid": event.client_oid,
                "fill_id": event.fill_id,
                "unhedged_qty_before": before,
                "unhedged_qty_after": self._unhedged_qty,
                "unhedged_since": self._unhedged_since,
                "perp_pos_internal": self._positions.perp_pos,
                "spot_pos_internal": self._positions.spot_pos,
                "delta": self._positions.spot_pos + self._positions.perp_pos,
            }
        )

    def _get_symbol_lock(self, symbol: str) -> asyncio.Lock:
        lock = self._symbol_locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            self._symbol_locks[symbol] = lock
        return lock

    async def _cancel_all_quotes_unlocked(self, reason: str) -> None:
        for intent, order in list(self._active_quotes.items()):
            if order is None:
                continue
            await self._cancel_order(
                InstType.USDT_FUTURES, order, reason=reason, state="cancel"
            )
            self._active_quotes[intent] = None

    async def _upsert_quote(
        self,
        intent: OrderIntent,
        side: Side,
        price: float,
        size: float,
        cycle_id: int,
        reason: str,
    ) -> None:
        existing = self._active_quotes[intent]
        if price <= 0 or size <= 0:
            if existing:
                await self._cancel_order(
                    InstType.USDT_FUTURES, existing, reason=reason, state="cancel"
                )
                self._active_quotes[intent] = None
            return

        constraints = self._gateway.constraints.get(InstType.USDT_FUTURES)
        if constraints is None or not constraints.is_ready():
            return
        price = float(quantize_perp_price(price, side, constraints))
        size = constraints.adjust_qty(size)
        if size <= 0:
            if existing:
                await self._cancel_order(
                    InstType.USDT_FUTURES, existing, reason=reason, state="cancel"
                )
                self._active_quotes[intent] = None
            return
        if not constraints.validate(price, size):
            return

        if existing and not self._should_replace(existing, price, size, constraints):
            return

        if existing:
            if self._pnl is not None:
                self._pnl.record_quote_replace()
            await self._cancel_order(
                InstType.USDT_FUTURES, existing, reason=reason, state="replace"
            )

        req = OrderRequest(
            inst_type=InstType.USDT_FUTURES,
            symbol=self._config.symbols.perp.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            size=size,
            force=Force.POST_ONLY,
            client_oid=self._new_client_oid(intent, cycle_id),
            intent=intent,
            cycle_id=cycle_id,
            price=price,
        )
        order_id = await self._submit_order(req, reason=reason)
        if order_id or self._dry_run:
            now = time.time()
            if self._dry_run and order_id is None:
                order_id = f"dryrun:{req.client_oid}"
            self._quote_orders += 1
            self._active_quotes[intent] = ActiveOrder(
                order_id=order_id,
                client_oid=req.client_oid,
                price=price,
                size=size,
                side=side,
                intent=intent,
                ts=now,
                symbol=self._config.symbols.perp.symbol,
                dry_run=self._dry_run,
                source="dry_run_virtual" if self._dry_run else "live_order",
            )

    async def _submit_order(self, req: OrderRequest, reason: str) -> Optional[str]:
        constraints = self._gateway.constraints.get(req.inst_type)
        if constraints is None or not constraints.is_ready():
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "order_skip",
                    "cycle_id": req.cycle_id,
                    "intent": req.intent.value,
                    "inst_type": req.inst_type.value,
                    "symbol": req.symbol,
                    "side": req.side.value,
                    "type": req.order_type.value,
                    "price": req.price,
                    "size": req.size,
                    "force": req.force.value,
                    "client_oid": req.client_oid,
                    "reason": reason,
                    "state": "blocked_constraints",
                }
            )
            return None

        price_before_round = req.price
        price_tick = None
        price_place = getattr(constraints, "price_place", None)
        price_payload = None
        if req.price is not None:
            if req.inst_type == InstType.USDT_FUTURES:
                rounded = quantize_perp_price(req.price, req.side, constraints)
                price_tick = format_price_for_bitget(get_price_tick(constraints))
                price_payload = format_price_for_bitget(rounded)
                req.price = float(rounded)
            else:
                req.price = constraints.adjust_price(req.price)
        req.size = constraints.adjust_qty(req.size)
        if req.size < constraints.min_qty:
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "order_skip",
                    "cycle_id": req.cycle_id,
                    "intent": req.intent.value,
                    "inst_type": req.inst_type.value,
                    "symbol": req.symbol,
                    "side": req.side.value,
                    "type": req.order_type.value,
                    "price": req.price,
                    "size": req.size,
                    "force": req.force.value,
                    "client_oid": req.client_oid,
                    "reason": reason,
                    "state": "blocked_constraints",
                }
            )
            return None
        if req.price is not None and not constraints.validate(req.price, req.size):
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "order_skip",
                    "cycle_id": req.cycle_id,
                    "intent": req.intent.value,
                    "inst_type": req.inst_type.value,
                    "symbol": req.symbol,
                    "side": req.side.value,
                    "type": req.order_type.value,
                    "price": req.price,
                    "size": req.size,
                    "force": req.force.value,
                    "client_oid": req.client_oid,
                    "reason": reason,
                    "state": "blocked_constraints",
                }
            )
            return None

        record = {
            "ts": time.time(),
            "event": "order_new",
            "cycle_id": req.cycle_id,
            "intent": req.intent.value,
            "inst_type": req.inst_type.value,
            "symbol": req.symbol,
            "side": req.side.value,
            "type": req.order_type.value,
            "price": req.price,
            "price_before_round": price_before_round,
            "price_after_round": req.price,
            "price_payload": price_payload,
            "tick_size": price_tick if price_tick is not None else constraints.tick_size,
            "pricePlace": price_place,
            "size": req.size,
            "force": req.force.value,
            "client_oid": req.client_oid,
            "reason": reason,
        }
        if self._dry_run:
            record["state"] = "dry_run"
            self._orders_logger.log(record)
            return None

        response = await self._gateway.place_order(req)
        record["state"] = "sent"
        resp_code = response.get("code")
        record["resp_code"] = resp_code
        self._orders_logger.log(record)
        if self._risk is not None:
            ok = str(resp_code) == "00000"
            streak = self._risk.record_order_result(ok)
            if not ok:
                if self._pnl is not None:
                    self._pnl.record_reject_streak(streak)
                spot_available = None
                if req.inst_type == InstType.SPOT and req.side == Side.SELL:
                    spot_available = self._spot_available_balance(self._spot_base_coin())
                self._orders_logger.log(
                    {
                        "ts": time.time(),
                        "event": "risk",
                        "intent": req.intent.value,
                        "source": "oms",
                        "mode": None,
                        "reason": "order_reject",
                        "leg": None,
                        "cycle_id": req.cycle_id,
                        "resp_code": resp_code,
                        "response_msg": response.get("msg"),
                        "reject_streak": streak,
                        "inst_type": req.inst_type.value,
                        "symbol": req.symbol,
                        "side": req.side.value,
                        "size": req.size,
                        "client_oid": req.client_oid,
                        "price": req.price,
                        "price_before_round": price_before_round,
                        "price_after_round": req.price,
                        "price_payload": price_payload,
                        "tick_size": price_tick if price_tick is not None else constraints.tick_size,
                        "pricePlace": price_place,
                        "spot_pos_internal": self._positions.spot_pos,
                        "perp_pos_internal": self._positions.perp_pos,
                        "delta": self._positions.spot_pos + self._positions.perp_pos,
                        "spot_available": spot_available,
                        "reject_detail": (
                            "spot_flatten_insufficient_balance"
                            if str(resp_code) == "43012"
                            and req.inst_type == InstType.SPOT
                            and req.intent == OrderIntent.FLATTEN
                            else "spot_sell_insufficient_balance"
                            if str(resp_code) == "43012"
                            and req.inst_type == InstType.SPOT
                            and req.side == Side.SELL
                            else None
                        ),
                    }
                )
        order_id = _extract_order_id(response)
        if order_id:
            self._order_client_map[order_id] = req.client_oid
        return order_id

    async def _cancel_order(
        self,
        inst_type: InstType,
        order: ActiveOrder,
        reason: str,
        state: str,
    ) -> None:
        record = {
            "ts": time.time(),
            "event": "order_cancel",
            "cycle_id": None,
            "intent": order.intent.value,
            "inst_type": inst_type.value,
            "symbol": self._config.symbols.perp.symbol,
            "side": order.side.value,
            "type": "cancel",
            "price": order.price,
            "size": order.size,
            "force": None,
            "client_oid": order.client_oid,
            "reason": reason,
            "state": state,
        }
        if self._dry_run:
            self._orders_logger.log(record)
            return
        response = await self._gateway.cancel_order(
            inst_type,
            symbol=self._config.symbols.perp.symbol,
            order_id=order.order_id,
            client_oid=order.client_oid,
        )
        record["resp_code"] = response.get("code")
        self._orders_logger.log(record)

    @staticmethod
    def _needs_replace(
        existing: ActiveOrder,
        price: float,
        size: float,
        constraints: InstrumentConstraints,
        threshold_bps: float = 0.0,
    ) -> bool:
        if abs(size - existing.size) > constraints.qty_step / 2:
            return True
        if existing.price <= 0:
            return True
        delta_bps = abs(price - existing.price) / existing.price * 10000.0
        if delta_bps < threshold_bps:
            return False
        if abs(price - existing.price) >= constraints.tick_size:
            return True
        return False

    def _should_replace(
        self,
        existing: ActiveOrder,
        price: float,
        size: float,
        constraints: InstrumentConstraints,
    ) -> bool:
        return self._needs_replace(
            existing,
            price,
            size,
            constraints,
            threshold_bps=self._config.strategy.reprice_threshold_bps,
        )

    def _spot_hedge_order_plan(
        self,
        ticket: HedgeTicket,
        spot_bbo: book_md.BBO,
        slip_bps: float,
    ) -> tuple[OrderType, Force, float]:
        price = spot_bbo.ask if ticket.side == Side.BUY else spot_bbo.bid
        unhedged_sec = max(0.0, time.time() - ticket.created_ts)
        if unhedged_sec < (self._config.risk.max_unhedged_sec * 0.5):
            return OrderType.LIMIT, Force.POST_ONLY, price
        if ticket.side == Side.BUY:
            price *= 1 + (slip_bps / 10000.0)
        else:
            price *= 1 - (slip_bps / 10000.0)
        return OrderType.LIMIT, Force.IOC, price

    def _hedge_latency_ms(
        self, event: ExecutionEvent, ticket_id: Optional[str]
    ) -> float | None:
        if ticket_id is None:
            return None
        ticket = self._hedge_tickets.get(ticket_id)
        if ticket is None:
            return None
        return max(0.0, (event.ts - ticket.perp_fill_ts) * 1000.0)

    def _spot_mid(self) -> float | None:
        channel = getattr(self._gateway, "public_book_channel", None)
        snapshot = book_md.snapshot_from_store(
            self._gateway.store,
            InstType.SPOT,
            self._config.symbols.spot.symbol,
            levels=1,
            channel=channel,
        )
        if snapshot is None:
            return None
        return book_md.calc_mid(book_md.bbo_from_snapshot(snapshot))

    def _perp_mid(self) -> float | None:
        channel = getattr(self._gateway, "public_book_channel", None)
        snapshot = book_md.snapshot_from_store(
            self._gateway.store,
            InstType.USDT_FUTURES,
            self._config.symbols.perp.symbol,
            levels=1,
            channel=channel,
        )
        if snapshot is None:
            return None
        return book_md.calc_mid(book_md.bbo_from_snapshot(snapshot))

    def _is_adverse_quote_fill(self, event: ExecutionEvent) -> bool:
        perp_mid = self._perp_mid()
        if perp_mid is None:
            return False
        if event.side == Side.BUY:
            return perp_mid < event.price
        return perp_mid > event.price

    @staticmethod
    def _intent_from_client_oid(client_oid: str) -> Optional[OrderIntent]:
        if not client_oid:
            return None
        for intent in OrderIntent:
            prefix = f"{intent.value}-"
            if client_oid.startswith(prefix):
                return intent
        return None

    def _new_client_oid(self, intent: OrderIntent, cycle_id: int | float) -> str:
        uniq = uuid.uuid4().hex[:10]
        return f"{intent.value}-{cycle_id}-{uniq}"

    def _parse_fill(self, row: dict) -> Optional[ExecutionEvent]:
        inst_type = _parse_inst_type(row.get("instType"))
        if inst_type is None:
            return None
        symbol = row.get("instId") or row.get("symbol")
        if not symbol:
            return None
        side = _parse_side(row.get("side"))
        if side is None:
            return None
        order_id = _first_string(row, ["orderId", "order_id", "ordId"]) or ""
        client_oid = _first_string(row, ["clientOid", "clientOrderId", "client_oid"]) or ""
        if not client_oid and order_id:
            client_oid = self._order_client_map.get(order_id, "")
        price = self._extract_fill_price(row, inst_type)
        if price is None or price <= 0:
            self._log_fill_parse_warning(
                row,
                parse_reason="fill_price_missing_or_invalid",
                inst_type=inst_type,
                order_id=order_id,
                client_oid=client_oid,
                price=price,
            )
            return None
        size = self._extract_fill_size(row, inst_type)
        if size is None or size <= 0:
            self._log_fill_parse_warning(
                row,
                parse_reason="fill_size_missing_or_zero",
                inst_type=inst_type,
                order_id=order_id,
                client_oid=client_oid,
                size=size,
            )
            return None
        fee, fee_coin = self._extract_fill_fee(row)
        if inst_type == InstType.SPOT and fee != 0 and not fee_coin:
            self._log_fill_parse_warning(
                row,
                parse_reason="spot_fee_coin_missing",
                inst_type=inst_type,
                order_id=order_id,
                client_oid=client_oid,
                price=price,
                size=size,
            )
        ts = _first_time(row, ["ts", "fillTime", "cTime", "tradeTime"]) or time.time()
        fill_id = _first_string(row, ["tradeId", "fillId", "execId", "id"])
        if not fill_id:
            fill_id = _fallback_fill_id(inst_type, order_id, ts, price, size)
        return ExecutionEvent(
            inst_type=inst_type,
            symbol=symbol,
            order_id=order_id,
            client_oid=client_oid,
            fill_id=fill_id,
            side=side,
            price=price,
            size=size,
            fee=fee,
            ts=ts,
            fee_coin=fee_coin,
        )

    def _extract_fill_price(self, row: dict, inst_type: InstType) -> Optional[float]:
        if inst_type == InstType.SPOT:
            keys = ["priceAvg", "fillPrice", "tradePrice", "price", "px"]
        else:
            keys = ["price", "fillPrice", "tradePrice", "priceAvg", "px"]
        for key in keys:
            if key not in row or row[key] is None:
                continue
            price = _safe_float(row[key])
            if price is None:
                self._log_fill_parse_warning(
                    row,
                    parse_reason="fill_price_parse_error",
                    inst_type=inst_type,
                    price_key=key,
                    price_value=row[key],
                )
                continue
            return price
        return None

    def _extract_fill_size(self, row: dict, inst_type: InstType) -> Optional[float]:
        if inst_type == InstType.USDT_FUTURES:
            keys = ["baseVolume", "size", "fillSz", "tradeQty", "tradeSize"]
        else:
            keys = ["size", "fillSz", "tradeQty", "tradeSize", "baseVolume"]
        for key in keys:
            if key not in row or row[key] is None:
                continue
            size = _safe_float(row[key])
            if size is None:
                self._log_fill_parse_warning(
                    row,
                    parse_reason="fill_size_parse_error",
                    inst_type=inst_type,
                    size_key=key,
                    size_value=row[key],
                )
                continue
            return size
        return None

    def _extract_fill_fee(self, row: dict) -> tuple[float, Optional[str]]:
        fee = _first_float(row, ["fee", "fillFee", "transactionFee", "totalFee"])
        fee_coin = _first_string(
            row,
            ["feeCoin", "feeCurrency", "feeCcy", "feeCurrencyCode", "feeCoinName"],
        )
        if fee is not None:
            if not fee_coin and row.get("feeDetail") is not None:
                parsed = _parse_json_like(row["feeDetail"])
                if parsed is not None:
                    _, detail_coin = _fee_detail_amount_and_coin(parsed)
                    fee_coin = detail_coin
            return fee, fee_coin
        if "feeDetail" not in row or row["feeDetail"] is None:
            return 0.0, fee_coin
        parsed = _parse_json_like(row["feeDetail"])
        if parsed is None:
            return 0.0, fee_coin
        fee_detail = _fee_detail_amount_and_coin(parsed)
        return fee_detail[0], fee_coin or fee_detail[1]

    def _log_fill_parse_warning(
        self,
        row: dict,
        *,
        parse_reason: str,
        inst_type: InstType,
        order_id: str | None = None,
        client_oid: str | None = None,
        price: float | None = None,
        price_key: str | None = None,
        price_value: object | None = None,
        size: float | None = None,
        size_key: str | None = None,
        size_value: object | None = None,
    ) -> None:
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "risk",
                "intent": "SYSTEM",
                "source": "oms",
                "mode": "RUN",
                "reason": "fill_parse_warning",
                "parse_reason": parse_reason,
                "leg": "fill",
                "inst_type": inst_type.value,
                "symbol": row.get("instId") or row.get("symbol"),
                "order_id": order_id or _first_string(row, ["orderId", "order_id", "ordId"]),
                "client_oid": client_oid
                or _first_string(row, ["clientOid", "clientOrderId", "client_oid"]),
                "trade_id": _first_string(row, ["tradeId", "fillId", "execId", "id"]),
                "fill_id": _first_string(row, ["tradeId", "fillId", "execId", "id"]),
                "raw_keys": sorted(str(key) for key in row.keys()),
                "price": price,
                "price_key": price_key,
                "price_value": None if price_value is None else str(price_value),
                "size": size,
                "size_key": size_key,
                "size_value": None if size_value is None else str(size_value),
                "available_size_fields": {
                    key: row.get(key)
                    for key in ("baseVolume", "size", "fillSz", "tradeQty", "tradeSize")
                    if key in row
                },
            }
        )

    def _spot_base_coin(self) -> str:
        return _base_coin_from_symbol(self._config.symbols.spot.symbol)

    def _spot_available_balance(self, base_coin: str) -> Optional[float]:
        store = getattr(self._gateway, "store", None)
        if store is None:
            return None
        for store_name in ("account", "accounts", "asset", "assets", "balance", "balances"):
            balance_store = getattr(store, store_name, None)
            if balance_store is None or not hasattr(balance_store, "find"):
                continue
            try:
                rows = list(balance_store.find())
            except Exception:
                continue
            available = _available_balance_from_rows(rows, base_coin)
            if available is not None:
                return available
        return None

    async def _get_spot_available_balance(self, base_coin: str) -> Optional[float]:
        getter = getattr(self._gateway, "get_spot_available_balance", None)
        if callable(getter):
            try:
                result = getter(base_coin)
                if hasattr(result, "__await__"):
                    result = await result
                if result is not None:
                    return float(result)
            except Exception:
                pass
        return self._spot_available_balance(base_coin)

    async def reconcile_startup_spot_balance(
        self,
        *,
        tolerance: float,
        dry_run: bool,
    ) -> bool:
        base_coin = self._spot_base_coin()
        actual = await self._get_spot_available_balance(base_coin)
        internal = self._positions.spot_pos
        perp_pos = self._positions.perp_pos
        if actual is None:
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "risk",
                    "intent": "SYSTEM",
                    "source": "oms",
                    "mode": "INIT",
                    "reason": "startup_spot_balance_unavailable",
                    "leg": "spot",
                    "internal_spot_pos": internal,
                    "actual_spot_available": None,
                    "diff": None,
                    "tolerance": tolerance,
                    "base_coin": base_coin,
                    "perp_pos": perp_pos,
                    "delta": internal + perp_pos,
                    "dry_run": dry_run,
                    "action_taken": "warn_only",
                }
            )
            return True

        diff = actual - internal
        mismatch = abs(diff) > tolerance
        open_spot_balance = abs(actual) > tolerance and abs(internal) <= tolerance
        if not mismatch and not open_spot_balance:
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "state",
                    "intent": "SYSTEM",
                    "source": "oms",
                    "mode": "INIT",
                    "reason": "startup_spot_balance_reconciled",
                    "leg": "spot",
                    "internal_spot_pos": internal,
                    "actual_spot_available": actual,
                    "diff": diff,
                    "tolerance": tolerance,
                    "base_coin": base_coin,
                    "perp_pos": perp_pos,
                    "delta": internal + perp_pos,
                    "dry_run": dry_run,
                    "action_taken": "none",
                }
            )
            return True

        reason = (
            "startup_open_spot_balance_detected"
            if open_spot_balance
            else "startup_spot_balance_mismatch"
        )
        action_taken = "warn_only"
        if not dry_run and self._risk is not None:
            self._risk.halt(reason)
            action_taken = "halted"
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "risk",
                "intent": "SYSTEM",
                "source": "oms",
                "mode": "INIT",
                "reason": reason,
                "leg": "spot",
                "internal_spot_pos": internal,
                "actual_spot_available": actual,
                "diff": diff,
                "tolerance": tolerance,
                "base_coin": base_coin,
                "perp_pos": perp_pos,
                "delta": internal + perp_pos,
                "dry_run": dry_run,
                "action_taken": action_taken,
            }
        )
        return action_taken != "halted"

    async def _precheck_spot_flatten_available(
        self,
        *,
        cycle_id: int,
        sell_size: float,
        symbol: str,
        side: Side,
        client_oid: str,
    ) -> bool:
        base_coin = self._spot_base_coin()
        spot_available = await self._get_spot_available_balance(base_coin)
        if spot_available is None:
            self._log_spot_flatten_available_precheck(
                cycle_id=cycle_id,
                spot_available=None,
                sell_size=sell_size,
                symbol=symbol,
                base_coin=base_coin,
                side=side,
                client_oid=client_oid,
            )
            return True
        if spot_available + 1e-12 < sell_size:
            self._log_spot_flatten_available_skip(
                cycle_id=cycle_id,
                spot_available=spot_available,
                sell_size=sell_size,
                symbol=symbol,
                base_coin=base_coin,
                side=side,
                client_oid=client_oid,
            )
            return False
        self._log_spot_flatten_available_precheck(
            cycle_id=cycle_id,
            spot_available=spot_available,
            sell_size=sell_size,
            symbol=symbol,
            base_coin=base_coin,
            side=side,
            client_oid=client_oid,
        )
        return True

    def _log_futures_flatten_skip_no_position(
        self,
        *,
        cycle_id: int,
        reason: str,
        side: Side | None,
        size: float,
    ) -> None:
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "order_skip",
                "intent": OrderIntent.FLATTEN.value,
                "source": "oms",
                "mode": "RUN",
                "reason": "futures_flatten_no_position_after_sync",
                "state": "blocked_precheck",
                "leg": "perp",
                "cycle_id": cycle_id,
                "inst_type": InstType.USDT_FUTURES.value,
                "symbol": self._config.symbols.perp.symbol,
                "side": None if side is None else side.value,
                "size": size,
                "perp_pos_internal": self._positions.perp_pos,
                "spot_pos_internal": self._positions.spot_pos,
                "delta": self._positions.spot_pos + self._positions.perp_pos,
                "positions_sync_authoritative": self._positions_sync_authoritative,
                "original_reason": reason,
                "action_taken": "skip_flatten",
            }
        )

    async def _precheck_spot_hedge_sell_available(self, ticket: HedgeTicket) -> bool:
        base_coin = self._spot_base_coin()
        spot_available = await self._get_spot_available_balance(base_coin)
        if spot_available is None:
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "risk",
                    "intent": OrderIntent.HEDGE.value,
                    "source": "oms",
                    "mode": "HEDGING",
                    "reason": "spot_hedge_available_precheck_unavailable",
                    "leg": "spot",
                    "cycle_id": None,
                    "ticket_id": ticket.ticket_id,
                    "remain": ticket.remain,
                    "spot_available": None,
                    "side": ticket.side.value,
                    "symbol": ticket.symbol,
                    "base_coin": base_coin,
                    "perp_pos_internal": self._positions.perp_pos,
                    "spot_pos_internal": self._positions.spot_pos,
                    "delta": self._positions.spot_pos + self._positions.perp_pos,
                    "action_taken": "warn_only",
                }
            )
            return True
        if spot_available + 1e-12 >= ticket.remain:
            self._orders_logger.log(
                {
                    "ts": time.time(),
                    "event": "risk",
                    "intent": OrderIntent.HEDGE.value,
                    "source": "oms",
                    "mode": "HEDGING",
                    "reason": "spot_hedge_available_precheck",
                    "leg": "spot",
                    "cycle_id": None,
                    "ticket_id": ticket.ticket_id,
                    "remain": ticket.remain,
                    "spot_available": spot_available,
                    "side": ticket.side.value,
                    "symbol": ticket.symbol,
                    "base_coin": base_coin,
                    "perp_pos_internal": self._positions.perp_pos,
                    "spot_pos_internal": self._positions.spot_pos,
                    "delta": self._positions.spot_pos + self._positions.perp_pos,
                    "action_taken": "continue_hedge",
                }
            )
            return True
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "order_skip",
                "intent": OrderIntent.HEDGE.value,
                "source": "oms",
                "mode": "HEDGING",
                "reason": "spot_hedge_insufficient_available_precheck",
                "state": "blocked_precheck",
                "leg": "spot",
                "cycle_id": None,
                "ticket_id": ticket.ticket_id,
                "remain": ticket.remain,
                "spot_available": spot_available,
                "side": ticket.side.value,
                "symbol": ticket.symbol,
                "base_coin": base_coin,
                "perp_pos_internal": self._positions.perp_pos,
                "spot_pos_internal": self._positions.spot_pos,
                "delta": self._positions.spot_pos + self._positions.perp_pos,
                "action_taken": "unwind_ticket",
            }
        )
        return False

    def _log_spot_flatten_available_precheck(
        self,
        *,
        cycle_id: int,
        spot_available: Optional[float],
        sell_size: float,
        symbol: str,
        base_coin: str,
        side: Side,
        client_oid: str,
    ) -> None:
        if spot_available is None:
            reason = "spot_flatten_available_precheck_unavailable"
        else:
            reason = "spot_flatten_available_precheck"
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "risk",
                "intent": OrderIntent.FLATTEN.value,
                "source": "oms",
                "mode": "RUN",
                "reason": reason,
                "leg": "spot",
                "cycle_id": cycle_id,
                "inst_type": InstType.SPOT.value,
                "symbol": symbol,
                "side": side.value,
                "client_oid": client_oid,
                "spot_pos_internal": self._positions.spot_pos,
                "perp_pos_internal": self._positions.perp_pos,
                "delta": self._positions.spot_pos + self._positions.perp_pos,
                "spot_available": spot_available,
                "sell_size": sell_size,
                "base_coin": base_coin,
                "action": "warn_only",
            }
        )

    def _log_spot_flatten_available_skip(
        self,
        *,
        cycle_id: int,
        spot_available: float,
        sell_size: float,
        symbol: str,
        base_coin: str,
        side: Side,
        client_oid: str,
    ) -> None:
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "order_skip",
                "intent": OrderIntent.FLATTEN.value,
                "source": "oms",
                "mode": "RUN",
                "reason": "spot_flatten_insufficient_available_precheck",
                "state": "blocked_precheck",
                "leg": "spot",
                "cycle_id": cycle_id,
                "inst_type": InstType.SPOT.value,
                "symbol": symbol,
                "side": side.value,
                "client_oid": client_oid,
                "spot_pos_internal": self._positions.spot_pos,
                "perp_pos_internal": self._positions.perp_pos,
                "delta": self._positions.spot_pos + self._positions.perp_pos,
                "spot_available": spot_available,
                "sell_size": sell_size,
                "base_coin": base_coin,
            }
        )

    def _perp_pos_from_store(self, positions_store) -> Optional[float]:
        rows = list(positions_store.find())
        if not rows:
            return None
        symbol = self._config.symbols.perp.symbol
        total = 0.0
        found = False
        for row in rows:
            row_symbol = row.get("instId") or row.get("symbol")
            if row_symbol != symbol:
                continue
            size = _first_float(
                row,
                ["total", "pos", "size", "position", "holdVol", "available", "quantity"],
            )
            if size is None:
                continue
            side = str(row.get("holdSide") or row.get("posSide") or row.get("side") or "").lower()
            if side in ("short", "sell"):
                total -= size
            else:
                total += size
            found = True
        return total if found else None


def _extract_order_id(payload: dict) -> Optional[str]:
    data = payload.get("data") or {}
    if isinstance(data, dict):
        return data.get("orderId") or data.get("order_id")
    return None


def _fallback_fill_id(
    inst_type: InstType,
    order_id: str,
    ts: float,
    price: float,
    size: float,
) -> str:
    return f"{inst_type.value}:{order_id}:{ts}:{price}:{size}"


def _parse_inst_type(value: Optional[str]) -> Optional[InstType]:
    if value == InstType.SPOT.value:
        return InstType.SPOT
    if value == InstType.USDT_FUTURES.value:
        return InstType.USDT_FUTURES
    return None


def _parse_side(value: Optional[str]) -> Optional[Side]:
    if value is None:
        return None
    value_lower = str(value).lower()
    if value_lower == "buy":
        return Side.BUY
    if value_lower == "sell":
        return Side.SELL
    return None


def _spot_position_delta_after_fee(event: ExecutionEvent) -> float:
    delta = event.size if event.side == Side.BUY else -event.size
    if event.fee == 0 or not event.fee_coin:
        return delta
    base_coin = _base_coin_from_symbol(event.symbol)
    if event.fee_coin.upper() != base_coin.upper():
        return delta
    fee_size = abs(event.fee)
    if event.side == Side.BUY:
        return event.size - fee_size
    return -event.size - fee_size


def _base_coin_from_symbol(symbol: str) -> str:
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def _first_string(row: dict, keys: list[str]) -> Optional[str]:
    for key in keys:
        if key in row and row[key]:
            return str(row[key])
    return None


def _first_float(row: dict, keys: list[str]) -> Optional[float]:
    for key in keys:
        if key in row and row[key] is not None:
            value = _safe_float(row[key])
            if value is not None:
                return value
    return None


def _safe_float(value: object) -> Optional[float]:
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_json_like(value: object) -> object | None:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _fee_detail_amount_and_coin(value: object) -> tuple[float, Optional[str]]:
    if isinstance(value, list):
        total = 0.0
        coin: Optional[str] = None
        mixed = False
        for item in value:
            item_total, item_coin = _fee_detail_amount_and_coin(item)
            total += item_total
            if item_coin:
                if coin is None:
                    coin = item_coin
                elif coin.upper() != item_coin.upper():
                    mixed = True
        return total, None if mixed else coin
    if not isinstance(value, dict):
        return 0.0, None
    total = 0.0
    found = False
    for key in ("fee", "fillFee", "transactionFee", "totalFee"):
        fee = _safe_float(value.get(key))
        if fee is None:
            continue
        total += fee
        found = True
    coin = _first_string(
        value,
        ["feeCoin", "feeCurrency", "feeCcy", "feeCurrencyCode", "feeCoinName", "coin", "currency"],
    )
    if found:
        return total, coin
    for nested_key in ("feeDetail", "details", "fees"):
        nested = value.get(nested_key)
        if nested is not None:
            nested_total, nested_coin = _fee_detail_amount_and_coin(nested)
            total += nested_total
            if coin is None:
                coin = nested_coin
    return total, coin


def _available_balance_from_rows(rows: list[dict], base_coin: str) -> Optional[float]:
    base_coin_upper = base_coin.upper()
    for row in rows:
        if not isinstance(row, dict):
            continue
        coin = _first_string(row, ["coin", "coinName", "currency", "asset", "baseCoin"])
        if coin is not None and coin.upper() != base_coin_upper:
            continue
        if coin is None:
            symbol = _first_string(row, ["symbol", "instId"])
            if symbol is not None and base_coin_upper not in symbol.upper():
                continue
        available = _first_float(
            row,
            ["available", "availableBalance", "availableAmount", "free", "normalBalance"],
        )
        if available is not None:
            return available
    return None


def _first_time(row: dict, keys: list[str]) -> Optional[float]:
    for key in keys:
        if key in row and row[key] is not None:
            try:
                ts = float(row[key])
            except (TypeError, ValueError):
                continue
            if ts > 1e12:
                return ts / 1000.0
            return ts
    return None
