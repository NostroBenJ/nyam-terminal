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
from claude_brief import generate_brief


def _cross_check(gex: dict, uw_levels: dict, tol: float = 0.004) -> list | None:
    """
    Compare our Black-Scholes levels against Unusual Whales' computed ones.

    Both sides claim to measure the same thing and define it the same way, so
    a disagreement means one of them is wrong — and you want to know that
    before you trade off either. This does NOT overwrite our numbers with
    theirs; two independent computations that agree are evidence, and one that
    silently replaces the other is not.

    Returns None when UW levels aren't available (free provider, or the
    endpoint isn't in your tier).
    """
    if not uw_levels:
        return None
    pairs = [("Call Wall", gex.get("call_wall"), uw_levels.get("call_wall")),
             ("Put Wall", gex.get("put_wall"), uw_levels.get("put_wall")),
             ("Gamma Flip", gex.get("gamma_flip"), uw_levels.get("gamma_flip")),
             ("Magnet", gex.get("control_node"), uw_levels.get("gamma_magnet"))]
    out = []
    for name, ours, theirs in pairs:
        if ours is None or theirs is None:
            out.append({"level": name, "ours": ours, "uw": theirs, "agree": None})
            continue
        drift = abs(ours - theirs) / theirs if theirs else 1.0
        out.append({"level": name, "ours": ours, "uw": theirs,
                    "drift_pct": round(100 * drift, 2), "agree": drift <= tol})
    return out


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


def build_snapshot(ticker: str = None) -> dict:
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
    bias = bias_engine.build_bias(gex, levels, smt, news, em=em, neg_zone=neg_zone)
    brief = generate_brief(bias, gex, levels, smt, news)

    # --- the actionable layer ----------------------------------------------
    grid = matrix.build(per_expiry, p["spot"])
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
        "uw_errors": market.get("uw_errors") or {},
        "flow_alerts": market.get("flow_alerts"),
        "darkpool": market.get("darkpool"),
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
        "brief": brief,
        "news": news,
        "track": track,
    }
