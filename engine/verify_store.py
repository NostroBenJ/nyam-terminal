"""
verify_store.py  --  the track record survives things going wrong.

This file holds the ONLY irreplaceable data in the app. A chain can be
re-fetched tomorrow; a call made on Tuesday morning and the reasoning behind it
cannot be reconstructed from anything.

Both faults tested here were reproduced against a copy of the real file before
being fixed:

  save() opened the target directly, truncating before writing, so a process
  killed mid-write left a half-file. load() then returned {} on any exception
  and the next save wrote that {} over the top — 4 graded calls became 0, with
  the 5,278 original bytes still readable on disk at the moment they were
  erased.

The governing rule, and what every check below is really testing: A READ THAT
FAILS MUST NEVER CAUSE A WRITE THAT LOSES.

Runs entirely in a sandbox. Never touches the real record.
"""
import json
import os
import shutil
import sys
import tempfile

import config

_fail = 0


def check(name, ok, detail="", on_fail=""):
    global _fail
    if not ok:
        _fail += 1
    extra = detail or (on_fail if not ok else "")
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))


_real_store = config.STORE_DIR
sandbox = tempfile.mkdtemp(prefix="verify_store_")
config.STORE_DIR = sandbox

import store  # noqa: E402  imported after the redirect

SAMPLE = {f"SPY:2026-08-{d:02d}": {"date": f"2026-08-{d:02d}", "ticker": "SPY",
                                   "bias": "NEUTRAL / RANGE", "score": -1.8,
                                   "note": "an em-dash — and a σ, for encoding"}
          for d in range(3, 8)}


def reset(with_backup=True):
    """
    Fresh sandbox holding SAMPLE.

    Saved TWICE by default, because save() backs up the file it is about to
    replace — so a brand-new store has no backup until its second write. That
    is the real steady state (records.json already exists, so the first save of
    any session makes a backup) and the test should model it rather than an
    idealised one.
    """
    for f in os.listdir(sandbox):
        os.remove(os.path.join(sandbox, f))
    store.save(SAMPLE)
    if with_backup:
        store.save(SAMPLE)


print("[0] round trip, including non-ASCII")
reset()
back = store.load()
check("all records survive", len(back) == len(SAMPLE), f"{len(back)}/{len(SAMPLE)}")
check("non-ASCII survives", "—" in list(back.values())[0]["note"],
      on_fail="em-dash or sigma mangled")

print("[1] THE BIG ONE — a truncated file does not become deletion")
p = store._path()
whole = open(p, encoding="utf-8").read()
with open(p, "w", encoding="utf-8") as f:
    f.write(whole[: len(whole) // 2])           # killed mid-write
recovered = store.load()
check("load recovers from the backup rather than returning empty",
      len(recovered) == len(SAMPLE), f"got {len(recovered)}")
# And the killer: the next ordinary save must not erase anything.
store.save(recovered)
on_disk = json.load(open(store._path(), encoding="utf-8"))
check("the next save preserves the history", len(on_disk) == len(SAMPLE),
      f"{len(on_disk)} on disk")

print("[2] the corrupt bytes are kept, not thrown away")
quarantined = [f for f in os.listdir(sandbox) if ".corrupt." in f]
check("the unreadable file is quarantined with a timestamp", len(quarantined) >= 1,
      str(quarantined))
if quarantined:
    size = max(os.path.getsize(os.path.join(sandbox, q)) for q in quarantined)
    check("and still holds the original bytes for hand recovery", size > 100,
          f"{size} bytes")

print("[3] no backup AND corrupt input -> empty, but the file is still kept")
# The worst case: first-ever write, then corruption. Nothing to recover FROM,
# so the guarantee narrows to "the bytes are still on disk" — which is the
# guarantee that actually matters, since it is the difference between a
# recoverable morning and a lost history.
reset(with_backup=False)
p = store._path()
with open(p, "w", encoding="utf-8") as f:
    f.write("{not json at all")
out = store.load()
check("returns empty rather than raising", out == {}, str(out)[:40])
check("but the bad file was preserved",
      any(".corrupt." in f for f in os.listdir(sandbox)))

print("[4] writes are atomic")
src = open(store.__file__, encoding="utf-8").read()
check("uses a temp file", "mkstemp" in src)
check("uses os.replace, not a plain rename or direct write", "os.replace" in src)
check("fsyncs before the swap", "fsync" in src,
      on_fail="a rename is pointless if the bytes are still in the OS cache")
check("does not open the live path for writing directly",
      'open(_path(), "w")' not in src.replace(" ", ""))

print("[5] a failed serialise leaves no litter and no damage")
reset()
before_files = set(os.listdir(sandbox))
try:
    store.save({"bad": {1, 2, 3}})              # a set is not JSON-serialisable
except (TypeError, ValueError):
    pass
after_files = set(os.listdir(sandbox))
check("no .tmp file orphaned",
      not [f for f in after_files - before_files if f.endswith(".tmp")],
      str(sorted(after_files - before_files)))
check("the existing record is untouched", len(store.load()) == len(SAMPLE),
      f"{len(store.load())}")

print("[6] wrong types are rejected at the door")
for bad in (None, [], "records", 42):
    try:
        store.save(bad)
        check(f"save({type(bad).__name__}) is refused", False,
              on_fail="it was accepted")
    except TypeError:
        check(f"save({type(bad).__name__}) is refused", True)

print("[7] a missing file is normal, not an error")
for f in os.listdir(sandbox):
    os.remove(os.path.join(sandbox, f))
check("no file -> empty dict, nothing quarantined",
      store.load() == {} and not os.listdir(sandbox))

shutil.rmtree(sandbox, ignore_errors=True)
config.STORE_DIR = _real_store

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all store checks passed")
