from __future__ import annotations

import json

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
