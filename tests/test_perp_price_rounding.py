from __future__ import annotations

import asyncio
from decimal import Decimal

from bot.config import (
    AppConfig,
    CostConfig,
    ExchangeConfig,
    HedgeConfig,
    RiskConfig,
    StrategyConfig,
    SymbolConfig,
    SymbolsConfig,
)
from bot.exchange.constraints import (
    InstrumentConstraints,
    format_price_for_bitget,
    format_size_for_bitget,
    get_price_tick,
    quantize_perp_price,
    quantize_size_floor,
)
from bot.exchange.bitget_gateway import BitgetGateway
from bot.types import Force, InstType, OrderIntent, OrderRequest, OrderType, Side


def _constraints(
    tick_size: float,
    price_place: int | None = None,
    qty_step: float = 0.001,
) -> InstrumentConstraints:
    return InstrumentConstraints(
        min_qty=0.001,
        qty_step=qty_step,
        min_notional=0.0,
        tick_size=tick_size,
        price_place=price_place,
    )


def test_get_price_tick_prefers_price_place() -> None:
    constraints = _constraints(0.0, price_place=2)

    assert get_price_tick(constraints) == Decimal("0.01")


def test_quantize_perp_price_buy_rounds_down_and_sell_rounds_up() -> None:
    cases = [
        (_constraints(0.01, price_place=2), "3000.105", Decimal("3000.10"), Decimal("3000.11")),
        (_constraints(0.1, price_place=1), "3000.15", Decimal("3000.1"), Decimal("3000.2")),
        (_constraints(0.001, price_place=3), "3000.1005", Decimal("3000.100"), Decimal("3000.101")),
    ]

    for constraints, price, expected_buy, expected_sell in cases:
        assert quantize_perp_price(price, Side.BUY, constraints) == expected_buy
        assert quantize_perp_price(price, Side.SELL, constraints) == expected_sell


def test_format_price_for_bitget_avoids_float_artifacts() -> None:
    rounded = quantize_perp_price("3000.1000000001", Side.BUY, _constraints(0.01, price_place=2))

    assert format_price_for_bitget(rounded) == "3000.1"


def test_quantize_size_floor_avoids_float_artifacts() -> None:
    rounded = quantize_size_floor(
        "0.039900000000000005",
        _constraints(0.01, price_place=2, qty_step=0.0001),
    )

    assert rounded == Decimal("0.0399")
    assert format_size_for_bitget(rounded) == "0.0399"


def test_quantize_size_floor_uses_qty_step() -> None:
    rounded = quantize_size_floor(
        "0.039900000000000005",
        _constraints(0.01, price_place=2, qty_step=0.001),
    )

    assert format_size_for_bitget(rounded) == "0.039"


def _config() -> AppConfig:
    return AppConfig(
        exchange=ExchangeConfig(name="bitget", base_url="", ws_public="", ws_private=""),
        symbols=SymbolsConfig(
            spot=SymbolConfig(instType="SPOT", symbol="ETHUSDT"),
            perp=SymbolConfig(
                instType="USDT-FUTURES",
                symbol="ETHUSDT",
                productType="USDT-FUTURES",
                marginMode="isolated",
                marginCoin="USDT",
            ),
        ),
        risk=RiskConfig(
            stale_sec=2.0,
            max_unhedged_sec=2.0,
            max_unhedged_notional=20.0,
            max_position_notional=200.0,
            cooldown_sec=30.0,
        ),
        strategy=StrategyConfig(
            enable_only_positive_funding=True,
            min_funding_rate=0.0,
            target_notional=50.0,
            delta_tolerance=0.01,
            obi_levels=5,
            alpha_obi_bps=1.0,
            gamma_inventory_bps=2.0,
            base_half_spread_bps=14.0,
            quote_refresh_ms=250,
        ),
        hedge=HedgeConfig(use_spot_limit_ioc=True, hedge_aggressive_bps=5.0),
        cost=CostConfig(fee_maker_perp_bps=1.4, fee_taker_spot_bps=10.0, slippage_bps=2.0),
    )


def test_spot_place_order_payload_quantizes_size_and_price(monkeypatch) -> None:
    gateway = BitgetGateway(client=None, store=None, config=_config())
    gateway.constraints.spot = _constraints(0.01, price_place=2, qty_step=0.0001)
    captured = {}

    async def fake_rest_post(path: str, data: dict) -> dict:
        captured["path"] = path
        captured["data"] = data
        return {"code": "00000", "data": {"orderId": "order-1"}}

    monkeypatch.setattr(gateway, "rest_post", fake_rest_post)

    asyncio.run(
        gateway.place_order(
            OrderRequest(
                inst_type=InstType.SPOT,
                symbol="ETHUSDT",
                side=Side.SELL,
                order_type=OrderType.LIMIT,
                size=0.039900000000000005,
                force=Force.IOC,
                client_oid="cid-1",
                intent=OrderIntent.FLATTEN,
                cycle_id=1,
                price=2129.4799999999996,
            )
        )
    )

    assert captured["path"] == "/api/v2/spot/trade/place-order"
    assert captured["data"]["size"] == "0.0399"
    assert captured["data"]["price"] == "2129.47"
