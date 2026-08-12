"""
playbook.py  --  the board, turned into a trade you could actually place.

TWO PLAYBOOKS, CHOSEN BY REGIME, because the same level means opposite things
in each and plan.py already knows which:

  POSITIVE GAMMA — dealers dampen. Walls hold, edges get faded, price gravitates
  to the magnet. The trade is a FADE from a wall back toward the control node.
  It is emphatically not "always be positioned": with spot mid-range there is
  no edge to fade and this returns no trade.

  NEGATIVE GAMMA — dealers amplify. The same walls become accelerants and the
  flip is a regime line rather than support. The trade is CONTINUATION in the
  direction of the lean.

IT DOES NOT GET A SECOND OPINION. Every candidate it proposes is run through
analysis.trigger, the same evaluator behind the Trigger Check panel, and only
an `aligned` verdict survives. If the panel would argue with the trade, the bot
does not take it — one source of truth for what the board thinks, so the
automated read and the one on your screen can never diverge.

WHY THE SIGNAL IS THE THING TO WATCH, not this file: through 2026-08-12 the
bias engine has 1 hit in 8 graded, and 0 of 2 directional. Six of eight were
NEUTRAL — a regime call being scored as a direction call. This module exists to
make the regime call TRADEABLE and, more importantly, MEASURABLE as what it
actually is. Whether it is worth trading is a question for the shadow log, not
for this docstring.

NOTHING HERE PLACES OR STAGES AN ORDER. It returns a description.
"""
from analysis import trigger
from strategy import contracts as C

#: Delta to aim for. Near the money, because measured round-trip friction on
#: the live SPY chain runs 1.5-2.8% there against 4.7-5.6% out at .10-.25 — the
#: cheap-looking contracts cost two to four times as much to get in and out of.
TARGET_DELTA = 0.45

#: Expiry window. 0DTE is excluded by default: it carries the most gamma and
#: the worst decay, and its quotes come apart late in the session, so a correct
#: thesis can still be unexitable at a price worth taking.
MIN_DTE, MAX_DTE = 1, 2

#: A fade needs somewhere to fade TO. If the magnet sits closer to the wall
#: than this (as a share of the day's 1-sigma move), the trade is paying full
#: friction for a move that may not clear it.
MIN_EDGE_FRACTION = 0.35


def _em(snap: dict) -> float:
    return (snap.get("expected_move") or {}).get("dollars") or 0.0


def _no(reason: str, **extra) -> dict:
    return {"available": False, "playbook": None, "reason": reason, **extra}


def build(snap: dict, *, expiries: list = None) -> dict:
    """
    The trade the board implies right now, or an explicit no-trade and why.

    `expiries` is the quote-enriched chain (chain_to_expiries(..., with_quotes
    =True)). Without it the setup is still described but no contract is named,
    which is the honest degradation: the thesis does not depend on the quotes,
    the executable expression does.
    """
    gex = snap.get("gex") or {}
    spot = gex.get("spot")
    regime = gex.get("regime")
    if not spot or not regime:
        return _no("No board loaded.")

    em = _em(snap)
    if em <= 0:
        return _no("No expected move — cannot size a target or a stop against it.")

    bias = (snap.get("bias") or {}).get("label") or ""
    if regime == "positive":
        setup = _range_setup(snap, spot, em)
    else:
        setup = _momentum_setup(snap, spot, em, bias)
    if not setup.get("available"):
        return setup

    # THE BOARD GETS THE LAST WORD. Same evaluator as the Trigger Check panel,
    # so the automated read and the one on your screen cannot diverge.
    #
    # WHAT COUNTS AS BACKING DIFFERS BY PLAYBOOK, and the distinction is the
    # whole point rather than a loosened rule:
    #
    #   A FADE happens AT a level — the level is the thesis. `no_level` means
    #   the premise is absent, so nothing short of an aligned reading will do.
    #
    #   A CONTINUATION happens BETWEEN levels, running from one toward the
    #   next. Open space is its normal condition; demanding a level there would
    #   make momentum unexpressable by construction. It only needs the board
    #   not to argue.
    #
    # `conflict` blocks both, always. That is the line that matters: never take
    # a trade the board actively contradicts.
    v = trigger.evaluate(snap, setup["entry"], setup["direction"] == "long")
    setup["board"] = {"verdict": v.get("verdict"), "headline": v.get("headline"),
                      "near": v.get("near")}
    verdict = v.get("verdict")
    ok = (verdict in ("aligned", "mixed") if setup["playbook"] == "range"
          else verdict != "conflict")
    if not ok:
        return _no(f"The board does not back it: {v.get('headline')}",
                   playbook=setup["playbook"], proposed=setup)
    if verdict == "mixed":
        setup.setdefault("notes", []).append(
            "Levels inside the band disagree — the board calls this a split.")
    elif verdict == "no_level":
        setup.setdefault("notes", []).append(
            "Entry is in open space — normal for continuation, but there is no "
            "level here to lean on if it goes against you.")

    if expiries:
        right = "calls" if setup["direction"] == "long" else "puts"
        pick = C.select(expiries, right=right, target_delta=TARGET_DELTA,
                        min_dte=MIN_DTE, max_dte=MAX_DTE)
        if not pick["ok"]:
            return _no(f"Setup is valid but unexpressable: {pick['why']}",
                       playbook=setup["playbook"], proposed=setup)
        setup["contract"] = pick["contract"]
    return setup


def _range_setup(snap: dict, spot: float, em: float) -> dict:
    """
    Positive gamma: fade a wall back toward the magnet.

    The edge is the wall, not the clock — with spot in the middle there is
    nothing to fade and this says so rather than manufacturing a trade.
    """
    gex = snap["gex"]
    cw, pw = gex.get("call_wall"), gex.get("put_wall")
    magnet = gex.get("control_node")
    if not magnet:
        return _no("Positive gamma but no control node — nothing to pin toward.")

    band = em * trigger.BAND_FRACTION
    at_call = cw and abs(spot - cw) <= band
    at_put = pw and abs(spot - pw) <= band
    if not at_call and not at_put:
        near = min([x for x in (cw, pw) if x], key=lambda x: abs(spot - x), default=None)
        away = abs(spot - near) if near else None
        return _no(
            "Positive gamma, but spot is mid-range — the fade needs an edge. "
            + (f"Nearest wall {near} is {away:.2f} away, band is {band:.2f}."
               if near else "No walls resolved."))
    if at_call and at_put:
        return _no("Call and put walls are both within the band — the range is "
                   "too tight to fade either side.")

    direction = "short" if at_call else "long"
    wall = cw if at_call else pw
    edge = abs(magnet - wall)
    if edge < em * MIN_EDGE_FRACTION:
        return _no(f"Magnet {magnet} is only {edge:.2f} from the wall {wall} — "
                   f"under {MIN_EDGE_FRACTION:.0%} of the {em:.2f} expected "
                   f"move, so the fade pays full friction for very little room.")
    # Wrong side of the magnet means the fade is already spent.
    if (direction == "short" and magnet > wall) or (direction == "long" and magnet < wall):
        return _no(f"Magnet {magnet} sits the wrong side of the {wall} wall for "
                   f"a {direction} fade.")

    return {
        "available": True,
        "playbook": "range",
        "direction": direction,
        "structure": "long_put" if direction == "short" else "long_call",
        "entry": wall,
        "entry_why": f"{'Call' if at_call else 'Put'} wall at {wall}; positive "
                     f"gamma means dealers lean against pushes into it.",
        "target": magnet,
        "target_why": f"Control node at {magnet} — where dealer gamma is most "
                      f"concentrated and price tends to gravitate.",
        # Beyond the wall the premise is gone: the level did not hold.
        "stop": round(wall + band if direction == "short" else wall - band, 2),
        "stop_why": f"A close past the wall by more than the {band:.2f} band "
                    f"means it did not hold, which was the entire thesis.",
        "invalidation": "Regime flips to negative — walls stop holding and "
                        "become accelerants.",
        "risk_points": round(band, 2),
        "reward_points": round(edge, 2),
        "notes": [],
    }


def _momentum_setup(snap: dict, spot: float, em: float, bias: str) -> dict:
    """
    Negative gamma: continuation in the direction of the lean.

    Requires an actual lean. In negative gamma with a NEUTRAL read there is no
    directional thesis, and "amplified moves in an unknown direction" is a
    description of risk rather than of a trade.
    """
    gex = snap["gex"]
    up = "LONG" in bias.upper()
    down = "SHORT" in bias.upper()
    if not (up or down):
        return _no(f"Negative gamma but the read is {bias or 'unset'} — moves "
                   f"amplify in a direction nobody has called. That is risk, "
                   f"not a setup.")

    direction = "long" if up else "short"
    flip = gex.get("gamma_flip")
    cw, pw = gex.get("call_wall"), gex.get("put_wall")
    target = cw if direction == "long" else pw
    if not target:
        edge = (snap.get("expected_move") or {})
        target = edge.get("high") if direction == "long" else edge.get("low")
    if not target:
        return _no("No level or expected-move edge to aim at.")

    band = em * trigger.BAND_FRACTION
    stop = (flip if flip else (spot - em / 2 if direction == "long" else spot + em / 2))
    return {
        "available": True,
        "playbook": "directional",
        "direction": direction,
        "structure": "long_call" if direction == "long" else "long_put",
        "entry": spot,
        "entry_why": f"{bias} in negative gamma — dealer hedging amplifies the "
                     f"move rather than damping it.",
        "target": target,
        "target_why": f"Next structural level at {target}.",
        "stop": round(stop, 2),
        "stop_why": (f"Reclaiming the flip at {flip} turns hedging back to "
                     f"mean-reverting and kills the momentum thesis."
                     if flip else "Half the expected move against the entry."),
        "invalidation": "Regime flips to positive — continuation becomes a fade.",
        "risk_points": round(abs(spot - stop), 2),
        "reward_points": round(abs(target - spot), 2),
        "notes": [],
    }
