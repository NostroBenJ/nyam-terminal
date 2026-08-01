"""
plan.py  --  Turns computed levels into a plain-language trade plan.

The dashboard already knows the call wall, the put wall, the flip and the
magnet. What it did not say is what to DO at each one, which is the difference
between a data readout and something usable at 9:31.

Every line here is derived from a number computed elsewhere — nothing is
asserted that the GEX math doesn't already support, and each row carries the
level it came from so you can check it.

The house rule encoded throughout: in POSITIVE gamma dealers dampen moves, so
edges get faded and the walls hold. In NEGATIVE gamma they amplify, so the same
levels become accelerants and "buy the dip at the put wall" is how you get run
over. Read the regime first; every row below flips meaning with it.
"""


def trap_door(gex: dict) -> float | None:
    """
    The strike below the put wall where a break accelerates.

    Defined as the most-negative-gamma strike below the put wall — the next
    concentration of dealer short gamma once the floor gives way. If price gets
    there, hedging pushes in the direction of the move rather than against it.

    None when there is no put wall, or nothing meaningfully negative beneath it.
    """
    pw = gex.get("put_wall")
    profile = gex.get("profile") or []
    if not pw or not profile:
        return None
    below = [p for p in profile if p["strike"] < pw and p["gex"] < 0]
    if not below:
        return None
    worst = min(below, key=lambda p: p["gex"])
    # ignore a "trap door" that is really just noise in the wings
    floor_mag = abs(min(profile, key=lambda p: p["gex"])["gex"]) * 0.15
    return worst["strike"] if abs(worst["gex"]) >= floor_mag else None


def build(gex: dict, em: dict = None, neg_zone: dict = None) -> dict:
    """Returns {"regime", "headline", "rows": [...], "bias_note"}."""
    spot = gex["spot"]
    neg = gex["regime"] == "negative"
    cw, pw = gex.get("call_wall"), gex.get("put_wall")
    flip, mag = gex.get("gamma_flip"), gex.get("control_node")
    trap = trap_door(gex)
    rows = []

    def add(level, label, action, tone, why):
        rows.append({"level": level, "label": label, "action": action,
                     "tone": tone, "why": why})

    # --- where price sits relative to the magnet ---------------------------
    if mag:
        side = "below" if spot < mag else "above" if spot > mag else "on"
        add(mag, "Magnet / Pin",
            "PIN" if side == "on" else f"PULL {'UP' if side == 'below' else 'DOWN'}",
            "mag",
            f"Spot {spot} is {side} the strike holding the most dealer gamma. "
            + ("Price tends to gravitate into it as expiry nears."
               if side != "on" else
               "Expect chop around this level rather than clean direction."))

    # --- call wall ----------------------------------------------------------
    if cw:
        if neg:
            add(cw, "Call Wall", "RESISTANCE", "down",
                f"Overhead supply at {cw}. In negative gamma it is a weaker cap "
                f"than usual — a break through it can run, so don't size a fade heavily.")
        else:
            add(cw, "Call Wall", "EXIT / FADE", "down",
                f"Positive gamma plus the largest positive-gamma strike above spot: "
                f"dealers sell into pushes toward {cw}. Take profit here, don't chase through it.")

    # --- put wall -----------------------------------------------------------
    if pw:
        if neg:
            add(pw, "Put Wall", "NOT A FLOOR", "down",
                f"{pw} is the biggest negative-gamma strike below spot. In negative "
                f"gamma dealers sell into weakness, so this is where moves speed up — "
                f"it is not the bounce level it would be in a positive-gamma tape.")
        else:
            add(pw, "Put Wall", "SUPPORT", "up",
                f"Dealers buy dips toward {pw} while gamma is positive. The most "
                f"defensible long-side reference on the board today.")

    # --- trap door ----------------------------------------------------------
    if trap:
        add(trap, "Trap Door", "FAST MOVE BELOW", "down",
            f"Next concentration of dealer short gamma under the put wall. Losing "
            f"{trap} removes the last structure before the flip — expect the move "
            f"to extend rather than mean-revert.")

    # --- gamma flip ---------------------------------------------------------
    if flip:
        if spot > flip:
            add(flip, "Gamma Flip", "REGIME LINE", "flip",
                f"Above {flip} dealers dampen moves. Losing it flips the whole tape "
                f"to amplification — treat a break as a change of rules, not a level.")
        else:
            add(flip, "Gamma Flip", "RECLAIM TO CALM", "flip",
                f"Price is below {flip}, so hedging is amplifying moves. Reclaiming "
                f"it is what turns the tape back to mean-reverting.")

    # --- headline -----------------------------------------------------------
    if neg:
        headline = "NEGATIVE GAMMA — momentum regime. Moves extend, bounces fail."
        bias = "Favour continuation over fades. Bottom-picking at the put wall is the trap."
    else:
        headline = "POSITIVE GAMMA — pinning regime. Edges get faded, ranges hold."
        bias = "Favour fading the extremes. Breakouts are lower-quality here."

    if neg_zone:
        bias += f" Accelerant band {neg_zone['low']}-{neg_zone['high']}."
    if em:
        bias += f" Today's 1-sigma band is {em['low']}-{em['high']} (±{em['pct']}%)."

    return {"regime": gex["regime"], "headline": headline,
            "rows": rows, "bias_note": bias, "trap_door": trap}


def trend_read(gex: dict, smt: dict, bias: dict) -> dict:
    """
    The one-line structural read for the header.

    Combines regime (how moves behave) with the bias score (which way the
    weighted signals lean) — they answer different questions and the header
    was previously showing neither.
    """
    neg = gex["regime"] == "negative"
    score = bias.get("score", 0)
    if score >= 2.0:
        arrow, word = "↑↑", "BULLISH"
    elif score <= -2.0:
        arrow, word = "↓↓", "BEARISH"
    else:
        arrow, word = "↔", "BALANCED"
    return {"arrow": arrow,
            "text": f"{word} {'MOMENTUM' if neg else 'STRUCTURE'}",
            "cls": "down" if score <= -2.0 else "up" if score >= 2.0 else "flat"}
