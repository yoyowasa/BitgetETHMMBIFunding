from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional

import yaml


@dataclass
class ExchangeConfig:
    name: str
    base_url: str
    ws_public: str
    ws_private: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    api_passphrase: Optional[str] = None


@dataclass
class SymbolConfig:
    instType: str
    symbol: str
    productType: Optional[str] = None
    marginMode: Optional[str] = None
    marginCoin: Optional[str] = None


@dataclass
class SymbolsConfig:
    spot: SymbolConfig
    perp: SymbolConfig


@dataclass
class RiskConfig:
    stale_sec: float
    max_unhedged_sec: float
    max_unhedged_notional: float
    max_position_notional: float
    cooldown_sec: float
    funding_stale_sec: float = 120.0
    reject_streak_limit: int = 3
    book_boot_timeout_sec: float | None = None
    book_stale_sec: float | None = None
    controlled_reconnect_grace_sec: float = 3.0


@dataclass
class StrategyConfig:
    enable_only_positive_funding: bool
    min_funding_rate: float
    target_notional: float
    delta_tolerance: float
    obi_levels: int
    alpha_obi_bps: float
    gamma_inventory_bps: float
    base_half_spread_bps: float
    quote_refresh_ms: int
    dry_run: bool = False


@dataclass
class HedgeConfig:
    use_spot_limit_ioc: bool
    hedge_aggressive_bps: float
    hedge_deadline_sec: float = 1.5
    hedge_max_tries: int = 2
    hedge_chase_slip_bps: float = 5.0
    unwind_enable: bool = True


@dataclass
class CostConfig:
    fee_maker_perp_bps: float
    fee_taker_spot_bps: float
    slippage_bps: float


@dataclass
class AppConfig:
    exchange: ExchangeConfig
    symbols: SymbolsConfig
    risk: RiskConfig
    strategy: StrategyConfig
    hedge: HedgeConfig
    cost: CostConfig


def _require(raw: dict, key: str):
    if key not in raw:
        raise KeyError(f"missing config key: {key}")
    return raw[key]


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    exchange_raw = _require(raw, "exchange")
    symbols_raw = _require(raw, "symbols")
    risk_raw = _require(raw, "risk")
    strategy_raw = _require(raw, "strategy")
    hedge_raw = _require(raw, "hedge")
    cost_raw = _require(raw, "cost")

    exchange = ExchangeConfig(
        name=exchange_raw["name"],
        base_url=exchange_raw["base_url"],
        ws_public=exchange_raw["ws_public"],
        ws_private=exchange_raw["ws_private"],
        api_key=exchange_raw.get("api_key"),
        api_secret=exchange_raw.get("api_secret"),
        api_passphrase=exchange_raw.get("api_passphrase"),
    )

    symbols = SymbolsConfig(
        spot=SymbolConfig(**symbols_raw["spot"]),
        perp=SymbolConfig(**symbols_raw["perp"]),
    )

    risk = RiskConfig(**risk_raw)
    strategy = StrategyConfig(**strategy_raw)
    hedge = HedgeConfig(**hedge_raw)
    cost = CostConfig(**cost_raw)

    return AppConfig(
        exchange=exchange,
        symbols=symbols,
        risk=risk,
        strategy=strategy,
        hedge=hedge,
        cost=cost,
    )


def load_apis(exchange: ExchangeConfig) -> dict:
    key = exchange.api_key or os.getenv("BITGET_API_KEY")
    secret = exchange.api_secret or os.getenv("BITGET_API_SECRET")
    passphrase = exchange.api_passphrase or os.getenv("BITGET_API_PASSPHRASE")

    if not key or not secret or not passphrase:
        raise ValueError("missing Bitget API credentials")

    return {
        # pybotters は [API_KEY, API_SECRET, API_PASSPHRASE] の3要素リストを期待する
        "bitget": [key, secret, passphrase]
    }


def apply_env_overrides(config: AppConfig) -> None:
    symbol = os.getenv("SYMBOL")
    if symbol:
        config.symbols.spot.symbol = symbol
        config.symbols.perp.symbol = symbol

    product_type = os.getenv("PRODUCT_TYPE")
    if product_type:
        config.symbols.perp.productType = product_type

    margin_mode = os.getenv("MARGIN_MODE")
    if margin_mode:
        config.symbols.perp.marginMode = margin_mode

    margin_coin = os.getenv("MARGIN_COIN")
    if margin_coin:
        config.symbols.perp.marginCoin = margin_coin
