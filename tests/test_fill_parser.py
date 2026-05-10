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
from bot.types import ExecutionEvent, InstType, Side


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
    assert event.fee_coin is None


def test_parse_fill_reads_fee_coin_from_fee_detail() -> None:
    oms, _ = _oms()
    event = oms._parse_fill(
        {
            "instType": "SPOT",
            "symbol": "ETHUSDT",
            "side": "buy",
            "orderId": "spot-fee-coin",
            "tradeId": "spot-trade-fee-coin",
            "priceAvg": "2315.42",
            "size": "0.02",
            "feeDetail": '[{"fee":"-0.00002","feeCoin":"ETH"}]',
        }
    )

    assert event is not None
    assert event.fee == -0.00002
    assert event.fee_coin == "ETH"


def test_spot_position_accounting_subtracts_base_fee_on_buy_and_keeps_sell_size() -> None:
    oms, _ = _oms()

    import asyncio

    asyncio.run(
        oms.ingest_fill(
            ExecutionEvent(
                inst_type=InstType.SPOT,
                symbol="ETHUSDT",
                order_id="buy-base-fee",
                client_oid="HEDGE-buy-base-fee",
                fill_id="fill-buy-base-fee",
                side=Side.BUY,
                price=2315.0,
                size=0.06,
                fee=-0.00006,
                fee_coin="ETH",
                ts=1.0,
            )
        )
    )
    assert abs(oms.positions.spot_pos - 0.05994) < 1e-12

    asyncio.run(
        oms.ingest_fill(
            ExecutionEvent(
                inst_type=InstType.SPOT,
                symbol="ETHUSDT",
                order_id="sell-quote-fee",
                client_oid="FLATTEN-sell-quote-fee",
                fill_id="fill-sell-quote-fee",
                side=Side.SELL,
                price=2315.0,
                size=0.02,
                fee=-0.1,
                fee_coin="USDT",
                ts=2.0,
            )
        )
    )
    assert abs(oms.positions.spot_pos - 0.03994) < 1e-12


def test_spot_position_accounting_does_not_subtract_quote_fee_on_buy() -> None:
    oms, _ = _oms()

    import asyncio

    asyncio.run(
        oms.ingest_fill(
            ExecutionEvent(
                inst_type=InstType.SPOT,
                symbol="ETHUSDT",
                order_id="buy-quote-fee",
                client_oid="HEDGE-buy-quote-fee",
                fill_id="fill-buy-quote-fee",
                side=Side.BUY,
                price=2315.0,
                size=0.06,
                fee=-0.1,
                fee_coin="USDT",
                ts=1.0,
            )
        )
    )

    assert oms.positions.spot_pos == 0.06


def test_spot_fee_coin_missing_logs_warning_and_keeps_size_accounting() -> None:
    oms, logger = _oms()

    import asyncio

    event = oms._parse_fill(
        {
            "instType": "SPOT",
            "symbol": "ETHUSDT",
            "side": "buy",
            "orderId": "buy-missing-fee-coin",
            "clientOid": "HEDGE-buy-missing-fee-coin",
            "tradeId": "fill-buy-missing-fee-coin",
            "priceAvg": "2315.0",
            "size": "0.06",
            "fee": "-0.00006",
            "ts": "1",
        }
    )
    assert event is not None
    asyncio.run(oms.ingest_fill(event))

    assert oms.positions.spot_pos == 0.06
    assert logger.records[-1]["reason"] == "fill_parse_warning"
    assert logger.records[-1]["parse_reason"] == "spot_fee_coin_missing"
    assert "fee" in logger.records[-1]["raw_keys"]


def test_futures_fill_accounting_skips_when_positions_sync_is_authoritative() -> None:
    oms, logger = _oms()
    oms._dry_run = False
    oms._positions_sync_authoritative = True
    oms.positions.perp_pos = 0.02

    import asyncio

    asyncio.run(
        oms.ingest_fill(
            ExecutionEvent(
                inst_type=InstType.USDT_FUTURES,
                symbol="ETHUSDT",
                order_id="futures-fill",
                client_oid="UNWIND-futures-fill",
                fill_id="futures-fill-id",
                side=Side.BUY,
                price=2300.0,
                size=0.02,
                fee=-0.01,
                fee_coin="USDT",
                ts=1.0,
            )
        )
    )

    assert oms.positions.perp_pos == 0.02
    skips = [
        record
        for record in logger.records
        if record.get("reason")
        == "futures_fill_position_accounting_skipped_positions_sync_authoritative"
    ]
    assert skips
    assert skips[-1]["perp_pos_before"] == 0.02
    assert skips[-1]["perp_pos_after"] == 0.02


def test_futures_fill_accounting_fallback_applies_without_positions_sync() -> None:
    oms, logger = _oms()
    oms._dry_run = False

    import asyncio

    asyncio.run(
        oms.ingest_fill(
            ExecutionEvent(
                inst_type=InstType.USDT_FUTURES,
                symbol="ETHUSDT",
                order_id="futures-fill",
                client_oid="UNWIND-futures-fill",
                fill_id="futures-fill-id",
                side=Side.BUY,
                price=2300.0,
                size=0.02,
                fee=-0.01,
                fee_coin="USDT",
                ts=1.0,
            )
        )
    )

    assert oms.positions.perp_pos == 0.02
    applied = [
        record
        for record in logger.records
        if record.get("reason") == "futures_fill_position_accounting_applied"
    ]
    assert applied
    assert applied[-1]["positions_sync_authoritative"] is False


def test_unwind_fill_reconciles_unhedged_qty_with_positions_sync_authoritative() -> None:
    oms, logger = _oms()
    oms._dry_run = False
    oms._positions_sync_authoritative = True
    oms._unhedged_qty = -0.02
    oms._unhedged_since = 1.0

    import asyncio

    asyncio.run(
        oms.ingest_fill(
            ExecutionEvent(
                inst_type=InstType.USDT_FUTURES,
                symbol="ETHUSDT",
                order_id="unwind-fill",
                client_oid="UNWIND-futures-fill",
                fill_id="unwind-fill-id",
                side=Side.SELL,
                price=2300.0,
                size=0.02,
                fee=-0.01,
                fee_coin="USDT",
                ts=2.0,
            )
        )
    )

    assert oms.unhedged_qty == 0.0
    assert oms.unhedged_since is None
    reconciled = [
        record
        for record in logger.records
        if record.get("reason") == "unwind_fill_unhedged_qty_reconciled"
    ]
    assert reconciled
    assert reconciled[-1]["unhedged_qty_before"] == -0.02
    assert reconciled[-1]["unhedged_qty_after"] == 0.0
