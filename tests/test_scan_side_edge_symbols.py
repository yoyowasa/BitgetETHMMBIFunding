from __future__ import annotations

from math import isclose

from scripts.scan_side_edge_symbols import scan_side_edges, ticker_map


def test_ticker_map_accepts_bitget_aliases() -> None:
    payload = {
        "data": [
            {
                "symbol": "WLDUSDT",
                "bidPr": "0.5100",
                "askPr": "0.5102",
                "usdtVolume": "12345.6",
            }
        ]
    }

    result = ticker_map(payload)

    assert result["WLDUSDT"].bid == 0.5100
    assert result["WLDUSDT"].ask == 0.5102
    assert result["WLDUSDT"].quote_volume == 12345.6


def test_scan_side_edges_matches_strategy_formula() -> None:
    spot_payload = {
        "data": [
            {
                "symbol": "AAAUSDT",
                "bidPr": "100.0",
                "askPr": "100.2",
                "usdtVolume": "1000",
            }
        ]
    }
    perp_payload = {
        "data": [
            {
                "symbol": "AAAUSDT",
                "bidPr": "99.6",
                "askPr": "99.8",
                "usdtVolume": "2000",
            }
        ]
    }

    rows = scan_side_edges(
        spot_payload,
        perp_payload,
        half_spread_bps=10.0,
        hedge_aggressive_bps=5.0,
        side_cost_bps=15.4,
        min_side_edge_bps=0.0,
    )

    assert len(rows) == 1
    row = rows[0]
    mid_spot = 100.1
    mid_perp = 99.7
    expected_bid = ((100.0 * 0.9995) - (mid_perp * 0.999)) / mid_spot * 10000.0 - 15.4
    expected_ask = ((mid_perp * 1.001) - (100.2 * 1.0005)) / mid_spot * 10000.0 - 15.4
    assert isclose(row.bid_side_edge_bps, expected_bid)
    assert isclose(row.ask_side_edge_bps, expected_ask)
    assert row.bid_pass is True
    assert row.ask_pass is False
