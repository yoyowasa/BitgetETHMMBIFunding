from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import pybotters

from ..config import AppConfig
from ..log.jsonl import JsonlLogger
from ..marketdata import book as book_md
from ..types import InstType, OrderRequest
from .constraints import ConstraintsRegistry, InstrumentConstraints


class BitgetGateway:
    def __init__(
        self,
        client: pybotters.Client,
        store: pybotters.BitgetV2DataStore,
        config: AppConfig,
        logger: Optional[JsonlLogger] = None,
        ws_disconnect_event: Optional[asyncio.Event] = None,
    ):
        self._client = client
        self.store = store
        self.config = config
        self.constraints = ConstraintsRegistry()
        self._ws_public = None
        self._ws_private = None
        self._logger = logger
        self._ws_disconnect_event = ws_disconnect_event
        self._public_book_channel = "books"
        self._book_filter_warned: set[tuple[str, str]] = set()
        self._book_ready_event = asyncio.Event()
        self._controlled_reconnect_until_ms = 0
        self._controlled_reconnect_reason: str | None = None
        self._book_channel_filter_supported: bool | None = None

    async def start_public_ws(self) -> None:
        spot = self.config.symbols.spot
        perp = self.config.symbols.perp
        channel = self._public_book_channel
        spot_inst_id = book_md._ws_inst_id(spot.symbol)
        perp_inst_id = book_md._ws_inst_id(perp.symbol)
        self._log(
            "book_ws_subscribe",
            intent="SYSTEM",
            source="ws_public",
            mode="INIT",
            reason="book_ws_subscribe",
            leg="books",
            inst_type=spot.instType,
            channel=channel,
            inst_id=spot_inst_id,
            raw_symbol=spot.symbol,
        )
        self._log(
            "book_ws_subscribe",
            intent="SYSTEM",
            source="ws_public",
            mode="INIT",
            reason="book_ws_subscribe",
            leg="books",
            inst_type=perp.instType,
            channel=channel,
            inst_id=perp_inst_id,
            raw_symbol=perp.symbol,
        )
        args = [
            {"instType": spot.instType, "channel": channel, "instId": spot_inst_id},
            {"instType": perp.instType, "channel": channel, "instId": perp_inst_id},
        ]
        payload = {"op": "subscribe", "args": args}
        self._ws_public = await self._client.ws_connect(
            self.config.exchange.ws_public,
            send_json=payload,
            hdlr_json=self._on_ws_message,
            auth=None,
        )

    async def start_private_ws(self) -> None:
        spot = self.config.symbols.spot
        perp = self.config.symbols.perp
        args = [
            {"instType": spot.instType, "channel": "orders", "instId": spot.symbol},
            {"instType": spot.instType, "channel": "fill", "instId": spot.symbol},
            {"instType": perp.instType, "channel": "orders", "instId": "default"},
            {"instType": perp.instType, "channel": "fill", "instId": "default"},
            {"instType": perp.instType, "channel": "positions", "instId": "default"},
        ]
        payload = {"op": "subscribe", "args": args}
        self._ws_private = await self._client.ws_connect(
            self.config.exchange.ws_private,
            send_json=payload,
            hdlr_json=self._on_ws_message,
        )

    def _on_ws_message(self, msg: dict, ws=None) -> None:
        self.store.onmessage(msg, ws)
        if isinstance(msg, dict) and ("event" in msg or "op" in msg):
            self._log(
                "ws_control_message",
                intent="SYSTEM",
                source="ws",
                mode="RUN",
                reason="ws_control_message",
                leg="books",
                data={"message": msg},
            )
        if book_md._is_book_push(msg):
            book_md._log_first_book_push(self._logger, msg)
            arg = msg.get("arg") if isinstance(msg.get("arg"), dict) else {}
            inst_type = arg.get("instType", "?")
            channel = arg.get("channel", "?")
            inst_id = arg.get("instId", "?")
            book_md._latch_book_ready(inst_type, channel, inst_id)
            book_md._stat_book_msg(self._logger, msg)

    async def run_public_ws(self, reconnect_delay: float = 3.0) -> None:
        book_timeout_sec = self.config.risk.book_boot_timeout_sec
        if book_timeout_sec is None:
            stale_sec = (
                self.config.risk.book_stale_sec
                if self.config.risk.book_stale_sec is not None
                else self.config.risk.stale_sec
            )
            book_timeout_sec = max(3.0, stale_sec * 2)
        current_channel = "books"
        while True:
            try:
                self._public_book_channel = current_channel
                self._book_ready_event.clear()
                await self.start_public_ws()
                self._log("ws_public_connected", channel=current_channel)
                ready = await self._wait_for_book_bootstrap(
                    book_timeout_sec, channel=current_channel
                )
                if not ready:
                    self._log(
                        "book_boot_timeout",
                        intent="SYSTEM",
                        source="ws_public",
                        mode="INIT",
                        reason="book_boot_timeout",
                        leg="books",
                        cycle_id=None,
                        channel=current_channel,
                    )
                    self._signal_ws_disconnect("public", error="book_fallback_failed")
                else:
                    self._clear_controlled_reconnect()
                    self._book_ready_event.set()
                if self._ws_public is not None:
                    await self._ws_public.wait()
                self._signal_ws_disconnect("public")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._signal_ws_disconnect("public", error=repr(exc))
            await asyncio.sleep(reconnect_delay)

    async def run_private_ws(self, reconnect_delay: float = 3.0) -> None:
        while True:
            try:
                await self.start_private_ws()
                self._log("ws_private_connected")
                if self._ws_private is not None:
                    await self._ws_private.wait()
                self._signal_ws_disconnect("private")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._signal_ws_disconnect("private", error=repr(exc))
            await asyncio.sleep(reconnect_delay)

    async def rest_get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.config.exchange.base_url}{path}"
        resp = await self._client.get(url, params=params)
        return await resp.json()

    async def rest_post(self, path: str, data: dict) -> dict:
        url = f"{self.config.exchange.base_url}{path}"
        resp = await self._client.post(url, data=data)
        return await resp.json()

    async def fetch_spot_symbols(self) -> dict:
        return await self.rest_get("/api/v2/spot/public/symbols")

    async def fetch_perp_contracts(self) -> dict:
        params = {
            "productType": self.config.symbols.perp.productType,
            "symbol": self.config.symbols.perp.symbol,
        }
        return await self.rest_get("/api/v2/mix/market/contracts", params=params)

    async def fetch_funding(self) -> dict:
        params = {
            "symbol": self.config.symbols.perp.symbol,
            "productType": self.config.symbols.perp.productType,
        }
        return await self.rest_get("/api/v2/mix/market/current-fund-rate", params=params)

    async def get_pos_mode(self) -> Optional[str]:
        perp = self.config.symbols.perp
        params = {
            "productType": perp.productType,
            "symbol": perp.symbol,
            "marginCoin": perp.marginCoin,
        }
        payload = await self.rest_get("/api/v2/mix/account/account", params=params)
        data = payload.get("data")
        if isinstance(data, dict):
            return data.get("posMode")
        if isinstance(data, list) and data:
            for row in data:
                if not isinstance(row, dict):
                    continue
                if row.get("symbol") == perp.symbol:
                    return row.get("posMode")
            return data[0].get("posMode")
        return None

    async def set_pos_mode(self, pos_mode: str) -> dict:
        perp = self.config.symbols.perp
        data = {"productType": perp.productType, "posMode": pos_mode}
        return await self.rest_post("/api/v2/mix/account/set-position-mode", data)

    async def load_constraints(self) -> ConstraintsRegistry:
        spot = self.config.symbols.spot
        perp = self.config.symbols.perp

        spot_data = await self.fetch_spot_symbols()
        spot_row = _find_row(spot_data, "symbol", spot.symbol)
        if spot_row:
            self.constraints.spot = _parse_spot_constraints(spot_row)

        perp_data = await self.fetch_perp_contracts()
        perp_row = _find_row(perp_data, "symbol", perp.symbol)
        if perp_row:
            self.constraints.perp = _parse_perp_constraints(perp_row)

        return self.constraints

    async def refresh_constraints_loop(
        self,
        interval_sec: float = 60.0,
        retry_sec: float = 5.0,
    ) -> None:
        while True:
            try:
                await self.load_constraints()
                self._log(
                    "constraints_loaded",
                    spot_ready=self.constraints.spot.is_ready() if self.constraints.spot else False,
                    perp_ready=self.constraints.perp.is_ready() if self.constraints.perp else False,
                )
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log("constraints_error", error=repr(exc))
                await asyncio.sleep(retry_sec)

    async def place_order(self, req: OrderRequest) -> dict:
        if req.inst_type == InstType.SPOT:
            data = {
                "symbol": req.symbol,
                "side": req.side.value,
                "orderType": req.order_type.value,
                "size": str(req.size),
                "clientOid": req.client_oid,
            }
            if req.price is not None:
                data["price"] = str(req.price)
            if req.force is not None:
                data["force"] = req.force.value
            return await self.rest_post("/api/v2/spot/trade/place-order", data)

        if req.inst_type == InstType.USDT_FUTURES:
            data = {
                "symbol": req.symbol,
                "productType": self.config.symbols.perp.productType,
                "marginMode": self.config.symbols.perp.marginMode,
                "marginCoin": self.config.symbols.perp.marginCoin,
                "side": req.side.value,
                "orderType": req.order_type.value,
                "size": str(req.size),
                "clientOid": req.client_oid,
            }
            if req.price is not None:
                data["price"] = str(req.price)
            if req.force is not None:
                data["timeInForceValue"] = req.force.value
            if req.reduce_only is not None:
                data["reduceOnly"] = "YES" if req.reduce_only else "NO"
            return await self.rest_post("/api/v2/mix/order/place-order", data)

        raise ValueError(f"unsupported inst_type: {req.inst_type}")

    async def cancel_order(
        self,
        inst_type: InstType,
        symbol: str,
        order_id: Optional[str] = None,
        client_oid: Optional[str] = None,
    ) -> dict:
        if inst_type == InstType.SPOT:
            data: dict[str, Any] = {"symbol": symbol}
            if order_id:
                data["orderId"] = order_id
            if client_oid:
                data["clientOid"] = client_oid
            return await self.rest_post("/api/v2/spot/trade/cancel-order", data)

        if inst_type == InstType.USDT_FUTURES:
            data = {
                "symbol": symbol,
                "productType": self.config.symbols.perp.productType,
            }
            if order_id:
                data["orderId"] = order_id
            if client_oid:
                data["clientOid"] = client_oid
            return await self.rest_post("/api/v2/mix/order/cancel-order", data)

        raise ValueError(f"unsupported inst_type: {inst_type}")

    def _log(self, event: str, **fields) -> None:
        if not self._logger:
            return
        self._logger.log({"event": event, **fields})

    def _signal_ws_disconnect(self, scope: str, error: Optional[str] = None) -> None:
        if scope == "public":
            self._book_ready_event.clear()
            if self._controlled_reconnect_active():
                self._log(
                    "ws_disconnect_controlled",
                    scope=scope,
                    reason=self._controlled_reconnect_reason,
                    error=error,
                )
                return
        if self._ws_disconnect_event is not None:
            self._ws_disconnect_event.set()
        self._log("ws_disconnect", scope=scope, error=error)

    @property
    def public_book_channel(self) -> str:
        return self._public_book_channel

    @property
    def book_ready(self) -> bool:
        return self._book_ready_event.is_set()

    def note_book_channel_filter_unavailable(
        self, inst_type: InstType, symbol: str, channel: str
    ) -> None:
        if not channel:
            return
        self._book_channel_filter_supported = False
        inst_value = inst_type.value if isinstance(inst_type, InstType) else str(inst_type)
        key = (symbol, channel)
        if key in self._book_filter_warned:
            return
        self._book_filter_warned.add(key)
        self._log(
            "book_channel_filter_unavailable",
            intent="SYSTEM",
            source="marketdata",
            mode="RUN",
            reason="book_channel_filter_unavailable",
            leg="books",
            cycle_id=None,
            inst_type=inst_value,
            symbol=symbol,
            channel=channel,
        )

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _enter_controlled_reconnect(self, reason: str) -> None:
        grace = self.config.risk.controlled_reconnect_grace_sec
        if grace <= 0:
            self._controlled_reconnect_until_ms = 0
            self._controlled_reconnect_reason = None
            return
        self._controlled_reconnect_until_ms = self._now_ms() + int(grace * 1000)
        self._controlled_reconnect_reason = reason

    def _clear_controlled_reconnect(self) -> None:
        self._controlled_reconnect_until_ms = 0
        self._controlled_reconnect_reason = None

    def _controlled_reconnect_active(self) -> bool:
        if self._controlled_reconnect_until_ms <= 0:
            return False
        return self._now_ms() <= self._controlled_reconnect_until_ms

    async def _close_public_ws(self) -> None:
        if self._ws_public is None:
            return
        self._book_ready_event.clear()
        close = getattr(self._ws_public, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result
        self._ws_public = None

    async def _unsubscribe_public_books(self, channel: str) -> None:
        if self._ws_public is None or not channel:
            return
        spot = self.config.symbols.spot
        perp = self.config.symbols.perp
        payload = {
            "op": "unsubscribe",
            "args": [
                {"instType": spot.instType, "channel": channel, "instId": spot.symbol},
                {"instType": perp.instType, "channel": channel, "instId": perp.symbol},
            ],
        }
        try:
            send_json = getattr(self._ws_public, "send_json", None)
            if callable(send_json):
                result = send_json(payload)
                if asyncio.iscoroutine(result):
                    await result
                return
            send_str = getattr(self._ws_public, "send_str", None)
            if callable(send_str):
                result = send_str(json.dumps(payload))
                if asyncio.iscoroutine(result):
                    await result
        except Exception:
            return

    async def _clear_book_store(self) -> tuple[bool, str | None]:
        book_store = getattr(self.store, "book", None)
        if book_store is None:
            return False, "missing"
        clear = getattr(book_store, "clear", None)
        if callable(clear):
            try:
                result = clear()
                if asyncio.iscoroutine(result):
                    await result
                return True, None
            except Exception as exc:
                return False, repr(exc)
        for attr in ("_data", "_store"):
            data = getattr(book_store, attr, None)
            if isinstance(data, (dict, list)):
                try:
                    data.clear()
                    return True, None
                except Exception as exc:
                    return False, repr(exc)
        return False, "unsupported"

    async def _wait_for_book_bootstrap(self, timeout_sec: float, channel: str) -> bool:
        spot = self.config.symbols.spot
        perp = self.config.symbols.perp
        spot_inst_id = book_md._ws_inst_id(spot.symbol)
        perp_inst_id = book_md._ws_inst_id(perp.symbol)
        spot_task = book_md._wait_for_book_bootstrap(
            self._logger, spot.instType, channel, spot_inst_id, timeout_sec
        )
        perp_task = book_md._wait_for_book_bootstrap(
            self._logger, perp.instType, channel, perp_inst_id, timeout_sec
        )
        spot_ready, perp_ready = await asyncio.gather(spot_task, perp_task)
        return spot_ready and perp_ready

    def _book_ready(self, inst_type: str, symbol: str, channel: str) -> bool:
        book_store = getattr(self.store, "book", None)
        if book_store is None or not hasattr(book_store, "sorted"):
            return False
        query = {"instType": inst_type, "instId": symbol, "channel": channel}
        try:
            book = book_store.sorted(query, limit=1)
        except Exception:
            return False
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if bids and asks:
            return True
        try:
            book = book_store.sorted({"instType": inst_type, "instId": symbol}, limit=1)
        except Exception:
            return False
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        return bool(bids) and bool(asks)


def _find_row(data: dict, key: str, value: str) -> Optional[dict]:
    for row in data.get("data", []) or []:
        if row.get(key) == value:
            return row
    return None


def _parse_spot_constraints(row: dict) -> InstrumentConstraints:
    min_qty = _first_float(row, ["minTradeAmount", "minTradeNum", "minTradeQty"])
    min_notional = _first_float(row, ["minTradeUSDT", "minTradeQuoteAmount", "minNotional"])
    qty_scale = _first_int(row, ["quantityScale", "basePrecision", "quantityPrecision"])
    price_scale = _first_int(row, ["priceScale", "pricePrecision"])
    qty_step = 10 ** (-qty_scale) if qty_scale is not None else 0.0
    tick_size = 10 ** (-price_scale) if price_scale is not None else 0.0
    # BitgetのSPOTでは minTradeAmount が "0" のことがあるため、
    # 最小数量は quantityPrecision 由来のステップにフォールバックする
    if (min_qty is None or min_qty <= 0.0) and qty_step > 0.0:
        min_qty = qty_step
    return InstrumentConstraints(
        min_qty=min_qty or 0.0,
        qty_step=qty_step,
        min_notional=min_notional or 0.0,
        tick_size=tick_size,
    )


def _parse_perp_constraints(row: dict) -> InstrumentConstraints:
    min_qty = _first_float(row, ["minTradeNum", "minTradeAmount", "minTradeVol"])
    min_notional = _first_float(row, ["minTradeUSDT", "minNotional"])
    qty_step = _first_float(row, ["sizeMultiplier", "qtyStep"])
    price_scale = _first_int(row, ["pricePlace", "pricePrecision"])
    tick_size = 10 ** (-price_scale) if price_scale is not None else 0.0
    if qty_step is None:
        qty_place = _first_int(row, ["volumePlace", "volPrecision"])
        qty_step = 10 ** (-qty_place) if qty_place is not None else 0.0
    return InstrumentConstraints(
        min_qty=min_qty or 0.0,
        qty_step=qty_step or 0.0,
        min_notional=min_notional or 0.0,
        tick_size=tick_size,
    )


def _first_float(row: dict, keys: list[str]) -> Optional[float]:
    for key in keys:
        if key in row and row[key] is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    return None


def _first_int(row: dict, keys: list[str]) -> Optional[int]:
    for key in keys:
        if key in row and row[key] is not None:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                continue
    return None
