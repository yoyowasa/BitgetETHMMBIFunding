from __future__ import annotations

import pytest

from bot.config import (
    AppConfig,
    CostConfig,
    ExchangeConfig,
    HedgeConfig,
    RiskConfig,
    StrategyConfig,
    SymbolConfig,
    SymbolsConfig,
    apply_env_overrides,
    load_apis,
)


def _empty_exchange() -> ExchangeConfig:
    return ExchangeConfig(
        name="bitget",
        base_url="https://api.bitget.com",
        ws_public="wss://ws.bitget.com/v2/ws/public",
        ws_private="wss://ws.bitget.com/v2/ws/private",
    )


def _app_config() -> AppConfig:
    return AppConfig(
        exchange=_empty_exchange(),
        symbols=SymbolsConfig(
            spot=SymbolConfig(instType="SPOT", symbol="ETHUSDT"),
            perp=SymbolConfig(
                instType="USDT-FUTURES",
                symbol="ETHUSDT",
                productType="USDT-FUTURES",
                marginMode="crossed",
                marginCoin="USDT",
            ),
        ),
        risk=RiskConfig(
            stale_sec=1.0,
            max_unhedged_sec=3.0,
            max_unhedged_notional=10.0,
            max_position_notional=100.0,
            cooldown_sec=1.0,
        ),
        strategy=StrategyConfig(
            enable_only_positive_funding=True,
            min_funding_rate=0.0,
            target_notional=50.0,
            delta_tolerance=0.01,
            obi_levels=5,
            alpha_obi_bps=0.0,
            gamma_inventory_bps=0.0,
            base_half_spread_bps=14.0,
            quote_refresh_ms=500,
            min_half_spread_bps=14.0,
        ),
        hedge=HedgeConfig(
            use_spot_limit_ioc=True,
            hedge_aggressive_bps=5.0,
            hedge_deadline_sec=1.5,
        ),
        cost=CostConfig(
            fee_maker_perp_bps=2.0,
            fee_taker_spot_bps=8.0,
            slippage_bps=1.0,
        ),
    )


def test_load_apis_returns_pybotters_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BITGET_API_KEY", "k")
    monkeypatch.setenv("BITGET_API_SECRET", "s")
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "p")

    apis = load_apis(_empty_exchange())

    assert "bitget" in apis
    assert apis["bitget"] == ["k", "s", "p"]


def test_load_apis_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BITGET_API_KEY", raising=False)
    monkeypatch.delenv("BITGET_API_SECRET", raising=False)
    monkeypatch.delenv("BITGET_API_PASSPHRASE", raising=False)

    with pytest.raises(ValueError, match="missing Bitget API credentials"):
        load_apis(_empty_exchange())


def test_apply_env_overrides_runtime_strategy_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _app_config()
    monkeypatch.setenv("SYMBOL", "WLDUSDT")
    monkeypatch.setenv("TARGET_NOTIONAL", "25")
    monkeypatch.setenv("BASE_HALF_SPREAD_BPS", "22.5")
    monkeypatch.setenv("MIN_HALF_SPREAD_BPS", "21")
    monkeypatch.setenv("TFI_FADE_POLICY", "threshold_0p7")
    monkeypatch.setenv("TFI_FADE_THRESHOLD", "0.55")
    monkeypatch.setenv("QUOTE_REFRESH_MS", "800")
    monkeypatch.setenv("HEDGE_AGGRESSIVE_BPS", "7.5")
    monkeypatch.setenv("HEDGE_DEADLINE_SEC", "2.25")

    apply_env_overrides(config)

    assert config.symbols.spot.symbol == "WLDUSDT"
    assert config.symbols.perp.symbol == "WLDUSDT"
    assert config.strategy.target_notional == 25.0
    assert config.strategy.base_half_spread_bps == 22.5
    assert config.strategy.min_half_spread_bps == 21.0
    assert config.strategy.tfi_fade_policy == "threshold_0p7"
    assert config.strategy.tfi_fade_threshold == 0.55
    assert config.strategy.quote_refresh_ms == 800
    assert config.hedge.hedge_aggressive_bps == 7.5
    assert config.hedge.hedge_deadline_sec == 2.25

