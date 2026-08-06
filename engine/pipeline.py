"""
pipeline.py  --  Runs the whole chain into one snapshot dict the dashboard
and the Obsidian logger both consume.
   raw data -> per-expiry GEX + aggregate GEX -> derived reads -> bias -> brief
"""
import datetime as dt

import config
from analysis import gex as gex_mod
from analysis import levels as levels_mod
from analysis import smt as smt_mod
from analysis import bias_engine
from analysis import derived
from analysis import matrix
from analysis import plan
from analysis import tracker
import store
from data.data_sources import get_market
from claude_brief import generate_brief_meta


def _cross_check(gex: dict, uw_levels: dict, tol: float = 0.004) -> list | None:
    """
    Compare our Black-Scholes levels against Unusual Whales' computed ones.

    Two independent computations agreeing is evidence; one silently replacing
    the other is not. This never overwrites our numbers with theirs.

    NOT EVERY DISAGREEMENT IS AN ERROR. An earlier version of this docstring
    asserted both sides "define it the same way", and for the gamma flip that
    is measurably false — see FLIP_NOTE. Flagging a definitional difference as
    a drift failure trains you to ignore the one panel whose entire job is to
    catch real breakage, so levels with a known definitional gap carry a note
    and are excluded from the agree/disagree verdict rather than failing it
    every single day.

    Returns None when UW levels aren't available.
    """
    if not uw_levels:
        return None
    pairs = [("Call Wall", gex.get("call_wall"), uw_levels.get("call_wall"), None),
             ("Put Wall", gex.get("put_wall"), uw_levels.get("put_wall"), PUT_WALL_NOTE),
             ("Gamma Flip", gex.get("gamma_flip"), uw_levels.get("gamma_flip"), FLIP_NOTE),
             ("Magnet", gex.get("control_node"), uw_levels.get("gamma_magnet"),
              MAGNET_NOTE)]
    out = []
    for name, ours, theirs, note in pairs:
        row = {"level": name, "ours": ours, "uw": theirs, "note": note}
        if ours is None or theirs is None:
            row["agree"] = None
            out.append(row)
            continue
        drift = abs(ours - theirs) / theirs if theirs else 1.0
        row["drift_pct"] = round(100 * drift, 2)
        # A known definitional difference is reported, not graded.
        row["agree"] = None if note else drift <= tol
        out.append(row)
    return out


# Measured 2026-08-03 against the live chain, not assumed. Two hypotheses were
# tested and both are dead: widening GEX_MAX_DTE from 1 to 365 moves our flip
# only 738.85 -> 747.9 and never toward UW's 764.08, and flipping the dealer
# sign convention destroys the call wall entirely, which the evidence forbids
# because our call wall matches UW's EXACTLY at 760.00 in every configuration.
#
# What does explain it: computing the CUMULATIVE-across-strikes crossing on our
# own profile yields 751.98 / 756.23 / 757.62 / 759.66 / 761.28 at windows of
# 7 / 21 / 45 / 90 / 365 days — walking straight at UW's number. They use the
# common cumulative shortcut over the whole chain; we re-price the chain at
# candidate spots and bisect the true zero.
#
# Ours is a real zero: net gamma at our flip is 375,770 against a book of
# 7.5 BILLION, i.e. 0.005%. At UW's flip our net gamma is +8,163,449,565 —
# nowhere near a regime boundary. gex.py deliberately removed the cumulative
# approximation because it read float-noise sign flips among worthless deep-OTM
# strikes as a regime line hundreds of points below spot.
#
# So: keep ours, show theirs, and stop calling it a discrepancy.
FLIP_NOTE = ("Different definitions, not a discrepancy. Ours re-prices the "
             "chain at candidate spots and bisects the true zero; UW uses the "
             "cumulative-across-strikes crossing over the full chain. Our flip "
             "sits at 0.005% of net gamma; theirs sits at +$8.2B of it.")

PUT_WALL_NOTE = ("Window-dependent. Ours is the largest negative-gamma strike "
                 "inside GEX_MAX_DTE; UW scans the full chain, so theirs sits "
                 "nearer spot. Ours moves with the window, theirs does not.")

# Measured 2026-08-04: our magnet and UW's agreed EXACTLY at 775, having
# differed by 16 points an hour earlier. Neither was wrong. The magnet is an
# argmax over near-tied strikes — the leader beat the runner-up by 5.4%, and a
# 0.1% move in spot flipped the answer from 775 to 762 with the chain
# unchanged. Two correct implementations sampling seconds apart land on
# different strikes. Grading that as drift would flag noise as breakage, so it
# is reported with its margin instead.
MAGNET_NOTE = ("An argmax over near-tied strikes, so it is the least stable "
               "level here: a 0.1% move in spot has been observed flipping it "
               "13 points with the chain unchanged. A gap versus UW usually "
               "means the two were sampled moments apart, not that either is "
               "wrong. Read the margin beside it.")


def _news_for(ticker: str, market: dict) -> dict:
    """
    Resolve the news/event-risk input the bias engine reads.

    In MOCK MODE this stays on the synthetic calendar — mock is meant to run
    offline with no keys and no cost, and quietly reaching the network there
    would break that promise.

    Live, it reads the real feeds. Before this, `_live_news` hardcoded
    `high_impact: False`, so the News Risk signal could never fire outside mock
    — the panel was written, the reason text existed, and the path was dead.
    A failure here degrades to "no known event risk" AND says so, because
    silently reporting a calm tape when the feed is down is the dangerous
    direction to be wrong in.
    """
    if config.USE_MOCK_DATA:
        return market.get("news") or {"high_impact": False, "headline": "", "items": []}

    from analysis import news_risk
    from data import news_feed

    try:
        feed = news_feed.fetch_news_cached(ticker, limit=80)
    except Exception as e:
        return {"high_impact": False, "headline": "",
                "items": [], "kind": "unavailable", "level": "unknown",
                "why": f"News feed unavailable ({type(e).__name__}) — event risk "
                       f"is UNKNOWN, not absent.",
                "error": str(e)}

    risk = news_risk.assess(feed.get("items", []))
    risk["feed_errors"] = feed.get("errors", {})
    risk["feed_count"] = len(feed.get("sources", {}))
    # Headlines the UI can show beside the signal that used them.
    risk["headlines"] = [
        {"title": i["title"], "source": i["source"], "age_hours": i["age_hours"],
         "tier": i["tier"], "link": i["link"]}
        for i in feed.get("items", [])[:8]
    ]
    return risk


def build_snapshot(ticker: str = None, with_brief: bool = True) -> dict:
    """
    Build the full board for `ticker`.

    `with_brief=False` returns everything except the written brief, which is
    the expensive part and the only part nothing else depends on. See the
    comment at the brief call for the measurements.
    """
    market = get_market(ticker)
    p, s = market["primary"], market["secondary"]
    r = config.RISK_FREE_RATE
    expiries = p["expiries"]

    # --- aggregate GEX across all loaded expirations -----------------------
    merged = {"calls": [], "puts": []}
    for e in expiries:
        merged["calls"] += e["calls"]
        merged["puts"] += e["puts"]
    gex = gex_mod.compute_gex(merged, p["spot"], r)

    # --- per-expiry GEX (confluence + the strike x expiry grid) -------------
    per_expiry = [
        {"label": e["label"], "dte": e["dte"],
         "gex": gex_mod.compute_gex({"calls": e["calls"], "puts": e["puts"]}, p["spot"], r)}
        for e in expiries
    ]
    front_dte = min(e["dte"] for e in expiries)

    # --- derived reads ------------------------------------------------------
    em = derived.expected_move(p["spot"], gex["atm_iv"], front_dte)
    expiry_conf = derived.multi_expiry_confluence(per_expiry)
    level_map = derived.build_level_map(gex)
    neg_zone = derived.neg_gamma_zone(gex)

    # --- session levels + cross-market -------------------------------------
    levels = levels_mod.session_levels({
        "prior_high": p["prior_high"], "prior_low": p["prior_low"],
        "prior_close": p["prior_close"], "on_high": p["on_high"],
        "on_low": p["on_low"], "spot": p["spot"],
    })
    confluences = levels_mod.find_confluences(levels, gex)
    smt = smt_mod.smt_divergence(
        {"name": p["ticker"], "on_high": p["on_high"], "on_low": p["on_low"],
         "prior_high": p["prior_high"], "prior_low": p["prior_low"], "spot": p["spot"],
         "on_is_real": p.get("on_is_real", True)},
        {"name": s["ticker"], "on_high": s["on_high"], "on_low": s["on_low"],
         "prior_high": s["prior_high"], "prior_low": s["prior_low"], "spot": s["spot"],
         "on_is_real": s.get("on_is_real", True)},
    )

    news = _news_for(p["ticker"], market)
    # Flow reads what money DID today; everything else reads where it is
    # positioned. Passed as one dict so a provider without a flow feed simply
    # contributes no flow signals rather than the engine needing to know why.
    flow_inputs = {
        "net_flow": market.get("net_flow"),
        "flow_alerts": market.get("flow_alerts"),
        "darkpool": market.get("darkpool"),
    }
    bias = bias_engine.build_bias(gex, levels, smt, news, em=em,
                                  neg_zone=neg_zone, flow=flow_inputs)

    # THE BRIEF DOES NOT BLOCK THE BOARD. Measured on a cold snapshot: the whole
    # build is 19.6s, of which the Claude call is 13.9s — the remaining 5.7s is
    # the UW fetch and the maths. So for fourteen seconds the app had every
    # level, the bias and the matrix computed, and showed a spinner while
    # waiting on prose.
    #
    # Nothing downstream reads the brief: it is commentary written FROM the
    # bias, not an input to it. So it is generated after the fact by the caller
    # and patched into the cached snapshot when it lands. `with_brief=True`
    # keeps the synchronous path for the scheduled log and the capture
    # recorder, which write a file once and genuinely want the finished text.
    if with_brief:
        brief = generate_brief_meta(bias, gex, levels, smt, news, p["ticker"])
    else:
        brief = {"text": "", "source": "pending", "model": "", "error": None,
                 "cached": False, "age_s": None, "calls_today": None,
                 "budget_capped": False}

    # --- the actionable layer ----------------------------------------------
    # `gex` supplies the flip and walls so the grid always contains the rows
    # that decide the regime, however far from spot they sit.
    grid = matrix.build(per_expiry, p["spot"], levels=gex)
    trade_plan = plan.build(gex, em=em, neg_zone=neg_zone)
    trend = plan.trend_read(gex, smt, bias)

    tracker.ensure_seeded()                       # mock-only demo history
    # stats are per-ticker: a SPY hit rate says nothing about NVDA
    track = tracker.compute_stats(store.load(), ticker=p["ticker"])

    return {
        "generated_at": dt.datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "ticker": p["ticker"],
        "confirmer": s["ticker"],
        "tickers": config.TICKERS,
        "mock": config.USE_MOCK_DATA,
        "provider": config.PROVIDER,
        "sources": market.get("sources"),
        # WHEN THE MARKET DATA IS FROM, which is not when we fetched it. The UI
        # derived staleness from `generated_at` alone, so a snapshot built one
        # second ago read "live" whether the price underneath it was one second
        # or fifteen minutes old — the age chip was measuring our own promptness
        # and presenting it as the freshness of the market. UW stamps its tape
        # server-side; Yahoo offers nothing equivalent, and null here is the
        # honest answer for it rather than a number we made up.
        "tape_time": market.get("tape_time"),
        "market_time": market.get("market_time"),
        "uw_errors": market.get("uw_errors") or {},
        "flow_alerts": market.get("flow_alerts"),
        "darkpool": market.get("darkpool"),
        "net_flow": market.get("net_flow"),
        "max_pain": market.get("max_pain"),
        "level_check": _cross_check(gex, market.get("uw_levels")),
        "matrix": grid,
        "plan": trade_plan,
        "trend": trend,
        "expiry_labels": [e["label"] for e in per_expiry],
        "expiries_loaded": len(expiries),
        "gex": gex,
        "expected_move": em,
        "level_map": level_map,
        "neg_zone": neg_zone,
        "levels": levels,
        "confluences": confluences,
        "expiry_confluence": expiry_conf,
        "smt": smt,
        "bias": bias,
        "brief": brief["text"],
        # Who wrote the brief. The UI labels it; it cannot tell from the prose.
        "brief_meta": {k: v for k, v in brief.items() if k != "text"},
        "news": news,
        "track": track,
    }
