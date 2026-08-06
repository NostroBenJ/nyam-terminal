"""
verify_flow_signals.py  --  the three flow votes, and when they stay silent.

WHAT MAKES THIS DANGEROUS. Every other signal here reads positioning: open
interest, dealer gamma, what is already on the books. These read participation,
they are noisier, and they now carry weight in a number the user sizes real
trades against. A signal that fires on every scrap of data is not measuring
conviction, it is adding noise with a weight attached.

So most of these checks are about SILENCE — that a thin, split, or missing
reading votes zero rather than picking a side. The thresholds are ratios, never
dollar amounts: an absolute premium that means "significant" on SPY means
"enormous" on IWM, and one engine grades both.

Offline, synthetic inputs. Run: python verify_flow_signals.py
"""
import sys

from analysis import bias_engine as be

_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def sig(out, name):
    for s in out:
        if s["name"] == name:
            return s
    return None


def netflow(net, call_p, put_p):
    return {"net_flow": {"available": True, "net": net,
                         "call_premium": call_p, "put_premium": put_p}}


print("[0] net premium votes only when it is a real share of the day")
# 90M net out of 100M gross is decisive; 5M out of 100M is two sides cancelling.
s = sig(be._flow_signals(netflow(90e6, 95e6, 5e6)), "Net Premium")
check("decisive net -> long", s["lean"] == 1 and s["weight"] > 0, str(s["lean"]))
s = sig(be._flow_signals(netflow(-90e6, 5e6, 95e6)), "Net Premium")
check("decisive net the other way -> short", s["lean"] == -1, str(s["lean"]))
s = sig(be._flow_signals(netflow(5e6, 52e6, 47e6)), "Net Premium")
check("thin net -> no lean AND no weight", s["lean"] == 0 and s["weight"] == 0,
      f"lean={s['lean']} weight={s['weight']}")
s = sig(be._flow_signals(netflow(0, 50e6, 50e6)), "Net Premium")
check("exactly balanced -> no lean", s["lean"] == 0)
check("no net_flow at all -> signal absent",
      sig(be._flow_signals({}), "Net Premium") is None)
check("net_flow unavailable -> signal absent",
      sig(be._flow_signals({"net_flow": {"available": False}}), "Net Premium") is None)

print("[1] the threshold is a RATIO, so ticker size cannot fake conviction")
# Same 15% share at two very different scales must read identically.
big = sig(be._flow_signals(netflow(300e6, 1150e6, 850e6)), "Net Premium")
small = sig(be._flow_signals(netflow(300e3, 1150e3, 850e3)), "Net Premium")
check("same share, same verdict at 1000x scale",
      big["lean"] == small["lean"], f"{big['lean']} vs {small['lean']}")

print("[2] flow alerts need count AND premium skew")
def alerts(n_call, n_put, prem=5e6):
    return {"flow_alerts":
            [{"type": "call", "premium": prem} for _ in range(n_call)] +
            [{"type": "put", "premium": prem} for _ in range(n_put)]}

s = sig(be._flow_signals(alerts(8, 1)), "Flow Alerts")
check("lopsided call flow -> long", s["lean"] == 1, str(s["lean"]))
s = sig(be._flow_signals(alerts(1, 8)), "Flow Alerts")
check("lopsided put flow -> short", s["lean"] == -1, str(s["lean"]))
s = sig(be._flow_signals(alerts(5, 4)), "Flow Alerts")
check("split flow -> no lean", s["lean"] == 0 and s["weight"] == 0, str(s["lean"]))
check("two alerts is anecdote, not flow -> absent",
      sig(be._flow_signals(alerts(2, 0)), "Flow Alerts") is None)
# Premium-weighted, not count-weighted: one enormous put beats six small calls.
s = sig(be._flow_signals({"flow_alerts":
        [{"type": "call", "premium": 1e5} for _ in range(6)] +
        [{"type": "put", "premium": 9e6}]}), "Flow Alerts")
check("weighted by premium, not by count", s["lean"] == -1,
      f"lean={s['lean']} (6 calls vs 1 large put)")

print("[3] dark pool is the weakest vote and behaves like it")
def dp(buys, sells, mids=0, canceled=0):
    return {"darkpool":
            [{"lean": "buy", "canceled": False} for _ in range(buys)] +
            [{"lean": "sell", "canceled": False} for _ in range(sells)] +
            [{"lean": "mid", "canceled": False} for _ in range(mids)] +
            [{"lean": "buy", "canceled": True} for _ in range(canceled)]}

s = sig(be._flow_signals(dp(9, 1)), "Dark Pool")
check("lopsided buys -> long", s["lean"] == 1, str(s["lean"]))
check("carries the LEAST weight of the three",
      s["weight"] < be.WEIGHTS["net_premium"] and s["weight"] < be.WEIGHTS["flow_alerts"],
      f"dp={s['weight']} flow={be.WEIGHTS['flow_alerts']} net={be.WEIGHTS['net_premium']}")
check("says the side is inferred", "inferred" in s["reason"].lower(), s["reason"][:60])
s = sig(be._flow_signals(dp(5, 4)), "Dark Pool")
check("near-even prints -> no lean", s["lean"] == 0, str(s["lean"]))
check("too few prints -> absent", sig(be._flow_signals(dp(2, 1)), "Dark Pool") is None)
# Cancelled prints are not trades and must not create conviction.
check("cancelled prints do not count toward the threshold",
      sig(be._flow_signals(dp(2, 0, canceled=9)), "Dark Pool") is None)
s = sig(be._flow_signals(dp(0, 0, mids=9)), "Dark Pool")
check("all-mid prints -> no lean", s is None or s["lean"] == 0,
      str(s["lean"]) if s else "absent")

print("[4] every emitted signal is shaped for the panel")
out = be._flow_signals({**netflow(90e6, 95e6, 5e6), **alerts(8, 1), **dp(9, 1)})
check("all three fired", len(out) == 3, str([s["name"] for s in out]))
for s in out:
    check(f"{s['name']}: has a reason", bool(s.get("reason")))
    check(f"{s['name']}: lean is -1/0/1", s["lean"] in (-1, 0, 1), str(s["lean"]))
    check(f"{s['name']}: weight is non-negative", s["weight"] >= 0, str(s["weight"]))

print("[5] flow can move the score, and by how much")
gex = {"spot": 100.0, "regime": "positive", "gamma_flip": 95.0, "call_wall": 105.0,
       "put_wall": 95.0, "control_node": 100.0, "call_oi_change": 0,
       "put_oi_change": 0, "put_call_ratio": 1.0}
smt = {"lean": 0, "note": "none"}
news = {"high_impact": False, "headline": ""}
base = be.build_bias(gex, {}, smt, news)
bull = be.build_bias(gex, {}, smt, news,
                     flow={**netflow(90e6, 95e6, 5e6), **alerts(8, 1), **dp(9, 1)})
bear = be.build_bias(gex, {}, smt, news,
                     flow={**netflow(-90e6, 5e6, 95e6), **alerts(1, 8), **dp(1, 9)})
print(f"      base={base['score']}  bullish flow={bull['score']}  bearish flow={bear['score']}")
check("bullish flow raises the score", bull["score"] > base["score"])
check("bearish flow lowers it", bear["score"] < base["score"])
swing = bull["score"] - bear["score"]
check("total flow swing equals 2x the summed weights",
      abs(swing - 2 * (be.WEIGHTS["net_premium"] + be.WEIGHTS["flow_alerts"]
                       + be.WEIGHTS["dark_pool"])) < 1e-9, f"{swing}")
# Flow must inform the lean, not dominate it: positioning still outweighs it.
positioning = (be.WEIGHTS["gex_regime"] + be.WEIGHTS["wall_proximity"]
               + be.WEIGHTS["smt"] + be.WEIGHTS["put_call"] + be.WEIGHTS["magnet"])
flow_total = (be.WEIGHTS["net_premium"] + be.WEIGHTS["flow_alerts"]
              + be.WEIGHTS["dark_pool"])
check("flow cannot outvote positioning", flow_total < positioning,
      f"flow={flow_total} positioning={positioning}")

print("[6] the signal mix is versioned and travels with the call")
check("bias carries a mix", base.get("mix") == be.MIX_VERSION, str(base.get("mix")))
check("mix is not the v1 default", be.MIX_VERSION != "v1", be.MIX_VERSION)
check("no flow at all still builds a bias", be.build_bias(gex, {}, smt, news) is not None)

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    sys.exit(1)
print("all flow signal checks passed")
