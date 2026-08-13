"""
verify_contract.py  --  the TypeScript types must match what the engine sends.

WHY THIS EXISTS. The worst UI failure this app has had was a blank screen, and
its cause was a type that lied: api.ts declared `dir_hit_rate: number` while
the engine sent null. tsc passed, because tsc checks the code against the
DECLARATION, never against the server. The declaration was the bug, so the one
tool that could have caught it was the one thing that agreed with it.

verify_ui checks fields it was told to look for. verify_null_contract checks
that nulls are declared. Neither does the mechanical thing: walk EVERY field of
EVERY interface against a live payload. That is what this does.

It reports four kinds of drift:

  MISSING    a non-optional field the engine never sent
  NULL       a field arriving null whose type has no `| null`
  TYPE       declared number, arrived string — the Unusual Whales trap, where
             the upstream serialises numerics as strings and one leaking
             through turns arithmetic into concatenation or a .toFixed crash
  EXTRA      the engine sends a field no interface declares (informational:
             either dead weight on the wire or a panel nobody typed)

Needs the engine running.

    python verify_contract.py
"""
import json
import re
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"
API_TS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "src", "lib", "api.ts")

FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------------------
# a very small TypeScript reader
# ---------------------------------------------------------------------------
# Only as much as this file's own style needs. Anything it cannot confidently
# parse is SKIPPED rather than guessed at — a checker that invents findings
# gets muted, and a muted checker is worth nothing.
def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def split_fields(body: str) -> list:
    """(name, optional, type_text) for each field at depth 0 of `body`."""
    out, depth, buf = [], 0, ""
    for ch in body:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == ";" and depth == 0:
            out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf)

    fields = []
    for raw in out:
        raw = raw.strip()
        if not raw or ":" not in raw:
            continue
        head, _, typ = raw.partition(":")
        head = head.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\??", head):
            continue                     # index signatures etc — skip
        fields.append((head.rstrip("?"), head.endswith("?"), typ.strip()))
    return fields


def parse_interfaces(src: str) -> dict:
    src = strip_comments(src)
    out = {}
    for m in re.finditer(r"export interface (\w+)\s*\{", src):
        name, i, depth = m.group(1), m.end(), 1
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        out[name] = split_fields(src[m.end():i - 1])
    return out


def parse_api_map(src: str) -> list:
    """(path, InterfaceName) for every GET the UI makes."""
    src = strip_comments(src)
    m = re.search(r"export const api = \{(.*?)\n\};", src, flags=re.S)
    if not m:
        return []
    body = m.group(1)
    out = []
    for call in re.finditer(r"req<(\w+)>\(\s*([`\"'])", body):
        iface, quote = call.group(1), call.group(2)
        # Scan to the MATCHING delimiter rather than to the first quote of any
        # kind. A template literal's `${refresh ? "?refresh=true" : ""}` holds
        # double quotes, and a naive character class stopped dead on them —
        # which truncated the calendar and news paths into something
        # unrequestable, so both were skipped without a word.
        i, path = call.end(), ""
        while i < len(body) and body[i] != quote:
            path += body[i]
            i += 1
        # POST routes take a method option AFTER the path, so the lookahead has
        # to reach past it — but it must STOP at the next req<...> or it reads
        # the following entry's options and filters out a perfectly good GET.
        # That silently dropped /api/changed, /api/calendar and /api/news:
        # three endpoints the checker reported nothing about because it never
        # asked them. A checker that quietly tests less than it claims is worse
        # than one that fails.
        tail = body[i:]
        nxt = tail.find("req<")
        if nxt != -1:
            tail = tail[:nxt]
        if re.search(r"method:\s*\"(POST|PUT|DELETE)\"", tail):
            continue
        out.append((path, iface))
    return out


# ---------------------------------------------------------------------------
# type matching
# ---------------------------------------------------------------------------
PRIMS = {"number": (int, float), "string": str, "boolean": bool}


def split_union(t: str) -> list:
    """
    Split on `|` at depth 0 ONLY.

    Splitting the raw string shreds an inline object type, because
    `{ model: string | null }` contains a pipe that belongs to a nested field.
    That made every child of brief_meta look undeclared — a checker inventing
    findings, which is the thing this file's own docstring says gets it muted.
    """
    parts, depth, buf = [], 0, ""
    for ch in t:
        if ch in "{[(<":
            depth += 1
        elif ch in "}])>":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def allows_null(t: str) -> bool:
    return "null" in split_union(t)


def base_types(t: str) -> list:
    return [p for p in split_union(t) if p not in ("null", "undefined")]


def walk(value, typ: str, path: str, ifaces: dict, problems: list, depth=0):
    if depth > 6:
        return
    typ = typ.strip()

    if value is None:
        if not allows_null(typ):
            problems.append(("NULL", path, typ, "null"))
        return

    parts = base_types(typ)
    if not parts:
        return

    # A union of anything with bare `string` accepts any string; skip.
    if len(parts) > 1 and "string" in parts:
        return

    t = parts[0]

    # arrays
    arr = re.fullmatch(r"Array<(.+)>", t) or re.fullmatch(r"(.+)\[\]", t)
    if arr:
        if not isinstance(value, list):
            problems.append(("TYPE", path, t, type(value).__name__))
            return
        for i, v in enumerate(value[:3]):        # 3 is enough to find drift
            walk(v, arr.group(1), f"{path}[{i}]", ifaces, problems, depth + 1)
        return

    if t.startswith("Record<") or t == "unknown" or t == "any":
        return

    if t in PRIMS:
        want = PRIMS[t]
        # bool is a subclass of int in Python; a JSON true is not a number.
        if t == "number" and isinstance(value, bool):
            problems.append(("TYPE", path, t, "boolean"))
        elif not isinstance(value, want):
            problems.append(("TYPE", path, t, type(value).__name__))
        return

    if t.startswith("{"):
        fields = split_fields(t[1:t.rfind("}")])
    elif t in ifaces:
        fields = ifaces[t]
    else:
        return                                   # literal union or unknown

    if not isinstance(value, dict):
        problems.append(("TYPE", path, t, type(value).__name__))
        return

    declared = set()
    for name, optional, ftyp in fields:
        declared.add(name)
        if name not in value:
            if not optional:
                problems.append(("MISSING", f"{path}.{name}", ftyp, "absent"))
            continue
        walk(value[name], ftyp, f"{path}.{name}", ifaces, problems, depth + 1)

    for k in value:
        if k not in declared:
            problems.append(("EXTRA", f"{path}.{k}", "-", type(value[k]).__name__))


def get(path: str) -> dict:
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


# EXTRA findings that are known and deliberate. Everything else is reported.
# Entering something here is a claim that you looked at it.
EXTRA_OK = {
    # Diagnostics the UI does not read but which are worth having on the wire
    # when something looks wrong and you are reading raw JSON at 09:35.
    ".restored", ".restored_from", ".restored_age_days", ".schema",
    ".uw_socket",                    # socket entitlement state; status only
    ".expiries_loaded", ".expiry_labels",   # which expiries built the board
    ".neg_zone",                     # its note already rides a bias signal
    ".bias.mix", ".records[].mix",   # mix is read in aggregate on TrackRecord
    ".records[].context.expected_move.dte",   # the horizon; band is what shows
    "._wall",                        # server-side build timing, for profiling
    # Written into the Obsidian note's frontmatter, not read by any panel: the
    # journal is where a decision gets queried later, the panel only shows now.
    ".status", ".score", ".mix",
    # The full ISO timestamp. WeekAhead renders `date` and `time` separately,
    # which are derived from this and already exchange-local.
    ".headline.at", ".high_impact[].at", ".upcoming_earnings[].at",
    ".days[].events[].at",
}


def normalise(path: str) -> str:
    """Collapse array indices so one entry covers every element."""
    return re.sub(r"\[\d+\]", "[]", path)


def main():
    try:
        get("/api/health")
    except (urllib.error.URLError, OSError) as e:
        print(f"engine not reachable: {e}\nstart it:  cd engine && python server.py")
        return 1

    src = open(os.path.normpath(API_TS), encoding="utf-8").read()
    ifaces = parse_interfaces(src)
    routes = parse_api_map(src)
    print(f"parsed {len(ifaces)} interfaces, {len(routes)} GET routes from api.ts")
    check("interfaces were parsed", len(ifaces) >= 25, str(len(ifaces)))
    check("routes were parsed", len(routes) >= 8, str(len(routes)))

    ticker = "SPY"
    for path, iface in routes:
        # Fill the template literal in the same shape the UI sends.
        p = (path.replace("${encodeURIComponent(ticker)}", ticker)
                 .replace("${encodeURIComponent(interval)}", "5m")
                 .replace("${days}", "5").replace("${limit}", "50")
                 .replace("${sort}", "relevance")
                 # The trigger route takes a REQUIRED price; stripping it the
                 # way the optional ternaries are stripped would send an empty
                 # value and get a 422 that looks like a broken endpoint.
                 .replace("${price}", "767").replace("${dir}", "long"))
        # Any remaining ${...} is a ternary that adds an optional query flag;
        # dropping it gives the default request the UI makes on load, which is
        # the one worth checking.
        p = re.sub(r"\$\{[^}]*\}", "", p)
        if "${" in p:
            continue
        print(f"[{iface}] {p}")
        try:
            payload = get(p)
        except Exception as e:                        # noqa: BLE001
            check(f"{iface}: endpoint answered", False, f"{type(e).__name__}: {e}")
            continue

        problems = []
        walk(payload, iface, "", ifaces, problems)
        hard = [x for x in problems if x[0] != "EXTRA"]
        extra = [x for x in problems if x[0] == "EXTRA"
                 and normalise(x[1]) not in EXTRA_OK]

        for kind, where, want, got in hard[:12]:
            print(f"       {kind:8s} {where:44s} declared {want!r}, got {got}")
        check(f"{iface}: every declared field matches the payload",
              not hard, f"{len(hard)} mismatch(es)")
        if extra:
            print(f"       (undeclared on the wire: "
                  f"{', '.join(sorted(x[1] for x in extra)[:8])})")

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:6])}")
        return 1
    print("all contract checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
