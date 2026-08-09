"""
gex.py  --  The heart of the tool. Computes Gamma Exposure (GEX) from a raw
option chain, the way SpotGamma / Unusual Whales do under the hood.

This is intentionally written to be READABLE, not maximally fast. Read it
top to bottom and you'll understand exactly what every number on the
dashboard is "based on" -- nothing here is a black box.

THE IDEA (short version):
  Dealers (market makers) sell options to retail and hedge their risk by
  trading the underlying. Their hedging FLOW is predictable from how much
  gamma they hold. GEX measures the dollars of underlying dealers must trade
  per 1% move.
    - Positive GEX  -> dealers buy dips / sell rips -> price gets pinned, mean-reverts
    - Negative GEX  -> dealers sell dips / buy rips -> moves get amplified, trends
  The "gamma flip" is the price where net GEX crosses zero -- the regime line.
"""
import math


# --- standard normal probability density function -------------------------
def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_gamma(spot: float, strike: float, t_years: float, iv: float, r: float) -> float:
    """
    Black-Scholes gamma for one option (calls and puts share the same gamma).

    gamma = N'(d1) / (S * sigma * sqrt(T))
    where d1 = [ln(S/K) + (r + sigma^2/2) * T] / (sigma * sqrt(T))

    Returns 0 for degenerate inputs (expired, zero IV) so the pipeline never
    blows up on a bad row in the chain.
    """
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    return _norm_pdf(d1) / (spot * iv * math.sqrt(t_years))


def dollar_gamma(gamma: float, open_interest: float, spot: float) -> float:
    """
    Convert raw gamma into dollar GEX for a 1% move.

      $GEX = gamma * OI * 100 * S^2 * 0.01

    100 = contract multiplier (1 option = 100 shares).
    S^2 * 0.01 turns "gamma per $1" into "dollars of delta per 1% move".
    """
    return gamma * open_interest * 100.0 * spot * spot * 0.01


def compute_gex(chain: dict, spot: float, r: float) -> dict:
    """
    Aggregate a full chain into the GEX picture.

    `chain` is a dict:
        {
          "calls": [ {strike, oi, iv, t_years, oi_change}, ... ],
          "puts":  [ {strike, oi, iv, t_years, oi_change}, ... ],
        }

    SIGN CONVENTION (the one modeling choice that matters):
      We assume dealers are LONG calls and SHORT puts -- the common retail
      convention. So calls add POSITIVE dealer gamma, puts add NEGATIVE.
      This is an assumption, not a law. SpotGamma et al. tweak it.

      IT IS NO LONGER UNVERIFIED. Checked 2026-08-08 against Unusual Whales,
      which computes exposure independently and publishes the call and put
      legs SEPARATELY -- so their signs state their convention outright rather
      than leaving it to be inferred:

          call_gex   positive on all 466 strikes
          put_gex    negative on all 367 strikes

      Identical to ours. Per-strike net agrees in sign on 145 of 158 common
      strikes (91.8%). And flipping our convention does not merely shift the
      levels, it DESTROYS the call wall -- no positive-gamma strike remains
      above spot -- which the 760.00 and 775.00 exact matches with UW forbid.

      Three independent lines pointing the same way is as close to settled as
      this gets without a track record. See verify_sign_convention.py; it runs
      against the live feed so a change in UW's convention would surface as a
      failure rather than as levels quietly drifting.
    """
    by_strike = {}  # strike -> net dollar gamma

    def _add(rows, sign):
        for o in rows:
            g = bs_gamma(spot, o["strike"], o["t_years"], o["iv"], r)
            dg = sign * dollar_gamma(g, o["oi"], spot)
            by_strike[o["strike"]] = by_strike.get(o["strike"], 0.0) + dg

    _add(chain["calls"], +1.0)
    _add(chain["puts"], -1.0)

    strikes = sorted(by_strike.keys())
    profile = [{"strike": k, "gex": by_strike[k]} for k in strikes]
    net_gex = sum(by_strike.values())

    # --- gamma flip: the spot price at which NET dealer gamma is zero -------
    # Computed by re-pricing the whole chain at candidate spots and bisecting
    # the sign change (see gamma_flip()). The old cumulative-across-strikes
    # approximation is gone: it read a float-noise sign flip among worthless
    # deep-OTM strikes as a regime line hundreds of points below spot.
    flip = gamma_flip(chain, spot, r)

    # --- walls -------------------------------------------------------------
    # A call wall is resistance, so it must sit AT or ABOVE spot; a put wall is
    # support, so it must sit AT or BELOW. Taking the global max/min ignores
    # that and happily returns a "floor" above the current price.
    above = [p for p in profile if p["strike"] >= spot]
    below = [p for p in profile if p["strike"] <= spot]
    call_wall = max(above, key=lambda p: p["gex"]) if above else None
    put_wall = min(below, key=lambda p: p["gex"]) if below else None
    # Only a POSITIVE-gamma strike is a real call wall (and negative for puts).
    # If the best candidate has the wrong sign there is no wall on that side.
    if call_wall and call_wall["gex"] <= 0:
        call_wall = None
    if put_wall and put_wall["gex"] >= 0:
        put_wall = None

    # --- put/call positioning ----------------------------------------------
    call_oi = sum(o["oi"] for o in chain["calls"])
    put_oi = sum(o["oi"] for o in chain["puts"])
    pc_ratio = (put_oi / call_oi) if call_oi else 0.0
    call_oi_chg = sum(o.get("oi_change", 0) for o in chain["calls"])
    put_oi_chg = sum(o.get("oi_change", 0) for o in chain["puts"])

    # --- control node / magnet: strike holding the most |dealer gamma| ------
    # This is the price the market tends to gravitate toward (the "pin").
    control = max(profile, key=lambda p: abs(p["gex"])) if profile else None
    control_node = control["strike"] if control else None

    # HOW CONTESTED THAT PICK IS, which matters more than it looks. The magnet
    # is an argmax over strikes that are frequently near-tied, so it is the
    # least stable of the four levels by construction. Measured live: the
    # leader beat the runner-up by 5.4%, and a 0.1% move in spot flipped the
    # answer from 775 to 762 — thirteen points — with the chain unchanged.
    #
    # That is also the whole explanation for "disagreeing" with a vendor here.
    # Two correct implementations sampling seconds apart land on different
    # strikes, and neither is wrong. Rendering it with the same confidence as
    # the call wall (which matches UW exactly and does not move) claims a
    # precision the number does not have, so the margin travels with it.
    runner_up, margin_pct = None, None
    if control and len(profile) > 1:
        rest = [p for p in profile if p["strike"] != control_node]
        if rest:
            second = max(rest, key=lambda p: abs(p["gex"]))
            lead = abs(control["gex"])
            runner_up = second["strike"]
            margin_pct = (round((lead - abs(second["gex"])) / lead * 100, 1)
                          if lead else 0.0)

    # --- ATM implied vol (nearest strike to spot) for expected-move math ----
    atm_iv = _atm_iv(chain, spot)

    # --- regime: the SIGN OF NET GAMMA AT SPOT, not a comparison to the flip.
    # These agree when the flip is computed correctly, but net_gex is the thing
    # being asked about and it is already exact — deriving the regime from a
    # derived level was how a -$3.7B (deeply negative) book got labelled
    # "positive gamma / fade the rips", i.e. exactly backwards.
    regime = "positive" if net_gex >= 0 else "negative"

    return {
        "spot": spot,
        "net_gex": net_gex,
        "regime": regime,
        "gamma_flip": flip,
        "control_node": control_node,
        # Runner-up strike and how far ahead the leader is, as a percentage of
        # the leader's own magnitude. Below ~15% the pick is a coin flip.
        "control_node_runner_up": runner_up,
        "control_node_margin_pct": margin_pct,
        "atm_iv": round(atm_iv, 4),
        "call_wall": call_wall["strike"] if call_wall else None,
        "put_wall": put_wall["strike"] if put_wall else None,
        "profile": profile,
        "put_call_ratio": pc_ratio,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_oi_change": call_oi_chg,
        "put_oi_change": put_oi_chg,
    }


def _atm_iv(chain: dict, spot: float) -> float:
    """Average implied vol of the call and put nearest to spot."""
    def nearest(rows):
        best, bd = 0.0, None
        for o in rows:
            d = abs(o["strike"] - spot)
            if bd is None or d < bd:
                bd, best = d, o["iv"]
        return best
    c, p = nearest(chain["calls"]), nearest(chain["puts"])
    vals = [v for v in (c, p) if v]
    return sum(vals) / len(vals) if vals else 0.0


def net_gex_at(chain: dict, spot: float, r: float) -> float:
    """
    Net dealer $GEX if the underlying were trading at `spot`.

    Note this is NOT "sum the existing per-strike profile". Gamma depends on
    spot, so every contract has to be re-priced at the candidate price. That
    re-pricing is the whole reason this function exists.
    """
    total = 0.0
    for rows, sign in ((chain["calls"], 1.0), (chain["puts"], -1.0)):
        for o in rows:
            g = bs_gamma(spot, o["strike"], o["t_years"], o["iv"], r)
            total += sign * dollar_gamma(g, o["oi"], spot)
    return total


def gamma_flip(chain: dict, spot: float, r: float,
               span: float = 0.15, steps: int = 60) -> float | None:
    """
    The zero-gamma level: the price at which net dealer gamma changes sign.
    Above it dealers are long gamma (they sell rips / buy dips, price pins);
    below it they are short gamma (hedging amplifies moves).

    Method: walk candidate spots outward from the current price across
    +/-`span`, find the first bracketing sign change, then bisect it. Scanning
    outward from spot matters — it returns the flip that price would actually
    reach first, rather than some far-out crossing.

    Returns None when net gamma holds one sign across the whole window. That
    is a real answer ("no flip in range"), not a failure, and it is reported
    as such instead of being papered over with a fabricated level.
    """
    if not (chain["calls"] or chain["puts"]) or spot <= 0:
        return None

    f0 = net_gex_at(chain, spot, r)
    if f0 == 0:
        return round(spot, 2)

    lo_b, hi_b = spot * (1 - span), spot * (1 + span)
    step = (hi_b - lo_b) / steps

    # candidate prices ordered by distance from spot, so the nearest flip wins
    offsets = sorted(
        [spot + i * step for i in range(1, steps + 1) if spot + i * step <= hi_b]
        + [spot - i * step for i in range(1, steps + 1) if spot - i * step >= lo_b],
        key=lambda s: abs(s - spot),
    )

    prev_s, prev_f = spot, f0
    for s in offsets:
        f = net_gex_at(chain, s, r)
        # only a bracket on the SAME side of spot is a genuine crossing;
        # candidates alternate sides, so re-anchor when the side changes
        if (s - spot) * (prev_s - spot) < 0:
            prev_s, prev_f = spot, f0
        if (prev_f < 0 <= f) or (prev_f > 0 >= f):
            return round(_bisect_zero(chain, r, prev_s, s), 2)
        prev_s, prev_f = s, f
    return None


def _bisect_zero(chain: dict, r: float, a: float, b: float, iters: int = 40) -> float:
    """Bisect net_gex_at to a zero between prices `a` and `b`."""
    fa = net_gex_at(chain, a, r)
    for _ in range(iters):
        m = 0.5 * (a + b)
        fm = net_gex_at(chain, m, r)
        if fm == 0:
            return m
        if (fa < 0) != (fm < 0):
            b = m
        else:
            a, fa = m, fm
    return 0.5 * (a + b)
