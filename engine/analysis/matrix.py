"""
matrix.py  --  The strike x expiry GEX grid ("heatseeker").

The aggregate GEX number tells you dealers are short gamma. It does not tell
you WHERE, or on WHICH expiration. A -$200M book concentrated in Friday's
expiry behaves nothing like the same -$200M spread evenly across five weeks:
the first unwinds in two days, the second is structural.

This grid is the thing the multi-expiry confluence check was already computing
and then discarding — `pipeline.build_snapshot` computes full per-strike GEX
for every loaded expiration, uses only the four headline levels from each, and
drops the rest. Nothing new is fetched here.

Read it as: rows are strikes (high to low, spot marked), columns are
expirations (nearest first). A column that is deeply negative near spot is
where the pressure actually lives.
"""


def build(per_expiry: list, spot: float, window_pct: float = 0.06,
          max_rows: int = 22, levels: dict = None) -> dict | None:
    """
    per_expiry: [{"label": str, "dte": int, "gex": <compute_gex output>}, ...]
    levels:     the aggregate compute_gex output, for the rows that must appear

    Returns a grid dict, or None when there is nothing to show.

    Strikes are restricted to a band around spot and then thinned to at most
    `max_rows`. Showing every strike in the chain is not more information —
    the deep wings carry ~0 gamma and would compress the colour scale until
    the strikes that matter all look identical.

    `levels` pins the gamma flip and the walls into the grid regardless of how
    far they sit from spot. Without it the thinning kept only the nearest
    strikes, which on a positive-gamma day are all above the flip: the grid
    came out uniformly green with the regime line and the put wall cropped off
    the bottom, and looked for all the world like a data problem.
    """
    if not per_expiry or not spot:
        return None

    # union of strikes inside the window, across all expiries
    lo, hi = spot * (1 - window_pct), spot * (1 + window_pct)
    strikes = sorted({row["strike"]
                      for e in per_expiry
                      for row in e["gex"]["profile"]
                      if lo <= row["strike"] <= hi}, reverse=True)
    if not strikes:
        return None
    lv = levels or {}
    strikes = _thin(strikes, spot, max_rows,
                    anchors=(lv.get("gamma_flip"), lv.get("put_wall"),
                             lv.get("call_wall"), lv.get("control_node")))

    # per-expiry lookup so the grid is a dict read, not a scan per cell
    lookups = [{r["strike"]: r["gex"] for r in e["gex"]["profile"]} for e in per_expiry]

    rows, biggest = [], 0.0
    for k in strikes:
        cells = []
        for lk in lookups:
            v = lk.get(k)
            cells.append(None if v is None else round(v, 2))
            if v is not None:
                biggest = max(biggest, abs(v))
        rows.append({
            "strike": k,
            "cells": cells,
            "total": round(sum(c for c in cells if c is not None), 2),
            # marks the row the current price sits in, so the eye lands there
            "at_spot": abs(k - spot) <= _nearest_gap(strikes) / 2,
            # Which named level this row IS, when it is one. The grid is a wall
            # of numbers; without this you have to cross-reference the level map
            # to find the row that actually decides the regime.
            "level": _level_at(k, lv, _nearest_gap(strikes)),
        })

    return {
        "expiries": [{"label": e["label"], "dte": e.get("dte")} for e in per_expiry],
        "rows": rows,
        # colour scale is shared across the whole grid — per-column scaling
        # would make a quiet expiry look as dramatic as a loaded one
        "max_abs": round(biggest, 2),
        "spot": spot,
        "loaded": len(per_expiry),
    }


def _level_at(strike: float, levels: dict, gap: float) -> str | None:
    """Name the level this strike carries, if any. Flip wins ties — it is the
    regime boundary and the others are only meaningful relative to it."""
    for key, label in (("gamma_flip", "flip"), ("put_wall", "put wall"),
                       ("call_wall", "call wall"), ("control_node", "magnet")):
        v = levels.get(key)
        if v is not None and abs(strike - v) <= max(gap / 2, 0.5):
            return label
    return None


def _nearest_gap(strikes: list) -> float:
    """Typical spacing between adjacent strikes, for the at-spot test."""
    if len(strikes) < 2:
        return 1.0
    gaps = sorted(abs(strikes[i] - strikes[i + 1]) for i in range(len(strikes) - 1))
    return gaps[len(gaps) // 2] or 1.0


def _thin(strikes: list, spot: float, max_rows: int, anchors=None) -> list:
    """
    Keep at most `max_rows`: the key levels first, then the strikes nearest spot.

    ANCHORS ARE KEPT UNCONDITIONALLY, and that is the whole point of this
    function's existence in its current form. Taking the N strikes closest to
    spot sounds obviously right and is quietly wrong: on a positive-gamma day
    every one of them sits above the gamma flip, so the grid rendered entirely
    green, the legend carried a "short gamma" swatch that could never appear,
    and the regime line itself was off-screen. Observed live — 91 of 156
    strikes carried negative gamma, including the put wall, and the matrix
    showed none of them.

    A view that cannot display the flip cannot answer the question the panel
    exists for, which is where price sits relative to it.

    Trimming still prefers proximity to spot for the remaining rows rather than
    sampling evenly: those are the strikes price can actually reach today, and
    an even sample would drop half of them for wings nobody trades.
    """
    if len(strikes) <= max_rows:
        return strikes

    keep = []
    for a in (anchors or []):
        if a is None:
            continue
        # Snap each level to the nearest real strike — a flip at 758.05 is not
        # itself a strike, but the row at 758 is the one that shows it.
        nearest = min(strikes, key=lambda k: abs(k - a))
        if nearest not in keep:
            keep.append(nearest)

    for k in sorted(strikes, key=lambda k: abs(k - spot)):
        if len(keep) >= max_rows:
            break
        if k not in keep:
            keep.append(k)

    return sorted(keep, reverse=True)
