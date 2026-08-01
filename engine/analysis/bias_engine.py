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
}


def build_bias(gex: dict, levels: dict, smt: dict, news: dict, em: dict = None, neg_zone: dict = None) -> dict:
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
    }


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
