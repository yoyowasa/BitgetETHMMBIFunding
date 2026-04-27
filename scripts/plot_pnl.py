"""Plot PnL timeseries from logs/pnl.jsonl using matplotlib.

For each sim_fill_enabled run boundary in logs/system.jsonl, render:
- cumulative net_pnl, gross_spread, fees
- per-minute net_pnl bars
And a comparison chart of all runs.
Saves PNGs to reports/pnl_plots/.

Requires: matplotlib (pip install matplotlib).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "pnl_plots"
OUT.mkdir(parents=True, exist_ok=True)


def load_runs() -> list[tuple[float, float, float]]:
    runs = []
    with open(ROOT / "logs" / "system.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("event") == "sim_fill_enabled":
                ts_ms = j.get("ts", 0)
                iv = j.get("data", {}).get("interval_sec", 0)
                qty = j.get("data", {}).get("fill_qty", 0)
                runs.append((ts_ms / 1000.0, iv, qty))
    runs.sort()
    return runs


def load_pnl(start: float, end: float) -> list[dict]:
    rows = []
    with open(ROOT / "logs" / "pnl.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("event") != "pnl_1min":
                continue
            ts = j.get("ts", 0)
            if ts < start or ts >= end:
                continue
            rows.append(j)
    return rows


def cumsum(xs: list[float]) -> list[float]:
    out, s = [], 0.0
    for v in xs:
        s += v
        out.append(s)
    return out


def plot_run(idx: int, start: float, iv: float, qty: float, end: float) -> None:
    rows = load_pnl(start - 5, end)
    if not rows:
        return
    ts = [datetime.fromtimestamp(r["ts"]) for r in rows]
    gross = [r.get("gross_spread_pnl", 0) for r in rows]
    fees = [r.get("fees_paid", 0) for r in rows]
    net = [r.get("net_pnl", 0) for r in rows]
    cum_net = cumsum(net)
    cum_gross = cumsum(gross)
    cum_fees = cumsum(fees)

    label = f"run{idx}_iv{iv:.0f}_qty{qty}"
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    ax = axes[0]
    ax.plot(ts, cum_net, label="cum net_pnl", color="C0", linewidth=2.5)
    ax.plot(ts, cum_gross, label="cum gross_spread", color="C2", linestyle="--")
    ax.plot(ts, cum_fees, label="cum fees", color="C3", linestyle="--")
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_ylabel("USDT (cumulative)")
    ax.set_title(f"PnL — {label} — net total = {cum_net[-1]:.3f} USDT ({len(rows)} min)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    colors = ["C0" if v >= 0 else "C3" for v in net]
    width_days = 50.0 / 86400.0  # 50 sec
    ax.bar(ts, net, width=width_days, color=colors)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_ylabel("USDT (per minute)")
    ax.set_xlabel("time")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    out = OUT / f"{label}.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  saved {out}  rows={len(rows)} net={cum_net[-1]:.3f}")


def plot_comparison(runs_tail: list[tuple[float, float, float]]) -> None:
    labels, means, totals = [], [], []
    for i, (t, iv, qty) in enumerate(runs_tail):
        next_t = runs_tail[i + 1][0] if i + 1 < len(runs_tail) else t + 7200
        rows = load_pnl(t - 5, next_t - 5)
        if not rows:
            labels.append(f"iv{iv:.0f}_q{qty}")
            means.append(0.0)
            totals.append(0.0)
            continue
        ns = [r.get("net_pnl", 0) for r in rows]
        labels.append(f"iv{iv:.0f}_q{qty}\n({len(rows)}min)")
        means.append(sum(ns) / len(ns))
        totals.append(sum(ns))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, vals, title, ylabel in (
        (axes[0], means, "mean net_pnl / min", "USDT/min"),
        (axes[1], totals, "total net_pnl", "USDT"),
    ):
        colors = ["C0" if v >= 0 else "C3" for v in vals]
        bars = ax.bar(labels, vals, color=colors)
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=0.3, axis="y")
        for b, v in zip(bars, vals):
            ax.text(
                b.get_x() + b.get_width() / 2,
                v,
                f"{v:.3f}" if abs(v) < 5 else f"{v:.2f}",
                ha="center",
                va="bottom" if v >= 0 else "top",
                fontsize=9,
            )
    fig.tight_layout()
    out = OUT / "comparison.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f"  saved {out}")


def main() -> None:
    runs = load_runs()
    if not runs:
        print("no sim_fill_enabled runs found")
        return
    print(f"runs found: {len(runs)}")
    last_n = min(6, len(runs))
    runs_tail = runs[-last_n:]
    for i, (t, iv, qty) in enumerate(runs_tail):
        next_t = runs_tail[i + 1][0] if i + 1 < len(runs_tail) else t + 7200
        plot_run(len(runs) - last_n + i + 1, t, iv, qty, next_t - 5)
    plot_comparison(runs_tail)


if __name__ == "__main__":
    main()
