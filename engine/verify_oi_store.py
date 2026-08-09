"""
verify_oi_store.py  --  Checks on the daily OI snapshot store.

The failure this guards against is subtle and expensive: `oi_change` stuck at 0
renders as "positioning didn't shift" when the truth is "we never had a
baseline". Those are different claims and the UI must be able to tell them
apart, so `available` is checked as carefully as the arithmetic.

Runs against a temp directory — never touches your real data_store.

    python verify_oi_store.py
"""
import datetime as dt
import os
import shutil
import tempfile

import config

# Redirect storage BEFORE importing the module under test.
_TMP = tempfile.mkdtemp(prefix="nyam_oi_test_")
config.STORE_DIR = _TMP

from data import oi_store                                    # noqa: E402

oi_store.DIR = os.path.join(_TMP, "oi")

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def chain(oi_map: dict, label="2026-08-07"):
    """Build a minimal expiry structure: {strike: (call_oi, put_oi)}."""
    return [{
        "label": label,
        "dte": 6,
        "calls": [{"strike": k, "oi": v[0], "iv": 0.16, "t_years": 0.02} for k, v in oi_map.items()],
        "puts": [{"strike": k, "oi": v[1], "iv": 0.16, "t_years": 0.02} for k, v in oi_map.items()],
    }]


def main():
    try:
        print("[1] first run has no baseline — and says so")
        day1 = chain({745.0: (1000, 800), 750.0: (500, 400)})
        oi_store.snapshot("SPY", day1, "2026-07-30")
        rep = oi_store.apply_oi_change("SPY", day1, "2026-07-30")
        # The whole point: absent data must not read as zero change.
        check("available False", rep["available"] is False, str(rep["available"]))
        check("note explains it's unknown, not zero",
              "unknown" in rep["note"].lower(), rep["note"])
        check("changes remain 0", all(c["oi_change"] == 0 for c in day1[0]["calls"]))

        print("[2] second session diffs against the first")
        day2 = chain({745.0: (1400, 600), 750.0: (500, 900)})
        oi_store.snapshot("SPY", day2, "2026-07-31")
        rep = oi_store.apply_oi_change("SPY", day2, "2026-07-31")
        check("available True", rep["available"] is True)
        check("baseline is the earlier date", rep["baseline_date"] == "2026-07-30",
              str(rep["baseline_date"]))
        calls = {c["strike"]: c["oi_change"] for c in day2[0]["calls"]}
        puts = {p["strike"]: p["oi_change"] for p in day2[0]["puts"]}
        check("call 745 +400", calls[745.0] == 400, str(calls[745.0]))
        check("put 745 -200", puts[745.0] == -200, str(puts[745.0]))
        check("call 750 unchanged", calls[750.0] == 0, str(calls[750.0]))
        check("put 750 +500", puts[750.0] == 500, str(puts[750.0]))
        check("matched count", rep["matched"] == 4, str(rep["matched"]))

        print("[3] a strike that is new today counts its full OI as the change")
        # Treating an unseen strike as 0 change would hide freshly opened
        # positioning — exactly the thing this signal exists to catch.
        day3 = chain({745.0: (1400, 600), 750.0: (500, 900), 760.0: (2200, 100)})
        oi_store.snapshot("SPY", day3, "2026-08-03")
        rep = oi_store.apply_oi_change("SPY", day3, "2026-08-03")
        calls = {c["strike"]: c["oi_change"] for c in day3[0]["calls"]}
        check("new strike 760 change == its OI", calls[760.0] == 2200, str(calls[760.0]))
        check("new counted separately", rep["new"] == 2, str(rep["new"]))
        check("baseline is yesterday, not the oldest", rep["baseline_date"] == "2026-07-31",
              str(rep["baseline_date"]))

        print("[4] a day's snapshot is written once, not overwritten")
        # Diffing a 15:59 capture against a 09:15 one would report intraday
        # drift as overnight positioning change.
        again = oi_store.snapshot("SPY", chain({745.0: (9999, 9999)}), "2026-08-03")
        check("second write refused", again is None, str(again))
        stored = oi_store.previous("SPY", "2026-08-04")
        check("stored value is the original",
              stored["chains"]["2026-08-07"]["C"]["745.0"] == 1400,
              str(stored["chains"]["2026-08-07"]["C"]["745.0"]))

        print("[5] tickers are isolated")
        qqq = chain({500.0: (100, 100)})
        oi_store.snapshot("QQQ", qqq, "2026-08-03")
        rep = oi_store.apply_oi_change("QQQ", qqq, "2026-08-03")
        check("QQQ has no baseline from SPY", rep["available"] is False, str(rep))

        print("[6] previous() ignores same-day and future files")
        check("strictly earlier only",
              oi_store.previous("SPY", "2026-07-30") is None,
              str(oi_store.previous("SPY", "2026-07-30")))

        print("[7] a corrupt snapshot degrades instead of crashing")
        bad = os.path.join(oi_store.DIR, "BAD_2026-07-30.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        check("corrupt file -> None", oi_store.previous("BAD", "2026-08-03") is None)

        print("[8] an unknown expiry label yields new, not a crash")
        other = chain({745.0: (10, 10)}, label="2026-09-19")
        rep = oi_store.apply_oi_change("SPY", other, "2026-08-04")
        check("unmatched expiry treated as new", rep["new"] == 2, str(rep["new"]))
        check("its change is full OI",
              other[0]["calls"][0]["oi_change"] == 10,
              str(other[0]["calls"][0]["oi_change"]))

        print("[9] a corrupt newest snapshot falls back — and admits it")
        # All four cases below were reproduced against the original code first.
        # Two good sessions on disk behind one bad file used to report "No
        # earlier snapshot yet": the recoverable case rendering as the empty
        # one, which is the exact confusion this module exists to prevent.
        for d, oi in (("2026-09-01", 1000), ("2026-09-02", 2000)):
            oi_store.snapshot("FBK", chain({700.0: (oi, oi)}, label="2026-09-18"),
                              d)
        with open(os.path.join(oi_store.DIR, "FBK_2026-09-03.json"), "w",
                  encoding="utf-8") as f:
            f.write("{truncated")
        today = chain({700.0: (2600, 2600)}, label="2026-09-18")
        rep = oi_store.apply_oi_change("FBK", today, "2026-09-04")
        check("history is NOT reported as absent", rep["available"] is True, str(rep["available"]))
        check("falls back past the corrupt file", rep["baseline_date"] == "2026-09-02",
              str(rep["baseline_date"]))
        check("the diff is real", today[0]["calls"][0]["oi_change"] == 600,
              str(today[0]["calls"][0]["oi_change"]))
        check("the degradation is flagged", rep.get("degraded") is True, str(rep.get("degraded")))
        check("and named in the note", "2026-09-03" in rep["note"], rep["note"])

        print("[10] a truncated file does not poison the date forever")
        # The original refused to write whenever os.path.exists, so one write
        # killed mid-flight cost that session's OI permanently — and OI for a
        # past date cannot be re-fetched from anywhere, at any price.
        poisoned = oi_store._path("PSN", "2026-09-08")
        with open(poisoned, "w", encoding="utf-8") as f:
            f.write('{"date": "2026-09-08", "ticker": "PSN", "chai')
        wrote = oi_store.snapshot("PSN", chain({700.0: (77, 77)}, label="2026-09-18"),
                                  "2026-09-08")
        check("the session is recoverable, not lost", wrote is not None, str(wrote))
        got = oi_store.previous("PSN", "2026-09-09")
        check("and reads back correctly",
              got and got["chains"]["2026-09-18"]["C"]["700.0"] == 77, str(got)[:60])
        # ...but a READABLE file is still written once and only once.
        check("a valid snapshot is still never overwritten",
              oi_store.snapshot("PSN", chain({700.0: (9999, 9999)}, label="2026-09-18"),
                                "2026-09-08") is None)

        print("[11] valid JSON of the wrong shape does not crash the build")
        with open(oi_store._path("LST", "2026-09-01"), "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")          # valid JSON, truthy, no .get
        check("a list is rejected, not returned",
              oi_store.previous("LST", "2026-09-04") is None)
        rep = oi_store.apply_oi_change("LST", chain({700.0: (5, 5)}), "2026-09-04")
        check("apply_oi_change survives it", rep["available"] is False)

        print("[12] concurrent writers cannot install a corrupt snapshot")
        # The engine writes on every market build and the recorder writes at
        # 09:20 — precisely when the app is most likely to be open. Reproduced
        # on the original code: six writers sharing one predictable temp name
        # produced a file that was atomically installed and UNREADABLE.
        import threading
        gate = threading.Barrier(6)      # all past the exists() check together
        errs = []

        def writer(n):
            body = chain({700.0 + i: (i * n, i * n) for i in range(300)},
                         label="2026-09-18")
            gate.wait()
            try:
                oi_store.snapshot("RCE", body, "2026-09-10")
            except Exception as exc:     # noqa: BLE001
                errs.append(f"{type(exc).__name__}: {exc}")

        ts = [threading.Thread(target=writer, args=(i + 1,)) for i in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        check("no writer raised", not errs, "; ".join(errs[:2]))
        final = oi_store.previous("RCE", "2026-09-11")
        check("the installed file is readable", final is not None)
        check("and complete", final and len(final["chains"]["2026-09-18"]["C"]) == 300,
              str(len(final["chains"]["2026-09-18"]["C"])) if final else "unreadable")
        check("no orphaned temp files",
              not [f for f in os.listdir(oi_store.DIR) if f.endswith(".tmp")],
              str([f for f in os.listdir(oi_store.DIR) if f.endswith(".tmp")]))
        # And the temp names must not be mistaken for snapshots by previous().
        check("temp files are excluded from the date scan",
              oi_store.previous("RCE", "2026-09-11")["date"] == "2026-09-10")

        print("[13] the same OCC publication read twice is not 'flat'")
        # Measured on the real store: Fri 21:31 -> Sat 10:36 moved 0 of 895
        # strikes. OI republishes once a session, so a weekend or premarket
        # re-read holds identical numbers. Reporting that as a genuine zero
        # would print "positioning didn't move" on a Monday premarket board.
        wide = {700.0 + i: (100 + i, 200 + i) for i in range(40)}
        oi_store.snapshot("STL", chain(wide, label="2026-10-16"), "2026-10-01")
        same = chain(wide, label="2026-10-16")           # identical numbers
        rep = oi_store.apply_oi_change("STL", same, "2026-10-02")
        check("not reported as available", rep["available"] is False, str(rep["available"]))
        check("flagged stale", rep.get("stale") is True, str(rep.get("stale")))
        check("the note says why", "read twice" in rep["note"], rep["note"])
        check("the baseline date is still named", rep["baseline_date"] == "2026-10-01",
              str(rep["baseline_date"]))
        # One strike moving is enough to make it a real session.
        moved_one = chain({**wide, 700.0: (101, 200)}, label="2026-10-16")
        rep = oi_store.apply_oi_change("STL", moved_one, "2026-10-02")
        check("one moved strike makes it real", rep["available"] is True, str(rep))
        check("and it is not flagged stale", not rep.get("stale"))

        print("[13b] a stale read with NEW strikes votes zero, not a landslide")
        # bias_engine decides OI Shift by `put_oi_change > call_oi_change` and
        # never checks `available` — it is safe on a missing baseline only
        # because both sides sum to exactly 0. If a stale read left a few
        # freshly listed strikes carrying their full OI, those would be the
        # only non-zero numbers in the chain and would swing the vote alone,
        # at the engine's top weight.
        with_new = chain({**wide, 999.0: (50000, 0)}, label="2026-10-16")
        rep = oi_store.apply_oi_change("STL", with_new, "2026-10-02")
        check("still detected as stale", rep.get("stale") is True, str(rep.get("stale")))
        calls = sum(c["oi_change"] for c in with_new[0]["calls"])
        puts = sum(p["oi_change"] for p in with_new[0]["puts"])
        check("call side sums to zero", calls == 0, str(calls))
        check("put side sums to zero", puts == 0, str(puts))
        check("so neither side outvotes the other", calls == puts)

        print("[14] non-trading days are not stored at all")
        # 2026-10-03 is a Saturday; 2026-12-25 is Christmas.
        for d, why in (("2026-10-03", "Saturday"), ("2026-12-25", "Christmas")):
            check(f"{why} refused",
                  oi_store.snapshot("CAL", chain({700.0: (1, 1)}), d) is None)
        check("a weekday is still stored",
              oi_store.snapshot("CAL", chain({700.0: (1, 1)}), "2026-10-02") is not None)

        print("[15] a duplicate publication is not stored under a new date")
        dup = chain(wide, label="2026-10-16")
        check("identical numbers refused",
              oi_store.snapshot("STL", dup, "2026-10-02") is None)
        changed = chain({**wide, 705.0: (999, 200)}, label="2026-10-16")
        check("changed numbers accepted",
              oi_store.snapshot("STL", changed, "2026-10-02") is not None)

        print()
        if FAILS:
            print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS)}")
            raise SystemExit(1)
        print("all OI store checks passed")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
