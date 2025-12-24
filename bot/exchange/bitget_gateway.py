from __future__ import annotations

from typing import Any, Optional

import pybotters

from ..config import AppConfig
from ..types import InstType, OrderRequest
from .constraints import ConstraintsRegistry, InstrumentConstraints


class BitgetGateway:
    def __init__(self, client: pybotters.Client, store: pybotters.BitgetV2DataStore, config: AppConfig):
        self._client = client
        self.store = store
        self.config = config
        self.constraints = ConstraintsRegistry()
        self._ws_public = None
        self._ws_private = None

    async def start_public_ws(self) -> None:
        spot = self.config.symbols.spot
        perp = self.config.symbols.perp
        args = [
            {"instType": spot.instType, "channel": "books5", "instId": spot.symbol},
            {"instType": perp.instType, "channel": "books", "instId": perp.symbol},
        ]
        payload = {"op": "subscribe", "args": args}
        self._ws_public = await self._client.ws_connect(
            self.config.exchange.ws_public,
            send_json=payload,
            data_store=self.store,
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
            data_store=self.store,
        )

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
