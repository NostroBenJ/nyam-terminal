"""
news_feed.py  --  Financial news aggregation. Stdlib only.

WorldMonitor is the inspiration for the shape of this — a dense, categorized,
freshness-tracked rail rather than a chronological dump. It is NOT the source
of this code: WorldMonitor is AGPL-3.0, and vendoring it would reach this whole
application. What is borrowed is the idea (categorized feeds, per-source
freshness, relevance filtering); the feeds below are public RSS/Atom endpoints
anyone may read, and the parser is written here.

Design rules carried over from the rest of the engine:
  - One slow or dead feed must never block the others, or take down the panel.
  - Every failure is RECORDED and surfaced, never silently swallowed. A news
    rail that quietly drops a source looks identical to a quiet news day.
  - Every item carries its source and its age, because "who said this and when"
    is most of what makes a headline actionable.
"""
import concurrent.futures as cf
import datetime as dt
import html
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "NYAM-Terminal/0.1 (personal research tool)"
TIMEOUT = 8

# ---------------------------------------------------------------------------
# THE FEED REGISTRY
# ---------------------------------------------------------------------------
# tier: "primary"  — official / market-moving. Never filtered out.
#       "market"   — financial press.
#       "wire"     — general business coverage; noisier.
#
# Deliberately small and high-signal. 500 feeds is a research tool; a day
# trader watching the open needs the handful that actually move prices.
FEEDS = [
    # --- primary: the actual source of the event ---------------------------
    {"id": "fed-press", "name": "Federal Reserve", "category": "central-bank",
     "tier": "primary", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"id": "fed-monetary", "name": "Fed — Monetary Policy", "category": "central-bank",
     "tier": "primary", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml"},
    {"id": "bls", "name": "Bureau of Labor Statistics", "category": "econ-data",
     "tier": "primary", "url": "https://www.bls.gov/feed/bls_latest.rss"},
    {"id": "sec-press", "name": "SEC", "category": "regulatory",
     "tier": "primary", "url": "https://www.sec.gov/news/pressreleases.rss"},
    {"id": "treasury", "name": "US Treasury", "category": "central-bank",
     "tier": "primary", "url": "https://home.treasury.gov/rss/press.xml"},
    {"id": "ecb", "name": "ECB", "category": "central-bank",
     "tier": "primary", "url": "https://www.ecb.europa.eu/rss/press.html"},

    # --- market press ------------------------------------------------------
    {"id": "cnbc-markets", "name": "CNBC Markets", "category": "markets",
     "tier": "market", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"id": "cnbc-econ", "name": "CNBC Economy", "category": "econ-data",
     "tier": "market", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"id": "mw-realtime", "name": "MarketWatch Realtime", "category": "markets",
     "tier": "market", "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"},
    {"id": "mw-top", "name": "MarketWatch Top", "category": "markets",
     "tier": "market", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"id": "nasdaq-markets", "name": "Nasdaq Markets", "category": "markets",
     "tier": "wire", "url": "https://www.nasdaq.com/feed/rssoutbound?category=Markets"},
]

# Per-ticker headlines. Yahoo serves these without a key.
TICKER_FEED = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
               "?s={ticker}&region=US&lang=en-US")

# For relevance scoring — a headline naming the company but not the symbol
# still matters. Kept short on purpose; a fuzzy match on common words would
# tag half the feed as relevant and make the filter useless.
COMPANY_NAMES = {
    "SPY": ["s&p 500", "s&p500", "spx"],
    "QQQ": ["nasdaq 100", "nasdaq-100"],
    "IWM": ["russell 2000"],
    "NVDA": ["nvidia"],
    "TSLA": ["tesla"],
    "AAPL": ["apple"],
    "AMZN": ["amazon"],
    "META": ["meta platforms", "facebook"],
    "MSFT": ["microsoft"],
    "GOOGL": ["alphabet", "google"],
}

# Words that make an item market-moving regardless of which ticker you hold.
MACRO_TERMS = [
    "fomc", "federal reserve", "fed chair", "powell", "rate cut", "rate hike",
    "interest rate", "inflation", "cpi", "ppi", "pce", "jobs report",
    "nonfarm", "payroll", "unemployment", "gdp", "recession", "tariff",
    "jobless claims", "ism", "treasury yield", "yield curve",
]


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# RSS and Atom name the same concepts differently, and Atom is namespaced.
_NS = {"atom": "http://www.w3.org/2005/Atom",
       "dc": "http://purl.org/dc/elements/1.1/"}


def _clean(text: str | None, limit: int = 400) -> str:
    """Strip markup and collapse whitespace. Feeds embed HTML in summaries."""
    if not text:
        return ""
    t = html.unescape(_TAG_RE.sub(" ", text))
    t = _WS_RE.sub(" ", t).strip()
    return t[:limit]


def _parse_date(raw: str | None) -> int | None:
    """
    Return epoch SECONDS (UTC), or None.

    None is a real answer and is preserved rather than defaulted to now() —
    stamping an undated item with the current time would make it sort as the
    freshest thing on the board, which is exactly backwards.
    """
    if not raw:
        return None
    raw = raw.strip()
    # RFC 822 (RSS) — "Fri, 01 Aug 2026 13:45:00 GMT" and friends.
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M %z"):
        try:
            d = dt.datetime.strptime(raw, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return int(d.timestamp())
        except ValueError:
            pass
    # ISO 8601 (Atom) — "2026-08-01T13:45:00Z".
    try:
        d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return int(d.timestamp())
    except ValueError:
        return None


def parse_feed(xml_text: str, feed: dict) -> list[dict]:
    """
    Parse RSS 2.0 or Atom into a common item shape.

    Both formats appear across these sources and several publishers switch
    without notice, so handling one and hoping is not an option.
    """
    root = ET.fromstring(xml_text)
    items = []

    # RSS: <channel><item>. Atom: <feed><entry>.
    nodes = root.findall(".//item")
    is_atom = False
    if not nodes:
        nodes = root.findall(".//atom:entry", _NS) or root.findall(".//entry")
        is_atom = True

    for n in nodes:
        if is_atom:
            title = n.findtext("atom:title", None, _NS) or n.findtext("title")
            link_el = n.find("atom:link", _NS) if n.find("atom:link", _NS) is not None else n.find("link")
            link = (link_el.get("href") if link_el is not None else None) or n.findtext("link")
            summary = (n.findtext("atom:summary", None, _NS) or n.findtext("summary")
                       or n.findtext("atom:content", None, _NS) or n.findtext("content"))
            raw_date = (n.findtext("atom:updated", None, _NS) or n.findtext("updated")
                        or n.findtext("atom:published", None, _NS) or n.findtext("published"))
        else:
            title = n.findtext("title")
            link = n.findtext("link")
            summary = n.findtext("description")
            raw_date = (n.findtext("pubDate") or n.findtext("dc:date", None, _NS))

        title = _clean(title, 300)
        if not title:
            continue
        items.append({
            "title": title,
            "link": (link or "").strip(),
            "summary": _clean(summary),
            "published": _parse_date(raw_date),
            "source": feed["name"],
            "source_id": feed["id"],
            "category": feed["category"],
            "tier": feed["tier"],
        })
    return items


# ---------------------------------------------------------------------------
# relevance
# ---------------------------------------------------------------------------
def score_item(item: dict, ticker: str | None) -> dict:
    """
    Tag an item with why it might matter. Additive, never subtractive — nothing
    is dropped here, so the UI can filter without the engine having decided
    what you're allowed to see.
    """
    hay = f"{item['title']} {item['summary']}".lower()

    macro = [t for t in MACRO_TERMS if t in hay]
    tick = False
    if ticker:
        t = ticker.upper()
        # Word-boundary match: "SPY" must not fire on "espystatement".
        if re.search(rf"\b{re.escape(t)}\b", hay, re.IGNORECASE):
            tick = True
        else:
            tick = any(name in hay for name in COMPANY_NAMES.get(t, []))

    item["macro_terms"] = macro[:4]
    item["ticker_match"] = tick
    # primary-tier sources are official releases; they are the event itself.
    item["relevance"] = (
        3 if tick else 0) + (2 if macro else 0) + (2 if item["tier"] == "primary" else 0)
    return item


#: Relevance half-life, in hours. At 12h an item keeps half its score, at 24h a
#: quarter. Tuned for a day-trading session rather than a research archive.
HALF_LIFE_H = 12.0


def _rank(item: dict) -> float:
    """
    Relevance decayed by age.

    Ranking on relevance alone floated a 23-day-old Fed enforcement action above
    everything current; ranking on recency alone floated whatever a wire
    published two minutes ago. Neither is what you want at the open. Decay makes
    an old item compete only if it is far more relevant, which is the actual
    trade-off being made.

    Undated items get a fixed mild penalty rather than being treated as new —
    unknown age is not freshness.
    """
    rel = float(item.get("relevance", 0))
    age = item.get("age_hours")
    if age is None:
        return rel * 0.25
    return rel * (0.5 ** (age / HALF_LIFE_H))


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------
def _ssl_context():
    """
    Prefer certifi's CA bundle when it's available.

    Python on Windows does not always carry the intermediate certificates these
    publishers chain through — the ECB feed fails verification against the
    default store. certifi rides along with yfinance, so this costs nothing;
    when it's absent we fall back rather than disabling verification, because
    silently accepting any certificate to make a news panel load is not a
    trade worth making.
    """
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _fetch_one(feed: dict) -> tuple[list[dict], str | None]:
    req = urllib.request.Request(feed["url"], headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        return [], f"HTTP {e.code}"
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    try:
        return parse_feed(raw.decode("utf-8", errors="replace"), feed), None
    except ET.ParseError as e:
        return [], f"malformed XML: {e}"


def fetch_news(ticker: str | None = None, limit: int = 60,
               include_ticker_feed: bool = True, sort: str = "relevance") -> dict:
    """
    Pull every feed concurrently and merge.

    Returns {"items", "sources", "errors", "fetched_at"}. `sources` reports what
    each feed contributed and `errors` why one gave nothing — both are rendered,
    because a rail that silently shrinks from 11 sources to 3 should look
    different from a slow news day.

    `sort` defaults to "relevance", not "latest". Sorting purely by recency
    fills the rail with whatever a wire published most recently — on a quiet
    Saturday that meant retirement-community features outranking everything
    that moves a price. Recency is the tiebreak, and "latest" remains available
    because during a live event the newest line genuinely is the story.
    """
    feeds = list(FEEDS)
    if include_ticker_feed and ticker:
        feeds.append({"id": f"yahoo-{ticker.upper()}",
                      "name": f"Yahoo — {ticker.upper()}",
                      "category": "ticker", "tier": "market",
                      "url": TICKER_FEED.format(ticker=ticker.upper())})

    items, sources, errors = [], {}, {}
    # Threads, not async: urllib is blocking and this is I/O bound. Keeping the
    # engine free of an async stack is worth more than the microseconds.
    with cf.ThreadPoolExecutor(max_workers=min(12, len(feeds))) as pool:
        futures = {pool.submit(_fetch_one, f): f for f in feeds}
        for fut in cf.as_completed(futures):
            f = futures[fut]
            try:
                got, err = fut.result()
            except Exception as e:                       # never let one kill the batch
                got, err = [], f"{type(e).__name__}: {e}"
            sources[f["id"]] = {"name": f["name"], "n": len(got),
                                "category": f["category"], "tier": f["tier"]}
            if err:
                errors[f["id"]] = err
            items.extend(got)

    # Dedupe: the same wire story lands in several feeds. Keep the earliest
    # (whoever carried it first) rather than whichever thread happened to win.
    seen = {}
    for it in items:
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:80]
        prev = seen.get(key)
        if prev is None or (it["published"] or 0) < (prev["published"] or 0):
            seen[key] = it
    merged = [score_item(it, ticker) for it in seen.values()]

    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    for it in merged:
        it["age_hours"] = None if it["published"] is None else \
            max(0.0, (now - it["published"]) / 3600.0)
        it["rank"] = _rank(it)

    # Undated items sort last rather than first — see _parse_date.
    dated = lambda x: (x["published"] is not None, x["published"] or 0)
    if sort == "latest":
        merged.sort(key=dated, reverse=True)
    else:
        merged.sort(key=lambda x: (x["rank"], *dated(x)), reverse=True)

    return {
        "items": merged[:limit],
        "sources": sources,
        "errors": errors,
        "fetched_at": int(dt.datetime.now(dt.timezone.utc).timestamp()),
        "ticker": (ticker or "").upper(),
        "sort": sort,
        "total_before_dedupe": len(items),
    }


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------
# One cache, module-level, shared by the HTTP layer and the snapshot pipeline.
# They both want the same 12 feeds within seconds of each other, and two
# independent caches would double the load on servers giving us this for free.
DEFAULT_TTL = 180
_cache: dict = {}
_cache_lock = __import__("threading").Lock()


def fetch_news_cached(ticker: str | None = None, limit: int = 60,
                      sort: str = "relevance", ttl: int = DEFAULT_TTL,
                      force: bool = False) -> dict:
    """`fetch_news` behind a TTL cache. Adds `cached` so callers can say so."""
    import time as _t

    key = ((ticker or "").upper(), sort, limit)
    now = _t.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and not force and now - hit["_wall"] < ttl:
            return {**hit, "cached": True}

    data = fetch_news(ticker, limit=limit, sort=sort)
    data["_wall"] = now
    with _cache_lock:
        _cache[key] = data
    return {**data, "cached": False}


if __name__ == "__main__":                               # quick manual probe
    import json
    import sys

    out = fetch_news(sys.argv[1] if len(sys.argv) > 1 else "SPY", limit=10)
    print(f"{len(out['items'])} items from {len(out['sources'])} feeds "
          f"({out['total_before_dedupe']} before dedupe)")
    for sid, err in out["errors"].items():
        print(f"  ERROR {sid}: {err}")
    for it in out["items"][:10]:
        age = "?" if not it["published"] else \
            f"{(out['fetched_at'] - it['published']) // 60}m"
        print(f"  [{it['relevance']}] {age:>6}  {it['source'][:20]:<20} {it['title'][:70]}")
