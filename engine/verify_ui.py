"""
verify_ui.py  --  Contract checks on what the UI is handed.

This does NOT render anything. It asserts that the payloads the frontend
consumes are complete and sane, which is where the bugs I actually hit lived:
a level rendering on the wrong side of price, a field the UI reads that the
engine renamed, a number arriving as NaN and painting as "NaN" on a board
someone sizes a position from.

Anything genuinely visual — did the canvas paint, is a panel clipped — needs a
real compositing window and is not fakeable here. Screenshot the app for that.

Run against a LIVE engine:
    python verify_ui.py [http://127.0.0.1:8765]
"""
import json
import math
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def get(path: str, timeout: int = 45):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        return json.load(r)


def finite_numbers(obj, path="") -> list:
    """Every NaN/Infinity reachable in the payload. JSON allows them; JS renders
    them as the literal text 'NaN', which on a trading board is indefensible."""
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            bad += finite_numbers(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += finite_numbers(v, f"{path}[{i}]")
    elif isinstance(obj, float) and not math.isfinite(obj):
        bad.append(f"{path}={obj}")
    return bad


def main():
    print(f"[0] engine reachable at {BASE}")
    try:
        h = get("/api/health", timeout=10)
        check("health ok", h.get("ok") is True, str(h)[:120])
    except Exception as e:
        check("health ok", False, f"{type(e).__name__}: {e}")
        print("\nEngine not running. Start it with: cd engine && python server.py")
        raise SystemExit(1)

    print("[1] /api/bias has every field the UI reads")
    snap = get("/api/bias?ticker=SPY")
    # Mirrors the Snapshot interface in src/lib/api.ts. A rename on either side
    # shows up here rather than as an empty panel.
    for key in ("generated_at", "ticker", "confirmer", "tickers", "mock", "provider",
                "gex", "expected_move", "level_map", "levels", "smt", "bias",
                "news", "track", "plan", "trend"):
        check(f"snapshot.{key}", key in snap)

    print("[2] gex block is complete and self-consistent")
    g = snap["gex"]
    for key in ("spot", "net_gex", "regime", "gamma_flip", "control_node",
                "atm_iv", "call_wall", "put_wall", "profile", "put_call_ratio"):
        check(f"gex.{key}", key in g)
    check("regime matches sign of net_gex",
          (g["regime"] == "positive") == (g["net_gex"] >= 0),
          f"{g['regime']} vs {g['net_gex']:.0f}")
    check("profile non-empty", len(g["profile"]) > 0, str(len(g["profile"])))
    check("profile strikes ascending",
          all(g["profile"][i]["strike"] < g["profile"][i + 1]["strike"]
              for i in range(len(g["profile"]) - 1)))
    # These are what the chart draws. A wall on the wrong side of spot is the
    # bug that makes correct code look broken.
    if g["call_wall"] is not None:
        check("call wall >= spot", g["call_wall"] >= g["spot"],
              f"{g['call_wall']} vs {g['spot']}")
    if g["put_wall"] is not None:
        check("put wall <= spot", g["put_wall"] <= g["spot"],
              f"{g['put_wall']} vs {g['spot']}")

    print("[3] no NaN or Infinity anywhere in the snapshot")
    bad = finite_numbers(snap, "snapshot")
    check("all numbers finite", not bad, ", ".join(bad[:5]))

    print("[4] bias renders without gaps")
    b = snap["bias"]
    check("label non-empty", bool(b.get("label")), str(b.get("label")))
    check("has signals", len(b.get("signals", [])) > 0)
    # "Every signal shows its why" is the rule the whole panel is built on.
    check("every signal has a reason",
          all(s.get("reason") for s in b["signals"]),
          str([s["name"] for s in b["signals"] if not s.get("reason")]))
    check("every signal has a name", all(s.get("name") for s in b["signals"]))
    check("conviction present", b.get("conviction") in ("normal", "reduced"),
          str(b.get("conviction")))

    print("[5] level_map is drawable")
    for row in snap["level_map"]:
        check(f"level {row.get('role', '?')} has price+tag",
              row.get("price") is not None and bool(row.get("tag")), str(row))

    print("[6] /api/bars is chartable")
    bars = get("/api/bars?ticker=SPY&interval=5m&days=2")
    check("source reported", bool(bars.get("source")), str(bars.get("source")))
    rows = bars["bars"]
    check("non-empty", len(rows) > 0, str(len(rows)))
    if rows:
        check("OHLC coherent",
              all(r["low"] <= r["open"] <= r["high"] and r["low"] <= r["close"] <= r["high"]
                  for r in rows))
        ts = [r["time"] for r in rows]
        # Lightweight Charts silently drops out-of-order data.
        check("times ascending & unique", ts == sorted(ts) and len(set(ts)) == len(ts))
        check("no NaN in bars", not finite_numbers(rows, "bars"))

    print("[7] /api/news is renderable")
    news = get("/api/news?ticker=SPY", timeout=60)
    check("items present", isinstance(news.get("items"), list))
    check("sources reported", len(news.get("sources", {})) > 0)
    # Every item needs the fields the rail renders, or a row comes out blank.
    for it in news["items"][:20]:
        check(f"item has title+source: {it.get('title', '')[:24]}",
              bool(it.get("title")) and bool(it.get("source")))
    check("rank present on all items",
          all("rank" in i for i in news["items"]))
    check("errors is a dict (named, not swallowed)",
          isinstance(news.get("errors"), dict))

    print("[8] event risk is explicit about what it knows")
    n = snap["news"]
    check("high_impact is a bool", isinstance(n.get("high_impact"), bool),
          str(type(n.get("high_impact"))))
    # The distinction the panel depends on: "no event" vs "we couldn't look".
    if "level" in n:
        check("level is a known value",
              n["level"] in ("high", "medium", "none", "unknown"), str(n["level"]))
    if n.get("high_impact"):
        check("a high_impact read names its driver", bool(n.get("headline")),
              str(n.get("headline")))

    print("[9] mock mode is labelled")
    # A synthetic number that doesn't announce itself is the worst failure here.
    check("mock flag present", isinstance(snap.get("mock"), bool))
    if snap["mock"]:
        check("provider still reported", bool(snap.get("provider")))

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:8])}")
        raise SystemExit(1)
    print("all UI contract checks passed")


if __name__ == "__main__":
    main()
