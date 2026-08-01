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
from analysis import tracker
from data import news_feed
from data.data_sources import get_ohlc, get_bars

DEFAULT_PORT = 8765
STARTED_AT = dt.datetime.now(dt.timezone.utc)

app = FastAPI(title="NYAM Terminal engine", docs_url="/api/docs")

# The Vite dev server runs on a different origin than the packaged app, where
# the frontend is served from tauri:// and same-origin doesn't apply either.
# Localhost-only binding is what actually keeps this closed to the network.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|tauri://localhost)$",
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

# News caching lives in news_feed so the snapshot pipeline shares one cache
# with this HTTP layer rather than each pulling the same 12 feeds.
NEWS_TTL = 180                                 # seconds


def refresh(ticker: str = None) -> dict:
    ticker = (ticker or _active["ticker"]).upper()
    snap = build_snapshot(ticker)
    tracker.record_prediction(snap)            # saves once per day per ticker
    with _lock:
        _latest[ticker] = snap
    return snap


def scheduled_refresh():
    if _running["on"]:
        refresh()


def scheduled_log():
    snap = refresh()
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


@app.post("/api/log")
def api_log():
    scheduled_log()
    return {"status": "logged"}


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
    open_h = int(config.MARKET_OPEN.split(":")[0])
    every = max(config.REFRESH_SECONDS, 30)
    sched.add_job(scheduled_refresh, "cron", day_of_week="mon-fri",
                  hour=f"{start_h}-{open_h}",
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
    args = ap.parse_args()

    tracker.ensure_seeded()
    tracker.grade_pending(get_ohlc)            # grade any past, ungraded calls

    if not args.no_warm:
        # Warm the cache so the first panel render is instant. A failure here
        # must not stop the server from binding — the shell would read a dead
        # port as a crashed engine, when the real fault is one bad data pull.
        try:
            refresh()
        except Exception as e:
            print(f"[warn] startup refresh failed: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)

    if not args.no_scheduler:
        start_scheduler()

    mode = "mock" if config.USE_MOCK_DATA else config.PROVIDER
    print(f"NYAM Terminal engine on http://127.0.0.1:{args.port}  (provider={mode})",
          flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
