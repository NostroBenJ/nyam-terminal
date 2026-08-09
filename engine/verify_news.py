"""
verify_news.py  --  Checks on the news parser. No network.

Parsing is where silent corruption lives: a date format that fails to parse
turns into a missing timestamp, an unhandled namespace turns into an empty
rail, and both look like "quiet news day" rather than "broken". Everything here
runs against fixtures so it can't pass because a feed happened to be up.

    python verify_news.py
"""
import data.news_feed as nf

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


FEED = {"id": "t", "name": "Test", "category": "markets", "tier": "market"}

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test</title>
  <item>
    <title>Fed holds rates steady at 4.25%</title>
    <link>https://example.com/a</link>
    <description>&lt;p&gt;The &lt;b&gt;FOMC&lt;/b&gt; voted   to hold.&lt;/p&gt;</description>
    <pubDate>Sat, 01 Aug 2026 13:45:00 GMT</pubDate>
  </item>
  <item>
    <title>NVDA beats on earnings</title>
    <link>https://example.com/b</link>
    <description>Nvidia reported.</description>
    <pubDate>Sat, 01 Aug 2026 12:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Undated filler item</title>
    <link>https://example.com/c</link>
    <description>No date here.</description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Test</title>
  <entry>
    <title>SEC charges firm over disclosure</title>
    <link href="https://example.com/atom1"/>
    <summary>Regulatory action.</summary>
    <updated>2026-08-01T09:30:00Z</updated>
  </entry>
</feed>"""


def main():
    print("[1] RSS 2.0 parses")
    items = nf.parse_feed(RSS, FEED)
    check("3 items", len(items) == 3, f"got {len(items)}")
    check("title read", items[0]["title"] == "Fed holds rates steady at 4.25%",
          items[0]["title"])
    check("link read", items[0]["link"] == "https://example.com/a")

    print("[2] HTML is stripped and whitespace collapsed")
    # Feeds embed markup in descriptions; leaving it in renders tags as text.
    s = items[0]["summary"]
    check("no tags", "<" not in s and ">" not in s, s)
    check("entities unescaped", "&lt;" not in s and "&amp;" not in s, s)
    check("whitespace collapsed", "  " not in s, repr(s))

    print("[3] Atom parses (different element names AND a namespace)")
    a = nf.parse_feed(ATOM, FEED)
    check("1 entry", len(a) == 1, f"got {len(a)}")
    if a:
        check("atom title", a[0]["title"] == "SEC charges firm over disclosure")
        # Atom puts the URL in an attribute, not element text.
        check("atom link from href", a[0]["link"] == "https://example.com/atom1",
              a[0]["link"])
        check("atom <updated> parsed", a[0]["published"] is not None)

    print("[4] dates")
    # Expected epochs derived independently (datetime.timestamp() and
    # calendar.timegm() agree), not read back off this parser's output.
    #   2026-08-01T13:45:00Z -> 1785591900
    #   2026-08-01T12:00:00Z -> 1785585600
    check("RFC822 GMT parsed", items[0]["published"] == 1785591900,
          str(items[0]["published"]))
    check("RFC822 +0000 parsed", items[1]["published"] == 1785585600,
          str(items[1]["published"]))
    check("GMT and +0000 differ by exactly 1h45m",
          items[0]["published"] - items[1]["published"] == 6300,
          str(items[0]["published"] - items[1]["published"]))
    # An undated item must stay undated. Defaulting to now() would make the
    # least-known item sort as the freshest thing on the board.
    check("undated stays None", items[2]["published"] is None,
          str(items[2]["published"]))
    # RFC 822 permits named US zones and strptime's %Z will not parse them, so
    # these came back undated and took the undated rank penalty while carrying
    # a perfectly good timestamp. Offsets computed by hand: 13:45 EDT is 17:45
    # UTC, 13:45 EST is 18:45 UTC.
    base = nf._parse_date("Fri, 07 Aug 2026 13:45:00 +0000")
    check("EDT parses to its offset",
          nf._parse_date("Fri, 07 Aug 2026 13:45:00 EDT") == base + 4 * 3600,
          str(nf._parse_date("Fri, 07 Aug 2026 13:45:00 EDT")))
    check("EST parses to its offset",
          nf._parse_date("Fri, 07 Aug 2026 13:45:00 EST") == base + 5 * 3600)
    check("PST parses to its offset",
          nf._parse_date("Fri, 07 Aug 2026 13:45:00 PST") == base + 8 * 3600)
    check("GMT is unchanged by the rewrite",
          nf._parse_date("Fri, 07 Aug 2026 13:45:00 GMT") == base)
    check("an unknown zone still yields None, not a wrong time",
          nf._parse_date("Fri, 07 Aug 2026 13:45:00 XYZ") is None,
          str(nf._parse_date("Fri, 07 Aug 2026 13:45:00 XYZ")))

    print("[5] date ordering puts undated last, not first")
    scored = [nf.score_item(dict(i), "SPY") for i in items]
    scored.sort(key=lambda x: (x["published"] is not None, x["published"] or 0),
                reverse=True)
    check("undated sorts last", scored[-1]["title"] == "Undated filler item",
          scored[-1]["title"])

    print("[6] relevance scoring")
    fed = nf.score_item(dict(items[0]), "SPY")
    check("macro terms detected", "federal reserve" in fed["macro_terms"]
          or "fomc" in fed["macro_terms"], str(fed["macro_terms"]))
    nvda = nf.score_item(dict(items[1]), "NVDA")
    check("ticker symbol matches", nvda["ticker_match"] is True)
    check("company name matches without symbol",
          nf.score_item({"title": "Nvidia unveils new chip", "summary": "",
                         "tier": "market"}, "NVDA")["ticker_match"] is True)
    # Word-boundary matching: a substring hit would tag unrelated stories.
    check("no substring false positive",
          nf.score_item({"title": "The espy report landed", "summary": "",
                         "tier": "market"}, "SPY")["ticker_match"] is False)
    check("unrelated item scores 0",
          nf.score_item({"title": "Local bakery opens", "summary": "",
                         "tier": "market"}, "SPY")["relevance"] == 0)
    check("primary tier scores without a match",
          nf.score_item({"title": "Routine notice", "summary": "",
                         "tier": "primary"}, "SPY")["relevance"] == 2)

    print("[6b] rank decays relevance with age")
    # Ranking on relevance alone floated a 23-day-old item above everything
    # current; these pin the trade-off rather than trusting it stayed tuned.
    fresh_low = nf._rank({"relevance": 2, "age_hours": 0.5})
    old_high = nf._rank({"relevance": 5, "age_hours": 24 * 23})
    check("fresh weak item beats very old strong one", fresh_low > old_high,
          f"fresh={fresh_low:.3f} old={old_high:.6f}")
    check("half-life is exactly half at 12h",
          abs(nf._rank({"relevance": 4, "age_hours": 12}) - 2.0) < 1e-9,
          str(nf._rank({"relevance": 4, "age_hours": 12})))
    check("a quarter at 24h",
          abs(nf._rank({"relevance": 4, "age_hours": 24}) - 1.0) < 1e-9,
          str(nf._rank({"relevance": 4, "age_hours": 24})))
    check("undated penalised, not treated as fresh",
          nf._rank({"relevance": 4, "age_hours": None})
          < nf._rank({"relevance": 4, "age_hours": 0}),
          f"undated={nf._rank({'relevance': 4, 'age_hours': None})}")
    check("equal age preserves relevance order",
          nf._rank({"relevance": 5, "age_hours": 3})
          > nf._rank({"relevance": 2, "age_hours": 3}))
    check("zero relevance stays zero at any age",
          nf._rank({"relevance": 0, "age_hours": 0}) == 0)

    print("[7] malformed input fails loudly, not silently")
    try:
        nf.parse_feed("<rss><channel><item><title>unclosed", FEED)
        check("ParseError raised on bad XML", False, "no exception")
    except Exception as e:
        check("ParseError raised on bad XML", type(e).__name__ == "ParseError",
              type(e).__name__)

    print("[8] empty feed yields no items, not a crash")
    empty = nf.parse_feed('<?xml version="1.0"?><rss version="2.0"><channel>'
                          "<title>x</title></channel></rss>", FEED)
    check("empty channel -> []", empty == [], str(empty))

    print("[9] items with no title are dropped")
    # A blank headline renders as an empty clickable row.
    notitle = nf.parse_feed('<?xml version="1.0"?><rss version="2.0"><channel>'
                            "<item><link>https://x.com</link></item>"
                            "</channel></rss>", FEED)
    check("untitled dropped", notitle == [], str(notitle))

    print("[10] registry is well-formed")
    ids = [f["id"] for f in nf.FEEDS]
    check("unique ids", len(ids) == len(set(ids)))
    check("all have url/name/tier/category",
          all(all(k in f for k in ("url", "name", "tier", "category"))
              for f in nf.FEEDS))
    check("all urls are https", all(f["url"].startswith("https://")
                                    for f in nf.FEEDS),
          str([f["id"] for f in nf.FEEDS if not f["url"].startswith("https://")]))
    # Unique IDS were checked; unique URLS were not, and that is the gap this
    # missed: cnbc-markets and cnbc-econ shipped pointing at one CNBC feed, so
    # every headline landed on the rail twice, half of it under the wrong
    # category, and both entries looked healthy in the source-status banner.
    urls = {}
    for f in nf.FEEDS:
        urls.setdefault(f["url"], []).append(f["id"])
    shared = {u: i for u, i in urls.items() if len(i) > 1}
    check("no two feeds share a url", not shared, str(shared))
    # Two sources may legitimately cover one category, but two entries with the
    # same name are a copy-paste artefact.
    names = [f["name"] for f in nf.FEEDS]
    check("unique display names", len(names) == len(set(names)),
          str([n for n in names if names.count(n) > 1]))

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS)}")
        raise SystemExit(1)
    print("all news checks passed")


if __name__ == "__main__":
    main()
