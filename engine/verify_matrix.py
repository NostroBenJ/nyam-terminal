"""
verify_matrix.py  --  the gamma grid shows the rows that decide the regime.

THE BUG THIS PINS. _thin kept the N strikes nearest spot, which sounds
obviously right and is quietly wrong. On a positive-gamma day every one of them
sits above the gamma flip, so the grid rendered uniformly green, the legend
carried a "short gamma" swatch that could never appear, and both the flip and
the put wall were cropped off the bottom. Observed live: 91 of 156 strikes
carried negative gamma, including a -$250.9M put wall, and the matrix displayed
none of them. It looked exactly like a data problem or a subscription limit,
and was neither.

A view that cannot show the flip cannot answer the question the panel exists
for: where price sits relative to it.

Offline, synthetic chains. Run: python verify_matrix.py
"""
import sys

from analysis import matrix

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def chain(spot, lo, hi, step=1.0, sign=1.0):
    """One expiry whose profile spans lo..hi, all one sign."""
    prof = []
    k = lo
    while k <= hi:
        prof.append({"strike": k, "gex": sign * 1e6 * (1 + abs(k - spot))})
        k += step
    return {"label": "2026-08-05", "dte": 1, "gex": {"profile": prof}}


SPOT = 772.59
FLIP = 758.05
PUT_WALL = 750.0
CALL_WALL = 775.0
NODE = 770.0
LEVELS = {"gamma_flip": FLIP, "put_wall": PUT_WALL,
          "call_wall": CALL_WALL, "control_node": NODE}

# 60 strikes inside a +/-6% window, far more than max_rows, so thinning bites.
per = [chain(SPOT, 730.0, 815.0)]

print("[0] the flip and both walls survive thinning")
g = matrix.build(per, SPOT, levels=LEVELS)
shown = sorted(r["strike"] for r in g["rows"])
check("grid built", g is not None)
check("more strikes available than rows shown", len(shown) <= 22, str(len(shown)))
for name, v in (("flip", FLIP), ("put wall", PUT_WALL),
                ("call wall", CALL_WALL), ("magnet", NODE)):
    nearest = min(shown, key=lambda k: abs(k - v))
    check(f"{name} {v} is in the grid (row {nearest})", abs(nearest - v) <= 1.0,
          f"nearest shown {nearest}")

print("[1] without levels the old cropping is reproduced")
# Proves the fix is the anchoring and not an accident of the window size.
g0 = matrix.build(per, SPOT)
shown0 = sorted(r["strike"] for r in g0["rows"])
check("un-anchored grid crops the put wall",
      min(shown0) > PUT_WALL, f"lowest row {min(shown0)} vs put wall {PUT_WALL}")

print("[2] rows are labelled with the level they carry")
labels = {r["level"]: r["strike"] for r in g["rows"] if r.get("level")}
for want in ("flip", "put wall", "call wall", "magnet"):
    check(f"row labelled '{want}'", want in labels, str(sorted(labels)))
check("exactly one row per level", len(labels) == 4, str(labels))
check("un-levelled rows carry None",
      all(r.get("level") is None for r in g["rows"] if r["strike"] not in labels.values()))

print("[3] spot is still marked and still present")
at_spot = [r["strike"] for r in g["rows"] if r["at_spot"]]
check("exactly one at_spot row", len(at_spot) == 1, str(at_spot))
check("at_spot row is nearest spot",
      at_spot and abs(at_spot[0] - SPOT) <= 1.0, str(at_spot))

print("[4] a grid holding both signs shows both")
# The failure mode was a uniformly-coloured grid, so prove the negative side
# reaches the output when it exists.
mixed = [{"label": "x", "dte": 0, "gex": {"profile":
         [{"strike": k, "gex": (1e6 if k > FLIP else -1e6)}
          for k in [730.0 + i for i in range(86)]]}}]
gm = matrix.build(mixed, SPOT, levels=LEVELS)
totals = [r["total"] for r in gm["rows"]]
check("grid contains positive rows", any(t > 0 for t in totals))
check("grid contains negative rows", any(t < 0 for t in totals),
      f"min={min(totals):,.0f} max={max(totals):,.0f}")

print("[5] ordering and arithmetic")
ks = [r["strike"] for r in g["rows"]]
check("rows descend by strike", ks == sorted(ks, reverse=True))
bad = [r["strike"] for r in g["rows"]
       if abs(sum(c for c in r["cells"] if c is not None) - r["total"]) > 1.0]
check("each row's cells sum to its total", not bad, str(bad))
check("one cell per expiry on every row",
      all(len(r["cells"]) == len(g["expiries"]) for r in g["rows"]))

print("[6] degenerate inputs")
check("no expiries -> None", matrix.build([], SPOT, levels=LEVELS) is None)
check("no spot -> None", matrix.build(per, 0, levels=LEVELS) is None)
check("levels omitted still builds", matrix.build(per, SPOT) is not None)
check("levels of all None still builds",
      matrix.build(per, SPOT, levels={"gamma_flip": None, "put_wall": None,
                                      "call_wall": None,
                                      "control_node": None}) is not None)
tiny = [chain(SPOT, 772.0, 774.0)]
check("fewer strikes than max_rows is untouched",
      len(matrix.build(tiny, SPOT, levels=LEVELS)["rows"]) == 3)

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all matrix checks passed")
