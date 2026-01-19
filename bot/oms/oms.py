from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from ..config import AppConfig, HedgeConfig
from ..exchange.bitget_gateway import BitgetGateway
from ..exchange.constraints import InstrumentConstraints
from ..log.jsonl import JsonlLogger
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

    @property
    def remain(self) -> float:
        return max(0.0, self.want_qty - self.filled_qty)


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
        delta = event.size if event.side == Side.BUY else -event.size
        if event.inst_type == InstType.SPOT:
            self.spot_pos += delta
        elif event.inst_type == InstType.USDT_FUTURES:
            self.perp_pos += delta


class OMS:
    def __init__(
        self,
        gateway: BitgetGateway,
        config: AppConfig,
        risk: RiskGuards,
        orders_logger: JsonlLogger,
        fills_logger: JsonlLogger,
    ):
        self._gateway = gateway
        self._config = config
        self._hedge_cfg: HedgeConfig = config.hedge
        self._risk = risk
        self._orders_logger = orders_logger
        self._fills_logger = fills_logger
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
        self._unhedged_qty = 0.0
        self._unhedged_since: Optional[float] = None
        self._dry_run = config.strategy.dry_run

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
        await self._upsert_quote(
            OrderIntent.QUOTE_BID, Side.BUY, bid_px, bid_size, cycle_id, reason
        )
        await self._upsert_quote(
            OrderIntent.QUOTE_ASK, Side.SELL, ask_px, ask_size, cycle_id, reason
        )

    async def cancel_all(self, reason: str) -> None:
        for intent, order in list(self._active_quotes.items()):
            if order is None:
                continue
            await self._cancel_order(
                InstType.USDT_FUTURES, order, reason=reason, state="cancel"
            )
            self._active_quotes[intent] = None

    async def flatten(self, spot_bbo: Optional[book_md.BBO], cycle_id: int, reason: str) -> None:
        await self.cancel_all(reason=reason)
        if not self._gateway.constraints.ready():
            return
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
        if spot_bbo and self._positions.spot_pos != 0:
            side = Side.SELL if self._positions.spot_pos > 0 else Side.BUY
            size = abs(self._positions.spot_pos)
            price = spot_bbo.ask if side == Side.BUY else spot_bbo.bid
            await self._submit_order(
                OrderRequest(
                    inst_type=InstType.SPOT,
                    symbol=self._config.symbols.spot.symbol,
                    side=side,
                    order_type=OrderType.LIMIT,
                    size=size,
                    force=Force.IOC,
                    client_oid=self._new_client_oid(OrderIntent.FLATTEN, cycle_id),
                    intent=OrderIntent.FLATTEN,
                    cycle_id=cycle_id,
                    price=price,
                ),
                reason=reason,
            )

    async def sync_positions(self, timeout_sec: float = 5.0) -> None:
        positions_store = getattr(self._gateway.store, "positions", None)
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
        self._orders_logger.log(
            {
                "ts": time.time(),
                "event": "state",
                "intent": "SYSTEM",
                "state": "positions_sync",
                "spot_pos": self._positions.spot_pos,
                "perp_pos": self._positions.perp_pos,
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
                await self._handle_fill(event)
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
            self._cleanup_ticket(ticket_id)

    async def _handle_fill(self, event: ExecutionEvent) -> None:
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
                "intent": None if intent is None else intent.value,
                "ticket_id": ticket_id,
            }
        )
        self._positions.apply_fill(event)
        if intent == OrderIntent.HEDGE or ticket_id is not None:
            self._apply_hedge_fill(event, ticket_id)
            return
        if intent in (OrderIntent.FLATTEN, OrderIntent.UNWIND):
            return
        if event.inst_type == InstType.USDT_FUTURES:
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
        price = spot_bbo.ask if ticket.side == Side.BUY else spot_bbo.bid
        if ticket.side == Side.BUY:
            price *= 1 + (slip_bps / 10000.0)
        else:
            price *= 1 - (slip_bps / 10000.0)
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
            order_type=OrderType.LIMIT,
            size=ticket.remain,
            force=Force.IOC,
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
        ticket.filled_qty += event.size
        if ticket.remain <= 1e-9:
            ticket.status = "DONE"
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

    def _add_unhedged(self, event: ExecutionEvent) -> None:
        delta = event.size if event.side == Side.SELL else -event.size
        self._unhedged_qty += delta
        if self._unhedged_since is None:
            self._unhedged_since = time.time()

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
        price = constraints.adjust_price(price)
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

        if existing and not self._needs_replace(existing, price, size, constraints):
            return

        if existing:
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
        if order_id:
            self._active_quotes[intent] = ActiveOrder(
                order_id=order_id,
                client_oid=req.client_oid,
                price=price,
                size=size,
                side=side,
                intent=intent,
                ts=time.time(),
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

        if req.price is not None:
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
                self._orders_logger.log(
                    {
                        "event": "risk",
                        "intent": req.intent.value,
                        "source": "oms",
                        "mode": None,
                        "reason": "order_reject",
                        "leg": None,
                        "cycle_id": req.cycle_id,
                        "resp_code": resp_code,
                        "reject_streak": streak,
                        "client_oid": req.client_oid,
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
    ) -> bool:
        if abs(size - existing.size) > constraints.qty_step / 2:
            return True
        if abs(price - existing.price) >= constraints.tick_size:
            return True
        return False

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
        price = _first_float(row, ["price", "fillPrice", "tradePrice"]) or 0.0
        size = _first_float(row, ["size", "fillSz", "tradeQty", "tradeSize"]) or 0.0
        fee = _first_float(row, ["fee", "fillFee"]) or 0.0
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


def _first_string(row: dict, keys: list[str]) -> Optional[str]:
    for key in keys:
        if key in row and row[key]:
            return str(row[key])
    return None


def _first_float(row: dict, keys: list[str]) -> Optional[float]:
    for key in keys:
        if key in row and row[key] is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
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
