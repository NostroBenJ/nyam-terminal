"""
verify_bias.py  --  The call itself: bias_engine, plan, derived, levels, smt.

These five modules had NO suite, which is how "If price loses None" reached
the scenarios panel and how the Gamma Flip row could vanish from the level map
at the one moment it matters most.

Everything here is arithmetic and branching on dicts, so it runs offline with
constructed inputs. No network, no engine, no data_store.

    python verify_bias.py
"""
import sys

from analysis import bias_engine, derived, levels, plan, smt

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def gexd(**over):
    """A complete gex dict; override any field."""
    g = {"spot": 763.0, "net_gex": -3.7e9, "regime": "negative",
         "gamma_flip": 770.0, "control_node": 765.0,
         "call_wall": 775.0, "put_wall": 758.0,
         "put_call_ratio": 1.32, "call_oi": 100000, "put_oi": 132000,
         "call_oi_change": 0, "put_oi_change": 0, "atm_iv": 0.16,
         "profile": [{"strike": 750.0 + i, "gex": -1e8 + i * 3e6} for i in range(40)]}
    g.update(over)
    return g


SMT = {"lean": 0, "note": "no divergence"}
NEWS = {"high_impact": False, "headline": None}
LVL = {"prior_day_high": 767.0, "prior_day_low": 760.0, "prior_day_mid": 763.5,
       "prior_close": 762.0, "overnight_high": 766.0, "overnight_low": 761.0,
       "spot": 763.0}


def build(g):
    return bias_engine.build_bias(g, LVL, SMT, NEWS)


print("[1] a missing level is never interpolated into the text")
# gex.py NULLS a wall whose sign is wrong and returns no flip when none is in
# range, so None is a designed outcome. This was unguarded interpolation.
for name, g in (("no call wall", gexd(call_wall=None)),
                ("no put wall", gexd(put_wall=None)),
                ("neither wall", gexd(call_wall=None, put_wall=None)),
                ("no flip", gexd(gamma_flip=None)),
                ("nothing at all", gexd(call_wall=None, put_wall=None,
                                        gamma_flip=None, control_node=None))):
    b = build(g)
    blob = " ".join(b["scenarios"]) + " ".join(str(s["reason"]) for s in b["signals"])
    check(f"{name}: no literal 'None' in any prose", "None" not in blob,
          next((s for s in b["scenarios"] if "None" in s), "")[:70])
    check(f"{name}: still returns at least one scenario", len(b["scenarios"]) >= 1,
          str(b["scenarios"]))

print("[2] the regime sentence describes where spot is, it does not assert it")
# regime is the SIGN OF NET GAMMA at spot; the flip is a separate bisected
# level. They usually agree. This text used to hardcode "above flip" for
# positive and "below flip" for negative, so on divergence it printed a false
# statement as the reason for the highest-weighted signal on the board.
b = build(gexd(regime="positive", net_gex=1e9, spot=763.0, gamma_flip=770.0))
reason = [s for s in b["signals"] if s["name"] == "Gamma Regime"][0]["reason"]
check("positive regime with spot BELOW the flip says 'below'",
      "763.0 below flip 770.0" in reason, reason[:90])
b = build(gexd(regime="negative", spot=780.0, gamma_flip=770.0))
reason = [s for s in b["signals"] if s["name"] == "Gamma Regime"][0]["reason"]
check("negative regime with spot ABOVE the flip says 'above'",
      "780.0 above flip 770.0" in reason, reason[:90])
b = build(gexd(gamma_flip=None))
reason = [s for s in b["signals"] if s["name"] == "Gamma Regime"][0]["reason"]
check("no flip is stated as such", "no gamma flip in range" in reason, reason[:90])

print("[3] the label thresholds are the SAME number in both places")
# bias_engine._label and plan.trend_read each hardcode +/-2.0. They agree
# today; nothing but this check stops them drifting apart, and a header that
# says BALANCED over a call that says LONG LEAN is worse than either alone.
for score, want in ((2.5, "LONG"), (2.0, "LONG"), (1.99, "NEUTRAL"),
                    (0.0, "NEUTRAL"), (-1.99, "NEUTRAL"), (-2.0, "SHORT"),
                    (-3.0, "SHORT")):
    lab, _ = bias_engine._label(score, gexd(), "normal")
    tr = plan.trend_read(gexd(), SMT, {"score": score})
    lab_dir = "LONG" if "LONG" in lab else "SHORT" if "SHORT" in lab else "NEUTRAL"
    tr_dir = {"BULLISH": "LONG", "BEARISH": "SHORT"}.get(tr["text"].split()[0], "NEUTRAL")
    check(f"score {score:>6}: label {lab_dir} == header {tr_dir}", lab_dir == tr_dir,
          f"{lab!r} vs {tr['text']!r}")

print("[4] plan rows only ever reference levels that exist")
for name, g in (("no walls", gexd(call_wall=None, put_wall=None)),
                ("no flip", gexd(gamma_flip=None)),
                ("bare", gexd(call_wall=None, put_wall=None, gamma_flip=None,
                              control_node=None, profile=[]))):
    p = plan.build(g)
    blob = p["headline"] + p["bias_note"] + " ".join(
        f"{r['level']}{r['label']}{r['action']}{r['why']}" for r in p["rows"])
    check(f"{name}: no 'None' in the plan", "None" not in blob)
    check(f"{name}: every row carries a real level",
          all(r["level"] is not None for r in p["rows"]))

print("[5] the plan flips meaning with the regime")
# The house rule: in positive gamma the put wall is support; in negative gamma
# it is an accelerant. Getting this backwards is the single most expensive
# error this file can make.
pos = plan.build(gexd(regime="positive", net_gex=1e9))
neg = plan.build(gexd(regime="negative"))
pw_pos = [r for r in pos["rows"] if r["label"] == "Put Wall"][0]
pw_neg = [r for r in neg["rows"] if r["label"] == "Put Wall"][0]
check("positive gamma: put wall is SUPPORT", pw_pos["action"] == "SUPPORT",
      pw_pos["action"])
check("negative gamma: put wall is NOT A FLOOR", pw_neg["action"] == "NOT A FLOOR",
      pw_neg["action"])
check("the two regimes do not give the same advice",
      pw_pos["action"] != pw_neg["action"])
check("positive headline says pinning", "PINNING" in pos["headline"].upper())
check("negative headline says momentum", "MOMENTUM" in neg["headline"].upper())

print("[6] trap_door is below the put wall, or absent")
g = gexd(put_wall=770.0,
         profile=[{"strike": 760.0, "gex": -9e8},      # the trap door
                  {"strike": 765.0, "gex": -1e7},
                  {"strike": 775.0, "gex": -9e8}])     # above the wall: ignored
td = plan.trap_door(g)
check("picks the most negative strike BELOW the wall", td == 760.0, str(td))
check("no put wall -> no trap door", plan.trap_door(gexd(put_wall=None)) is None)
check("empty profile -> no trap door",
      plan.trap_door(gexd(profile=[])) is None)
# Noise in the wings must not be promoted to a level.
g = gexd(put_wall=770.0, profile=[{"strike": 700.0, "gex": -1e5},
                                  {"strike": 765.0, "gex": -1e9}])
check("a trivial wing print is not a trap door",
      plan.trap_door(g) == 765.0, str(plan.trap_door(g)))

print("[7] coincident levels MERGE — the flip is never dropped")
# The dedupe sorted by price and kept whichever row was added first, so the
# Gamma Flip row vanished whenever it landed on the magnet or on spot — the
# one moment the regime line matters most.
for name, g in (("flip on the magnet", gexd(gamma_flip=765.0, control_node=765.0)),
                ("flip on spot", gexd(gamma_flip=763.0)),
                ("flip on the put wall", gexd(gamma_flip=758.0))):
    rows = derived.build_level_map(g)
    check(f"{name}: the flip survives",
          any("Gamma Flip" in r["role"] for r in rows),
          str([r["role"] for r in rows]))
    merged = [r for r in rows if r["confluence"]]
    check(f"{name}: and is marked as confluence", len(merged) == 1,
          str([r["role"] for r in merged]))
rows = derived.build_level_map(gexd(gamma_flip=765.0, control_node=765.0))
row = [r for r in rows if r["confluence"]][0]
check("the merged row keeps the higher-priority tag", row["tag"] == "REGIME LINE",
      row["tag"])
check("and names both roles", "Gamma Flip" in row["role"] and "Magnet" in row["role"],
      row["role"])

print("[8] the level map is ordered high to low and has no duplicate prices")
rows = derived.build_level_map(gexd())
prices = [r["price"] for r in rows]
check("descending", prices == sorted(prices, reverse=True), str(prices))
check("unique", len(prices) == len(set(prices)), str(prices))
check("spot always appears", any("Spot" in r["role"] for r in rows))

print("[9] expected move")
em = derived.expected_move(spot=100.0, atm_iv=0.20, dte=365)
# 100 * 0.20 * sqrt(1) = 20 exactly.
check("one year at 20 vol is a 20.00 move", em["dollars"] == 20.0, str(em["dollars"]))
check("pct is the move over spot", em["pct"] == 20.0, str(em["pct"]))
check("band is spot +/- the move", (em["low"], em["high"]) == (80.0, 120.0),
      f"{em['low']}-{em['high']}")
# Scaling: four times the horizon is twice the move.
a = derived.expected_move(100.0, 0.20, 90)["dollars"]
b = derived.expected_move(100.0, 0.20, 360)["dollars"]
check("move scales with sqrt(T): 4x time is 2x move", abs(b - 2 * a) < 0.02,
      f"{a} -> {b}")
check("0 DTE is floored at one session, not zero",
      derived.expected_move(100.0, 0.20, 0)["dollars"] > 0)

print("[10] confluence needs at least two expiries to count")
def per(label, **g):
    return {"label": label, "gex": gexd(**g)}
out = derived.multi_expiry_confluence([per("A", call_wall=775.0)])
check("a single expiry is never confluent", out == [], str(out))
out = derived.multi_expiry_confluence([per("A", call_wall=775.0),
                                       per("B", call_wall=775.5)])
cw = [c for c in out if c["type"] == "Call Wall"]
check("two near-identical walls cluster", len(cw) == 1, str(out))
check("and count 2 of 2", cw and cw[0]["count"] == 2 and cw[0]["total"] == 2)
check("full flag set when every expiry agrees", cw and cw[0]["full"] is True)
out = derived.multi_expiry_confluence([per("A", call_wall=700.0),
                                       per("B", call_wall=900.0)])
check("far-apart levels do not cluster",
      not [c for c in out if c["type"] == "Call Wall"], str(out))

print("[11] SMT refuses to read a range against itself")
def side(name, oh, ol, ph, pl, real=True):
    return {"name": name, "on_high": oh, "on_low": ol, "prior_high": ph,
            "prior_low": pl, "spot": 763.0, "on_is_real": real}
r = smt.smt_divergence(side("QQQ", 766, 761, 767, 760, real=False),
                       side("SPY", 766, 761, 767, 760))
check("no real overnight -> no_data", r["signal"] == "no_data", r["signal"])
check("and votes 0", r["lean"] == 0, str(r["lean"]))
# A manufactured "in sync" would read as evidence of agreement; it is not.
check("the note says the read is unavailable", "No divergence read" in r["note"],
      r["note"])

print("[12] SMT divergences point the right way")
r = smt.smt_divergence(side("QQQ", 768, 761, 767, 760),      # QQQ new high
                       side("SPY", 766, 761, 767, 760))      # SPY did not
check("one index alone at new highs is BEARISH", r["lean"] == -1, str(r))
r = smt.smt_divergence(side("QQQ", 766, 759, 767, 760),      # QQQ new low
                       side("SPY", 766, 761, 767, 760))      # SPY held
check("one index alone at new lows is BULLISH", r["lean"] == +1, str(r))
r = smt.smt_divergence(side("QQQ", 768, 759, 767, 760),
                       side("SPY", 768, 759, 767, 760))
check("both confirming is no signal", r["lean"] == 0 and r["signal"] == "none",
      str(r))

print("[13] confluence between session and GEX levels")
c = levels.find_confluences(LVL, gexd(call_wall=767.0))
check("prior day high sitting on the call wall is flagged",
      any("Prior Day High" in x["label"] and "Call Wall" in x["label"] for x in c),
      str(c))
check("the flagged price is between the two", c and 766.9 <= c[0]["price"] <= 767.1,
      str(c and c[0]["price"]))
check("nothing is flagged when levels are far apart",
      levels.find_confluences(LVL, gexd(call_wall=900.0, put_wall=600.0,
                                        gamma_flip=500.0)) == [])
check("a null GEX level is skipped, not matched",
      levels.find_confluences(LVL, gexd(call_wall=None, put_wall=None,
                                        gamma_flip=None)) == [])

print("[14] news cuts conviction without moving the score")
plain = build(gexd())
risky = bias_engine.build_bias(gexd(), LVL, SMT,
                               {"high_impact": True, "headline": "CPI 08:30"})
check("conviction drops", risky["conviction"] == "reduced", risky["conviction"])
check("but the score is unchanged", risky["score"] == plain["score"],
      f"{plain['score']} -> {risky['score']}")
check("and the event is named in a signal",
      any("CPI 08:30" in str(s["reason"]) for s in risky["signals"]))
check("the news signal casts no directional vote",
      all(s["lean"] == 0 for s in risky["signals"] if s["name"] == "News Risk"))

print("[15] every signal carries the fields the UI reads")
b = build(gexd())
for s in b["signals"]:
    ok = all(k in s for k in ("name", "lean", "weight", "reason"))
    check(f"{s.get('name', '?')} is well-formed", ok, str(sorted(s)))
check("score equals the weighted sum of leans",
      abs(b["score"] - round(sum(s["lean"] * s["weight"] for s in b["signals"]), 2)) < 1e-9,
      str(b["score"]))
check("the mix version is recorded", b.get("mix") == bias_engine.MIX_VERSION
      or bias_engine.MIX_VERSION in str(b), bias_engine.MIX_VERSION)

print()
if FAILS:
    print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:8])}")
    sys.exit(1)
print("all bias/plan checks passed")
