"""
verify_brief_cost.py  --  Pins the brief's call volume.

The bug this prevents was measured, not theoretical: the pre-market scheduler
fires 180 times a morning, the brief regenerated on every one, and at Opus
rates that is $105/month for a written read that changes a handful of times a
session.

These checks use a STUBBED model call and count invocations. No API key needed,
no money spent, and the assertion is on call count rather than on output text —
the thing that costs money is how often it runs.

    python verify_brief_cost.py
"""
import config
import claude_brief as cb

FAILS = []
CALLS = {"n": 0}


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if (not ok and detail) else ""))
    if not ok:
        FAILS.append(name)


GEX = {"spot": 745.0, "regime": "positive", "gamma_flip": 742.46,
       "call_wall": 757.0, "put_wall": 733.0, "control_node": 757.0,
       "put_call_ratio": 0.99, "call_oi_change": 100, "put_oi_change": 200,
       "net_gex": 2.6e8, "atm_iv": 0.16}
LEVELS = {"prior_day_high": 746.27, "prior_day_low": 740.23, "prior_close": 744.03,
          "overnight_high": 747.23, "overnight_low": 741.87, "spot": 745.0}
SMT = {"signal": "bearish_divergence", "lean": -1, "note": "SPY high, QQQ didn't."}
NEWS = {"high_impact": True, "headline": "10:00 ET — ISM Services PMI", "items": []}
BIAS = {"label": "SHORT LEAN", "score": -2.0, "conviction": "reduced",
        "summary": "Leans short.",
        "signals": [{"name": "Gamma Regime", "lean": 0, "weight": 2.0, "reason": "Positive."},
                    {"name": "SMT", "lean": -1, "weight": 2.0, "reason": "Divergence."}],
        "scenarios": []}


def reset(deep=None):
    """Clear cache + budget so each scenario starts clean."""
    cb._cache.clear()
    cb._calls["date"] = None
    cb._calls["n"] = {}
    CALLS["n"] = 0


def stub(*a, **k):
    CALLS["n"] += 1
    return "## SPY — Pre-Market Bias Brief\n\nStubbed."


def gen(gex=None, bias=None, news=None, ticker="SPY"):
    return cb.generate_brief_meta(bias or BIAS, gex or GEX, LEVELS, SMT,
                                  news or NEWS, ticker)


def main():
    saved_key, saved_fn = config.ANTHROPIC_API_KEY, cb._claude_brief
    config.ANTHROPIC_API_KEY = "test-key-not-real"
    cb._claude_brief = stub
    try:
        print("[1] an unchanged read costs ONE call, not one per refresh")
        # The exact failure that shipped: 180 scheduler fires -> 180 calls.
        reset()
        for _ in range(180):
            gen()
        check("180 refreshes -> 1 model call", CALLS["n"] == 1, f"{CALLS['n']} calls")
        check("subsequent results are marked cached", gen()["cached"] is True)

        print("[2] price ticks inside the bucket do NOT regenerate")
        # Spot moves every minute; the read does not.
        reset()
        gen()
        for tick in (745.10, 745.25, 744.90, 745.40):
            gen(gex={**GEX, "spot": tick})
        check("small ticks reuse the brief", CALLS["n"] == 1, f"{CALLS['n']} calls")

        print("[3] the things that CHANGE the read do regenerate")
        for label, changed in (
            ("bias label flips", {"bias": {**BIAS, "label": "LONG LEAN", "score": 2.0}}),
            ("regime flips", {"gex": {**GEX, "regime": "negative"}}),
            ("spot crosses the flip", {"gex": {**GEX, "spot": 741.0}}),
            ("a wall moves", {"gex": {**GEX, "call_wall": 760.0}}),
            ("news headline changes", {"news": {**NEWS, "headline": "08:30 ET — CPI"}}),
            ("a signal flips direction", {"bias": {**BIAS, "signals": [
                {"name": "Gamma Regime", "lean": 1, "weight": 2.0, "reason": "x"},
                {"name": "SMT", "lean": -1, "weight": 2.0, "reason": "y"}]}}),
        ):
            reset()
            gen()
            gen(**changed)
            check(label, CALLS["n"] == 2, f"{CALLS['n']} calls (expected 2)")

        print("[4] a large move regenerates even with the same structure")
        reset()
        gen()
        gen(gex={**GEX, "spot": 745.0 * 1.01})     # +1%, well past the bucket
        check("1% move regenerates", CALLS["n"] == 2, f"{CALLS['n']} calls")

        print("[5] tickers are cached independently")
        reset()
        gen(ticker="SPY")
        gen(ticker="QQQ")
        gen(ticker="SPY")
        check("SPY+QQQ -> 2 calls", CALLS["n"] == 2, f"{CALLS['n']} calls")

        print("[6] the daily budget is a hard ceiling")
        # A runaway loop should hit a wall, not a bill.
        reset()
        for i in range(cb.MAX_CALLS_PER_DAY + 25):
            # Vary the SCORE, not spot — with correct bucketing many nearby
            # spots share a bucket, so spot is a poor way to force distinct
            # signatures. The score is in the signature verbatim.
            gen(bias={**BIAS, "score": float(i)})
        check(f"capped at {cb.MAX_CALLS_PER_DAY}", CALLS["n"] == cb.MAX_CALLS_PER_DAY,
              f"{CALLS['n']} calls")
        out = gen(bias={**BIAS, "score": 999.0})
        check("capped result says so", out.get("budget_capped") is True)
        check("capped result explains why", "budget" in (out.get("error") or "").lower(),
              str(out.get("error")))

        print("[7] staleness still forces a refresh")
        reset()
        gen()
        cb._cache["SPY"]["at"] -= cb.MAX_AGE_S + 1   # pretend it's old
        gen()
        check("brief older than MAX_AGE_S regenerates", CALLS["n"] == 2, f"{CALLS['n']} calls")

        print("[8] measured saving")
        reset()
        for _ in range(180):
            gen()
        saved = 180 - CALLS["n"]
        print(f"       180 scheduler fires -> {CALLS['n']} model call(s); {saved} avoided")
        check("at least 95% of calls avoided on a static read", saved / 180 >= 0.95,
              f"{saved}/180")
    finally:
        config.ANTHROPIC_API_KEY = saved_key
        cb._claude_brief = saved_fn

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:6])}")
        raise SystemExit(1)
    print("all brief-cost checks passed")


if __name__ == "__main__":
    main()
