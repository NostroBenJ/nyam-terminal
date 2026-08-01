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

    rows = []
    if cw:
        rows.append({"price": cw, "role": "Call Wall / Ceiling", "tag": "FADE / EXIT", "cls": "down"})
    if mag and mag not in (cw, pw):
        rows.append({"price": mag, "role": "Control Node / Magnet", "tag": "PIN", "cls": "mag"})
    rows.append({"price": spot, "role": "Spot — Current", "tag": "WATCH", "cls": "watch"})
    if flip:
        rows.append({"price": flip, "role": "Gamma Flip", "tag": "REGIME LINE", "cls": "flip"})
    if pw:
        tag = "ACCEL / PUTS ONLY" if neg else "FLOOR / BUY DIPS"
        cls = "down" if neg else "up"
        rows.append({"price": pw, "role": "Put Wall / Floor", "tag": tag, "cls": cls})

    # dedupe by price, keep highest-priority role (first wins), sort high->low
    seen, deduped = set(), []
    for r in sorted(rows, key=lambda r: -r["price"]):
        if r["price"] in seen:
            continue
        seen.add(r["price"])
        deduped.append(r)
    return deduped


def neg_gamma_zone(gex: dict) -> dict | None:
    """If in negative gamma, the band between flip and put wall is the
    accelerant zone — moves extend, bounces fail. Worth flagging explicitly."""
    if gex["regime"] != "negative" or not (gex["gamma_flip"] and gex["put_wall"]):
        return None
    hi = max(gex["gamma_flip"], gex["put_wall"])
    lo = min(gex["gamma_flip"], gex["put_wall"])
    return {"low": lo, "high": hi,
            "note": f"Negative-gamma accelerant zone {lo}-{hi}: below here moves extend and bounces tend to fail."}
