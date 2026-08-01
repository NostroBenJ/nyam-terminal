"""
sessions.py  --  Market session clock. Stdlib only.

WHY THIS EARNS SPACE ON A TRADING BOARD
Volatility is not uniform through the day. The London/New York overlap is where
it concentrates, the lunch lull is where edges go to die, and 0DTE gamma pins
hardest into the afternoon. Knowing which of those you are in changes position
size, not just curiosity.

HOLIDAYS ARE COMPUTED, NOT HARDCODED
A pasted list of dates is correct for one year and silently wrong afterwards —
and "silently wrong" here means the clock says OPEN on Thanksgiving. Every US
market holiday except Good Friday is a fixed date or an nth-weekday rule, and
Good Friday follows Easter, which has a closed-form algorithm. All of it is
derived, so it holds for any year without maintenance.
"""
import datetime as dt

import config

# ---------------------------------------------------------------------------
# sessions, in America/New_York
# ---------------------------------------------------------------------------
# (id, label, start, end, kind). Times are ET. `wraps` marks a session that
# crosses midnight.
SESSIONS = [
    ("asia",    "Tokyo",       dt.time(19, 0), dt.time(3, 0),  "fx",     True),
    ("london",  "London",      dt.time(3, 0),  dt.time(11, 30), "fx",    False),
    ("pre",     "Pre-market",  dt.time(4, 0),  dt.time(9, 30),  "us",    False),
    ("regular", "US Regular",  dt.time(9, 30), dt.time(16, 0),  "us",    False),
    ("after",   "After-hours", dt.time(16, 0), dt.time(20, 0),  "us",    False),
]

# Windows worth calling out by name, because each implies a different posture.
WINDOWS = [
    ("overlap", "London / NY overlap", dt.time(9, 30), dt.time(11, 30),
     "Both books live. The highest-volatility stretch of the day."),
    ("lunch", "Lunch lull", dt.time(11, 30), dt.time(13, 30),
     "Volume thins and ranges compress. Breakouts here fail more often."),
    ("power", "Power hour", dt.time(15, 0), dt.time(16, 0),
     "Closing imbalances and 0DTE gamma pin hardest into the bell."),
]


# ---------------------------------------------------------------------------
# holiday calendar — derived
# ---------------------------------------------------------------------------
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """nth weekday of a month. weekday: Monday=0. n=-1 means the last one."""
    if n > 0:
        d = dt.date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + dt.timedelta(days=offset + 7 * (n - 1))
    # last: step back from the first of the next month
    nxt = dt.date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def easter(year: int) -> dt.date:
    """Anonymous Gregorian algorithm. Good Friday is Easter minus two days."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def _observed(d: dt.date) -> dt.date:
    """NYSE rule: a Saturday holiday is taken Friday, a Sunday one Monday."""
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def holidays(year: int) -> dict:
    """{date: name} of full US equity market closures for `year`."""
    out = {
        _observed(dt.date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        easter(year) - dt.timedelta(days=2): "Good Friday",
        _nth_weekday(year, 5, 0, -1): "Memorial Day",
        _observed(dt.date(year, 6, 19)): "Juneteenth",
        _observed(dt.date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        _observed(dt.date(year, 12, 25)): "Christmas Day",
    }
    return out


def early_closes(year: int) -> dict:
    """{date: name} of 13:00 ET closes."""
    out = {}
    # Day after Thanksgiving.
    out[_nth_weekday(year, 11, 3, 4) + dt.timedelta(days=1)] = "Day after Thanksgiving"
    # Christmas Eve and July 3rd, but only when they are real trading days —
    # in years where either IS the observed holiday, an early close is wrong.
    hols = holidays(year)
    for d, name in ((dt.date(year, 12, 24), "Christmas Eve"),
                    (dt.date(year, 7, 3), "Independence Day eve")):
        if d.weekday() < 5 and d not in hols:
            out[d] = name
    return out


def day_status(d: dt.date) -> dict:
    """Is `d` a trading day, and if not, why not."""
    if d.weekday() >= 5:
        return {"open": False, "reason": "Weekend", "early_close": None}
    h = holidays(d.year)
    if d in h:
        return {"open": False, "reason": h[d], "early_close": None}
    ec = early_closes(d.year)
    return {"open": True, "reason": None,
            "early_close": ec.get(d) and "13:00", "early_close_name": ec.get(d)}


# ---------------------------------------------------------------------------
# current state
# ---------------------------------------------------------------------------
def _in_window(now: dt.time, start: dt.time, end: dt.time, wraps: bool) -> bool:
    if wraps:
        return now >= start or now < end
    return start <= now < end


def _minutes_until(now: dt.datetime, target: dt.time) -> int:
    t = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if t <= now:
        t += dt.timedelta(days=1)
    return int((t - now).total_seconds() // 60)


def state(now: dt.datetime = None) -> dict:
    """
    Full clock state. `now` is injectable so this is testable at any instant
    rather than only at whatever time the suite happens to run.
    """
    now = now or dt.datetime.now(config.TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=config.TZ)
    now = now.astimezone(config.TZ)
    today = now.date()
    t = now.time()

    day = day_status(today)

    active = []
    for sid, label, start, end, kind, wraps in SESSIONS:
        on = _in_window(t, start, end, wraps)
        # US sessions don't run on a closed day; FX sessions are context and
        # keep their own clock, but are marked so they can be shown dimmed.
        if kind == "us" and not day["open"]:
            on = False
        active.append({
            "id": sid, "label": label, "kind": kind, "active": on,
            "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M"),
        })

    windows = []
    for wid, label, start, end, note in WINDOWS:
        on = day["open"] and _in_window(t, start, end, False)
        windows.append({"id": wid, "label": label, "active": on, "note": note,
                        "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")})

    # The window the user actually trades, straight from config so the clock and
    # the grading rule can never drift apart.
    exit_h, exit_m = map(int, config.GRADE_EXIT_TIME.split(":"))
    open_h, open_m = map(int, config.MARKET_OPEN.split(":"))
    my_window = _in_window(t, dt.time(open_h, open_m), dt.time(exit_h, exit_m), False)

    close_t = dt.time(13, 0) if day.get("early_close") else dt.time(16, 0)
    if day["open"] and _in_window(t, dt.time(open_h, open_m), close_t, False):
        phase, until, until_label = "open", _minutes_until(now, close_t), "close"
    elif day["open"] and t < dt.time(open_h, open_m):
        phase, until, until_label = "pre", _minutes_until(now, dt.time(open_h, open_m)), "open"
    else:
        phase, until, until_label = "closed", None, None

    return {
        "now_et": now.strftime("%H:%M:%S"),
        "date": today.isoformat(),
        "weekday": now.strftime("%A"),
        "trading_day": day["open"],
        "closed_reason": day["reason"],
        "early_close": day.get("early_close"),
        "early_close_name": day.get("early_close_name"),
        "phase": phase,
        "minutes_until": until,
        "until_label": until_label,
        "sessions": active,
        "windows": windows,
        "my_window": {
            "active": my_window and day["open"],
            "start": f"{open_h:02d}:{open_m:02d}",
            "end": f"{exit_h:02d}:{exit_m:02d}",
            "note": "Your traded window — the bias is graded over exactly this span.",
        },
    }
