"""
tracker.py  --  The honesty engine. Records each morning's bias, grades it
against what price actually did, and computes a running hit-rate.

GRADING (the choice that matters, documented so you own it):
  We grade the lean against the SAME session's open -> close move.
    - LONG correct  if close > open by more than the band
    - SHORT correct if close < open by more than the band
    - NEUTRAL correct if |open->close| stayed inside the band (a range day)
  open->close is clean, free, and reproducible. If you'd rather grade against
  the morning only (e.g. open -> 11:30), swap the OHLC source — the logic here
  doesn't change.

Why this exists: it's the difference between "I think the bias works" and
"the bias is 57% directional over 40 trades." Small samples are noise — the
panel shows sample size on purpose. Don't trust a hot streak of 6.
"""
import datetime as dt
import random

import config
import store


def predicted_dir(label: str) -> str:
    if "LONG" in label:
        return "up"
    if "SHORT" in label:
        return "down"
    return "flat"


def key(ticker: str, date: str) -> str:
    """Record key. MUST include the ticker: keying on date alone meant flipping
    the selector from SPY to NVDA overwrote the morning's SPY call, silently
    corrupting the hit rate that the whole track-record panel exists to report."""
    return f"{ticker.upper()}|{date}"


def record_prediction(snap: dict) -> dict:
    """
    Save today's prediction once. Never overwrites (keeps the morning call).

    Skips non-trading days. Any refresh used to write a record, so opening the
    dashboard on a Saturday filed a prediction for a session that never
    happens: it can never be graded, so it sits 'pending' forever and pads the
    count. Only weekdays get recorded.
    """
    recs = store.load()
    date = snap["generated_at"][:10]
    if dt.date.fromisoformat(date).weekday() >= 5:
        return recs
    k = key(snap["ticker"], date)
    if k in recs:
        return recs
    g, b = snap["gex"], snap["bias"]
    recs[k] = {
        "date": date,
        "ticker": snap["ticker"],
        "bias": b["label"],
        "score": b["score"],
        "predicted_dir": predicted_dir(b["label"]),
        "spot": g["spot"],
        "outcome": None,
        # WHY the call was made, captured at call time.
        #
        # The record used to hold only bias/score/spot — enough to score a call
        # and useless for learning from it. Months later you could see that a
        # SHORT LEAN lost, with no way to ask whether the reasoning was wrong or
        # the read was right and the tape disagreed. None of this is
        # reconstructable afterwards: the chain that produced these levels is
        # gone by the next session. Captured now or not at all.
        "context": {
            "regime": g.get("regime"),
            "net_gex": g.get("net_gex"),
            "gamma_flip": g.get("gamma_flip"),
            "call_wall": g.get("call_wall"),
            "put_wall": g.get("put_wall"),
            "control_node": g.get("control_node"),
            "atm_iv": g.get("atm_iv"),
            "put_call_ratio": g.get("put_call_ratio"),
            "expected_move": snap.get("expected_move"),
            "conviction": b.get("conviction"),
            "summary": b.get("summary"),
            "signals": [{"name": s.get("name"), "lean": s.get("lean"),
                         "weight": s.get("weight"), "reason": s.get("reason")}
                        for s in b.get("signals", [])],
            "smt": (snap.get("smt") or {}).get("note"),
            "news": (snap.get("news") or {}).get("headline"),
            "news_level": (snap.get("news") or {}).get("level"),
            "provider": "mock" if snap.get("mock") else snap.get("provider"),
        },
        # Yours to write. Never touched by grading.
        "note": "",
    }
    store.save(recs)
    return recs


def set_note(ticker: str, date: str, note: str) -> bool:
    """Attach a note to one call. False if there's no such record."""
    recs = store.load()
    k = key(ticker, date)
    if k not in recs:
        return False
    recs[k]["note"] = note
    store.save(recs)
    return True


def journal(ticker: str = None, limit: int = 120) -> list:
    """
    Full records, newest first — the graded call plus what drove it.

    Deliberately separate from compute_stats: stats answer "is this working",
    the journal answers "why did this one go the way it did".
    """
    recs = store.load()
    vals = list(recs.values())
    if ticker:
        vals = [r for r in vals if r.get("ticker", "").upper() == ticker.upper()]
    vals.sort(key=lambda r: r.get("date", ""), reverse=True)
    return vals[:limit]


def grade_record(rec: dict, ohlc: dict, band: float = None) -> dict | None:
    """
    Grade one call over the window actually traded (open -> GRADE_EXIT_TIME).

    `rule` rides along on every outcome. Without it, changing the exit time or
    the band later would blend results measured two different ways into a
    single hit rate — and a hit rate that mixes rules describes nothing.
    """
    ticker = rec.get("ticker", "")
    band = config.grade_band_for(ticker) if band is None else band
    o = ohlc.get("open")
    c = ohlc.get("exit", ohlc.get("close"))   # tolerate the old key
    if not o or c is None:
        return None
    move_pct = 100 * (c - o) / o
    actual = "up" if move_pct > band else "down" if move_pct < -band else "flat"
    # The rule string is composed here, where the ticker's band is known.
    # get_ohlc only reports HOW it got the price; any caveat rides along.
    note = ohlc.get("note", "")
    return {"open": round(o, 2), "exit": round(c, 2), "move_pct": round(move_pct, 2),
            "actual_dir": actual, "correct": rec["predicted_dir"] == actual,
            "rule": config.grade_rule_for(ticker) + (f" {note}" if note else "")}


def grade_pending(get_ohlc) -> dict:
    """Grade any past, ungraded predictions. `get_ohlc(ticker, date)` -> ohlc|None."""
    recs = store.load()
    today = dt.date.today().isoformat()
    changed = False
    for rec in recs.values():
        date = rec["date"]
        if rec.get("outcome") is None and date < today:
            ohlc = get_ohlc(rec["ticker"], date)
            if ohlc:
                graded = grade_record(rec, ohlc)
                if graded:
                    rec["outcome"] = graded
                    changed = True
    if changed:
        store.save(recs)
    return recs


def compute_stats(recs: dict, ticker: str = None) -> dict:
    """Stats for one ticker (or all, if `ticker` is None). Pooling tickers
    would report a blended hit rate that describes no instrument you trade."""
    vals = list(recs.values())
    if ticker:
        vals = [r for r in vals if r.get("ticker", "").upper() == ticker.upper()]
    graded = [r for r in vals if r.get("outcome")]
    n = len(graded)
    wins = sum(1 for r in graded if r["outcome"]["correct"])

    by = {}
    for r in graded:
        b = "LONG" if "LONG" in r["bias"] else "SHORT" if "SHORT" in r["bias"] else "NEUTRAL"
        d = by.setdefault(b, {"n": 0, "wins": 0})
        d["n"] += 1
        d["wins"] += 1 if r["outcome"]["correct"] else 0

    dir_graded = [r for r in graded if r["predicted_dir"] != "flat"]
    dir_wins = sum(1 for r in dir_graded if r["outcome"]["correct"])
    recent = sorted(graded, key=lambda r: r["date"])[-15:]

    return {
        "n": n, "wins": wins, "losses": n - wins,
        "hit_rate": round(100 * wins / n, 1) if n else None,
        "dir_n": len(dir_graded),
        "dir_hit_rate": round(100 * dir_wins / len(dir_graded), 1) if dir_graded else None,
        "by_type": {k: {"n": v["n"], "wins": v["wins"],
                        "rate": round(100 * v["wins"] / v["n"]) if v["n"] else None}
                    for k, v in by.items()},
        "recent": [{"date": r["date"], "bias": r["bias"], "predicted": r["predicted_dir"],
                    "actual": r["outcome"]["actual_dir"], "correct": r["outcome"]["correct"],
                    "move_pct": r["outcome"]["move_pct"]} for r in recent],
        # pending must respect the same ticker filter as everything else above,
        # or a SPY panel reports NVDA's ungraded calls as its own
        "pending": sum(1 for r in vals if r.get("outcome") is None),
        "ticker": ticker,
        # per-ticker, matching how these records were actually graded
        "rule": config.grade_rule_for(ticker) if ticker else config.GRADE_RULE,
        # Surfaced so a hit rate built from two different grading rules can't
        # be read as if it were one measurement.
        "mixed_rules": sorted({r["outcome"].get("rule", "?") for r in graded}) if len(
            {r["outcome"].get("rule", "?") for r in graded}) > 1 else None,
    }


def ensure_seeded() -> None:
    """MOCK ONLY: populate ~22 days of believable history so the panel isn't
    empty in the demo. Deterministic. Real (live) mode starts empty and builds
    its own record over time."""
    if not config.USE_MOCK_DATA:
        return
    if store.load():
        return
    biases = ["LONG LEAN", "SHORT LEAN", "NEUTRAL / RANGE"]
    out = {}
    for ticker in config.TICKERS:
        random.seed(hash(ticker) % 10_000)   # per-ticker but reproducible
        count, d = 0, dt.date.today() - dt.timedelta(days=1)
        base = 100 + (hash(ticker) % 600)
        while count < 22:
            if d.weekday() < 5:
                label = random.choices(biases, weights=[4, 4, 2])[0]
                pdir = predicted_dir(label)
                o = base + random.uniform(-6, 6)
                agree = random.random() < 0.58   # ~58% realistic-ish edge
                # Move sizes match the open->12:00 window, not open->close:
                # measured median |move| over 60 SPY sessions is ~0.28% with a
                # p90 near 0.62%. Seeding full-day magnitudes here would make
                # the demo panel look nothing like what live grading produces.
                if pdir == "flat":
                    mv = random.uniform(-0.14, 0.14) if agree else random.choice([-1, 1]) * random.uniform(0.25, 0.9)
                else:
                    sign = 1 if pdir == "up" else -1
                    mv = sign * random.uniform(0.2, 0.95) if agree else -sign * random.uniform(0.2, 0.8)
                c = o * (1 + mv / 100)
                band = config.grade_band_for(ticker)   # per-ticker, as live grading is
                actual = "up" if mv > band else "down" if mv < -band else "flat"
                out[key(ticker, d.isoformat())] = {
                    "date": d.isoformat(), "ticker": ticker, "bias": label,
                    "score": round(random.uniform(-4, 4), 1), "predicted_dir": pdir, "spot": round(o, 2),
                    "outcome": {"open": round(o, 2), "exit": round(c, 2), "move_pct": round(mv, 2),
                                "actual_dir": actual, "correct": pdir == actual,
                                "rule": config.grade_rule_for(ticker)},
                }
                count += 1
            d -= dt.timedelta(days=1)
    store.save(out)
