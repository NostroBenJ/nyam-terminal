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
        oi_store.snapshot("SPY", day3, "2026-08-01")
        rep = oi_store.apply_oi_change("SPY", day3, "2026-08-01")
        calls = {c["strike"]: c["oi_change"] for c in day3[0]["calls"]}
        check("new strike 760 change == its OI", calls[760.0] == 2200, str(calls[760.0]))
        check("new counted separately", rep["new"] == 2, str(rep["new"]))
        check("baseline is yesterday, not the oldest", rep["baseline_date"] == "2026-07-31",
              str(rep["baseline_date"]))

        print("[4] a day's snapshot is written once, not overwritten")
        # Diffing a 15:59 capture against a 09:15 one would report intraday
        # drift as overnight positioning change.
        again = oi_store.snapshot("SPY", chain({745.0: (9999, 9999)}), "2026-08-01")
        check("second write refused", again is None, str(again))
        stored = oi_store.previous("SPY", "2026-08-02")
        check("stored value is the original",
              stored["chains"]["2026-08-07"]["C"]["745.0"] == 1400,
              str(stored["chains"]["2026-08-07"]["C"]["745.0"]))

        print("[5] tickers are isolated")
        qqq = chain({500.0: (100, 100)})
        oi_store.snapshot("QQQ", qqq, "2026-08-01")
        rep = oi_store.apply_oi_change("QQQ", qqq, "2026-08-01")
        check("QQQ has no baseline from SPY", rep["available"] is False, str(rep))

        print("[6] previous() ignores same-day and future files")
        check("strictly earlier only",
              oi_store.previous("SPY", "2026-07-30") is None,
              str(oi_store.previous("SPY", "2026-07-30")))

        print("[7] a corrupt snapshot degrades instead of crashing")
        bad = os.path.join(oi_store.DIR, "BAD_2026-07-30.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        check("corrupt file -> None", oi_store.previous("BAD", "2026-08-01") is None)

        print("[8] an unknown expiry label yields new, not a crash")
        other = chain({745.0: (10, 10)}, label="2026-09-19")
        rep = oi_store.apply_oi_change("SPY", other, "2026-08-02")
        check("unmatched expiry treated as new", rep["new"] == 2, str(rep["new"]))
        check("its change is full OI",
              other[0]["calls"][0]["oi_change"] == 10,
              str(other[0]["calls"][0]["oi_change"]))

        print()
        if FAILS:
            print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS)}")
            raise SystemExit(1)
        print("all OI store checks passed")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
