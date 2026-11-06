#!/usr/bin/env python3
"""
index_research.py - does the Nifty / Bank Nifty "bias" actually mean anything?

For each index it computes the same states the daily report uses (EMA stack, RSI zone,
MACD, Supertrend, VIX bucket, combined bias) on up to 20 years of daily data and measures
what the index did NEXT: 1/3/5/10-day forward returns, win%, and the best / worst move
inside the next 5 and 10 days (MFE / MAE).

USAGE
    python index_research.py                         (yfinance, Nifty 50 + Bank Nifty)
    python index_research.py --source csv --csv-dir data
    python index_research.py --indices "NIFTY 50" --target-pct 4 --horizon 5

HOW TO READ IT
  * edge_vs_base = average forward return in that state minus the average of ALL days.
  * t_eff uses n/horizon as the sample size (forward windows overlap), so it is conservative.
    |t_eff| < 2  -> no reliable difference from "just any day".
  * Many rows are tested, so a few will look good by luck. Trust only edges that keep the same
    sign in most years (see the by-year table) and in BOTH indices.
  * Signals are measured from the close of the signal day (no look-ahead in the states).
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

import index_core as core

LINES: list[str] = []


def out(text: str = ""):
    print(text)
    LINES.append(text)


def fmt(df: pd.DataFrame) -> str:
    return df.round(2).to_string(index=False)


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


def analyse(name: str, f: pd.DataFrame, args) -> dict:
    H = args.horizon
    tp = args.target_pct
    f = f.iloc[260:].copy()             # indicator warm-up (EMA200, VIX percentile)
    n_all = f[f"fwd{H}"].notna().sum()
    out("=" * 78)
    out(f"{name}   {f.index[0].date()} -> {f.index[-1].date()}   ({n_all} days with a {H}-day outcome)")
    out("=" * 78)

    # ---- baseline
    rows = []
    for h in core.FORWARD_H:
        x = f[f"fwd{h}"].dropna()
        rows.append(dict(horizon_days=h, n=len(x), mean_pct=x.mean(), median_pct=x.median(),
                         win_pct=(x > 0).mean() * 100, std_pct=x.std()))
    base = pd.DataFrame(rows)
    out("\nBASELINE - every day, no filter")
    out(fmt(base))
    base_mean = f[f"fwd{H}"].dropna().mean()

    tables = {"baseline": base}

    # ---- single components
    for key, title in [("ema_state", "EMA stack (close/EMA20/EMA50)"), ("lt_state", "Close vs EMA200"),
                       ("rsi_zone", "RSI zone"), ("macd_state", "MACD"), ("st_state", "Supertrend"),
                       ("vix_state", "India VIX percentile (1y)")]:
        t = stats_table(f, key, H, base_mean, args.min_n)
        tables[key] = t
        out(f"\n{title} - what happened over the next {H} days")
        out(fmt(t))

    # ---- agreement count
    for key, title in [("align_bull", "Number of BULLISH components (0-4)"), ("align_bear", "Number of BEARISH components (0-4)")]:
        t = stats_table(f, key, H, base_mean, args.min_n)
        tables[key] = t
        out(f"\n{title}")
        out(fmt(t))

    # ---- combined bias (the rule from your original script)
    f["bias_strength"] = f["bias_raw"] + " / " + f["strength_raw"]
    for key, title in [("bias_raw", "COMBINED BIAS (no VIX gate)"), ("bias", f"COMBINED BIAS (VIX gate < {core.VIX_GATE_ABS})"),
                       ("bias_strength", "COMBINED BIAS by strength (no VIX gate)")]:
        t = stats_table(f, key, H, base_mean, args.min_n)
        tables[key] = t
        out(f"\n{title}")
        out(fmt(t))

    # ---- stability by year
    yr = f[f["bias_raw"].isin(["BULLISH", "BEARISH"])].copy()
    yr["year"] = yr.index.year
    piv = yr.pivot_table(index="year", columns="bias_raw", values=f"fwd{H}", aggfunc=["mean", "count"])
    piv.columns = [f"{a}_{b}" for a, b in piv.columns]
    piv = piv.reset_index()
    tables["by_year"] = piv
    out(f"\nBY YEAR - mean {H}-day return after BULLISH vs BEARISH bias (stable edge = same sign in most years)")
    out(fmt(piv))
    if "mean_BULLISH" in piv and "mean_BEARISH" in piv:
        both = piv.dropna(subset=["mean_BULLISH", "mean_BEARISH"])
        if len(both):
            good = (both["mean_BULLISH"] > both["mean_BEARISH"]).mean() * 100
            out(f"Years where BULLISH beat BEARISH: {good:.0f}% of {len(both)} years with both calls")

    # ---- target move probability
    lvl = float(f["close"].iloc[-1])
    pts = lvl * tp / 100.0
    rows = []
    for key, g in f.groupby("bias_raw"):
        x = g["mfe10"].dropna()
        y = g["mae10"].dropna()
        if len(x):
            rows.append(dict(state=key, n=len(x), p_up_move_pct=(x >= tp).mean() * 100,
                             p_down_move_pct=(y <= -tp).mean() * 100))
    x = f["mfe10"].dropna()
    y = f["mae10"].dropna()
    rows.append(dict(state="ALL DAYS", n=len(x), p_up_move_pct=(x >= tp).mean() * 100, p_down_move_pct=(y <= -tp).mean() * 100))
    tgt = pd.DataFrame(rows)
    tables["target"] = tgt
    out(f"\nHOW OFTEN DOES THE INDEX MOVE {tp}% (about {pts:,.0f} points at {lvl:,.0f}) WITHIN 10 DAYS?")
    out("(best move up / worst move down inside the next 10 days, measured from the signal close)")
    out(fmt(tgt))
    return tables


def main():
    core.ensure_utf8()
    ap = argparse.ArgumentParser(description="Index bias research")
    ap.add_argument("--source", choices=["yfinance", "csv", "angel"], default="yfinance")
    ap.add_argument("--csv-dir", default="data")
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--indices", default=",".join(core.INDICES), help="comma separated names from index_core.INDICES")
    ap.add_argument("--horizon", type=int, default=5, choices=list(core.FORWARD_H))
    ap.add_argument("--target-pct", type=float, default=4.0, help="move size to test (4%% ~ 1000 Nifty points)")
    ap.add_argument("--min-n", type=int, default=40)
    ap.add_argument("--out-dir", default="index_research_out")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    try:
        vix = core.load_prices(core.VIX_SYMBOL, args.source, args.csv_dir, args.years)
    except Exception as e:
        out(f"[warn] India VIX not available ({e}); VIX tables will be empty")
        vix = None

    for name in [s.strip() for s in args.indices.split(",") if s.strip()]:
        if name not in core.INDICES:
            out(f"[skip] unknown index {name}")
            continue
        try:
            df = core.load_prices(core.INDICES[name], args.source, args.csv_dir, args.years)
            if len(df) < 600:
                out(f"[skip] {name}: only {len(df)} bars")
                continue
            feats = core.compute_features(df, vix)
            tables = analyse(name, feats, args)
            tag = name.replace(" ", "_")
            for k, t in tables.items():
                t.to_csv(os.path.join(args.out_dir, f"{tag}_{k}.csv"), index=False)
        except Exception as e:
            out(f"[error] {name}: {e}")

    out("\nCAUTION: past statistics, many comparisons, overlapping windows. A real edge keeps the same sign "
        "across years and across both indices. 'no reliable edge' rows are the normal result.")
    path = os.path.join(args.out_dir, "index_research_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES))
    print(f"\nSaved report and CSV tables in {args.out_dir}/")


if __name__ == "__main__":
    main()
