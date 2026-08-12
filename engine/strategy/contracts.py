"""
contracts.py  --  which SPY option to actually trade.

The board has never had to name a contract. It says "put wall 767, positive
gamma, fade toward the magnet" — which is a level and a regime, not something
you can send to a broker. This turns that into a specific option, or refuses
and says why.

FRICTION IS THE FIRST-CLASS INPUT, not an afterthought. Measured on the live
SPY chain, round-trip spread cost as a share of mid:

    1 DTE, .40-.60 delta     2.4%
    2 DTE, .25-.40 delta     1.5%
    2 DTE, .40-.60 delta     2.8%
    1 DTE, .10-.25 delta     5.6%
    2 DTE, .10-.25 delta     4.7%

The cheap-looking contracts are the expensive ones. A far-OTM lottery ticket
costs 2-4x the friction of a near-ATM one, so a signal has to be ~5% right
before it pays for the privilege of being expressed that way. That is why the
default delta band sits near the money and why anything wider than
MAX_ROUND_TRIP_PCT is refused outright rather than merely scored down.

NOTHING HERE PLACES AN ORDER. It returns a description of a contract.
"""

#: Round-trip spread cost, as a percent of mid, above which a contract is
#: refused. 6% means a 3% edge is gone twice over before direction matters.
MAX_ROUND_TRIP_PCT = 6.0

#: Liquidity floors. A tight spread on a contract nobody trades is a quote,
#: not a market — it will not be there when you need out.
MIN_OPEN_INTEREST = 250
MIN_VOLUME = 100

#: Below this the quote is too coarse to reason about: a one-cent tick on a
#: $0.05 option is a 20% move, and rounding dominates every calculation
#: downstream.
MIN_MID = 0.10


def _quote(c: dict) -> tuple:
    """(bid, ask, mid, round_trip_pct) or (0,0,0,None) when unusable."""
    bid, ask = c.get("bid") or 0.0, c.get("ask") or 0.0
    if bid <= 0 or ask <= 0 or ask < bid:
        return 0.0, 0.0, 0.0, None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return bid, ask, 0.0, None
    # Round trip: you cross the spread going in and again coming out.
    return bid, ask, mid, 200.0 * (ask - bid) / mid


def tradeable(c: dict) -> dict:
    """
    Why this contract is or is not usable. Returns a verdict rather than a
    bool, because "no contract found" with no reason is the failure mode this
    whole codebase keeps finding.
    """
    bid, ask, mid, rt = _quote(c)
    if rt is None:
        return {"ok": False, "why": "no usable two-sided quote"}
    if mid < MIN_MID:
        return {"ok": False, "why": f"mid {mid:.2f} below {MIN_MID:.2f} — tick noise dominates"}
    if rt > MAX_ROUND_TRIP_PCT:
        return {"ok": False, "why": f"round trip {rt:.1f}% over {MAX_ROUND_TRIP_PCT}%"}
    if (c.get("oi") or 0) < MIN_OPEN_INTEREST:
        return {"ok": False, "why": f"open interest {c.get('oi')} under {MIN_OPEN_INTEREST}"}
    if (c.get("volume") or 0) < MIN_VOLUME:
        return {"ok": False, "why": f"volume {c.get('volume')} under {MIN_VOLUME}"}
    return {"ok": True, "why": "", "bid": bid, "ask": ask,
            "mid": round(mid, 3), "round_trip_pct": round(rt, 2)}


def select(expiries: list, *, right: str, target_delta: float,
           min_dte: int = 1, max_dte: int = 2,
           delta_tol: float = 0.15) -> dict:
    """
    The single contract closest to `target_delta` that survives every filter.

    `min_dte` defaults to 1, NOT 0. Zero-DTE carries the most gamma and the
    worst decay, and its quotes go to pieces late in the session — the whole
    position can be right and still not be exitable at a price worth taking.
    Pass min_dte=0 deliberately if a playbook wants it.

    Returns {"ok": bool, "contract": {...}, "why": str, "considered": int,
             "rejected": {reason: count}}. The rejection tally is the point:
    "no contract" and "forty contracts, all too wide" are different answers and
    only one of them means the market is closed to you.
    """
    right = "calls" if str(right).lower().startswith("c") else "puts"
    best, best_gap = None, None
    considered = 0
    rejected: dict = {}

    for e in expiries or []:
        dte = e.get("dte")
        if dte is None or dte < min_dte or dte > max_dte:
            continue
        for c in e.get(right) or []:
            d = c.get("delta")
            if d is None:
                rejected["no delta"] = rejected.get("no delta", 0) + 1
                continue
            considered += 1
            gap = abs(abs(float(d)) - target_delta)
            if gap > delta_tol:
                rejected["outside delta band"] = rejected.get("outside delta band", 0) + 1
                continue
            v = tradeable(c)
            if not v["ok"]:
                key = v["why"].split(" —")[0].split(" over")[0].split(" under")[0]
                rejected[key] = rejected.get(key, 0) + 1
                continue
            # Closest delta wins; ties go to the tighter round trip.
            score = (round(gap, 4), v["round_trip_pct"])
            if best_gap is None or score < best_gap:
                best_gap = score
                best = {**{k: c.get(k) for k in
                           ("symbol", "strike", "delta", "oi", "volume", "iv")},
                        "right": "call" if right == "calls" else "put",
                        "expiry": e.get("label"), "dte": dte,
                        "bid": v["bid"], "ask": v["ask"], "mid": v["mid"],
                        "round_trip_pct": v["round_trip_pct"]}

    if best is None:
        detail = ", ".join(f"{k}: {n}" for k, n in sorted(rejected.items())) or "none"
        return {"ok": False, "contract": None, "considered": considered,
                "rejected": rejected,
                "why": f"no {right[:-1]} within {delta_tol:.2f} of {target_delta:.2f} "
                       f"delta at {min_dte}-{max_dte} DTE cleared the filters "
                       f"({detail})"}
    return {"ok": True, "contract": best, "considered": considered,
            "rejected": rejected, "why": ""}
