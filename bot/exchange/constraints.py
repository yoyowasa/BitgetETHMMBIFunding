from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
from typing import Optional

from ..types import InstType, Side


@dataclass
class InstrumentConstraints:
    min_qty: float
    qty_step: float
    min_notional: float
    tick_size: float
    price_place: Optional[int] = None

    def is_ready(self) -> bool:
        return self.min_qty > 0 and self.qty_step > 0 and self.tick_size > 0

    def adjust_qty(self, qty: float) -> float:
        if self.qty_step <= 0:
            return qty
        return math.floor(qty / self.qty_step) * self.qty_step

    def adjust_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        return math.floor(price / self.tick_size) * self.tick_size

    def validate(self, price: float, qty: float) -> bool:
        if qty < self.min_qty:
            return False
        if self.min_notional > 0 and price * qty < self.min_notional:
            return False
        return True


def get_price_tick(constraints: InstrumentConstraints) -> Decimal:
    # 役割: constraints から PERP の price tick を取得する関数
    price_place = getattr(constraints, "price_place", None)
    if price_place is not None:
        return Decimal(1).scaleb(-price_place)
    return Decimal(str(getattr(constraints, "tick_size", 0.0)))


def quantize_perp_price(
    price: float | Decimal | str,
    side: Side,
    constraints: InstrumentConstraints,
) -> Decimal:
    # 役割: PERP 注文価格を Bitget の tick multiple に合わせる関数
    tick = get_price_tick(constraints)
    if tick <= 0:
        return Decimal(str(price))
    raw = Decimal(str(price))
    rounding = ROUND_FLOOR if side == Side.BUY else ROUND_CEILING
    units = (raw / tick).to_integral_value(rounding=rounding)
    return units * tick


def format_price_for_bitget(price: Decimal) -> str:
    # 役割: Decimal を Bitget REST payload 用の文字列に変換する関数
    return format(price.normalize(), "f")


@dataclass
class ConstraintsRegistry:
    spot: Optional[InstrumentConstraints] = None
    perp: Optional[InstrumentConstraints] = None

    def ready(self) -> bool:
        return (
            self.spot is not None
            and self.perp is not None
            and self.spot.is_ready()
            and self.perp.is_ready()
        )

    def get(self, inst_type: InstType) -> Optional[InstrumentConstraints]:
        if inst_type == InstType.SPOT:
            return self.spot
        if inst_type == InstType.USDT_FUTURES:
            return self.perp
        return None
