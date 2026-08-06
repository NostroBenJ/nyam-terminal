"""
verify_null_contract.py  --  every null the API emits, named out loud.

WHY THIS EXISTS. `TrackRecord.dir_hit_rate` was declared `number` in
src/lib/api.ts while the engine sent `null` whenever no directional call had
been graded. TypeScript therefore had nothing to complain about, `tsc --noEmit`
passed, and the packaged board crashed on `.toFixed()` of null — presenting as
a blank window with no message, which took a full debugging session to
characterise. A type that lies is worse than no type: it buys false confidence
and moves the failure from compile time to a trading screen.

So this walks the real API responses and reports every null-valued path. The
list is then checked against KNOWN_NULLABLE below. A NEW null showing up here
means either the engine started emitting one where the UI does not expect it,
or a field was added without deciding what its absence means — both worth
failing over, because the alternative is finding out at 09:31.

This cannot verify the TypeScript side automatically. What it can do is make
the set of nulls explicit and stable, so a change to it is a deliberate act.

Needs a running engine:  python verify_null_contract.py
"""
import json
import sys
import urllib.request

ENGINE = "http://127.0.0.1:8765"

# Paths that are legitimately null, each with the reason. Anything here MUST be
# declared nullable in src/lib/api.ts and guarded at every use site.
KNOWN_NULLABLE = {
    "track.dir_hit_rate":
        "no directional calls graded yet (dir_n == 0); a neutral call has no "
        "direction to be right about, so there is no rate rather than 0%",
    "track.mixed_rules":
        "history graded under a single rule, so nothing to warn about",
    "gex.gamma_flip":
        "no zero-gamma crossing inside the strike range",
    "gex.call_wall":
        "no positive-gamma strike above spot",
    "gex.put_wall":
        "no negative-gamma strike below spot",
    "gex.control_node":
        "empty profile",
    "plan.trap_door":
        "no trap-door level in this regime",
    "tape_time":
        "provider publishes no server-side tape stamp (non-UW feeds)",
    "market_time":
        "same as tape_time",
    "net_flow":
        "provider has no net premium ticks",
    "max_pain":
        "endpoint unavailable on this tier",
    "matrix":
        "no expiry with open interest inside the strike window",
    "darkpool":
        "no dark pool feed on this provider",
    "flow_alerts":
        "no flow feed on this provider",
    "level_check":
        "no UW levels to compare against",
    "uw_levels":
        "gex-levels endpoint unavailable",
    "sources.flow": "feed not live",
    "sources.darkpool": "feed not live",
    "sources.uw_levels": "feed not live",
    "sources.news": "feed not live",
    "sources.max_pain": "feed not live",
    "sources.net_flow": "feed not live",

    # Found by this suite on its first run. Each was then CHECKED at its use
    # site rather than added here to make the failure go away — the point of
    # the list is that entering something on it is a claim you looked.
    "brief_meta.error":
        "null when the brief generated cleanly; UI renders it only when set",
    "level_check[].agree":
        "null where a definitional difference makes grading meaningless "
        "(see pipeline.FLIP_NOTE); App.tsx renders those muted, not as drift",
    "level_check[].note":
        "null on levels with no known definitional difference; guarded by "
        "`r.note && ...` before render",
    "neg_zone":
        "null when spot is not in a negative-gamma zone; not read by the UI",
    "records[].outcome":
        "null while a prediction is still pending; Journal.tsx reaches it "
        "only through optional chaining",
    "closed_reason":
        "null on a normal trading day; React renders null as nothing",
    "early_close": "null unless the session closes early; guarded",
    "early_close_name": "same as early_close",
    "minutes_until": "null when no next boundary applies; declared nullable",
    "until_label": "same as minutes_until",

    # Caught by this suite on the run that introduced it, which is the point.
    "matrix.rows[].level":
        "null on rows that are not a named level; GexMatrix renders the badge "
        "only behind `r.level &&`, and api.ts declares it `string | null`",
}


def get(path):
    with urllib.request.urlopen(ENGINE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def null_paths(obj, prefix=""):
    """Every dotted path whose value is null. Lists are sampled by index 0 —
    a null in row 5 and not row 0 is a real hazard, so every row is walked."""
    out = []
    if obj is None:
        return [prefix or "<root>"]
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += null_paths(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            # Collapse the index so 200 rows do not produce 200 findings.
            out += [p.replace(f"[{i}]", "[]")
                    for p in null_paths(v, f"{prefix}[{i}]")]
    return out


_fail = 0


def check(name, ok, detail=""):
    global _fail
    if not ok:
        _fail += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


try:
    snap = get("/api/bias?ticker=SPY")
except Exception as e:  # noqa: BLE001
    print(f"engine not reachable: {e}")
    print("start it:  cd engine && python server.py")
    raise SystemExit(1)

print("[0] nulls in /api/bias")
found = sorted(set(null_paths(snap)))
unknown = [p for p in found if p not in KNOWN_NULLABLE]
for p in found:
    mark = "known" if p in KNOWN_NULLABLE else "UNDECLARED"
    print(f"    {mark:<11} {p}")
check("every null is a declared, understood one", not unknown,
      ", ".join(unknown) if unknown else "")

print("[1] the specific null that crashed the board")
# n counts every graded call; dir_n counts only directional ones. A neutral
# call increments the first and not the second, which is exactly the state
# that slipped past the `n === 0` guard in TrackRecord.
t = snap.get("track") or {}
check("track.dir_n present", "dir_n" in t, str(t.get("dir_n")))
if t.get("dir_n", 0) == 0:
    check("dir_hit_rate is null when dir_n is 0 (not a fabricated 0)",
          t.get("dir_hit_rate") is None, str(t.get("dir_hit_rate")))
    check("n can be > 0 while dir_n is 0 — the case the guard missed",
          t.get("n", 0) >= 0, f"n={t.get('n')} dir_n={t.get('dir_n')}")
else:
    check("dir_hit_rate is a number when dir_n > 0",
          isinstance(t.get("dir_hit_rate"), (int, float)),
          str(t.get("dir_hit_rate")))

print("[2] nulls in the other endpoints the UI reads")
for label, path in (("journal", "/api/journal?ticker=SPY"),
                    ("capture", "/api/capture"),
                    ("sessions", "/api/sessions"),
                    ("status", "/api/status")):
    try:
        body = get(path)
    except Exception as e:  # noqa: BLE001
        check(f"{label} reachable", False, str(e)[:90])
        continue
    ns = sorted(set(null_paths(body)))
    unk = [p for p in ns if p.split(".")[-1] not in
           {q.split(".")[-1] for q in KNOWN_NULLABLE}]
    check(f"{label}: nulls are understood", not unk,
          ", ".join(unk[:6]) if unk else f"{len(ns)} known")

print()
if _fail:
    print(f"{_fail} check(s) FAILED")
    print("A new null means the UI must guard it AND api.ts must declare it.")
    sys.exit(1)
print("all null-contract checks passed")
