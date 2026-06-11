from __future__ import annotations

import json

import pytest

from scripts.analyze_live_profitability import analyze


def _write_jsonl(path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_analyze_live_profitability_pairs_quote_and_unwind(tmp_path) -> None:
    _write_jsonl(
        tmp_path / "fills.jsonl",
        [
            {
                "ts": 1.0,
                "event": "fill",
                "inst_type": "USDT-FUTURES",
                "intent": "QUOTE_BID",
                "side": "buy",
                "price": 100.0,
                "size": 0.02,
                "fee": -0.001,
                "fee_coin": "USDT",
            },
            {
                "ts": 2.0,
                "event": "fill",
                "inst_type": "USDT-FUTURES",
                "intent": "UNWIND",
                "side": "sell",
                "price": 101.0,
                "size": 0.02,
                "fee": -0.003,
                "fee_coin": "USDT",
            },
        ],
    )

    result = analyze(tmp_path)

    assert result["fill_count"] == 2
    assert result["fill_by_intent"]["USDT-FUTURES:QUOTE_BID"]["count"] == 1
    assert result["rough_pairs"][0]["gross_usdt"] == 0.02
    assert result["rough_pairs"][0]["fee_usdt_known"] == 0.004
    assert result["rough_pair_net_usdt_known"] == 0.016
    assert result["realized_gross_cashflow_usdt"] == pytest.approx(0.02)
    assert result["realized_fee_usdt_observed_abs"] == pytest.approx(0.004)
    assert result["realized_cashflow_after_usdt_fees"] == pytest.approx(0.016)


def test_analyze_live_profitability_flags_repeated_unrealized_basis(tmp_path) -> None:
    _write_jsonl(
        tmp_path / "fills.jsonl",
        [
            {
                "ts": 1.0,
                "event": "fill",
                "inst_type": "SPOT",
                "intent": "HEDGE",
                "side": "buy",
                "price": 100.0,
                "size": 1.0,
                "fee": 0.001,
                "fee_coin": "BASE",
            },
        ],
    )
    _write_jsonl(
        tmp_path / "pnl.jsonl",
        [
            {"ts": 60.0, "event": "pnl_1min", "basis_pnl": -1.0, "net_pnl": -1.0},
            {"ts": 120.0, "event": "pnl_1min", "basis_pnl": -1.2, "net_pnl": -1.2},
        ],
    )

    result = analyze(tmp_path)

    assert result["pnl_net_sum"] == pytest.approx(-2.2)
    assert result["pnl_net_sum_repeats_unrealized_basis"] is True
    assert result["realized_base_fee_qty"] == {"BASE": 0.001}
