"""
probe_live.py  --  First real run against live data. Reports, never asserts.

The point is to find out what the free Yahoo path actually returns before
trusting any of it. Each stage is timed and reported separately so a failure
names the stage rather than surfacing as one opaque traceback.

    NYAM_MOCK=0 python probe_live.py [TICKER]
"""
import datetime as dt
import os
import sys
import time

os.environ.setdefault("NYAM_MOCK", "0")
os.environ.setdefault("NYAM_PROVIDER", "yahoo")

import config                                            # noqa: E402

config.USE_MOCK_DATA = False
config.PROVIDER = "yahoo"

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()


def stage(name):
    print(f"\n=== {name} ===")
    return time.time()


def done(t0):
    print(f"  ({time.time() - t0:.1f}s)")


def main():
    print(f"probing LIVE data for {TICKER}")
    print(f"  mock={config.USE_MOCK_DATA}  provider={config.PROVIDER}")
    print(f"  now={dt.datetime.now(config.TZ):%Y-%m-%d %H:%M:%S %Z}")

    t = stage("1. raw market pull (chain + session levels)")
    from data.data_sources import get_market
    try:
        market = get_market(TICKER)
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        raise SystemExit(1)
    p = market["primary"]
    print(f"  ticker        : {p['ticker']}")
    print(f"  spot          : {p['spot']}")
    print(f"  prior H/L/C   : {p['prior_high']} / {p['prior_low']} / {p['prior_close']}")
    print(f"  overnight H/L : {p['on_high']} / {p['on_low']}  (real={p.get('on_is_real')})")
    print(f"  confirmer     : {market['secondary']['ticker']} @ {market['secondary']['spot']}")
    exps = p.get("expiries", [])
    print(f"  expiries      : {len(exps)}")
    for e in exps:
        print(f"     {e['label']}  dte={e['dte']:>2}  calls={len(e['calls']):>4}  puts={len(e['puts']):>4}")
    total = sum(len(e["calls"]) + len(e["puts"]) for e in exps)
    print(f"  contracts     : {total}")
    oi = market.get("oi_report") or {}
    print(f"  OI change     : available={oi.get('available')}  {oi.get('note', '')}")
    done(t)

    if not exps:
        print("\n  NO EXPIRIES — everything downstream is meaningless. Stopping.")
        raise SystemExit(1)

    t = stage("2. sanity of the raw chain")
    ivs = [c["iv"] for e in exps for c in e["calls"] + e["puts"]]
    ois = [c["oi"] for e in exps for c in e["calls"] + e["puts"]]
    strikes = [c["strike"] for e in exps for c in e["calls"] + e["puts"]]
    print(f"  IV range      : {min(ivs):.3f} – {max(ivs):.3f}   (sane: ~0.05–3.0)")
    print(f"  OI range      : {min(ois):,.0f} – {max(ois):,.0f}")
    print(f"  strike range  : {min(strikes)} – {max(strikes)}   (spot {p['spot']})")
    print(f"  strikes span spot: {min(strikes) < p['spot'] < max(strikes)}")
    zero_iv = sum(1 for c in ivs if c <= 0)
    print(f"  zero/neg IV   : {zero_iv}  (these are dropped upstream)")
    done(t)

    t = stage("3. full snapshot (GEX -> bias -> brief)")
    from pipeline import build_snapshot
    try:
        snap = build_snapshot(TICKER)
    except Exception as e:
        import traceback
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()
        raise SystemExit(1)
    g = snap["gex"]
    print(f"  spot          : {g['spot']}")
    print(f"  net GEX       : {g['net_gex']:,.0f}")
    print(f"  regime        : {g['regime']}")
    print(f"  gamma flip    : {g['gamma_flip']}")
    print(f"  call wall     : {g['call_wall']}")
    print(f"  put wall      : {g['put_wall']}")
    print(f"  control node  : {g['control_node']}")
    print(f"  ATM IV        : {g['atm_iv']:.4f}")
    print(f"  P/C ratio     : {g['put_call_ratio']:.3f}")
    print(f"  OI change C/P : {g['call_oi_change']:,} / {g['put_oi_change']:,}")
    print(f"  profile points: {len(g['profile'])}")
    print(f"  expected move : {snap['expected_move']}")
    done(t)

    t = stage("4. invariants that must hold on ANY data")
    ok = True

    def inv(name, cond, detail=""):
        nonlocal ok
        print(f"  [{'OK ' if cond else 'BAD'}] {name}{('  — ' + detail) if not cond else ''}")
        ok = ok and cond

    inv("regime matches sign of net GEX",
        (g["regime"] == "positive") == (g["net_gex"] >= 0),
        f"{g['regime']} vs {g['net_gex']:,.0f}")
    if g["call_wall"] is not None:
        inv("call wall >= spot", g["call_wall"] >= g["spot"],
            f"{g['call_wall']} < {g['spot']}")
    if g["put_wall"] is not None:
        inv("put wall <= spot", g["put_wall"] <= g["spot"],
            f"{g['put_wall']} > {g['spot']}")
    if g["gamma_flip"] is not None:
        inv("gamma flip within strike range",
            min(strikes) <= g["gamma_flip"] <= max(strikes),
            f"{g['gamma_flip']} outside {min(strikes)}–{max(strikes)}")
    inv("ATM IV plausible", 0.01 < g["atm_iv"] < 3.0, f"{g['atm_iv']}")
    inv("overnight low <= high", p["on_low"] <= p["on_high"])
    inv("overnight range near spot",
        abs(p["on_low"] - p["spot"]) / p["spot"] < 0.15,
        f"ON low {p['on_low']} vs spot {p['spot']} — phantom-bar symptom")
    inv("expected move positive", snap["expected_move"]["dollars"] > 0)
    done(t)

    t = stage("5. bias + news")
    b = snap["bias"]
    print(f"  label         : {b['label']}  score={b['score']}  ({b['conviction']})")
    for s in b["signals"]:
        print(f"     {s['lean']:>+2}  w={s['weight']:<5} {s['name']}")
    n = snap["news"]
    print(f"  news kind     : {n.get('kind')}  level={n.get('level')}")
    print(f"  high impact   : {n.get('high_impact')}")
    print(f"  headline      : {n.get('headline') or '(none)'}")
    if n.get("feed_errors"):
        print(f"  feed errors   : {list(n['feed_errors'])}")
    bm = snap.get("brief_meta", {})
    print(f"  brief         : {bm.get('source')} cached={bm.get('cached')} "
          f"calls_today={bm.get('calls_today')}")
    done(t)

    t = stage("6. bars for the chart")
    from data.data_sources import get_bars
    for iv in ("5m", "1h"):
        r = get_bars(TICKER, interval=iv, lookback_days=5)
        bars = r["bars"]
        if bars:
            last = bars[-1]
            drift = abs(last["close"] - g["spot"]) / g["spot"] * 100
            print(f"  {iv:>3}: {len(bars):>4} bars  src={r['source']:<6} "
                  f"last={last['close']}  vs spot {g['spot']}  drift={drift:.2f}%")
        else:
            print(f"  {iv:>3}: NO BARS — {r.get('note')}")
    done(t)

    print("\n" + "=" * 60)
    print("LIVE PROBE PASSED — invariants hold" if ok else
          "LIVE PROBE FOUND PROBLEMS — see [BAD] lines above")
    print("=" * 60)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
