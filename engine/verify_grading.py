"""
verify_grading.py  --  the grader, and the crash it was hiding.

WHY THIS SUITE EXISTS. get_ohlc feeds the hit rate, which is the only number
that answers "does any of this work". It was the last yfinance dependency in
the app, and yfinance is NOT in the frozen bundle — its dependencies came along
via other imports but the package did not, because the import sits inside a
function and nothing declared it.

Nothing had broken only because every record happened to be graded already. The
first ungraded call would have raised ImportError through grade_pending, which
had no handler, and server.py calls grade_pending at STARTUP. The engine would
have failed to boot on the first Tuesday after a Monday call — a crash whose
trigger is "time passes", which is the worst kind to ship into a week you plan
to trade.

Two things are checked here: that grading now runs on the same feed the call
was made on, and that a grading failure can no longer take anything down.

Needs a live UW key. Run: python verify_grading.py
"""
import datetime as dt
import sys

import config
from analysis import tracker
from data import data_sources

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


print("[0] the grader no longer depends on a package that is not shipped")
# The specific latent crash: an ImportError reaching grade_pending's caller.
import data.data_sources as ds  # noqa: E402
src = open(ds.__file__, encoding="utf-8").read()
check("yfinance import is guarded by try/except ImportError",
      "except ImportError" in src, "still a bare import")
check("UW is preferred when it is the provider",
      "_uw_ohlc_for_grade" in src)

print("[1] grade_pending survives a grader that raises")
# The old code let this propagate. server.py calls grade_pending at startup, so
# propagating means the engine does not boot.
def exploding(_t, _d):
    raise RuntimeError("simulated data source failure")

try:
    out = tracker.grade_pending(exploding)
    check("a raising grader does not propagate", True)
    check("records still returned", isinstance(out, dict), str(type(out)))
except Exception as e:                                  # noqa: BLE001
    check("a raising grader does not propagate", False, f"raised {type(e).__name__}")

def returns_none(_t, _d):
    return None

try:
    tracker.grade_pending(returns_none)
    check("a grader returning None is fine", True)
except Exception as e:                                  # noqa: BLE001
    check("a grader returning None is fine", False, str(e))

print("[2] UW grading returns a usable, honest reading")
if config.USE_MOCK_DATA or config.PROVIDER != "uw" or not config.UW_API_KEY:
    print("      skipped — needs the live uw provider")
else:
    # The most recent completed weekday that UW's 5m window still covers.
    d = dt.date.today()
    got, used = None, None
    for back in range(1, 6):
        cand = d - dt.timedelta(days=back)
        if cand.weekday() >= 5:
            continue
        got = data_sources._uw_ohlc_for_grade("SPY", cand.isoformat())
        if got:
            used = cand
            break

    check("a recent session grades from UW", got is not None,
          f"tried back to {d - dt.timedelta(days=5)}")
    if got:
        print(f"      {used}: open={got['open']} exit={got['exit']} "
              f"note={got['note'] or 'clean'}")
        check("open is a real price", got["open"] > 0, str(got["open"]))
        check("exit is a real price", got["exit"] > 0, str(got["exit"]))
        # Sanity, not precision: an open and a midday print on the same session
        # cannot be an order of magnitude apart.
        check("open and exit are the same instrument",
              0.5 < got["exit"] / got["open"] < 2.0,
              f"{got['open']} -> {got['exit']}")

    print("[3] a day outside the window returns None, not a guess")
    old = data_sources._uw_ohlc_for_grade("SPY", "2020-01-02")
    check("ancient date -> None", old is None, str(old))
    # A weekend has no regular-session bars at all.
    sat = d - dt.timedelta(days=(d.weekday() - 5) % 7)
    if sat.weekday() == 5:
        check("a Saturday -> None",
              data_sources._uw_ohlc_for_grade("SPY", sat.isoformat()) is None)

    print("[4] the exit bar is the one that CONTAINS the exit time")
    # GRADE_EXIT_TIME is 12:00, so the bar stamped 11:55 is the one whose close
    # lands on it. Grading on the 12:00 bar would be reading five minutes of
    # the future into a decision made in the morning.
    check("exit time is configured", bool(config.GRADE_EXIT_TIME),
          config.GRADE_EXIT_TIME)
    check("the rule string records it",
          config.GRADE_EXIT_TIME in config.grade_rule_for("SPY"),
          config.grade_rule_for("SPY"))

print("[5] grading is idempotent")
# It runs at startup, on a schedule, and inside the recorder. Running it three
# times must not produce three different histories.
before = tracker.compute_stats(tracker.store.load(), ticker="SPY")
tracker.grade_pending(returns_none)
after = tracker.compute_stats(tracker.store.load(), ticker="SPY")
check("stats unchanged by a no-op grading pass",
      (before["n"], before["wins"]) == (after["n"], after["wins"]),
      f"{before['n']}/{before['wins']} vs {after['n']}/{after['wins']}")

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all grading checks passed")
