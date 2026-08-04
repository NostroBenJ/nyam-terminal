"""
verify_uw_session.py  --  the UW-only session shape, with no Yahoo anywhere.

WHAT THIS IS DEFENDING. The board used to take a Yahoo baseline and overwrite
the pieces UW covered, which left spot real-time while the overnight range it
gets compared against was ~15 minutes behind. That is not a small
inconsistency, it is an impossible state: spot could print above an overnight
high that did not yet know the print had happened, and SMT, the level map and
the bias all read that contradiction as signal.

So the checks here are mostly RELATIONSHIPS, not values. A number fetched from
the right endpoint can still be the wrong number; what proves one clock is that
the numbers agree with each other. spot inside the overnight range, the
overnight window starting where the prior session ended, the prior session
strictly before today.

Live network calls. Run against a real key:  python verify_uw_session.py
"""
import datetime as dt
import sys

import config
from data import unusual_whales as uw

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


if config.USE_MOCK_DATA or config.PROVIDER != "uw" or not config.UW_API_KEY:
    print("Needs the live uw provider and a key. "
          f"provider={config.PROVIDER} mock={config.USE_MOCK_DATA}")
    raise SystemExit(1)

T = "SPY"
today = dt.date.today()

print("[0] prior session is the last COMPLETED regular session")
prior = uw.prior_session(T, today)
check("date is strictly before today", prior["date"] < today,
      f"{prior['date']} vs {today}")
check("within the last 10 days", (today - prior["date"]).days <= 10,
      f"{(today - prior['date']).days}d ago")
check("not a weekend", prior["date"].weekday() < 5, prior["date"].strftime("%a"))
check("high >= low", prior["high"] >= prior["low"],
      f"{prior['high']} / {prior['low']}")
check("close within the day's range",
      prior["low"] <= prior["close"] <= prior["high"],
      f"{prior['low']} <= {prior['close']} <= {prior['high']}")

print("[1] the prior session must be the REGULAR one, not pre/post")
# The pre and post rows for a date carry their own high/low. Mixing them in
# reports an overnight spike as part of the regular range, which is how a
# 'breakout' that only ever happened at 4am gets onto the board.
rows = uw.ohlc(T, candle_size="1d", limit=30)
same_day = [r for r in rows if str(r.get("date"))[:10] == prior["date"].isoformat()]
sessions = {r.get("market_time") for r in same_day}
check("that date has multiple session rows", len(same_day) > 1, str(sessions))
reg = [r for r in same_day if r.get("market_time") == uw.MT_REGULAR]
check("we picked the regular row", len(reg) == 1, str(sessions))
if reg:
    check("high matches the regular row exactly",
          abs(uw._f(reg[0], "high") - prior["high"]) < 1e-9)
    # Informational, not a check: whether the extended rows would have widened
    # the range depends on the day, so asserting either way would be asserting
    # the weather. Printed because it shows what the regular-row filter is
    # buying on any given run.
    non_reg = [r for r in same_day if r.get("market_time") != uw.MT_REGULAR]
    widened = [r for r in non_reg
               if uw._f(r, "high") > prior["high"] or
               (0 < uw._f(r, "low") < prior["low"])]
    print(f"      (info) {len(widened)} of {len(non_reg)} extended-hours rows "
          f"would have widened the range today")

print("[2] overnight range starts at the prior close, not before")
hi, lo, real = uw.overnight_range(T, prior, config.TZ)
check("high >= low", hi >= lo, f"{hi} / {lo}")
check("is_real is a bool", isinstance(real, bool), str(real))
if real:
    # A full prior session is NOT an overnight. If the window had leaked back
    # past the prior close it would span two sessions and be much wider.
    span = hi - lo
    prior_span = prior["high"] - prior["low"]
    check("overnight span is not absurdly wider than the prior day",
          span <= prior_span * 3,
          f"on={span:.2f} prior={prior_span:.2f}")
else:
    check("fallback is exactly the prior range, not widened",
          (hi, lo) == (prior["high"], prior["low"]), f"{hi}/{lo}")

print("[3] one clock — spot agrees with the range it is compared against")
st = uw.stock_state(T)
spot = uw._f(st, "close")
check("spot is positive", spot > 0, str(spot))
if real:
    # THE WHOLE POINT. With mixed feeds spot could sit outside an overnight
    # range that had not caught up yet. Same clock means it cannot.
    check("spot lies within the overnight range",
          lo <= spot <= hi, f"{lo} <= {spot} <= {hi}")
else:
    print("      (no overnight session yet — containment not applicable)")

print("[4] tape_time is present and sane")
tape = st.get("tape_time")
check("tape_time present", bool(tape), str(tape))
if tape:
    t = dt.datetime.fromisoformat(str(tape).replace("Z", "+00:00"))
    age = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
    check("tape_time is tz-aware", t.tzinfo is not None)
    check("not in the future", age > -120, f"{age:.0f}s")
    check("prev_close present", uw._f(st, "prev_close") > 0,
          str(st.get("prev_close")))

print("[5] the confirmer resolves on the same feed")
conf = config.confirmer_for(T)
cst = uw.stock_state(conf)
cprior = uw.prior_session(conf, today)
check(f"confirmer is {conf}", bool(conf))
check("confirmer spot positive", uw._f(cst, "close") > 0)
# SMT compares two instruments. If their prior sessions were different dates,
# the divergence would be measuring a calendar mismatch rather than the market.
check("confirmer prior session is the SAME date as the primary",
      cprior["date"] == prior["date"], f"{cprior['date']} vs {prior['date']}")

print("[6] headlines are usable")
try:
    news = uw.headlines(limit=10)
    check("returned rows", len(news) > 0, str(len(news)))
    if news:
        check("has a headline", bool(news[0].get("headline")))
        check("has a timestamp", bool(news[0].get("created_at")))
except uw.UWError as e:
    check("headlines endpoint reachable", False, str(e)[:100])

print("[7] the market dict carries no Yahoo anywhere")
from data import data_sources  # noqa: E402

m = data_sources.get_market(T)
srcs = m.get("sources") or {}
yahoo = {k: v for k, v in srcs.items() if v == "yahoo"}
check("no source reports yahoo", not yahoo, str(yahoo))
# The whole point of the change. If yfinance is importable but never imported,
# nothing on this path can be silently reaching it.
check("yfinance was never imported building the market",
      "yfinance" not in sys.modules,
      "imported" if "yfinance" in sys.modules else "absent")
check("spot from UW", srcs.get("spot") == "unusual_whales", str(srcs.get("spot")))
check("prior session from UW", srcs.get("prior_session") == "unusual_whales",
      str(srcs.get("prior_session")))
check("overnight from UW", srcs.get("overnight") == "unusual_whales",
      str(srcs.get("overnight")))
check("chain from UW", srcs.get("chain") == "unusual_whales", str(srcs.get("chain")))
check("no uw_errors", not m.get("uw_errors"), str(m.get("uw_errors"))[:160])

print("[8] the shape downstream code expects is intact")
p = m["primary"]
for key in ("ticker", "spot", "prior_high", "prior_low", "prior_close",
            "on_high", "on_low", "on_is_real", "expiries"):
    check(f"primary.{key}", key in p)
s = m["secondary"]
for key in ("ticker", "spot", "prior_high", "prior_low", "on_high", "on_low"):
    check(f"secondary.{key}", key in s)
check("oi_report present", "oi_report" in m, str(m.get("oi_report"))[:110])
check("news present", isinstance(m.get("news"), list), str(type(m.get("news"))))

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all UW session checks passed — one provider, one clock")
