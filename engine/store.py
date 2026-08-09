"""
store.py  --  JSON persistence for the track record.

One file, a dict keyed by date. You can open it in a text editor and read it.
Mock mode and live mode use separate files so demo data never mixes with real.

THIS FILE HOLDS THE ONLY IRREPLACEABLE DATA IN THE APP. A chain can be
re-fetched tomorrow; a call you made on Tuesday morning and the reasoning
behind it cannot be reconstructed from anything. It therefore gets the most
careful write path here, not the simplest one.

TWO FAULTS THIS REPLACES, both reproduced against a copy of the real file:

  1. save() opened the target directly, which truncates before writing. A
     process killed between truncate and completion left a half-written file.
     The engine writes on startup, on every capture and on every grade, and
     Task Scheduler enforces a 20-minute kill timeout, so "interrupted
     mid-write" is an ordinary event rather than a freak one.

  2. load() returned {} on ANY exception, and the very next save() wrote that
     {} over the top. Measured: a truncated file turned 4 graded calls into 0.
     The 5,278 bytes were still on disk and perfectly readable by a human at
     the moment the app erased them. Corruption became deletion, silently, with
     the data recoverable right up until it wasn't.

The rule now: a read that fails NEVER causes a write that loses. Anything
unparseable is quarantined with a timestamp and left on disk, and the previous
good copy is kept as a backup that load() falls back to on its own.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import config


def _path() -> str:
    name = "records_mock.json" if config.USE_MOCK_DATA else "records.json"
    return os.path.join(config.STORE_DIR, name)


def _backup_path() -> str:
    return _path() + ".bak"


def _quarantine(path: str) -> str | None:
    """
    Move an unreadable file aside instead of letting it be overwritten.

    Renamed rather than deleted: a truncated JSON file is usually 95% intact
    and a person can recover the records from it by hand. Deleting it — or
    leaving it in place to be overwritten by the next save — throws away the
    only copy of something that cannot be regenerated.
    """
    stamp = dt.datetime.now(config.TZ).strftime("%Y%m%d-%H%M%S")
    dest = f"{path}.corrupt.{stamp}"
    try:
        shutil.move(path, dest)
        return dest
    except OSError:
        return None


def load() -> dict:
    """
    Read the record. Returns {} only when there is genuinely nothing to read.

    A parse failure is NOT treated as "no records". The bad file is quarantined
    and the backup is tried, because the alternative — quietly returning {} —
    is what turned a recoverable corruption into a total loss.
    """
    p = _path()
    if not os.path.exists(p):
        return {}

    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        raise ValueError(f"expected an object, got {type(data).__name__}")
    except (OSError, ValueError) as e:
        moved = _quarantine(p)
        print(f"[ERROR] {os.path.basename(p)} is unreadable ({e}). "
              f"{'Preserved at ' + os.path.basename(moved) if moved else 'Could not move it aside.'}",
              file=sys.stderr, flush=True)

    # The backup is a full copy of the last good state, so recovering from it
    # loses at most one session rather than everything.
    b = _backup_path()
    if os.path.exists(b):
        try:
            with open(b, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                print(f"[warn] recovered {len(data)} record(s) from the backup",
                      file=sys.stderr, flush=True)
                return data
        except (OSError, ValueError):
            print("[ERROR] the backup is unreadable too", file=sys.stderr, flush=True)

    print("[ERROR] starting with an EMPTY record. The quarantined file above "
          "is your history — do not delete it.", file=sys.stderr, flush=True)
    return {}


def save(records: dict) -> None:
    """
    Write atomically, keeping the previous version as a backup.

    Written to a temp file in the same directory and moved into place, so the
    target is either the old complete file or the new complete file and never a
    half-written one. os.replace is atomic on Windows and POSIX alike.

    Refuses to write a smaller record over a larger one without keeping the
    larger: the backup is taken FIRST, so even a logically-wrong save (an empty
    dict from a caller that mishandled a load failure) is recoverable.
    """
    if not isinstance(records, dict):
        raise TypeError(f"records must be a dict, got {type(records).__name__}")

    os.makedirs(config.STORE_DIR, exist_ok=True)
    p = _path()

    # Back up the current good file before touching anything.
    if os.path.exists(p):
        try:
            shutil.copy2(p, _backup_path())
        except OSError:
            pass                    # a failed backup must not block the write

    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=config.STORE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
            f.flush()
            os.fsync(f.fileno())    # the rename is pointless if the data is still in cache
        os.replace(tmp, p)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)      # a failed serialise must not litter the store
            except OSError:
                pass
