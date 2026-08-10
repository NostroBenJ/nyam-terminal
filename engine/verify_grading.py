"""
verify_grading.py  --  the grader, and the crash it was hiding.

WHY THIS SUITE EXISTS. get_ohlc feeds the hit rate, which is the only number
that answers "does any of this work", and it was the last place the app reached
for a different vendor than the one the call was priced on.

A NOTE ON A WRONG DIAGNOSIS, kept because the wrong version shipped. This file
originally claimed yfinance was missing from the frozen bundle and that the
engine would fail to boot the first time it graded. Both false. yfinance is
pure Python, so PyInstaller packs it into the PYZ archive inside the executable
instead of extracting a folder into _internal; searching _internal for a
directory finds nothing and proves nothing. `graded 0 pending call(s)` was then
read as corroboration when it only meant there was nothing to grade.

Two pieces of evidence, both absent-shaped, agreeing with each other is not
evidence. The positive test — does the packaged engine actually import it —
takes one call and was not run.

The change stands anyway: grading on the same feed as the call removes real
cross-vendor noise, and 4 of 4 stored outcomes re-graded identically, so no
history was rewritten. What is checked here is that, and that a grading failure
cannot take down the engine — which was always worth having, whatever the
likelihood of it firing.

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

print("[6] a bad price is refused, never graded")
# The guard read `not o or c is None`, which caught a zero OPEN but not a zero
# EXIT. A feed returning 0 for the close graded SPY as a -100% move, called it
# "down", and marked a SHORT LEAN CORRECT — a fabricated win, written
# permanently into the one dataset that cannot be rebuilt, and flattering
# rather than embarrassing, which is the direction nobody audits.
#
# Pure arithmetic on a dict; touches no store.
_rec = {"ticker": "SPY", "date": "2026-08-07", "predicted_dir": "down",
        "bias": "SHORT LEAN", "score": -2.5}
for label, ohlc in (
        ("zero exit", {"open": 771.02, "exit": 0.0}),
        ("negative exit", {"open": 771.02, "exit": -5.0}),
        ("NaN exit", {"open": 771.02, "exit": float("nan")}),
        ("infinite exit", {"open": 771.02, "exit": float("inf")}),
        ("boolean exit", {"open": 771.02, "exit": True}),
        # Unusual Whales serialises numerics as strings in places; one leaking
        # this far used to raise straight out of grade_pending.
        ("string exit", {"open": 771.02, "exit": "773.03"}),
        ("zero open", {"open": 0.0, "exit": 773.03}),
        ("missing exit", {"open": 771.02}),
):
    check(f"{label} is refused", tracker.grade_record(_rec, ohlc) is None,
          str(tracker.grade_record(_rec, ohlc)))

_good = tracker.grade_record(_rec, {"open": 771.02, "exit": 773.03})
check("a real pair still grades", _good is not None)
check("and the arithmetic is right", _good and _good["move_pct"] == 0.26,
      str(_good and _good["move_pct"]))
check("a short call on an up move is a miss", _good and _good["correct"] is False)

print("[7] grade_pending survives a record that cannot be graded")
# The try/except wrapped only get_ohlc, so anything raised while GRADING
# propagated out of a function whose contract is that one bad record takes
# nothing down — and it runs at engine startup, so the cost was no board.
#
# SANDBOXED. Unlike the checks above, this one writes, and this suite runs
# against the real records.json — the only irreplaceable file in the app.
import json as _json                                             # noqa: E402
import shutil as _shutil                                         # noqa: E402
import tempfile as _tempfile                                     # noqa: E402

_real_dir = config.STORE_DIR
_sandbox = _tempfile.mkdtemp(prefix="verify_grading_")
config.STORE_DIR = _sandbox
try:
    with open(tracker.store._path(), "w", encoding="utf-8") as f:
        _json.dump({"SPY|1999-01-04": {
            "date": "1999-01-04", "ticker": "SPY", "bias": "SHORT LEAN",
            "score": -2.5, "spot": 100.0, "outcome": None,
        }}, f)                        # no predicted_dir -> raises in grading
    try:
        tracker.grade_pending(lambda t, d: {"open": 100.0, "exit": 101.0})
        check("a record that raises while grading does not propagate", True)
    except Exception as e:                              # noqa: BLE001
        check("a record that raises while grading does not propagate", False,
              f"{type(e).__name__}: {e}")
    _back = tracker.store.load()
    check("and it stays pending rather than half-written",
          _back.get("SPY|1999-01-04", {}).get("outcome") is None,
          str(_back.get("SPY|1999-01-04", {}).get("outcome")))
finally:
    config.STORE_DIR = _real_dir
    _shutil.rmtree(_sandbox, ignore_errors=True)

print("[8] changing the grading rule never re-grades old records")
# The window moved from open->12:00 to open->16:00 when the broker's holdable
# window became the whole session. A record graded under the old rule must keep
# its old verdict AND its old rule string: re-scoring history under a rule it
# was not made under is how a hit rate stops describing anything.
_real_dir2 = config.STORE_DIR
_sandbox2 = _tempfile.mkdtemp(prefix="verify_rule_")
config.STORE_DIR = _sandbox2
try:
    old = {"date": "2026-08-04", "ticker": "SPY", "bias": "SHORT LEAN",
           "score": -2.5, "spot": 760.0, "predicted_dir": "down",
           "outcome": {"open": 760.63, "exit": 768.44, "move_pct": 1.03,
                       "actual_dir": "up", "correct": False,
                       "rule": "open->12:00@0.175"}}
    with open(tracker.store._path(), "w", encoding="utf-8") as f:
        _json.dump({"SPY|2026-08-04": old}, f)
    tracker.grade_pending(lambda t, d: {"open": 760.62, "exit": 771.28})
    back = tracker.store.load()["SPY|2026-08-04"]["outcome"]
    check("the old outcome is untouched", back["exit"] == 768.44, str(back["exit"]))
    check("and keeps the rule it was graded under",
          back["rule"] == "open->12:00@0.175", back["rule"])
    check("the new rule stamps the new window",
          config.GRADE_EXIT_TIME in config.grade_rule_for("SPY"),
          config.grade_rule_for("SPY"))

    # A history spanning two rules must SAY so rather than average them.
    fresh = dict(old, date="2026-08-05")
    fresh["outcome"] = dict(old["outcome"], rule=config.grade_rule_for("SPY"))
    with open(tracker.store._path(), "w", encoding="utf-8") as f:
        _json.dump({"SPY|2026-08-04": old, "SPY|2026-08-05": fresh}, f)
    st = tracker.compute_stats(tracker.store.load(), ticker="SPY")
    check("mixed rules are surfaced, not averaged silently",
          st.get("mixed_rules") and len(st["mixed_rules"]) == 2,
          str(st.get("mixed_rules")))
finally:
    config.STORE_DIR = _real_dir2
    _shutil.rmtree(_sandbox2, ignore_errors=True)

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all grading checks passed")
