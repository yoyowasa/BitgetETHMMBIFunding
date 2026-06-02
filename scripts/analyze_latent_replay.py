from __future__ import annotations

import argparse
import csv
import gzip
import json
import sqlite3
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPORTS = Path("reports")


@dataclass
class Quote:
    ts: float
    client_oid: str
    intent: str
    side: str
    price: float
    size: float
    cycle_id: object
    end_ts: float | None = None
    end_reason: str = ""


@dataclass
class MarketPoint:
    ts: float
    mid_perp: float | None = None
    mid_spot: float | None = None
    bid_px: float | None = None
    ask_px: float | None = None
    spread_bps: float | None = None
    trade_px: float | None = None
    trade_side: str | None = None
    trade_id: str | None = None


def iter_jsonl(path: Path) -> Iterable[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def fnum(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_records(log_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for name in ("decision.jsonl", "orders.jsonl", "fills.jsonl", "pnl.jsonl", "system.jsonl"):
        path = log_dir / name
        if path.exists():
            rows.extend(iter_jsonl(path))
    for path in sorted(log_dir.glob("*.jsonl.gz")):
        rows.extend(iter_jsonl(path))
    rows.sort(key=lambda r: fnum(r.get("ts")) or 0.0)
    return rows


def build_quotes(records: list[dict]) -> list[Quote]:
    quotes: dict[str, Quote] = {}
    for r in records:
        if r.get("event") == "order_new" and str(r.get("intent", "")).startswith("QUOTE_"):
            client_oid = str(r.get("client_oid") or "")
            if not client_oid:
                continue
            px = fnum(r.get("price"))
            size = fnum(r.get("size"))
            ts = fnum(r.get("ts"))
            if px is None or size is None or ts is None:
                continue
            quotes[client_oid] = Quote(
                ts=ts,
                client_oid=client_oid,
                intent=str(r.get("intent") or ""),
                side=str(r.get("side") or ""),
                price=px,
                size=size,
                cycle_id=r.get("cycle_id"),
            )
        elif r.get("event") == "order_cancel":
            client_oid = str(r.get("client_oid") or "")
            q = quotes.get(client_oid)
            ts = fnum(r.get("ts"))
            if q is not None and ts is not None:
                q.end_ts = ts
                q.end_reason = str(r.get("reason") or "order_cancel")
    return sorted(quotes.values(), key=lambda q: q.ts)


def build_market(records: list[dict]) -> list[MarketPoint]:
    pts: list[MarketPoint] = []
    seen_trade_ids: set[str] = set()
    for r in records:
        ts = fnum(r.get("ts"))
        if ts is None:
            continue
        event = r.get("event")
        if event == "tick":
            mid_perp = fnum(r.get("mid_perp"))
            mid_spot = fnum(r.get("mid_spot"))
            if mid_perp is None and mid_spot is None:
                continue
            pts.append(MarketPoint(ts=ts, mid_perp=mid_perp, mid_spot=mid_spot))
            continue
        if event != "risk":
            continue
        reason = str(r.get("reason") or "")
        if "cancel_aggressive" not in reason and reason not in {"tfi_fade_suppressed", "quote_fade"}:
            continue
        trade_id = r.get("trade_id")
        if trade_id:
            key = str(trade_id)
            if key in seen_trade_ids:
                continue
            seen_trade_ids.add(key)
        pts.append(
            MarketPoint(
                ts=ts,
                mid_perp=fnum(r.get("mid_perp")),
                bid_px=fnum(r.get("bid_px")) or fnum(r.get("best_bid_px")),
                ask_px=fnum(r.get("ask_px")) or fnum(r.get("best_ask_px")),
                spread_bps=fnum(r.get("spread_bps")),
                trade_px=fnum(r.get("trade_px")),
                trade_side=str(r.get("trade_side") or "") or None,
                trade_id=str(trade_id) if trade_id else None,
            )
        )
    pts.sort(key=lambda p: p.ts)
    return pts


def first_market_after(points: list[MarketPoint], target_ts: float) -> MarketPoint | None:
    times = [p.ts for p in points]
    idx = bisect_left(times, target_ts)
    while idx < len(points):
        p = points[idx]
        if p.mid_perp is not None or p.bid_px is not None or p.ask_px is not None:
            return p
        idx += 1
    return None


def detect_latent_fills(quotes: list[Quote], market: list[MarketPoint], max_trades: int | None = None) -> list[dict]:
    fills: list[dict] = []
    for q in quotes:
        if q.intent != "QUOTE_ASK" or q.side != "sell":
            continue
        end_ts = q.end_ts if q.end_ts is not None else q.ts + 60.0
        for p in market:
            if p.ts < q.ts:
                continue
            if p.ts > end_ts:
                break
            if p.trade_px is None or p.trade_side != "buy":
                continue
            if p.trade_px >= q.price:
                fill_ts = p.ts
                entry_perp_px = q.price
                hedge_point = first_market_after(market, fill_ts + 5.0)
                if hedge_point is None:
                    hedge_point = p
                # Spot ask is not logged. Approximate from mid_spot if available, otherwise perp ask/mid.
                hedge_spot_px = hedge_point.mid_spot or hedge_point.ask_px or hedge_point.mid_perp or p.mid_perp or q.price
                spread_bps = p.spread_bps or 0.0
                qty = q.size
                gross = (entry_perp_px - hedge_spot_px) * qty
                # Perp maker 1.4bps + spot taker 10bps + configured slippage 2bps.
                fee_slip = (1.4 + 10.0 + 2.0) / 10000.0 * abs(entry_perp_px * qty)
                net = gross - fee_slip
                fills.append(
                    {
                        "idx": len(fills) + 1,
                        "quote_ts": q.ts,
                        "fill_ts": fill_ts,
                        "client_oid": q.client_oid,
                        "quote_price": entry_perp_px,
                        "trade_px": p.trade_px,
                        "hedge_px_proxy": hedge_spot_px,
                        "qty": qty,
                        "gross_usdt": gross,
                        "fee_slip_usdt": fee_slip,
                        "net_usdt": net,
                        "spread_bps_at_touch": spread_bps,
                        "end_reason": q.end_reason,
                        "cycle_id": q.cycle_id,
                    }
                )
                break
        if max_trades is not None and len(fills) >= max_trades:
            break
    return fills


def diagnose_quote_touch(quotes: list[Quote], market: list[MarketPoint]) -> dict:
    times = [p.ts for p in market]
    active_buy_rows = 0
    touch_rows = 0
    min_gap_bps: float | None = None
    for q in quotes:
        if q.intent != "QUOTE_ASK" or q.side != "sell":
            continue
        end_ts = q.end_ts if q.end_ts is not None else q.ts + 60.0
        start = bisect_left(times, q.ts)
        for p in market[start:]:
            if p.ts > end_ts:
                break
            if p.trade_px is None or p.trade_side != "buy":
                continue
            active_buy_rows += 1
            gap_bps = ((q.price - p.trade_px) / p.trade_px) * 10000.0
            min_gap_bps = gap_bps if min_gap_bps is None else min(min_gap_bps, gap_bps)
            if p.trade_px >= q.price:
                touch_rows += 1
    return {
        "active_buy_rows": active_buy_rows,
        "touch_rows": touch_rows,
        "min_active_ask_gap_bps": min_gap_bps,
    }


def summarize_prefix(fills: list[dict], cuts: list[int], daily_stop_usdt: float | None) -> list[dict]:
    rows: list[dict] = []
    for cut in cuts:
        subset = fills[:cut]
        pnl = [float(r["net_usdt"]) for r in subset]
        cum = 0.0
        stopped_at = None
        for i, x in enumerate(pnl, 1):
            cum += x
            if daily_stop_usdt is not None and cum <= daily_stop_usdt:
                stopped_at = i
                break
        if stopped_at is not None:
            no_stop_tail = sum(pnl[stopped_at:])
        else:
            no_stop_tail = 0.0
        rows.append(
            {
                "cut": cut,
                "n": len(subset),
                "net_usdt": sum(pnl),
                "win_rate": (sum(1 for x in pnl if x > 0) / len(pnl)) if pnl else None,
                "avg_net_usdt": (sum(pnl) / len(pnl)) if pnl else None,
                "daily_stop_usdt": daily_stop_usdt,
                "daily_stop_hit_at": stopped_at,
                "latent_after_stop_count": 0 if stopped_at is None else max(0, len(subset) - stopped_at),
                "latent_after_stop_net_usdt": no_stop_tail,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_sqlite(path: Path, fills: list[dict], summary: list[dict]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        if fills:
            cols = list(fills[0].keys())
            con.execute(f"create table latent_fills ({', '.join(c + ' text' for c in cols)})")
            con.executemany(
                f"insert into latent_fills ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
                [[str(r.get(c, "")) for c in cols] for r in fills],
            )
        if summary:
            cols = list(summary[0].keys())
            con.execute(f"create table prefix_summary ({', '.join(c + ' text' for c in cols)})")
            con.executemany(
                f"insert into prefix_summary ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
                [[str(r.get(c, "")) for c in cols] for r in summary],
            )
        con.commit()
    finally:
        con.close()


def analyze(log_dir: Path, cuts: list[int], daily_stop_usdt: float | None, max_trades: int | None) -> tuple[Path, dict]:
    records = load_records(log_dir)
    quotes = build_quotes(records)
    market = build_market(records)
    fills = detect_latent_fills(quotes, market, max_trades=max_trades)
    diagnostics = diagnose_quote_touch(quotes, market)
    summary = summarize_prefix(fills, cuts, daily_stop_usdt)
    counts = Counter(q.end_reason for q in quotes)

    out_dir = REPORTS / f"latent_replay_{log_dir.name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "latent_fills.csv", fills)
    write_csv(out_dir / "prefix_summary.csv", summary)
    write_sqlite(out_dir / "latent_replay.sqlite", fills, summary)

    result = {
        "log_dir": str(log_dir),
        "records": len(records),
        "quotes": len(quotes),
        "quote_end_reasons": dict(counts.most_common(10)),
        "market_points": len(market),
        "latent_fills": len(fills),
        "diagnostics": diagnostics,
        "prefix_summary": summary,
        "sqlite": str(out_dir / "latent_replay.sqlite"),
    }
    text = f"""# Latent Replay Analysis

## 目的
- B案/DRY継続判断用に、active quote中のlatent fillを抽出し、10/30/50 trades、daily stopなし、spread/ask-bid entry込みproxyを確認する。

## 定義
- latent fill: `QUOTE_ASK` active中に `trade_side=buy` かつ `trade_px >= quote_price`。
- entry: futures ask quote fill price。
- hedge proxy: fill後5秒以降の `mid_spot` 優先、なければ best ask / perp mid。
- cost: perp maker `1.4bps` + spot taker `10bps` + slippage `2bps`。
- order送信: なし。

## 観測事実
```json
{json.dumps(result, ensure_ascii=False, indent=2)}
```

## 推論
- latent_fillsが10未満ならB案単体判断は保留。
- daily_stop_hit_atがある場合、latent_after_stop_net_usdtで今日の停止後17件相当が勝ち負けどちらかを見る。

## 未確定点
- Spot askの完全なL2はログに無いため、hedge proxyは近似。
- 実queue position、partial fill、private fill欠落は未反映。
"""
    (out_dir / "RESULT_LATENT_REPLAY.md").write_text(text, encoding="utf-8")
    return out_dir, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_dir", type=Path)
    parser.add_argument("--cuts", default="10,30,50")
    parser.add_argument("--daily-stop-usdt", type=float, default=None)
    parser.add_argument("--max-trades", type=int, default=None)
    args = parser.parse_args()
    cuts = [int(x.strip()) for x in args.cuts.split(",") if x.strip()]
    out_dir, result = analyze(args.log_dir, cuts, args.daily_stop_usdt, args.max_trades)
    print(json.dumps({"out_dir": str(out_dir), **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
