"""
bias_engine.py  --  Combines every signal into ONE pre-market lean.

Design principle (the honest one): this is NOT a prediction. It produces a
weighted, probabilistic *lean* plus the reasoning behind it, and explicit
"if/then" scenarios. Every contributing signal carries its own `reason`
string so nothing on the dashboard is unexplained — that's the
"every signal shows its why" requirement.

Tune the WEIGHTS to match how you actually trade. They're not gospel.
"""

WEIGHTS = {
    "gex_regime": 2.0,     # positive vs negative gamma regime
    "wall_proximity": 1.5, # where price sits vs call/put walls
    "smt": 2.0,            # cross-market divergence
    "put_call": 1.0,       # put/call positioning + OI shift
    "magnet": 1.0,         # pull toward the control node / magnet
    "news": 0.0,           # news doesn't add direction, it cuts conviction (below)

    # --- flow signals, added 2026-08-04 with the UW feed -------------------
    # Weighted by HOW MUCH EACH ACTUALLY KNOWS, not by how interesting it is.
    "net_premium": 1.5,    # dollars committed today, directional, measured
    "flow_alerts": 0.8,    # real but noisy; needs a premium floor to mean much
    "dark_pool": 0.4,      # side is INFERRED from the spread, never reported
}

# THE SIGNAL MIX IS VERSIONED, for the same reason the grading rule is.
#
# A hit rate answers "does this bias work". Change which signals feed the bias
# and the number keeps counting, silently blending calls made by two different
# systems into one average that describes neither. The tracker already solved
# this for the grading RULE — every outcome records the rule it was graded
# under and `mixed_rules` warns when a rate spans more than one — so the mix
# rides along the same way rather than inventing a second mechanism.
#
# BUMP THIS whenever a signal is added, removed, or reweighted. The old records
# are not deleted: they stay, correctly labelled as belonging to the previous
# mix, so the comparison remains available instead of the baseline being reset.
MIX_VERSION = "v2-flow"


def build_bias(gex: dict, levels: dict, smt: dict, news: dict, em: dict = None,
               neg_zone: dict = None, flow: dict = None) -> dict:
    signals = []
    spot = gex["spot"]

    # 1) GEX regime ----------------------------------------------------------
    if gex["regime"] == "positive":
        signals.append({
            "name": "Gamma Regime",
            "lean": 0,
            "weight": WEIGHTS["gex_regime"],
            "reason": f"Positive gamma (spot {spot} above flip {gex['gamma_flip']}). "
                      f"Dealers sell rips / buy dips — expect mean-reversion and pinning, not a runaway trend.",
        })
    else:
        signals.append({
            "name": "Gamma Regime",
            "lean": 0,
            "weight": WEIGHTS["gex_regime"],
            "reason": f"Negative gamma (spot {spot} below flip {gex['gamma_flip']}). "
                      f"Dealer hedging amplifies moves — favor momentum/breakout over fading.",
        })

    # 2) Wall proximity ------------------------------------------------------
    cw, pw = gex["call_wall"], gex["put_wall"]
    if cw and pw:
        dist_cw = (cw - spot) / spot
        dist_pw = (spot - pw) / spot
        if abs(dist_cw) < 0.003:
            signals.append({"name": "Call Wall", "lean": -1, "weight": WEIGHTS["wall_proximity"],
                            "reason": f"Spot is right at the call wall ({cw}) — strong overhead resistance / likely sell zone."})
        elif abs(dist_pw) < 0.003:
            signals.append({"name": "Put Wall", "lean": +1, "weight": WEIGHTS["wall_proximity"],
                            "reason": f"Spot is right at the put wall ({pw}) — strong support / likely bounce zone."})
        else:
            signals.append({"name": "Walls", "lean": 0, "weight": WEIGHTS["wall_proximity"] * 0.5,
                            "reason": f"Price boxed between put wall {pw} and call wall {cw} — treat as the day's range until one breaks."})

    # 3) SMT divergence ------------------------------------------------------
    signals.append({"name": "SMT", "lean": smt["lean"], "weight": WEIGHTS["smt"], "reason": smt["note"]})

    # 4) Put/Call positioning ------------------------------------------------
    pc = gex["put_call_ratio"]
    if gex["put_oi_change"] > gex["call_oi_change"]:
        signals.append({"name": "OI Shift", "lean": -1, "weight": WEIGHTS["put_call"],
                        "reason": f"Puts added more open interest than calls overnight (P/C ratio {pc:.2f}) — defensive positioning."})
    elif gex["call_oi_change"] > gex["put_oi_change"]:
        signals.append({"name": "OI Shift", "lean": +1, "weight": WEIGHTS["put_call"],
                        "reason": f"Calls added more open interest than puts overnight (P/C ratio {pc:.2f}) — bullish positioning."})

    # 5) Magnet / control node pull -----------------------------------------
    mag = gex.get("control_node")
    if mag:
        dist = (mag - spot) / spot
        if dist > 0.002:
            signals.append({"name": "Magnet", "lean": +1, "weight": WEIGHTS["magnet"],
                            "reason": f"Control node / magnet sits above at {mag} — net dealer gamma there tends to pull price up into it."})
        elif dist < -0.002:
            signals.append({"name": "Magnet", "lean": -1, "weight": WEIGHTS["magnet"],
                            "reason": f"Control node / magnet sits below at {mag} — net dealer gamma there tends to pull price down into it."})
        else:
            signals.append({"name": "Magnet", "lean": 0, "weight": WEIGHTS["magnet"] * 0.5,
                            "reason": f"Price is sitting on the magnet ({mag}) — expect pinning / chop around this level."})

    # --- flow signals -------------------------------------------------------
    # Everything above reads POSITIONING: open interest, where dealers are, what
    # is already on the books. These three read PARTICIPATION — what money did
    # today. They answer a different question and can disagree with positioning,
    # which is exactly why they are worth a vote rather than a panel.
    signals += _flow_signals(flow)

    # --- aggregate ----------------------------------------------------------
    score = sum(s["lean"] * s["weight"] for s in signals)

    # negative-gamma accelerant zone (context, not a directional vote)
    if neg_zone:
        signals.append({"name": "Neg-Gamma Zone", "lean": 0, "weight": 0, "reason": neg_zone["note"]})

    # news cuts conviction rather than adding direction
    conviction = "normal"
    if news.get("high_impact"):
        conviction = "reduced"
        signals.append({"name": "News Risk", "lean": 0, "weight": 0,
                        "reason": f"High-impact event today ({news['headline']}). "
                                  f"Positioning signals are less reliable through the release — size down / wait for the reaction."})

    label, summary = _label(score, gex, conviction)

    scenarios = [
        f"If price reclaims and holds above {gex['gamma_flip']}, the positive-gamma case strengthens — favor fades of pushes toward {cw}.",
        f"If price loses {gex['put_wall']}, dealer hedging flips to amplifying — favor downside continuation, not bottom-picking.",
    ]

    return {
        "label": label,
        "score": round(score, 2),
        "conviction": conviction,
        "summary": summary,
        "signals": signals,
        "scenarios": scenarios,
        # Which signal set produced this call. Recorded on the prediction so a
        # hit rate can never silently average two different systems together.
        "mix": MIX_VERSION,
    }


# Below these, a reading is noise and votes 0 rather than a direction. Every
# one is a RATIO or a count, never a dollar figure: an absolute premium
# threshold that means "significant" on SPY means "enormous" on IWM, and the
# same engine grades both.
NET_PREM_MIN_SHARE = 0.15   # |net| as a share of total premium traded
FLOW_MIN_ALERTS = 3         # fewer than this is anecdote, not flow
FLOW_MIN_SKEW = 0.60        # one side must hold 60% of qualifying premium
DP_MIN_PRINTS = 4           # dark pool prints needed before a lean counts
DP_MIN_SKEW = 0.65          # and how lopsided they must be


def _flow_signals(flow: dict) -> list:
    """
    Votes from what money DID today, as opposed to where it is positioned.

    Each returns at most one signal and stays silent when the reading is thin.
    A signal that fires on every scrap of data is not measuring conviction, it
    is adding noise with a weight attached — and this engine's output is read
    as a lean on real trades.
    """
    out = []
    if not flow:
        return out

    # 1) NET PREMIUM — dollars into calls minus dollars into puts.
    nf = flow.get("net_flow") or {}
    if nf.get("available"):
        net = nf.get("net") or 0.0
        gross = abs(nf.get("call_premium") or 0.0) + abs(nf.get("put_premium") or 0.0)
        share = abs(net) / gross if gross else 0.0
        if share >= NET_PREM_MIN_SHARE and net != 0:
            lean = 1 if net > 0 else -1
            side = "calls" if net > 0 else "puts"
            out.append({
                "name": "Net Premium",
                "lean": lean,
                "weight": WEIGHTS["net_premium"],
                "reason": (f"${abs(net) / 1e6:,.1f}M net premium into {side} today "
                           f"({share * 100:.0f}% of all premium traded). Dollars "
                           f"committed, not contracts counted."),
            })
        else:
            out.append({
                "name": "Net Premium", "lean": 0, "weight": 0,
                "reason": (f"Net premium is only {share * 100:.0f}% of the day's "
                           f"total — calls and puts roughly cancel, so no lean."),
            })

    # 2) FLOW ALERTS — sweeps and blocks, weighted by premium not count.
    alerts = flow.get("flow_alerts") or []
    if alerts:
        calls = sum(a.get("premium") or 0 for a in alerts
                    if str(a.get("type", "")).lower().startswith("c"))
        puts = sum(a.get("premium") or 0 for a in alerts
                   if str(a.get("type", "")).lower().startswith("p"))
        total = calls + puts
        if len(alerts) >= FLOW_MIN_ALERTS and total > 0:
            skew = max(calls, puts) / total
            if skew >= FLOW_MIN_SKEW:
                lean = 1 if calls > puts else -1
                side = "call" if calls > puts else "put"
                out.append({
                    "name": "Flow Alerts", "lean": lean,
                    "weight": WEIGHTS["flow_alerts"],
                    "reason": (f"{len(alerts)} alerts, {skew * 100:.0f}% of premium "
                               f"on the {side} side (${max(calls, puts) / 1e6:,.1f}M)."),
                })
            else:
                out.append({
                    "name": "Flow Alerts", "lean": 0, "weight": 0,
                    "reason": (f"{len(alerts)} alerts but premium is split "
                               f"{skew * 100:.0f}/{100 - skew * 100:.0f} — no side."),
                })

    # 3) DARK POOL — the weakest of the three, and weighted accordingly.
    prints = flow.get("darkpool") or []
    live = [p for p in prints if not p.get("canceled")]
    if len(live) >= DP_MIN_PRINTS:
        buys = sum(1 for p in live if p.get("lean") == "buy")
        sells = sum(1 for p in live if p.get("lean") == "sell")
        sided = buys + sells
        if sided:
            skew = max(buys, sells) / sided
            if skew >= DP_MIN_SKEW and buys != sells:
                lean = 1 if buys > sells else -1
                out.append({
                    "name": "Dark Pool", "lean": lean,
                    "weight": WEIGHTS["dark_pool"],
                    # Said on the signal itself, not just in the panel: side is
                    # read from where the print sat in the spread, because dark
                    # pool prints carry no aggressor flag.
                    "reason": (f"{buys} of {sided} sided prints near the "
                               f"{'ask' if buys > sells else 'bid'}. Side is "
                               f"inferred from the spread, not reported."),
                })
            else:
                out.append({
                    "name": "Dark Pool", "lean": 0, "weight": 0,
                    "reason": f"{buys} buy / {sells} sell prints — no clear side.",
                })
    return out


def _label(score: float, gex: dict, conviction: str) -> tuple:
    if score >= 2.0:
        lab = "LONG LEAN"
        msg = f"Net positioning leans long into the open; primary upside target the call wall at {gex['call_wall']}."
    elif score <= -2.0:
        lab = "SHORT LEAN"
        msg = f"Net positioning leans short; primary downside reference the put wall at {gex['put_wall']}."
    else:
        lab = "NEUTRAL / RANGE"
        msg = f"No directional edge — treat {gex['put_wall']}–{gex['call_wall']} as the range and trade the edges."
    if conviction == "reduced":
        msg += " Conviction reduced by today's news risk."
    return lab, msg
