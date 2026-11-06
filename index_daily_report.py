#!/usr/bin/env python3
"""
index_daily_report.py - daily Nifty 50 / Bank Nifty report sent to your Telegram bot.

WHAT IT DOES (once per run, intended for ~15:45 IST on trading days)
  * loads daily candles (completed bars only) for each index + India VIX
  * computes the trend / momentum state, the legacy bias score, VIX percentile
  * builds correct support / resistance levels (always below / above price)
  * lists what CHANGED since the previous session
  * logs the call (one row per index per date, no duplicates) and fills in the later
    1/3/5-day outcomes, then reports its OWN live track record
  * sends the message to Telegram (retries, long-message splitting, plain-text fallback)

SETUP
  1. Copy env_example.txt to .env and fill TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
  2. pip install pandas numpy requests yfinance
  3. Run:   python index_daily_report.py --dry-run          (prints, sends nothing)
            python index_daily_report.py                    (sends to Telegram)
            python index_daily_report.py --test-telegram    (sends a short test message)
            python index_daily_report.py --size 145 --index "NIFTY 50"   (position size)
  4. Schedule run_index_report.bat with Windows Task Scheduler (weekdays ~15:45 IST).

The bias score is UNVALIDATED until index_research.py supports it - the message says so.
This is research information, not advice.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import time

import numpy as np
import pandas as pd

import index_core as core

LOG_COLS = ["date", "index", "close", "bias", "strength", "score", "st", "rsi", "vix", "vix_pctl",
            "source", "ret_1d", "ret_3d", "ret_5d"]


# ------------------------------------------------------------------ Telegram
def tg_base() -> str:
    return os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")


def chunk_text(text: str, limit: int = 3800) -> list[str]:
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > limit and cur:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts or [text]


def strip_tags(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def send_telegram(text: str, token: str, chat_id: str, retries: int = 3, timeout: int = 15) -> bool:
    import requests

    url = f"{tg_base()}/bot{token}/sendMessage"
    all_ok = True
    for part in chunk_text(text):
        sent = False
        plain = False
        for attempt in range(1, retries + 1):
            payload = dict(chat_id=chat_id, text=strip_tags(part) if plain else part, disable_web_page_preview=True)
            if not plain:
                payload["parse_mode"] = "HTML"
            try:
                r = requests.post(url, json=payload, timeout=timeout)
                ok = r.status_code == 200 and r.json().get("ok")
                if ok:
                    sent = True
                    break
                print(f"[telegram] attempt {attempt}: HTTP {r.status_code} {r.text[:160]}")
                if r.status_code == 400 and not plain:
                    plain = True            # probably a formatting problem: resend as plain text
                    continue
            except Exception as e:
                print(f"[telegram] attempt {attempt} failed: {e}")
            time.sleep(min(2 * attempt, 6))
        all_ok &= sent
    return all_ok


# ---------------------------------------------------------------------- log
def read_log(path: str) -> pd.DataFrame:
    if os.path.isfile(path):
        try:
            log = pd.read_csv(path)
            for c in LOG_COLS:
                if c not in log.columns:
                    log[c] = np.nan
            return log[LOG_COLS]
        except Exception as e:
            print(f"[warn] could not read {path}: {e}")
    return pd.DataFrame(columns=LOG_COLS)


def upsert_log(log: pd.DataFrame, row: dict) -> pd.DataFrame:
    if len(log):
        log = log[~((log["date"].astype(str) == row["date"]) & (log["index"] == row["index"]))]
    new = pd.DataFrame([row], columns=LOG_COLS)
    return new if log.empty else pd.concat([log, new], ignore_index=True)


def fill_outcomes(log: pd.DataFrame, name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Fill ret_1d/3d/5d (%) for logged rows once enough later closes exist."""
    closes = df["close"].to_numpy(float)
    index = df.index
    for i in log.index[log["index"] == name]:
        d = pd.Timestamp(str(log.at[i, "date"]))
        pos = index.get_indexer([d])[0]
        if pos < 0:
            continue
        for H, col in ((1, "ret_1d"), (3, "ret_3d"), (5, "ret_5d")):
            if pd.isna(log.at[i, col]) and pos + H < len(closes):
                log.at[i, col] = round((closes[pos + H] / closes[pos] - 1) * 100, 3)
    return log


def track_record(log: pd.DataFrame, name: str, n: int = 20) -> str:
    live = log[(log["index"] == name) & (log["source"] == "live")]
    parts = []
    for bias, win in (("BULLISH", lambda x: x > 0), ("BEARISH", lambda x: x < 0)):
        g = live[(live["bias"] == bias) & live["ret_5d"].notna()].tail(n)
        if len(g):
            hit = win(g["ret_5d"].astype(float)).mean() * 100
            parts.append(f"{bias.lower()} calls {len(g)} -> right {hit:.0f}%")
    if not parts:
        return "building (no live calls with a 5-day outcome yet)"
    return "5-day, live: " + "; ".join(parts)


# ------------------------------------------------------------------ analysis
def esc(x) -> str:
    return html.escape(str(x))


def analyse_index(name: str, spec: dict, args, vix_df) -> dict:
    df = core.load_prices(spec, args.source, args.csv_dir, args.years)
    if args.asof:
        df = df[df.index <= pd.Timestamp(args.asof)]
    elif not args.include_partial:
        df = core.drop_partial(df)
    if len(df) < 300:
        raise ValueError(f"only {len(df)} bars of data")
    feats = core.compute_features(df, vix_df)
    last, prev = feats.iloc[-1], feats.iloc[-2]
    lv = core.compute_levels(df, spec["round_step"])

    changes = []
    if last["bias"] != prev["bias"]:
        changes.append(f"bias {prev['bias']} -> {last['bias']}")
    if last["st_state"] != prev["st_state"]:
        changes.append(f"Supertrend flipped to {'green' if last['st_state'] == 'bull' else 'red'}")
    if last["ema_state"] != prev["ema_state"]:
        changes.append(f"EMA stack {prev['ema_state']} -> {last['ema_state']}")
    if last["lt_state"] != prev["lt_state"]:
        changes.append(f"close moved {last['lt_state']} the 200 EMA")
    if last["macd_state"] != prev["macd_state"]:
        changes.append(f"MACD {prev['macd_state']} -> {last['macd_state']}")
    if last["vix_state"] != prev["vix_state"] and last["vix_state"] != "n/a":
        changes.append(f"VIX regime {prev['vix_state']} -> {last['vix_state']}")

    return dict(name=name, spec=spec, df=df, feats=feats, last=last, levels=lv, changes=changes)


def build_block(info: dict, track: str, show_option: bool) -> str:
    last, spec, name = info["last"], info["spec"], info["name"]
    date = info["feats"].index[-1].date()
    close = float(last["close"])
    arrow = "+" if last["chg_pct"] >= 0 else ""
    bias, strength = last["bias"], last["strength"]
    tag = {"BULLISH": "🟢", "BEARISH": "🔴"}.get(bias, "⚪")
    ema_txt = {"bull": "close > EMA20 > EMA50", "bear": "close < EMA20 < EMA50", "mixed": "EMA20/50 mixed"}[last["ema_state"]]
    lt_txt = {"above": "above 200 EMA", "below": "below 200 EMA", "n/a": "200 EMA n/a"}[last["lt_state"]]
    st_txt = "green" if last["st_state"] == "bull" else "red"

    lines = [f"<b>{esc(name)}</b>  {date}",
             f"Close <code>{close:,.1f}</code> ({arrow}{last['chg_pct']:.2f}%)   ATR {last['atr_pct']:.2f}%",
             f"{tag} <b>{esc(bias)}</b> ({esc(strength)})  score {last['score']:+.1f}"]
    if bias == "NEUTRAL" and last["bias_raw"] != "NEUTRAL":
        lines[-1] += f"  [raw {esc(last['bias_raw'])}, blocked by VIX gate]"
    lines += [f"Trend: {ema_txt}, {lt_txt}, Supertrend {st_txt}",
              f"Momentum: RSI {last['rsi']:.0f} ({esc(last['rsi_zone'])}), MACD {esc(last['macd_state'])}"]
    if not np.isnan(last["vix"]):
        lines.append(f"India VIX {last['vix']:.2f}  (1y percentile {last['vix_pctl']:.0f}%, "
                     f"5d change {last['vix_chg5']:+.1f}%) - {esc(last['vix_state'])}")
    lines.append("Changes: " + (esc("; ".join(info["changes"])) if info["changes"] else "none vs previous session"))
    lv = info["levels"]
    res = ", ".join(f"{p:,} ({esc(l)})" for p, l in reversed(lv["resistances"])) or "-"
    sup = ", ".join(f"{p:,} ({esc(l)})" for p, l in lv["supports"]) or "-"
    lines += [f"Resistance: {res}", f"Support: {sup}", f"Track record - {esc(track)}"]
    if show_option:
        atm = core.atm_strike(close, spec["strike_step"])
        if bias == "BULLISH":
            idea = f"{atm} CE"
        elif bias == "BEARISH":
            idea = f"{atm} PE"
        else:
            idea = None
        if idea:
            lines.append(f"Option idea (unvalidated): ATM {idea}; stop {int(core.SL_PREMIUM_PCT * 100)}% of premium; "
                         f"choose the expiry yourself; size: --size &lt;premium&gt; --index \"{esc(name)}\"")
        else:
            lines.append("Option idea: none (neutral)")
    return "\n".join(lines)


def build_message(infos: list[dict], log: pd.DataFrame, errors: list[str], stale: str | None, show_option: bool) -> str:
    now = dt.datetime.now(core.IST)
    head = f"<b>INDEX REPORT</b>  {now:%Y-%m-%d %H:%M} IST"
    parts = [head]
    if stale:
        parts.append(f"⚠️ {esc(stale)}")
    for info in infos:
        parts.append(build_block(info, track_record(log, info["name"]), show_option))
    for e in errors:
        parts.append(f"⚠️ {esc(e)}")
    foot = ("Bias rule: " + ("validated by research" if core.BIAS_VALIDATED else
                            "NOT validated yet - run index_research.py before relying on it") +
            ". Research information, not advice.")
    parts.append(f"<i>{esc(foot)}</i>")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------- main
def main():
    core.ensure_utf8()
    core.load_env_file(".env")
    ap = argparse.ArgumentParser(description="Daily index report -> Telegram")
    ap.add_argument("--source", choices=["yfinance", "csv", "angel"], default="yfinance")
    ap.add_argument("--csv-dir", default="data")
    ap.add_argument("--years", type=int, default=6, help="history to load (6 is enough for the daily report)")
    ap.add_argument("--indices", default=",".join(core.INDICES))
    ap.add_argument("--dry-run", action="store_true", help="print only, send nothing")
    ap.add_argument("--no-log", action="store_true", help="do not write the signal log")
    ap.add_argument("--include-partial", action="store_true", help="keep today's candle even if the market is open")
    ap.add_argument("--asof", default=None, help="YYYY-MM-DD, run as if that were the last session (testing)")
    ap.add_argument("--log-file", default="index_signal_log.csv")
    ap.add_argument("--out-dir", default="index_reports")
    ap.add_argument("--no-option-idea", action="store_true")
    ap.add_argument("--test-telegram", action="store_true")
    ap.add_argument("--size", type=float, default=None, help="option premium: print position size and exit")
    ap.add_argument("--index", default="NIFTY 50", help="index name for --size")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")

    if args.size is not None:
        spec = core.INDICES[args.index]
        r = core.size_calc(args.size, spec["lot"])
        print(f"{args.index}: lot {spec['lot']} (verify), premium {args.size:.1f}")
        print(f"Max risk Rs {r['max_risk']:,.0f}; risk per lot at {int(core.SL_PREMIUM_PCT*100)}% stop Rs {r['risk_per_lot']:,.0f}")
        print(f"Lots allowed: {r['lots']}   capital needed Rs {r['capital_needed']:,.0f}")
        if r["lots"] == 0:
            print("Even 1 lot exceeds the risk limit - skip, or look at a cheaper premium / a defined-risk spread.")
        return

    if args.test_telegram:
        ok = send_telegram("Index report bot: test message OK.", token, chat) if token and chat else False
        print("sent" if ok else "NOT sent (check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return

    # --- load data
    try:
        vix_df = core.load_prices(core.VIX_SYMBOL, args.source, args.csv_dir, args.years)
        if args.asof:
            vix_df = vix_df[vix_df.index <= pd.Timestamp(args.asof)]
    except Exception as e:
        print(f"[warn] India VIX unavailable: {e}")
        vix_df = None

    infos, errors = [], []
    for name in [s.strip() for s in args.indices.split(",") if s.strip()]:
        if name not in core.INDICES:
            errors.append(f"unknown index {name}")
            continue
        try:
            infos.append(analyse_index(name, core.INDICES[name], args, vix_df))
        except Exception as e:
            errors.append(f"{name}: {e}")

    # --- stale-data check (no holiday calendar: it only warns)
    stale = None
    if infos and not args.asof:
        expected = core.expected_last_session()
        last_date = max(i["feats"].index[-1].date() for i in infos)
        if last_date < expected:
            stale = (f"latest candle is {last_date}, expected {expected} - market holiday, "
                     "or the data source has not updated yet")

    # --- log + outcomes
    log = read_log(args.log_file)
    if not args.no_log:
        for info in infos:
            last = info["last"]
            log = upsert_log(log, dict(
                date=str(info["feats"].index[-1].date()), index=info["name"], close=round(float(last["close"]), 2),
                bias=last["bias"], strength=last["strength"], score=round(float(last["score"]), 2),
                st=last["st_state"], rsi=round(float(last["rsi"]), 1),
                vix=round(float(last["vix"]), 2) if not np.isnan(last["vix"]) else np.nan,
                vix_pctl=round(float(last["vix_pctl"]), 1) if not np.isnan(last["vix_pctl"]) else np.nan,
                source="asof-test" if args.asof else "live", ret_1d=np.nan, ret_3d=np.nan, ret_5d=np.nan))
    for info in infos:
        log = fill_outcomes(log, info["name"], info["df"])
    if not args.no_log and not args.dry_run:
        log.to_csv(args.log_file, index=False)
    elif not args.no_log and args.dry_run and args.asof:
        log.to_csv(args.log_file, index=False)   # allows testing the outcome fill

    msg = build_message(infos, log, errors, stale, not args.no_option_idea)
    print(strip_tags(msg))
    os.makedirs(args.out_dir, exist_ok=True)
    stamp = dt.datetime.now(core.IST).strftime("%Y%m%d")
    with open(os.path.join(args.out_dir, f"index_report_{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write(strip_tags(msg))

    if args.dry_run:
        print("\n[dry-run] nothing sent")
        return
    if not token or not chat:
        print("\n[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - message not sent")
        return
    ok = send_telegram(msg, token, chat)
    print("\n[telegram] sent" if ok else "\n[telegram] FAILED after retries")


if __name__ == "__main__":
    main()
