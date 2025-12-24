from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional

from ..types import InstType


@dataclass
class InstrumentConstraints:
    min_qty: float
    qty_step: float
    min_notional: float
    tick_size: float

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
