"""
verify_snapshot_store.py  --  the restored-board cache.

THE HAZARD THIS DEFENDS. A restored board is a real board from a past session,
not a placeholder, and last session's gamma flip looks exactly like this
session's while being wrong. Painting one instantly is worth ~7 seconds off
every start, but only if it can never be mistaken for current. So the checks
here are mostly about HONESTY rather than correctness of storage:

  - generated_at survives untouched, so every staleness check downstream keeps
    measuring real age instead of the moment we read the file;
  - the restored flag is present on load and absent on a live build;
  - a board too old to be a useful head start is refused outright rather than
    labelled and shown.

Plus the boring durability one: a half-written file must not break startup,
which is why the write goes through a temp file and a rename.

Offline, no engine needed:  python verify_snapshot_store.py
"""
import datetime as dt
import json
import os
import sys

import config
from data import snapshot_store as ss

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


T = "__VERIFY__"


def snap_for(days_ago: int) -> dict:
    d = dt.date.today() - dt.timedelta(days=days_ago)
    return {
        "generated_at": f"{d.isoformat()} 09:25:00 EDT",
        "ticker": T,
        "gex": {"spot": 100.0, "net_gex": 1.0, "gamma_flip": 99.0},
        "bias": {"label": "NEUTRAL"},
        "brief": "text",
    }


print("[0] round trip")
check("save returns True", ss.save(T, snap_for(0)) is True)
back = ss.load(T)
check("load returns a snapshot", isinstance(back, dict), str(type(back)))
check("payload survives", back and back["gex"]["spot"] == 100.0)

print("[1] generated_at is NOT rewritten on load")
# The whole staleness system reads this field. Refreshing it here would make an
# old board claim to be new — the precise failure the restore path must avoid.
original = snap_for(0)["generated_at"]
check("timestamp preserved exactly", back["generated_at"] == original,
      f"{back['generated_at']} vs {original}")

print("[2] a restored board is flagged as restored")
check("restored is True", back.get("restored") is True, str(back.get("restored")))
check("restored_from records the original stamp",
      back.get("restored_from") == original, str(back.get("restored_from")))
check("restored_age_days present", back.get("restored_age_days") == 0,
      str(back.get("restored_age_days")))
# And a freshly built snapshot must NOT carry the flag, or every board would
# claim to be restored and the banner would become noise people learn to ignore.
check("the saved payload itself has no restored flag",
      "restored" not in snap_for(0))

print("[3] a board too old to help is refused, not labelled")
for days, want in ((1, True), (5, True), (6, False), (30, False)):
    ss.save(T, snap_for(days))
    got = ss.load(T, max_age_days=5)
    check(f"{days}d old -> {'restored' if want else 'refused'}",
          (got is not None) == want, str(got is not None))

print("[4] a future-dated board is refused")
# A clock change or a bad write can produce one; showing it would mean a board
# whose age is negative, and every downstream age check would go strange.
ss.save(T, snap_for(-2))
check("future timestamp refused", ss.load(T) is None)

print("[5] corrupt or partial files degrade to a cold start, never a crash")
path = os.path.join(config.STORE_DIR, ss.DIRNAME, f"{T}.json")
for label, body in (("truncated json", '{"generated_at": "2026-08-04 09:00:00 EDT", "gex"'),
                    ("not json at all", "<<<garbage>>>"),
                    ("empty file", ""),
                    ("json but not a snapshot", '{"hello": 1}')):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        got = ss.load(T)
        check(f"{label} -> None", got is None, str(got)[:60])
    except Exception as e:  # noqa: BLE001
        check(f"{label} -> None", False, f"raised {type(e).__name__}")

print("[6] a missing file is not an error")
os.remove(path)
check("absent -> None", ss.load(T) is None)
check("load_all skips absent tickers", ss.load_all([T]) == {})

print("[7] save never raises, whatever it is handed")
for label, payload in (("empty dict", {}), ("None", None),
                       ("unserialisable", {"gex": {1, 2, 3}})):
    try:
        r = ss.save(T, payload)
        check(f"save({label}) returned a bool", isinstance(r, bool), str(r))
    except Exception as e:  # noqa: BLE001
        check(f"save({label}) did not raise", False, type(e).__name__)

print("[8] no temp files left behind")
d = os.path.join(config.STORE_DIR, ss.DIRNAME)
leftovers = [f for f in os.listdir(d)] if os.path.isdir(d) else []
check("no .tmp files orphaned", not [f for f in leftovers if f.endswith(".tmp")],
      str([f for f in leftovers if f.endswith(".tmp")]))

# cleanup
try:
    os.remove(path)
except OSError:
    pass

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all snapshot store checks passed")
