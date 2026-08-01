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
"""
import datetime as dt
import json
import os

import config

DIR = os.path.join(config.STORE_DIR, "oi")


def _path(ticker: str, date_iso: str) -> str:
    return os.path.join(DIR, f"{ticker.upper()}_{date_iso}.json")


def _today() -> str:
    return dt.datetime.now(config.TZ).date().isoformat()


def snapshot(ticker: str, expiries: list, date_iso: str = None) -> str | None:
    """
    Persist today's OI, once. Returns the path written, or None if today's file
    already exists (first pull of the session is the one that counts).
    """
    date_iso = date_iso or _today()
    os.makedirs(DIR, exist_ok=True)
    path = _path(ticker, date_iso)
    if os.path.exists(path):
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
    payload = {
        "date": date_iso,
        "ticker": ticker.upper(),
        "captured_at": dt.datetime.now(config.TZ).isoformat(),
        "chains": chains,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)          # atomic; a half-written snapshot is worse than none
    return path


def previous(ticker: str, before_date: str = None) -> dict | None:
    """The most recent snapshot STRICTLY BEFORE `before_date`."""
    before_date = before_date or _today()
    if not os.path.isdir(DIR):
        return None
    prefix = f"{ticker.upper()}_"
    dates = sorted(
        f[len(prefix):-len(".json")]
        for f in os.listdir(DIR)
        if f.startswith(prefix) and f.endswith(".json")
    )
    earlier = [d for d in dates if d < before_date]
    if not earlier:
        return None
    try:
        with open(_path(ticker, earlier[-1]), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
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
    matched = new = 0
    for e in expiries:
        base = chains.get(e.get("label"))
        for right, bucket in (("C", e.get("calls", [])), ("P", e.get("puts", []))):
            table = (base or {}).get(right, {})
            for c in bucket:
                key = str(c["strike"])
                if base is not None and key in table:
                    c["oi_change"] = c.get("oi", 0) - table[key]
                    matched += 1
                else:
                    # Unseen strike: the whole of its OI is new.
                    c["oi_change"] = c.get("oi", 0)
                    new += 1
    return {"available": True, "baseline_date": prev.get("date"),
            "matched": matched, "new": new,
            "note": f"OI change vs {prev.get('date')} ({matched} strikes matched, "
                    f"{new} new)."}
