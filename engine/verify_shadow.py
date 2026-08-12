"""
verify_shadow.py  --  risk sizing, the gates, and the decision journal.

Three properties matter more than the arithmetic:

  A REFUSAL IS A DECISION. How often a setup appears at all is the first thing
  worth knowing about this strategy, and it is invisible if only fills are
  written down.

  THE JOURNAL DEDUPES ON SITUATION, NOT ON TEXT. The mid-range refusal carries
  a distance that moves every tick; logging the raw reason would bury the two
  lines a day that matter under four hundred that do not.

  A JOURNAL FAILURE NEVER TAKES THE DECISION DOWN. The decision already
  happened; losing the note is the smaller loss.

Sandboxed vault, no engine, no network.

    python verify_shadow.py
"""
import datetime as dt
import io
import os
import shutil
import sys
import tempfile

import config

_real_vault = config.OBSIDIAN_VAULT
_VAULT = tempfile.mkdtemp(prefix="verify_shadow_")
config.OBSIDIAN_VAULT = _VAULT

from analysis import plan                                        # noqa: E402
from strategy import decision_log, risk, shadow                  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def board(spot=772.0, regime="positive", bias="NEUTRAL / RANGE", events=(), **o):
    g = {"spot": spot, "regime": regime, "net_gex": 2e9, "gamma_flip": 765.0,
         "call_wall": 780.0, "put_wall": 770.0, "control_node": 776.0,
         "put_call_ratio": 1.2, "atm_iv": 0.10,
         "profile": [{"strike": 760.0 + i, "gex": (i - 10) * 1e8} for i in range(30)]}
    g.update(o)
    em = {"dollars": 5.0, "pct": 0.65, "low": spot - 5, "high": spot + 5, "dte": 1}
    return {"ticker": "SPY", "gex": g, "expected_move": em,
            "bias": {"label": bias, "score": -1.8, "conviction": "normal"},
            "plan": plan.build(g, em=em), "confluences": [],
            "calendar": {"high_impact": list(events)}}


def chain(bid=1.95, ask=2.00):
    def r(k, d, s):
        return {"strike": k, "oi": 5000, "iv": 0.10, "t_years": 1 / 365, "oi_change": 0,
                "symbol": s, "bid": bid, "ask": ask, "delta": d, "volume": 5000}
    return [{"label": "2026-08-14", "dte": 1,
             "calls": [r(772.0, 0.45, "SPY260814C00772000")],
             "puts": [r(772.0, -0.45, "SPY260814P00772000")]}]


T = lambda h, m: dt.datetime(2026, 8, 13, h, m, tzinfo=config.TZ)   # noqa: E731

print("[1] sizing reports BOTH the estimate and the guarantee")
# Sizing on full premium makes every position one contract and wastes the stop.
# Sizing on the delta estimate alone hides that the option can go to zero. Both
# numbers ride on the record so the gap is never invisible.
s = risk.size({"entry": 770.0, "stop": 769.0,
               "contract": {"mid": 1.97, "delta": 0.45}})
check("sized", s["ok"], s.get("reason", ""))
check("estimate at the stop is inside the risk budget",
      s["est_loss_at_stop"] <= s["risk_budget"],
      f"{s['est_loss_at_stop']} of {s['risk_budget']}")
check("max loss is the full premium and is LARGER",
      s["max_loss"] > s["est_loss_at_stop"],
      f"{s['max_loss']} vs {s['est_loss_at_stop']}")
check("the binding constraint is named", bool(s["binding_constraint"]),
      s["binding_constraint"])
check("the note says the estimate assumes you get out at the stop",
      "gap" in s["note"], s["note"][:60])

print("[2] sizing refuses rather than rounding to zero")
big = risk.size({"entry": 770.0, "stop": 740.0,
                 "contract": {"mid": 40.0, "delta": 0.90}})
check("one contract over budget is refused", not big["ok"])
check("and says what it would have cost", "premium" in big["reason"],
      big["reason"][:70])
for bad, why in (({"entry": 770.0, "stop": 770.0,
                   "contract": {"mid": 2.0, "delta": 0.4}}, "no room"),
                 ({"entry": 770.0, "contract": {"mid": 2.0, "delta": 0.4}}, "no stop"),
                 ({"entry": 770.0, "stop": 769.0, "contract": {"mid": 2.0}}, "no delta"),
                 ({"entry": 770.0, "stop": 769.0}, "no contract")):
    check(f"refused: {why}", not risk.size(bad)["ok"])

print("[3] the gates block for reasons unrelated to the read")
i = {"entry": 770.0, "stop": 769.0, "reward_points": 6.0, "risk_points": 1.0,
     "contract": {"mid": 1.97, "delta": 0.45}}
check("clean at 10:30", risk.gate(i, snap=board(), now=T(10, 30))["ok"])
for label, kw, frag in (
        ("before the open", {"now": T(9, 0)}, "Before the open"),
        ("after the cutoff", {"now": T(15, 45)}, "After 15:30"),
        ("daily cap hit", {"now": T(10, 30), "day_pnl": -500.0}, "day is done"),
        ("already positioned", {"now": T(10, 30), "open_positions": 1}, "already open")):
    g = risk.gate(i, snap=board(), **kw)
    check(f"blocked: {label}", not g["ok"] and any(frag in b for b in g["blocks"]),
          g["blocks"][0][:60] if g["blocks"] else "not blocked")

print("[4] the calendar the board already has becomes a veto")
ev = [{"title": "Core CPI m/m", "at": "2026-08-13T10:40:00-04:00"}]
g = risk.gate(i, snap=board(events=ev), now=T(10, 30))
check("inside the pre-release window is blocked", not g["ok"])
check("the event is named", "Core CPI" in g["blocks"][0], g["blocks"][0][:60])
g = risk.gate(i, snap=board(events=ev), now=T(10, 0))
check("40 minutes out warns rather than blocks",
      g["ok"] and any("Core CPI" in w for w in g["warnings"]), str(g["warnings"]))

print("[5] a refusal is a decision, fully formed")
d = shadow.evaluate(board(spot=775.0), expiries=chain(), now=T(10, 30))
check("not available", d["available"] is False)
check("but still a record with a timestamp and a ticker",
      bool(d["at"]) and d["ticker"] == "SPY")
check("carrying the regime and the read", d["regime"] == "positive"
      and d["bias"] == "NEUTRAL / RANGE")
check("and a reason", "mid-range" in d["reason"], d["reason"][:60])

print("[6] a live setup carries everything needed to act or to review")
d = shadow.evaluate(board(spot=770.0), expiries=chain(), now=T(10, 30))
check("actionable", d.get("actionable"), d.get("reason", ""))
for k in ("playbook", "direction", "entry", "target", "stop", "contract",
          "sizing", "board", "blocks", "warnings"):
    check(f"has {k}", k in d)
check("sizing produced contracts", d["sizing"]["contracts"] >= 1,
      str(d["sizing"]["contracts"]))

print("[7] blocked-but-real is distinct from no-setup")
# The difference matters: one says the signal produced nothing, the other says
# it produced something the risk layer declined. Only the first is about the
# signal.
d = shadow.evaluate(board(spot=770.0), expiries=chain(), now=T(15, 45))
check("the setup still exists", d["available"] is True)
check("but it is not actionable", d["actionable"] is False)
check("and the block is recorded", bool(d["blocks"]), str(d["blocks"])[:60])

print("[8] the journal dedupes on SITUATION, not on text")
shadow._last.clear()
w1 = shadow.record(shadow.evaluate(board(spot=775.0), expiries=chain(), now=T(9, 35)))
w2 = shadow.record(shadow.evaluate(board(spot=774.8), expiries=chain(), now=T(9, 40)))
check("first refusal is written", w1["written"])
check("the same situation a tick later is NOT",
      not w2["written"], w2.get("why", ""))
w3 = shadow.record(shadow.evaluate(board(spot=770.0), expiries=chain(), now=T(10, 15)))
check("arriving at the wall IS written", w3["written"])
check("and gets its own note", bool(w3["note"]), str(w3["note"]))
w4 = shadow.record(shadow.evaluate(board(spot=770.0), expiries=chain(), now=T(10, 20)),
                   force=True)
check("force overrides the dedup", w4["written"])

print("[9] the notes are what Obsidian needs")
day = io.open(os.path.join(_VAULT, "NYAM Decisions", "2026-08-13.md"),
              encoding="utf-8").read()
check("the day log holds both refusals and the setup",
      "no trade" in day and "range long" in day)
check("and links the decision note", "[[" in day and "|note]]" in day)
note_files = [f for f in os.listdir(os.path.join(_VAULT, "NYAM Decisions"))
              if f != "2026-08-13.md"]
check("a decision note exists", len(note_files) >= 1, str(note_files))
note = io.open(os.path.join(_VAULT, "NYAM Decisions", note_files[0]),
               encoding="utf-8").read()
check("frontmatter opens the file", note.startswith("---\n"))
for field in ("playbook:", "regime:", "delta:", "round_trip_pct:", "max_loss:",
              "board_verdict:", "tags:"):
    check(f"queryable field {field}", field in note)
check("it asks whether the READ or the EXPRESSION was wrong",
      "EXPRESSION" in note)
check("no orphaned temp files",
      not [f for f in os.listdir(os.path.join(_VAULT, "NYAM Decisions"))
           if f.endswith(".tmp")])

print("[10] a journal that cannot write does not take the decision down")
_dir = decision_log._dir
decision_log._dir = lambda: "//nonexistent-share/nope"
try:
    out = shadow.record(shadow.evaluate(board(spot=770.0), expiries=chain(),
                                        now=T(11, 0)), force=True)
    check("record returns rather than raising", out["written"] is False,
          out.get("why", "")[:50])
except Exception as e:                                           # noqa: BLE001
    check("record returns rather than raising", False, f"{type(e).__name__}: {e}")
finally:
    decision_log._dir = _dir
d = shadow.evaluate(board(spot=770.0), expiries=chain(), now=T(11, 0))
check("and the decision itself is unaffected", d["actionable"] is True)

print("[11] nothing in the strategy layer can place an order")
import glob                                                      # noqa: E402
banned = ("place_order", "submit_order", "robinhood", "place_option_order",
          "requests.post", "urlopen")
hits = []
for p in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "strategy", "*.py")):
    src = io.open(p, encoding="utf-8").read().lower()
    for b in banned:
        if b in src:
            hits.append(f"{os.path.basename(p)}:{b}")
check("no execution call anywhere in strategy/", not hits, str(hits))

shutil.rmtree(_VAULT, ignore_errors=True)
config.OBSIDIAN_VAULT = _real_vault

print()
if FAILS:
    print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:8])}")
    sys.exit(1)
print("all shadow/risk checks passed")
