"""
store.py  --  Dead-simple JSON persistence for the track record.

One file, a dict keyed by date. You can open it in a text editor and read it.
Mock mode and live mode use separate files so demo data never mixes with real.
"""
import json
import os

import config


def _path() -> str:
    name = "records_mock.json" if config.USE_MOCK_DATA else "records.json"
    return os.path.join(config.STORE_DIR, name)


def load() -> dict:
    p = _path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


def save(records: dict) -> None:
    os.makedirs(config.STORE_DIR, exist_ok=True)
    with open(_path(), "w") as f:
        json.dump(records, f, indent=2)
