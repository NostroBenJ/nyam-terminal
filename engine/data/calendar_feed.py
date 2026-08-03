"""
calendar_feed.py  --  The week AHEAD: scheduled econ events and earnings.

This is the counterpart to news_feed.py, and the distinction matters. RSS tells
you what already published; this tells you what is COMING. "CPI landed an hour
ago" and "CPI drops Wednesday 08:30" call for opposite trades, and until now the
app could only see the first.

Two sources:
  - Forex Factory's weekly calendar (public JSON) for macro releases, with
    impact, forecast and previous.
  - yfinance per-ticker earnings dates for the names on your selector.

Stdlib for the fetch; yfinance is imported inside the function that needs it so
the pricing core stays dependency-free.
"""
import datetime as dt
import json
import urllib.error
import urllib.request

import config

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
USER_AGENT = "NYAM-Terminal/0.1 (personal research tool)"
TIMEOUT = 10

#: Countries whose releases actually move US index options. Forex Factory
#: carries every economy; a SPY trader reading JPY building consents is noise.
#: "All" covers cross-market events like OPEC meetings.
DEFAULT_COUNTRIES = ("USD", "All")

IMPACT_RANK = {"High": 3, "Medium": 2, "Low": 1, "Holiday": 0}


# Forex Factory RATE-LIMITS. Hitting it a handful of times in a minute during
# development returned HTTP 429. This is a WEEKLY calendar — the data changes
# on the order of days, so refetching it often buys nothing and risks a ban.
# On failure the last good result is served and flagged stale, because a
# calendar that blanks on a 429 is worse than one that is an hour old.
CACHE_TTL = 1800
_cache: dict = {"at": 0.0, "data": None}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _parse_dt(raw: str) -> dt.datetime | None:
    """FF emits ISO with offset, e.g. 2026-08-02T05:15:00-04:00."""
    try:
        d = dt.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=config.TZ)
    return d.astimezone(config.TZ)


def econ_week(countries=DEFAULT_COUNTRIES) -> tuple[list, str | None]:
    """
    Scheduled macro events for the current week, in ET.

    Returns (events, error). An error returns an empty list rather than raising
    — a missing calendar should grey out a panel, not take down the board.
    """
    try:
        raw = _get(FF_URL)
    except urllib.error.HTTPError as e:
        return [], f"HTTP {e.code}"
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    try:
        rows = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return [], f"malformed JSON: {e}"

    out = []
    for r in rows:
        country = (r.get("country") or "").strip()
        if countries and country not in countries:
            continue
        when = _parse_dt(r.get("date"))
        if when is None:
            continue
        impact = (r.get("impact") or "").strip() or "Low"
        out.append({
            "kind": "econ",
            "title": (r.get("title") or "").strip(),
            "country": country,
            "impact": impact,
            "rank": IMPACT_RANK.get(impact, 1),
            "at": when.isoformat(),
            "date": when.date().isoformat(),
            "time": when.strftime("%H:%M"),
            "forecast": (r.get("forecast") or "").strip(),
            "previous": (r.get("previous") or "").strip(),
            "url": (r.get("url") or "").strip(),
        })
    out.sort(key=lambda e: e["at"])
    return out, None


#: ETFs have no earnings. Asking Yahoo for their fundamentals returns a 404 per
#: symbol, and yfinance logs that itself before we ever see the exception — so
#: the fix is to not ask, rather than to catch it more quietly.
NO_EARNINGS = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "SPX", "NDX", "RUT",
               "XLF", "XLE", "XLK", "SMH", "ARKK", "TLT", "GLD", "SLV", "USO"}

#: Five weeks, not one. Verified against live data on 2026-08-02: NVDA reports
#: Aug 26, TSLA Oct 21, AAPL Oct 29 — a 14-day window returned nothing at all
#: for every name on the selector, because Q2 season had just ended. A calendar
#: that is empty most of the year is a calendar you stop opening.
EARNINGS_HORIZON_DAYS = 35


def earnings_week(tickers=None, horizon_days: int = EARNINGS_HORIZON_DAYS) -> tuple[list, dict]:
    """
    Earnings dates for the selector's single names in the next `horizon_days`.

    Per-ticker because there is no free market-wide earnings feed worth
    trusting. Errors are collected per ticker rather than failing the batch —
    one delisted symbol should not blank the calendar.
    """
    tickers = [t for t in (tickers or config.TICKERS) if t.upper() not in NO_EARNINGS]
    errors: dict = {}
    out: list = []
    try:
        import yfinance as yf
    except ImportError as e:
        return [], {"_import": str(e)}

    # yfinance logs HTTP failures itself; we report them through `errors`.
    import logging
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    today = dt.date.today()
    horizon = today + dt.timedelta(days=horizon_days)
    for sym in tickers:
        try:
            cal = yf.Ticker(sym).calendar or {}
            dates = cal.get("Earnings Date") or []
            if isinstance(dates, (dt.date, dt.datetime)):
                dates = [dates]
            for d in dates:
                day = d.date() if isinstance(d, dt.datetime) else d
                if not isinstance(day, dt.date):
                    continue
                if today <= day <= horizon:
                    eps = cal.get("Earnings Average")
                    out.append({
                        "kind": "earnings",
                        "title": f"{sym} earnings",
                        "ticker": sym,
                        "impact": "High",
                        "rank": 3,
                        "date": day.isoformat(),
                        "at": day.isoformat(),
                        "time": "",
                        "country": "USD",
                        # Consensus EPS, so the row says something beyond "a
                        # date exists" — this is the number the print is
                        # measured against.
                        "forecast": f"EPS est {eps:.2f}" if isinstance(eps, (int, float)) else "",
                        "previous": "",
                        "days_out": (day - today).days,
                        "url": "",
                    })
        except Exception as e:                       # per-ticker, never fatal
            errors[sym] = f"{type(e).__name__}: {e}"
    out.sort(key=lambda e: (e["date"], e["title"]))
    return out, errors


def week_ahead(tickers=None, countries=DEFAULT_COUNTRIES,
               include_earnings: bool = True, force: bool = False) -> dict:
    """
    Everything scheduled this week, grouped by day.

    `headline` is the single event most worth knowing about from now onward —
    the highest-impact one still in the future, which is what a pre-market read
    actually needs.

    Cached for CACHE_TTL. On a fetch failure the last good payload is returned
    with `stale: True` rather than an empty week.
    """
    import time as _t

    now_s = _t.time()
    if not force and _cache["data"] and (now_s - _cache["at"]) < CACHE_TTL:
        return {**_cache["data"], "cached": True,
                "age_s": int(now_s - _cache["at"]), "stale": False}

    econ, econ_err = econ_week(countries)

    # Rate-limited or down, and we have something from before: serve it rather
    # than blanking the panel, but say plainly that it is old.
    if econ_err and _cache["data"]:
        return {**_cache["data"], "cached": True, "stale": True,
                "age_s": int(now_s - _cache["at"]),
                "errors": {**_cache["data"].get("errors", {}),
                           "forex_factory": f"{econ_err} — showing last good calendar"}}
    earn, earn_errs = ([], {})
    if include_earnings:
        earn, earn_errs = earnings_week(tickers)

    now = dt.datetime.now(config.TZ)
    today = now.date().isoformat()
    # The week grid covers this week only; earnings can sit weeks out, so they
    # get their own list rather than stretching the grid to a month of mostly
    # empty days.
    week_end = (now.date() + dt.timedelta(days=7)).isoformat()
    earn_this_week = [e for e in earn if e["date"] <= week_end]
    events = econ + earn_this_week

    by_day: dict = {}
    for e in events:
        by_day.setdefault(e["date"], []).append(e)
    for day in by_day.values():
        day.sort(key=lambda e: (e.get("time") or "", -e["rank"]))

    days = [{"date": d,
             "weekday": dt.date.fromisoformat(d).strftime("%a"),
             "is_today": d == today,
             "is_past": d < today,
             "events": by_day[d]}
            for d in sorted(by_day)]

    # The next high-impact event still ahead of us.
    upcoming = [e for e in events
                if e["rank"] >= 3 and (e.get("at") or e["date"]) >= now.isoformat()[:len(e.get("at") or e["date"])]]
    upcoming.sort(key=lambda e: (e.get("at") or e["date"]))
    headline = upcoming[0] if upcoming else None

    errors = {}
    if econ_err:
        errors["forex_factory"] = econ_err
    errors.update({f"earnings:{k}": v for k, v in earn_errs.items()})

    payload = {
        "days": days,
        "headline": headline,
        "high_impact": [e for e in events if e["rank"] >= 3],
        # Every upcoming earnings date on the selector, including ones past this
        # week — NVDA at 24 days out is exactly the thing worth planning around.
        "upcoming_earnings": sorted(earn, key=lambda e: e["date"]),
        "counts": {"econ": len(econ), "earnings_this_week": len(earn_this_week),
                   "earnings_upcoming": len(earn), "total": len(events)},
        "errors": errors,
        "fetched_at": int(now.timestamp()),
        "source": "Forex Factory + Yahoo earnings",
    }
    # Only cache a payload that actually got the calendar; caching an empty
    # week after a 429 would pin the failure in place for the whole TTL.
    if not econ_err:
        _cache["at"], _cache["data"] = now_s, payload
    return {**payload, "cached": False, "stale": bool(econ_err), "age_s": 0}


if __name__ == "__main__":                            # manual probe
    w = week_ahead()
    print(f"{w['counts']['total']} events ({w['counts']['econ']} econ, "
          f"{w['counts']['earnings']} earnings)")
    if w["errors"]:
        print("errors:", w["errors"])
    if w["headline"]:
        h = w["headline"]
        print(f"next high-impact: {h['date']} {h['time']} {h['title']}")
    for d in w["days"]:
        mark = " <- today" if d["is_today"] else ""
        print(f"\n{d['weekday']} {d['date']}{mark}")
        for e in d["events"]:
            star = "***" if e["rank"] >= 3 else "   "
            fc = f"  fc={e['forecast']}" if e["forecast"] else ""
            print(f"  {star} {e['time']:>5}  {e['title'][:52]:<52}{fc}")
