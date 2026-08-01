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

    market = _live_market(ticker)          # Yahoo baseline: OHLC + confirmer
    market["sources"] = {"chain": "yahoo", "levels": "yahoo",
                         "flow": None, "darkpool": None, "uw_levels": None}
    market["uw_errors"] = {}

    def _try(name, fn):
        try:
            return fn()
        except Exception as e:                       # incl. uw.UWError
            market["uw_errors"][name] = str(e)
            return None

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

    return market


# ----------------------------------------------------------------------------
# LIVE  (free, ~15-min delayed — fine for a pre-market bias)
# ----------------------------------------------------------------------------
def _live_market(ticker: str) -> dict:
    import yfinance as yf

    confirmer = config.confirmer_for(ticker)
    primary = _live_ticker(yf, ticker, with_chain=True)
    secondary = _live_ticker(yf, confirmer, with_chain=False)
    return {
        "primary": primary,
        "secondary": secondary,
        "news": _live_news(yf, ticker),
    }


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

    on_high, on_low, on_is_real = _overnight_range(tk, prior)

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
        out["expiries"] = _live_expiries(tk)
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
