"""
smt.py  --  SMT divergence (Smart Money Technique) between your two indices.

The idea you already use on ES/NQ: if one index makes a new overnight high
but the other fails to, that non-confirmation is a divergence and often
front-runs a reversal at the open. Here we approximate it with the QQQ/SPY
overnight extremes (your NQ/ES proxies).
"""


def smt_divergence(primary: dict, secondary: dict) -> dict:
    """
    Each input: {"name", "on_high", "on_low", "prior_high", "prior_low", "spot",
                 "on_is_real"}
    primary = the one you trade (QQQ/NQ), secondary = confirmer (SPY/ES).

    When either side has no real overnight session yet (weekend, or pre-open
    before the first print), its prior-day range is standing in for the
    overnight range. Comparing a range against itself can only ever produce a
    non-signal, so say that explicitly rather than letting the caller read a
    manufactured "in sync" as evidence.
    """
    if not (primary.get("on_is_real", True) and secondary.get("on_is_real", True)):
        return {"signal": "no_data", "lean": 0,
                "note": "No overnight session yet — SMT needs a real Globex range "
                        "to compare. No divergence read available."}

    p_made_high = primary["on_high"] > primary["prior_high"]
    s_made_high = secondary["on_high"] > secondary["prior_high"]
    p_made_low = primary["on_low"] < primary["prior_low"]
    s_made_low = secondary["on_low"] < secondary["prior_low"]

    signal, lean, note = "none", 0, "Indices in sync overnight — no SMT signal."

    if p_made_high and not s_made_high:
        signal, lean = "bearish_divergence", -1
        note = f"{primary['name']} made a new overnight high but {secondary['name']} didn't — bearish SMT (potential fade of highs)."
    elif s_made_high and not p_made_high:
        signal, lean = "bearish_divergence", -1
        note = f"{secondary['name']} made a new overnight high but {primary['name']} didn't — bearish SMT non-confirmation."
    elif p_made_low and not s_made_low:
        signal, lean = "bullish_divergence", +1
        note = f"{primary['name']} made a new overnight low but {secondary['name']} held — bullish SMT (potential reversal off lows)."
    elif s_made_low and not p_made_low:
        signal, lean = "bullish_divergence", +1
        note = f"{secondary['name']} made a new overnight low but {primary['name']} held — bullish SMT non-confirmation."

    return {"signal": signal, "lean": lean, "note": note}
