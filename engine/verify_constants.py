"""
verify_constants.py  --  which constants move the board, and which do not.

Every number in config.py sets something the board displays, and several carry
claims in their comments. A claim in a comment is not a measurement, and the
gap between the two is where a "harmless default" quietly becomes load-bearing.

Two things are pinned here, for opposite reasons:

  RISK_FREE_RATE is asserted to be low-impact. Verified across a range far
  wider than rates move, so its being a stale hardcoded snapshot is genuinely
  fine — and if the model ever changes such that r starts mattering, this FAILS
  rather than the staleness quietly becoming a real error.

  GEX_MAX_DTE is asserted to be load-bearing, and that is checked too: if a
  refactor ever made the window stop mattering, the comment above it would have
  become a lie, and lies in comments are how the next person gets misled.

Needs a live UW key. Run: python verify_constants.py
"""
import re
import sys

import config
from analysis import gex as gexmod
from data import unusual_whales as uw

_fail = 0


def check(name, ok, detail="", on_fail=""):
    """
    `detail` prints either way — use it for a measured value worth seeing.
    `on_fail` prints only on failure — use it for an explanation.

    Separated because writing a failure explanation into `detail` produces
    lines like "[PASS] config documents the coupling — config.py does not
    mention it", which contradicts itself and trains you to stop reading the
    output. That slip has now happened in three suites, so the helper enforces
    the distinction rather than relying on remembering it.
    """
    global _fail
    if not ok:
        _fail += 1
    extra = detail or (on_fail if not ok else "")
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))


if config.USE_MOCK_DATA or config.PROVIDER != "uw" or not config.UW_API_KEY:
    print("Needs the live uw provider and a key.")
    raise SystemExit(1)

st = uw.stock_state("SPY")
spot = uw._f(st, "close")
contracts = uw.option_contracts("SPY", exclude_zero_oi_chains=True)


def levels(max_dte, r):
    flat = {"calls": [], "puts": []}
    for e in uw.chain_to_expiries(contracts, max_dte):
        flat["calls"] += e["calls"]
        flat["puts"] += e["puts"]
    if not flat["calls"] or not flat["puts"]:
        return None
    return gexmod.compute_gex(flat, spot, r=r)


base = levels(config.GEX_MAX_DTE, config.RISK_FREE_RATE)
check("a baseline computes", base is not None)
if base is None:
    raise SystemExit(1)
print(f"      spot={spot} flip={base['gamma_flip']} "
      f"put={base['put_wall']} call={base['call_wall']}")

print("[0] RISK_FREE_RATE is NOT load-bearing (the comment's claim)")
flips, walls = [], set()
for r in (0.0, 0.02, 0.043, 0.06, 0.08):
    g = levels(config.GEX_MAX_DTE, r)
    if g and g["gamma_flip"] is not None:
        flips.append(g["gamma_flip"])
        walls.add((g["call_wall"], g["put_wall"]))
spread = max(flips) - min(flips)
print(f"      flip across r=0.00..0.08: {min(flips)} .. {max(flips)}  "
      f"(spread {spread:.2f})")
check("flip moves under 2 points across the whole range", spread < 2.0,
      f"{spread:.2f}")
check("walls do not move at all", len(walls) == 1, str(walls))
check("a stale hardcoded rate is therefore harmless", spread < 2.0)

print("[1] GEX_MAX_DTE IS load-bearing (also the comment's claim)")
# If this ever stops being true, the warning above the constant is misleading
# and should come down — a comment that overstates danger trains you to ignore
# the ones that do not.
seen_flip, seen_put, seen_net = set(), set(), []
for mx in (3, 7, 14, 30):
    g = levels(mx, config.RISK_FREE_RATE)
    if not g:
        continue
    seen_flip.add(g["gamma_flip"])
    seen_put.add(g["put_wall"])
    seen_net.append(g["net_gex"])
print(f"      put walls seen across dte 3..30: {sorted(x for x in seen_put if x)}")
check("the window changes the flip", len(seen_flip) > 1, str(sorted(seen_flip)))
check("the window changes the put wall", len(seen_put) > 1,
      str(sorted(x for x in seen_put if x)))
check("net gamma varies by more than 2x across the window",
      max(seen_net) / max(min(seen_net), 1) > 2,
      f"{min(seen_net):,.0f} .. {max(seen_net):,.0f}")

print("[2] changing the window would invalidate the record")
# The tracker versions the signal mix so a hit rate cannot average two systems.
# GEX_MAX_DTE sits upstream of every signal, so it belongs to that contract.
from analysis import bias_engine  # noqa: E402
check("a signal mix version exists to bump", bool(bias_engine.MIX_VERSION),
      bias_engine.MIX_VERSION)
check("config documents the coupling",
      "MIX_VERSION" in open(config.__file__, encoding="utf-8").read(),
      on_fail="config.py does not mention MIX_VERSION beside GEX_MAX_DTE")

print("[3] the grading band is per ticker and recorded on every outcome")
check("SPY has a measured band", config.grade_band_for("SPY") > 0,
      str(config.grade_band_for("SPY")))
check("an unlisted ticker falls back rather than erroring",
      config.grade_band_for("ZZZZ") == config.GRADE_BAND_PCT,
      str(config.grade_band_for("ZZZZ")))
check("the rule string carries band AND window",
      str(config.grade_band_for("SPY")) in config.grade_rule_for("SPY")
      and config.GRADE_EXIT_TIME in config.grade_rule_for("SPY"),
      config.grade_rule_for("SPY"))

print("[4] auto-refresh covers the whole session someone is watching")
# This bug has now been fixed twice. It first stopped at 09:59, covering only
# pre-market; that was extended to the GRADING window and stopped at 12:59,
# which left 13:00-16:00 frozen — the same failure, three hours later in the
# day. The cron spec is `hour=f"{start}-{end}"`, so it fires through end:59.
_start_h = int(config.PREMARKET_START.split(":")[0])
_end_h = int(config.SESSION_REFRESH_UNTIL.split(":")[0])
_open_h, _open_m = map(int, config.MARKET_OPEN.split(":"))
check("refreshing begins before the open", _start_h < _open_h,
      f"{config.PREMARKET_START} vs {config.MARKET_OPEN}")
check("and continues to the closing bell", _end_h >= 16,
      f"last auto-refresh {_end_h:02d}:59, market closes 16:00")
# Checks the SOURCE, not the values. The first version of this asserted
# `SESSION_REFRESH_UNTIL != GRADE_EXIT_TIME`, which is a proxy for the thing it
# cares about and a bad one: two independent constants may legitimately hold
# the same value, and the day the grade window moved to 16:00 this check failed
# while nothing was actually wrong. What matters is that neither is DERIVED
# from the other, so moving one cannot silently move the other.
_cfg_src = open(config.__file__, encoding="utf-8").read()
check("the refresh window is its own constant, not derived from the grade exit",
      not re.search(r"SESSION_REFRESH_UNTIL\s*=\s*[^\n#]*GRADE_EXIT_TIME", _cfg_src),
      "one is computed from the other")
check("and the grade exit is not derived from the refresh window",
      not re.search(r"GRADE_EXIT_TIME\s*=\s*[^\n#]*SESSION_REFRESH_UNTIL", _cfg_src),
      "one is computed from the other")

print("[5] the daily request budget is enforced, not merely displayed")
# It was counted and shown from the start and consulted by nothing. Survivable
# while auto-refresh stopped at 12:59; running to the close spends most of the
# day's allowance on one ticker, and the cap is reached in the AFTERNOON —
# during the session rather than politely overnight.
from data import unusual_whales as uw                            # noqa: E402
check("a reserve is held back for manual work", uw.RESERVE > 0, str(uw.RESERVE))
check("the reserve covers several full builds by hand", uw.RESERVE >= 55 * 5,
      f"{uw.RESERVE} vs a ~55-request build")

_real = uw.budget
try:
    uw.budget = lambda: {"used": 0, "limit": 30000, "remaining": 30000,
                         "pct": 0.0, "date": "2026-08-10"}
    check("a fresh budget does not stand down", uw.budget_exhausted() is False)
    uw.budget = lambda: {"used": 29000, "limit": 30000,
                         "remaining": uw.RESERVE - 1, "pct": 96.7,
                         "date": "2026-08-10"}
    check("at the reserve, automatic work stands down",
          uw.budget_exhausted() is True)
    uw.budget = lambda: {"used": 30000, "limit": 30000, "remaining": 0,
                         "pct": 100.0, "date": "2026-08-10"}
    check("and stays stood down at zero", uw.budget_exhausted() is True)

    def _boom():
        raise RuntimeError("counter file unreadable")
    uw.budget = _boom
    # A broken counter must not be able to stop refreshing — that would turn a
    # bookkeeping fault into a dead board.
    check("a counter failure never stands the refresh down",
          uw.budget_exhausted() is False)
finally:
    uw.budget = _real

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all constant checks passed")
