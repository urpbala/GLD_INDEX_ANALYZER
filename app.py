"""
Index Analyzer - Streamlit web app (Nifty 50 / Bank Nifty), mobile friendly.

Tabs
  Today     - state of each index, levels, VIX, track record, optional "send to Telegram"
  Research  - does the bias carry information? (forward returns by state, by year)
  Size      - option position-size calculator
  Log       - the signal log written by the scheduled GitHub job

Deploy: see README.md (GitHub + Streamlit Community Cloud). Educational tool only, not advice.
"""
import datetime as dt
import os
from types import SimpleNamespace

import numpy as np
import pandas as pd
import streamlit as st

import index_context as ictx
import index_core as core
import index_daily_report as dr
import index_research as rs

LOG_FILE = "index_signal_log.csv"

st.set_page_config(page_title="Index Analyzer", page_icon="📈", layout="centered",
                   initial_sidebar_state="collapsed")


def ordinal(x: float) -> str:
    n = int(round(x))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def secret(name: str) -> str:
    try:
        return str(st.secrets[name])
    except Exception:
        return os.environ.get(name, "")


# ------------------------------------------------------------------ sidebar
st.sidebar.title("Settings")
names = st.sidebar.multiselect("Indices", list(core.INDICES), default=list(core.INDICES))
include_live = st.sidebar.checkbox("Include today's live (incomplete) candle", value=False,
                                   help="Off = only completed daily candles are used.")
show_option = st.sidebar.checkbox("Show option idea", value=True)
capital = st.sidebar.number_input("Capital (Rs)", value=float(core.CAPITAL), step=10000.0)
risk_pct = st.sidebar.number_input("Max risk per trade (%)", value=core.MAX_RISK_PCT * 100, step=0.1) / 100
sl_pct = st.sidebar.number_input("Stop on option premium (%)", value=core.SL_PREMIUM_PCT * 100, step=5.0) / 100
if st.sidebar.button("Refresh data", width="stretch"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.info("Educational tool only. Not financial advice.")


# --------------------------------------------------------------------- data
@st.cache_data(ttl=600, show_spinner=False)
def get_infos(index_names: tuple, include_partial: bool):
    args = SimpleNamespace(source="yfinance", csv_dir="data", years=6, asof=None, include_partial=include_partial)
    try:
        vix = core.load_prices(core.VIX_SYMBOL, "yfinance", "data", 6)
    except Exception:
        vix = None
    infos, errors = [], []
    for n in index_names:
        try:
            infos.append(dr.analyse_index(n, core.INDICES[n], args, vix))
        except Exception as e:
            errors.append(f"{n}: {e}")
    return infos, errors


@st.cache_data(ttl=86400, show_spinner=False)
def get_research_features(name: str, years: int):
    df = core.load_prices(core.INDICES[name], "yfinance", "data", years)
    try:
        vix = core.load_prices(core.VIX_SYMBOL, "yfinance", "data", years)
    except Exception:
        vix = None
    return core.compute_features(df, vix)


@st.cache_data(ttl=300, show_spinner=False)
def get_gift_auto():
    return ictx.fetch_gift_nifty()


@st.cache_data(ttl=900, show_spinner=False)
def get_context(prev_close, gift_price, gift_change):
    gift = None
    if gift_price:
        gift = dict(price=gift_price, change_pct=gift_change, source="entered in the app")
    return ictx.collect_context(prev_close, True, gift_override=gift)


@st.cache_data(ttl=86400, show_spinner=False)
def get_global_impact(years: int):
    nifty = core.load_prices(core.INDICES["NIFTY 50"], "yfinance", "data", years)
    return ictx.global_impact(nifty, ictx.load_global(years))


def stale_note(infos):
    if not infos:
        return None
    expected = core.expected_last_session()
    last_date = max(i["feats"].index[-1].date() for i in infos)
    if last_date < expected:
        return (f"latest candle is {last_date}, expected {expected} - market holiday, "
                "or the data source has not updated yet")
    return None


def levels_table(lv: dict) -> pd.DataFrame:
    rows = [{"Type": "Resistance", "Level": p, "Source": l} for p, l in reversed(lv["resistances"])]
    rows += [{"Type": "Support", "Level": p, "Source": l} for p, l in lv["supports"]]
    return pd.DataFrame(rows)


def ranges_table(ranges: list) -> pd.DataFrame:
    rows = []
    for r in ranges:
        rows.append({"Period": r["period"], "PH": r["ph"], "PL": r["pl"],
                     "To PH": f"{r['to_ph_pct']:+.1f}%", "To PL": f"{r['to_pl_pct']:+.1f}%",
                     "Last close vs prior range": core.STATUS_TEXT[r["status"]],
                     "Range used": r["ref_label"]})
    return pd.DataFrame(rows)


def show_index(info: dict, log: pd.DataFrame):
    last, name, spec = info["last"], info["name"], info["spec"]
    date = info["feats"].index[-1].date()
    close = float(last["close"])
    st.subheader(f"{name}  ·  {date}")

    c1, c2 = st.columns(2)
    c1.metric("Close", f"{close:,.1f}", f"{last['chg_pct']:+.2f}%")
    c2.metric("Bias", str(last["bias"]), f"{last['strength']} (score {last['score']:+.1f})", delta_color="off")
    c3, c4 = st.columns(2)
    if not np.isnan(last["vix"]):
        c3.metric("India VIX", f"{last['vix']:.2f}", f"{ordinal(last['vix_pctl'])} percentile (1y)", delta_color="off")
    else:
        c3.metric("India VIX", "n/a")
    c4.metric("Supertrend", "Green" if last["st_state"] == "bull" else "Red")

    if last["bias"] == "NEUTRAL" and last["bias_raw"] != "NEUTRAL":
        st.caption(f"Raw bias was {last['bias_raw']} but the VIX gate (VIX ≥ {core.VIX_GATE_ABS}) blocks it.")

    ema_txt = {"bull": "close > EMA20 > EMA50", "bear": "close < EMA20 < EMA50", "mixed": "EMA20/50 mixed"}[last["ema_state"]]
    lt_txt = {"above": "above the 200 EMA", "below": "below the 200 EMA", "n/a": "200 EMA n/a"}[last["lt_state"]]
    st.markdown(f"**Trend:** {ema_txt}, {lt_txt}  \n"
                f"**Momentum:** RSI {last['rsi']:.0f} ({last['rsi_zone']}), MACD {last['macd_state']}  \n"
                f"**ATR:** {last['atr_pct']:.2f}% of price")
    st.markdown("**Changes vs previous session:** " + ("; ".join(info["changes"]) if info["changes"] else "none"))

    st.dataframe(levels_table(info["levels"]), hide_index=True, width="stretch",
                 column_config={"Level": st.column_config.NumberColumn(format="%d")})
    st.markdown("**Previous high / low** (PH / PL, levels to watch next session)")
    st.dataframe(ranges_table(info["ranges"]), hide_index=True, width="stretch",
                 column_config={"PH": st.column_config.NumberColumn(format="%d"),
                                "PL": st.column_config.NumberColumn(format="%d")})
    st.caption("Track record - " + dr.track_record(log, name))

    if show_option:
        atm = core.atm_strike(close, spec["strike_step"])
        if last["bias"] == "BULLISH":
            st.info(f"Option idea (unvalidated): ATM {atm} CE. Stop {int(sl_pct * 100)}% of premium. "
                    "Choose the expiry yourself; use the Size tab for lots.")
        elif last["bias"] == "BEARISH":
            st.info(f"Option idea (unvalidated): ATM {atm} PE. Stop {int(sl_pct * 100)}% of premium. "
                    "Choose the expiry yourself; use the Size tab for lots.")
        else:
            st.caption("Option idea: none (neutral)")


# ---------------------------------------------------------------------- UI
st.title("📈 Index Analyzer")
st.caption(f"{dt.datetime.now(core.IST):%d %b %Y, %I:%M %p} IST  ·  daily candles  ·  research information, not advice")
if not core.BIAS_VALIDATED:
    st.warning("The bias score is NOT validated yet. Use the Research tab to see whether it carries any information.")

infos, errors = [], []
tab_today, tab_global, tab_research, tab_size, tab_log = st.tabs(["Today", "Global", "Research", "Size", "Log"])

# ------------------------------------------------------------------- Today
with tab_today:
    if not names:
        st.info("Pick at least one index in the sidebar.")
    else:
        with st.spinner("Loading market data..."):
            infos, errors = get_infos(tuple(names), include_live)
        log = dr.read_log(LOG_FILE)
        note = stale_note(infos)
        if note:
            st.warning(note)
        for e in errors:
            st.error(e)
        for info in infos:
            show_index(info, log)
            st.markdown("---")
        if infos:
            if st.button("Send this report to Telegram", width="stretch"):
                token, chat = secret("TELEGRAM_BOT_TOKEN"), secret("TELEGRAM_CHAT_ID")
                if not token or not chat:
                    st.warning("Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the app's Secrets (see README).")
                else:
                    msg = dr.build_message(infos, log, errors, note, show_option, st.session_state.get("ctx"), "post")
                    if dr.send_telegram(msg, token, chat):
                        st.success("Sent to Telegram.")
                    else:
                        st.error("Telegram send failed.")

# ------------------------------------------------------------------ Global
with tab_global:
    st.markdown("Clues from outside the chart: **GIFT Nifty**, **global markets** and **news**. "
                "They hint at how the market may open. They do not decide the trend.")
    nifty_info = next((i for i in infos if i["name"] == "NIFTY 50"), None)
    prev_close = float(nifty_info["last"]["close"]) if nifty_info else None

    st.subheader("GIFT Nifty")
    st.caption("Read automatically from the public GIFT Nifty pages of Angel One / Upstox when possible (ordinary web pages, "
               "not an official feed, so they can change or block). Typing a price below always overrides it.")
    g1, g2 = st.columns(2)
    gift_price = g1.number_input("GIFT Nifty price (0 = skip)", value=0.0, step=10.0, key="gift_price")
    gift_chg = g2.number_input("Its change % on its own close (optional)", value=0.0, step=0.1, key="gift_chg")
    gift = None
    if gift_price > 0:
        gift = dict(price=gift_price, change_pct=gift_chg if gift_chg != 0 else None, source="entered in the app")
    else:
        gift = get_gift_auto()
    if gift and prev_close:
        gap = ictx.implied_gap(gift["price"], prev_close)
        m1, m2, m3 = st.columns(3)
        m1.metric("GIFT Nifty", f"{gift['price']:,.0f}",
                  f"{gift['change_pct']:+.2f}%" if gift.get("change_pct") is not None else None, delta_color="off")
        m2.metric("Gap vs Nifty close", f"{gap['points']:+,.0f} pts", f"{gap['pct']:+.2f}%")
        m3.metric("Read", gap["label"])
        src = f"Source: {gift['source']}" + (f", as of {gift['asof']:%d %b %H:%M} IST" if gift.get("asof") else "")
        if gift.get("stale"):
            st.warning(src + " - older than 18 hours (market closed, or the page is not updating).")
        else:
            st.caption(src)
        st.caption("GIFT is a futures price, so a small premium is normal. Its change on its own close is the cleaner signal.")
    elif not names or "NIFTY 50" not in names:
        st.info("Select NIFTY 50 in the sidebar to compare GIFT with the Nifty close.")
    else:
        st.info("GIFT Nifty not entered.")

    st.subheader("Global cues and news")
    if st.button("Load global cues and news", width="stretch"):
        with st.spinner("Loading global markets and headlines..."):
            st.session_state["ctx"] = get_context(prev_close, gift["price"] if gift else None,
                                                  gift.get("change_pct") if gift else None)
    ctx = st.session_state.get("ctx")
    if ctx:
        gd = ctx.get("global_df")
        if gd is not None and len(gd):
            st.metric("Global cue score", f"{ctx['composite']:+.2f}", ctx["global_label"], delta_color="off")
            show = gd[["region", "name", "date", "last", "chg_pct", "chg5_pct"]].copy()
            show.columns = ["Region", "Market", "Last session", "Level", "1-day %", "5-day %"]
            st.dataframe(show.round(2), hide_index=True, width="stretch")
            st.caption("A rise in the dollar index, US yields, Brent, USD/INR or US VIX counts against Indian stocks. "
                       "Each move is scaled by that market's own recent volatility.")
        news = ctx.get("news")
        if news and news["n"]:
            themes = f" Themes: {', '.join(news['themes'])}." if news["themes"] else ""
            st.markdown(f"**News tone:** {news['label']} ({news['neg']} negative / {news['pos']} positive of {news['n']} headlines).{themes}")
            for note in news["notes"]:
                st.markdown("- " + note.replace("$", "\\$"))
            st.caption("Keyword tone is crude: read the headlines, do not trade the label.")
        elif news is not None:
            st.caption("No recent headlines found.")
        for e in ctx.get("errors", []):
            st.warning(e)

    with st.expander("How much have global markets mattered for Nifty? (research)"):
        st.caption("US markets close after India, so the NEXT-day correlation is the relevant one. Asia and Europe overlap "
                   "Indian hours, so look at the same-day column. Correlation is not a trading rule.")
        if st.button("Run global impact research", width="stretch"):
            with st.spinner("Loading 15 years of global data - the first run takes a while..."):
                try:
                    st.session_state["gimpact"] = get_global_impact(15)
                except Exception as e:
                    st.error(f"Research failed: {e}")
        gi = st.session_state.get("gimpact")
        if gi is not None and len(gi):
            gi = gi.rename(columns={"name": "Market", "region": "Region", "days": "Days", "corr_same_day": "Corr same day",
                                    "corr_next_day": "Corr next day", "n_down": "# drops >=1%",
                                    "nifty_after_down_pct": "Nifty next day after a drop %", "n_up": "# rises >=1%",
                                    "nifty_after_up_pct": "Nifty next day after a rise %"})
            st.dataframe(gi.round(2), hide_index=True, width="stretch")

# ---------------------------------------------------------------- Research
with tab_research:
    st.markdown("Does the combined bias (and each part of it) say anything about what the index does **next**? "
                "Measured on up to 20 years of daily data. 'No reliable edge' is the normal answer.")
    r1, r2 = st.columns(2)
    r_index = r1.selectbox("Index", list(core.INDICES))
    r_h = r2.selectbox("Look ahead (days)", list(core.FORWARD_H), index=list(core.FORWARD_H).index(5))
    r3, r4 = st.columns(2)
    r_tp = r3.number_input("Move size to test (%)", value=4.0, step=0.5, help="4% is about 1000 Nifty points")
    r_min = r4.number_input("Minimum days per row", value=40, step=10)
    r_years = st.slider("Years of history", 5, 20, 20)
    if st.button("Run research", width="stretch"):
        with st.spinner("Computing - the first run takes a little while..."):
            try:
                feats = get_research_features(r_index, r_years)
                res = rs.compute_tables(rs.prepare(feats), int(r_h), float(r_tp), int(r_min))
                st.session_state["research"] = (r_index, res)
            except Exception as e:
                st.error(f"Research failed: {e}")
    if "research" in st.session_state:
        name, res = st.session_state["research"]
        m = res["meta"]
        st.success(f"{name}: {m['start']} to {m['end']}, {m['n']} days with a {m['H']}-day outcome")
        for s in res["sections"]:
            st.markdown(f"**{s['title']}**")
            st.dataframe(s["df"].round(2), hide_index=True, width="stretch")
            if s["key"] == "by_year":
                piv = res["by_year"]
                if {"mean_BULLISH", "mean_BEARISH"} <= set(piv.columns):
                    st.bar_chart(piv.set_index("year")[["mean_BULLISH", "mean_BEARISH"]])
                if res["year_note"]:
                    st.caption(res["year_note"])
        st.download_button("Download report (text)", rs.render_text(name, res) + "\n\n" + rs.CAUTION,
                           file_name=f"{name.replace(' ', '_')}_research.txt", width="stretch")
        st.caption(rs.CAUTION)

# -------------------------------------------------------------------- Size
with tab_size:
    st.markdown("How many lots keep the loss within your risk limit if the premium falls to your stop?")
    s_index = st.selectbox("Index", list(core.INDICES), key="size_index")
    spec = core.INDICES[s_index]
    s_lot = st.number_input("Lot size (verify with NSE)", value=int(spec["lot"]), step=1)
    s_premium = st.number_input("Live premium of the option", min_value=1.0, value=120.0, step=1.0)
    r = core.size_calc(s_premium, int(s_lot), capital, risk_pct, sl_pct)
    m1, m2, m3 = st.columns(3)
    m1.metric("Risk per lot", f"₹{r['risk_per_lot']:,.0f}")
    m2.metric("Max lots", f"{r['lots']}")
    m3.metric("Capital needed", f"₹{r['capital_needed']:,.0f}")
    st.caption(f"Max risk per trade: ₹{r['max_risk']:,.0f}  ·  stop at {int(sl_pct * 100)}% of premium")
    if r["lots"] == 0:
        st.error("Even 1 lot exceeds your risk limit. Skip it, or look at a cheaper premium or a defined-risk spread.")
    else:
        st.success(f"Up to {r['lots']} lot(s). Gaps and low liquidity can make the real loss bigger than the stop.")

# --------------------------------------------------------------------- Log
with tab_log:
    log = dr.read_log(LOG_FILE)
    if log.empty:
        st.info("No log yet. The scheduled GitHub job (see README) writes one row per index per day.")
    else:
        for n in core.INDICES:
            st.caption(f"{n}: " + dr.track_record(log, n))
        st.dataframe(log.sort_values("date", ascending=False).head(120), hide_index=True, width="stretch")
