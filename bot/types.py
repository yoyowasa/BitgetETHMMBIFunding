from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class InstType(str, Enum):
    SPOT = "SPOT"
    USDT_FUTURES = "USDT-FUTURES"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class Force(str, Enum):
    GTC = "gtc"
    POST_ONLY = "post_only"
    IOC = "ioc"
    FOK = "fok"


class OrderIntent(str, Enum):
    QUOTE_BID = "QUOTE_BID"
    QUOTE_ASK = "QUOTE_ASK"
    HEDGE = "HEDGE"
    FLATTEN = "FLATTEN"


@dataclass
class BBO:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    ts: float


@dataclass
class BookSnapshot:
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    ts: float


@dataclass
class FundingInfo:
    funding_rate: float
    next_update_time: Optional[float]
    interval_sec: Optional[float]
    ts: float


@dataclass
class OrderRequest:
    inst_type: InstType
    symbol: str
    side: Side
    order_type: OrderType
    size: float
    force: Force
    client_oid: str
    intent: OrderIntent
    cycle_id: int
    price: Optional[float] = None
    reduce_only: Optional[bool] = None


@dataclass
class ExecutionEvent:
    inst_type: InstType
    symbol: str
    order_id: str
    client_oid: str
    fill_id: str
    side: Side
    price: float
    size: float
    fee: float
    ts: float
