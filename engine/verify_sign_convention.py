"""
verify_sign_convention.py  --  the deepest assumption in the model.

compute_gex assumes dealers are LONG calls and SHORT puts. Every level inherits
it: the flip, both walls, the magnet, the regime, and therefore the bias and
every graded call. It cannot be proven from our own numbers, because it sits
upstream of all of them.

What it CAN be checked against is an independent implementation that had to
make the same choice. Unusual Whales publishes call and put exposure
separately, so their signs state their convention outright.

ALSO PINNED HERE: that the regime comes from the SIGN OF NET GAMMA and never
from where spot sits relative to a flip. gex.py switched to that after the
latter produced a backwards answer on a deeply negative book — and the mistake
was made AGAIN during this very audit, by inferring UW's regime from their
published flip. UW's flip sits above spot while their own summed exposure is
positive; reading the first as the regime says "negative" and is wrong. A trap
that catches you twice deserves a test.

Needs a live UW key. Run: python verify_sign_convention.py
"""
import sys

import config
from analysis import gex as gexmod
from data import unusual_whales as uw

_fail = 0


def check(name, ok, detail="", on_fail=""):
    global _fail
    if not ok:
        _fail += 1
    extra = detail or (on_fail if not ok else "")
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))


if config.USE_MOCK_DATA or config.PROVIDER != "uw" or not config.UW_API_KEY:
    print("Needs the live uw provider and a key.")
    raise SystemExit(1)

T = "SPY"
spot = uw._f(uw.stock_state(T), "close")
rows = uw.greek_exposure_by_strike(T)

print("[0] UW's published signs state their convention")
calls = [uw._f(r, "call_gex") for r in rows if uw._f(r, "call_gex")]
puts = [uw._f(r, "put_gex") for r in rows if uw._f(r, "put_gex")]
check("call exposure is published positive", all(c > 0 for c in calls),
      f"{sum(1 for c in calls if c > 0)}/{len(calls)}")
check("put exposure is published negative", all(p < 0 for p in puts),
      f"{sum(1 for p in puts if p < 0)}/{len(puts)}")
check("which is exactly our convention", all(c > 0 for c in calls) and all(p < 0 for p in puts))

print("[1] flipping the convention destroys the call wall")
contracts = uw.option_contracts(T, exclude_zero_oi_chains=True)
flat = {"calls": [], "puts": []}
for e in uw.chain_to_expiries(contracts, config.GEX_MAX_DTE):
    flat["calls"] += e["calls"]
    flat["puts"] += e["puts"]
ours = gexmod.compute_gex(flat, spot, r=config.RISK_FREE_RATE)
# Swapping the leg lists is mathematically identical to flipping both signs.
flipped = gexmod.compute_gex({"calls": flat["puts"], "puts": flat["calls"]},
                             spot, r=config.RISK_FREE_RATE)
check("ours produces a call wall", ours["call_wall"] is not None,
      str(ours["call_wall"]))
# Net gamma negating under the swap is an IDENTITY — true on any chain on any
# day — so it is asserted against the live feed.
check("net gamma is exactly negated by the flip",
      abs(ours["net_gex"] + flipped["net_gex"]) < 1.0,
      f"{ours['net_gex']:,.0f} vs {flipped['net_gex']:,.0f}")
# The live flipped call wall is NOT an identity, so it is reported, not
# asserted. See [1b].
print(f"      live flipped call wall: {flipped['call_wall']}")

print("[1b] the flip destroys the wall — on a chain where that MUST hold")
# This used to assert `flipped["call_wall"] is None` against the LIVE chain,
# making it an argument from absence resting on market composition. Walls are
# computed per strike as call gamma MINUS put gamma, so the flipped call wall
# is None only while NO strike above spot is put-dominated. On 2026-08-12
# exactly one was — 773.0, half a point above spot, where near-ATM put gamma
# outweighed call gamma — and a test of the SIGN CONVENTION failed because of
# where the puts happened to sit that morning.
#
# The convention is a property of the code, so it is tested on a chain built to
# have exactly one answer: calls only above spot, puts only below.
_S = 100.0
_synth = {
    "calls": [{"strike": 105.0, "oi": 5000, "iv": 0.20, "t_years": 5 / 365.0}],
    "puts":  [{"strike":  95.0, "oi": 5000, "iv": 0.20, "t_years": 5 / 365.0}],
}
_ok = gexmod.compute_gex(_synth, _S, r=config.RISK_FREE_RATE)
_fl = gexmod.compute_gex({"calls": _synth["puts"], "puts": _synth["calls"]},
                         _S, r=config.RISK_FREE_RATE)
check("calls above spot make a call wall", _ok["call_wall"] == 105.0,
      str(_ok["call_wall"]))
check("puts below spot make a put wall", _ok["put_wall"] == 95.0,
      str(_ok["put_wall"]))
check("flipped: nothing above spot is positive, so no call wall",
      _fl["call_wall"] is None, str(_fl["call_wall"]))
check("flipped: nothing below spot is negative, so no put wall",
      _fl["put_wall"] is None, str(_fl["put_wall"]))
check("and the flip negates net gamma here too",
      abs(_ok["net_gex"] + _fl["net_gex"]) < 1e-6,
      f"{_ok['net_gex']:,.2f} vs {_fl['net_gex']:,.2f}")

print("[2] per-strike sign agreement with UW")
uw_net = {}
for r in rows:
    k = uw._f(r, "strike")
    if k:
        uw_net[k] = uw_net.get(k, 0.0) + uw._f(r, "call_gex") + uw._f(r, "put_gex")
our_net = {p["strike"]: p["gex"] for p in ours["profile"]}
common = sorted(set(uw_net) & set(our_net))
agree = sum(1 for k in common if (uw_net[k] >= 0) == (our_net[k] >= 0))
pct = agree / max(len(common), 1) * 100
print(f"      {agree}/{len(common)} strikes agree in sign ({pct:.1f}%)")
check("the large majority of strikes agree in sign", pct >= 80, f"{pct:.1f}%")

print("[3] REGIME COMES FROM NET GAMMA, NEVER FROM THE FLIP")
# The trap, stated as a test. UW's flip sits above spot here while their own
# summed exposure is positive. Inferring the regime from the flip gives the
# opposite of the truth — which is how a deeply negative book once got labelled
# positive, and how this audit briefly mis-read UW.
uw_total = sum(uw_net.values())
uw_flip = uw.levels_to_floats(uw.gex_levels(T)).get("gamma_flip")
by_sum = "positive" if uw_total >= 0 else "negative"
print(f"      UW summed exposure {uw_total:,.0f} -> {by_sum}")
if uw_flip:
    by_flip = "positive" if spot > uw_flip else "negative"
    print(f"      UW flip {uw_flip} vs spot {spot} -> would imply {by_flip}")
    if by_flip != by_sum:
        print("      ^ these DISAGREE, which is the whole point of this check")
check("our regime is derived from net_gex, not from the flip",
      (ours["regime"] == "positive") == (ours["net_gex"] >= 0),
      f"{ours['regime']} / {ours['net_gex']:,.0f}")
check("our regime agrees with UW's SUMMED exposure",
      ours["regime"] == by_sum, f"ours {ours['regime']} vs UW {by_sum}")

print("[4] the convention is applied consistently across windows")
# A convention bug would tend to show up as a regime that flips with the
# window. If every window agrees, the sign is structural rather than an
# artefact of which expiries were included.
regimes = set()
for mx in (7, 14, 30, 90, 365):
    f2 = {"calls": [], "puts": []}
    for e in uw.chain_to_expiries(contracts, mx):
        f2["calls"] += e["calls"]
        f2["puts"] += e["puts"]
    if f2["calls"]:
        regimes.add(gexmod.compute_gex(f2, spot, r=config.RISK_FREE_RATE)["regime"])
check("regime is stable across every expiry window", len(regimes) == 1,
      str(sorted(regimes)))

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all sign convention checks passed")
