from __future__ import annotations

import asyncio
import time
from typing import Optional

from ..log.jsonl import JsonlLogger
from ..types import FundingInfo


class FundingCache:
    def __init__(self, gateway, poll_sec: float = 60.0, logger: Optional[JsonlLogger] = None):
        self._gateway = gateway
        self._poll_sec = poll_sec
        self._last: Optional[FundingInfo] = None
        self._logger = logger

    @property
    def last(self) -> Optional[FundingInfo]:
        return self._last

    async def run(self) -> None:
        while True:
            try:
                await self.update_once()
            except Exception as exc:
                self._log("funding_error", error=repr(exc))
            await asyncio.sleep(self._poll_sec)

    async def update_once(self) -> None:
        data = await self._gateway.fetch_funding()
        info = _parse_funding(data)
        if info is None:
            self._log("funding_parse_error", payload=_summarize_payload(data))
            return
        self._last = info

    def _log(self, event: str, **fields) -> None:
        if not self._logger:
            return
        self._logger.log({"event": event, **fields})


def _parse_funding(payload: dict) -> Optional[FundingInfo]:
    data = payload.get("data")
    if isinstance(data, list):
        if not data:
            return None
        row = data[0]
    elif isinstance(data, dict):
        row = data
    else:
        return None

    rate = _first_float(row, ["fundingRate", "funding_rate", "rate"])
    if rate is None:
        return None
    next_ts = _first_time(row, ["nextUpdateTime", "nextSettleTime", "fundingTime"])
    interval = _first_float(row, ["fundingInterval", "intervalSec", "interval"])
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


def _summarize_payload(payload: dict) -> dict:
    data = payload.get("data")
    data_len = len(data) if isinstance(data, list) else None
    return {
        "code": payload.get("code"),
        "data_type": type(data).__name__,
        "data_len": data_len,
    }
