"""
verify_gex.py  --  Numerical verification of the GEX math.

Run:  python verify_gex.py

The rule this follows: a closed form has to be checked against an independent
numerical computation, not against a number someone once printed. Every test
here either finite-differences the analytic formula or checks a structural
identity the model implies.

These exist because the two bugs they would have caught were both live:
  - regime was read off the gamma flip instead of the sign of net gamma, so a
    deeply short-gamma book (-$3.7B) was labelled "positive gamma";
  - the flip itself came from a cumulative-sum sign change that triggered on
    float noise among worthless deep-OTM strikes, ~38% below spot.
Test [2] and [4] fail loudly on both.
"""
import math

from analysis.gex import (
    bs_gamma, dollar_gamma, compute_gex, net_gex_at, gamma_flip,
)

R = 0.043


# ---------------------------------------------------------------------------
# reference Black-Scholes price -- deliberately an INDEPENDENT implementation,
# so gamma is checked against something that doesn't share gex.py's code path.
# ---------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S: float, K: float, T: float, sigma: float, r: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_put(S: float, K: float, T: float, sigma: float, r: float) -> float:
    return bs_call(S, K, T, sigma, r) - S + K * math.exp(-r * T)


def _chain(calls, puts):
    """calls/puts: list of (strike, oi, iv, t_years)."""
    mk = lambda rows: [{"strike": k, "oi": oi, "iv": iv, "t_years": t, "oi_change": 0}
                       for k, oi, iv, t in rows]
    return {"calls": mk(calls), "puts": mk(puts)}


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


# ---------------------------------------------------------------------------
def t1_gamma_is_second_derivative():
    """gamma = d2V/dS2. Central second difference against the reference pricer."""
    print("\n[1] bs_gamma vs finite-differenced BS price")
    cases = [
        (100.0, 100.0, 30 / 365, 0.20),   # ATM
        (100.0, 110.0, 30 / 365, 0.20),   # OTM call
        (100.0,  90.0, 30 / 365, 0.20),   # ITM call
        (684.0, 690.0,  3 / 365, 0.22),   # short-dated, real-ish QQQ numbers
        (684.0, 620.0, 45 / 365, 0.35),   # deep OTM put strike, high vol
    ]
    worst = 0.0
    for S, K, T, sig in cases:
        h = S * 1e-4
        for pricer in (bs_call, bs_put):
            fd = (pricer(S + h, K, T, sig, R) - 2 * pricer(S, K, T, sig, R)
                  + pricer(S - h, K, T, sig, R)) / (h * h)
            an = bs_gamma(S, K, T, sig, R)
            rel = abs(an - fd) / max(abs(fd), 1e-12)
            worst = max(worst, rel)
    # calls and puts share gamma -- that identity is exercised by the loop above
    check("gamma matches d2V/dS2 for calls and puts", worst < 1e-4,
          f"worst rel err {worst:.2e}")


def t2_flip_is_actually_a_zero():
    """The definitional check: net gamma AT the returned flip must be ~0."""
    print("\n[2] gamma_flip returns a genuine zero of net_gex_at")
    # put-heavy book near spot (the shape that broke the old code)
    ch = _chain(
        calls=[(700 + 5 * i, 400, 0.20, 5 / 365) for i in range(8)],
        puts=[(690 - 5 * i, 3000, 0.25, 5 / 365) for i in range(8)],
    )
    spot = 684.0
    flip = gamma_flip(ch, spot, R)
    check("a flip is found for a mixed book", flip is not None, f"flip={flip}")
    if flip:
        at_flip = net_gex_at(ch, flip, R)
        scale = max(abs(net_gex_at(ch, spot, R)), 1.0)
        check("net gamma at the flip is ~0", abs(at_flip) / scale < 1e-3,
              f"net@flip={at_flip:,.0f} vs net@spot={net_gex_at(ch, spot, R):,.0f}")
        # and the sign genuinely differs on the two sides
        lo = net_gex_at(ch, flip * 0.99, R)
        hi = net_gex_at(ch, flip * 1.01, R)
        check("net gamma changes sign across the flip", (lo < 0) != (hi < 0),
              f"below={lo:,.0f} above={hi:,.0f}")


def t3_no_flip_reports_none():
    """A calls-only book is positive-gamma everywhere. There is no flip, and
    the honest answer is None -- not a fabricated middle strike."""
    print("\n[3] one-sided book -> no flip, reported honestly")
    ch = _chain(calls=[(100 + i, 500, 0.2, 7 / 365) for i in range(20)], puts=[])
    check("calls-only book has no zero-gamma level", gamma_flip(ch, 110.0, R) is None)
    ch2 = _chain(calls=[], puts=[(100 + i, 500, 0.2, 7 / 365) for i in range(20)])
    check("puts-only book has no zero-gamma level", gamma_flip(ch2, 110.0, R) is None)


def t4_regime_matches_sign_of_net_gamma():
    """regime must equal the sign of net gamma at spot, in every case."""
    print("\n[4] regime label == sign of net gamma at spot")
    books = {
        "put-heavy (short gamma)": _chain(
            calls=[(700 + 5 * i, 300, 0.20, 5 / 365) for i in range(6)],
            puts=[(690 - 5 * i, 5000, 0.25, 5 / 365) for i in range(6)]),
        "call-heavy (long gamma)": _chain(
            calls=[(690 + 5 * i, 5000, 0.20, 5 / 365) for i in range(6)],
            puts=[(680 - 5 * i, 300, 0.25, 5 / 365) for i in range(6)]),
    }
    ok = True
    for name, ch in books.items():
        g = compute_gex(ch, 684.0, R)
        expect = "positive" if g["net_gex"] >= 0 else "negative"
        good = g["regime"] == expect
        ok &= good
        print(f"       {name}: net={g['net_gex']:>16,.0f} -> {g['regime']}"
              f"{'' if good else '  <-- MISMATCH'}")
    check("regime never contradicts net_gex", ok)


def t5_walls_are_on_the_right_side():
    """A call wall is resistance (>= spot); a put wall is support (<= spot)."""
    print("\n[5] walls sit on the correct side of spot")
    ch = _chain(
        calls=[(700 + 5 * i, 400, 0.20, 5 / 365) for i in range(8)],
        puts=[(695 - 5 * i, 3000, 0.25, 5 / 365) for i in range(8)],
    )
    spot = 684.0
    g = compute_gex(ch, spot, R)
    cw, pw = g["call_wall"], g["put_wall"]
    check("call wall is at or above spot", cw is None or cw >= spot, f"call_wall={cw} spot={spot}")
    check("put wall is at or below spot", pw is None or pw <= spot, f"put_wall={pw} spot={spot}")


def t6_dollar_gamma_scaling():
    """
    $GEX = gamma * OI * 100 * S^2 * 0.01 is the LINEARIZED dealer-delta change
    per 1% move. Differencing delta over a literal 1% step does NOT reproduce it
    to high precision, and that is correct behaviour, not a bug: a 1% step also
    picks up third-order curvature (speed, dGamma/dS).

    So this checks the right thing in two parts:
      (a) against a small-step difference, where truncation is negligible;
      (b) that the full-1% gap is pure truncation, by confirming it decays at
          second order as the step shrinks. If $GEX were mis-scaled, the error
          would plateau at a constant instead of converging to zero.
    """
    print("\n[6] dollar_gamma == linearized dealer-delta change per 1% move")
    S, K, T, sig, oi = 500.0, 505.0, 10 / 365, 0.22, 1200
    h = S * 1e-5
    delta = lambda s: (bs_call(s + h, K, T, sig, R) - bs_call(s - h, K, T, sig, R)) / (2 * h)
    analytic = dollar_gamma(bs_gamma(S, K, T, sig, R), oi, S)

    def fd_gex(frac):
        """delta change over a `frac` move, rescaled to the 1% definition."""
        m = S * frac
        return (delta(S + m / 2) - delta(S - m / 2)) * oi * 100 * S * (0.01 / frac)

    rel_small = abs(fd_gex(1e-4) - analytic) / analytic
    check("$GEX matches differenced dealer delta (small step)", rel_small < 1e-5,
          f"rel err {rel_small:.2e}")

    e_coarse = abs(fd_gex(1e-2) - analytic) / analytic
    e_fine = abs(fd_gex(1e-3) - analytic) / analytic
    ratio = e_coarse / e_fine
    check("full-1% gap is O(h^2) truncation, not mis-scaling", 50 < ratio < 200,
          f"err {e_coarse:.1e} -> {e_fine:.1e} on 10x smaller step (ratio {ratio:.0f}x, expect ~100x)")


def t7_profile_sums_to_net():
    """Structural: the per-strike profile must sum to net_gex exactly."""
    print("\n[7] per-strike profile sums to net_gex")
    ch = _chain(
        calls=[(690 + 5 * i, 900, 0.21, 6 / 365) for i in range(7)],
        puts=[(685 - 5 * i, 1400, 0.24, 6 / 365) for i in range(7)],
    )
    g = compute_gex(ch, 684.0, R)
    s = sum(p["gex"] for p in g["profile"])
    check("sum(profile) == net_gex", abs(s - g["net_gex"]) < 1e-6,
          f"diff {abs(s - g['net_gex']):.3e}")
    # and net_gex must equal an independent recompute at the same spot
    check("net_gex == net_gex_at(spot)",
          abs(g["net_gex"] - net_gex_at(ch, 684.0, R)) < 1e-6)


if __name__ == "__main__":
    print("=" * 68)
    print("GEX MATH VERIFICATION")
    print("=" * 68)
    for t in (t1_gamma_is_second_derivative, t2_flip_is_actually_a_zero,
              t3_no_flip_reports_none, t4_regime_matches_sign_of_net_gamma,
              t5_walls_are_on_the_right_side, t6_dollar_gamma_scaling,
              t7_profile_sums_to_net):
        t()
    print("\n" + "=" * 68)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        raise SystemExit(1)
    print("All GEX checks passed.")
