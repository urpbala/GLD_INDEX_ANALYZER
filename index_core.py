#!/usr/bin/env python3
"""
index_core.py - shared helpers for index_research.py and index_daily_report.py

Edit the CONFIG block below (indices, lot sizes, risk rules, bias weights).
Nothing secret lives here: Telegram token / chat id come from environment
variables or a .env file (see env_example.txt).
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# ============================== CONFIG ==============================
# yf = Yahoo ticker, csv = file name in --csv-dir (without .csv)
# lot sizes change from time to time - VERIFY against the NSE contract list.
INDICES = {
    "NIFTY 50": dict(yf="^NSEI", csv="NIFTY50", round_step=100, strike_step=50, lot=65),
    "BANK NIFTY": dict(yf="^NSEBANK", csv="BANKNIFTY", round_step=500, strike_step=100, lot=30),
}
VIX_SYMBOL = dict(yf="^INDIAVIX", csv="INDIAVIX")

CAPITAL = 200000
MAX_RISK_PCT = 0.01          # 1% of capital per trade
SL_PREMIUM_PCT = 0.45        # stop on option premium (your rule)

# Same scoring as your original script. UNVALIDATED until index_research.py supports it.
BIAS_WEIGHTS = dict(ema=2.0, rsi=1.0, macd=1.5, st=1.5)
BIAS_GAP = 2.0               # directional bias if |bull - bear| >= gap
STRONG_SCORE = 5.0
VIX_GATE_ABS = 19.0          # bias forced to NEUTRAL when VIX >= this (None = off)
BIAS_VALIDATED = False       # set True after the research supports the rule

FORWARD_H = (1, 3, 5, 10)
# ====================================================================


def ensure_utf8():
    """Windows consoles often use cp1252; make printing emoji / symbols safe."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def load_env_file(path: str = ".env"):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ============================ DATA LOADING ============================
def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df.index = pd.to_datetime(df.index)
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["open", "high", "low", "close"])


def load_yf(ticker: str, years: int = 20) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(ticker, period=f"{years}y", interval="1d", auto_adjust=False, progress=False)
    if raw is None or raw.empty:
        raise ValueError(f"no data for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):   # newer yfinance versions
        raw.columns = raw.columns.get_level_values(0)
    return _clean(raw)


def load_csv(name: str, csv_dir: str = "data") -> pd.DataFrame:
    raw = pd.read_csv(os.path.join(csv_dir, f"{name}.csv"))
    raw.columns = [c.lower() for c in raw.columns]
    raw["date"] = pd.to_datetime(raw["date"])
    return _clean(raw.set_index("date"))


def load_angel(spec: dict) -> pd.DataFrame:
    """Plug in your Angel One SmartAPI daily-candle fetch here.
    Return a DataFrame indexed by date with columns open, high, low, close, volume.
    Use spec["csv"] (e.g. 'NIFTY50') or add your own token keys to INDICES."""
    raise NotImplementedError("Edit load_angel() in index_core.py to use your Angel One modules.")


def load_prices(spec: dict, source: str = "yfinance", csv_dir: str = "data", years: int = 20) -> pd.DataFrame:
    if source == "yfinance":
        return load_yf(spec["yf"], years)
    if source == "csv":
        return load_csv(spec["csv"], csv_dir)
    if source == "angel":
        return load_angel(spec)
    raise ValueError(f"unknown source {source}")


def drop_partial(df: pd.DataFrame, now: dt.datetime | None = None, cutoff=(15, 35)) -> pd.DataFrame:
    """Drop today's bar if the market is still open (so the report uses completed candles only)."""
    now = now or dt.datetime.now(IST)
    if len(df) and df.index[-1].date() == now.date() and (now.hour, now.minute) < cutoff:
        return df.iloc[:-1]
    return df


def expected_last_session(now: dt.datetime | None = None) -> dt.date:
    """Last weekday whose daily candle should be complete (no holiday calendar)."""
    now = now or dt.datetime.now(IST)
    d = now.date()
    if (now.hour, now.minute) < (15, 45):
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


# ============================== INDICATORS ==============================
def sh(x, k=1):
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def rma(x, n):
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    idx = np.where(~np.isnan(x))[0]
    if len(idx) == 0:
        return out
    f = idx[0]
    if f + n > len(x):
        return out
    out[f + n - 1] = x[f:f + n].mean()
    for i in range(f + n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def ema_np(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def true_range(h, l, c):
    pc = sh(c, 1)
    with np.errstate(invalid="ignore"):
        tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    tr[0] = h[0] - l[0]
    return tr


def supertrend(h, l, c, tr, factor=3.0, n=10):
    """Standard Supertrend (same logic as TradingView). direction -1 = bullish, +1 = bearish."""
    atr = rma(tr, n)
    hl2 = (h + l) / 2.0
    up = hl2 + factor * atr
    lo = hl2 - factor * atr
    N = len(c)
    fu = np.full(N, np.nan)
    fl = np.full(N, np.nan)
    st = np.full(N, np.nan)
    d = np.full(N, np.nan)
    for i in range(N):
        if np.isnan(atr[i]):
            continue
        prev_u = fu[i - 1] if i > 0 and not np.isnan(fu[i - 1]) else 0.0
        prev_l = fl[i - 1] if i > 0 and not np.isnan(fl[i - 1]) else 0.0
        prev_c = c[i - 1] if i > 0 else c[i]
        fl[i] = lo[i] if (lo[i] > prev_l or prev_c < prev_l) else prev_l
        fu[i] = up[i] if (up[i] < prev_u or prev_c > prev_u) else prev_u
        if i == 0 or np.isnan(atr[i - 1]):
            d[i] = 1.0
        elif st[i - 1] == prev_u:
            d[i] = -1.0 if c[i] > fu[i] else 1.0
        else:
            d[i] = 1.0 if c[i] < fl[i] else -1.0
        st[i] = fl[i] if d[i] == -1.0 else fu[i]
    return st, d


def rsi_calc(c, n=14):
    ch = np.diff(c, prepend=np.nan)
    up = rma(np.where(np.isnan(ch), np.nan, np.maximum(ch, 0.0)), n)
    dn = rma(np.where(np.isnan(ch), np.nan, np.maximum(-ch, 0.0)), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(dn == 0, 100.0, np.where(up == 0, 0.0, 100.0 - 100.0 / (1.0 + up / dn)))
    out[np.isnan(up) | np.isnan(dn)] = np.nan
    return out


# ================================ FEATURES ================================
def compute_features(df: pd.DataFrame, vix_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per day. Every state uses only data up to that day's close (no look-ahead).
    fwd*/mfe*/mae* columns look forward and are only for research."""
    idx = df.index
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(c)

    tr = true_range(h, l, c)
    atr = rma(tr, 14)
    st, st_dir = supertrend(h, l, c, tr, 3.0, 10)
    e20, e50, e200 = ema_np(c, 20), ema_np(c, 50), ema_np(c, 200)
    rsi = rsi_calc(c, 14)
    macd_line = ema_np(c, 12) - ema_np(c, 26)
    macd_sig = ema_np(macd_line, 9)
    hist = macd_line - macd_sig

    with np.errstate(invalid="ignore"):
        ema_bull = (c > e20) & (e20 > e50)
        ema_bear = (c < e20) & (e20 < e50)
        macd_bull = (macd_line > macd_sig) & (hist > 0)
        macd_bear = (macd_line < macd_sig) & (hist < 0)
        rsi_bull = rsi > 55
        rsi_bear = rsi < 45
        st_bull = st_dir < 0
        st_bear = st_dir > 0

    f = pd.DataFrame(index=idx)
    cs = pd.Series(c, index=idx)
    f["close"] = c
    f["high"] = h
    f["low"] = l
    f["chg_pct"] = (cs / cs.shift(1) - 1) * 100
    f["atr"] = atr
    f["atr_pct"] = atr / c * 100
    f["ema20"], f["ema50"], f["ema200"] = e20, e50, e200
    f["st_line"] = st
    f["rsi"] = rsi

    f["ema_state"] = np.where(ema_bull, "bull", np.where(ema_bear, "bear", "mixed"))
    f["lt_state"] = np.where(np.arange(n) < 200, "n/a", np.where(c > e200, "above", "below"))
    f["rsi_zone"] = np.select([np.isnan(rsi), rsi < 40, rsi < 50, rsi < 60], ["n/a", "<40", "40-50", "50-60"], default=">60")
    f["macd_state"] = np.where(macd_bull, "bull", np.where(macd_bear, "bear", "mixed"))
    f["st_state"] = np.where(np.isnan(st_dir), "n/a", np.where(st_bull, "bull", "bear"))

    W = BIAS_WEIGHTS
    bull = W["ema"] * ema_bull + W["rsi"] * rsi_bull + W["macd"] * macd_bull + W["st"] * st_bull
    bear = W["ema"] * ema_bear + W["rsi"] * rsi_bear + W["macd"] * macd_bear + W["st"] * st_bear
    score = bull - bear
    f["bull_score"], f["bear_score"], f["score"] = bull, bear, score
    bias_raw = np.where(score >= BIAS_GAP, "BULLISH", np.where(score <= -BIAS_GAP, "BEARISH", "NEUTRAL"))
    f["bias_raw"] = bias_raw
    f["strength_raw"] = np.where(bias_raw == "NEUTRAL", "Weak",
                                 np.where(np.maximum(bull, bear) >= STRONG_SCORE, "Strong", "Moderate"))
    f["align_bull"] = ema_bull.astype(int) + rsi_bull.astype(int) + macd_bull.astype(int) + st_bull.astype(int)
    f["align_bear"] = ema_bear.astype(int) + rsi_bear.astype(int) + macd_bear.astype(int) + st_bear.astype(int)

    # India VIX context
    if vix_df is not None and len(vix_df):
        v = vix_df["close"].reindex(idx, method="ffill")
        pct = v.rolling(252).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True) * 100
        f["vix"] = v.to_numpy()
        f["vix_pctl"] = pct.to_numpy()
        f["vix_chg5"] = ((v / v.shift(5) - 1) * 100).to_numpy()
    else:
        f["vix"] = np.nan
        f["vix_pctl"] = np.nan
        f["vix_chg5"] = np.nan
    f["vix_state"] = np.select([np.isnan(f["vix_pctl"]), f["vix_pctl"] < 33.3, f["vix_pctl"] > 66.7],
                               ["n/a", "low", "high"], default="mid")
    if VIX_GATE_ABS:
        gate = (f["vix"] < VIX_GATE_ABS) | f["vix"].isna()
        f["bias"] = np.where(gate, f["bias_raw"], "NEUTRAL")
    else:
        f["bias"] = f["bias_raw"]
    f["strength"] = np.where(f["bias"] == "NEUTRAL", "Weak", f["strength_raw"])

    # forward outcomes (research only)
    for H in FORWARD_H:
        f[f"fwd{H}"] = (cs.shift(-H) / cs - 1) * 100
    hs = pd.Series(h, index=idx)
    ls = pd.Series(l, index=idx)
    for H in (5, 10):
        mfe = pd.concat([hs.shift(-k) for k in range(1, H + 1)], axis=1).max(axis=1) / cs - 1
        mae = pd.concat([ls.shift(-k) for k in range(1, H + 1)], axis=1).min(axis=1) / cs - 1
        ok = f[f"fwd{H}"].notna()
        f[f"mfe{H}"] = (mfe * 100).where(ok)
        f[f"mae{H}"] = (mae * 100).where(ok)
    return f


# ================================= LEVELS =================================
def _pivots(hi, lo, cl):
    pp = (hi + lo + cl) / 3.0
    return dict(PP=pp, R1=2 * pp - lo, S1=2 * pp - hi, R2=pp + (hi - lo), S2=pp - (hi - lo))


def _select(items, close, below, n=3, tol=0.0010):
    pts = [(p, lab) for lab, p in items if (p < close if below else p > close)]
    pts.sort(reverse=below)               # nearest first
    merged = []
    for p, lab in pts:
        if merged and abs(p - merged[-1][0]) / close < tol:
            merged[-1][1].append(lab)
        else:
            merged.append([p, [lab]])
    return [(int(round(p)), "/".join(labs)) for p, labs in merged[:n]]


def compute_levels(df: pd.DataFrame, round_step: int = 100) -> dict:
    """Support / resistance relative to the last close. All levels are filtered by price,
    so supports are always below and resistances always above."""
    close = float(df["close"].iloc[-1])
    items = []
    if len(df) >= 2:
        p = df.iloc[-2]
        items += [("PDH", float(p["high"])), ("PDL", float(p["low"]))]
    wk = df.groupby(df.index.to_period("W-FRI")).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    if len(wk) >= 2:
        w = wk.iloc[-2]
        pv = _pivots(w["high"], w["low"], w["close"])
        items += [("W-PP", pv["PP"]), ("W-R1", pv["R1"]), ("W-S1", pv["S1"]), ("W-R2", pv["R2"]), ("W-S2", pv["S2"])]
    mo = df.groupby(df.index.to_period("M")).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    if len(mo) >= 2:
        m = mo.iloc[-2]
        pv = _pivots(m["high"], m["low"], m["close"])
        items += [("M-PP", pv["PP"]), ("M-R1", pv["R1"]), ("M-S1", pv["S1"])]
    items += [("20D-H", float(df["high"].tail(20).max())), ("20D-L", float(df["low"].tail(20).min())),
              ("52W-H", float(df["high"].tail(252).max())), ("52W-L", float(df["low"].tail(252).min()))]
    base = int(close // round_step) * round_step
    for k in (-1, 0, 1, 2):
        lvl = base + k * round_step
        items.append((f"{lvl}", float(lvl)))
    return dict(
        supports=_select(items, close, below=True),
        resistances=_select(items, close, below=False),
    )


# ============================ OPTION HELPERS ============================
def atm_strike(close: float, step: int) -> int:
    return int(round(close / step) * step)


def size_calc(premium: float, lot: int, capital: float = CAPITAL, risk_pct: float = MAX_RISK_PCT,
              sl_pct: float = SL_PREMIUM_PCT) -> dict:
    """Whole lots allowed so that a stop at sl_pct of premium loses at most risk_pct of capital."""
    max_risk = capital * risk_pct
    risk_per_lot = premium * sl_pct * lot
    lots = int(max_risk // risk_per_lot) if risk_per_lot > 0 else 0
    return dict(max_risk=max_risk, risk_per_lot=risk_per_lot, lots=lots,
                capital_needed=premium * lot * lots)
