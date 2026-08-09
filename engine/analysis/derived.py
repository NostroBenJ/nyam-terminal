"""
derived.py  --  Higher-level reads built on top of the raw GEX, mirroring the
pieces from your teacher's dashboard that are free to replicate:

  - expected_move        : ATM-IV implied 1-sigma move + projected range
  - multi_expiry_confluence : levels confirmed across several expirations
  - build_level_map      : the laddered "Level Action Map" with action tags
"""
import math


def expected_move(spot: float, atm_iv: float, dte: int) -> dict:
    """
    1-sigma expected move = Spot * IV * sqrt(T).  ~68% of the time price stays
    inside this band by expiry. The projected range is spot +/- that move.
    """
    t = max(dte, 1) / 365.0   # floor at one session — this is "today's move"
    em = spot * atm_iv * math.sqrt(t)
    return {
        "dollars": round(em, 2),
        "pct": round(100 * em / spot, 2) if spot else 0,
        "low": round(spot - em, 2),
        "high": round(spot + em, 2),
        "dte": dte,
    }


def multi_expiry_confluence(per_expiry: list, tol: float = 0.004) -> list:
    """
    A level is "confluent" when several expirations independently land on the
    same strike. The more expiries agree, the more it matters (your teacher's
    "needs all 5 expirations" check). Clusters by level type + price.

    `per_expiry`: list of {"label": str, "gex": <compute_gex output>}
    """
    candidates = []
    for e in per_expiry:
        g = e["gex"]
        for typ, key in (("Call Wall", "call_wall"), ("Put Wall", "put_wall"),
                         ("Gamma Flip", "gamma_flip"), ("Magnet", "control_node")):
            v = g.get(key)
            if v:
                candidates.append((typ, v, e["label"]))

    clusters = []
    for typ, v, lbl in candidates:
        for c in clusters:
            if c["type"] == typ and abs(c["price"] - v) / c["price"] <= tol:
                c["prices"].append(v)
                c["expiries"].add(lbl)
                break
        else:
            clusters.append({"type": typ, "price": v, "prices": [v], "expiries": {lbl}})

    total = len(per_expiry)
    out = []
    for c in clusters:
        n = len(c["expiries"])
        if n >= 2:
            out.append({
                "type": c["type"],
                "price": round(sum(c["prices"]) / len(c["prices"]), 2),
                "count": n,
                "total": total,
                "full": n == total,
            })
    out.sort(key=lambda x: (-x["count"], x["type"]))
    return out


def build_level_map(gex: dict) -> list:
    """
    Laddered key levels (high -> low) each with a role and an action tag,
    derived purely from the computed levels. No data cost.
    """
    spot = gex["spot"]
    cw, pw = gex["call_wall"], gex["put_wall"]
    flip, mag = gex["gamma_flip"], gex["control_node"]
    neg = gex["regime"] == "negative"

    # `rank` is explicit priority, low = most decision-relevant. It used to be
    # implicit in insertion order, and the dedupe below sorted by PRICE first —
    # so ties resolved by whichever row happened to be added earlier, not by
    # importance. When the flip landed on the magnet or on spot, the
    # "Gamma Flip / REGIME LINE" row was silently dropped, which is the one
    # moment it matters most: price sitting on the line the regime turns at.
    # `short` is used when roles are joined. The full names read well alone but
    # concatenate into something that wraps in the panel's column — "Call Wall
    # / Ceiling + Control Node / Magnet" is 42 characters to say "Call Wall +
    # Magnet", and the wrap made the merged row half again as tall as every
    # other row in a table whose whole value is being scannable at a glance.
    rows = []
    if cw:
        rows.append({"price": cw, "role": "Call Wall / Ceiling", "short": "Call Wall",
                     "tag": "FADE / EXIT", "cls": "down", "rank": 1})
    if mag:
        rows.append({"price": mag, "role": "Control Node / Magnet", "short": "Magnet",
                     "tag": "PIN", "cls": "mag", "rank": 2})
    rows.append({"price": spot, "role": "Spot — Current", "short": "Spot",
                 "tag": "WATCH", "cls": "watch", "rank": 3})
    if flip:
        rows.append({"price": flip, "role": "Gamma Flip", "short": "Gamma Flip",
                     "tag": "REGIME LINE", "cls": "flip", "rank": 0})
    if pw:
        tag = "ACCEL / PUTS ONLY" if neg else "FLOOR / BUY DIPS"
        cls = "down" if neg else "up"
        rows.append({"price": pw, "role": "Put Wall / Floor", "short": "Put Wall",
                     "tag": tag, "cls": cls, "rank": 1})

    # Coincident levels are MERGED, not discarded. Two things pointing at one
    # price is confluence — the highest-conviction reaction point there is, and
    # the whole reason levels.find_confluences exists — so throwing one away
    # loses exactly the information worth having. The surviving tag and colour
    # come from the highest-priority member; every role is named.
    by_price: dict = {}
    for r in rows:
        by_price.setdefault(r["price"], []).append(r)

    merged = []
    for price, group in by_price.items():
        group.sort(key=lambda r: r["rank"])
        lead = group[0]
        # One level keeps its full descriptive name; several use short ones.
        if len(group) == 1:
            role = lead["role"]
        else:
            role = " + ".join(dict.fromkeys(r["short"] for r in group))
        merged.append({
            "price": price,
            "role": role,
            "tag": lead["tag"],
            "cls": lead["cls"],
            "confluence": len(group) > 1,
        })
    merged.sort(key=lambda r: -r["price"])
    return merged


def neg_gamma_zone(gex: dict) -> dict | None:
    """If in negative gamma, the band between flip and put wall is the
    accelerant zone — moves extend, bounces fail. Worth flagging explicitly."""
    if gex["regime"] != "negative" or not (gex["gamma_flip"] and gex["put_wall"]):
        return None
    hi = max(gex["gamma_flip"], gex["put_wall"])
    lo = min(gex["gamma_flip"], gex["put_wall"])
    return {"low": lo, "high": hi,
            "note": f"Negative-gamma accelerant zone {lo}-{hi}: below here moves extend and bounces tend to fail."}
