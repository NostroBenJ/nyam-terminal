"""
unusual_whales.py  --  Unusual Whales API adapter.

Status: WRITTEN AGAINST THE PUBLISHED SPEC, NOT YET RUN AGAINST A LIVE KEY.
Every path and field name here was taken from the official OpenAPI document
(https://api.unusualwhales.com/api/openapi) rather than guessed — but no
response has actually been observed. Run `python -m data.unusual_whales probe`
the moment your key arrives; it hits each endpoint and prints exactly which
ones your tier allows and whether the shape matches what this file expects.
Treat anything the probe doesn't confirm as unverified.

WHY THIS IS A SEPARATE FILE FROM data_sources.py
The dashboard's contract is the market dict, and everything downstream — GEX
math, bias engine, UI — reads only that. Providers are swappable underneath it.
This file translates UW into that contract and nothing else.

WHAT UW GIVES YOU THAT YAHOO CANNOT
  - real-time chains instead of a ~15-minute-delayed snapshot
  - day-over-day open interest, which is the input `oi_change` has been
    hardcoded to 0 for (see the OI Shift signal in bias_engine.py — it has
    been silently dead on live data)
  - flow alerts and dark pool prints, which have no free equivalent
  - UW's own computed GEX levels, which is worth having as an independent
    check against our Black-Scholes numbers rather than a replacement for them

A NOTE ON TYPES: UW returns numbers as JSON *strings* ('9356683.4241', '150').
Every read goes through _f() / _i(). Do not index the raw dicts directly.
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import config

BASE = "https://api.unusualwhales.com"
TIMEOUT = 20
USER_AGENT = "nyam-terminal/1.0 (personal research client)"


def api_key() -> str:
    """
    The UW key, resolved at CALL time rather than import time.

    Deliberately not a module-level constant. `config` reads `engine/.env` into
    the environment as an import side effect, so a constant captured up here is
    empty or populated depending purely on whether config happened to be
    imported first. That is not a hypothetical: it is exactly how `probe`
    reported "UW_API_KEY is not set" with the key sitting in .env — this module
    is importable without ever pulling in config, so nothing loaded the file.
    It worked inside the server only because server.py imports config earlier.

    Reading through config (which has already loaded .env) with a live os.getenv
    fallback makes the answer independent of import order.
    """
    return config.UW_API_KEY or os.getenv("UW_API_KEY", "")


# ---------------------------------------------------------------------------
# request budget
# ---------------------------------------------------------------------------
# UW allows 30,000 requests/day (x-uw-token-req-limit); the per-minute headroom
# is effectively unlimited, so the daily count is the only real constraint. One
# full refresh of one ticker costs ~35 requests, 28 of which are the paged chain
# walk — so the budget is comfortable until either the refresh interval drops or
# the ticker list grows, and then it moves fast. Counted rather than estimated,
# because "I did the arithmetic once" is how you find out you were wrong at 3pm
# with the board frozen. Persisted so a restart doesn't reset the day's tally.
DAILY_LIMIT = 30000
_counter = {"date": None, "count": 0}


def _counter_path() -> str:
    return os.path.join(config.STORE_DIR, "uw_requests.json")


def _bump():
    today = dt.date.today().isoformat()
    if _counter["date"] != today:
        _counter.update(_load_counter(today))
    _counter["count"] += 1
    try:
        os.makedirs(config.STORE_DIR, exist_ok=True)
        with open(_counter_path(), "w", encoding="utf-8") as f:
            json.dump(_counter, f)
    except OSError:
        pass  # a budget counter must never be able to take the engine down


def _load_counter(today: str) -> dict:
    try:
        with open(_counter_path(), encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("date") == today:
            return {"date": today, "count": int(saved.get("count", 0))}
    except (OSError, ValueError):
        pass
    return {"date": today, "count": 0}


def budget() -> dict:
    """Today's request usage. Safe to call with no key set."""
    today = dt.date.today().isoformat()
    c = _counter if _counter["date"] == today else _load_counter(today)
    used = c["count"]
    return {
        "used": used,
        "limit": DAILY_LIMIT,
        "remaining": max(DAILY_LIMIT - used, 0),
        "pct": round(used / DAILY_LIMIT * 100, 1),
        "date": today,
    }


class UWError(RuntimeError):
    """Any failure talking to Unusual Whales. Carries the HTTP status if known."""

    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


# ---------------------------------------------------------------------------
# coercion — UW sends numbers as strings, and nulls are real
# ---------------------------------------------------------------------------
def _f(d: dict, key: str, default=0.0) -> float:
    v = d.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(d: dict, key: str, default=0) -> int:
    return int(_f(d, key, default))


def _get(path: str, params: dict = None, key: str = None) -> dict:
    """GET an endpoint and return the parsed body. Raises UWError on failure."""
    key = key or api_key()
    if not key:
        raise UWError("UW_API_KEY is not set")
    url = BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        # REQUIRED. Not politeness — urllib's default "Python-urllib/3.x" is
        # rejected by UW's Cloudflare edge with error 1010 ("blocked based on
        # your browser's signature") before the request reaches the API at all.
        # Every endpoint 403s with a valid key. Any real UA string returns 200;
        # tested against urllib-default / curl / browser, only the default
        # fails. Identifying honestly works, so there is no reason to pretend
        # to be a browser.
        "User-Agent": USER_AGENT,
    })
    _bump()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        # A 403 has two very different causes and guessing wrong sends you
        # hunting the wrong problem — the first version of this line blamed the
        # tier for what was actually a Cloudflare block, which reads as "you
        # need to pay more" when the real fix is a header. Cloudflare's own
        # error codes are in the body, so read them instead of assuming.
        hint = ""
        if e.code == 403:
            hint = (" (Cloudflare edge block, not your tier — check User-Agent)"
                    if "error_code\":1010" in body.replace(" ", "")
                    else " (tier may not include this endpoint)")
        raise UWError(f"HTTP {e.code} on {path}{hint}: {body}", status=e.code) from e
    except urllib.error.URLError as e:
        raise UWError(f"network error on {path}: {e.reason}") from e


def _rows(body: dict) -> list:
    """UW wraps everything in {"data": ...}. Normalize to a list."""
    d = body.get("data", body)
    if isinstance(d, dict):
        return [d]
    return d or []


# ---------------------------------------------------------------------------
# endpoints  (paths verified against the published OpenAPI spec)
# ---------------------------------------------------------------------------
PAGE_SIZE = 500          # UW's hard cap; larger `limit` values are ignored
MAX_PAGES = 60           # 30k contracts — far beyond any single underlying


def option_contracts(ticker: str, max_pages: int = MAX_PAGES, **q) -> list:
    """
    GET /api/stock/{ticker}/option-contracts — the full chain, ALL pages.

    THE PAGINATION IS NOT OPTIONAL. UW caps a page at 500 contracts and simply
    ignores a larger `limit` (asking for 5000 returns 500, with no error and no
    "truncated" flag). SPY's real chain is ~14,000 contracts, so the obvious
    single call returns under 4% of it — and the failure is invisible: you get
    a well-formed chain, GEX computes happily, and every level is wrong because
    most of the open interest was never in the sum.

    Ends only on an EMPTY page. A short page does NOT mean end-of-data: UW
    intermittently returns a partial page mid-walk, and treating that as the end
    truncated SPY's chain from 13,958 contracts to 10,124 (20 full pages plus a
    124-row page) on one run while three identical walks moments later each
    returned the full 13,958. Nothing about the short result distinguishes it
    from a real ending, so the only safe stop condition is a page with no rows
    at all. This cost one extra request and bought back 27% of the open
    interest.

    `max_pages` is a runaway guard, and hitting it is a real anomaly worth
    surfacing rather than absorbing.
    """
    out, seen = [], set()
    for page in range(max_pages):
        params = dict(q)
        params.setdefault("limit", PAGE_SIZE)
        params["page"] = page
        rows = _rows(_get(f"/api/stock/{ticker.upper()}/option-contracts", params))
        if not rows:
            return out
        for c in rows:
            # UW pages by offset; a chain changing underneath a multi-page walk
            # can hand back a contract twice, which would double-count its OI.
            sym = c.get("option_symbol") or c.get("option_chain")
            if sym and sym in seen:
                continue
            if sym:
                seen.add(sym)
            out.append(c)
    raise UWError(
        f"option-contracts still full after {max_pages} pages "
        f"({len(out)} contracts) — refusing to return a possibly truncated chain"
    )


def greek_exposure_by_strike(ticker: str, **q) -> list:
    """GET /api/stock/{ticker}/greek-exposure/strike — UW's per-strike GEX.
    Rows carry call_gex / put_gex / strike (plus delta, charm, vanna)."""
    return _rows(_get(f"/api/stock/{ticker.upper()}/greek-exposure/strike", q))


def gex_levels(ticker: str, **q) -> dict:
    """GET /api/stock/{ticker}/gex-levels — UW's own call_wall / put_wall /
    gamma_flip / gamma_magnet.

    Worth cross-checking ours against: UW defines these exactly the way
    analysis/gex.py now does — call wall is the largest positive net gamma
    strike ABOVE spot, put wall the largest negative BELOW, and the flip is the
    zero-gamma crossing nearest to spot. A large disagreement means one of the
    two is wrong, and that is worth knowing before you trade off either."""
    rows = _rows(_get(f"/api/stock/{ticker.upper()}/gex-levels", q))
    return rows[0] if rows else {}


def spot_exposures_by_strike(ticker: str, **q) -> list:
    """GET /api/stock/{ticker}/spot-exposures/strike — spot-based GEX."""
    return _rows(_get(f"/api/stock/{ticker.upper()}/spot-exposures/strike", q))


def flow_alerts(ticker: str = None, **q) -> list:
    """Unusual options flow. Per-ticker or market-wide.
    GET /api/stock/{ticker}/flow-alerts  |  GET /api/option-trades/flow-alerts"""
    path = (f"/api/stock/{ticker.upper()}/flow-alerts" if ticker
            else "/api/option-trades/flow-alerts")
    return _rows(_get(path, q))


def darkpool(ticker: str = None, **q) -> list:
    """GET /api/darkpool/{ticker}  |  GET /api/darkpool/recent"""
    path = f"/api/darkpool/{ticker.upper()}" if ticker else "/api/darkpool/recent"
    return _rows(_get(path, q))


def ohlc(ticker: str, candle_size: str = "1d", **q) -> list:
    """GET /api/stock/{ticker}/ohlc/{candle_size}"""
    return _rows(_get(f"/api/stock/{ticker.upper()}/ohlc/{candle_size}", q))


def stock_state(ticker: str) -> dict:
    """GET /api/stock/{ticker}/stock-state — current quote / session state."""
    rows = _rows(_get(f"/api/stock/{ticker.upper()}/stock-state"))
    return rows[0] if rows else {}


# ---------------------------------------------------------------------------
# translation into the dashboard's contract
# ---------------------------------------------------------------------------
def chain_to_expiries(contracts: list, max_dte: int, today: dt.date = None) -> list:
    """
    Reshape UW option contracts into the per-expiry structure compute_gex()
    consumes: [{label, dte, calls:[{strike,oi,iv,t_years,oi_change}], puts:[...]}]

    Contract identity comes from `option_symbol` (OCC format, e.g.
    SPY260724C00600000) when UW doesn't hand back parsed fields — the strike and
    right are encoded in the last 15 characters of that symbol.
    """
    today = today or dt.date.today()
    by_expiry = {}
    for c in contracts:
        sym = c.get("option_symbol") or c.get("option_chain") or ""
        strike, right, expiry = _parse_occ(sym)
        # prefer explicit fields when present; fall back to the parsed symbol
        strike = _f(c, "strike", strike) or strike
        right = (c.get("option_type") or right or "").lower()
        expiry = c.get("expiry") or expiry
        if not (strike and right and expiry):
            continue

        oi = _i(c, "open_interest")
        iv = _f(c, "implied_volatility")
        if oi <= 0 or iv <= 0:
            continue

        try:
            exp_date = dt.date.fromisoformat(str(expiry)[:10])
        except ValueError:
            continue
        dte = (exp_date - today).days
        if dte < 0 or dte > max_dte:
            continue

        row = {
            "strike": strike,
            "oi": oi,
            "iv": iv,
            "t_years": max(dte, 0.5) / 365.0,
            # THE REASON TO BE HERE: yfinance has no day-over-day OI, so the
            # OI Shift signal in bias_engine.py has been reading a hardcoded 0
            # on live data. UW carries the previous session's OI.
            "oi_change": oi - _i(c, "prev_oi", oi),
        }
        e = by_expiry.setdefault(expiry, {"label": str(expiry), "dte": dte,
                                          "calls": [], "puts": []})
        (e["calls"] if right.startswith("c") else e["puts"]).append(row)

    return [by_expiry[k] for k in sorted(by_expiry)]


def _parse_occ(symbol: str):
    """
    Parse an OCC option symbol -> (strike, right, expiry_iso).

    Layout is <root><yymmdd><C|P><strike * 1000, 8 digits>, so the trailing 15
    characters are fixed-width regardless of how long the root is.
    """
    if not symbol or len(symbol) < 16:
        return 0.0, "", ""
    tail = symbol[-15:]
    try:
        yy, mm, dd = int(tail[0:2]), int(tail[2:4]), int(tail[4:6])
        right = tail[6].lower()
        strike = int(tail[7:15]) / 1000.0
        if right not in ("c", "p"):
            return 0.0, "", ""
        return strike, right, f"20{yy:02d}-{mm:02d}-{dd:02d}"
    except (ValueError, IndexError):
        return 0.0, "", ""


# ---------------------------------------------------------------------------
# session shape  --  what Yahoo used to supply
# ---------------------------------------------------------------------------
# UW segments every candle by session: market_time is "pr" (pre), "r" (regular)
# or "po" (post). That is strictly better than inferring the boundary from a
# timestamp the way the Yahoo path had to, because the boundary is the
# provider's own rather than our guess about exchange hours on a half day.
MT_PRE, MT_REGULAR, MT_POST = "pr", "r", "po"


def prior_session(ticker: str, today: dt.date = None) -> dict:
    """
    The last COMPLETED regular session: {date, high, low, close}.

    "Completed" is the whole difficulty. Indexing a fixed offset into the daily
    bars silently shifts the reference day by one during pre-market — exactly
    when this tool is meant to be used — because whether today already has a
    bar depends on what time you ask. So the session is selected by date
    comparison, never by position.

    Only `market_time == "r"` rows are considered. The pre and post rows for a
    date carry their own high/low, and mixing them in would report an overnight
    spike as part of the regular range.
    """
    today = today or dt.date.today()
    rows = ohlc(ticker, candle_size="1d", limit=30)
    regular = []
    for r in rows:
        if r.get("market_time") != MT_REGULAR:
            continue
        try:
            d = dt.date.fromisoformat(str(r.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        if d < today:                       # strictly before today = completed
            regular.append((d, r))
    if not regular:
        raise UWError(f"no completed regular session found for {ticker}")
    d, r = max(regular, key=lambda x: x[0])
    return {"date": d, "high": _f(r, "high"), "low": _f(r, "low"),
            "close": _f(r, "close")}


def overnight_range(ticker: str, prior: dict, tz, now_et: dt.datetime = None) -> tuple:
    """
    True overnight (Globex) range: prior regular close through now.

    Returns (high, low, is_real), matching what SMT consumes.

    The window is the point. A full prior session is NOT an overnight; feeding
    that into SMT compares yesterday's day range against yesterday's day range
    and manufactures agreement. The overnight that matters for the next open
    starts at the prior close and runs to this moment, so it spans post-market,
    the globex session and this morning's pre-market, plus today's regular
    session so far once it opens.

    `is_real` is False when no overnight session exists yet (weekends, or
    before the first post-close print), in which case the prior session's range
    stands in. Never widen that fallback: reporting a multi-day range as
    "overnight" invents a breakout that never happened and hands SMT a fake
    divergence.

    Zero-volume candles are dropped. The Yahoo path had to do this because its
    extended-hours bars carried phantom prints with nonsense lows; whether UW
    does the same is unverified, so the guard stays. It costs nothing and the
    failure it prevents is a 5% error in the overnight low.
    """
    fallback = (prior["high"], prior["low"], False)
    try:
        bars = ohlc(ticker, candle_size="5m", limit=500)
    except UWError:
        return fallback
    if not bars:
        return fallback

    anchor = None                 # end of the prior regular session, in ET
    parsed = []
    for b in bars:
        raw = b.get("start_time") or b.get("end_time")
        if not raw:
            continue
        try:
            t = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(tz)
        except ValueError:
            continue
        if _i(b, "volume") <= 0:                      # phantom print
            continue
        parsed.append((t, b))
        # The prior session's close is the newest regular bar dated on the
        # prior session's date. Anchoring on "the newest regular bar" alone
        # would anchor to TODAY once the open happens, collapsing the window.
        if b.get("market_time") == MT_REGULAR and t.date() == prior["date"]:
            if anchor is None or t > anchor:
                anchor = t
    if anchor is None or not parsed:
        return fallback

    on = [b for t, b in parsed if t > anchor]
    if not on:
        return fallback
    highs = [_f(b, "high") for b in on]
    lows = [_f(b, "low") for b in on if _f(b, "low") > 0]
    if not highs or not lows:
        return fallback
    return max(highs), min(lows), True


def headlines(limit: int = 40, ticker: str = None) -> list:
    """GET /api/news/headlines. Fields: headline, source, created_at,
    sentiment, is_major, tickers, tags."""
    q = {"limit": limit}
    if ticker:
        q["ticker"] = ticker.upper()
    return _rows(_get("/api/news/headlines", q))


def levels_to_floats(levels: dict) -> dict:
    """UW's gex-levels come back as decimal strings or null."""
    out = {}
    for k in ("call_wall", "put_wall", "gamma_flip", "gamma_magnet"):
        v = levels.get(k)
        out[k] = float(v) if v not in (None, "") else None
    return out


def summarize_flow(alerts: list, limit: int = 8) -> list:
    """Trim flow alerts to what the dashboard panel shows."""
    out = []
    for a in alerts[:limit]:
        out.append({
            "ticker": a.get("ticker"),
            "type": a.get("type"),
            "strike": _f(a, "strike"),
            "expiry": a.get("expiry"),
            "premium": _f(a, "total_premium"),
            "size": _i(a, "total_size"),
            "volume": _i(a, "volume"),
            "open_interest": _i(a, "open_interest"),
            "vol_oi_ratio": _f(a, "volume_oi_ratio"),
            "has_sweep": bool(a.get("has_sweep")),
            "rule": a.get("alert_rule"),
            "at": a.get("created_at"),
        })
    return out


def summarize_darkpool(prints: list, limit: int = 12) -> list:
    """
    Trim dark pool prints to the fields worth showing, plus NBBO placement.

    The raw rows carry nbbo_bid/nbbo_ask and the earlier version discarded
    them, which threw away the only thing that makes a print readable. A block
    printing at the ask is a buyer lifting; the same size at the bid is a
    seller hitting. Without that, every row is just "someone traded".

    `lean` is a HEURISTIC and labelled as one. Dark pool prints are reported
    without an aggressor flag, so side is inferred from where the print sat in
    the spread — the standard read, and still an inference. Prints exactly at
    the midpoint get "mid" rather than being forced to a side, and a missing or
    crossed NBBO gets None rather than a guess.
    """
    out = []
    for p in prints[:limit]:
        price = _f(p, "price")
        bid, ask = _f(p, "nbbo_bid"), _f(p, "nbbo_ask")
        lean, pos = None, None
        if price > 0 and 0 < bid < ask:
            # 0 = at bid, 1 = at ask.
            pos = (price - bid) / (ask - bid)
            pos = min(max(pos, 0.0), 1.0)
            if pos >= 0.65:
                lean = "buy"
            elif pos <= 0.35:
                lean = "sell"
            else:
                lean = "mid"
        out.append({
            "ticker": p.get("ticker"),
            "price": price,
            "size": _i(p, "size"),
            "premium": _f(p, "premium"),
            "at": p.get("executed_at"),
            "market_center": p.get("market_center"),
            "nbbo_bid": bid or None,
            "nbbo_ask": ask or None,
            "spread_pos": round(pos, 3) if pos is not None else None,
            "lean": lean,
            # A cancelled print is not a trade. Shown rather than filtered, so
            # a tape that is mostly cancellations cannot look like conviction.
            "canceled": bool(p.get("canceled")),
        })
    return out


# ---------------------------------------------------------------------------
# probe — run this the day the key arrives
# ---------------------------------------------------------------------------
CHECKS = [
    ("option-contracts", lambda t: option_contracts(t, exclude_zero_oi_chains=True)),
    ("greek-exposure/strike", lambda t: greek_exposure_by_strike(t)),
    ("gex-levels", lambda t: [gex_levels(t)]),
    ("spot-exposures/strike", lambda t: spot_exposures_by_strike(t)),
    ("flow-alerts", lambda t: flow_alerts(t, limit=3)),
    ("darkpool", lambda t: darkpool(t, limit=3)),
    ("ohlc/1d", lambda t: ohlc(t, "1d", limit=3)),
    ("stock-state", lambda t: [stock_state(t)]),
]


def probe(ticker: str = "SPY") -> dict:
    """
    Hit every endpoint this adapter uses and report what your key can reach.

    This exists because tier coverage is not knowable in advance and the
    published spec documents the full surface, not your subset. Run it before
    trusting any of the above.
    """
    print(f"Probing Unusual Whales with {ticker}...")
    _k = api_key()
    print(f"key: {'set (%d chars)' % len(_k) if _k else 'NOT SET — export UW_API_KEY'}\n")
    results = {}
    for name, fn in CHECKS:
        try:
            rows = fn(ticker)
            n = len(rows)
            sample = sorted(rows[0].keys())[:9] if n and isinstance(rows[0], dict) else []
            results[name] = {"ok": True, "rows": n, "fields": sample}
            print(f"  OK    {name:<24} {n:>5} rows   {', '.join(sample)}")
        except UWError as e:
            results[name] = {"ok": False, "error": str(e), "status": e.status}
            print(f"  FAIL  {name:<24} {e}")
    ok = sum(1 for r in results.values() if r["ok"])
    print(f"\n{ok}/{len(CHECKS)} endpoints reachable.")
    if ok:
        print("Set NYAM_PROVIDER=uw to use them. Anything that FAILed above will "
              "fall back to Yahoo automatically.")
    return results


if __name__ == "__main__":
    import sys
    probe(sys.argv[2] if len(sys.argv) > 2 else "SPY")
