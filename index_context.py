#!/usr/bin/env python3
"""
index_context.py - the "outside the chart" clues for Nifty: GIFT Nifty, global markets, news.

* Global cues  : S&P 500, Nasdaq, Asia, Europe, dollar index, US yield, Brent, USD/INR, US VIX
                 (Yahoo Finance). Each move is scaled by its own recent volatility and combined
                 into one score. global_impact() measures how much each one has historically
                 mattered for Nifty (same-day and next-day correlation).
* GIFT Nifty   : NO verified free automatic source was found. It works from a price you type
                 (app) or set (GIFT_NIFTY_PRICE env var), from a Yahoo ticker you find
                 (GIFT_YF_TICKER), or from your own broker feed (edit load_gift_custom()).
* News         : Google News RSS headlines, scored by a crude keyword list. A clue only.

Every part fails soft: if a source is down the report says so and carries on.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import numpy as np
import pandas as pd

import index_core as core

# ------------------------------------------------------------------ config
# name, yahoo ticker, region, weight, sign (+1 = a rise is good for Indian stocks, -1 = a rise is bad)
GLOBAL = [
    ("S&P 500", "^GSPC", "US", 1.0, +1),
    ("Nasdaq", "^IXIC", "US", 0.6, +1),
    ("Nikkei 225", "^N225", "Asia", 0.5, +1),
    ("Hang Seng", "^HSI", "Asia", 0.5, +1),
    ("KOSPI", "^KS11", "Asia", 0.3, +1),
    ("Shanghai", "000001.SS", "Asia", 0.2, +1),
    ("FTSE 100", "^FTSE", "Europe", 0.3, +1),
    ("DAX", "^GDAXI", "Europe", 0.4, +1),
    ("Dollar Index", "DX-Y.NYB", "Macro", 0.5, -1),
    ("US 10Y yield", "^TNX", "Macro", 0.3, -1),
    ("Brent crude", "BZ=F", "Macro", 0.5, -1),
    ("USD/INR", "INR=X", "Macro", 0.4, -1),
    ("US VIX", "^VIX", "Macro", 0.4, -1),
]

NEWS_QUERIES = [
    'Nifty OR Sensex OR "Indian stock market" when:1d',
    '"Wall Street" OR "Federal Reserve" OR "crude oil" OR "Asian markets" when:1d',
]

POS_WORDS = ["rally", "surge", "gain", "jump", "record high", "rebound", "rise", "soar", "upgrade", "rate cut",
             "inflow", "buying", "ceasefire", "trade deal", "beat", "strong", "optimism", "recovery", "boost"]
NEG_WORDS = ["fall", "slump", "crash", "plunge", "sell-off", "selloff", "tumble", "drop", "decline", "slide", "rate hike",
             "outflow", "selling", "tariff", "war", "sanction", "inflation", "downgrade", "weak", "miss", "fear",
             "probe", "ban", "recession", "default", "attack"]
THEMES = {
    "US Fed / rates": ["fed ", "fomc", "powell", "treasury yield", "rate cut", "rate hike"],
    "Crude oil": ["crude", "brent", "oil price", "opec"],
    "FII / DII flows": ["fii", "fpi", "dii", "foreign investor", "foreign fund"],
    "RBI / India rates": ["rbi", "repo rate", "mpc"],
    "Earnings": ["earnings", "q1", "q2", "q3", "q4", "quarterly result", "profit"],
    "Geopolitics / trade": ["war", "tariff", "sanction", "ceasefire", "trade deal", "geopolit", "attack"],
    "Rupee": ["rupee"],
    "Global markets": ["wall street", "dow", "nasdaq", "asian market", "european market"],
}


# ----------------------------------------------------------------- loaders
def load_series(ticker: str, years: int = 1) -> pd.DataFrame:
    """Daily candles from Yahoo. Replaced in tests; edit if you use another data source."""
    return core.load_yf(ticker, years)


def load_global(years: int = 1) -> dict:
    out = {}
    for name, tk, *_ in GLOBAL:
        try:
            out[name] = load_series(tk, years)
        except Exception as e:
            print(f"[context] {name} ({tk}) unavailable: {e}")
    return out


# ------------------------------------------------------------ global cues
def _pct(c: pd.Series) -> pd.Series:
    return (c / c.shift(1) - 1) * 100


def global_snapshot(series: dict) -> tuple[pd.DataFrame, float | None, str]:
    rows, num, den = [], 0.0, 0.0
    for name, tk, region, w, sign in GLOBAL:
        df = series.get(name)
        if df is None or len(df) < 30:
            continue
        c = df["close"].astype(float)
        r = _pct(c)
        last = float(r.iloc[-1])
        sd = float(r.tail(60).std())
        z = float(np.clip(last / sd, -3, 3)) if sd and sd > 0 else 0.0
        rows.append(dict(name=name, region=region, date=c.index[-1].date(), last=float(c.iloc[-1]),
                         chg_pct=last, chg5_pct=float((c.iloc[-1] / c.iloc[-6] - 1) * 100), z=z, weight=w, sign=sign))
        num += w * sign * z
        den += w
    if not rows:
        return pd.DataFrame(), None, "unavailable"
    comp = num / den
    label = "Positive" if comp >= 0.4 else "Negative" if comp <= -0.4 else "Neutral / mixed"
    return pd.DataFrame(rows), comp, label


def global_impact(nifty: pd.DataFrame, series: dict, thr: float = 1.0) -> pd.DataFrame:
    """How much has each global series mattered for Nifty?
    corr_next_day : global move on date D vs Nifty's move on the NEXT Indian session (relevant for US markets,
                    which close after India).
    corr_same_day : same calendar date (relevant for Asia / Europe, which overlap Indian hours).
    after_down/up : Nifty's average next-session move after a global move of at least thr %."""
    n = _pct(nifty["close"].astype(float)).dropna()
    nd = n.rename("nifty").rename_axis("date").reset_index().sort_values("date")
    rows = []
    for name, tk, region, w, sign in GLOBAL:
        df = series.get(name)
        if df is None or len(df) < 250:
            continue
        g = _pct(df["close"].astype(float)).dropna()
        gd = g.rename("g").rename_axis("gdate").reset_index().sort_values("gdate")
        same = pd.merge(nd, gd, left_on="date", right_on="gdate", how="inner")
        nxt = pd.merge_asof(nd, gd, left_on="date", right_on="gdate", direction="backward", allow_exact_matches=False)
        nxt = nxt.dropna()
        nxt = nxt[(nxt["date"] - nxt["gdate"]).dt.days <= 5]
        down, up = nxt[nxt["g"] <= -thr], nxt[nxt["g"] >= thr]
        rows.append(dict(
            name=name, region=region, days=len(nxt),
            corr_same_day=same["nifty"].corr(same["g"]) if len(same) > 30 else np.nan,
            corr_next_day=nxt["nifty"].corr(nxt["g"]) if len(nxt) > 30 else np.nan,
            n_down=len(down), nifty_after_down_pct=down["nifty"].mean() if len(down) else np.nan,
            n_up=len(up), nifty_after_up_pct=up["nifty"].mean() if len(up) else np.nan))
    return pd.DataFrame(rows)


# --------------------------------------------------------------- GIFT Nifty
def load_gift_custom():
    """Plug in your own GIFT Nifty source here (Angel One / Kite / another broker feed).
    Return dict(price=float, change_pct=float or None, source='...') or None."""
    return None


def _num(x):
    try:
        return float(str(x).strip())
    except Exception:
        return None


_NUM = r"([\d,]+(?:\.\d+)?)"
_SGN = r"([+-]?\s?[\d,]+(?:\.\d+)?)"


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _f(x: str) -> float:
    return float(x.replace(",", "").replace(" ", ""))


def _parse_angel(text: str):
    """Angel One public page: '<price> <+/-pts> (<pct>%) Price as of 07 Nov 2026 08:15 ... Previous Close <x>'."""
    m = re.search(_NUM + r"\s+" + _SGN + r"\s*\(\s*([+-]?[\d.]+)\s*%\s*\)\s*Price as of\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+(\d{1,2}:\d{2})", text)
    if not m:
        return None
    pts, pct = _f(m.group(2)), abs(_f(m.group(3)))
    asof = dt.datetime.strptime(f"{m.group(4)} {m.group(5)}", "%d %b %Y %H:%M").replace(tzinfo=core.IST)
    prev = re.search(r"Previous Close\s*" + _NUM, text)
    return dict(price=_f(m.group(1)), change_pct=pct if pts >= 0 else -pct,
                prev_close=_f(prev.group(1)) if prev else None, source="Angel One page", asof=asof)


def _parse_upstox(text: str):
    """Upstox public page: '<price> <+/-pts> (<pct>%) Today Last updated on 05 Nov, 2026 | 01:00 IST ... Previous session close <x>'."""
    m = re.search(_NUM + r"\s+" + _SGN + r"\s*\(\s*([+-]?[\d.]+)\s*%\s*\)\s*Today\s*Last updated on\s*(\d{1,2}\s+[A-Za-z]{3},?\s+\d{4})\s*\|\s*(\d{1,2}:\d{2})\s*IST", text)
    if not m:
        return None
    pts, pct = _f(m.group(2)), abs(_f(m.group(3)))
    asof = dt.datetime.strptime(f"{m.group(4).replace(',', '')} {m.group(5)}", "%d %b %Y %H:%M").replace(tzinfo=core.IST)
    prev = re.search(r"Previous session close\s*" + _NUM, text)
    return dict(price=_f(m.group(1)), change_pct=pct if pts >= 0 else -pct,
                prev_close=_f(prev.group(1)) if prev else None, source="Upstox page", asof=asof)


GIFT_PAGES = [
    ("Angel One", "https://www.angelone.in/indices/gift-nifty", _parse_angel),
    ("Upstox", "https://upstox.com/gift-nifty/", _parse_upstox),
]


def gift_from_web(fetcher=None, now=None):
    """Read GIFT Nifty from the public quote pages of two brokers and keep the freshest reading.
    These are ordinary web pages, not an official API: they can change, block cloud servers or have
    terms that restrict automated use. It is called at most a couple of times a day. Switch off with GIFT_WEB=0."""
    if os.environ.get("GIFT_WEB", "1") == "0":
        return None
    now = now or dt.datetime.now(core.IST)
    if fetcher is None:
        import requests

        def fetcher(url):
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; personal-market-notes)",
                                           "Accept-Language": "en-IN,en;q=0.9"}, timeout=10)
            r.raise_for_status()
            return r.text
    found = []
    for name, url, parser in GIFT_PAGES:
        try:
            res = parser(_strip_html(fetcher(url)))
            if res:
                found.append(res)
            else:
                print(f"[context] {name}: price not found on the page (layout changed or blocked?)")
        except Exception as e:
            print(f"[context] {name} GIFT page failed: {e}")
    if not found:
        return None
    best = max(found, key=lambda r: r["asof"])
    best["stale"] = (now - best["asof"]) > dt.timedelta(hours=18)
    return best


def fetch_gift_nifty():
    price = _num(os.environ.get("GIFT_NIFTY_PRICE", ""))
    if price:
        return dict(price=price, change_pct=_num(os.environ.get("GIFT_NIFTY_CHANGE_PCT", "")), source="entered manually")
    tk = os.environ.get("GIFT_YF_TICKER", "").strip()
    if tk:
        try:
            df = load_series(tk, 1)
            c = df["close"].astype(float)
            return dict(price=float(c.iloc[-1]), change_pct=float((c.iloc[-1] / c.iloc[-2] - 1) * 100), source=f"Yahoo {tk}")
        except Exception as e:
            print(f"[context] GIFT ticker {tk} failed: {e}")
    try:
        custom = load_gift_custom()
        if custom:
            return custom
    except Exception as e:
        print(f"[context] load_gift_custom failed: {e}")
    return gift_from_web()


def implied_gap(gift_price: float, prev_close: float) -> dict:
    pts = gift_price - prev_close
    pct = pts / prev_close * 100
    label = ("strong gap-up" if pct >= 0.5 else "mild gap-up" if pct >= 0.2 else
             "strong gap-down" if pct <= -0.5 else "mild gap-down" if pct <= -0.2 else "roughly flat")
    return dict(points=pts, pct=pct, label=label)


# --------------------------------------------------------------------- news
def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def parse_rss(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if " - " in title:
            head, tail = title.rsplit(" - ", 1)
            title, src = head.strip(), (src or tail.strip())
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=dt.timezone.utc)
        except Exception:
            pub = None
        if title:
            items.append(dict(title=title, source=src, published=pub, link=(it.findtext("link") or "").strip()))
    return items


def score_title(title: str) -> tuple[int, list[str]]:
    t = " " + title.lower() + " "
    pos = sum(1 for w in POS_WORDS if w in t)
    neg = sum(1 for w in NEG_WORDS if w in t)
    tone = 1 if pos > neg else -1 if neg > pos else 0
    tags = [k for k, words in THEMES.items() if any(w in t for w in words)]
    return tone, tags


def fetch_news(queries=None, hours: int = 24, per_query: int = 8, fetcher=None) -> list[dict]:
    """Recent headlines (Google News RSS). fetcher(url) -> xml text can be injected for tests."""
    queries = queries or NEWS_QUERIES
    if fetcher is None:
        import requests

        def fetcher(url):
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
            return r.text
    now = dt.datetime.now(dt.timezone.utc)
    seen, out = set(), []
    for q in queries:
        try:
            xml_text = fetcher(f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en")
            items = parse_rss(xml_text)
        except Exception as e:
            print(f"[context] news query failed: {e}")
            continue
        taken = 0
        for it in items:
            key = _norm(it["title"])[:80]
            if key in seen:
                continue
            if it["published"] is not None and now - it["published"] > dt.timedelta(hours=hours):
                continue
            seen.add(key)
            it["tone"], it["tags"] = score_title(it["title"])
            out.append(it)
            taken += 1
            if taken >= per_query:
                break
    out.sort(key=lambda x: x["published"] or dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc), reverse=True)
    return out


def news_summary(items: list[dict], max_notes: int = 5) -> dict:
    if not items:
        return dict(label="no headlines", score=0.0, n=0, pos=0, neg=0, themes=[], notes=[], items=[])
    pos = sum(1 for i in items if i["tone"] > 0)
    neg = sum(1 for i in items if i["tone"] < 0)
    score = (pos - neg) / len(items)
    label = "mildly positive" if score >= 0.2 else "mildly negative" if score <= -0.2 else "mixed / neutral"
    counts: dict[str, int] = {}
    for i in items:
        for t in i["tags"]:
            counts[t] = counts.get(t, 0) + 1
    themes = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
    mark = {1: "+", -1: "-", 0: "="}
    notes = [f"[{mark[i['tone']]}] {i['title'][:105]}" + (f" ({i['source']})" if i["source"] else "") for i in items[:max_notes]]
    return dict(label=label, score=score, n=len(items), pos=pos, neg=neg, themes=themes, notes=notes, items=items)


# --------------------------------------------------------------- collection
def collect_context(prev_close: float | None = None, want_news: bool = True, gift_override: dict | None = None) -> dict:
    ctx = dict(global_df=None, composite=None, global_label=None, gift=None, gap=None, news=None, errors=[])
    try:
        series = load_global(1)
        df, comp, label = global_snapshot(series)
        ctx.update(global_df=df, composite=comp, global_label=label)
        if df.empty:
            ctx["errors"].append("global indices unavailable")
    except Exception as e:
        ctx["errors"].append(f"global indices: {e}")
    gift = gift_override or fetch_gift_nifty()
    if gift and prev_close:
        gap = implied_gap(gift["price"], prev_close)
        if abs(gap["pct"]) > 8:
            ctx["errors"].append(f"GIFT value {gift['price']:,.0f} is more than 8% from the Nifty close - ignored (parse error or stale?)")
        else:
            ctx["gift"], ctx["gap"] = gift, gap
    if want_news:
        try:
            ctx["news"] = news_summary(fetch_news())
        except Exception as e:
            ctx["errors"].append(f"news: {e}")
    return ctx


def context_html(ctx: dict | None) -> str:
    if not ctx:
        return ""
    e = html.escape
    lines = ["<b>GLOBAL, GIFT &amp; NEWS</b> (clues only)"]
    g, gap = ctx.get("gift"), ctx.get("gap")
    if g and gap:
        chg = f", GIFT {g['change_pct']:+.2f}% on its own close" if g.get("change_pct") is not None else ""
        when = f", as of {g['asof']:%d %b %H:%M} IST" if g.get("asof") else ""
        old = " - STALE (older than 18h; market closed or page not updating)" if g.get("stale") else ""
        lines.append(f"GIFT Nifty <code>{g['price']:,.0f}</code>: {gap['pct']:+.2f}% ({gap['points']:+,.0f} pts) vs Nifty close - "
                     f"{e(gap['label'])}{chg} [{e(g['source'])}{when}]{old}. Includes futures basis, so a small premium is normal.")
    else:
        lines.append("GIFT Nifty: not available (enter it in the app, or see README) - check your broker app.")
    gd = ctx.get("global_df")
    if gd is not None and len(gd):
        lines.append(f"Global cues: <b>{e(str(ctx['global_label']))}</b> (score {ctx['composite']:+.2f})")
        for region in ("US", "Asia", "Europe", "Macro"):
            part = gd[gd["region"] == region]
            if len(part):
                lines.append(f"{region}: " + ", ".join(f"{e(r['name'])} {r['chg_pct']:+.1f}%" for _, r in part.iterrows()))
    else:
        lines.append("Global cues: unavailable.")
    n = ctx.get("news")
    if n and n["n"]:
        th = f" Themes: {e(', '.join(n['themes']))}." if n["themes"] else ""
        lines.append(f"News tone: {e(n['label'])} ({n['neg']} negative / {n['pos']} positive of {n['n']} headlines).{th}")
        lines += [e(x) for x in n["notes"]]
        lines.append("<i>Keyword tone is crude: read the headlines, do not trade the label.</i>")
    elif n is not None:
        lines.append("News: no headlines found.")
    if ctx.get("track"):
        lines.append(f"Context track record: {e(ctx['track'])}")
    for err in ctx.get("errors", []):
        lines.append(f"⚠️ {e(err)}")
    return "\n".join(lines)


# ------------------------------------------------------------- context log
CTX_COLS = ["date", "gift_price", "gift_change_pct", "nifty_prev_close", "implied_gap_pct", "global_score",
            "global_label", "news_score", "source", "actual_gap_pct", "nifty_day_ret_pct"]


def read_ctx_log(path: str) -> pd.DataFrame:
    if os.path.isfile(path):
        try:
            log = pd.read_csv(path)
            for c in CTX_COLS:
                if c not in log.columns:
                    log[c] = np.nan
            return log[CTX_COLS]
        except Exception as e:
            print(f"[warn] could not read {path}: {e}")
    return pd.DataFrame(columns=CTX_COLS)


def upsert_ctx_log(log: pd.DataFrame, row: dict) -> pd.DataFrame:
    if len(log):
        log = log[log["date"].astype(str) != row["date"]]
    new = pd.DataFrame([row], columns=CTX_COLS)
    return new if log.empty else pd.concat([log, new], ignore_index=True)


def fill_ctx_outcomes(log: pd.DataFrame, nifty: pd.DataFrame) -> pd.DataFrame:
    for i in log.index:
        d = pd.Timestamp(str(log.at[i, "date"]))
        pos = nifty.index.get_indexer([d])[0]
        if pos > 0 and pd.isna(log.at[i, "actual_gap_pct"]):
            prev = float(nifty["close"].iloc[pos - 1])
            log.at[i, "actual_gap_pct"] = round((float(nifty["open"].iloc[pos]) / prev - 1) * 100, 3)
            log.at[i, "nifty_day_ret_pct"] = round((float(nifty["close"].iloc[pos]) / prev - 1) * 100, 3)
    return log


def ctx_track_record(log: pd.DataFrame) -> str:
    parts = []
    g = log[log["implied_gap_pct"].notna() & log["actual_gap_pct"].notna()]
    g = g[g["implied_gap_pct"].abs() >= 0.15]
    if len(g):
        hit = (np.sign(g["implied_gap_pct"].astype(float)) == np.sign(g["actual_gap_pct"].astype(float))).mean() * 100
        parts.append(f"GIFT gap direction matched the real open in {hit:.0f}% of {len(g)} sessions")
    s = log[log["global_score"].notna() & log["nifty_day_ret_pct"].notna()]
    s = s[s["global_score"].abs() >= 0.4]
    if len(s):
        hit = (np.sign(s["global_score"].astype(float)) == np.sign(s["nifty_day_ret_pct"].astype(float))).mean() * 100
        parts.append(f"global score direction matched Nifty's day in {hit:.0f}% of {len(s)} sessions")
    return "; ".join(parts) if parts else "building (needs live pre-market runs)"
