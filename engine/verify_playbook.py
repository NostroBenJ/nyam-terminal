"""
verify_playbook.py  --  the board turned into a trade, and refusing to.

The failure this guards against is a bot that always finds a trade. Most of the
checks below are REFUSALS, because most of the time the honest answer is no —
and a playbook that manufactures a setup out of a mid-range tape is worse than
no playbook, since it launders "I have nothing" into "I have something".

The other property pinned here: the playbook never gets a second opinion. Every
candidate is scored by analysis.trigger, the same evaluator behind the Trigger
Check panel, so the automated read and the one on your screen cannot diverge.

Offline. No engine, no network.

    python verify_playbook.py
"""
import sys

from analysis import plan
from strategy import playbook as P

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def board(spot=772.0, regime="positive", bias="NEUTRAL / RANGE", **over):
    g = {"spot": spot, "regime": regime,
         "net_gex": 2.0e9 if regime == "positive" else -2.0e9,
         "gamma_flip": 765.0, "call_wall": 780.0, "put_wall": 770.0,
         "control_node": 776.0, "put_call_ratio": 1.2, "atm_iv": 0.10,
         "profile": [{"strike": 760.0 + i, "gex": (i - 10) * 1e8} for i in range(30)]}
    g.update(over)
    em = {"dollars": 5.0, "pct": 0.65, "low": spot - 5, "high": spot + 5, "dte": 1}
    return {"ticker": "SPY", "gex": g, "expected_move": em,
            "bias": {"label": bias}, "plan": plan.build(g, em=em),
            "confluences": []}


def chain(*, call_delta=0.45, put_delta=-0.45):
    """A minimal quote-enriched chain with one tradeable strike each side."""
    def row(strike, d, sym):
        return {"strike": strike, "oi": 5000, "iv": 0.10, "t_years": 1 / 365.0,
                "oi_change": 0, "symbol": sym, "bid": 1.95, "ask": 2.00,
                "delta": d, "volume": 5000}
    return [{"label": "2026-08-14", "dte": 1,
             "calls": [row(772.0, call_delta, "SPY260814C00772000")],
             "puts": [row(772.0, put_delta, "SPY260814P00772000")]}]


print("[1] positive gamma, spot AT the put wall -> long fade toward the magnet")
r = P.build(board(spot=770.0), expiries=chain())
check("a trade is found", r["available"], r.get("reason", ""))
check("playbook is range", r.get("playbook") == "range", str(r.get("playbook")))
check("direction is long", r.get("direction") == "long", str(r.get("direction")))
check("entry is the wall", r.get("entry") == 770.0, str(r.get("entry")))
check("target is the magnet", r.get("target") == 776.0, str(r.get("target")))
check("stop sits BELOW the wall", r.get("stop", 999) < 770.0, str(r.get("stop")))
check("the board was consulted and agreed",
      (r.get("board") or {}).get("verdict") in ("aligned", "mixed"),
      str((r.get("board") or {}).get("verdict")))
check("a contract was named", bool(r.get("contract")),
      str((r.get("contract") or {}).get("symbol")))
check("and it is a CALL for a long", (r.get("contract") or {}).get("right") == "call")

print("[2] spot AT the call wall -> short fade")
r = P.build(board(spot=780.0, control_node=774.0), expiries=chain())
check("a trade is found", r["available"], r.get("reason", ""))
check("direction is short", r.get("direction") == "short", str(r.get("direction")))
check("structure is a long put", r.get("structure") == "long_put",
      str(r.get("structure")))
check("stop sits ABOVE the wall", r.get("stop", 0) > 780.0, str(r.get("stop")))
check("the contract is a PUT", (r.get("contract") or {}).get("right") == "put")

print("[3] THE COMMON CASE — mid-range is not a trade")
# A bot that always finds something is the failure this file exists to prevent.
r = P.build(board(spot=775.0), expiries=chain())
check("refused", not r["available"], str(r.get("playbook")))
check("and says the edge is missing", "mid-range" in r["reason"], r["reason"][:80])
check("naming the distance and the band", "away" in r["reason"] and "band" in r["reason"])

print("[4] a magnet too close to the wall does not pay for the friction")
# Round-trip cost near the money runs ~2-3%; a two-point fade on SPY does not
# clear that reliably, so the setup is refused rather than scored down.
r = P.build(board(spot=770.0, control_node=771.0), expiries=chain())
check("refused", not r["available"])
check("the reason names the shortfall", "expected" in r["reason"] and "friction" in r["reason"],
      r["reason"][:90])

print("[5] a magnet on the wrong side of the wall is not a fade")
r = P.build(board(spot=770.0, control_node=766.0), expiries=chain())
check("refused", not r["available"], str(r.get("reason"))[:60])
check("named as a wrong-side magnet", "wrong side" in r["reason"], r["reason"][:70])

print("[6] both walls inside the band — too tight to fade either way")
r = P.build(board(spot=770.0, call_wall=770.4), expiries=chain())
check("refused", not r["available"])
check("the reason says the range is too tight", "too tight" in r["reason"],
      r["reason"][:70])

print("[7] negative gamma WITH a lean -> continuation")
r = P.build(board(spot=772.0, regime="negative", bias="SHORT LEAN"),
            expiries=chain())
check("a trade is found", r["available"], r.get("reason", ""))
check("playbook is directional", r.get("playbook") == "directional",
      str(r.get("playbook")))
check("direction follows the lean", r.get("direction") == "short",
      str(r.get("direction")))
check("target is the level below", r.get("target") == 770.0, str(r.get("target")))
check("stop is the flip", r.get("stop") == 765.0, str(r.get("stop")))

print("[8] negative gamma WITHOUT a lean is risk, not a setup")
# "Moves amplify in a direction nobody has called" describes exposure, not an
# edge. Six of the engine's eight graded calls were NEUTRAL, so this is the
# branch that will fire most often in a negative regime.
r = P.build(board(spot=772.0, regime="negative", bias="NEUTRAL / RANGE"),
            expiries=chain())
check("refused", not r["available"])
check("named as risk rather than a setup", "risk" in r["reason"], r["reason"][:80])

print("[9] the board has the last word")
# The playbook must never talk itself into a trade the Trigger Check panel
# would argue with — one evaluator, so the bot and the screen cannot diverge.
# A long fade at the put wall in NEGATIVE gamma is the canonical conflict:
# plan.py calls that level NOT A FLOOR.
r = P.build(board(spot=770.0, regime="negative", bias="LONG LEAN"),
            expiries=chain())
if r["available"]:
    check("negative-gamma long at the put wall was not sold as a clean fade",
          r.get("playbook") != "range", str(r.get("playbook")))
else:
    check("refused outright", True, r["reason"][:70])
# And directly: a setup whose trigger verdict is a conflict never survives.
import analysis.trigger as T                                     # noqa: E402
v = T.evaluate(board(spot=770.0, regime="negative"), 770.0, True)
check("trigger calls that long a conflict", v["verdict"] == "conflict",
      f"{v['verdict']} — {v['headline']}")

print("[10] a valid setup with no expressable contract is refused, not faked")
# Wide quotes: 1.00/2.00 is a 66% round trip. The thesis is fine and the trade
# is not, and those are different sentences.
wide = chain()
for side in ("calls", "puts"):
    for c in wide[0][side]:
        c["bid"], c["ask"] = 1.00, 2.00
r = P.build(board(spot=770.0), expiries=wide)
check("refused", not r["available"])
check("named as unexpressable, not as a bad read",
      "unexpressable" in r["reason"], r["reason"][:80])
check("the proposed setup is still returned for the log",
      bool(r.get("proposed")), str(bool(r.get("proposed"))))

print("[11] no chain means no contract, but the thesis still stands")
r = P.build(board(spot=770.0))
check("setup is described", r["available"], r.get("reason", ""))
check("but no contract is invented", r.get("contract") is None)

print("[12] missing inputs are refused with a reason")
check("no board", P.build({})["reason"].startswith("No board"))
b = board(spot=770.0)
b["expected_move"] = {"dollars": 0}
check("no expected move", "expected move" in P.build(b)["reason"],
      P.build(b)["reason"][:60])
b2 = board(spot=770.0, control_node=None)
check("no magnet in positive gamma", "nothing to pin" in P.build(b2)["reason"],
      P.build(b2)["reason"][:70])

print()
if FAILS:
    print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:8])}")
    sys.exit(1)
print("all playbook checks passed")
