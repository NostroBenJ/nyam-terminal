"""
shadow.py  --  the bot, with the wires to the broker deliberately not attached.

It does the whole job: reads the board, picks a playbook, names a contract,
sizes it, applies every risk gate, and writes the decision down. Then it stops.

THIS IS THE EVIDENCE MACHINE, and it is the point of phase two. Through
2026-08-12 the bias engine has 1 hit in 8 graded and 0 of 2 directional, which
is not a sample you can size a position from. What is missing is not conviction
but data — and specifically these, none of which the current record answers:

    how often does a setup appear at all?
    when one appears, what does the round trip cost?
    does the read fail, or does the expression fail?

Those come from a recorder, cheaply, in shadow. They do not come from fills.

It is also ~80% of what live trading needs, so nothing built here is thrown
away when and if it earns promotion: the only missing piece is the adapter that
turns a staged intent into an order, plus a human clicking it.

Nothing in this module places or stages an order.
"""
import datetime as dt

import config
from strategy import decision_log, playbook, risk

#: Last state key per ticker, so a refusal that has not changed is not written
#: four hundred times a session.
_last: dict = {}

#: Today's count. THIS IS THE HEADLINE NUMBER OF PHASE TWO — how often a setup
#: appears at all, which nothing in the app has ever been able to answer.
#: Counted from run(), never from evaluate(), so opening a panel cannot inflate
#: it: a number that grows when you look at it is not a measurement.
_tally: dict = {"date": None, "evaluations": 0, "setups": 0, "actionable": 0,
                "blocked": 0, "first_setup_at": None, "last_at": None}


def tally() -> dict:
    """Today's counts, rolled over on the exchange day."""
    today = config.today().isoformat()
    if _tally["date"] != today:
        _tally.update({"date": today, "evaluations": 0, "setups": 0,
                       "actionable": 0, "blocked": 0, "first_setup_at": None,
                       "last_at": None})
    return dict(_tally)


def _count(d: dict) -> None:
    tally()                                   # rolls the day if it has turned
    _tally["evaluations"] += 1
    _tally["last_at"] = (d.get("at") or "")[11:16]
    if d.get("available"):
        _tally["setups"] += 1
        if not _tally["first_setup_at"]:
            _tally["first_setup_at"] = _tally["last_at"]
        if d.get("actionable"):
            _tally["actionable"] += 1
        else:
            _tally["blocked"] += 1


def evaluate(snap: dict, *, expiries: list = None, now: dt.datetime = None,
             day_pnl: float = 0.0, open_positions: int = 0) -> dict:
    """
    The full decision for this instant. Pure — writes nothing.

    A refusal is a decision and comes back fully formed, because "no setup
    today" and "a setup the risk layer vetoed" are different facts and only one
    of them says anything about the signal.
    """
    now = now or dt.datetime.now(config.TZ)
    gex = snap.get("gex") or {}
    bias = snap.get("bias") or {}

    out = {
        "at": now.isoformat(timespec="seconds"),
        "ticker": snap.get("ticker") or config.PRIMARY_TICKER,
        "status": "shadow",
        "regime": gex.get("regime"),
        "spot": gex.get("spot"),
        "bias": bias.get("label"),
        "score": bias.get("score"),
        "mix": bias.get("mix"),
    }

    setup = playbook.build(snap, expiries=expiries)
    out["available"] = bool(setup.get("available"))
    # Set on EVERY path, never merely absent. A missing field reads as falsy in
    # both Python and JavaScript, which is the right answer for the wrong
    # reason — and it is exactly the shape of the `dir_hit_rate` that once
    # blanked the whole screen.
    out["actionable"] = False
    if not setup.get("available"):
        out["reason"] = setup.get("reason")
        # A setup that got as far as being described and then failed on the
        # contract is worth keeping — it is the strategy working and the market
        # refusing, which is a different result from having no idea.
        if setup.get("proposed"):
            out["proposed"] = setup["proposed"]
        return out

    for k in ("playbook", "direction", "structure", "entry", "entry_why",
              "target", "target_why", "stop", "stop_why", "invalidation",
              "risk_points", "reward_points", "board", "contract", "notes"):
        if k in setup:
            out[k] = setup[k]

    out["sizing"] = risk.size(setup)
    g = risk.gate(setup, snap=snap, now=now, day_pnl=day_pnl,
                  open_positions=open_positions)
    out["blocks"], out["warnings"] = g["blocks"], g["warnings"]
    # `actionable` means: a real setup, sized, with nothing blocking it. It is
    # NOT an instruction — no order exists anywhere in this codebase.
    out["actionable"] = bool(out["sizing"].get("ok") and g["ok"])
    out["reason"] = ("Setup stands." if out["actionable"]
                     else "; ".join(g["blocks"]) or out["sizing"].get("reason", ""))
    return out


def record(decision: dict, *, force: bool = False) -> dict:
    """
    Persist, but only when the situation has actually changed.

    Deduped on a number-stripped state key: the mid-range refusal carries a
    distance that moves every tick, and logging the raw reason would bury the
    two lines a day that matter under four hundred that do not.
    """
    t = decision.get("ticker") or "?"
    key = decision_log.state_key(decision)
    if not force and _last.get(t) == key:
        return {"written": False, "why": "unchanged since the last evaluation"}
    _last[t] = key

    written = {"written": True, "day": None, "note": None}
    try:
        written["day"] = decision_log.append_day(decision)
        if decision.get("available"):
            written["note"] = decision_log.write_decision(decision)
    except OSError as e:
        # A journal that cannot write must never stop the board. The decision
        # already happened; losing the note is a smaller failure than losing
        # the engine.
        return {"written": False, "why": f"{type(e).__name__}: {e}"}
    return written


def run(snap: dict, **kw) -> dict:
    """evaluate + record. What the scheduler calls."""
    d = evaluate(snap, **{k: v for k, v in kw.items()
                          if k in ("expiries", "now", "day_pnl", "open_positions")})
    _count(d)
    d["journal"] = record(d, force=kw.get("force", False))
    return d
