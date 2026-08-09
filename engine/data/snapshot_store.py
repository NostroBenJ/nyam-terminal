"""
snapshot_store.py  --  the last board, on disk.

WHY. A cold engine takes ~5.7s to build a board and the window appears in 0.8s,
so the app spent five seconds showing a spinner before it could show anything —
and before the brief was moved off the critical path, twenty. None of that
waiting is necessary: yesterday's board is not today's board, but it is a real
board, and painting it instantly with an honest "this is old, refreshing now"
is strictly better than an empty rectangle that says nothing at all.

THE DANGER, AND THE RULE. A restored snapshot must never be mistakable for a
current one. Levels from the previous session shown without comment are the
single most dangerous thing this app could do — they look exactly like today's
and they are wrong. So a restored snapshot carries `restored: true` and
`restored_from`, the UI shows a banner until the first live build replaces it,
and `generated_at` is left UNTOUCHED so every existing staleness check keeps
working on the real age rather than the time we read the file.

One file per ticker, overwritten in place. This is a cache, not a dataset: the
capture recorder owns history and stores the raw chain alongside it, which is
the thing that cannot be re-fetched. Losing this directory costs one refresh.
"""
import datetime as dt
import json
import os
import tempfile

import config

DIRNAME = "snapshots"


def _dir() -> str:
    return os.path.join(config.STORE_DIR, DIRNAME)


def _path(ticker: str) -> str:
    return os.path.join(_dir(), f"{(ticker or '').upper()}.json")


def save(ticker: str, snap: dict) -> bool:
    """
    Persist a snapshot. Never raises — a cache write must not fail a refresh.

    Written to a temp file and moved into place, because the reader is the next
    engine start and a half-written JSON file would make the app fail to boot
    with a parse error instead of merely starting cold.
    """
    if not snap:
        return False
    tmp = None
    try:
        os.makedirs(_dir(), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_dir(), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        os.replace(tmp, _path(ticker))
        return True
    except (OSError, TypeError, ValueError):
        # A serialisation failure leaves the temp file behind, and this runs on
        # every refresh — so the leak is slow, silent, and lands in the same
        # directory the loader scans. Clean up on the way out.
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def load(ticker: str, max_age_days: int = 5) -> dict | None:
    """
    Read the last saved board, marked as restored.

    Returns None past `max_age_days`. A week-old board is not a useful head
    start on a live session — expiries have rolled, the chain is gone, and the
    levels describe a market that no longer exists. Better to show the boot
    spinner for five seconds than to paint something that stale, however
    loudly it is labelled.
    """
    try:
        with open(_path(ticker), encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict) or "gex" not in snap:
        return None

    stamp = str(snap.get("generated_at") or "")[:10]
    try:
        # generated_at is stamped in ET, so the comparison has to be too or a
        # board saved this evening reads as a day old on a UTC host.
        age_days = (config.today() - dt.date.fromisoformat(stamp)).days
    except ValueError:
        return None
    if age_days > max_age_days or age_days < 0:
        return None

    # generated_at is deliberately NOT rewritten: every staleness check in the
    # UI reads it, and refreshing it here would make an old board claim to be
    # new — the exact failure this whole file is careful about.
    snap["restored"] = True
    snap["restored_from"] = snap.get("generated_at")
    snap["restored_age_days"] = age_days
    return snap


def load_all(tickers, max_age_days: int = 5) -> dict:
    """Every ticker that has a usable saved board. For engine startup."""
    out = {}
    for t in tickers or []:
        snap = load(t, max_age_days=max_age_days)
        if snap:
            out[t.upper()] = snap
    return out
