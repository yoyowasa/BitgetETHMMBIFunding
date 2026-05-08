from __future__ import annotations

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
from bot.oms.oms import OMS
from bot.types import InstType


class CapturingLogger:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def log(self, record: dict) -> None:
        self.records.append(record)


class DummyGateway:
    pass


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
            max_unhedged_notional=200.0,
            max_position_notional=2000.0,
            cooldown_sec=30.0,
            funding_stale_sec=120.0,
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
            min_half_spread_bps=14.0,
            quote_refresh_ms=250,
            dry_run=True,
        ),
        hedge=HedgeConfig(use_spot_limit_ioc=True, hedge_aggressive_bps=5.0),
        cost=CostConfig(fee_maker_perp_bps=1.4, fee_taker_spot_bps=10.0, slippage_bps=2.0),
    )


def _oms() -> tuple[OMS, CapturingLogger]:
    orders_logger = CapturingLogger()
    fills_logger = CapturingLogger()
    return (
        OMS(
            DummyGateway(),
            _config(),
            risk=None,
            orders_logger=orders_logger,
            fills_logger=fills_logger,
        ),
        orders_logger,
    )


def test_parse_futures_fill_uses_base_volume() -> None:
    oms, logger = _oms()
    event = oms._parse_fill(
        {
            "instType": "USDT-FUTURES",
            "symbol": "ETHUSDT",
            "side": "buy",
            "orderId": "o1",
            "clientOid": "QUOTE_BID-1",
            "tradeId": "t1",
            "price": "2357.68",
            "baseVolume": "0.02",
            "fee": "0.001",
            "ts": "1777881904190",
        }
    )

    assert event is not None
    assert event.inst_type == InstType.USDT_FUTURES
    assert event.size == 0.02
    assert event.fee == 0.001
    assert logger.records == []


def test_parse_futures_fill_keeps_existing_size_fields() -> None:
    for key in ("size", "fillSz", "tradeQty", "tradeSize"):
        oms, _ = _oms()
        event = oms._parse_fill(
            {
                "instType": "USDT-FUTURES",
                "symbol": "ETHUSDT",
                "side": "sell",
                "orderId": f"o-{key}",
                "tradeId": f"t-{key}",
                "price": "2374.73",
                key: "0.03",
            }
        )

        assert event is not None
        assert event.size == 0.03


def test_parse_fill_rejects_zero_size_with_warning() -> None:
    oms, logger = _oms()
    event = oms._parse_fill(
        {
            "instType": "USDT-FUTURES",
            "symbol": "ETHUSDT",
            "side": "buy",
            "orderId": "o-zero",
            "tradeId": "t-zero",
            "price": "2357.68",
            "baseVolume": "0",
        }
    )

    assert event is None
    assert logger.records[-1]["reason"] == "fill_parse_warning"
    assert logger.records[-1]["parse_reason"] == "fill_size_missing_or_zero"
    assert logger.records[-1]["size"] == 0.0


def test_parse_spot_fill_uses_price_avg() -> None:
    oms, logger = _oms()
    event = oms._parse_fill(
        {
            "instType": "SPOT",
            "symbol": "ETHUSDT",
            "side": "buy",
            "orderId": "spot-price-avg",
            "clientOid": "HEDGE-1",
            "tradeId": "spot-trade-avg",
            "priceAvg": "2315.42",
            "size": "0.02",
        }
    )

    assert event is not None
    assert event.inst_type == InstType.SPOT
    assert event.price == 2315.42
    assert event.size == 0.02
    assert logger.records == []


def test_parse_spot_fill_price_fallbacks() -> None:
    for key in ("fillPrice", "tradePrice", "price", "px"):
        oms, _ = _oms()
        event = oms._parse_fill(
            {
                "instType": "SPOT",
                "symbol": "ETHUSDT",
                "side": "sell",
                "orderId": f"spot-{key}",
                "tradeId": f"spot-trade-{key}",
                key: "2316.12",
                "size": "0.01",
            }
        )

        assert event is not None
        assert event.price == 2316.12


def test_parse_spot_fill_rejects_missing_price_with_warning() -> None:
    oms, logger = _oms()
    event = oms._parse_fill(
        {
            "instType": "SPOT",
            "symbol": "ETHUSDT",
            "side": "buy",
            "orderId": "spot-no-price",
            "clientOid": "HEDGE-no-price",
            "tradeId": "spot-trade-no-price",
            "size": "0.02",
        }
    )

    assert event is None
    warning = logger.records[-1]
    assert warning["reason"] == "fill_parse_warning"
    assert warning["parse_reason"] == "fill_price_missing_or_invalid"
    assert warning["inst_type"] == "SPOT"
    assert warning["order_id"] == "spot-no-price"
    assert warning["trade_id"] == "spot-trade-no-price"
    assert warning["client_oid"] == "HEDGE-no-price"
    assert "size" in warning["raw_keys"]


def test_parse_fill_reads_fee_detail() -> None:
    oms, _ = _oms()
    event = oms._parse_fill(
        {
            "instType": "SPOT",
            "symbol": "ETHUSDT",
            "side": "buy",
            "orderId": "spot-fee",
            "tradeId": "spot-trade-fee",
            "priceAvg": "2315.42",
            "size": "0.02",
            "feeDetail": '[{"fee":"-0.00001"},{"totalFee":"-0.00002"}]',
        }
    )

    assert event is not None
    assert abs(event.fee - -0.00003) < 1e-12
