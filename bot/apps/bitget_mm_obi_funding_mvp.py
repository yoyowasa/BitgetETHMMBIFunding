from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Literal, Optional

import orjson
import pybotters
from dotenv import load_dotenv

# --- Bitget V2 WS エンドポイント（公開/認証） ---
WS_PUBLIC = "wss://ws.bitget.com/v2/ws/public"
WS_PRIVATE = "wss://ws.bitget.com/v2/ws/private"
BASE_URL = "https://api.bitget.com"

load_dotenv(override=True)


# -----------------------------
# JSONL ロガー
# -----------------------------
class JsonlLogger:
    def __init__(self, path: str) -> None:
        self.path = path
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

    def write(self, event: str, **fields: Any) -> None:
        required = {
            "intent": None,
            "source": None,
            "mode": None,
            "reason": None,
            "leg": None,
            "cycle_id": None,
        }
        rec = {"ts": int(time.time() * 1000), "event": event, **required, **fields}
        with open(self.path, "ab") as f:
            f.write(orjson.dumps(rec) + b"\n")


# -----------------------------
# 設定（環境変数）
# -----------------------------
@dataclass
class Settings:
    symbol: str = os.getenv("SYMBOL", "ETHUSDT")

    # WS instType（識別子）
    spot_inst: str = os.getenv("SPOT_INST_TYPE", "SPOT")
    perp_inst: str = os.getenv("PERP_INST_TYPE", "USDT-FUTURES")

    # REST 先物
    product_type: str = os.getenv("PRODUCT_TYPE", "USDT-FUTURES")
    margin_mode: str = os.getenv("MARGIN_MODE", "isolated")
    margin_coin: str = os.getenv("MARGIN_COIN", "USDT")

    # トレード
    dry_run: bool = os.getenv("DRY_RUN", "1") == "1"
    log_path: str = os.getenv("LOG_PATH", "logs/bitget_mm_obi_funding.jsonl")
    target_pos_mode: str = os.getenv("TARGET_POS_MODE", "one_way_mode")
    auto_set_pos_mode: bool = os.getenv("AUTO_SET_POS_MODE", "1") == "1"

    # クオート
    quote_size: float = float(os.getenv("QUOTE_SIZE", "0.05"))  # ETH ベース
    refresh_ms: int = int(os.getenv("REFRESH_MS", "500"))
    obi_levels: int = int(os.getenv("OBI_LEVELS", "5"))  # books5 は最大 5

    base_half_spread_bps: float = float(os.getenv("BASE_HALF_SPREAD_BPS", "2.0"))
    obi_k_bps: float = float(os.getenv("OBI_K_BPS", "0.8"))

    # 資金調達率フィルタ/バイアス
    min_abs_funding: float = float(os.getenv("MIN_ABS_FUNDING", "0.00002"))
    funding_bias: float = float(os.getenv("FUNDING_BIAS", "0.6"))

    # ヘッジ
    hedge_ioc_slip_bps: float = float(os.getenv("HEDGE_IOC_SLIP_BPS", "5.0"))
    max_unhedged_notional: float = float(os.getenv("MAX_UNHEDGED_NOTIONAL", "200.0"))
    max_unhedged_sec: float = float(os.getenv("MAX_UNHEDGED_SEC", "2.0"))
    max_position_notional: float = float(os.getenv("MAX_POSITION_NOTIONAL", "0"))
    stale_sec: float = float(os.getenv("STALE_SEC", "2.0"))
    cooldown_sec: float = float(os.getenv("COOLDOWN_SEC", "0"))

    # 疑似約定（dry-run 検証用）
    simulate_fills: bool = os.getenv("SIMULATE_FILLS", "0") == "1"
    simulate_fill_interval_sec: float = float(os.getenv("SIM_FILL_INTERVAL_SEC", "5"))
    simulate_fill_qty: float = float(os.getenv("SIM_FILL_QTY", os.getenv("QUOTE_SIZE", "0.01")))
    simulate_fill_side: str = os.getenv("SIM_FILL_SIDE", "both")  # buy/sell/both
    simulate_hedge_success: bool = os.getenv("SIMULATE_HEDGE_SUCCESS", "0") == "1"

    # books チャネル
    book_channel: str = os.getenv("BOOK_CHANNEL", "books5")  # books5 が簡易スナップショット


def now_ms() -> int:
    return int(time.time() * 1000)


# -----------------------------
# 制約
# -----------------------------
@dataclass
class SpotConstraints:
    price_precision: int
    qty_precision: int
    min_trade_usdt: float


@dataclass
class PerpConstraints:
    price_place: int
    volume_place: int
    price_end_step: float
    size_multiplier: float
    min_trade_num: float
    min_trade_usdt: float


def round_down(x: float, step: float) -> float:
    if step <= 0:
        return x
    return int(x / step) * step


def round_price_precision(px: float, prec: int) -> float:
    p = 10**prec
    return round(px * p) / p


def round_qty_precision(q: float, prec: int) -> float:
    p = 10**prec
    return round_down(q * p, 1.0) / p


def format_by_precision(value: float, precision: int) -> str:
    if precision < 0:
        return str(value)
    quant = Decimal("1").scaleb(-precision)
    d = Decimal(str(value)).quantize(quant, rounding=ROUND_DOWN)
    return f"{d:.{precision}f}"


# -----------------------------
# Bitget REST（薄いラッパ）
# -----------------------------
class BitgetRest:
    def __init__(self, client: pybotters.Client, s: Settings, log: JsonlLogger) -> None:
        self.client = client
        self.s = s
        self.log = log
        self.spot_c: Optional[SpotConstraints] = None
        self.perp_c: Optional[PerpConstraints] = None

    async def _safe_json(
        self,
        resp,
        *,
        intent: str,
        source: str,
        mode: str,
        reason: str,
        leg: str,
    ) -> dict:
        try:
            return await resp.json(content_type=None)
        except Exception as e:
            text = await resp.text()
            self.log.write(
                "http_error",
                intent=intent,
                source=source,
                mode=mode,
                reason=reason,
                leg=leg,
                status=resp.status,
                url=str(resp.url),
                error=repr(e),
                body=text[:500],
            )
            return {
                "ok": False,
                "status": resp.status,
                "url": str(resp.url),
                "error": repr(e),
                "body": text[:500],
            }

    async def load_constraints(self) -> None:
        # Spot シンボル
        try:
            async with self.client.get(
                f"{BASE_URL}/api/v2/spot/public/symbols",
                params={"symbol": self.s.symbol},
            ) as r:
                j = await self._safe_json(
                    r,
                    intent="system",
                    source="rest",
                    mode="HTTP",
                    reason="spot_symbols",
                    leg="spot",
                )
            row = j["data"][0]
            self.spot_c = SpotConstraints(
                price_precision=int(row["pricePrecision"]),
                qty_precision=int(row["quantityPrecision"]),
                min_trade_usdt=float(row["minTradeUSDT"]),
            )
        except Exception:
            self.spot_c = None

        # Perp 契約
        try:
            async with self.client.get(
                f"{BASE_URL}/api/v2/mix/market/contracts",
                params={"productType": self.s.product_type, "symbol": self.s.symbol},
            ) as r:
                j = await self._safe_json(
                    r,
                    intent="system",
                    source="rest",
                    mode="HTTP",
                    reason="perp_contracts",
                    leg="perp",
                )
            row = j["data"][0]
            self.perp_c = PerpConstraints(
                price_place=int(row["pricePlace"]),
                volume_place=int(row["volumePlace"]),
                price_end_step=float(row["priceEndStep"]),
                size_multiplier=float(row["sizeMultiplier"]),
                min_trade_num=float(row["minTradeNum"]),
                min_trade_usdt=float(row["minTradeUSDT"]),
            )
        except Exception:
            self.perp_c = None

        self.log.write(
            "constraints_loaded",
            spot=self.spot_c.__dict__ if self.spot_c else None,
            perp=self.perp_c.__dict__ if self.perp_c else None,
        )

    async def get_pos_mode(self) -> Optional[str]:
        try:
            async with self.client.get(
                f"{BASE_URL}/api/v2/mix/account/account",
                params={
                    "symbol": self.s.symbol,
                    "productType": self.s.product_type,
                    "marginCoin": self.s.margin_coin,
                },
            ) as r:
                j = await r.json()
            data = j.get("data")
            if isinstance(data, dict):
                return data.get("posMode")
            if isinstance(data, list) and data:
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    if row.get("symbol") == self.s.symbol:
                        return row.get("posMode")
                return data[0].get("posMode")
            return None
        except Exception:
            return None

    async def set_position_mode(self, pos_mode: str) -> dict:
        data = {"productType": self.s.product_type, "posMode": pos_mode}
        if self.s.dry_run:
            return {"dry_run": True, "data": data}
        async with self.client.post(
            f"{BASE_URL}/api/v2/mix/account/set-position-mode", data=data
        ) as r:
            return await r.json()

    async def get_funding_rate(self) -> Optional[float]:
        # current-fund-rate（API 名）
        try:
            async with self.client.get(
                f"{BASE_URL}/api/v2/mix/market/current-fund-rate",
                params={"symbol": self.s.symbol, "productType": self.s.product_type},
            ) as r:
                j = await self._safe_json(
                    r,
                    intent="system",
                    source="rest",
                    mode="HTTP",
                    reason="funding_rate",
                    leg="perp",
                )
            data = j.get("data")
            if isinstance(data, list):
                row = data[0] if data else None
            elif isinstance(data, dict):
                row = data
            else:
                row = None
            if not row or "fundingRate" not in row:
                return None
            return float(row["fundingRate"])
        except Exception:
            return None

    async def spot_place(self, data: dict) -> dict:
        # spot place-order（API 名）
        if self.s.dry_run:
            return {"dry_run": True, "data": data}
        async with self.client.post(f"{BASE_URL}/api/v2/spot/trade/place-order", data=data) as r:
            return await self._safe_json(
                r,
                intent="order",
                source="rest",
                mode="HTTP",
                reason="spot_place",
                leg="spot",
            )

    async def spot_cancel(self, data: dict) -> dict:
        # spot cancel-order（API 名）
        if self.s.dry_run:
            return {"dry_run": True, "data": data}
        async with self.client.post(f"{BASE_URL}/api/v2/spot/trade/cancel-order", data=data) as r:
            return await self._safe_json(
                r,
                intent="order",
                source="rest",
                mode="HTTP",
                reason="spot_cancel",
                leg="spot",
            )

    async def perp_place(self, data: dict) -> dict:
        # mix place-order（API 名）
        if self.s.dry_run:
            return {"dry_run": True, "data": data}
        async with self.client.post(f"{BASE_URL}/api/v2/mix/order/place-order", data=data) as r:
            return await self._safe_json(
                r,
                intent="order",
                source="rest",
                mode="HTTP",
                reason="perp_place",
                leg="perp",
            )

    async def perp_cancel(self, data: dict) -> dict:
        # mix cancel-order（API 名）
        if self.s.dry_run:
            return {"dry_run": True, "data": data}
        async with self.client.post(f"{BASE_URL}/api/v2/mix/order/cancel-order", data=data) as r:
            return await self._safe_json(
                r,
                intent="order",
                source="rest",
                mode="HTTP",
                reason="perp_cancel",
                leg="perp",
            )


# -----------------------------
# 板ヘルパー（DataStore）
# -----------------------------
@dataclass
class BBO:
    bid: Optional[float]
    ask: Optional[float]
    bid_sz: Optional[float]
    ask_sz: Optional[float]
    ts_ms: int


def book_sorted(store: pybotters.BitgetV2DataStore, inst_type: str, inst_id: str, limit: int) -> dict:
    # store.book.sorted(query, limit=...) を使う
    return store.book.sorted({"instType": inst_type, "instId": inst_id}, limit=limit)


def _book_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _book_price(row: dict) -> Optional[float]:
    if isinstance(row, (list, tuple)) and len(row) >= 1:
        return _book_float(row[0])
    if isinstance(row, dict):
        return _book_float(row.get("price") or row.get("px"))
    return None


def _book_amount(row: dict) -> Optional[float]:
    if isinstance(row, (list, tuple)) and len(row) >= 2:
        return _book_float(row[1])
    if isinstance(row, dict):
        return _book_float(row.get("amount") or row.get("size") or row.get("qty") or row.get("sz"))
    return None


def _book_ts_ms(*rows_list: list[dict]) -> Optional[int]:
    ts_val = None
    for rows in rows_list:
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("ts") or row.get("timestamp") or row.get("time")
            ts = _book_float(raw)
            if ts is None:
                continue
            if ts > 1e12:
                ts_val = ts if ts_val is None else max(ts_val, ts)
            else:
                ts_ms = ts * 1000.0
                ts_val = ts_ms if ts_val is None else max(ts_val, ts_ms)
    return int(ts_val) if ts_val is not None else None


def bbo_obi_from_sorted(book: dict, levels: int) -> tuple[BBO, float]:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid = _book_price(bids[0]) if bids else None
    ask = _book_price(asks[0]) if asks else None
    bid_sz = _book_amount(bids[0]) if bids else None
    ask_sz = _book_amount(asks[0]) if asks else None

    bsum = sum(_book_amount(x) or 0.0 for x in bids[:levels]) if bids else 0.0
    asum = sum(_book_amount(x) or 0.0 for x in asks[:levels]) if asks else 0.0
    denom = bsum + asum
    obi = 0.0 if denom <= 0 else (bsum - asum) / denom

    ts_ms = _book_ts_ms(bids, asks) or now_ms()
    return BBO(bid=bid, ask=ask, bid_sz=bid_sz, ask_sz=ask_sz, ts_ms=ts_ms), obi


# -----------------------------
# 約定正規化（Bitget V2）
# -----------------------------
def pick(d: dict, keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def parse_fill(inst_type: str, d: dict) -> Optional[dict]:
    """
    SPOT/PERP の約定行を正規化する。

    - PERP: tradeId / clientOid / price / baseVolume
    - SPOT: tradeId / orderId / priceAvg / size（spot fill に clientOid は無い）
    """
    try:
        symbol = str(pick(d, ["symbol", "instId"], ""))
        side = str(pick(d, ["side"], "")).lower()
        if not symbol or side not in ("buy", "sell"):
            return None

        trade_id = str(pick(d, ["tradeId", "fillId", "execId", "id"], ""))
        order_id = str(pick(d, ["orderId", "order_id"], ""))
        ts = int(pick(d, ["uTime", "ts", "cTime"], 0))

        price = None
        base_volume = None
        price_avg = None
        size = None
        client_oid = ""

        if inst_type == "USDT-FUTURES":
            price = float(pick(d, ["price", "fillPx", "tradePrice"]))
            base_volume = float(pick(d, ["baseVolume", "size", "fillSz", "tradeSize"]))
            client_oid = str(pick(d, ["clientOid", "clientOrderId"], ""))
        elif inst_type == "SPOT":
            price_avg = float(pick(d, ["priceAvg", "price", "fillPx"]))
            size = float(pick(d, ["size", "baseVolume", "fillSz"]))
            client_oid = ""  # spot push には clientOid が来ない
        else:
            return None

        qty = base_volume if inst_type == "USDT-FUTURES" else size
        px_for_dedupe = price if price is not None else price_avg
        dedupe_id = trade_id if trade_id else f"{inst_type}:{order_id}:{ts}:{qty}:{px_for_dedupe}"

        return {
            "instType": inst_type,
            "symbol": symbol,
            "side": side,
            "tradeId": trade_id,
            "orderId": order_id,
            "clientOid": client_oid,
            "price": price,
            "baseVolume": base_volume,
            "priceAvg": price_avg,
            "size": size,
            "ts_ms": ts,
            "dedupe_id": dedupe_id,
            "raw": d,
        }
    except Exception:
        return None


# -----------------------------
# OMS: PERP 2本クオート + SPOT IOC ヘッジ
# -----------------------------
@dataclass
class LiveOrder:
    side: Literal["buy", "sell"]
    px: float
    qty: float
    client_oid: str
    order_id: Optional[str]
    created_ms: int


class OMS:
    def __init__(self, s: Settings, rest: BitgetRest, log: JsonlLogger) -> None:
        self.s = s
        self.rest = rest
        self.log = log

        self.quote_bid: Optional[LiveOrder] = None
        self.quote_ask: Optional[LiveOrder] = None
        self._lock = asyncio.Lock()

        # 影ポジション（約定ベース）
        self.perp_pos: float = 0.0
        self.spot_pos: float = 0.0

        # Spot orderId 対応: clientOid -> orderId と逆引き
        self.spot_client_to_order: dict[str, str] = {}
        self.spot_order_to_client: dict[str, str] = {}

        # ヘッジ追跡: clientOid -> deadline
        self.pending_hedge: dict[str, int] = {}
        self.unhedged_since_ms: Optional[int] = None

    def mk_client_oid(self, prefix: str, cycle_id: int, leg: str, side: str) -> str:
        # 長さ制限に備えて短くする
        rnd = uuid.uuid4().hex[:6]
        s = "B" if side == "buy" else "S"
        return f"{prefix}{cycle_id:08x}{s}{leg[:1]}{rnd}"

    def normalize_perp(self, px: float, qty: float) -> tuple[Optional[float], Optional[float], str]:
        c = self.rest.perp_c
        if not c:
            return None, None, "constraints_missing"

        # tick サイズ推定（必要なら調整）
        tick = c.price_end_step * (10 ** (-c.price_place))
        px2 = round_down(px, tick)
        qty2 = round_down(qty, c.size_multiplier)
        qty2 = round_qty_precision(qty2, c.volume_place)

        if qty2 < c.min_trade_num:
            return None, None, "below_min_qty"

        if (px2 * qty2) < c.min_trade_usdt:
            return None, None, "below_min_notional"

        return px2, qty2, "ok"

    def normalize_spot(self, px: float, qty: float) -> tuple[Optional[float], Optional[float], str]:
        c = self.rest.spot_c
        if not c:
            return None, None, "constraints_missing"
        px2 = round_price_precision(px, c.price_precision)
        qty2 = round_qty_precision(qty, c.qty_precision)
        if qty2 <= 0:
            return None, None, "qty_zero"
        if (px2 * qty2) < c.min_trade_usdt:
            return None, None, "below_min_notional"
        return px2, qty2, "ok"

    def fmt_perp_price(self, px: float) -> str:
        c = self.rest.perp_c
        if not c:
            return str(px)
        return format_by_precision(px, c.price_place)

    def fmt_perp_size(self, qty: float) -> str:
        c = self.rest.perp_c
        if not c:
            return str(qty)
        return format_by_precision(qty, c.volume_place)

    def fmt_spot_price(self, px: float) -> str:
        c = self.rest.spot_c
        if not c:
            return str(px)
        return format_by_precision(px, c.price_precision)

    def fmt_spot_size(self, qty: float) -> str:
        c = self.rest.spot_c
        if not c:
            return str(qty)
        return format_by_precision(qty, c.qty_precision)

    async def cancel_quotes(self, reason: str, source: str = "strategy") -> None:
        async with self._lock:
            if self.quote_bid:
                data = {
                    "symbol": self.s.symbol,
                    "productType": self.s.product_type,
                    "marginCoin": self.s.margin_coin,
                    "orderId": self.quote_bid.order_id or "",
                    "clientOid": self.quote_bid.client_oid,
                }
                await self.rest.perp_cancel(data)
                self.log.write(
                    "order_cancel",
                    intent="quote",
                    source=source,
                    mode="QUOTING",
                    reason=reason,
                    leg="perp_bid",
                    cycle_id=None,
                    data=data,
                )
                self.quote_bid = None

            if self.quote_ask:
                data = {
                    "symbol": self.s.symbol,
                    "productType": self.s.product_type,
                    "marginCoin": self.s.margin_coin,
                    "orderId": self.quote_ask.order_id or "",
                    "clientOid": self.quote_ask.client_oid,
                }
                await self.rest.perp_cancel(data)
                self.log.write(
                    "order_cancel",
                    intent="quote",
                    source=source,
                    mode="QUOTING",
                    reason=reason,
                    leg="perp_ask",
                    cycle_id=None,
                    data=data,
                )
                self.quote_ask = None

    async def set_quotes(
        self, cycle_id: int, bid_px: float, ask_px: float, qty: float, funding: float, obi: float
    ) -> None:
        """
        PERP に最大 1 本の bid / ask を維持する。
        """
        async with self._lock:

            def need_replace(o: Optional[LiveOrder], px: float, q: float) -> bool:
                if o is None:
                    return True
                if abs(o.px - px) / max(px, 1e-9) > 2e-5:
                    return True
                if abs(o.qty - q) / max(q, 1e-9) > 0.05:
                    return True
                return False

            nbid_px, nbid_qty, ok = self.normalize_perp(bid_px, qty)
            if ok != "ok":
                self.log.write(
                    "order_skip",
                    intent="quote",
                    source="strategy",
                    mode="QUOTING",
                    reason=ok,
                    leg="perp_bid",
                    cycle_id=cycle_id,
                    bid_px=bid_px,
                    qty=qty,
                    funding=funding,
                    obi=obi,
                )
            else:
                assert nbid_px is not None and nbid_qty is not None
                if need_replace(self.quote_bid, nbid_px, nbid_qty):
                    if self.quote_bid:
                        await self.rest.perp_cancel(
                            {
                                "symbol": self.s.symbol,
                                "productType": self.s.product_type,
                                "marginCoin": self.s.margin_coin,
                                "orderId": self.quote_bid.order_id or "",
                                "clientOid": self.quote_bid.client_oid,
                            }
                        )
                    coid = self.mk_client_oid("Q", cycle_id, "BID", "buy")
                    data = {
                        "symbol": self.s.symbol,
                        "productType": self.s.product_type,
                        "marginMode": self.s.margin_mode,
                        "marginCoin": self.s.margin_coin,
                        "size": self.fmt_perp_size(nbid_qty),
                        "price": self.fmt_perp_price(nbid_px),
                        "side": "buy",
                        "orderType": "limit",
                        "force": "post_only",
                        "clientOid": coid,
                    }
                    res = await self.rest.perp_place(data)
                    oid = None
                    try:
                        oid = res.get("data", {}).get("orderId")
                    except Exception:
                        oid = None

                    self.quote_bid = LiveOrder("buy", nbid_px, nbid_qty, coid, oid, now_ms())
                    self.log.write(
                        "order_new",
                        intent="quote",
                        source="strategy",
                        mode="QUOTING",
                        reason="set_quote",
                        leg="perp_bid",
                        cycle_id=cycle_id,
                        data=data,
                        res=res,
                        dry_run=self.s.dry_run,
                    )

            nask_px, nask_qty, ok = self.normalize_perp(ask_px, qty)
            if ok != "ok":
                self.log.write(
                    "order_skip",
                    intent="quote",
                    source="strategy",
                    mode="QUOTING",
                    reason=ok,
                    leg="perp_ask",
                    cycle_id=cycle_id,
                    ask_px=ask_px,
                    qty=qty,
                    funding=funding,
                    obi=obi,
                )
            else:
                assert nask_px is not None and nask_qty is not None
                if need_replace(self.quote_ask, nask_px, nask_qty):
                    if self.quote_ask:
                        await self.rest.perp_cancel(
                            {
                                "symbol": self.s.symbol,
                                "productType": self.s.product_type,
                                "marginCoin": self.s.margin_coin,
                                "orderId": self.quote_ask.order_id or "",
                                "clientOid": self.quote_ask.client_oid,
                            }
                        )
                    coid = self.mk_client_oid("Q", cycle_id, "ASK", "sell")
                    data = {
                        "symbol": self.s.symbol,
                        "productType": self.s.product_type,
                        "marginMode": self.s.margin_mode,
                        "marginCoin": self.s.margin_coin,
                        "size": self.fmt_perp_size(nask_qty),
                        "price": self.fmt_perp_price(nask_px),
                        "side": "sell",
                        "orderType": "limit",
                        "force": "post_only",
                        "clientOid": coid,
                    }
                    res = await self.rest.perp_place(data)
                    oid = None
                    try:
                        oid = res.get("data", {}).get("orderId")
                    except Exception:
                        oid = None

                    self.quote_ask = LiveOrder("sell", nask_px, nask_qty, coid, oid, now_ms())
                    self.log.write(
                        "order_new",
                        intent="quote",
                        source="strategy",
                        mode="QUOTING",
                        reason="set_quote",
                        leg="perp_ask",
                        cycle_id=cycle_id,
                        data=data,
                        res=res,
                        dry_run=self.s.dry_run,
                    )

    async def hedge_spot_ioc(
        self,
        cycle_id: int,
        side: Literal["buy", "sell"],
        qty_base: float,
        spot_bbo: BBO,
        reason: str,
        source: str = "hedge",
    ) -> None:
        """
        SPOT を limit IOC でヘッジする（base qty 指定）。
        """
        if spot_bbo.bid is None or spot_bbo.ask is None:
            self.log.write(
                "order_skip",
                intent="hedge",
                source=source,
                mode="HEDGING",
                reason="spot_bbo_missing",
                leg="spot_ioc",
                cycle_id=cycle_id,
                side=side,
                qty=qty_base,
            )
            return

        # ヘッジ時のみ 1 回だけ constraints を再取得
        if self.rest.spot_c is None:
            await self.rest.load_constraints()

        slip = self.s.hedge_ioc_slip_bps * 1e-4
        raw_px = (spot_bbo.ask * (1 + slip)) if side == "buy" else (spot_bbo.bid * (1 - slip))

        px, qty, ok = self.normalize_spot(raw_px, qty_base)
        if ok != "ok" or px is None or qty is None:
            self.log.write(
                "order_skip",
                intent="hedge",
                source=source,
                mode="HEDGING",
                reason=ok,
                leg="spot_ioc",
                cycle_id=cycle_id,
                side=side,
                raw_px=raw_px,
                qty=qty_base,
            )
            return

        coid = self.mk_client_oid("H", cycle_id, "SPOT", side)
        data = {
            "symbol": self.s.symbol,
            "side": side,
            "orderType": "limit",
            "force": "ioc",
            "price": self.fmt_spot_price(px),
            "size": self.fmt_spot_size(qty),
            "clientOid": coid,
        }

        if self.s.simulate_hedge_success:
            sim_ts = now_ms()
            sim_order_id = f"SIMH{sim_ts}{coid[:6]}"
            sim_trade_id = f"SIMH{sim_ts}{coid[-6:]}"
            sim_res = {"dry_run": True, "data": data, "simulated": True}
            self.log.write(
                "order_new",
                intent="hedge",
                source=source,
                mode="HEDGING",
                reason=reason,
                leg="spot_ioc",
                cycle_id=cycle_id,
                data=data,
                res=sim_res,
                dry_run=True,
                simulated=True,
                spot_order_id=sim_order_id,
            )
            self.log.write(
                "fill",
                intent="hedge",
                source=source,
                mode="HEDGING",
                reason="simulated",
                leg="spot_ioc",
                cycle_id=cycle_id,
                instType="SPOT",
                symbol=self.s.symbol,
                side=side,
                tradeId=sim_trade_id,
                orderId=sim_order_id,
                clientOid=coid,
                price=None,
                baseVolume=None,
                priceAvg=px,
                size=qty,
                ts_ms=sim_ts,
                simulated=True,
            )
            self.spot_pos += qty if side == "buy" else -qty
            return

        res = await self.rest.spot_place(data)

        order_id = None
        try:
            order_id = res.get("data", {}).get("orderId")
        except Exception:
            order_id = None

        if order_id:
            self.spot_client_to_order[coid] = str(order_id)
            self.spot_order_to_client[str(order_id)] = coid

        self.pending_hedge[coid] = now_ms() + int(self.s.max_unhedged_sec * 1000)

        self.log.write(
            "order_new",
            intent="hedge",
            source=source,
            mode="HEDGING",
            reason=reason,
            leg="spot_ioc",
            cycle_id=cycle_id,
            data=data,
            res=res,
            dry_run=self.s.dry_run,
            spot_order_id=order_id,
        )

    def unhedged_notional(self, mid: Optional[float]) -> float:
        if mid is None:
            return 0.0
        # デルタ ≒ spot_pos + perp_pos
        return abs(self.spot_pos + self.perp_pos) * mid

    def update_unhedged_timer(self) -> None:
        delta = self.spot_pos + self.perp_pos
        if abs(delta) <= 1e-9:
            self.unhedged_since_ms = None
        elif self.unhedged_since_ms is None:
            self.unhedged_since_ms = now_ms()

    def prune_pending_hedges(self, now_ts_ms: int) -> list[str]:
        expired = [coid for coid, deadline in self.pending_hedge.items() if deadline <= now_ts_ms]
        for coid in expired:
            self.pending_hedge.pop(coid, None)
        return expired


# -----------------------------
# 戦略（PERP 板 + 資金調達率バイアス）
# -----------------------------
class Strategy:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.funding: Optional[float] = None
        self.mode: str = "IDLE"
        self.cooldown_until_ms: int = 0

    def in_cooldown(self, now_ts_ms: int) -> bool:
        return now_ts_ms < self.cooldown_until_ms

    def set_cooldown(self, now_ts_ms: int) -> None:
        if self.s.cooldown_sec <= 0:
            return
        self.cooldown_until_ms = now_ts_ms + int(self.s.cooldown_sec * 1000)

    def compute_quotes(self, perp_bbo: BBO, obi: float, perp_pos: float) -> Optional[tuple[float, float]]:
        if perp_bbo.bid is None or perp_bbo.ask is None:
            return None
        mid = (perp_bbo.bid + perp_bbo.ask) / 2.0

        fr = self.funding
        if fr is None or abs(fr) < self.s.min_abs_funding:
            return None

        # 予約価格を OBI で微調整
        rp = mid * (1.0 + (self.s.obi_k_bps * 1e-4) * obi)

        base_h = self.s.base_half_spread_bps * 1e-4
        bias = max(0.0, min(1.0, self.s.funding_bias))

        # funding>0: PERP ショート優先 => 売り側をタイト化
        if fr > 0:
            ask_h = base_h * (1.0 - 0.7 * bias)
            bid_h = base_h * (1.0 + 0.7 * bias)
        else:
            bid_h = base_h * (1.0 - 0.7 * bias)
            ask_h = base_h * (1.0 + 0.7 * bias)

        bid_px = rp * (1.0 - bid_h)
        ask_px = rp * (1.0 + ask_h)

        # 交差回避
        bid_px = min(bid_px, perp_bbo.ask * (1.0 - 0.5e-4))
        ask_px = max(ask_px, perp_bbo.bid * (1.0 + 0.5e-4))
        return bid_px, ask_px


# -----------------------------
# WS 購読ヘルパー
# -----------------------------
def make_public_sub(s: Settings) -> dict:
    return {
        "op": "subscribe",
        "args": [
            {"instType": s.spot_inst, "channel": s.book_channel, "instId": s.symbol},
            {"instType": s.perp_inst, "channel": s.book_channel, "instId": s.symbol},
        ],
    }


def make_private_sub(s: Settings) -> dict:
    # 注文/約定/ポジション（チャンネル）
    return {
        "op": "subscribe",
        "args": [
            {"instType": s.spot_inst, "channel": "orders", "instId": "default"},
            {"instType": s.spot_inst, "channel": "fill", "instId": "default"},
            {"instType": s.perp_inst, "channel": "orders", "instId": "default"},
            {"instType": s.perp_inst, "channel": "fill", "instId": "default"},
            {"instType": s.perp_inst, "channel": "positions", "instId": "default"},
        ],
    }


# -----------------------------
# メイン
# -----------------------------
async def main_async() -> None:
    s = Settings()
    log = JsonlLogger(s.log_path)

    key = os.getenv("BITGET_API_KEY", "")
    sec = os.getenv("BITGET_API_SECRET", "")
    pas = os.getenv("BITGET_API_PASSPHRASE", "")
    if not (key and sec and pas):
        raise SystemExit("Set BITGET_API_KEY / BITGET_API_SECRET / BITGET_API_PASSPHRASE")

    apis = {"bitget": [key, sec, pas]}
    store = pybotters.BitgetV2DataStore()

    strat = Strategy(s)

    async with pybotters.Client(apis=apis) as client:
        rest = BitgetRest(client, s, log)
        await rest.load_constraints()

        cur = await rest.get_pos_mode()
        log.write(
            "pos_mode",
            intent="system",
            source="startup",
            mode="INIT",
            reason="check",
            leg="perp",
            current=cur,
            target=s.target_pos_mode,
        )

        if (not s.dry_run) and s.auto_set_pos_mode and cur and (cur != s.target_pos_mode):
            res = await rest.set_position_mode(s.target_pos_mode)
            log.write(
                "pos_mode_set",
                intent="system",
                source="startup",
                mode="INIT",
                reason="set",
                leg="perp",
                target=s.target_pos_mode,
                res=res,
            )

            cur2 = await rest.get_pos_mode()
            log.write(
                "pos_mode",
                intent="system",
                source="startup",
                mode="INIT",
                reason="recheck",
                leg="perp",
                current=cur2,
                target=s.target_pos_mode,
            )

            if cur2 != s.target_pos_mode:
                raise SystemExit(
                    f"posMode mismatch: current={cur2} target={s.target_pos_mode}. "
                    f"Close all futures positions/orders for productType={s.product_type} and retry."
                )

        oms = OMS(s, rest, log)

        public_sub = make_public_sub(s)
        private_sub = make_private_sub(s)

        ws_pub = await client.ws_connect(
            WS_PUBLIC,
            send_str="ping",
            send_json=public_sub,
            hdlr_json=store.onmessage,
            auth=None,
        )
        ws_prv = await client.ws_connect(
            WS_PRIVATE,
            send_str="ping",
            send_json=private_sub,
            hdlr_json=store.onmessage,
        )

        log.write("start", symbol=s.symbol, dry_run=s.dry_run, book_channel=s.book_channel)

        # 資金調達率ポーリング
        async def funding_loop() -> None:
            while True:
                fr = await rest.get_funding_rate()
                strat.funding = fr
                log.write("funding", symbol=s.symbol, funding_rate=fr)
                await asyncio.sleep(30)

        async def handle_fill(inst_norm: str, e: dict, simulated: bool = False) -> None:
            # Spot: orderId から clientOid を復元
            if inst_norm == "SPOT" and not e["clientOid"]:
                oid = e["orderId"]
                if oid and oid in oms.spot_order_to_client:
                    e["clientOid"] = oms.spot_order_to_client[oid]

            log.write(
                "fill",
                instType=e["instType"],
                symbol=e["symbol"],
                side=e["side"],
                tradeId=e["tradeId"],
                orderId=e["orderId"],
                clientOid=e["clientOid"],
                price=e["price"],
                baseVolume=e["baseVolume"],
                priceAvg=e["priceAvg"],
                size=e["size"],
                ts_ms=e["ts_ms"],
                simulated=simulated,
            )

            qty_raw = e["baseVolume"] if inst_norm == "USDT-FUTURES" else e["size"]
            if qty_raw is None:
                return
            qty = float(qty_raw)
            side = e["side"]

            # 影ポジション更新
            if inst_norm == "USDT-FUTURES":
                oms.perp_pos += qty if side == "buy" else -qty
            else:
                oms.spot_pos += qty if side == "buy" else -qty
                coid = str(e.get("clientOid") or "")
                if coid in oms.pending_hedge:
                    oms.pending_hedge.pop(coid, None)

            oms.update_unhedged_timer()

            # PERP のクオート約定なら SPOT ヘッジ
            coid = str(e.get("clientOid") or "")
            if inst_norm == "USDT-FUTURES" and coid.startswith("Q") and qty > 0:
                spot_book = book_sorted(store, s.spot_inst, s.symbol, limit=1)
                spot_bbo, _ = bbo_obi_from_sorted(spot_book, 1)
                hedge_side: Literal["buy", "sell"] = "buy" if side == "sell" else "sell"
                cycle_id = int(now_ms() & 0xFFFFFFFF)

                await oms.hedge_spot_ioc(
                    cycle_id=cycle_id,
                    side=hedge_side,
                    qty_base=qty,
                    spot_bbo=spot_bbo,
                    reason=f"perp_fill:{side}",
                    source="hedge",
                )

        # 約定: 影ポジション更新 + PERP 約定で SPOT ヘッジ
        async def fill_loop() -> None:
            seen: set[str] = set()
            with store.fill.watch() as stream:
                async for msg in stream:
                    rows = getattr(msg, "data", None)
                    if rows is None:
                        continue
                    if isinstance(rows, dict):
                        rows = [rows]
                    if not isinstance(rows, list):
                        continue

                    for d in rows:
                        if not isinstance(d, dict):
                            continue

                        inst_type = str(pick(d, ["instType"], "")).strip()
                        if not inst_type:
                            inst_type = s.perp_inst if "productType" in d else ""

                        if inst_type not in (s.spot_inst, s.perp_inst, "SPOT", "USDT-FUTURES"):
                            continue
                        inst_norm = (
                            "SPOT"
                            if inst_type == s.spot_inst or inst_type == "SPOT"
                            else "USDT-FUTURES"
                        )

                        e = parse_fill(inst_norm, d)
                        if not e:
                            continue

                        did = e["dedupe_id"]
                        if did in seen:
                            continue
                        seen.add(did)

                        await handle_fill(inst_norm, e)

        async def simulate_fill_loop() -> None:
            if not s.simulate_fills:
                return
            side_toggle: Literal["buy", "sell"] = "buy"
            while True:
                await asyncio.sleep(s.simulate_fill_interval_sec)

                if s.simulate_fill_side == "buy":
                    side: Literal["buy", "sell"] = "buy"
                elif s.simulate_fill_side == "sell":
                    side = "sell"
                else:
                    side = side_toggle

                quote = oms.quote_bid if side == "buy" else oms.quote_ask
                raw_qty = s.simulate_fill_qty
                if quote:
                    price = quote.px
                    raw_qty = min(raw_qty, quote.qty)
                    client_oid = quote.client_oid
                else:
                    perp_book = book_sorted(store, s.perp_inst, s.symbol, limit=1)
                    perp_bbo, _ = bbo_obi_from_sorted(perp_book, 1)
                    if perp_bbo.bid is None or perp_bbo.ask is None:
                        continue
                    price = perp_bbo.bid if side == "buy" else perp_bbo.ask
                    cycle_id = int(now_ms() & 0xFFFFFFFF)
                    client_oid = oms.mk_client_oid("Q", cycle_id, "SIM", side)

                px2, qty2, ok = oms.normalize_perp(price, raw_qty)
                if ok != "ok" or px2 is None or qty2 is None:
                    continue

                ts_ms = now_ms()
                trade_id = f"SIM{ts_ms}{side[:1].upper()}"
                order_id = f"SIM{ts_ms}{client_oid[:6]}"

                e = {
                    "instType": "USDT-FUTURES",
                    "symbol": s.symbol,
                    "side": side,
                    "tradeId": trade_id,
                    "orderId": order_id,
                    "clientOid": client_oid,
                    "price": px2,
                    "baseVolume": qty2,
                    "priceAvg": None,
                    "size": None,
                    "ts_ms": ts_ms,
                }
                await handle_fill("USDT-FUTURES", e, simulated=True)

                if s.simulate_fill_side == "both":
                    side_toggle = "sell" if side_toggle == "buy" else "buy"

        # 戦略ループ: クオート + リスク確認
        async def strategy_loop() -> None:
            while True:
                await asyncio.sleep(s.refresh_ms / 1000.0)
                now_ts_ms = now_ms()

                if strat.in_cooldown(now_ts_ms):
                    strat.mode = "COOLDOWN"
                    await oms.cancel_quotes(reason="cooldown", source="risk")
                    log.write("state", mode=strat.mode, reason="cooldown")
                    continue

                # 板スナップショット（books5）
                perp_book = book_sorted(store, s.perp_inst, s.symbol, limit=s.obi_levels)
                spot_book = book_sorted(store, s.spot_inst, s.symbol, limit=1)

                perp_bbo, obi = bbo_obi_from_sorted(perp_book, s.obi_levels)
                spot_bbo, _ = bbo_obi_from_sorted(spot_book, 1)

                if s.stale_sec > 0:
                    stale_ms = int(s.stale_sec * 1000)
                    if (now_ts_ms - perp_bbo.ts_ms) > stale_ms or (now_ts_ms - spot_bbo.ts_ms) > stale_ms:
                        strat.mode = "IDLE"
                        await oms.cancel_quotes(reason="stale_book", source="risk")
                        log.write(
                            "state",
                            mode=strat.mode,
                            reason="stale_book",
                            perp_ts_ms=perp_bbo.ts_ms,
                            spot_ts_ms=spot_bbo.ts_ms,
                        )
                        continue

                fr = strat.funding
                if fr is None or abs(fr) < s.min_abs_funding:
                    strat.mode = "IDLE"
                    await oms.cancel_quotes(reason="funding_too_small", source="strategy")
                    log.write("state", mode=strat.mode, reason="funding_too_small", funding=fr, obi=obi)
                    continue

                quotes = strat.compute_quotes(perp_bbo, obi, oms.perp_pos)
                if quotes is None:
                    strat.mode = "IDLE"
                    await oms.cancel_quotes(reason="no_quotes", source="strategy")
                    log.write("state", mode=strat.mode, reason="no_quotes", funding=fr, obi=obi)
                    continue

                strat.mode = "QUOTING"
                bid_px, ask_px = quotes

                mid = None
                if spot_bbo.bid is not None and spot_bbo.ask is not None:
                    mid = (spot_bbo.bid + spot_bbo.ask) / 2.0

                unhedged = oms.unhedged_notional(mid)
                if unhedged > s.max_unhedged_notional:
                    await oms.cancel_quotes(reason="unhedged_breach", source="risk")
                    log.write(
                        "risk",
                        kind="unhedged_breach",
                        unhedged_notional=unhedged,
                        max=s.max_unhedged_notional,
                    )
                    strat.set_cooldown(now_ts_ms)
                    continue

                expired = oms.prune_pending_hedges(now_ts_ms)
                if expired:
                    await oms.cancel_quotes(reason="hedge_timeout", source="risk")
                    log.write(
                        "risk",
                        kind="hedge_timeout",
                        expired=len(expired),
                        unhedged_notional=unhedged,
                        max_sec=s.max_unhedged_sec,
                    )
                    strat.set_cooldown(now_ts_ms)
                    continue

                if (
                    oms.unhedged_since_ms is not None
                    and (now_ts_ms - oms.unhedged_since_ms) > (s.max_unhedged_sec * 1000)
                ):
                    await oms.cancel_quotes(reason="unhedged_timeout", source="risk")
                    log.write(
                        "risk",
                        kind="unhedged_timeout",
                        unhedged_notional=unhedged,
                        unhedged_ms=(now_ts_ms - oms.unhedged_since_ms),
                        max_sec=s.max_unhedged_sec,
                    )
                    strat.set_cooldown(now_ts_ms)
                    continue

                if s.max_position_notional > 0 and mid is not None:
                    spot_notional = abs(oms.spot_pos) * mid
                    perp_notional = abs(oms.perp_pos) * mid
                    if spot_notional > s.max_position_notional or perp_notional > s.max_position_notional:
                        await oms.cancel_quotes(reason="max_position", source="risk")
                        log.write(
                            "risk",
                            kind="max_position",
                            spot_notional=spot_notional,
                            perp_notional=perp_notional,
                            max=s.max_position_notional,
                        )
                        strat.set_cooldown(now_ts_ms)
                        continue

                cycle_id = int(now_ms() & 0xFFFFFFFF)
                await oms.set_quotes(cycle_id, bid_px, ask_px, s.quote_size, funding=fr, obi=obi)
                log.write(
                    "tick",
                    mode=strat.mode,
                    funding=fr,
                    obi=obi,
                    perp_bbo=perp_bbo.__dict__,
                    spot_bbo=spot_bbo.__dict__,
                    perp_pos=oms.perp_pos,
                    spot_pos=oms.spot_pos,
                    unhedged_notional=unhedged,
                    cycle_id=cycle_id,
                )

        tasks = [
            asyncio.create_task(ws_pub.wait()),
            asyncio.create_task(ws_prv.wait()),
            asyncio.create_task(funding_loop()),
            asyncio.create_task(fill_loop()),
            asyncio.create_task(strategy_loop()),
        ]
        if s.simulate_fills:
            tasks.append(asyncio.create_task(simulate_fill_loop()))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            # Ctrl+C 等の終了で CancelledError を抑制
            log.write(
                "shutdown",
                intent="system",
                source="signal",
                mode="STOPPING",
                reason="cancelled",
            )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    from ..app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
