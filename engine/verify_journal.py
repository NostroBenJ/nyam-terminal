"""
verify_journal.py  --  Checks the journal records the REASONING, not just the score.

Runs against a temp store so it never touches your real records.

The gap this closes: the record used to hold bias/score/spot only. That is
enough to compute a hit rate and useless for learning from a loss — months
later you can see a SHORT LEAN missed, with no way to ask whether the reasoning
was wrong or the read was right and the tape disagreed. The chain that produced
those levels is gone by the next session, so context is captured at call time
or never.

    python verify_journal.py
"""
import datetime as dt
import shutil
import tempfile

import config

_TMP = tempfile.mkdtemp(prefix="nyam_journal_test_")
config.STORE_DIR = _TMP
config.USE_MOCK_DATA = True          # keeps store.py on a predictable filename

from analysis import tracker         # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if (not ok and detail) else ""))
    if not ok:
        FAILS.append(name)


def snap(date, ticker="SPY", label="SHORT LEAN", score=-2.0):
    return {
        "generated_at": f"{date} 09:25:00 EDT",
        "ticker": ticker, "mock": False, "provider": "yahoo",
        "gex": {"spot": 747.03, "regime": "positive", "net_gex": 8.8e8,
                "gamma_flip": 745.26, "call_wall": 749.0, "put_wall": 730.0,
                "control_node": 749.0, "atm_iv": 0.1343, "put_call_ratio": 1.698},
        "expected_move": {"dollars": 5.25, "pct": 0.7, "low": 741.78, "high": 752.28},
        "bias": {"label": label, "score": score, "conviction": "reduced",
                 "summary": "Net positioning leans short.",
                 "signals": [
                     {"name": "Gamma Regime", "lean": 0, "weight": 2.0, "reason": "Positive gamma."},
                     {"name": "Call Wall", "lean": -1, "weight": 1.5, "reason": "Spot near the wall."}]},
        "smt": {"note": "SPY made a new ON high, QQQ didn't."},
        "news": {"headline": "10:00 ET — ISM Services PMI", "level": "high"},
    }


def main():
    try:
        # Must be a weekday (record_prediction skips weekends) AND in the past
        # (grade_pending refuses to grade a session that hasn't happened yet —
        # dating this in the future made the grading check fail, correctly).
        d = "2026-07-31"
        assert dt.date.fromisoformat(d).weekday() < 5, "must be a weekday"
        assert d < dt.date.today().isoformat(), "must be in the past to be gradeable"

        print("[1] a call records its reasoning")
        tracker.record_prediction(snap(d))
        recs = tracker.journal("SPY")
        check("one record", len(recs) == 1, str(len(recs)))
        r = recs[0]
        ctx = r.get("context") or {}
        check("context present", bool(ctx))
        for f in ("regime", "gamma_flip", "call_wall", "put_wall", "control_node",
                  "atm_iv", "put_call_ratio", "conviction", "summary", "smt", "news"):
            check(f"context.{f}", ctx.get(f) is not None, "missing")
        check("signals captured with reasons",
              len(ctx.get("signals", [])) == 2
              and all(s.get("reason") for s in ctx["signals"]),
              str(ctx.get("signals")))
        check("provider recorded", ctx.get("provider") == "yahoo", str(ctx.get("provider")))

        print("[2] the morning call is never overwritten")
        # Refreshing all morning must not replace the 09:25 read with an 11:00 one.
        tracker.record_prediction(snap(d, label="LONG LEAN", score=3.0))
        recs = tracker.journal("SPY")
        check("still one record", len(recs) == 1, str(len(recs)))
        check("original bias kept", recs[0]["bias"] == "SHORT LEAN", recs[0]["bias"])

        print("[3] weekends are not recorded")
        sat = "2026-08-01"
        assert dt.date.fromisoformat(sat).weekday() >= 5
        tracker.record_prediction(snap(sat))
        check("saturday skipped", len(tracker.journal("SPY")) == 1,
              str(len(tracker.journal("SPY"))))

        print("[4] notes")
        check("note starts empty", tracker.journal("SPY")[0].get("note") == "")
        ok = tracker.set_note("SPY", d, "Didn't take it — too close to the wall.")
        check("set_note returns True", ok is True)
        check("note persisted",
              tracker.journal("SPY")[0]["note"] == "Didn't take it — too close to the wall.")
        check("unknown date returns False", tracker.set_note("SPY", "1999-01-04", "x") is False)

        print("[5] a note survives grading")
        # Grading writes `outcome`; it must never clobber what you wrote.
        recs_all = tracker.grade_pending(lambda t, dd: {"open": 747.0, "exit": 742.0, "note": ""})
        after = tracker.journal("SPY")[0]
        check("outcome written", after.get("outcome") is not None)
        check("note intact", after.get("note") == "Didn't take it — too close to the wall.",
              str(after.get("note")))
        check("context intact after grading", bool(after.get("context")))

        print("[6] tickers stay separate")
        tracker.record_prediction(snap(d, ticker="QQQ", label="LONG LEAN", score=2.0))
        check("SPY sees only its own", len(tracker.journal("SPY")) == 1)
        check("QQQ sees only its own", len(tracker.journal("QQQ")) == 1)
        check("unfiltered sees both", len(tracker.journal()) == 2)

        print("[7] newest first")
        tracker.record_prediction(snap("2026-07-29"))
        dates = [x["date"] for x in tracker.journal("SPY")]
        check("descending by date", dates == sorted(dates, reverse=True), str(dates))

        print("[8] old records without context still load")
        # Records written before context capture must not break the journal.
        import store
        raw = store.load()
        raw["SPY|2026-07-30"] = {"date": "2026-07-30", "ticker": "SPY",
                                 "bias": "LONG LEAN", "score": 1.0,
                                 "predicted_dir": "up", "spot": 740.0, "outcome": None}
        store.save(raw)
        old = [x for x in tracker.journal("SPY") if x["date"] == "2026-07-30"]
        check("legacy record loads", len(old) == 1)
        check("legacy record has no context", old and old[0].get("context") is None)

        print()
        if FAILS:
            print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:6])}")
            raise SystemExit(1)
        print("all journal checks passed")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
