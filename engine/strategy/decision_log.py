"""
decision_log.py  --  every decision as an Obsidian note you can query.

WHY OBSIDIAN AND NOT ANOTHER JSON FILE. records.json answers "what was the hit
rate". It cannot answer the question that finds an edge:

    every fade at the call wall in positive gamma, and what happened

That is a query over structured, linked history, which is what Obsidian's
frontmatter plus Dataview is for and what a flat file is not. It doubles as the
bot's audit trail: when something surprising gets staged, you read the note
rather than grepping.

A NO-TRADE IS A DECISION AND IS RECORDED AS ONE. Whether a setup appears at all
is the first thing worth knowing about this strategy, and it is invisible if
only fills are written down. The daily note carries the running log of every
state change including the refusals; a full note is written only when there was
an actual setup, so the vault does not fill with "mid-range" four hundred times
a session.

Nothing here places or stages an order. It writes files.
"""
import datetime as dt
import os
import re
import tempfile

import config

DIRNAME = "NYAM Decisions"


def _dir() -> str:
    return os.path.join(config.OBSIDIAN_VAULT, DIRNAME)


def _write(path: str, text: str) -> str:
    """Atomic, utf-8, unique temp name. Same rules as every other writer here."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
        return path
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _yaml(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace('"', "'")
    return f'"{s}"' if any(ch in s for ch in ":#[]{},") else s


def note_name(d: dict) -> str:
    when = d.get("at") or ""
    stamp = when[11:16].replace(":", "") if len(when) >= 16 else "0000"
    bits = [when[:10] or "unknown", stamp, d.get("ticker") or "?",
            d.get("playbook") or "notrade", d.get("direction") or ""]
    return " ".join(b for b in bits if b).strip() + ".md"


def write_decision(d: dict) -> str:
    """
    One note per decision that had a setup. Frontmatter is Dataview-queryable —
    the field names are the query surface, so they are flat and stable rather
    than nested.
    """
    c = d.get("contract") or {}
    s = d.get("sizing") or {}
    b = d.get("board") or {}
    fm = {
        "date": d.get("at", "")[:10],
        "time": d.get("at", "")[11:16],
        "ticker": d.get("ticker"),
        "status": d.get("status", "shadow"),
        "playbook": d.get("playbook"),
        "direction": d.get("direction"),
        "structure": d.get("structure"),
        "regime": d.get("regime"),
        "bias": d.get("bias"),
        "score": d.get("score"),
        "entry": d.get("entry"),
        "target": d.get("target"),
        "stop": d.get("stop"),
        "reward_points": d.get("reward_points"),
        "risk_points": d.get("risk_points"),
        "contract": c.get("symbol"),
        "strike": c.get("strike"),
        "dte": c.get("dte"),
        "delta": c.get("delta"),
        "mid": c.get("mid"),
        "round_trip_pct": c.get("round_trip_pct"),
        "contracts": s.get("contracts"),
        "premium_total": s.get("premium_total"),
        "est_loss_at_stop": s.get("est_loss_at_stop"),
        "max_loss": s.get("max_loss"),
        "board_verdict": b.get("verdict"),
        "blocked": bool(d.get("blocks")),
        "outcome": d.get("outcome"),
        "tags": "[nyam, decision, " + (d.get("status") or "shadow") + "]",
    }
    lines = ["---"]
    lines += [f"{k}: {_yaml(v)}" for k, v in fm.items() if v not in (None, "")]
    lines.append("---")
    lines.append("")
    lines.append(f"# {d.get('playbook', 'no-trade')} {d.get('direction', '')} "
                 f"— {d.get('ticker')} {d.get('at', '')[:16]}")
    lines.append("")

    if d.get("blocks"):
        lines.append("> [!warning] Blocked by risk")
        for x in d["blocks"]:
            lines.append(f"> - {x}")
        lines.append("")
    if d.get("warnings"):
        lines.append("> [!note] Warnings")
        for x in d["warnings"]:
            lines.append(f"> - {x}")
        lines.append("")

    lines.append("## The read")
    lines.append(f"- **Regime**: {d.get('regime')} gamma, bias {d.get('bias')} "
                 f"({d.get('score')})")
    if b.get("headline"):
        lines.append(f"- **Board**: {b.get('verdict')} — {b['headline']}")
    for k, why in (("entry", "entry_why"), ("target", "target_why"),
                   ("stop", "stop_why")):
        if d.get(k) is not None:
            lines.append(f"- **{k.title()}** {d[k]} — {d.get(why, '')}")
    if d.get("invalidation"):
        lines.append(f"- **Invalidated if**: {d['invalidation']}")
    lines.append("")

    if c:
        lines.append("## The expression")
        lines.append(f"- `{c.get('symbol')}` — {c.get('strike')} "
                     f"{c.get('right')}, {c.get('dte')} DTE, delta "
                     f"{c.get('delta')}")
        lines.append(f"- mid {c.get('mid')}, round trip "
                     f"{c.get('round_trip_pct')}% of mid")
        if s.get("ok"):
            lines.append(f"- **{s.get('contracts')} contract(s)** — "
                         f"${s.get('premium_total')} premium, "
                         f"${s.get('est_loss_at_stop')} estimated at the stop, "
                         f"${s.get('max_loss')} max loss")
            lines.append(f"- bound by the {s.get('binding_constraint')}")
        elif s.get("reason"):
            lines.append(f"- **Not sized**: {s['reason']}")
        lines.append("")

    lines.append("## Outcome")
    lines.append("- [ ] Taken?")
    lines.append("- [ ] What actually happened, and was the READ wrong or the "
                 "EXPRESSION wrong?")
    lines.append("")
    lines.append(f"[[{d.get('at', '')[:10]}|Day]]")
    lines.append("")
    return _write(os.path.join(_dir(), note_name(d)), "\n".join(lines))


def append_day(d: dict) -> str:
    """
    The running log for a session: every state change, refusals included.

    Rewritten whole each time rather than appended to, because an append that
    races leaves a half-line and this file is read by a human at the end of a
    day they cannot re-run.
    """
    day = d.get("at", "")[:10] or config.today().isoformat()
    path = os.path.join(_dir(), f"{day}.md")
    entry = f"- **{d.get('at','')[11:16]}** "
    if d.get("_marker"):
        entry += f"`{d.get('reason', '')}`"
    elif d.get("available"):
        entry += (f"**{d.get('playbook')} {d.get('direction')}** @ "
                  f"{d.get('entry')} → {d.get('target')} "
                  f"(stop {d.get('stop')})")
        if d.get("blocks"):
            entry += f" — *blocked: {d['blocks'][0]}*"
        elif (d.get("sizing") or {}).get("ok"):
            entry += f" — {d['sizing']['contracts']}x {(d.get('contract') or {}).get('symbol')}"
        entry += f"  [[{note_name(d)[:-3]}|note]]"
    else:
        entry += f"no trade — {d.get('reason', '')}"

    head = [f"---", f"date: {day}", "tags: [nyam, daylog]", "---", "",
            f"# Decisions — {day}", "",
            "Every state change, refusals included. How often a setup appears "
            "at all is the first thing worth knowing about this strategy, and "
            "it is invisible if only fills get written down.", ""]
    prior = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                prior = [ln for ln in f.read().splitlines() if ln.startswith("- **")]
        except OSError:
            prior = []
    return _write(path, "\n".join(head + prior + [entry]) + "\n")


def mark(text: str, now: dt.datetime = None) -> str:
    """
    A session marker in the day log — recorder up, recorder down.

    WITHOUT THESE, A GAP IS UNREADABLE. The recorder only runs while the engine
    is up, so a quiet stretch in the log means either "nothing changed" or
    "nobody was watching", and those are opposite facts. A silent hour is
    evidence about the market only if something was awake to observe it.
    """
    now = now or dt.datetime.now(config.TZ)
    return append_day({"at": now.isoformat(timespec="seconds"),
                       "available": False, "reason": text, "_marker": True})


_SHAPE = re.compile(r"[\d.]+")


def state_key(d: dict) -> str:
    """
    What counts as "the same situation".

    Numbers are stripped, because the mid-range refusal carries a distance that
    changes every tick — logging on the raw reason would write four hundred
    near-identical lines a session and bury the two that matter.
    """
    core = f"{d.get('available')}|{d.get('playbook')}|{d.get('direction')}|"
    return core + _SHAPE.sub("#", d.get("reason") or "")
