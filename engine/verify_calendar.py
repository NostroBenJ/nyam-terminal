"""
verify_calendar.py  --  Checks on the week-ahead calendar.

This module had no suite at all, which is how a `__main__` probe that raises
KeyError on its own payload survived, and how the headline filter came to
compare ISO strings sliced to each other's length.

NO NETWORK. Forex Factory rate-limits hard — probing it by hand during this
audit returned HTTP 429 within a handful of requests — so every fetch here is
stubbed. A verification suite that can get you banned from your own data source
is not one you will run.

    python verify_calendar.py
"""
import datetime as dt
import json
import sys

import config
from data import calendar_feed as cf

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def at(days_ahead: float, hour=8, minute=30) -> str:
    """An ISO timestamp relative to now, in exchange time."""
    d = dt.datetime.now(config.TZ) + dt.timedelta(days=days_ahead)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def row(title, when, country="USD", impact="High", forecast="", previous=""):
    return {"title": title, "country": country, "date": when, "impact": impact,
            "forecast": forecast, "previous": previous, "url": ""}


def stub(rows, err=None):
    """Replace the network with a fixed payload."""
    cf._fetch_week = lambda url: ([], err) if err else (list(rows), None)


def reset_cache():
    cf._cache["at"], cf._cache["data"] = 0.0, None


_real_fetch = cf._fetch_week
_real_earn = cf.earnings_week
cf.earnings_week = lambda tickers=None: ([], {})     # yfinance stays out of this


def main():
    print("[1] rows become events, in exchange time")
    reset_cache()
    stub([row("CPI m/m", at(1), forecast="0.3%", previous="0.2%"),
          row("Unemployment Rate", at(2), impact="Medium")])
    w = cf.week_ahead(force=True)
    check("both events kept", w["counts"]["econ"] == 2, str(w["counts"]))
    ev = w["days"][0]["events"][0]
    check("forecast carried", ev["forecast"] == "0.3%", ev["forecast"])
    check("High ranks 3", ev["rank"] == 3, str(ev["rank"]))
    med = [e for d in w["days"] for e in d["events"] if e["impact"] == "Medium"][0]
    check("Medium ranks 2", med["rank"] == 2, str(med["rank"]))

    print("[2] only the countries that move US index options")
    reset_cache()
    stub([row("CPI m/m", at(1)),
          row("JPY Building Consents", at(1), country="JPY"),
          row("OPEC Meeting", at(2), country="All")])
    w = cf.week_ahead(force=True)
    titles = {e["title"] for d in w["days"] for e in d["events"]}
    check("USD kept", "CPI m/m" in titles)
    check("All kept", "OPEC Meeting" in titles)
    check("JPY dropped", "JPY Building Consents" not in titles, str(titles))

    print("[3] the headline is the next HIGH-impact event still ahead")
    reset_cache()
    stub([row("Already Happened", at(-1)),               # past, high
          row("Minor Thing", at(0.5), impact="Low"),     # soon, low
          row("Next Big One", at(1)),                    # the answer
          row("Later Big One", at(3))])
    w = cf.week_ahead(force=True)
    check("picks the nearest future high-impact",
          w["headline"] and w["headline"]["title"] == "Next Big One",
          str(w["headline"] and w["headline"]["title"]))
    check("not spent — something is ahead", w["spent"] is False, str(w["spent"]))

    print("[4] a week entirely in the past is SPENT, not broken")
    # The normal weekend state: Forex Factory's file does not roll over until
    # the week turns and there is no next-week feed (that path 404s), so from
    # Friday evening onward every event is behind us. Without this flag the
    # panel is indistinguishable from a failed fetch — during the exact
    # session in which Monday gets planned.
    reset_cache()
    stub([row("Monday CPI", at(-5)), row("Friday NFP", at(-1))])
    w = cf.week_ahead(force=True)
    check("no headline", w["headline"] is None, str(w["headline"]))
    check("spent is True", w["spent"] is True, str(w["spent"]))
    check("but the events are still listed", w["counts"]["econ"] == 2,
          str(w["counts"]["econ"]))
    check("and no error is claimed", not w["errors"], str(w["errors"]))

    print("[5] an empty calendar is not 'spent'")
    reset_cache()
    stub([])
    w = cf.week_ahead(force=True)
    check("spent False when there is nothing at all", w["spent"] is False,
          str(w["spent"]))

    print("[6] the cache serves, and a failure never poisons it")
    reset_cache()
    stub([row("CPI m/m", at(1))])
    first = cf.week_ahead(force=True)
    check("first call is uncached", first["cached"] is False, str(first["cached"]))
    second = cf.week_ahead()
    check("second call is served from cache", second["cached"] is True,
          str(second["cached"]))
    check("and is not marked stale", second["stale"] is False, str(second["stale"]))

    # Now the source starts failing. The last good week must survive.
    stub([], err="HTTP 429")
    third = cf.week_ahead(force=True)
    check("a 429 serves the last good calendar", third["counts"]["econ"] == 1,
          str(third["counts"]["econ"]))
    check("flagged stale", third["stale"] is True, str(third["stale"]))
    check("and says why", "429" in json.dumps(third["errors"]), str(third["errors"]))

    print("[7] a failure with NO cache yields an empty week, not a crash")
    reset_cache()
    stub([], err="HTTP 429")
    w = cf.week_ahead(force=True)
    check("no days", w["days"] == [], str(w["days"])[:40])
    check("error surfaced", "forex_factory" in w["errors"], str(w["errors"]))
    check("stale flagged", w["stale"] is True)
    # The killer: an empty failed week must never become the cached answer,
    # or the failure pins itself in place for the whole TTL.
    stub([row("CPI m/m", at(1))])
    w = cf.week_ahead()
    check("the failure was NOT cached", w["counts"]["econ"] == 1,
          str(w["counts"]["econ"]))

    print("[8] the payload has the keys its own __main__ prints")
    # This is why the manual probe raised KeyError: 'earnings' — counts never
    # had that key. The probe is the documented way to test this module.
    reset_cache()
    stub([row("CPI m/m", at(1))])
    w = cf.week_ahead(force=True)
    for k in ("econ", "earnings_this_week", "earnings_upcoming", "total"):
        check(f"counts.{k} exists", k in w["counts"], str(list(w["counts"])))
    for k in ("days", "headline", "high_impact", "upcoming_earnings", "counts",
              "errors", "spent", "fetched_at", "source"):
        check(f"payload.{k} exists", k in w, str(sorted(w)))

    print("[9] timestamps parse to exchange time, and junk does not raise")
    check("offset ISO parses",
          cf._parse_dt("2026-08-12T08:30:00-04:00") is not None)
    check("parsed value lands in exchange tz",
          str(cf._parse_dt("2026-08-12T08:30:00-04:00").tzinfo) == str(config.TZ),
          str(cf._parse_dt("2026-08-12T08:30:00-04:00")))
    for junk in (None, "", "not a date", 12345, {}):
        check(f"{junk!r} -> None, no raise", cf._parse_dt(junk) is None)

    print("[10] events are ordered within a day")
    reset_cache()
    stub([row("Late", at(1, hour=14)), row("Early", at(1, hour=8)),
          row("Middle", at(1, hour=10))])
    w = cf.week_ahead(force=True)
    day = [d for d in w["days"] if d["events"]][0]
    check("sorted by time",
          [e["title"] for e in day["events"]] == ["Early", "Middle", "Late"],
          str([e["title"] for e in day["events"]]))

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS)}")
        return 1
    print("all calendar checks passed")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        cf._fetch_week, cf.earnings_week = _real_fetch, _real_earn
        reset_cache()
    sys.exit(code)
