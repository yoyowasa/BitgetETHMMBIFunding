from __future__ import annotations

import pytest

from bot.config import ExchangeConfig, load_apis


def _empty_exchange() -> ExchangeConfig:
    return ExchangeConfig(
        name="bitget",
        base_url="https://api.bitget.com",
        ws_public="wss://ws.bitget.com/v2/ws/public",
        ws_private="wss://ws.bitget.com/v2/ws/private",
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

