#!/usr/bin/env python3
"""
index_plan.py - turns the report into a trade-plan checklist for each index.

It does NOT predict anything. It applies fixed, visible rules to the numbers already in the report:
  1. side        : from the combined bias (bullish -> long idea, bearish -> short idea, neutral -> no trade)
  2. day class   : ALIGNED / TREND ONLY / HALF SIZE or SKIP / SKIP / WAIT (big gap) / NO TRADE
                   using trend filters, GIFT Nifty gap and the global score
  3. levels      : breakout trigger = previous day's high (long) or low (short); pullback entry = nearest support / resistance
  4. stop        : breakout stop 0.7 daily ATR beyond the trigger; pullback stop 0.3 ATR beyond the level
  5. targets     : the next levels in the trade direction that give at least 1R
  6. risk check  : does the stop fit ONE lot inside your risk limit? (futures-equivalent and ATM option with delta ~0.5)

The bias behind it is unvalidated until index_research.py supports it. Planning aid, not advice.
"""
from __future__ import annotations

import html

import numpy as np

import index_core as core

BREAKOUT_STOP_ATR = 0.7
PULLBACK_STOP_ATR = 0.3
OPTION_DELTA = 0.5
GAP_BIG_PCT = 0.5
GAP_CONFLICT_PCT = 0.2
GLOBAL_THRESHOLD = 0.4
EVENT_THEMES = {"RBI / India rates", "US Fed / rates"}

LIMITS = ("Limits: at most 2 trades a day, stop after 2 losses, intraday exit by 15:15, "
          "check the event calendar (RBI, Fed, Budget, big results) first.")


def _target_list(levels: list, trigger: float, risk: float, side: int):
    out = []
    for p, lab in levels:
        dist = (p - trigger) * side
        if dist >= risk:                       # at least 1R away
            out.append((p, lab, dist / risk))
    return out[:2]


def build_plan(info: dict, ctx: dict | None = None, capital: float = core.CAPITAL,
               risk_pct: float = core.MAX_RISK_PCT) -> dict:
    last, spec, name = info["last"], info["spec"], info["name"]
    close = float(last["close"])
    atr = float(last["atr"])
    bias = str(last["bias"])
    side = {"BULLISH": 1, "BEARISH": -1}.get(bias, 0)
    plan = dict(name=name, side=side, bias=bias, verdict="NO TRADE", confirms=[], conflicts=[], notes=[],
                lot=spec["lot"], max_risk=capital * risk_pct)

    if side == 0:
        if str(last["bias_raw"]) != "NEUTRAL":
            plan["reason"] = f"the VIX gate blocked the raw {last['bias_raw'].lower()} bias"
        else:
            plan["reason"] = "no directional bias"
        return plan

    # ---- confirmations and conflicts
    confirms, conflicts, notes = plan["confirms"], plan["conflicts"], plan["notes"]
    st_ok = (last["st_state"] == "bull") == (side == 1) and last["st_state"] != "n/a"
    lt_ok = (last["lt_state"] == "above") == (side == 1) and last["lt_state"] != "n/a"
    if st_ok and lt_ok:
        confirms.append("Supertrend and the 200 EMA agree")
    else:
        conflicts.append("trend filters disagree (Supertrend / 200 EMA)")

    gap_pct, gift_price, big_gap = None, None, False
    if ctx and ctx.get("gap") and info["name"] == "NIFTY 50":
        gap_pct = ctx["gap"]["pct"]
        gift_price = ctx["gift"]["price"]
        if gap_pct * side >= GAP_CONFLICT_PCT:
            confirms.append(f"GIFT gap {gap_pct:+.2f}% points the same way")
        elif gap_pct * side <= -GAP_CONFLICT_PCT:
            conflicts.append(f"GIFT gap {gap_pct:+.2f}% points against it")
        if abs(gap_pct) >= GAP_BIG_PCT:
            big_gap = True
    comp = ctx.get("composite") if ctx else None
    if comp is not None:
        if comp * side >= GLOBAL_THRESHOLD:
            confirms.append(f"global cue score {comp:+.2f} agrees")
        elif comp * side <= -GLOBAL_THRESHOLD:
            conflicts.append(f"global cue score {comp:+.2f} is against it")
    if str(last["vix_state"]) == "high":
        notes.append("India VIX is in the top third of its 1-year range: option premiums are expensive, prefer spreads or smaller size")
    news = (ctx or {}).get("news")
    if news and EVENT_THEMES & set(news.get("themes", [])):
        notes.append("event themes in the headlines: " + ", ".join(sorted(EVENT_THEMES & set(news["themes"]))))

    if len(conflicts) >= 2:
        verdict = "SKIP"
    elif len(conflicts) == 1:
        verdict = "HALF SIZE or SKIP"
    elif big_gap:
        verdict = "WAIT (big gap)"
    elif len(confirms) >= 2:
        verdict = "ALIGNED"
    else:
        verdict = "TREND ONLY"
    plan["verdict"] = verdict

    # ---- levels, stops, targets
    pr = {r["period"]: r for r in info["ranges"]}
    lv = info["levels"]
    day = pr.get("Day")
    trigger = (day["ph"] if side == 1 else day["pl"]) if day else close
    stop = trigger - side * BREAKOUT_STOP_ATR * atr
    risk = abs(trigger - stop)
    beyond = lv["resistances"] if side == 1 else lv["supports"]
    pull = (lv["supports"] if side == 1 else lv["resistances"])
    plan.update(trigger=trigger, trigger_label="previous day high" if side == 1 else "previous day low",
                stop=stop, stop_pts=risk, stop_pct=risk / close * 100, atr_pts=atr)
    targets = _target_list(beyond, trigger, risk, side)
    if not targets:
        targets = [(trigger + side * m * risk, f"{m}R", float(m)) for m in (1, 2)]
    plan["targets"] = targets
    if pull:
        lvl, lab = pull[0]
        pstop = lvl - side * PULLBACK_STOP_ATR * atr
        plan.update(pull_level=lvl, pull_label=lab, pull_stop=pstop, pull_pts=abs(lvl - pstop))
    if gift_price is not None and (gift_price - trigger) * side > 0:
        notes.append(f"GIFT ({gift_price:,.0f}) is already beyond the trigger: do not chase the open, wait for a pullback or skip")

    # ---- does it fit the risk limit?
    fut_pts = plan["max_risk"] / spec["lot"]
    opt_pts = fut_pts / OPTION_DELTA
    plan.update(budget_fut_pts=fut_pts, budget_opt_pts=opt_pts, fits_option=risk <= opt_pts, fits_future=risk <= fut_pts)
    return plan


def plan_html(plan: dict) -> str:
    e = html.escape
    side_txt = "LONG" if plan["side"] == 1 else "SHORT" if plan["side"] == -1 else "-"
    head = f"<b>TRADE PLAN - {e(plan['name'])}</b> ({e(plan['bias'])}; planning aid, unvalidated)"
    if plan["side"] == 0:
        return head + f"\nNo trade: {e(plan.get('reason', 'no directional bias'))}."
    icon = {"ALIGNED": "✅", "TREND ONLY": "🟡", "WAIT (big gap)": "⏳"}.get(plan["verdict"], "⚠️")
    lines = [head, f"{icon} <b>{e(plan['verdict'])}</b> - {side_txt} idea"]
    if plan["confirms"]:
        lines.append("For: " + e("; ".join(plan["confirms"])))
    if plan["conflicts"]:
        lines.append("Against: " + e("; ".join(plan["conflicts"])))
    lines.append(f"Breakout entry: 15-min close {'above' if plan['side'] == 1 else 'below'} <code>{plan['trigger']:,.0f}</code> "
                 f"({e(plan['trigger_label'])}). Stop <code>{plan['stop']:,.0f}</code> "
                 f"({BREAKOUT_STOP_ATR} ATR = {plan['stop_pts']:,.0f} pts / {plan['stop_pct']:.1f}%)")
    if plan.get("pull_level") is not None:
        lines.append(f"Pullback entry: hold {'above' if plan['side'] == 1 else 'below'} <code>{plan['pull_level']:,.0f}</code> "
                     f"({e(plan['pull_label'])}). Stop <code>{plan['pull_stop']:,.0f}</code> ({plan['pull_pts']:,.0f} pts)")
    lines.append("Targets: " + ", ".join(f"<code>{p:,.0f}</code> ({e(l)}, {r:.1f}R)" for p, l, r in plan["targets"])
                 + ". Book part at 1R, trail the rest.")
    fit = "fits" if plan["fits_option"] else "does NOT fit"
    lines.append(f"Risk check: your limit Rs {plan['max_risk']:,.0f} = {plan['budget_fut_pts']:,.0f} index pts per lot (futures) or about "
                 f"{plan['budget_opt_pts']:,.0f} pts (ATM option, delta {OPTION_DELTA}). The {plan['stop_pts']:,.0f}-pt stop {fit} one lot."
                 + ("" if plan["fits_option"] else " Skip, use a tighter 15-min structure stop, or a defined-risk spread."))
    for n in plan["notes"]:
        lines.append("Note: " + e(n))
    lines.append(e(LIMITS))
    return "\n".join(lines)
