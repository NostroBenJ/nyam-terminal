"""
verify_changes.py  --  the "what moved since yesterday" panel.

This module had no suite, and it shipped with a crash on the two rows that
matter most: the regime row and the call row carry STRINGS, and `pct` was
computed without the isinstance guard that `move` already had. Subtracting two
strings raised TypeError, so an overnight regime flip — the change this panel
puts FIRST by design — took the whole panel down.

Runs against a temp capture store. No network, no engine.

    python verify_changes.py
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import config

_TMP = tempfile.mkdtemp(prefix="nyam_changes_")
config.STORE_DIR = _TMP

from analysis import changes            # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def snap(**over):
    g = {"spot": 763.0, "regime": "positive", "net_gex": 2.0e9,
         "gamma_flip": 766.0, "put_wall": 758.0, "call_wall": 775.0,
         "control_node": 765.0, "put_call_ratio": 1.20, "atm_iv": 0.16}
    g.update({k: v for k, v in over.items() if k != "bias"})
    return {"ticker": "SPY", "generated_at": "2026-08-07 16:20:00 EDT",
            "gex": g, "bias": {"label": over.get("bias", "NEUTRAL / RANGE")}}


def write_capture(date_iso: str, payload: dict):
    d = os.path.join(_TMP, "capture", date_iso)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SPY_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)


TODAY = dt.date(2026, 8, 10)

print("[1] no baseline is reported as such, not as 'nothing moved'")
out = changes.build(snap(), "SPY", today=TODAY)
check("available False", out["available"] is False, str(out["available"]))
check("changes empty", out["changes"] == [])
check("the note explains why", "starts working once one exists" in out["note"],
      out["note"][:80])

print("[2] THE BIG ONE — a regime flip does not raise")
# Strings through a numeric path. This is the row the module puts at rank 0
# "regardless of how small the number moved", so the panel died precisely when
# it had the most important thing to say.
write_capture("2026-08-07", snap(regime="positive", net_gex=2.0e9))
try:
    out = changes.build(snap(regime="negative", net_gex=-2.0e9), "SPY", today=TODAY)
    check("build() survives a regime flip", True)
except TypeError as e:
    check("build() survives a regime flip", False, str(e))
    out = None
if out:
    regime = [r for r in out["changes"] if r["key"] == "regime"]
    check("the regime row is present", len(regime) == 1, str(out["changes"]))
    check("it leads the list", out["changes"][0]["key"] == "regime",
          out["changes"][0]["key"])
    check("its move is null, not a number", regime[0]["move"] is None,
          str(regime[0]["move"]))
    check("its pct is null too", regime[0]["pct"] is None, str(regime[0]["pct"]))
    check("before/after carry the words",
          (regime[0]["before"], regime[0]["after"]) == ("positive", "negative"),
          str((regime[0]["before"], regime[0]["after"])))
    check("and it says what the flip means",
          "amplifying" in (regime[0]["note"] or ""), str(regime[0]["note"]))

print("[3] a changed call does not raise either")
write_capture("2026-08-07", snap(bias="NEUTRAL / RANGE"))
try:
    out = changes.build(snap(bias="SHORT LEAN"), "SPY", today=TODAY)
    check("build() survives a changed call label", True)
    call = [r for r in out["changes"] if r["key"] == "bias"]
    check("the call row is present", len(call) == 1, str(out["changes"]))
    check("it sorts LAST, being downstream of everything else",
          out["changes"][-1]["key"] == "bias", out["changes"][-1]["key"])
except TypeError as e:
    check("build() survives a changed call label", False, str(e))

print("[4] numeric rows still carry real arithmetic")
write_capture("2026-08-07", snap(put_wall=758.0, gamma_flip=766.14))
out = changes.build(snap(put_wall=763.0, gamma_flip=764.52), "SPY", today=TODAY)
pw = [r for r in out["changes"] if r["key"] == "put_wall"][0]
check("put wall move is +5.0", pw["move"] == 5.0, str(pw["move"]))
check("of_spot is the move as a share of spot", pw["of_spot"] == 0.66,
      str(pw["of_spot"]))
flip = [r for r in out["changes"] if r["key"] == "gamma_flip"][0]
check("flip move is -1.62", flip["move"] == -1.62, str(flip["move"]))
check("the flip outranks the walls",
      out["changes"].index(flip) < out["changes"].index(pw))

print("[5] a level on only one side is 'not a move'")
write_capture("2026-08-07", snap(call_wall=None))
out = changes.build(snap(call_wall=775.0), "SPY", today=TODAY)
cw = [r for r in out["changes"] if r["key"] == "call_wall"][0]
check("reported", cw is not None)
check("known is False", cw["known"] is False, str(cw["known"]))
check("move is null", cw["move"] is None)
check("and it says so", "only one side" in (cw["note"] or ""), str(cw["note"]))
# Absent on BOTH sides is not news at all.
write_capture("2026-08-07", snap(call_wall=None))
out = changes.build(snap(call_wall=None), "SPY", today=TODAY)
check("absent on both sides is omitted entirely",
      not [r for r in out["changes"] if r["key"] == "call_wall"])

print("[6] noise below the thresholds is not reported")
write_capture("2026-08-07", snap())
out = changes.build(snap(), "SPY", today=TODAY)
check("an identical board reports no changes", out["changes"] == [],
      str(out["changes"]))
check("and says nothing moved, rather than going quiet",
      "Nothing moved" in (out["note"] or ""), str(out["note"]))
# A one-cent wall shift is strike-granularity noise.
out = changes.build(snap(put_wall=758.01), "SPY", today=TODAY)
check("a 0.01 move on a 763 spot is below LEVEL_EPS",
      not [r for r in out["changes"] if r["key"] == "put_wall"],
      str(out["changes"]))
# But a real one is.
out = changes.build(snap(put_wall=763.0), "SPY", today=TODAY)
check("a 5-point move is reported",
      [r for r in out["changes"] if r["key"] == "put_wall"] != [])

print("[7] a missing spot does not silently swallow every level change")
# spot only provides SCALE. Skipping the comparison without it reported
# "nothing moved" for a wall that moved sixteen points.
write_capture("2026-08-07", snap(put_wall=742.0))
cur = snap(put_wall=758.0)
cur["gex"].pop("spot")
out = changes.build(cur, "SPY", today=TODAY)
pw = [r for r in out["changes"] if r["key"] == "put_wall"]
check("the level change is still reported", len(pw) == 1, str(out["changes"]))
check("with a real move", pw and pw[0]["move"] == 16.0, str(pw and pw[0]["move"]))
check("but of_spot is null rather than invented",
      pw and pw[0]["of_spot"] is None, str(pw and pw[0]["of_spot"]))

print("[8] the baseline is the newest capture STRICTLY BEFORE today")
write_capture("2026-08-05", snap(put_wall=700.0))
write_capture("2026-08-07", snap(put_wall=758.0))
write_capture("2026-08-10", snap(put_wall=999.0))       # today — must be ignored
out = changes.build(snap(put_wall=763.0), "SPY", today=TODAY)
check("picks 08-07, not 08-05", out["baseline_date"] == "2026-08-07",
      str(out["baseline_date"]))
check("today's own capture is not used as its own baseline",
      out["baseline_date"] != "2026-08-10")
check("age in days is reported", out["age_days"] == 3, str(out["age_days"]))

print("[9] a stale baseline is flagged, not quietly used")
# Comparing Monday against the previous Tuesday is legitimate; doing it
# silently is not.
check("3 days is not stale", out["stale_baseline"] is False,
      str(out["stale_baseline"]))
shutil.rmtree(os.path.join(_TMP, "capture"), ignore_errors=True)
write_capture("2026-07-28", snap(put_wall=700.0))
out = changes.build(snap(put_wall=763.0), "SPY", today=TODAY)
check("13 days is stale", out["stale_baseline"] is True, str(out["age_days"]))

print("[10] a corrupt capture is skipped, not fatal")
shutil.rmtree(os.path.join(_TMP, "capture"), ignore_errors=True)
write_capture("2026-08-06", snap(put_wall=742.0))
bad = os.path.join(_TMP, "capture", "2026-08-07")
os.makedirs(bad, exist_ok=True)
with open(os.path.join(bad, "SPY_snapshot.json"), "w", encoding="utf-8") as f:
    f.write("{truncated")
out = changes.build(snap(put_wall=763.0), "SPY", today=TODAY)
check("falls back past the corrupt day", out["baseline_date"] == "2026-08-06",
      str(out["baseline_date"]))
check("and still reports the change",
      [r for r in out["changes"] if r["key"] == "put_wall"] != [])
# A non-date directory must not derail the walk either.
os.makedirs(os.path.join(_TMP, "capture", "notadate"), exist_ok=True)
out = changes.build(snap(put_wall=763.0), "SPY", today=TODAY)
check("a non-date folder is ignored", out["available"] is True)

print("[11] every row carries the fields the UI reads")
for r in out["changes"]:
    ok = all(k in r for k in ("key", "label", "before", "after", "move", "pct",
                              "of_spot", "rank", "kind", "note", "known"))
    check(f"{r['key']} is well-formed", ok, str(sorted(r)))
check("rows are sorted by rank",
      [r["rank"] for r in out["changes"]] == sorted(r["rank"] for r in out["changes"]),
      str([r["rank"] for r in out["changes"]]))

shutil.rmtree(_TMP, ignore_errors=True)
print()
if FAILS:
    print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:8])}")
    sys.exit(1)
print("all changes checks passed")
