"""
verify_bars.py  --  Numerical checks on the chart's bar series.

The chart shares a price axis with levels computed elsewhere, so a bar series
that disagrees with those levels makes correct code look broken. These checks
pin the properties the chart relies on rather than asserting a hardcoded
snapshot of the output.

    python verify_bars.py
"""
import statistics as st

import config

# Pin mock mode. Every check below is about the SYNTHETIC bar generator — that
# it anchors to the mock spot, that spacing is exact, that it's deterministic.
# Without this the suite reads whatever NYAM_MOCK happens to be in .env, so
# switching the app to live data made 11 checks fail against real Yahoo bars
# they were never written to describe. A suite's mode is part of the suite.
config.USE_MOCK_DATA = True

import data.data_sources as ds                            # noqa: E402
from data.mock_data import _mock_spot                     # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def main():
    print("[1] mock bars anchor to the mock spot")
    # The GEX walls, flip and bias are all computed off _mock_spot. If the walk
    # ended anywhere else, the overlays would render on the wrong side of the
    # candles — the exact failure this constraint exists to prevent.
    for iv in ("1m", "5m", "30m", "1h"):
        b = ds.get_bars("SPY", interval=iv, lookback_days=2)["bars"]
        last, spot = b[-1]["close"], _mock_spot("SPY")
        check(f"{iv} last close == spot", abs(last - spot) <= 0.011,
              f"last={last} spot={spot}")

    print("[2] OHLC is internally consistent")
    # low <= open,close <= high. Violated, Lightweight Charts draws an inverted
    # candle that reads as a real price event.
    for iv in ("1m", "5m", "30m"):
        b = ds.get_bars("SPY", interval=iv, lookback_days=2)["bars"]
        bad = [x for x in b
               if not (x["low"] <= x["open"] <= x["high"]
                       and x["low"] <= x["close"] <= x["high"])]
        check(f"{iv} every bar has low <= o,c <= high", not bad,
              f"{len(bad)} bad bars" if bad else f"{len(b)} bars")

    print("[3] timestamps strictly ascending and unique")
    # Lightweight Charts silently drops or misorders out-of-sequence data.
    for iv in ("1m", "5m", "30m"):
        b = ds.get_bars("SPY", interval=iv, lookback_days=2)["bars"]
        ts = [x["time"] for x in b]
        check(f"{iv} ascending", ts == sorted(ts))
        check(f"{iv} unique", len(set(ts)) == len(ts))

    print("[4] bar spacing matches the requested interval")
    for iv, secs in (("1m", 60), ("5m", 300), ("30m", 1800)):
        b = ds.get_bars("SPY", interval=iv, lookback_days=2)["bars"]
        gaps = {b[i]["time"] - b[i - 1]["time"] for i in range(1, len(b))}
        check(f"{iv} spacing == {secs}s", gaps == {secs}, f"observed {sorted(gaps)}")

    print("[5] deterministic — same request, same bars")
    # A chart that reshuffles itself on every poll is unreadable, and in mock
    # mode it would also desync from the (seeded) chain.
    a = ds.get_bars("SPY", "5m", 2)["bars"]
    c = ds.get_bars("SPY", "5m", 2)["bars"]
    check("repeat call identical", a == c)

    print("[6] per-bar volatility scales with sqrt(interval)")
    # A 30m bar should move ~sqrt(6)x a 5m bar. Without this, every timeframe
    # looks like the same series at a different zoom.
    sd = {}
    for iv in ("5m", "30m"):
        b = ds.get_bars("SPY", interval=iv, lookback_days=2)["bars"]
        sd[iv] = st.pstdev([b[i]["close"] - b[i - 1]["close"] for i in range(1, len(b))])
    ratio = sd["30m"] / sd["5m"] if sd["5m"] else 0
    check("sd(30m)/sd(5m) ~ sqrt(6)=2.45", 1.7 <= ratio <= 3.3,
          f"ratio={ratio:.2f} (5m={sd['5m']:.4f}, 30m={sd['30m']:.4f})")

    print("[7] per-ticker anchoring")
    for t in ("QQQ", "NVDA", "TSLA"):
        b = ds.get_bars(t, "5m", 2)["bars"]
        check(f"{t} anchors to its own spot",
              abs(b[-1]["close"] - _mock_spot(t)) <= 0.011,
              f"last={b[-1]['close']} spot={_mock_spot(t)}")

    print("[8] source is always reported")
    # The UI must be able to say whether a candle is real-time, delayed, or fake.
    out = ds.get_bars("SPY", "5m", 2)
    check("source present", bool(out.get("source")), out.get("source"))
    check("mock is labelled as such", out["source"] == "mock", out["source"])

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS)}")
        raise SystemExit(1)
    print("all bar checks passed")


if __name__ == "__main__":
    main()
