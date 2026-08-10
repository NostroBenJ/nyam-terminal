"""
verify_trigger.py  --  the join between the board and an external entry model.

The whole value of this panel is that it does not have an opinion of its own.
It reads plan.build(), which already encodes the house rule — in POSITIVE gamma
dealers dampen so walls hold, in NEGATIVE gamma they amplify so the same levels
become accelerants — and compares that to what you are about to do.

So the headline check is [2]: the SAME trigger, at the SAME price, must flip
from aligned to conflict when the regime flips. If it ever stops doing that,
the panel has grown an opinion and is no longer reading the board.

Offline. No engine, no network.

    python verify_trigger.py
"""
import sys

from analysis import plan, trigger

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def board(regime="positive", spot=772.86, **over):
    g = {"spot": spot, "regime": regime,
         "net_gex": 2.0e9 if regime == "positive" else -2.0e9,
         "gamma_flip": 764.7, "call_wall": 780.0, "put_wall": 767.0,
         "control_node": 775.0, "put_call_ratio": 1.2, "atm_iv": 0.072,
         "profile": [{"strike": 760.0 + i, "gex": (i - 10) * 1e8} for i in range(30)]}
    g.update(over)
    em = {"dollars": 2.47, "pct": 0.32, "low": spot - 2.47, "high": spot + 2.47,
          "dte": 0}
    return {"ticker": "SPY", "gex": g, "expected_move": em,
            "plan": plan.build(g, em=em),
            "confluences": [{"price": 767.0, "label": "Prior Day Low + Put Wall"}]}


print("[1] a trigger on a level is matched; open space is not")
b = board()
v = trigger.evaluate(b, 767.0, True)
check("put wall is found", v["available"] and v["verdict"] != "no_level", v["verdict"])
check("and named", any("Put Wall" in (e["label"] or "") for e in v["near"]),
      str([e["label"] for e in v["near"]]))
mid = trigger.evaluate(b, 772.9, True)
check("mid-range is reported as open space", mid["verdict"] == "no_level",
      mid["verdict"])
check("and says so plainly rather than going quiet",
      "No board level" in mid["headline"], mid["headline"])

print("[2] THE HEADLINE — the same trigger flips with the regime")
# The house rule, straight out of plan.py: the put wall is SUPPORT while gamma
# is positive and an ACCELERANT once it is negative. A long there is a
# different trade in each, and this panel exists to say which.
pos = trigger.evaluate(board("positive"), 767.0, True)
neg = trigger.evaluate(board("negative"), 767.0, True)
check("long at the put wall is ALIGNED in positive gamma",
      pos["verdict"] == "aligned", pos["verdict"] + " — " + pos["headline"])
check("the same long is CONFLICT in negative gamma",
      neg["verdict"] == "conflict", neg["verdict"] + " — " + neg["headline"])
check("the two regimes do not give the same verdict",
      pos["verdict"] != neg["verdict"])
check("and the reason is quoted from the board, not invented",
      "dealers" in (neg["note"] or "").lower() or "gamma" in (neg["note"] or "").lower(),
      (neg["note"] or "")[:70])

print("[3] direction is respected")
long_pw = trigger.evaluate(board(), 767.0, True)
short_pw = trigger.evaluate(board(), 767.0, False)
check("long at support is aligned", long_pw["verdict"] == "aligned")
check("short at that same support is a conflict", short_pw["verdict"] == "conflict",
      short_pw["headline"])
long_cw = trigger.evaluate(board(), 780.0, True)
check("long into the call wall is never reported as clean",
      long_cw["verdict"] in ("conflict", "mixed"), long_cw["verdict"])

print("[4] two levels on one price disagree out loud")
# The magnet and the call wall can sit on the same strike, pulling opposite
# ways. Reporting only one of them would hide the actual tension.
v = trigger.evaluate(board(control_node=780.0), 780.0, True)
check("verdict is mixed", v["verdict"] == "mixed", v["verdict"])
check("both levels are named", "supports" in v["headline"] and "argues" in v["headline"],
      v["headline"])
check("both appear in `near`", len(v["near"]) >= 2,
      str([e["label"] for e in v["near"]]))

print("[5] the band is anchored to vol, with a floor at half a strike")
# Levels ARE strikes. On a quiet session a pure fraction-of-expected-move band
# collapses under half a strike and a trigger at 767.4 stops matching 767.
wide = trigger.evaluate(board(), 767.0, True)
check("band is set", wide["band"] > 0, str(wide["band"]))
quiet = board()
quiet["expected_move"] = {"dollars": 0.05, "pct": 0.01, "low": 0, "high": 0, "dte": 0}
qv = trigger.evaluate(quiet, 767.4, True)
check("a near-zero expected move does not collapse the band",
      qv["band"] >= 0.4, str(qv["band"]))
check("so 767.4 still matches the put wall at 767",
      qv["verdict"] == "aligned", f"{qv['verdict']} band={qv['band']}")
busy = board()
busy["expected_move"] = {"dollars": 12.0, "pct": 1.6, "low": 0, "high": 0, "dte": 1}
check("and a busy day widens it", trigger.evaluate(busy, 767.0, True)["band"] > 2.0,
      str(trigger.evaluate(busy, 767.0, True)["band"]))

print("[6] stacked levels are surfaced with the trigger")
v = trigger.evaluate(board(), 767.0, True)
check("the confluence at this price is attached", len(v["confluence"]) == 1,
      str(v["confluence"]))
check("a trigger elsewhere carries none",
      trigger.evaluate(board(), 780.0, True)["confluence"] == [])

print("[7] targets point the way the trade is going")
v = trigger.evaluate(board(), 767.0, True)
nxt = v["targets"]["next_level"]
check("next level is ABOVE a long", nxt and nxt["level"] > 767.0,
      str(nxt and nxt["level"]))
check("and the 1-sigma edge is the upper one",
      v["targets"]["em_edge"] == board()["expected_move"]["high"],
      str(v["targets"]["em_edge"]))
s = trigger.evaluate(board(), 780.0, False)
nxts = s["targets"]["next_level"]
check("next level is BELOW a short", nxts and nxts["level"] < 780.0,
      str(nxts and nxts["level"]))
check("the 1-sigma reference is labelled as a band, not a forecast",
      "one session in three" in s["targets"]["em_note"], s["targets"]["em_note"])

print("[8] bad input is refused, not guessed at")
for bad in (0, -5, None, "seven"):
    v = trigger.evaluate(board(), bad, True)
    check(f"price {bad!r} refused", v["available"] is False, str(v)[:50])
check("no board yet is refused with a reason",
      trigger.evaluate({}, 767.0, True)["available"] is False)
check("an empty plan is open space, not a crash",
      trigger.evaluate({"gex": {"spot": 772.0, "regime": "positive"}},
                       767.0, True)["verdict"] == "no_level")

print("[9] every plan action this table can meet is understood")
# An action plan.py grows later must show up as unknown rather than be folded
# silently into "neutral", or the two files drift apart without anyone noticing.
seen = set()
for regime in ("positive", "negative"):
    for r in plan.build(board(regime)["gex"], em=board()["expected_move"])["rows"]:
        seen.add(r["action"])
missing = sorted(a for a in seen if a not in trigger._DIRECTION)
check("no plan action is unmapped", not missing, str(missing))
check("the map covers both regimes", len(seen) >= 5, str(sorted(seen)))

print()
if FAILS:
    print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:8])}")
    sys.exit(1)
print("all trigger checks passed")
