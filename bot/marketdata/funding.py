from __future__ import annotations

import asyncio
import time
from typing import Optional

from ..types import FundingInfo


class FundingCache:
    def __init__(self, gateway, poll_sec: float = 60.0):
        self._gateway = gateway
        self._poll_sec = poll_sec
        self._last: Optional[FundingInfo] = None

    @property
    def last(self) -> Optional[FundingInfo]:
        return self._last

    async def run(self) -> None:
        while True:
            try:
                await self.update_once()
            except Exception:
                pass
            await asyncio.sleep(self._poll_sec)

    async def update_once(self) -> None:
        data = await self._gateway.fetch_funding()
        self._last = _parse_funding(data)


def _parse_funding(payload: dict) -> FundingInfo:
    data = payload.get("data") or {}
    rate = _first_float(data, ["fundingRate", "funding_rate", "rate"]) or 0.0
    next_ts = _first_time(data, ["nextUpdateTime", "nextSettleTime", "fundingTime"])
    interval = _first_float(data, ["fundingInterval", "intervalSec", "interval"])
    return FundingInfo(
        funding_rate=rate,
        next_update_time=next_ts,
        interval_sec=interval,
        ts=time.time(),
    )


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
