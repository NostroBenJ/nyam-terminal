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
          max_rows: int = 16) -> dict | None:
    """
    per_expiry: [{"label": str, "dte": int, "gex": <compute_gex output>}, ...]

    Returns a grid dict, or None when there is nothing to show.

    Strikes are restricted to a band around spot and then thinned to at most
    `max_rows`. Showing every strike in the chain is not more information —
    the deep wings carry ~0 gamma and would compress the colour scale until
    the strikes that matter all look identical.
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
    strikes = _thin(strikes, spot, max_rows)

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


def _nearest_gap(strikes: list) -> float:
    """Typical spacing between adjacent strikes, for the at-spot test."""
    if len(strikes) < 2:
        return 1.0
    gaps = sorted(abs(strikes[i] - strikes[i + 1]) for i in range(len(strikes) - 1))
    return gaps[len(gaps) // 2] or 1.0


def _thin(strikes: list, spot: float, max_rows: int) -> list:
    """
    Keep at most `max_rows`, preferring strikes nearest spot.

    Trimming from the edges rather than sampling evenly: the rows that matter
    are the ones price can actually reach today, and an evenly-sampled grid
    would drop half of those to make room for wings nobody trades.
    """
    if len(strikes) <= max_rows:
        return strikes
    keep = sorted(strikes, key=lambda k: abs(k - spot))[:max_rows]
    return sorted(keep, reverse=True)
