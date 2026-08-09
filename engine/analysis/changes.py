"""
changes.py  --  what moved since the prior session's close.

WHY THIS IS THE PANEL THAT WAS MISSING. Every other surface on the board is a
snapshot: here is the flip, here are the walls, here is the lean. None of them
answer the question you actually have at 9:30, which is what is DIFFERENT from
when you last looked. A put wall at 758 means one thing on its own and quite
another if it was 742 yesterday and sixteen points of support just appeared.

THE BASELINE IS THE PRIOR SESSION'S CAPTURE, not the last snapshot in memory.
That distinction matters: the in-memory snapshot is whatever the engine last
built, which on a Saturday is Saturday's stale board and on a restart is
whatever got restored. The capture store holds one dated folder per trading day
written at a known time, which is the only baseline that means "yesterday".

ORDERED BY WHAT CHANGES A DECISION, not by field order. A regime flip is the
single most consequential thing that can happen overnight and leads regardless
of how small the number moved; ATM IV drifting a tenth is noise and sorts last
even when its percentage change is large.

NOTHING IS INVENTED. A field missing from either side is reported as unknown
rather than treated as zero — "the put wall went from nothing to 763" and "the
put wall was not recorded yesterday" are different statements and only one of
them is true.
"""
import datetime as dt
import json
import os

import config

CAPTURE_DIRNAME = "capture"

# How much a level must move before it is worth your attention. Below this it
# is strike-granularity noise and reporting it trains you to ignore the panel.
LEVEL_EPS = 0.01          # percent of spot
GEX_EPS = 0.05            # 5% change in net gamma
IV_EPS = 0.05             # 5% relative change in ATM IV


def _capture_root() -> str:
    return os.path.join(config.STORE_DIR, CAPTURE_DIRNAME)


def prior_capture(ticker: str, before: dt.date = None) -> tuple:
    """
    The newest captured snapshot STRICTLY BEFORE `before`, as (date, snapshot).

    Strictly before, because a capture written earlier today is not a baseline
    for today — diffing this morning against this morning reports nothing moved
    and looks like the panel is broken rather than like there is no comparison
    to make.
    """
    before = before or config.today()
    root = _capture_root()
    try:
        days = sorted(os.listdir(root), reverse=True)
    except OSError:
        return None, None

    for name in days:
        try:
            d = dt.date.fromisoformat(name)
        except ValueError:
            continue
        if d >= before:
            continue
        path = os.path.join(root, name, f"{ticker.upper()}_snapshot.json")
        try:
            with open(path, encoding="utf-8") as f:
                return d, json.load(f)
        except (OSError, ValueError):
            continue          # a corrupt day is skipped, not fatal
    return None, None


def _pct(a: float, b: float) -> float:
    return ((b - a) / abs(a) * 100) if a else 0.0


def _row(key, label, before, after, *, rank, kind="level", note=None,
         spot=None):
    """One reported change. `rank` is decision impact, not magnitude."""
    known = before is not None and after is not None
    move = (after - before) if known and isinstance(before, (int, float)) else None
    return {
        "key": key,
        "label": label,
        "before": before,
        "after": after,
        "move": round(move, 2) if move is not None else None,
        "pct": round(_pct(before, after), 2) if known and before else None,
        # As a share of spot — the only scale on which "the wall moved 5" is
        # comparable between SPY at 770 and a stock at 40.
        "of_spot": (round(abs(move) / spot * 100, 2)
                    if move is not None and spot else None),
        "rank": rank,
        "kind": kind,
        "note": note,
        "known": known,
    }


def build(current: dict, ticker: str = None, today: dt.date = None) -> dict:
    """
    Diff the live snapshot against the prior session's capture.

    Returns {available, baseline_date, age_days, changes[], note}. `available`
    is False with a reason rather than an empty list, because "nothing moved"
    and "we have nothing to compare against" look identical in a list and mean
    opposite things.
    """
    ticker = (ticker or current.get("ticker") or config.PRIMARY_TICKER).upper()
    # Exchange day: the baseline is "the prior TRADING session", and capture
    # folders are named in ET.
    today = today or config.today()
    base_date, base = prior_capture(ticker, before=today)

    if base is None:
        return {
            "available": False,
            "baseline_date": None,
            "changes": [],
            "note": ("No prior session captured for "
                     f"{ticker}. This panel compares against the recorder's "
                     "last trading-day snapshot; it starts working once one "
                     "exists."),
        }

    cg, bg = current.get("gex") or {}, base.get("gex") or {}
    spot = cg.get("spot")
    rows = []

    # 1. REGIME. The one change that inverts how you trade everything else, so
    #    it leads whenever it moved and is omitted entirely when it did not.
    if bg.get("regime") and cg.get("regime") and bg["regime"] != cg["regime"]:
        rows.append(_row(
            "regime", "Gamma regime", bg["regime"], cg["regime"],
            rank=0, kind="regime",
            note=("Dealer hedging has flipped from fading moves to amplifying "
                  "them." if cg["regime"] == "negative" else
                  "Dealer hedging has flipped from amplifying moves to fading "
                  "them.")))

    # 2. LEVELS. Where the structure moved to.
    for key, label, rank in (("gamma_flip", "Gamma flip", 1),
                             ("put_wall", "Put wall", 2),
                             ("call_wall", "Call wall", 2),
                             ("control_node", "Magnet", 4)):
        b, c = bg.get(key), cg.get(key)
        if b is None and c is None:
            continue
        if b is None or c is None:
            rows.append(_row(key, label, b, c, rank=rank,
                             note="Present on only one side — not a move."))
            continue
        if spot and abs(c - b) / spot * 100 >= LEVEL_EPS:
            rows.append(_row(key, label, b, c, rank=rank, spot=spot))

    # 3. POSITIONING SIZE.
    b, c = bg.get("net_gex"), cg.get("net_gex")
    if b is not None and c is not None and b and abs(_pct(b, c)) >= GEX_EPS * 100:
        rows.append(_row("net_gex", "Net gamma", round(b), round(c),
                         rank=3, kind="size"))

    b, c = bg.get("put_call_ratio"), cg.get("put_call_ratio")
    if b is not None and c is not None and b and abs(_pct(b, c)) >= 5:
        rows.append(_row("put_call_ratio", "Put/call OI",
                         round(b, 2), round(c, 2), rank=5, kind="size"))

    # 4. VOL. Sorts last: a tenth of a vol point is not a decision.
    b, c = bg.get("atm_iv"), cg.get("atm_iv")
    if b is not None and c is not None and b and abs(_pct(b, c)) >= IV_EPS * 100:
        rows.append(_row("atm_iv", "ATM IV", round(b * 100, 1),
                         round(c * 100, 1), rank=6, kind="vol"))

    # 5. THE CALL ITSELF. Last because it is downstream of everything above —
    #    if the lean changed, the reason is already listed higher up.
    bb, cb = (base.get("bias") or {}), (current.get("bias") or {})
    if bb.get("label") and cb.get("label") and bb["label"] != cb["label"]:
        rows.append(_row("bias", "Call", bb["label"], cb["label"],
                         rank=7, kind="call"))

    rows.sort(key=lambda r: r["rank"])
    age = (today - base_date).days
    return {
        "available": True,
        "baseline_date": base_date.isoformat(),
        "baseline_at": base.get("generated_at"),
        "age_days": age,
        "changes": rows,
        "note": (None if rows else
                 "Nothing moved enough to report. The structure is where it "
                 "was at the last capture."),
        # Surfaced so a stale baseline cannot read as "yesterday". Comparing
        # Monday against the previous Tuesday is a legitimate thing to do and
        # an illegitimate thing to do silently.
        "stale_baseline": age > 4,
    }
