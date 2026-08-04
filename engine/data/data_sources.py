"""
data_sources.py  --  The ONLY file that touches the outside world.

In mock mode it returns synthetic data (free, offline). In live mode it pulls
free option chains from Yahoo Finance via yfinance and reshapes them into the
exact same structure the analysis code expects. Because everything downstream
reads the same shape, you can swap data providers here without touching the
GEX math, the bias engine, or the UI.

To go live:
    pip install yfinance
    export NYAM_MOCK=0
"""
import datetime as dt
import time as _time

import config
from data.mock_data import mock_market


def get_ohlc(ticker: str, date_iso: str) -> dict | None:
    """
    Fetch the session's open and the price at GRADE_EXIT_TIME, for grading.

    Returns {"open", "exit", "note"} or None. `note` is empty on a clean read
    and carries a caveat otherwise, so a record graded by a fallback can never
    be mistaken for one graded the intended way. The tracker composes the full
    rule string from it, since only the tracker knows the ticker's band.

    Grading the bias over hours you aren't in the market measures the wrong
    thing, so the exit is the configured intraday time rather than the close.
    That needs intraday bars, which Yahoo only serves for the last ~60 days —
    fine in practice, since grading runs the same afternoon as the call.
    """
    if config.USE_MOCK_DATA:
        return None
    import yfinance as yf

    d = dt.date.fromisoformat(date_iso)
    tk = yf.Ticker(ticker)
    exit_h, exit_m = map(int, config.GRADE_EXIT_TIME.split(":"))

    try:
        bars = tk.history(start=d.isoformat(),
                          end=(d + dt.timedelta(days=1)).isoformat(),
                          interval="30m", prepost=False)
    except Exception:
        bars = None

    if bars is not None and len(bars):
        bars = bars[bars["Volume"] > 0]
        idx = bars.index
        rth = bars[(idx.hour > 9) | ((idx.hour == 9) & (idx.minute >= 30))]
        if len(rth):
            o = float(rth["Open"].iloc[0])
            # the bar STARTING 30 minutes before the exit time closes on it
            at = rth.index
            hit = rth[(at.hour == exit_h - 1) & (at.minute == 30)] if exit_m == 0 \
                else rth[(at.hour == exit_h) & (at.minute == exit_m - 30)]
            if len(hit):
                return {"open": o, "exit": float(hit["Close"].iloc[0]), "note": ""}
            # session ended before the exit time (half day) — use its last print
            return {"open": o, "exit": float(rth["Close"].iloc[-1]),
                    "note": "[short session]"}

    # Older than Yahoo's intraday window. Grade on the close, but SAY SO —
    # silently mixing a full-day result into a morning hit rate would corrupt
    # the one number this whole panel exists to report.
    h = tk.history(start=d.isoformat(), end=(d + dt.timedelta(days=1)).isoformat())
    if len(h) == 0:
        return None
    return {"open": float(h["Open"].iloc[0]), "exit": float(h["Close"].iloc[0]),
            "note": "[FULL DAY - intraday unavailable]"}


# ----------------------------------------------------------------------------
# PRICE BARS (for the chart)
# ----------------------------------------------------------------------------
# Lightweight Charts wants epoch SECONDS for intraday series and a "YYYY-MM-DD"
# string for daily ones. Both are produced here rather than in the frontend, so
# there is exactly one place that knows the convention.

_INTRADAY = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}


def get_bars(ticker: str, interval: str = "5m", lookback_days: int = 5) -> dict:
    """
    OHLC bars for the price chart.

    Returns {"bars": [...], "source", "interval", "note"}. `source` is always
    reported so the UI can say whether a candle came from a paid real-time feed
    or a delayed free one — those are different claims about the same number.

    Never raises for a data problem: an empty list with a `note` lets the panel
    render an honest empty state instead of taking the whole screen down.
    """
    ticker = (ticker or config.PRIMARY_TICKER).upper()
    if config.USE_MOCK_DATA:
        return _mock_bars(ticker, interval, lookback_days)
    if config.PROVIDER == "uw":
        try:
            return _uw_bars(ticker, interval, lookback_days)
        except Exception as e:
            # Degrade to the free path rather than blanking the chart, and say
            # in `note` that this is the fallback, not the tier you paid for.
            out = _yahoo_bars(ticker, interval, lookback_days)
            out["note"] = f"UW unavailable ({e}); showing Yahoo".strip()
            return out
    return _yahoo_bars(ticker, interval, lookback_days)


def _mock_bars(ticker: str, interval: str, lookback_days: int) -> dict:
    """
    A deterministic synthetic walk that ENDS EXACTLY AT THE MOCK SPOT.

    That endpoint constraint is the whole point. The GEX levels, the walls and
    the bias are all computed off `_mock_spot`, so a chart that drifted to some
    other last price would render the walls on the wrong side of the candles
    and make correct code look broken. The walk is generated freely, then
    shifted so its final close lands on spot — the shape stays random, the
    anchor does not.
    """
    import math
    import random as _r
    from data.mock_data import _mock_spot

    step = _INTRADAY.get(interval, 300)
    spot = _mock_spot(ticker)
    # ~6.5h of RTH per day, capped so the payload stays small on 1m.
    n = min(int(lookback_days * 6.5 * 3600 / step), 900)
    if n < 2:
        n = 2

    rng = _r.Random(hash((ticker, interval, lookback_days)) & 0xFFFFFFFF)
    # Per-bar sigma scaled off a ~0.8% daily move, so 1m and 30m bars look
    # like themselves rather than like the same series at different zooms.
    sigma = spot * 0.008 * math.sqrt(step / (6.5 * 3600))

    closes = []
    px = spot
    for _ in range(n):
        px += rng.gauss(0, sigma)
        closes.append(px)
    drift = closes[-1] - spot
    closes = [c - drift for c in closes]          # land exactly on spot

    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    t0 = now - (now % step) - (n - 1) * step

    bars, prev = [], closes[0] - rng.gauss(0, sigma)
    for i, c in enumerate(closes):
        o = prev
        wick = abs(rng.gauss(0, sigma)) * 0.6
        bars.append({
            "time": t0 + i * step,
            "open": round(o, 2),
            "high": round(max(o, c) + wick, 2),
            "low": round(min(o, c) - wick, 2),
            "close": round(c, 2),
            "volume": int(abs(rng.gauss(0, 1)) * 50_000 + 10_000),
        })
        prev = c
    return {"bars": bars, "source": "mock", "interval": interval,
            "note": "synthetic — anchored to the mock spot, not a market"}


def _yahoo_bars(ticker: str, interval: str, lookback_days: int) -> dict:
    import yfinance as yf

    # Yahoo caps intraday history: 1m to ~7d, other intraday to ~60d.
    cap = 7 if interval == "1m" else 60
    days = max(1, min(lookback_days, cap))
    try:
        h = yf.Ticker(ticker).history(
            period=f"{days}d", interval=interval, prepost=False)
    except Exception as e:
        return {"bars": [], "source": "yahoo", "interval": interval,
                "note": f"fetch failed: {e}"}
    if h is None or len(h) == 0:
        return {"bars": [], "source": "yahoo", "interval": interval,
                "note": "no bars returned"}

    # Zero-volume extended-hours prints carry nonsense highs and lows — the
    # same phantom-bar problem that put the overnight low 5% off reality.
    if "Volume" in h:
        h = h[h["Volume"] > 0]

    daily = interval not in _INTRADAY
    bars = []
    for idx, row in h.iterrows():
        t = idx.strftime("%Y-%m-%d") if daily else int(idx.timestamp())
        bars.append({
            "time": t,
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row.get("Volume", 0) or 0),
        })
    return {"bars": bars, "source": "yahoo", "interval": interval,
            "note": "~15-min delayed"}


def _uw_bars(ticker: str, interval: str, lookback_days: int) -> dict:
    """UW candle sizes are named differently ('1h', '1d', '5m' ...)."""
    from data import unusual_whales as uw

    rows = uw.ohlc(ticker, candle_size=interval, limit=1000)
    daily = interval not in _INTRADAY
    bars = []
    for r in rows:
        # UW returns numbers as JSON STRINGS on many endpoints; coercing here
        # keeps that quirk from leaking into the chart as NaN candles.
        try:
            stamp = r.get("start_time") or r.get("timestamp") or r.get("date")
            o, h_, l_, c = (float(r["open"]), float(r["high"]),
                            float(r["low"]), float(r["close"]))
        except (KeyError, TypeError, ValueError):
            continue
        if daily:
            t = str(stamp)[:10]
        else:
            t = int(dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp())
        bars.append({"time": t, "open": round(o, 2), "high": round(h_, 2),
                     "low": round(l_, 2), "close": round(c, 2),
                     "volume": int(float(r.get("volume", 0) or 0))})
    bars.sort(key=lambda b: b["time"])
    return {"bars": bars, "source": "unusual_whales", "interval": interval,
            "note": "" if bars else "no bars returned"}


def get_market(ticker: str = None) -> dict:
    ticker = (ticker or config.PRIMARY_TICKER).upper()
    if config.USE_MOCK_DATA:
        return mock_market(ticker)
    if config.PROVIDER == "uw":
        return _uw_market(ticker)
    return _live_market(ticker)


# ----------------------------------------------------------------------------
# UNUSUAL WHALES  (paid; real-time chains, day-over-day OI, flow, dark pool)
# ----------------------------------------------------------------------------
def _uw_market(ticker: str) -> dict:
    """
    UW for the option chain and the extras; Yahoo still supplies session OHLC
    and the confirmer leg.

    Deliberately PARTIAL rather than all-or-nothing. Endpoint coverage varies
    by UW tier, and a bias built on three of four signals is more useful than
    a dashboard that refuses to load because the dark pool endpoint 403s. Every
    piece that fails degrades to the free path and records why in `sources`,
    so the UI can show what's actually live rather than implying it all is.
    """
    from data import unusual_whales as uw

    market = {"sources": {}, "uw_errors": {}}

    def _try(name, fn):
        try:
            return fn()
        except Exception as e:                       # incl. uw.UWError
            market["uw_errors"][name] = str(e)
            return None

    # NO YAHOO BASELINE. This used to start from _live_market() and overwrite
    # the pieces UW covered, which left spot real-time while the overnight
    # range it is compared against was ~15 minutes behind. That combination is
    # not "mostly live", it is internally impossible: spot could print above an
    # overnight high that did not yet know the print happened, and every
    # level-relative read downstream inherited the contradiction.
    #
    # One provider, one clock. Anything UW cannot answer is reported as missing
    # rather than quietly backfilled from a feed on a different clock.
    market["primary"] = _uw_ticker(uw, ticker, market, is_primary=True)
    market["secondary"] = _uw_ticker(uw, config.confirmer_for(ticker), market)
    # NOT fetched per refresh. `_news_for` in pipeline.py reads the RSS rail
    # live and only falls back to market["news"] in mock mode, so populating
    # this here would spend a request every cycle on data nothing renders.
    # `_uw_news` and `uw.headlines()` stay ready for wiring into the rail —
    # UW carries Truth Social and aggregator headlines the RSS feeds do not.
    market["news"] = []
    market["sources"]["news"] = "rss (see news_feed.py)"

    contracts = _try("chain", lambda: uw.option_contracts(
        ticker, exclude_zero_oi_chains=True))
    if contracts:
        expiries = uw.chain_to_expiries(contracts, config.GEX_MAX_DTE)
        if expiries:
            market["primary"]["expiries"] = expiries
            market["sources"]["chain"] = "unusual_whales"

    lv = _try("gex_levels", lambda: uw.gex_levels(ticker))
    if lv:
        market["uw_levels"] = uw.levels_to_floats(lv)
        market["sources"]["uw_levels"] = "unusual_whales"

    fa = _try("flow", lambda: uw.flow_alerts(ticker, limit=15))
    if fa:
        market["flow_alerts"] = uw.summarize_flow(fa)
        market["sources"]["flow"] = "unusual_whales"

    dp = _try("darkpool", lambda: uw.darkpool(ticker, limit=15))
    if dp:
        market["darkpool"] = uw.summarize_darkpool(dp)
        market["sources"]["darkpool"] = "unusual_whales"

    nf = _try("net_flow", lambda: uw.net_prem_ticks(ticker))
    if nf:
        market["net_flow"] = uw.summarize_net_flow(nf)
        market["sources"]["net_flow"] = "unusual_whales"

    mp = _try("max_pain", lambda: uw.max_pain(ticker))
    if mp:
        # Nearest expiry only. The far-dated rows are real but not actionable
        # today, and showing eight of them buries the one that matters.
        rows = sorted(mp, key=lambda r: str(r.get("expiry") or ""))
        market["max_pain"] = [{"expiry": r.get("expiry"),
                               "strike": uw._f(r, "max_pain")}
                              for r in rows[:3] if uw._f(r, "max_pain") > 0]
        market["sources"]["max_pain"] = "unusual_whales"

    market["oi_report"] = _uw_oi_report(ticker, market)
    return market


def _uw_ticker(uw, symbol: str, market: dict, is_primary: bool = False) -> dict:
    """
    One ticker's session shape, entirely from UW.

    Same contract `_live_ticker` produced, so everything downstream — SMT, the
    level map, the bias engine — is unchanged. The difference is that spot,
    the prior session and the overnight range now share one clock.

    A failure here is recorded and the field left absent rather than filled
    from Yahoo. Backfilling from a second feed is what created the impossible
    state this function exists to remove, and a missing number the UI can show
    as missing beats a plausible one from the wrong clock.
    """
    key = symbol.upper()
    out = {"ticker": key}

    st = None
    try:
        st = uw.stock_state(key)
    except Exception as e:                            # noqa: BLE001
        market["uw_errors"][f"{key}:spot"] = str(e)
    if st:
        px = uw._f(st, "close")
        if px > 0:
            out["spot"] = round(px, 2)
            market["sources"]["spot"] = "unusual_whales"
        # Server-side stamp: the age of this number is measured, not assumed.
        # Only the traded instrument's stamp goes on the board; the confirmer
        # has its own and showing whichever arrived last would be meaningless.
        if is_primary:
            market["tape_time"] = st.get("tape_time")
            market["market_time"] = st.get("market_time")

    try:
        prior = uw.prior_session(key)
        out["prior_high"] = round(prior["high"], 2)
        out["prior_low"] = round(prior["low"], 2)
        out["prior_close"] = round(prior["close"], 2)
        market["sources"]["prior_session"] = "unusual_whales"
    except Exception as e:                            # noqa: BLE001
        market["uw_errors"][f"{key}:prior_session"] = str(e)
        return out                                    # overnight needs prior

    try:
        hi, lo, real = uw.overnight_range(key, prior, config.TZ)
        out["on_high"] = round(hi, 2)
        out["on_low"] = round(lo, 2)
        # False => no overnight session yet; the prior range is standing in and
        # SMT must not read it as a real divergence.
        out["on_is_real"] = real
        market["sources"]["overnight"] = "unusual_whales"
    except Exception as e:                            # noqa: BLE001
        market["uw_errors"][f"{key}:overnight"] = str(e)

    return out


def _uw_news(uw, ticker: str) -> list:
    """
    UW headlines, shaped like the news items the UI already renders.

    Kept separate from the RSS rail in news_feed.py, which stays as it is: that
    covers Fed/Treasury/macro sources UW does not carry, and this covers the
    tape. Two different things that both happen to be called news.
    """
    rows = uw.headlines(limit=40, ticker=ticker)
    items = []
    for r in rows:
        title = (r.get("headline") or "").strip()
        if not title:
            continue
        items.append({
            "title": title,
            "source": r.get("source") or "Unusual Whales",
            "published": r.get("created_at"),
            "sentiment": r.get("sentiment"),
            "major": bool(r.get("is_major")),
            "tickers": r.get("tickers") or [],
        })
    return items


def _uw_oi_report(ticker: str, market: dict) -> dict:
    """
    Snapshot the chain for history, but let UW's own OI change stand.

    The store still records today's chain — that dataset is the whole reason
    the recorder exists and an option chain cannot be re-fetched for a past
    date. What it does NOT do any more is overwrite `oi_change`. On the Yahoo
    path that field was computed by diffing our own snapshots because there was
    no alternative; UW carries the exchange's `prev_oi`, which is the real
    number rather than our reconstruction of it. Applying the local diff on top
    would replace a measurement with an estimate.
    """
    from data import oi_store

    expiries = (market.get("primary") or {}).get("expiries")
    if not expiries:
        return {"available": False, "note": "no chain loaded"}
    try:
        oi_store.snapshot(ticker, expiries)
    except Exception as e:                            # noqa: BLE001
        return {"available": False,
                "note": f"OI snapshot failed ({type(e).__name__}: {e})"}
    changed = sum(1 for e in expiries
                  for o in e["calls"] + e["puts"] if o.get("oi_change"))
    return {
        "available": changed > 0,
        "source": "unusual_whales",
        "legs_with_change": changed,
        "note": ("day-over-day OI from UW prev_oi" if changed
                 else "UW returned no prev_oi; oi_change is 0"),
    }


# ----------------------------------------------------------------------------
# LIVE  (free, ~15-min delayed — fine for a pre-market bias)
# ----------------------------------------------------------------------------
def _live_market(ticker: str) -> dict:
    import yfinance as yf

    from data import oi_store

    confirmer = config.confirmer_for(ticker)
    primary = _live_ticker(yf, ticker, with_chain=True)
    secondary = _live_ticker(yf, confirmer, with_chain=False)

    # Day-over-day OI is the one thing the free path can't fetch — so store it
    # ourselves. Snapshot first (today's chain, written once), then diff against
    # the previous session to fill oi_change, which was otherwise stuck at 0.
    oi_report = {"available": False, "note": "no chain loaded"}
    if primary.get("expiries"):
        try:
            oi_store.snapshot(ticker, primary["expiries"])
            oi_report = oi_store.apply_oi_change(ticker, primary["expiries"])
        except Exception as e:                    # never let storage break a load
            oi_report = {"available": False,
                         "note": f"OI snapshot failed ({type(e).__name__}: {e})"}

    return {
        "primary": primary,
        "secondary": secondary,
        "news": _live_news(yf, ticker),
        "oi_report": oi_report,
    }


# ---------------------------------------------------------------------------
# Chain cache
# ---------------------------------------------------------------------------
# Splits the expensive, slow-moving part of a refresh (the option chain and the
# overnight range — 6 of 9 Yahoo requests) from the cheap, fast-moving part
# (spot). Yahoo publishes open interest once a day; spot moves all session and
# genuinely changes GEX, because gamma is spot-dependent. So the quote is
# re-fetched every cycle and the chain is reused until CHAIN_REFRESH_SECONDS.
#
# Cached expiry dicts are handed back by reference. The only thing downstream
# that writes to them is `oi_store.apply_oi_change`, which recomputes
# `oi_change` from `oi` against a stored baseline — idempotent, so re-applying
# it to a reused chain gives the same answer rather than compounding.
_chain_cache: dict = {}


def chain_cache_state(ticker: str) -> dict:
    """For the UI: how old the cached chain is, and when it refreshes."""
    hit = _chain_cache.get((ticker or "").upper())
    if not hit:
        return {"cached": False, "age_s": None, "ttl_s": config.CHAIN_REFRESH_SECONDS}
    return {"cached": True, "age_s": int(_time.time() - hit["at"]),
            "ttl_s": config.CHAIN_REFRESH_SECONDS}


def _live_ticker(yf, symbol: str, with_chain: bool) -> dict:
    tk = yf.Ticker(symbol)
    hist = tk.history(period="10d", interval="1d")
    spot = float(hist["Close"].iloc[-1])

    # "Prior session" must be the last COMPLETED regular session. Whether the
    # current day already has a daily bar depends on the time of day you run
    # this, so indexing a fixed iloc[-2] silently shifts the reference day by
    # one during pre-market -- exactly when this tool is meant to be used.
    today = dt.date.today()
    last_idx = hist.index[-1].date()
    prior = hist.iloc[-2] if last_idx >= today else hist.iloc[-1]

    # The chain and the overnight range are the slow half of a refresh; spot
    # above is the fast half and is always re-fetched.
    key = symbol.upper()
    hit = _chain_cache.get(key)
    fresh = hit and (_time.time() - hit["at"]) < config.CHAIN_REFRESH_SECONDS
    # A cache entry that predates the current session is never reused: expiries
    # roll and yesterday's chain would be quietly wrong rather than merely old.
    if fresh and hit.get("day") != dt.date.today():
        fresh = False

    if fresh and (not with_chain or hit.get("expiries") is not None):
        on_high, on_low, on_is_real = hit["overnight"]
        expiries = hit.get("expiries")
    else:
        on_high, on_low, on_is_real = _overnight_range(tk, prior)
        expiries = _live_expiries(tk) if with_chain else None
        _chain_cache[key] = {
            "at": _time.time(), "day": dt.date.today(),
            "overnight": (on_high, on_low, on_is_real), "expiries": expiries,
        }

    out = {
        "ticker": symbol,
        "spot": round(spot, 2),
        "prior_high": round(float(prior["High"]), 2),
        "prior_low": round(float(prior["Low"]), 2),
        "prior_close": round(float(prior["Close"]), 2),
        "on_high": round(on_high, 2),
        "on_low": round(on_low, 2),
        # False => no overnight session yet; prior-day range is standing in and
        # SMT should not be read as a real divergence signal.
        "on_is_real": on_is_real,
    }
    if with_chain:
        out["expiries"] = expiries or []
    return out


def _overnight_range(tk, prior) -> tuple:
    """
    True overnight (Globex) range: prior session's 16:00 ET close through now.

    Two things this has to get right, both of which were wrong before:

    1. WINDOW. `period="1d"` returns the last trading day 04:00-20:00 — that is
       a full regular session, not an overnight. Feeding that into SMT compares
       yesterday's day range against yesterday's day range. The overnight that
       matters for the next open starts at the PRIOR close.
    2. BAD TICKS. Yahoo's extended-hours bars include zero-volume prints with
       nonsense lows (an observed QQQ bar: O 684.85 / C 684.76 / L 649.28, vol
       0). One of those drags the overnight low 5% below reality and poisons
       every level and divergence read downstream. Zero-volume bars are dropped.

    Returns (high, low, is_real). `is_real` is False when no overnight session
    exists yet (weekends, or before the first post-close print), in which case
    the prior session's range stands in. Never widen the fallback beyond that:
    reporting a multi-day range as "overnight" invents a breakout that never
    happened and hands SMT a fake divergence.
    """
    fallback = (float(prior["High"]), float(prior["Low"]), False)
    try:
        bars = tk.history(period="5d", interval="5m", prepost=True)
    except Exception:
        return fallback
    if bars is None or len(bars) == 0:
        return fallback

    bars = bars[bars["Volume"] > 0]           # drop phantom prints
    if len(bars) == 0:
        return fallback

    # anchor at the close of the last completed regular session
    idx = bars.index
    rth_close = idx[(idx.hour == 15) & (idx.minute >= 55)]
    if not len(rth_close):
        return fallback
    on = bars[idx > rth_close[-1]]
    if not len(on):
        return fallback
    return float(on["High"].max()), float(on["Low"].min()), True


def _live_expiries(tk) -> list:
    """Return a list of per-expiration chains shaped for compute_gex()."""
    today = dt.date.today()
    expiries = []
    for exp in tk.options:
        exp_date = dt.date.fromisoformat(exp)
        dte = (exp_date - today).days
        if dte < 0 or dte > config.GEX_MAX_DTE:
            continue
        t_years = max(dte, 0.5) / 365.0
        oc = tk.option_chain(exp)
        calls, puts = [], []
        for df, bucket in ((oc.calls, calls), (oc.puts, puts)):
            for _, row in df.iterrows():
                iv = float(row.get("impliedVolatility", 0) or 0)
                oi = float(row.get("openInterest", 0) or 0)
                if iv <= 0 or oi <= 0:
                    continue
                bucket.append({
                    "strike": float(row["strike"]),
                    "oi": oi,
                    "iv": iv,
                    "t_years": t_years,
                    # yfinance snapshots have no day-over-day OI; persist daily
                    # snapshots yourself to fill this in (see README TODO).
                    "oi_change": 0,
                })
        expiries.append({"label": exp, "dte": dte, "calls": calls, "puts": puts})
    return expiries


def _live_news(yf, symbol: str) -> dict:
    """Lightweight free news pull. Econ-calendar wiring is a README TODO."""
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:
        items = []
    headlines = [{"time": "", "event": n.get("title", ""), "impact": "unknown"} for n in items[:5]]
    return {"high_impact": False, "headline": headlines[0]["event"] if headlines else "", "items": headlines}
