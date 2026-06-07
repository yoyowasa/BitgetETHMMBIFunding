from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bot.config import apply_env_overrides, load_config


SPOT_TICKERS_PATH = "/api/v2/spot/market/tickers"
PERP_TICKERS_PATH = "/api/v2/mix/market/tickers"
PERP_FUNDING_PATH = "/api/v2/mix/market/current-fund-rate"


@dataclass(frozen=True)
class Ticker:
    symbol: str
    bid: float
    ask: float
    quote_volume: float


@dataclass(frozen=True)
class SideEdgeScanRow:
    symbol: str
    spot_bid: float
    spot_ask: float
    perp_bid: float
    perp_ask: float
    mid_spot: float
    mid_perp: float
    mid_basis_bps: float
    perp_quote_bid: float
    perp_quote_ask: float
    bid_side_edge_bps: float
    ask_side_edge_bps: float
    best_side_edge_bps: float
    funding_rate: float | None
    funding_skew_bps: float
    bid_funding_adjust_bps: float
    ask_funding_adjust_bps: float
    bid_pass: bool
    ask_pass: bool
    spot_quote_volume: float
    perp_quote_volume: float


def _float(row: dict[str, Any], names: tuple[str, ...]) -> float:
    for name in names:
        value = row.get(name)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def ticker_map(payload: dict[str, Any]) -> dict[str, Ticker]:
    tickers: dict[str, Ticker] = {}
    for row in _rows(payload):
        symbol = str(row.get("symbol") or row.get("instId") or "").upper()
        bid = _float(row, ("bidPr", "bidPx", "bestBid", "bid", "buyOne"))
        ask = _float(row, ("askPr", "askPx", "bestAsk", "ask", "sellOne"))
        quote_volume = _float(
            row,
            (
                "usdtVolume",
                "quoteVolume",
                "quoteVol",
                "turnover24h",
                "quoteVolume24h",
                "baseVolume",
                "volume",
            ),
        )
        if symbol and bid > 0 and ask > 0:
            tickers[symbol] = Ticker(symbol=symbol, bid=bid, ask=ask, quote_volume=quote_volume)
    return tickers


def scan_side_edges(
    spot_payload: dict[str, Any],
    perp_payload: dict[str, Any],
    *,
    half_spread_bps: float,
    hedge_aggressive_bps: float,
    side_cost_bps: float,
    min_side_edge_bps: float,
    symbols: list[str] | None = None,
    min_perp_quote_volume: float = 0.0,
    max_abs_mid_basis_bps: float | None = None,
    funding_rates: dict[str, float | None] | None = None,
    funding_skew_bps_per_rate: float = 0.0,
) -> list[SideEdgeScanRow]:
    spot_tickers = ticker_map(spot_payload)
    perp_tickers = ticker_map(perp_payload)
    candidates = set(symbol.upper() for symbol in symbols) if symbols else set(spot_tickers)
    candidates &= set(perp_tickers)

    rows: list[SideEdgeScanRow] = []
    for symbol in sorted(candidates):
        spot = spot_tickers[symbol]
        perp = perp_tickers[symbol]
        if perp.quote_volume < min_perp_quote_volume:
            continue
        mid_spot = (spot.bid + spot.ask) / 2.0
        mid_perp = (perp.bid + perp.ask) / 2.0
        if mid_spot <= 0 or mid_perp <= 0:
            continue
        mid_basis_bps = (mid_perp - mid_spot) / mid_spot * 10000.0
        if max_abs_mid_basis_bps is not None and abs(mid_basis_bps) > max_abs_mid_basis_bps:
            continue
        funding_rate = None if funding_rates is None else funding_rates.get(symbol)
        funding_skew_bps = (funding_rate or 0.0) * funding_skew_bps_per_rate
        if (funding_rate or 0.0) > 0:
            bid_funding_adjust = max(0.0, funding_skew_bps)
            ask_funding_adjust = -max(0.0, funding_skew_bps)
        else:
            bid_funding_adjust = min(0.0, funding_skew_bps)
            ask_funding_adjust = -min(0.0, funding_skew_bps)
        perp_quote_bid = mid_perp * (1.0 - (half_spread_bps + bid_funding_adjust) / 10000.0)
        perp_quote_ask = mid_perp * (1.0 + (half_spread_bps + ask_funding_adjust) / 10000.0)
        bid_spot_hedge_px = spot.bid * (1.0 - hedge_aggressive_bps / 10000.0)
        ask_spot_hedge_px = spot.ask * (1.0 + hedge_aggressive_bps / 10000.0)
        bid_edge = (bid_spot_hedge_px - perp_quote_bid) / mid_spot * 10000.0 - side_cost_bps
        ask_edge = (perp_quote_ask - ask_spot_hedge_px) / mid_spot * 10000.0 - side_cost_bps
        rows.append(
            SideEdgeScanRow(
                symbol=symbol,
                spot_bid=spot.bid,
                spot_ask=spot.ask,
                perp_bid=perp.bid,
                perp_ask=perp.ask,
                mid_spot=mid_spot,
                mid_perp=mid_perp,
                mid_basis_bps=mid_basis_bps,
                perp_quote_bid=perp_quote_bid,
                perp_quote_ask=perp_quote_ask,
                bid_side_edge_bps=bid_edge,
                ask_side_edge_bps=ask_edge,
                best_side_edge_bps=max(bid_edge, ask_edge),
                funding_rate=funding_rate,
                funding_skew_bps=funding_skew_bps,
                bid_funding_adjust_bps=bid_funding_adjust,
                ask_funding_adjust_bps=ask_funding_adjust,
                bid_pass=bid_edge >= min_side_edge_bps,
                ask_pass=ask_edge >= min_side_edge_bps,
                spot_quote_volume=spot.quote_volume,
                perp_quote_volume=perp.quote_volume,
            )
        )
    return sorted(rows, key=lambda row: (row.best_side_edge_bps, row.perp_quote_volume), reverse=True)


def fetch_json(base_url: str, path: str, params: dict[str, str] | None = None, timeout_sec: float = 10.0) -> dict[str, Any]:
    query = f"?{urlencode(params)}" if params else ""
    with urlopen(f"{base_url}{path}{query}", timeout=timeout_sec) as response:
        return json.loads(response.read().decode("utf-8"))


def filter_side_rows(rows: list[SideEdgeScanRow], side: str) -> list[SideEdgeScanRow]:
    if side == "bid":
        return [row for row in rows if row.bid_pass]
    if side == "ask":
        return [row for row in rows if row.ask_pass]
    return rows


def sort_side_rows(rows: list[SideEdgeScanRow], side: str) -> list[SideEdgeScanRow]:
    if side == "bid":
        return sorted(rows, key=lambda row: (row.bid_side_edge_bps, row.perp_quote_volume), reverse=True)
    if side == "ask":
        return sorted(rows, key=lambda row: (row.ask_side_edge_bps, row.perp_quote_volume), reverse=True)
    return sorted(rows, key=lambda row: (row.best_side_edge_bps, row.perp_quote_volume), reverse=True)


def funding_rate_from_payload(payload: dict[str, Any]) -> float | None:
    rows = _rows(payload)
    if not rows:
        return None
    rate = rows[0].get("fundingRate") or rows[0].get("funding_rate") or rows[0].get("rate")
    if rate in (None, ""):
        return None
    try:
        return float(rate)
    except (TypeError, ValueError):
        return None


def fetch_funding_rate(
    base_url: str,
    *,
    symbol: str,
    product_type: str,
    timeout_sec: float,
) -> float | None:
    payload = fetch_json(
        base_url,
        PERP_FUNDING_PATH,
        params={"symbol": symbol, "productType": product_type},
        timeout_sec=timeout_sec,
    )
    return funding_rate_from_payload(payload)


def _symbol_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only public ticker scan for spot/perp side-edge candidates."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--symbols", help="Comma-separated symbols. Default: spot/perp intersection.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--min-perp-quote-volume", type=float, default=0.0)
    parser.add_argument("--max-abs-mid-basis-bps", type=float, default=200.0)
    parser.add_argument(
        "--side",
        choices=("best", "bid", "ask"),
        default="best",
        help="Filter/sort by quote side. Use ask when starting with no spot inventory.",
    )
    parser.add_argument("--include-funding", action="store_true", help="Fetch current funding for displayed rows.")
    parser.add_argument(
        "--require-positive-funding",
        action="store_true",
        help="Keep only rows with funding rate >= strategy.min_funding_rate.",
    )
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    config = load_config(args.config)
    apply_env_overrides(config)
    side_cost_bps = (
        config.cost.fee_maker_perp_bps
        + config.cost.fee_taker_spot_bps
        + config.cost.slippage_bps
        + config.strategy.adverse_buffer_bps
    )
    spot_payload = fetch_json(config.exchange.base_url, SPOT_TICKERS_PATH, timeout_sec=args.timeout_sec)
    perp_payload = fetch_json(
        config.exchange.base_url,
        PERP_TICKERS_PATH,
        params={"productType": config.symbols.perp.productType or "USDT-FUTURES"},
        timeout_sec=args.timeout_sec,
    )
    product_type = config.symbols.perp.productType or "USDT-FUTURES"
    preliminary_rows = scan_side_edges(
        spot_payload,
        perp_payload,
        half_spread_bps=config.strategy.base_half_spread_bps,
        hedge_aggressive_bps=(
            config.hedge.hedge_aggressive_bps if config.hedge.use_spot_limit_ioc else 0.0
        ),
        side_cost_bps=side_cost_bps,
        min_side_edge_bps=config.strategy.side_edge_min_bps,
        symbols=_symbol_list(args.symbols),
        min_perp_quote_volume=args.min_perp_quote_volume,
        max_abs_mid_basis_bps=args.max_abs_mid_basis_bps,
    )
    funding_by_symbol: dict[str, float | None] = {}
    if args.include_funding or args.require_positive_funding:
        for row in preliminary_rows:
            funding_by_symbol[row.symbol] = fetch_funding_rate(
                config.exchange.base_url,
                symbol=row.symbol,
                product_type=product_type,
                timeout_sec=args.timeout_sec,
            )
    rows = scan_side_edges(
        spot_payload,
        perp_payload,
        half_spread_bps=config.strategy.base_half_spread_bps,
        hedge_aggressive_bps=(
            config.hedge.hedge_aggressive_bps if config.hedge.use_spot_limit_ioc else 0.0
        ),
        side_cost_bps=side_cost_bps,
        min_side_edge_bps=config.strategy.side_edge_min_bps,
        symbols=_symbol_list(args.symbols),
        min_perp_quote_volume=args.min_perp_quote_volume,
        max_abs_mid_basis_bps=args.max_abs_mid_basis_bps,
        funding_rates=funding_by_symbol if funding_by_symbol else None,
        funding_skew_bps_per_rate=config.strategy.funding_skew_bps_per_rate,
    )
    rows = sort_side_rows(filter_side_rows(rows, args.side), args.side)
    if args.include_funding or args.require_positive_funding:
        if args.require_positive_funding:
            rows = [
                row
                for row in rows
                if funding_by_symbol.get(row.symbol) is not None
                and funding_by_symbol[row.symbol] >= config.strategy.min_funding_rate
            ]
    limited = rows[: max(args.limit, 0)]
    output_rows = []
    for row in limited:
        output_row = asdict(row)
        if args.include_funding or args.require_positive_funding:
            output_row["funding_bps"] = (
                row.funding_rate * 10000.0 if row.funding_rate is not None else None
            )
        output_rows.append(output_row)
    output = {
        "config_path": str(Path(args.config)),
        "half_spread_bps": config.strategy.base_half_spread_bps,
        "hedge_aggressive_bps": (
            config.hedge.hedge_aggressive_bps if config.hedge.use_spot_limit_ioc else 0.0
        ),
        "side_cost_bps": side_cost_bps,
        "funding_skew_bps_per_rate": config.strategy.funding_skew_bps_per_rate,
        "min_side_edge_bps": config.strategy.side_edge_min_bps,
        "max_abs_mid_basis_bps": args.max_abs_mid_basis_bps,
        "side": args.side,
        "include_funding": args.include_funding,
        "require_positive_funding": args.require_positive_funding,
        "candidate_count": len(rows),
        "rows": output_rows,
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        funding_header = " funding_bps" if args.include_funding or args.require_positive_funding else ""
        print(
            "symbol best bid_edge ask_edge mid_basis bid_pass ask_pass spot_bid spot_ask perp_bid perp_ask perp_quote_volume"
            f"{funding_header}"
        )
        for row in limited:
            funding_value = ""
            if args.include_funding or args.require_positive_funding:
                funding_value = (
                    f" {row.funding_rate * 10000.0:.4f}"
                    if row.funding_rate is not None
                    else " null"
                )
            print(
                f"{row.symbol} "
                f"{row.best_side_edge_bps:.4f} "
                f"{row.bid_side_edge_bps:.4f} "
                f"{row.ask_side_edge_bps:.4f} "
                f"{row.mid_basis_bps:.4f} "
                f"{int(row.bid_pass)} "
                f"{int(row.ask_pass)} "
                f"{row.spot_bid:.10g} "
                f"{row.spot_ask:.10g} "
                f"{row.perp_bid:.10g} "
                f"{row.perp_ask:.10g} "
                f"{row.perp_quote_volume:.2f}"
                f"{funding_value}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
