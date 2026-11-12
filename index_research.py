#!/usr/bin/env python3
"""
index_research.py - does the Nifty / Bank Nifty "bias" actually mean anything?

For each index it computes the same states the daily report uses (EMA stack, RSI zone, MACD,
Supertrend, VIX bucket, combined bias) on up to 20 years of daily data and measures what the
index did NEXT: 1/3/5/10-day forward returns, win%, and the best / worst move inside the next
5 and 10 days.

Command line:
    python index_research.py                         (yfinance, Nifty 50 + Bank Nifty)
    python index_research.py --source csv --csv-dir data
    python index_research.py --indices "NIFTY 50" --target-pct 4 --horizon 5

HOW TO READ IT
  * edge_vs_base = average forward return in that state minus the average of ALL days.
  * t_eff uses n/horizon as the sample size (forward windows overlap), so it is conservative.
    |t_eff| < 2  -> no reliable difference from "just any day".
  * Many rows are tested, so a few will look good by luck. Trust only edges that keep the same
    sign in most years (by-year table) and in BOTH indices.
  * States use only data up to the signal day's close (no look-ahead).
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

import index_core as core

WARMUP = 260   # bars skipped for indicator warm-up (EMA200, VIX percentile)


def prepare(f: pd.DataFrame) -> pd.DataFrame:
    return f.iloc[WARMUP:].copy()


def stats_table(f: pd.DataFrame, key: str, H: int, base_mean: float, min_n: int) -> pd.DataFrame:
    rows = []
    for state, g in f.groupby(key):
        x = g[f"fwd{H}"].dropna()
        n = len(x)
        if n == 0:
            continue
        mean = x.mean()
        se = x.std() / np.sqrt(max(n / H, 1.0)) if n > 1 else np.nan
        t = (mean - base_mean) / se if se and se > 0 else np.nan
        if n < min_n:
            note = "too few"
        elif np.isnan(t) or abs(t) < 2:
            note = "no reliable edge"
        else:
            note = "positive? check years" if mean > base_mean else "negative? check years"
        rows.append(dict(
            state=state, n=n, mean_pct=mean, median_pct=x.median(), win_pct=(x > 0).mean() * 100,
            edge_vs_base=mean - base_mean, t_eff=t,
            avg_best_move=g[f"mfe{H}"].mean() if f"mfe{H}" in g.columns else np.nan,
            avg_worst_move=g[f"mae{H}"].mean() if f"mae{H}" in g.columns else np.nan,
            verdict=note,
        ))
    return pd.DataFrame(rows)


def compute_tables(f: pd.DataFrame, H: int = 5, tp: float = 4.0, min_n: int = 40) -> dict:
    """f must already be passed through prepare(). Returns plain DataFrames (no printing)."""
    f = f.copy()
    lvl = float(f["close"].iloc[-1])
    meta = dict(start=f.index[0].date(), end=f.index[-1].date(), n=int(f[f"fwd{H}"].notna().sum()),
                H=H, tp=tp, lvl=lvl, pts=lvl * tp / 100.0)
    sections = []

    rows = []
    for h in core.FORWARD_H:
        x = f[f"fwd{h}"].dropna()
        rows.append(dict(horizon_days=h, n=len(x), mean_pct=x.mean(), median_pct=x.median(),
                         win_pct=(x > 0).mean() * 100, std_pct=x.std()))
    sections.append(dict(key="baseline", title="BASELINE - every day, no filter", df=pd.DataFrame(rows)))
    base_mean = f[f"fwd{H}"].dropna().mean()

    for key, title in [("ema_state", "EMA stack (close/EMA20/EMA50)"), ("lt_state", "Close vs EMA200"),
                       ("rsi_zone", "RSI zone"), ("macd_state", "MACD"), ("st_state", "Supertrend"),
                       ("vix_state", "India VIX percentile (1y)")]:
        sections.append(dict(key=key, title=f"{title} - next {H} days", df=stats_table(f, key, H, base_mean, min_n)))
    for key, title in [("align_bull", "Number of BULLISH components (0-4)"), ("align_bear", "Number of BEARISH components (0-4)")]:
        sections.append(dict(key=key, title=f"{title} - next {H} days", df=stats_table(f, key, H, base_mean, min_n)))

    f["bias_strength"] = f["bias_raw"] + " / " + f["strength_raw"]
    for key, title in [("bias_raw", "COMBINED BIAS (no VIX gate)"),
                       ("bias", f"COMBINED BIAS (VIX gate < {core.VIX_GATE_ABS})"),
                       ("bias_strength", "COMBINED BIAS by strength (no VIX gate)")]:
        sections.append(dict(key=key, title=f"{title} - next {H} days", df=stats_table(f, key, H, base_mean, min_n)))

    yr = f[f["bias_raw"].isin(["BULLISH", "BEARISH"])].copy()
    yr["year"] = yr.index.year
    piv = yr.pivot_table(index="year", columns="bias_raw", values=f"fwd{H}", aggfunc=["mean", "count"])
    piv.columns = [f"{a}_{b}" for a, b in piv.columns]
    piv = piv.reset_index()
    sections.append(dict(key="by_year", title=f"BY YEAR - mean {H}-day return after BULLISH vs BEARISH bias", df=piv))
    year_note = ""
    if "mean_BULLISH" in piv and "mean_BEARISH" in piv:
        both = piv.dropna(subset=["mean_BULLISH", "mean_BEARISH"])
        if len(both):
            good = (both["mean_BULLISH"] > both["mean_BEARISH"]).mean() * 100
            year_note = f"Years where BULLISH beat BEARISH: {good:.0f}% of {len(both)} years with both calls"

    rows = []
    for key, g in f.groupby("bias_raw"):
        x, y = g["mfe10"].dropna(), g["mae10"].dropna()
        if len(x):
            rows.append(dict(state=key, n=len(x), p_up_move_pct=(x >= tp).mean() * 100, p_down_move_pct=(y <= -tp).mean() * 100))
    x, y = f["mfe10"].dropna(), f["mae10"].dropna()
    rows.append(dict(state="ALL DAYS", n=len(x), p_up_move_pct=(x >= tp).mean() * 100, p_down_move_pct=(y <= -tp).mean() * 100))
    sections.append(dict(
        key="target",
        title=f"HOW OFTEN DOES THE INDEX MOVE {tp}% (about {meta['pts']:,.0f} points at {lvl:,.0f}) WITHIN 10 DAYS?",
        df=pd.DataFrame(rows)))
    return dict(meta=meta, sections=sections, year_note=year_note, by_year=piv)


def render_text(name: str, res: dict) -> str:
    m = res["meta"]
    lines = ["=" * 78,
             f"{name}   {m['start']} -> {m['end']}   ({m['n']} days with a {m['H']}-day outcome)",
             "=" * 78]
    for s in res["sections"]:
        lines += ["", s["title"], s["df"].round(2).to_string(index=False)]
        if s["key"] == "by_year" and res["year_note"]:
            lines.append(res["year_note"])
    return "\n".join(lines)


CAUTION = ("CAUTION: past statistics, many comparisons, overlapping windows. A real edge keeps the same sign "
           "across years and across both indices. 'no reliable edge' rows are the normal result.")


def main():
    core.ensure_utf8()
    ap = argparse.ArgumentParser(description="Index bias research")
    ap.add_argument("--source", choices=["yfinance", "csv", "angel"], default="yfinance")
    ap.add_argument("--csv-dir", default="data")
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--indices", default=",".join(core.INDICES))
    ap.add_argument("--horizon", type=int, default=5, choices=list(core.FORWARD_H))
    ap.add_argument("--target-pct", type=float, default=4.0, help="move size to test (4%% ~ 1000 Nifty points)")
    ap.add_argument("--min-n", type=int, default=40)
    ap.add_argument("--out-dir", default="index_research_out")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    report = []
    try:
        vix = core.load_prices(core.VIX_SYMBOL, args.source, args.csv_dir, args.years)
    except Exception as e:
        print(f"[warn] India VIX not available ({e}); VIX tables will be empty")
        vix = None

    for name in [s.strip() for s in args.indices.split(",") if s.strip()]:
        if name not in core.INDICES:
            print(f"[skip] unknown index {name}")
            continue
        try:
            df = core.load_prices(core.INDICES[name], args.source, args.csv_dir, args.years)
            if len(df) < 600:
                print(f"[skip] {name}: only {len(df)} bars")
                continue
            res = compute_tables(prepare(core.compute_features(df, vix)), args.horizon, args.target_pct, args.min_n)
            text = render_text(name, res)
            print(text)
            report.append(text)
            tag = name.replace(" ", "_")
            for s in res["sections"]:
                s["df"].to_csv(os.path.join(args.out_dir, f"{tag}_{s['key']}.csv"), index=False)
        except Exception as e:
            print(f"[error] {name}: {e}")

    print("\n" + CAUTION)
    report.append(CAUTION)
    with open(os.path.join(args.out_dir, "index_research_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(report))
    print(f"\nSaved report and CSV tables in {args.out_dir}/")


if __name__ == "__main__":
    main()
