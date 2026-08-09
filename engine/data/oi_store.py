"""
oi_store.py  --  Daily open-interest snapshots, so day-over-day OI change works
on the free data path.

WHY
`oi_change` is the one input Yahoo cannot supply: yfinance returns a snapshot of
today's chain with no previous session to diff against, so `_live_expiries`
hardcodes `oi_change: 0` and the bias engine's OI Shift signal reads a flat zero
forever. That is a signal rendering as "no positioning change" when the truth is
"we never looked".

This fixes it without a paid feed by storing one snapshot per session and
diffing against the most recent EARLIER one. History cannot be bought
retroactively — every day this doesn't run is a day of OI change that is gone
for good — so it writes from the first live pull onward.

STORAGE
One JSON file per ticker per date under data_store/oi/, small and inspectable:
    {"date": "2026-08-01", "ticker": "SPY", "captured_at": "...",
     "chains": {"2026-08-07": {"C": {"745.0": 12345}, "P": {...}}}}

A day's file is written ONCE (first pull of the session wins) rather than
overwritten all morning. Diffing a 15:59 snapshot against a 09:15 one would
report intraday drift as overnight positioning change, which is a different
thing entirely and would quietly corrupt the signal.

ONE FILE PER PUBLICATION, NOT PER CALENDAR DAY
OI is settled data. OCC republishes it once a morning for the prior session and
it does not move intraday or over a weekend, so two captures inside the same
window hold identical numbers. Storing both, then diffing them, yields zero at
every strike — which reads as "positioning didn't move" when the truth is "no
new OI has published yet". Non-trading days are refused, and so is any snapshot
whose numbers match the previous one. `apply_oi_change` makes the same check at
read time and returns `available: False` rather than a false flat.
"""
import datetime as dt
import json
import os
import tempfile
import time

import config
from analysis import sessions

DIR = os.path.join(config.STORE_DIR, "oi")

# Below this many matched strikes, "nothing moved" is not evidence of a stale
# publication — a two-strike test chain can legitimately be unchanged. SPY runs
# 800-1,100 strikes a session, so the threshold never binds in practice.
STALE_MIN_STRIKES = 25

# os.replace contention window. Generous on purpose: the cost of waiting is a
# few hundred milliseconds once a session, and the cost of giving up early is
# an exception thrown inside the market build.
REPLACE_ATTEMPTS = 8
REPLACE_BACKOFF = 0.02


def _path(ticker: str, date_iso: str) -> str:
    return os.path.join(DIR, f"{ticker.upper()}_{date_iso}.json")


def _today() -> str:
    return config.today().isoformat()      # exchange day, like everything else


def snapshot(ticker: str, expiries: list, date_iso: str = None) -> str | None:
    """
    Persist today's OI, once. Returns the path written, or None if today's file
    already exists (first pull of the session is the one that counts).
    """
    date_iso = date_iso or _today()
    os.makedirs(DIR, exist_ok=True)
    path = _path(ticker, date_iso)
    # "Already written" has to mean "already written READABLY". A zero-byte or
    # truncated file — exactly what the old non-atomic write left behind if the
    # process died mid-flight — passes os.path.exists forever, so this function
    # would decline to write today's OI for the rest of time and previous()
    # would keep skipping that date. One interrupted write would poison the
    # date permanently, and OI for a past session cannot be re-fetched.
    if os.path.exists(path) and _read(path) is not None:
        return None

    # One file per OI PUBLICATION, not per calendar day.
    #
    # OI is settled data: OCC republishes it once each morning for the prior
    # session, and it does not move intraday or over a weekend. Two captures
    # inside the same publication window therefore hold identical numbers, and
    # diffing one against the other yields zero at every strike — which the
    # panel renders as "positioning didn't move" when the truth is "no new OI
    # has published yet". That is the same false-flat this module exists to
    # prevent, arriving through a different door.
    #
    # Measured on the real store: Friday 21:31 -> Saturday 10:36 moved 0 of 895
    # strikes, while Monday 20:06 -> Tuesday 17:04 moved 686 of 845. The
    # weekend file is a duplicate; the weekday one is a session of real change.
    if not sessions.day_status(dt.date.fromisoformat(date_iso))["open"]:
        return None

    chains = {}
    for e in expiries:
        label = e.get("label")
        if not label:
            continue
        chains[label] = {
            "C": {str(c["strike"]): c.get("oi", 0) for c in e.get("calls", [])},
            "P": {str(p["strike"]): p.get("oi", 0) for p in e.get("puts", [])},
        }

    # The calendar catches weekends and holidays. This catches the rest: a
    # capture that runs before OCC publishes, an unscheduled run, a half day
    # the calendar disagrees about. If the numbers are identical to the last
    # snapshot then no new data exists, whatever the date says.
    prior = previous(ticker, date_iso)
    if prior is not None and prior.get("chains") == chains:
        return None
    payload = {
        "date": date_iso,
        "ticker": ticker.upper(),
        "captured_at": dt.datetime.now(config.TZ).isoformat(),
        "chains": chains,
    }
    # A UNIQUE temp name, not path + ".tmp".
    #
    # Two processes write this concurrently now: the engine on every market
    # build, and the headless recorder at 09:20 — which is precisely when the
    # app is most likely to be open and refreshing. Sharing one predictable
    # temp path means both can be writing the same file, and whichever calls
    # os.replace first installs whatever bytes happen to be there. The
    # exists() check above narrows the window but does not close it: both can
    # pass it before either writes.
    #
    # mkstemp gives each writer its own file, so the worst case becomes a
    # harmless duplicate write of identical data rather than an atomically
    # installed corrupt snapshot. OI cannot be re-fetched for a past date.
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=DIR, prefix=f".{ticker.upper()}_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        # Windows raises ACCESS_DENIED from os.replace when another writer is
        # swapping the same target at that instant. Losing that race is the
        # NORMAL outcome here ("first pull of the session wins"), so it must
        # not surface as an exception inside the market build.
        #
        # The check has to be retried, not made once: the first version of this
        # asked `_read(path) is None` immediately and re-raised, which made the
        # verify suite FLAKY — two passes then a failure. A loser can look
        # while a third writer is mid-swap and see nothing readable, even
        # though a valid file lands a millisecond later. One observation of
        # absence is not evidence of absence under a race.
        last = None
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)   # atomic; a half-written file is worse than none
                tmp = None
                return path
            except OSError as exc:
                last = exc
                if _read(path) is not None:
                    return None         # someone else got there; benign
                time.sleep(REPLACE_BACKOFF * (attempt + 1))
        if _read(path) is not None:
            return None
        raise last
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _read(path: str) -> dict | None:
    """A snapshot, or None if the file is unreadable or not snapshot-shaped."""
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    # Valid JSON is not the same as a valid snapshot. A list here would reach
    # apply_oi_change and raise AttributeError on .get, taking the whole market
    # build down over one bad file.
    if not isinstance(payload, dict) or not isinstance(payload.get("chains"), dict):
        return None
    return payload


def previous(ticker: str, before_date: str = None) -> dict | None:
    """
    The most recent USABLE snapshot strictly before `before_date`.

    Walks backwards rather than taking only the newest. If yesterday's file is
    corrupt, a two-day diff against Wednesday is far better than reporting "no
    baseline yet" — the baseline_date travels with the result, so the UI says
    which day it actually compared to and the reader can judge it. Silently
    downgrading real history to "we never looked" is the failure mode this
    whole module exists to prevent.

    Skipped files are named in `_skipped` so the caller can say so out loud
    instead of the degradation being invisible.
    """
    before_date = before_date or _today()
    if not os.path.isdir(DIR):
        return None
    prefix = f"{ticker.upper()}_"
    dates = sorted(
        f[len(prefix):-len(".json")]
        for f in os.listdir(DIR)
        if f.startswith(prefix) and f.endswith(".json")
    )
    skipped = []
    for date_iso in reversed([d for d in dates if d < before_date]):
        payload = _read(_path(ticker, date_iso))
        if payload is not None:
            if skipped:
                payload = dict(payload, _skipped=skipped)
            return payload
        skipped.append(date_iso)
    return None


def apply_oi_change(ticker: str, expiries: list, date_iso: str = None) -> dict:
    """
    Fill `oi_change` on each contract by diffing against the previous session.

    Mutates `expiries` in place and returns a report the UI can show, because
    "no OI change data yet" and "OI didn't change" must not look the same on
    screen. On the first ever run there is no baseline and every change stays
    0 — correctly reported as `available: False` rather than as a flat market.

    A strike present today but absent yesterday is a NEW strike: its change is
    its full OI, not zero.
    """
    date_iso = date_iso or _today()
    prev = previous(ticker, date_iso)
    if not prev:
        # Set the field explicitly rather than leaving it absent. Downstream,
        # gex.py reads `.get("oi_change", 0)`, so a missing key and a real zero
        # are indistinguishable by the time they reach the bias engine — the
        # `available` flag is the ONLY thing that separates "no baseline yet"
        # from "positioning didn't move", and the UI must show which.
        for e in expiries:
            for c in list(e.get("calls", [])) + list(e.get("puts", [])):
                c["oi_change"] = 0
        return {"available": False, "baseline_date": None, "matched": 0, "new": 0,
                "note": "No earlier snapshot yet — OI change is unknown, not zero. "
                        "It becomes available the next session this runs."}

    chains = prev.get("chains", {})
    matched = new = moved = 0
    for e in expiries:
        base = chains.get(e.get("label"))
        for right, bucket in (("C", e.get("calls", [])), ("P", e.get("puts", []))):
            table = (base or {}).get(right, {})
            for c in bucket:
                key = str(c["strike"])
                if base is not None and key in table:
                    c["oi_change"] = c.get("oi", 0) - table[key]
                    matched += 1
                    if c["oi_change"]:
                        moved += 1
                else:
                    # Unseen strike: the whole of its OI is new.
                    c["oi_change"] = c.get("oi", 0)
                    new += 1

    # Not one strike moved out of hundreds: this is the same OCC publication
    # read twice, not a still market. Reporting it as a real zero would put
    # "positioning didn't move" on the board on a Monday premarket, when the
    # honest answer is that Friday's OI is the newest that exists.
    #
    # `available: False` is the right channel for that — it is precisely the
    # "we never looked" flag, and "no new publication since the baseline" is a
    # kind of not having looked. A genuinely flat session is conceivable on a
    # thin chain, but not across the hundreds of SPY strikes this runs on.
    if matched >= STALE_MIN_STRIKES and moved == 0:
        # Note this does NOT require new == 0. A strike listed since the
        # baseline gets its full OI booked as "change" by the loop above, and
        # on a stale read that is not a measured change at all — it is the
        # first time we have seen a contract whose OI was set at the same
        # settlement as everything else. Left in, a handful of freshly listed
        # strikes would be the ONLY non-zero numbers in the chain and would
        # carry the entire OI Shift vote by themselves.
        #
        # bias_engine emits that signal at the top weight tier and decides
        # direction by `put_oi_change > call_oi_change`, so it is protected
        # from a missing baseline only because both sides sum to exactly zero.
        # Zeroing here is what keeps that true.
        for e in expiries:
            for c in list(e.get("calls", [])) + list(e.get("puts", [])):
                c["oi_change"] = 0
        return {"available": False, "baseline_date": prev.get("date"),
                "matched": matched, "new": new, "stale": True,
                "note": f"No new OI since {prev.get('date')} — every one of "
                        f"{matched} strikes is unchanged, so this is the same "
                        f"settlement read twice, not flat positioning. OI "
                        f"publishes once per session, after the close."}
    skipped = prev.get("_skipped") or []
    note = (f"OI change vs {prev.get('date')} ({matched} strikes matched, "
            f"{new} new).")
    if skipped:
        # Say it plainly. A diff spanning several sessions is a legitimate
        # number but it is NOT the overnight change the panel usually shows,
        # and the reader has to know which one they are looking at before they
        # size anything off it.
        note += (f" Spans more than one session — {len(skipped)} unreadable "
                 f"snapshot(s) skipped ({', '.join(skipped)}).")
    return {"available": True, "baseline_date": prev.get("date"),
            "matched": matched, "new": new, "degraded": bool(skipped),
            "skipped": skipped, "note": note}
