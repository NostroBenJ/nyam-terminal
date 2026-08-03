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
import traceback

import config

CAPTURE_DIR = os.path.join(config.STORE_DIR, "capture")


def _day_dir(date_iso: str) -> str:
    return os.path.join(CAPTURE_DIR, date_iso)


def _write(path: str, obj) -> int:
    """Atomic write. A half-written capture is worse than a missing one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)
    return os.path.getsize(path)


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
    ("flow_alerts", lambda uw, t: uw.flow_alerts(t, limit=500)),
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
    missing = []
    try:
        from analysis import sessions
        have = {d["date"] for d in days}
        if have:
            since = min(have)
            cur = dt.date.today()
            for back in range(1, 40):
                d = cur - dt.timedelta(days=back)
                iso = d.isoformat()
                if iso < since:
                    break
                if sessions.day_status(d)["open"] and iso not in have:
                    missing.append(iso)
    except Exception:
        pass

    return {"dir": CAPTURE_DIR, "days": days, "missing_trading_days": missing,
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
        return

    tickers = ([t.strip().upper() for t in args.tickers.split(",")]
               if args.tickers else [config.PRIMARY_TICKER])
    m = run(tickers, grade_only=args.grade_only, force=args.force)
    # Non-zero only on a total failure — a single bad endpoint must not make
    # Task Scheduler report the whole job as failed and stop trusting it.
    fatal = any("fatal" in (v.get("errors") or {}) for v in m["tickers"].values())
    sys.exit(1 if fatal else 0)


if __name__ == "__main__":
    main()
