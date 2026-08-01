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

BASE = "https://api.unusualwhales.com"
API_KEY = os.getenv("UW_API_KEY", "")
TIMEOUT = 20


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
    key = key or API_KEY
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
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        # 403 here almost always means "your tier doesn't include this endpoint"
        # rather than a bad key — worth distinguishing in the message.
        hint = " (tier may not include this endpoint)" if e.code == 403 else ""
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
def option_contracts(ticker: str, **q) -> list:
    """GET /api/stock/{ticker}/option-contracts — the full chain."""
    return _rows(_get(f"/api/stock/{ticker.upper()}/option-contracts", q))


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


def summarize_darkpool(prints: list, limit: int = 8) -> list:
    """Trim dark pool prints to the fields worth showing."""
    out = []
    for p in prints[:limit]:
        out.append({
            "ticker": p.get("ticker"),
            "price": _f(p, "price"),
            "size": _i(p, "size"),
            "premium": _f(p, "premium"),
            "at": p.get("executed_at"),
            "market_center": p.get("market_center"),
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
    print(f"key: {'set (%d chars)' % len(API_KEY) if API_KEY else 'NOT SET — export UW_API_KEY'}\n")
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
