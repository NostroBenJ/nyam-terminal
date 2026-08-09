"""
verify_trading_day.py  --  one definition of "today", and it is the exchange's.

THE CATEGORY THIS CLOSES. The engine had two notions of the current day mixed
through it: capture folders named from ET, the missed-days audit comparing
those folders against a LOCAL date, grading deciding what counts as "past"
locally, the request budget resetting locally, and — worst — chain_to_expiries
computing DTE locally.

On a machine sitting in ET the two agree and nothing shows, which is why none
of it surfaced. On a UTC host they diverge every evening after 8pm ET. A DTE
wrong by one day changes t_years, which changes gamma, which changes every
level on the board — silently, and only outside the timezone it was written in.
A server was already under discussion.

These checks do not depend on where they run: the divergence is constructed
rather than waited for.

Run: python verify_trading_day.py
"""
import datetime as dt
import sys
from zoneinfo import ZoneInfo

import config

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


print("[0] config.today() is the exchange day")
et_now = dt.datetime.now(ZoneInfo("America/New_York"))
check("matches America/New_York", config.today() == et_now.date(),
      f"{config.today()} vs {et_now.date()}")
check("now_et is timezone-aware", config.now_et().tzinfo is not None)
check("now_et is in the configured zone",
      str(config.now_et().tzinfo) == str(config.TZ),
      f"{config.now_et().tzinfo} vs {config.TZ}")

print("[1] the divergence being defended against is real")
# 9pm ET is already tomorrow in UTC. Constructed, so this holds year-round and
# on any host.
evening_et = dt.datetime(2026, 8, 7, 21, 30, tzinfo=ZoneInfo("America/New_York"))
as_utc = evening_et.astimezone(ZoneInfo("UTC"))
check("9:30pm ET is the NEXT day in UTC", evening_et.date() != as_utc.date(),
      f"ET {evening_et.date()} vs UTC {as_utc.date()}")
check("the gap is exactly one day", (as_utc.date() - evening_et.date()).days == 1)

print("[2] a one-day error changes gamma, which is why this matters")
from analysis import gex  # noqa: E402

spot, strike, iv, r = 773.0, 775.0, 0.10, config.RISK_FREE_RATE
# A 2-DTE option priced as though it were 1 DTE or 3.
g2 = gex.bs_gamma(spot, strike, 2 / 365.0, iv, r)
g1 = gex.bs_gamma(spot, strike, 1 / 365.0, iv, r)
g3 = gex.bs_gamma(spot, strike, 3 / 365.0, iv, r)
off_low = abs(g1 - g2) / g2 * 100
off_high = abs(g3 - g2) / g2 * 100
print(f"      2DTE gamma {g2:.6f} · as 1DTE {g1:.6f} ({off_low:.1f}% off) "
      f"· as 3DTE {g3:.6f} ({off_high:.1f}% off)")
check("a one-day DTE error moves gamma materially", off_low > 5,
      f"{off_low:.1f}% on the near side")

print("[3] no engine module still reads the host's local date")
# The compile-time guarantee: the wrong call is simply not present. Verify and
# probe scripts are exempt — they are not shipped and often construct dates.
import os  # noqa: E402
import re  # noqa: E402

root = os.path.dirname(os.path.abspath(__file__))
offenders = []
for base, _dirs, files in os.walk(root):
    if any(p in base for p in ("dist", "build", "__pycache__", "data_store")):
        continue
    for fn in files:
        if not fn.endswith(".py"):
            continue
        # config.py defines the replacement and documents the trap by name, so
        # it is the one file expected to contain the string.
        if fn.startswith(("verify_", "measure_", "probe_")) or fn == "config.py":
            continue
        path = os.path.join(base, fn)
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if re.search(r"\bdate\.today\(\)", line) and "config.today" not in line:
                    offenders.append(f"{os.path.relpath(path, root)}:{i}")

check("no shipped module calls date.today()", not offenders,
      ", ".join(offenders[:6]) if offenders else "")

print("[4] the day is stable across a call")
# Two reads either side of work must not straddle midnight in a way that makes
# a single snapshot internally inconsistent. Cheap to assert, and it documents
# that the function is not memoised.
a = config.today()
b = config.today()
check("consecutive reads agree", a == b, f"{a} vs {b}")

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all trading-day checks passed")
