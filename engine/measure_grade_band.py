"""
measure_grade_band.py  --  Derive GRADE_BAND_PCT from real data.

Run:  python measure_grade_band.py [TICKER ...]

WHY THIS EXISTS
The band decides which days count as "flat", which is the only way a NEUTRAL
lean can be graded correct. Pick it too wide and NEUTRAL scores for free; too
narrow and it can never score at all. Either way the headline directional hit
rate stops meaning anything.

The band therefore has to match the WINDOW being graded. When grading moved
from open->close to open->12:00, the old 0.15% band was measuring a shorter
move with a full-day yardstick. This script finds the band that leaves the same
share of days flat over the new window as the old one did over the old window,
so NEUTRAL stays exactly as hard to score as it was.

CAVEAT, AND IT IS THE POINT
Yahoo only serves 30-minute bars for ~60 sessions, so this runs on a thin
sample and the answer moves with the volatility regime. Split those sessions in
half and the ratio swings meaningfully. Treat the output as a starting point to
re-run periodically, not a constant.
"""
import sys

import config

OLD_WINDOW_BAND = 0.15   # the band that was in use for open->close grading


def _sessions(ticker: str):
    """(date, open->exit %, open->close %) per session, from 30m bars."""
    import yfinance as yf

    exit_h, exit_m = map(int, config.GRADE_EXIT_TIME.split(":"))
    h = yf.Ticker(ticker).history(period="60d", interval="30m", prepost=False)
    if not len(h):
        return []
    h = h[h["Volume"] > 0]
    out = []
    for day, g in h.groupby(h.index.date):
        g = g.sort_index()
        idx = g.index
        rth = g[(idx.hour > 9) | ((idx.hour == 9) & (idx.minute >= 30))]
        if len(rth) < 6:
            continue
        at = rth.index
        hit = rth[(at.hour == exit_h - 1) & (at.minute == 30)] if exit_m == 0 \
            else rth[(at.hour == exit_h) & (at.minute == exit_m - 30)]
        if not len(hit):
            continue
        o = float(rth["Open"].iloc[0])
        out.append((day,
                    100 * (float(hit["Close"].iloc[0]) - o) / o,
                    100 * (float(rth["Close"].iloc[-1]) - o) / o))
    return out


def _quantile(sorted_vals, q):
    """Linear-interpolated quantile. Stdlib only — no numpy needed here."""
    if not sorted_vals:
        return 0.0
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def measure(ticker: str) -> dict | None:
    rows = _sessions(ticker)
    if len(rows) < 20:
        print(f"{ticker}: only {len(rows)} sessions — too few to say anything.")
        return None

    exits = sorted(abs(r[1]) for r in rows)
    closes = sorted(abs(r[2]) for r in rows)
    n = len(rows)

    flat_share = sum(1 for c in closes if c <= OLD_WINDOW_BAND) / n
    band = _quantile(exits, flat_share)
    ratio = _quantile(exits, 0.5) / _quantile(closes, 0.5)

    # stability: does the answer hold across the two halves of the sample?
    half = n // 2
    r1 = sorted(abs(r[1]) for r in rows[:half])
    c1 = sorted(abs(r[2]) for r in rows[:half])
    r2 = sorted(abs(r[1]) for r in rows[half:])
    c2 = sorted(abs(r[2]) for r in rows[half:])
    ratio1 = _quantile(r1, 0.5) / _quantile(c1, 0.5)
    ratio2 = _quantile(r2, 0.5) / _quantile(c2, 0.5)

    up = sum(1 for r in rows if r[1] > band) / n
    dn = sum(1 for r in rows if r[1] < -band) / n

    print(f"\n{ticker}  ({n} sessions, open -> {config.GRADE_EXIT_TIME})")
    print(f"  median |move|   {_quantile(exits, .5):.3f}%   (open->close: {_quantile(closes, .5):.3f}%)")
    print(f"  p90    |move|   {_quantile(exits, .9):.3f}%   (open->close: {_quantile(closes, .9):.3f}%)")
    print(f"  ratio vs close  {ratio:.3f}   halves: {ratio1:.3f} -> {ratio2:.3f}"
          + ("   << UNSTABLE" if abs(ratio1 - ratio2) > 0.15 else ""))
    print(f"  band matching the old {OLD_WINDOW_BAND}% flat share ({100*flat_share:.0f}%):"
          f"  {band:.3f}%")
    print(f"  that band splits days:  up {100*up:.0f}%  down {100*dn:.0f}%  "
          f"flat {100*(1-up-dn):.0f}%")
    return {"ticker": ticker, "n": n, "band": band, "ratio": ratio}


if __name__ == "__main__":
    tickers = [t.upper() for t in sys.argv[1:]] or ["SPY", "QQQ"]
    print("=" * 66)
    print(f"GRADE BAND MEASUREMENT   (current config: {config.GRADE_BAND_PCT}%)")
    print("=" * 66)
    results = [r for r in (measure(t) for t in tickers) if r]
    if results:
        primary = results[0]
        print(f"\nSuggested GRADE_BAND_PCT for {primary['ticker']}: "
              f"{primary['band']:.3f}  (config.py currently has {config.GRADE_BAND_PCT})")
        print("Sample is ~60 sessions and regime-dependent. Re-run periodically;\n"
              "don't chase small moves in the number.")
