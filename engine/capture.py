"""
capture.py  --  Headless daily recorder. No GUI, no server, no you.

WHY THIS IS NOT PART OF THE APP
Everything that persists — the OI snapshot, the morning call, the 16:15 grading
— only happened while the desktop app was open. That is fine on a day you sit
in front of it and useless on a day you are at work. None of it backfills: an
option chain you didn't store is not re-fetchable, and a Unusual Whales trial
that expires takes its data with it.

So this runs as a scheduled task instead: one command, does its work, exits.
The app can be closed. You can be elsewhere.

WHAT IT STORES, AND WHY RAW
Both the computed snapshot AND the raw inputs that produced it. Storing only
the snapshot locks you into today's version of the math — with the raw chain on
disk you can recompute GEX six different ways next month against the same real
data. That is the difference between a log and a dataset.

    python capture.py                 # capture now, for the active ticker(s)
    python capture.py --tickers SPY,QQQ
    python capture.py --grade-only     # just grade yesterday's calls
    python capture.py --status         # what has been captured so far
"""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
import traceback

import config

CAPTURE_DIR = os.path.join(config.STORE_DIR, "capture")


def _day_dir(date_iso: str) -> str:
    return os.path.join(CAPTURE_DIR, date_iso)


# os.replace contention window. A capture writes ten files and runs three times
# a day; waiting a few hundred milliseconds is free, and giving up early loses
# a session's chain.
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF = 0.02


def _write(path: str, obj) -> int:
    """
    Atomic write. A half-written capture is worse than a missing one.

    UNIQUE temp name and an fsync, for the reasons oi_store learned the hard
    way: `path + ".tmp"` is one predictable name shared by every writer, and
    two processes past it interleave into the same file — which os.replace
    then installs ATOMICALLY, so the corruption arrives looking valid. The
    three scheduled captures write into the SAME dated folder, and nothing
    stops a manual run overlapping one of them.

    The fsync matters because the rename is only atomic with respect to the
    directory entry; without it the entry can point at bytes still sitting in
    the OS cache. This folder is the one dataset that cannot be re-fetched for
    a past date.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        # Windows raises ACCESS_DENIED from os.replace while another writer is
        # swapping the same target. Unlike oi_store — where losing the race IS
        # the intended outcome, since the first pull of a session wins — here
        # every caller means its content to land, so this RETRIES rather than
        # conceding, and raises if it never gets through.
        last = None
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                tmp = None
                return os.path.getsize(path)
            except OSError as exc:
                last = exc
                time.sleep(_REPLACE_BACKOFF * (attempt + 1))
        raise last
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _log(msg: str) -> None:
    print(f"[{dt.datetime.now(config.TZ):%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Unusual Whales — the trial-expiry problem
# ---------------------------------------------------------------------------
# These endpoints are the reason the recorder exists. Flow and dark pool prints
# are not historical products on the free tier; a week of trial access either
# lands on disk or is gone. Each is captured independently so one 403 (a tier
# that lacks an endpoint) never costs you the others.
UW_CAPTURES = [
    # PER-ENDPOINT LIMITS DIFFER and exceeding one is a hard 422, not a clamp.
    # flow-alerts caps at 200: "Invalid limit 500 - limit must be smaller than
    # 200". Asking for 500 failed every single capture, so the one endpoint
    # whose data genuinely cannot be bought back later was the one being lost.
    # Found only by running the recorder and reading its output; the per-
    # endpoint isolation below meant it failed quietly beside six successes.
    ("flow_alerts", lambda uw, t: uw.flow_alerts(t, limit=199)),
    ("darkpool", lambda uw, t: uw.darkpool(t, limit=500)),
    ("gex_levels", lambda uw, t: [uw.gex_levels(t)]),
    ("greek_exposure_strike", lambda uw, t: uw.greek_exposure_by_strike(t)),
    ("spot_exposures_strike", lambda uw, t: uw.spot_exposures_by_strike(t)),
    ("stock_state", lambda uw, t: [uw.stock_state(t)]),
    ("option_contracts", lambda uw, t: uw.option_contracts(t, exclude_zero_oi_chains=True)),
]


def capture_uw(ticker: str, day: str) -> dict:
    """Snapshot every UW endpoint we can reach. Reports per-endpoint outcome."""
    if not config.UW_API_KEY:
        return {"available": False, "note": "no UW_API_KEY — nothing to capture"}

    from data import unusual_whales as uw

    got, errors, bytes_total = {}, {}, 0
    for name, fn in UW_CAPTURES:
        try:
            rows = fn(uw, ticker) or []
            path = os.path.join(_day_dir(day), f"{ticker}_uw_{name}.json")
            bytes_total += _write(path, rows)
            got[name] = len(rows)
            _log(f"  uw/{name}: {len(rows)} rows")
        except Exception as e:
            # A 403 here means your tier lacks the endpoint, not that the key is
            # wrong. Recorded and moved past — the others are still worth having.
            errors[name] = f"{type(e).__name__}: {e}"
            _log(f"  uw/{name}: FAILED — {e}")
    return {"available": True, "captured": got, "errors": errors,
            "bytes": bytes_total}


# ---------------------------------------------------------------------------
# the capture
# ---------------------------------------------------------------------------
def capture_ticker(ticker: str, day: str) -> dict:
    """One ticker, one day. Safe to run repeatedly — later runs overwrite."""
    out = {"ticker": ticker, "errors": {}, "files": {}}
    _log(f"{ticker}: building snapshot")

    from pipeline import build_snapshot
    from analysis import tracker

    try:
        snap = build_snapshot(ticker)
    except Exception as e:
        out["errors"]["snapshot"] = f"{type(e).__name__}: {e}"
        _log(f"{ticker}: snapshot FAILED — {e}")
        return out

    g = snap["gex"]
    _log(f"{ticker}: spot={g['spot']} regime={g['regime']} flip={g['gamma_flip']} "
         f"bias={snap['bias']['label']}")

    # 1. The computed snapshot — what the board showed.
    p = os.path.join(_day_dir(day), f"{ticker}_snapshot.json")
    out["files"]["snapshot"] = _write(p, snap)

    # 2. The RAW chain — the input, so the math can be redone later. This is the
    #    single most valuable file here: option chains are not retrievable for a
    #    past date on any free source, so an un-captured day is permanently gone.
    try:
        from data.data_sources import get_market
        market = get_market(ticker)
        raw = {
            "captured_at": dt.datetime.now(config.TZ).isoformat(),
            "ticker": ticker,
            "spot": market["primary"]["spot"],
            "session": {k: market["primary"].get(k) for k in
                        ("prior_high", "prior_low", "prior_close",
                         "on_high", "on_low", "on_is_real")},
            "expiries": market["primary"].get("expiries", []),
            "provider": "mock" if config.USE_MOCK_DATA else config.PROVIDER,
        }
        p = os.path.join(_day_dir(day), f"{ticker}_chain.json")
        out["files"]["chain"] = _write(p, raw)
        n = sum(len(e.get("calls", [])) + len(e.get("puts", [])) for e in raw["expiries"])
        _log(f"{ticker}: chain {n} contracts across {len(raw['expiries'])} expiries")
    except Exception as e:
        out["errors"]["chain"] = f"{type(e).__name__}: {e}"
        _log(f"{ticker}: chain FAILED — {e}")

    # 3. Bars, so intraday shape survives Yahoo's rolling ~60-day window.
    try:
        from data.data_sources import get_bars
        bars = get_bars(ticker, interval="5m", lookback_days=5)
        p = os.path.join(_day_dir(day), f"{ticker}_bars_5m.json")
        out["files"]["bars"] = _write(p, bars)
        _log(f"{ticker}: {len(bars.get('bars', []))} 5m bars")
    except Exception as e:
        out["errors"]["bars"] = f"{type(e).__name__}: {e}"

    # 4. Unusual Whales, while the key lasts.
    out["uw"] = capture_uw(ticker, day)

    # 5. File the morning call. record_prediction writes once per day per
    #    ticker, so a midday run never overwrites the 09:20 read.
    try:
        tracker.record_prediction(snap)
        out["recorded_call"] = True
    except Exception as e:
        out["errors"]["record"] = f"{type(e).__name__}: {e}"

    return out


def run(tickers, grade_only: bool = False, force: bool = False) -> dict:
    now = dt.datetime.now(config.TZ)
    day = now.date().isoformat()
    _log(f"capture start — {day} ({now:%A}) provider="
         f"{'mock' if config.USE_MOCK_DATA else config.PROVIDER}")

    from analysis import tracker
    from data.data_sources import get_ohlc

    manifest = {
        "date": day,
        "started_at": now.isoformat(),
        "weekday": now.strftime("%A"),
        "trading_day": None,
        "provider": "mock" if config.USE_MOCK_DATA else config.PROVIDER,
        "uw_key": bool(config.UW_API_KEY),
        "tickers": {},
        "graded": None,
        "errors": {},
    }

    try:
        from analysis import sessions
        st = sessions.day_status(now.date())
        manifest["trading_day"] = st["open"]
        if not st["open"] and not force:
            _log(f"not a trading day ({st['reason']}) — grading only")
            grade_only = True
        elif not st["open"]:
            _log(f"not a trading day ({st['reason']}) — capturing anyway (--force)")
            manifest["forced"] = True
    except Exception as e:
        manifest["errors"]["sessions"] = str(e)

    # Grade first: yesterday's calls are gradeable regardless of today's state,
    # and doing it first means a later failure still leaves grading done.
    try:
        before = sum(1 for r in tracker.journal() if r.get("outcome"))
        tracker.grade_pending(get_ohlc)
        after = sum(1 for r in tracker.journal() if r.get("outcome"))
        manifest["graded"] = after - before
        _log(f"graded {after - before} pending call(s)")
    except Exception as e:
        manifest["errors"]["grading"] = f"{type(e).__name__}: {e}"
        _log(f"grading FAILED — {e}")

    if not grade_only:
        for t in tickers:
            try:
                manifest["tickers"][t] = capture_ticker(t, day)
            except Exception as e:
                manifest["tickers"][t] = {"errors": {"fatal": str(e)}}
                _log(f"{t}: FATAL — {e}")
                traceback.print_exc()

    manifest["finished_at"] = dt.datetime.now(config.TZ).isoformat()
    _write(os.path.join(_day_dir(day), "manifest.json"), manifest)

    total = sum(sum(v.get("files", {}).values()) + (v.get("uw", {}).get("bytes") or 0)
                for v in manifest["tickers"].values() if isinstance(v, dict))
    _log(f"capture done — {len(manifest['tickers'])} ticker(s), "
         f"{total / 1024:.0f} KB written")
    return manifest


def status(limit: int = 30) -> dict:
    """What has been captured, and where the gaps are."""
    days = []
    if os.path.isdir(CAPTURE_DIR):
        for d in sorted(os.listdir(CAPTURE_DIR), reverse=True)[:limit]:
            path = os.path.join(CAPTURE_DIR, d, "manifest.json")
            try:
                with open(path, encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, json.JSONDecodeError):
                m = {"date": d, "errors": {"manifest": "unreadable"}}
            files = [x for x in os.listdir(os.path.join(CAPTURE_DIR, d))
                     if x.endswith(".json")]
            size = sum(os.path.getsize(os.path.join(CAPTURE_DIR, d, x)) for x in files)
            days.append({
                "date": m.get("date", d),
                "weekday": m.get("weekday"),
                "trading_day": m.get("trading_day"),
                "provider": m.get("provider"),
                "uw_key": m.get("uw_key"),
                "tickers": list((m.get("tickers") or {}).keys()),
                "graded": m.get("graded"),
                "files": len(files),
                "bytes": size,
                "errors": m.get("errors") or {},
                "ticker_errors": {t: v.get("errors", {}) for t, v in
                                  (m.get("tickers") or {}).items() if v.get("errors")},
            })

    # Trading days with no capture — but only SINCE recording began.
    #
    # Counting every trading day before the recorder existed reports a fresh
    # install as having "missed 10 days", which is true and useless: it is not
    # a gap you could have prevented, and an alert that fires on day one is an
    # alert you learn to ignore. The meaningful signal is a day you were
    # recording and still missed — the machine was off, or a run failed.
    # THE FAILURE IS REPORTED, NOT SWALLOWED. This used to be `except: pass`,
    # which meant that if day_status or the import raised, `missing` stayed
    # empty and the Journal rendered "no missed days" — a silent FALSE NEGATIVE
    # on the single thing this panel exists to warn about. An option chain
    # cannot be re-fetched for a past date, so a gap reported as no-gap is the
    # most expensive lie the recorder can tell.
    missing = []
    missing_error = None
    try:
        from analysis import sessions
        have = {d["date"] for d in days}
        if have:
            since = dt.date.fromisoformat(min(have))
            # Capture folders are named from ET (see run()), so the audit that
            # compares against them has to use the same clock or it invents a
            # missing day every evening on a non-ET host.
            cur = config.today()
            # Bounded by the FIRST CAPTURE, not by an arbitrary window. The old
            # `range(1, 40)` silently capped the audit at forty days, so after
            # three months of recording the earliest gaps became invisible —
            # the check quietly stopped covering the history it claimed to.
            span = (cur - since).days
            for back in range(1, span + 1):
                d = cur - dt.timedelta(days=back)
                if sessions.day_status(d)["open"] and d.isoformat() not in have:
                    missing.append(d.isoformat())
    except Exception as e:                              # noqa: BLE001
        missing_error = f"{type(e).__name__}: {e}"

    return {"dir": CAPTURE_DIR, "days": days,
            "missing_trading_days": sorted(missing, reverse=True),
            # Non-null means the list above is NOT authoritative. The UI must
            # say "unknown", never "none".
            "missing_error": missing_error,
            "audited_since": min({d["date"] for d in days}) if days else None,
            "total_bytes": sum(d["bytes"] for d in days)}


def main():
    ap = argparse.ArgumentParser(description="NYAM Terminal headless capture")
    ap.add_argument("--tickers", default=None,
                    help="comma-separated; defaults to PRIMARY_TICKER")
    ap.add_argument("--grade-only", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="capture even on a non-trading day (testing, or a "
                         "weekend chain snapshot)")
    args = ap.parse_args()

    if args.status:
        sys.exit(print_status())

    sys.exit(run_cli(tickers=args.tickers, grade_only=args.grade_only,
                     force=args.force))


def print_status() -> int:
    """
    What has been captured, on stdout. Shared by both entry points.

    Prints the MISSING trading days last and unmissably, because that is the
    only line here that cannot be fixed later: an option chain is not
    retrievable for a past date on any feed, so a gap is permanent.
    """
    s = status()
    print(f"capture dir: {s['dir']}")
    print(f"{len(s['days'])} day(s), {s['total_bytes'] / 1024 / 1024:.1f} MB")
    for d in s["days"]:
        errs = " ERRORS" if (d["errors"] or d["ticker_errors"]) else ""
        print(f"  {d['date']} {d['weekday'] or '':<9} "
              f"{','.join(d['tickers']) or '-':<12} {d['files']:>2} files "
              f"{d['bytes'] / 1024:>7.0f} KB{errs}")
    if s["missing_trading_days"]:
        print(f"  MISSING trading days: {', '.join(s['missing_trading_days'])}")
        print("  These cannot be backfilled - no feed sells a past option chain.")
    return 0


def run_cli(tickers: str = None, grade_only: bool = False,
            force: bool = False) -> int:
    """
    One capture run, returning an exit code. Shared by `python capture.py` and
    by `nyam-engine.exe --capture`, so the scheduled job and the manual command
    execute exactly the same path — a scheduler running a different code path
    from the one you tested is how you find out in a month that it never worked.
    """
    names = ([t.strip().upper() for t in tickers.split(",")]
             if tickers else [config.PRIMARY_TICKER])
    m = run(names, grade_only=grade_only, force=force)
    # Non-zero only on a total failure — a single bad endpoint must not make
    # Task Scheduler report the whole job as failed and stop trusting it.
    fatal = any("fatal" in (v.get("errors") or {}) for v in m["tickers"].values())
    return 1 if fatal else 0


if __name__ == "__main__":
    main()
