"""
verify_netflow.py  --  the net premium summariser.

The dangerous part is the downsample. 406 one-minute ticks do not fit in a
sparkline, so they are bucketed — and if the bucketing SAMPLED (took every nth
tick) instead of SUMMING, whole minutes of premium would vanish and the
cumulative line would finish somewhere the session never reached. The line
would still look plausible, which is what makes it worth a test.

Also pinned: ordering. UW returns newest-first, and a cumulative walk run in
that order produces a mirror image of the real day.

Pure functions on synthetic rows — offline, no key.
Run:  python verify_netflow.py
"""
import sys

from data import unusual_whales as uw

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def tick(i, call_prem, put_prem, **kw):
    r = {"tape_time": f"2026-08-03T{13 + i // 60:02d}:{i % 60:02d}:00.000000Z",
         "net_call_premium": str(call_prem), "net_put_premium": str(put_prem),
         "net_delta": "0", "call_volume_ask_side": "10",
         "call_volume_bid_side": "10", "put_volume_ask_side": "10",
         "put_volume_bid_side": "10"}
    r.update(kw)
    return r


print("[0] totals are sums of every tick, not of a sample")
rows = [tick(i, 100, 40) for i in range(400)]
s = uw.summarize_net_flow(rows, points=20)
check("call premium totals all 400", s["call_premium"] == 40000.0, str(s["call_premium"]))
check("put premium totals all 400", s["put_premium"] == 16000.0, str(s["put_premium"]))
check("net is calls minus puts", s["net"] == 24000.0, str(s["net"]))
check("tick count reported", s["ticks"] == 400, str(s["ticks"]))

print("[1] the sparkline ENDS at the true cumulative total")
# This is the check that catches sampling. If buckets dropped ticks, the last
# point would fall short of `net` while still drawing a believable curve.
check("last series point == net", abs(s["series"][-1]["v"] - s["net"]) < 1e-6,
      f"{s['series'][-1]['v']} vs {s['net']}")
check("series was actually downsampled", len(s["series"]) <= 21,
      str(len(s["series"])))
check("series is not empty", len(s["series"]) > 1, str(len(s["series"])))

print("[2] the cumulative walk runs forward in time")
# UW returns newest-first. Walking that order mirrors the day.
rising = [tick(i, 100, 0) for i in range(60)]
fwd = uw.summarize_net_flow(rising, points=10)
rev = uw.summarize_net_flow(list(reversed(rising)), points=10)
check("input order does not change the total", fwd["net"] == rev["net"],
      f"{fwd['net']} vs {rev['net']}")
_same = [p["v"] for p in fwd["series"]] == [p["v"] for p in rev["series"]]
check("input order does not change the curve", _same,
      "" if _same else "series differ")
check("cumulative is monotonic when every tick is positive",
      all(fwd["series"][i]["v"] <= fwd["series"][i + 1]["v"]
          for i in range(len(fwd["series"]) - 1)))

print("[3] a sign flip mid-session is preserved, not averaged away")
mixed = [tick(i, 1000, 0) for i in range(50)] + [tick(50 + i, -3000, 0) for i in range(50)]
m = uw.summarize_net_flow(mixed, points=20)
vals = [p["v"] for p in m["series"]]
check("peaks positive before the flip", max(vals) > 0, str(max(vals)))
check("ends negative after it", vals[-1] < 0, str(vals[-1]))
check("end equals the true net", abs(vals[-1] - m["net"]) < 1e-6,
      f"{vals[-1]} vs {m['net']}")

print("[4] ask-side share separates 'traded' from 'bought'")
lifted = [tick(i, 100, 0, call_volume_ask_side="90", call_volume_bid_side="10")
          for i in range(10)]
a = uw.summarize_net_flow(lifted)
check("call ask share is 90%", a["call_ask_pct"] == 90.0, str(a["call_ask_pct"]))
none_vol = [tick(i, 100, 0, call_volume_ask_side="0", call_volume_bid_side="0")
            for i in range(5)]
b = uw.summarize_net_flow(none_vol)
check("no volume -> None, not a fabricated 50%", b["call_ask_pct"] is None,
      str(b["call_ask_pct"]))

print("[5] degenerate inputs")
check("empty -> unavailable", uw.summarize_net_flow([])["available"] is False)
one = uw.summarize_net_flow([tick(0, 500, 100)])
check("single tick works", one["available"] and one["net"] == 400.0, str(one["net"]))
check("single tick yields a point", len(one["series"]) == 1, str(len(one["series"])))
junk = uw.summarize_net_flow([tick(0, "abc", "def")])
check("garbage coerces to 0 rather than raising", junk["net"] == 0.0, str(junk["net"]))

print("[6] points= is a ceiling, not a promise")
few = uw.summarize_net_flow([tick(i, 10, 0) for i in range(7)], points=50)
check("fewer ticks than points -> one point each", len(few["series"]) == 7,
      str(len(few["series"])))
check("still ends at the total", abs(few["series"][-1]["v"] - few["net"]) < 1e-6)

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all net flow checks passed")
