"""
server.py  --  The engine's HTTP face. Headless: it serves JSON and nothing else.

The Tauri shell spawns this as a sidecar on launch and kills it on exit. The
frontend talks only to http://127.0.0.1:<port>, so the engine stays independently
runnable and independently testable:

    cd engine && python server.py            # then curl http://127.0.0.1:8765/api/health

WHY THIS IS NOT app.py FROM nyam_bias
That one also served templates/ and static/ — it *was* the UI. Here the UI is a
Tauri + React frontend that ships separately, so this file loses the HTML routes
and gains the two things a sidecar needs: a health endpoint the shell can poll
for readiness, and CORS for the Vite dev server.

WHY BARE IMPORTS (`import config`, not `from engine import config`)
Twelve modules downstream use them, including the ones verify_gex.py covers.
Rewriting all of that to package-relative imports would churn verified math for
no functional gain, so engine/ stays its own root and is run from inside itself.
"""
import argparse
import datetime as dt
import json
import os
import sys
import threading

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import chat as chat_mod
import config
from pipeline import build_snapshot
from logging_obsidian import log_to_obsidian
from analysis import changes, tracker, trigger
import store as store_mod
from analysis import sessions
from claude_brief import generate_brief_meta
from data import calendar_feed, flow, news_feed, snapshot_store
from data.data_sources import get_ohlc, get_bars

DEFAULT_PORT = 8765
STARTED_AT = dt.datetime.now(dt.timezone.utc)

app = FastAPI(title="NYAM Terminal engine", docs_url="/api/docs")

# The dev server and the packaged app are DIFFERENT ORIGINS, and getting this
# list wrong fails in the worst way: dev works perfectly while the packaged app
# shows "engine unreachable", because a blocked fetch and a dead engine look
# identical from the frontend.
#
# Origins that must be allowed:
#   http://localhost:1420    Vite dev server
#   http://tauri.localhost   packaged app on WINDOWS  <- this one was missing
#   tauri://localhost        packaged app on macOS/Linux
#
# Localhost-only binding, not CORS, is what actually keeps this off the network.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^(https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?"
        r"|tauri://localhost)$"
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Cache the latest snapshot PER TICKER so every consumer shares one source of
# truth. Keyed by ticker because switching the selector must not hand you
# another symbol's stale GEX while the new pull is still in flight.
_latest = {}                                   # ticker -> snapshot
_running = {"on": True}                        # live-feed toggle
_active = {"ticker": config.PRIMARY_TICKER}    # what the scheduler refreshes
_lock = threading.Lock()
_brief_jobs = {}                               # ticker -> brief thread in flight

# News caching lives in news_feed so the snapshot pipeline shares one cache
# with this HTTP layer rather than each pulling the same 12 feeds.
NEWS_TTL = 180                                 # seconds


def refresh(ticker: str = None, with_brief: bool = False) -> dict:
    """
    Rebuild a ticker's board and publish it.

    The brief is generated AFTER publishing, on a worker thread, because it
    costs 13.9s of a 19.6s cold build and nothing else depends on it. The board
    is complete and correct the moment this returns; the prose arrives a few
    seconds later and is patched into the cached snapshot in place.

    `with_brief=True` restores the blocking path for callers that write a file
    once and want the finished text in it — the scheduled log and the recorder.
    """
    ticker = (ticker or _active["ticker"]).upper()
    snap = build_snapshot(ticker, with_brief=with_brief)
    tracker.record_prediction(snap)            # saves once per day per ticker
    with _lock:
        _latest[ticker] = snap
    snapshot_store.save(ticker, snap)
    if not with_brief:
        _start_brief(ticker, snap)
    return snap


def _start_brief(ticker: str, snap: dict):
    """
    Generate the brief off the critical path and patch it into the cache.

    Patches the CACHED snapshot rather than the one already returned to a
    caller: whoever triggered this refresh has their copy and is not waiting.
    The next poll picks up the prose.

    Guarded so two refreshes of the same ticker cannot both call Claude — that
    would be paid work done twice for one result, and the budget limiter in
    claude_brief counts calls, not answers.
    """
    with _lock:
        if _brief_jobs.get(ticker):
            return
        _brief_jobs[ticker] = True

    def _work():
        try:
            meta = generate_brief_meta(
                snap["bias"], snap["gex"], snap["levels"], snap["smt"],
                snap["news"], ticker)
            # COPY-ON-WRITE, not in-place mutation.
            #
            # Readers take the lock, read the reference, release, and then hand
            # the dict to FastAPI to serialise — so the lock is not held at the
            # moment that matters. Patching keys into that same object meant a
            # response could be serialised BETWEEN the two assignments below and
            # go out with the prose present but brief_meta still saying
            # "pending". Building a new dict and swapping the reference means a
            # reader either sees the old snapshot or the new one, entirely.
            #
            # (A crash from mutating during json.dumps was the suspicion that
            # started this; it did not reproduce across ~8,000 concurrent
            # serialisations, because CPython's C encoder holds the GIL for the
            # whole encode. The torn-read above is the real defect, and it is
            # cheap to remove either way.)
            to_save = None
            with _lock:
                cur = _latest.get(ticker)
                # Only patch if the cached snapshot is still the one this brief
                # was written about. A refresh that landed meanwhile has newer
                # levels, and prose describing the old ones would read as
                # current commentary on numbers that have moved.
                if cur is not None and cur.get("generated_at") == snap.get("generated_at"):
                    patched = dict(cur)
                    patched["brief"] = meta["text"]
                    patched["brief_meta"] = {k: v for k, v in meta.items()
                                             if k != "text"}
                    _latest[ticker] = patched
                    to_save = patched
            # Disk I/O OUTSIDE the lock. snapshot_store.save fsyncs, and holding
            # the global lock across an fsync stalls every API request for its
            # duration — on a board that polls every 60s and is read on demand.
            if to_save is not None:
                snapshot_store.save(ticker, to_save)
        except Exception as e:                       # noqa: BLE001
            with _lock:
                cur = _latest.get(ticker)
                if cur is not None:
                    # Same copy-on-write rule as the success path.
                    _latest[ticker] = dict(
                        cur, brief_meta=dict(cur.get("brief_meta") or {},
                                             source="error", error=str(e)))
            print(f"[warn] brief failed for {ticker}: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
        finally:
            with _lock:
                _brief_jobs.pop(ticker, None)

    threading.Thread(target=_work, name=f"brief-{ticker}", daemon=True).start()


def scheduled_refresh():
    if not _running["on"]:
        return
    # AUTOMATIC work stands down near the cap; anything you ask for by hand
    # still goes through. Spending the last of the day's budget on a refresh
    # nobody asked for is how the board ends up broken at 15:30 rather than
    # merely stale — and stale-and-labelled is a state this app handles well.
    if config.PROVIDER == "uw":
        try:
            from data import unusual_whales as uw
            if uw.budget_exhausted():
                b = uw.budget()
                print(f"[{dt.datetime.now(config.TZ)}] auto-refresh paused — "
                      f"{b['remaining']} UW requests left of {b['limit']}; "
                      f"manual refresh still available", flush=True)
                return
        except Exception:                             # noqa: BLE001
            pass          # never let the guard itself stop a refresh
    refresh()


def scheduled_log():
    # Writes a file once and wants the finished prose in it, so this one waits.
    snap = refresh(with_brief=True)
    path = log_to_obsidian(snap)
    print(f"[{dt.datetime.now(config.TZ)}] Logged NYAM bias -> {path}", flush=True)


# ---------------------------------------------------------------------------
# lifecycle — what the Tauri shell polls
# ---------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    """Readiness probe for the sidecar.

    `warm` distinguishes "process is up" from "a snapshot exists". The shell
    can show a window as soon as the former is true, but the UI must not render
    a bias panel off an empty cache and let it read as a real one.
    """
    with _lock:
        warm = sorted(_latest.keys())
    return {
        "ok": True,
        "warm": warm,
        "pid": os.getpid(),
        "started_at": STARTED_AT.isoformat(),
        "provider": "mock" if config.USE_MOCK_DATA else config.PROVIDER,
        "uw_key_set": bool(config.UW_API_KEY),
        "anthropic_key_set": bool(config.ANTHROPIC_API_KEY),
        "uw_budget": _uw_budget(),
        "uw_socket": _uw_socket_status(),
    }


def _uw_socket_status():
    """
    WebSocket state, so the board can say which path it is actually on.

    Matters because the two paths have different freshness: a live socket
    pushes updates continuously, while REST is as fresh as the last poll. A UI
    that cannot tell them apart would let a silently-dead socket read as live
    data, which is the same class of bug as the fetch-age chip.
    """
    try:
        from data import uw_socket
        return uw_socket.status()
    except Exception as e:  # noqa: BLE001
        return {"state": "error", "error": f"{type(e).__name__}: {e}"}


def _uw_budget():
    """
    Today's UW request usage, or None when UW isn't the provider.

    Surfaced because the daily cap (30,000) is comfortable at one ticker on a
    five-minute refresh (~2,000/day) and stops being comfortable quickly if
    either of those changes. A limit you only find out about by hitting it is
    one you hit during a session, so the number belongs on screen.
    """
    if config.USE_MOCK_DATA or config.PROVIDER != "uw":
        return None
    try:
        from data import unusual_whales as uw
        return uw.budget()
    except Exception:
        return None


@app.get("/api/paths")
def api_paths():
    """
    Where the engine is reading config and writing data.

    Exists because frozen builds resolve these differently and get it wrong
    silently: a `.env` that is never found looks identical to a missing key,
    and a STORE_DIR inside the app bundle looks identical to a working one
    until a reinstall wipes the OI history, which cannot be re-fetched.
    Reports NO secret values — only paths and whether they resolved.
    """
    store = config.STORE_DIR
    writable = False
    try:
        os.makedirs(store, exist_ok=True)
        probe = os.path.join(store, ".write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        writable = True
    except OSError:
        pass
    dotenv = os.path.join(config.BASE_DIR, ".env")
    return {
        "frozen": bool(getattr(sys, "frozen", False)),
        "base_dir": config.BASE_DIR,
        "exe_dir": os.path.dirname(sys.executable),
        "store_dir": store,
        "store_writable": writable,
        "dotenv_path": dotenv,
        "dotenv_found": os.path.exists(dotenv),
        "vault": config.OBSIDIAN_VAULT,
    }


@app.get("/api/status")
def api_status():
    return {"running": _running["on"], "ticker": _active["ticker"]}


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------
@app.get("/api/tickers")
def api_tickers():
    return {"tickers": config.TICKERS, "active": _active["ticker"]}


@app.get("/api/bias")
def api_bias(ticker: str = None):
    t = (ticker or _active["ticker"]).upper()
    with _lock:
        data = _latest.get(t)
    if data is None:
        data = refresh(t)
    return JSONResponse(data)


@app.get("/api/bars")
def api_bars(ticker: str = None, interval: str = "5m", days: int = 5):
    """
    OHLC for the price chart.

    Deliberately NOT part of the /api/bias snapshot. Bars refresh on a different
    cadence than a positioning snapshot and are far larger; bundling them would
    force the whole board to re-render on every candle and make one slow feed
    block every panel.
    """
    t = (ticker or _active["ticker"]).upper()
    return JSONResponse(get_bars(t, interval=interval, lookback_days=days))


@app.get("/api/news")
def api_news(ticker: str = None, sort: str = "relevance", limit: int = 60,
             refresh: bool = False):
    """
    Aggregated financial news.

    Cached for NEWS_TTL. Eleven upstream feeds is a real request every time,
    and re-pulling all of them because someone switched tabs is both slow and
    rude to servers that are giving us data for free. `refresh=true` forces it.
    """
    t = (ticker or _active["ticker"]).upper()
    return JSONResponse(news_feed.fetch_news_cached(
        t, limit=limit, sort=sort, ttl=NEWS_TTL, force=refresh))


@app.get("/api/capture")
def api_capture(limit: int = 30):
    """
    What the headless recorder has stored, and which trading days it missed.

    Gaps matter more than totals here: an option chain not captured on a given
    day is not retrievable later at any price, so a missing weekday is a
    permanent hole rather than a cosmetic one.
    """
    import capture as capture_mod
    return JSONResponse(capture_mod.status(limit=limit))


@app.get("/api/journal")
def api_journal(ticker: str = None, limit: int = 120):
    """
    Every recorded call for a ticker, newest first, with the reasoning that
    produced it. Reads the store directly — no market data, so it stays fast
    and works when a feed is down.
    """
    t = (ticker or _active["ticker"]).upper()
    return JSONResponse({
        "ticker": t,
        "records": tracker.journal(t, limit=limit),
        "stats": tracker.compute_stats(store_mod.load(), ticker=t),
    })


@app.post("/api/journal/note")
async def api_journal_note(request: Request):
    """Attach your own note to one call. Grading never touches it."""
    body = await request.json()
    t = (body.get("ticker") or _active["ticker"]).upper()
    date = (body.get("date") or "").strip()
    note = body.get("note") or ""
    if not date:
        return JSONResponse({"error": "date required"}, status_code=400)
    ok = tracker.set_note(t, date, note)
    if not ok:
        return JSONResponse({"error": f"no record for {t} on {date}"}, status_code=404)
    return {"ok": True, "ticker": t, "date": date}


@app.get("/api/sessions")
def api_sessions():
    """Market session clock. Cheap and pure — no cache, no upstream."""
    return sessions.state()


@app.get("/api/flow")
def api_flow(ticker: str = None, limit: int = 100):
    """
    Options order flow.

    Reads spot from the cached snapshot rather than re-pulling the chain: the
    scanner refreshes far more often than positioning does, and a flow request
    should never trigger a chain fetch.
    """
    t = (ticker or _active["ticker"]).upper()
    with _lock:
        snap = _latest.get(t)
    spot = (snap or {}).get("gex", {}).get("spot")
    if spot is None:
        spot = refresh(t)["gex"]["spot"]
    return JSONResponse(flow.get_flow(t, spot, limit=limit))


@app.get("/api/calendar")
def api_calendar(refresh: bool = False):
    """
    The week AHEAD — scheduled macro releases and upcoming earnings.

    Distinct from /api/news, which reports what already published. "CPI lands
    Wednesday 08:30" and "CPI landed an hour ago" call for opposite trades.
    Cached inside calendar_feed because Forex Factory rate-limits.
    """
    return JSONResponse(calendar_feed.week_ahead(force=refresh))


@app.get("/api/news/sources")
def api_news_sources():
    """The feed registry, so the UI can show what it's actually watching."""
    return {"feeds": news_feed.FEEDS, "half_life_hours": news_feed.HALF_LIFE_H}


@app.post("/api/ticker")
def api_ticker(ticker: str):
    """Switch the active underlying, and point the scheduler at it too so the
    auto-refresh follows whatever is actually on screen."""
    t = ticker.upper()
    if t not in config.TICKERS:
        config.TICKERS.append(t)               # allow ad-hoc symbols
    _active["ticker"] = t
    return JSONResponse(refresh(t))


@app.post("/api/refresh")
def api_refresh(ticker: str = None):
    return JSONResponse(refresh(ticker))


@app.post("/api/start")
def api_start(ticker: str = None):
    _running["on"] = True
    return JSONResponse(refresh(ticker))


@app.post("/api/stop")
def api_stop():
    _running["on"] = False
    return {"running": False}


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------
@app.get("/api/chat/status")
def api_chat_status():
    """Lets the UI explain why chat is off instead of failing on first send."""
    return {"enabled": bool(config.ANTHROPIC_API_KEY), "model": config.CLAUDE_MODEL}


@app.post("/api/chat")
async def api_chat(request: Request):
    """Stream a reply grounded in the current snapshot.

    Plain text chunks rather than SSE — the frontend appends them as they land
    and there is no event framing to parse. The API key never leaves this side.
    """
    body = await request.json()
    ticker = (body.get("ticker") or _active["ticker"]).upper()
    user_msg = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not user_msg:
        return JSONResponse({"error": "empty message"}, status_code=400)

    with _lock:
        snap = _latest.get(ticker)
    if snap is None:
        snap = refresh(ticker)

    def gen():
        try:
            for chunk in chat_mod.stream_reply(history, user_msg, snap):
                yield chunk
        except chat_mod.ChatUnavailable as e:
            yield f"[unavailable] {e}"
        except Exception as e:                 # surface, never swallow
            yield f"[chat failed] {type(e).__name__}: {e}"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.get("/api/trigger")
def api_trigger(price: float, dir: str = "long", ticker: str = None):
    """
    What the board says about an entry trigger at `price` going `dir`.

    Reads the live snapshot rather than rebuilding: this is asked at the moment
    a candle closes, so it must answer instantly and must not spend a request.
    """
    t = (ticker or _active["ticker"]).upper()
    with _lock:
        snap = _latest.get(t)
    if snap is None:
        return JSONResponse({"available": False,
                             "note": "No board loaded for this ticker yet."})
    bull = str(dir).lower() in ("long", "buy", "bull", "up", "1", "true")
    return JSONResponse(trigger.evaluate(snap, price, bull))


@app.post("/api/cisd")
async def api_cisd(request: Request):
    """
    Webhook sink for an external entry model (CISD° on TradingView).

    Shares one evaluator with /api/trigger so the manual path and the automated
    path can never drift into two different answers. Nothing here reaches the
    market: it records what fired and returns the board's read of it.

    TradingView cannot reach 127.0.0.1, so this only receives anything behind a
    tunnel. It exists now so that turning that on later is a config change and
    not a feature.
    """
    try:
        body = await request.json()
    except Exception:                                    # noqa: BLE001
        return JSONResponse({"available": False, "note": "Body was not JSON."},
                            status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"available": False, "note": "Expected a JSON object."},
                            status_code=400)

    t = str(body.get("ticker") or _active["ticker"]).upper()
    raw = body.get("price")
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return JSONResponse({"available": False,
                             "note": f"Unusable price: {raw!r}"}, status_code=400)
    bull = str(body.get("dir") or body.get("direction") or "").lower() in (
        "long", "buy", "bull", "up")

    with _lock:
        snap = _latest.get(t)
    if snap is None:
        return JSONResponse({"available": False,
                             "note": f"No board loaded for {t}."})
    out = trigger.evaluate(snap, price, bull)
    out["source"] = str(body.get("source") or "webhook")
    out["grade"] = body.get("grade")
    print(f"[{dt.datetime.now(config.TZ)}] CISD {out.get('direction')} "
          f"{price} -> {out.get('verdict')}: {out.get('headline')}", flush=True)
    return JSONResponse(out)


@app.get("/api/changed")
def api_changed(ticker: str = None):
    """
    What moved since the prior session's capture.

    Reads the live snapshot rather than rebuilding, so this never triggers a
    fetch — it is a comparison of two things that already exist.
    """
    t = (ticker or _active["ticker"]).upper()
    with _lock:
        snap = _latest.get(t)
    if snap is None:
        snap = refresh(t)
    return JSONResponse(changes.build(snap, ticker=t))


@app.post("/api/log")
def api_log():
    scheduled_log()
    return {"status": "logged"}


@app.post("/api/client-error")
async def api_client_error(request: Request):
    """
    Record a frontend crash to disk.

    Exists because a React render error unmounts the tree and leaves an empty
    window painted in the theme background — which reads as "the app is blank"
    and carries no information at all. The packaged build has no devtools and
    no console the user can reach, so without this the only evidence a crash
    ever happened is a dark rectangle.

    Deliberately append-only and never rotated on write: the interesting crash
    is usually the FIRST one, and a log that drops history to stay tidy loses
    exactly the entry worth having. Fails silently rather than raising; an
    error reporter that can itself error is a second bug on top of the first.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    entry = {
        "at": dt.datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "message": str(body.get("message", ""))[:2000],
        "stack": str(body.get("stack", ""))[:6000],
        "component": str(body.get("component", ""))[:4000],
        "section": str(body.get("section", ""))[:80],
        "ticker": str(body.get("ticker", ""))[:20],
        "theme": str(body.get("theme", ""))[:40],
        "ua": str(body.get("ua", ""))[:400],
    }
    try:
        os.makedirs(config.STORE_DIR, exist_ok=True)
        path = os.path.join(config.STORE_DIR, "client_errors.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    return {"status": "recorded"}


@app.get("/api/client-error")
def api_client_error_list(limit: int = 20):
    """The most recent frontend crashes, newest first. For the Sources panel."""
    path = os.path.join(config.STORE_DIR, "client_errors.log")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        return {"errors": [], "path": path}
    return {"errors": list(reversed(out))[:limit], "path": path,
            "total": len(out)}


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------
def start_scheduler():
    """Refresh through the pre-market window, log before the open, grade after
    the close. Weekdays only.

    The cron trigger is deliberate: an interval job anchored to a start_date
    earlier the same day just steps forward to now, so it polls around the
    clock including weekends instead of covering the intended window.
    """
    sched = BackgroundScheduler(timezone=str(config.TZ))
    start_h = int(config.PREMARKET_START.split(":")[0])
    # Runs to the CLOSE. `hour=f"{start_h}-{open_h}"` stopped firing at 09:59,
    # so the two hours actually traded had no auto-refresh; extending it to the
    # grading window then stopped it at 12:59, which was the same bug three
    # hours later. See config.SESSION_REFRESH_UNTIL.
    #
    # NOT cheap, and the comment that used to sit here claiming otherwise was
    # describing the Yahoo path. CHAIN_REFRESH_SECONDS guards _live_ticker's
    # cache; _uw_market has no chain cache at all and walks the full paged
    # chain on every cycle. Measured: 35 UW requests per refresh, ~22 of them
    # that walk. Roughly 21k of the 30k daily budget for one ticker across
    # 07:00-16:59 — which is why budget_exhausted() exists.
    end_h = int(config.SESSION_REFRESH_UNTIL.split(":")[0])
    every = max(config.REFRESH_SECONDS, 30)
    sched.add_job(scheduled_refresh, "cron", day_of_week="mon-fri",
                  hour=f"{start_h}-{end_h}",
                  second=f"*/{every}" if every < 60 else "0",
                  minute="*" if every < 60 else f"*/{max(1, every // 60)}")

    log_h, log_m = map(int, config.SNAPSHOT_AND_LOG_AT.split(":"))
    sched.add_job(scheduled_log, "cron", day_of_week="mon-fri", hour=log_h, minute=log_m)

    grade_h, grade_m = map(int, config.GRADE_TIME.split(":"))
    sched.add_job(lambda: tracker.grade_pending(get_ohlc), "cron",
                  day_of_week="mon-fri", hour=grade_h, minute=grade_m)
    sched.start()
    return sched


def main():
    ap = argparse.ArgumentParser(description="NYAM Terminal engine")
    ap.add_argument("--port", type=int,
                    default=int(os.getenv("NYAM_ENGINE_PORT", DEFAULT_PORT)))
    ap.add_argument("--no-scheduler", action="store_true",
                    help="skip the cron jobs (useful when running tests)")
    ap.add_argument("--no-warm", action="store_true",
                    help="skip the startup snapshot so the port opens immediately")
    # CAPTURE AS A MODE OF THE FROZEN ENGINE, not a separate `python capture.py`.
    #
    # Task Scheduler ran `python capture.py` against the source repo and every
    # run failed with 0x80070002 (FILE_NOT_FOUND): a bare `python` is not
    # resolved the way an interactive shell resolves it, so nothing was ever
    # captured. Worse, the working directory was the source tree, so even a
    # successful run would have written to the repo's data_store while the
    # installed app reads its own — the Journal would have shown an empty
    # recorder and the days would still have been gone.
    #
    # Running it through this executable removes both faults at once: the path
    # is the app's own binary, and STORE_DIR resolves next to it.
    ap.add_argument("--capture", action="store_true",
                    help="run the headless capture once and exit (no server)")
    ap.add_argument("--tickers", default=None,
                    help="with --capture: comma-separated; defaults to PRIMARY_TICKER")
    ap.add_argument("--grade-only", action="store_true",
                    help="with --capture: only grade yesterday's calls")
    ap.add_argument("--force", action="store_true",
                    help="with --capture: run on a non-trading day too")
    ap.add_argument("--status", action="store_true",
                    help="with --capture: print what has been captured and exit")
    args = ap.parse_args()

    if args.capture:
        import capture
        if args.status:
            raise SystemExit(capture.print_status())
        raise SystemExit(capture.run_cli(
            tickers=args.tickers, grade_only=args.grade_only, force=args.force))

    tracker.ensure_seeded()
    tracker.grade_pending(get_ohlc)            # grade any past, ungraded calls

    # Restore the last saved boards BEFORE binding, so /api/bias answers on the
    # first request instead of blocking a cold build. Each is flagged
    # `restored` and keeps its original generated_at, so the UI shows it as
    # what it is: the last board, being replaced right now.
    restored = snapshot_store.load_all(config.TICKERS)
    if restored:
        with _lock:
            _latest.update(restored)
        print(f"restored {len(restored)} saved board(s): "
              f"{', '.join(sorted(restored))}", flush=True)

    if not args.no_warm:
        # Warm the cache IN THE BACKGROUND so the port opens immediately.
        #
        # This used to run inline. Once the brief started calling Claude, that
        # put a network round-trip in front of the socket bind — cold start went
        # from ~0.6s to several seconds, and during that window the shell and
        # any health probe see a dead port, which is indistinguishable from a
        # crashed engine. Nothing that can block on a remote service belongs
        # before the listener.
        #
        # A failure here must not stop the server binding either; the frontend
        # already handles an empty cache by fetching on demand.
        def _warm():
            try:
                refresh()
            except Exception as e:
                print(f"[warn] startup refresh failed: {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)

        threading.Thread(target=_warm, name="warm", daemon=True).start()

    if not args.no_scheduler:
        start_scheduler()

    mode = "mock" if config.USE_MOCK_DATA else config.PROVIDER
    print(f"NYAM Terminal engine on http://127.0.0.1:{args.port}  (provider={mode})",
          flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
