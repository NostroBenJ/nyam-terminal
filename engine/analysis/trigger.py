"""
trigger.py  --  what the board says about a trigger at a given price.

WHY THIS EXISTS. The dashboard answers WHERE — dealer positioning, the walls,
the flip, the magnet. An external entry model (CISD° on TradingView) answers
WHEN. Neither knows about the other, and the join happens in your head at 09:31
while a candle closes.

This does the join. Given a price and a direction, it reports which of the
board's own levels that trigger lands on, and whether the regime supports
trading it that way.

IT INVENTS NOTHING. Every verdict here is read off plan.build(), which already
encodes the house rule: in POSITIVE gamma dealers dampen, so walls hold and
edges get faded; in NEGATIVE gamma they amplify, so the same levels become
accelerants. A long at the put wall is a different trade in each regime, and
plan.py already knows which. This just looks it up and compares it to what you
are about to do.

THE POINT IS THE DISAGREEMENT. Two systems agreeing is weak evidence when they
share inputs — both read prior-day levels and liquidity sweeps. Two systems
DISAGREEING is strong information, and it is the case worth surfacing loudly:
a trigger that is long into a level the board calls EXIT / FADE.
"""

# What each plan action implies about direction. +1 supports a long, -1
# supports a short, 0 is directionally neutral (a line you cross, not a level
# you trade off).
#
# Keyed on plan.py's own action strings so the two cannot drift apart silently
# — an unrecognised action is reported as unknown rather than assumed neutral.
_DIRECTION = {
    "SUPPORT":            +1,
    "PULL UP":            +1,
    "RESISTANCE":         -1,
    "EXIT / FADE":        -1,
    "NOT A FLOOR":        -1,
    "FAST MOVE BELOW":    -1,
    "PULL DOWN":          -1,
    "PIN":                 0,
    "REGIME LINE":         0,
    "RECLAIM TO CALM":     0,
}

#: "At the level" tolerance, as a fraction of the day's 1-sigma expected move.
#: Anchored to implied vol rather than a fixed point count so it widens on a
#: busy day and tightens on a quiet one, and transfers across tickers without a
#: per-symbol table.
BAND_FRACTION = 0.20

#: FLOOR, as a fraction of spot — and it is not merely a divide-by-zero guard.
#:
#: The board's levels ARE strikes, and SPY strikes are $1 apart near spot, so
#: "at this level" has a natural minimum of about half a strike. Measured on a
#: live 0DTE board: expected move 2.47, which at 0.20 gives 0.49 — but on a
#: quiet session that same fraction collapses well under half a strike and a
#: trigger at 767.4 stops matching the put wall at 767. The floor stops the
#: band shrinking below the grid the levels sit on.
#:
#: 0.06% of spot is ~0.46 on SPY at 773.
BAND_FLOOR_PCT = 0.0006


def _band(snap: dict) -> float:
    gex = snap.get("gex") or {}
    spot = gex.get("spot") or 0.0
    floor = max(spot * BAND_FLOOR_PCT, 0.01)
    em = (snap.get("expected_move") or {}).get("dollars")
    if em and em > 0:
        return max(em * BAND_FRACTION, floor)
    return floor


def evaluate(snap: dict, price: float, bull: bool) -> dict:
    """
    Read the board for a trigger at `price` going `bull`/short.

    Returns a verdict dict. `available` is False with a reason rather than an
    empty result, because "no level near this price" and "the board could not
    be read" mean opposite things.
    """
    gex = snap.get("gex") or {}
    spot = gex.get("spot")
    if not isinstance(price, (int, float)) or price <= 0:
        return {"available": False, "note": "A trigger needs a positive price."}
    if not spot:
        return {"available": False, "note": "No board loaded yet."}

    rows = ((snap.get("plan") or {}).get("rows")) or []
    band = _band(snap)
    regime = gex.get("regime")
    want = 1 if bull else -1

    near, ranked = [], []
    for r in rows:
        lvl = r.get("level")
        if not isinstance(lvl, (int, float)):
            continue
        dist = abs(price - lvl)
        sign = _DIRECTION.get(r.get("action"))
        entry = {
            "label": r.get("label"),
            "action": r.get("action"),
            "level": lvl,
            "distance": round(dist, 2),
            "of_band": round(dist / band, 2) if band else None,
            # None means plan.py grew an action this table has not been taught.
            # Reported rather than folded into "neutral", so it shows up.
            "supports": sign,
            "why": r.get("why"),
        }
        ranked.append(entry)
        if dist <= band:
            near.append(entry)

    ranked.sort(key=lambda e: e["distance"])
    near.sort(key=lambda e: e["distance"])

    # Stacked levels — a session level sitting on a gamma level. Same band.
    conf = [c for c in (snap.get("confluences") or [])
            if isinstance(c.get("price"), (int, float))
            and abs(price - c["price"]) <= band]

    agree = [e for e in near if e["supports"] == want]
    against = [e for e in near if e["supports"] == -want]
    unknown = [e for e in near if e["supports"] is None]

    side = "long" if bull else "short"
    if not near:
        verdict = "no_level"
        headline = f"No board level within {round(band, 2)} of {price}."
        note = ("The trigger is in open space. Nothing here argues against it — "
                "but nothing on this board argues for it either.")
    elif against and not agree:
        verdict = "conflict"
        first = against[0]
        headline = f"{side.upper()} into {first['label']} — the board says {first['action']}."
        note = first["why"] or ""
    elif agree and against:
        verdict = "mixed"
        headline = (f"{agree[0]['label']} supports the {side}, "
                    f"{against[0]['label']} argues against it.")
        note = "Two levels inside the band disagree. Treat the closer one as the read."
    elif agree:
        verdict = "aligned"
        first = agree[0]
        headline = f"{side.upper()} at {first['label']} — the board says {first['action']}."
        note = first["why"] or ""
    else:
        verdict = "neutral"
        first = (unknown or near)[0]
        headline = f"{first['label']} is here, but it is not a directional level."
        note = first["why"] or ""

    return {
        "available": True,
        "price": round(price, 2),
        "direction": side,
        "spot": spot,
        "regime": regime,
        "band": round(band, 2),
        "verdict": verdict,
        "headline": headline,
        "note": note,
        "near": near,
        "nearest": ranked[0] if ranked else None,
        "confluence": conf,
        "targets": _targets(snap, price, bull, ranked),
    }


def _targets(snap: dict, price: float, bull: bool, ranked: list) -> dict:
    """
    Where the trade is headed if it works: the next board level in its
    direction, and the edge of the day's expected range.

    Both are references, not forecasts — the expected move is a 1-sigma band,
    so price finishes outside it about a third of the time.
    """
    ahead = [e for e in ranked
             if (e["level"] > price if bull else e["level"] < price)]
    ahead.sort(key=lambda e: e["distance"])
    em = snap.get("expected_move") or {}
    edge = em.get("high") if bull else em.get("low")
    return {
        "next_level": ahead[0] if ahead else None,
        "em_edge": edge,
        "em_note": ("1-sigma band edge — price closes outside it roughly one "
                    "session in three."),
    }
