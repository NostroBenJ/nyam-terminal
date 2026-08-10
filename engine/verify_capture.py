"""
verify_capture.py  --  Is a captured day actually a DATASET, or just a log?

The whole claim behind storing the raw chain is that you can redo the math on
it later — recompute GEX a different way, or check that a change to the pricing
core reproduces a day you already know. That claim is worth proving rather than
asserting: this loads a captured chain, recomputes GEX from scratch, and checks
the result matches the snapshot recorded that day.

If this passes, every captured day is replayable. If it fails, the capture is a
diary, not data.

    python verify_capture.py [YYYY-MM-DD] [TICKER]
"""
import json
import os
import re
import shutil
import sys
import tempfile

import capture
import config

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if (not ok and detail) else ""))
    if not ok:
        FAILS.append(name)


def latest_capture():
    d = os.path.join(config.STORE_DIR, "capture")
    if not os.path.isdir(d):
        return None
    days = sorted((x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))),
                  reverse=True)
    for day in days:
        files = os.listdir(os.path.join(d, day))
        if any(f.endswith("_chain.json") for f in files):
            return day
    return None


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else latest_capture()
    ticker = (sys.argv[2] if len(sys.argv) > 2 else config.PRIMARY_TICKER).upper()
    if not day:
        print("no capture with a raw chain found — run: python capture.py --force")
        raise SystemExit(0)

    base = os.path.join(config.STORE_DIR, "capture", day)
    print(f"replaying {ticker} from {day}")

    chain_p = os.path.join(base, f"{ticker}_chain.json")
    snap_p = os.path.join(base, f"{ticker}_snapshot.json")
    check("raw chain on disk", os.path.exists(chain_p), chain_p)
    check("snapshot on disk", os.path.exists(snap_p), snap_p)
    if FAILS:
        raise SystemExit(1)

    with open(chain_p, encoding="utf-8") as f:
        raw = json.load(f)
    with open(snap_p, encoding="utf-8") as f:
        snap = json.load(f)

    print("[1] the chain is complete enough to price")
    exps = raw.get("expiries", [])
    check("expiries present", len(exps) > 0, str(len(exps)))
    contracts = [c for e in exps for c in e.get("calls", []) + e.get("puts", [])]
    check("contracts present", len(contracts) > 0, str(len(contracts)))
    for field in ("strike", "oi", "iv", "t_years"):
        check(f"every contract has {field}",
              all(field in c for c in contracts),
              f"{sum(1 for c in contracts if field not in c)} missing")
    check("spot recorded", isinstance(raw.get("spot"), (int, float)), str(raw.get("spot")))

    print("[2] GEX recomputes from the raw chain")
    # The real test: run the same pricing core over the stored inputs and see
    # whether it reproduces what the board showed that day.
    from analysis import gex as gex_mod
    merged = {"calls": [], "puts": []}
    for e in exps:
        merged["calls"] += e.get("calls", [])
        merged["puts"] += e.get("puts", [])
    redone = gex_mod.compute_gex(merged, raw["spot"], config.RISK_FREE_RATE)
    orig = snap["gex"]

    for field in ("regime", "gamma_flip", "call_wall", "put_wall", "control_node"):
        check(f"{field} reproduces", redone.get(field) == orig.get(field),
              f"replay={redone.get(field)} captured={orig.get(field)}")

    for field, tol in (("net_gex", 1.0), ("atm_iv", 1e-9), ("put_call_ratio", 1e-9)):
        a, b = redone.get(field), orig.get(field)
        ok = a is not None and b is not None and abs(a - b) <= tol
        check(f"{field} reproduces", ok, f"replay={a} captured={b}")

    check("profile length matches",
          len(redone.get("profile", [])) == len(orig.get("profile", [])),
          f"{len(redone.get('profile', []))} vs {len(orig.get('profile', []))}")

    print("[3] the session context needed to re-derive a bias is there")
    for field in ("prior_high", "prior_low", "prior_close", "on_high", "on_low",
                  "on_is_real"):
        check(f"session.{field}", field in (raw.get("session") or {}), "missing")
    check("provider recorded", bool(raw.get("provider")), str(raw.get("provider")))
    check("capture timestamp", bool(raw.get("captured_at")))

    print("[4] the manifest describes the day")
    mp = os.path.join(base, "manifest.json")
    check("manifest present", os.path.exists(mp))
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        check("records provider", bool(m.get("provider")), str(m.get("provider")))
        check("records whether a UW key was present", "uw_key" in m)
        check("records grading outcome", "graded" in m)
        # Read by changes.prior_capture to decide what "yesterday" means. A
        # forced weekend capture is a full, valid folder and was being used as
        # a Monday baseline until this flag was consulted.
        check("records whether it was a trading day", "trading_day" in m,
              str(m.get("trading_day")))

    print("[5] writes are atomic and survive two writers")
    # The three scheduled captures write into the SAME dated folder, and
    # nothing stops a manual run overlapping one. capture._write used
    # `path + ".tmp"` — one predictable name for every writer — which is how
    # oi_store came to install a corrupt file ATOMICALLY, looking valid.
    import threading                                             # noqa: E402
    src = open(capture.__file__, encoding="utf-8").read()
    check("uses a unique temp name", "mkstemp" in src)
    check("fsyncs before the swap", "fsync" in src,
          "a rename is atomic for the directory entry, not for the bytes")
    # Match the ASSIGNMENT, not the phrase. The first version searched for
    # `path + ".tmp"` anywhere in the file and tripped on the docstring that
    # explains why the code no longer does it — a check failing on its own
    # explanation, which is worse than no check because it teaches you to
    # ignore the suite.
    check("does not open a predictable temp path",
          not re.search(r'tmp\s*=\s*path\s*\+\s*"\.tmp"', src))

    scratch = tempfile.mkdtemp(prefix="verify_capture_")
    target = os.path.join(scratch, "contended.json")
    gate = threading.Barrier(6)
    errs = []

    def _w(n):
        payload = {"writer": n, "rows": [{"strike": 700 + i} for i in range(400)]}
        gate.wait()
        try:
            capture._write(target, payload)
        except Exception as exc:                                 # noqa: BLE001
            errs.append(f"{type(exc).__name__}: {exc}")

    ts = [threading.Thread(target=_w, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("no writer raised", not errs, "; ".join(errs[:2]))
    try:
        with open(target, encoding="utf-8") as f:
            got = json.load(f)
        check("the installed file is readable", True)
        check("and complete", len(got.get("rows", [])) == 400, str(len(got.get("rows", []))))
    except Exception as e:                                       # noqa: BLE001
        check("the installed file is readable", False, f"{type(e).__name__}: {e}")
    check("no orphaned temp files",
          not [f for f in os.listdir(scratch) if f.endswith(".tmp")],
          str([f for f in os.listdir(scratch) if f.endswith(".tmp")]))
    shutil.rmtree(scratch, ignore_errors=True)

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:6])}")
        print("The capture is a log, not a dataset — replay does not reproduce it.")
        raise SystemExit(1)
    print(f"all capture checks passed — {day} {ticker} replays exactly")


if __name__ == "__main__":
    main()
