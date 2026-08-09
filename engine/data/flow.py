"""
flow.py  --  Options order flow for the scanner.

Three states, and the UI must be able to tell them apart:
  live         — a UW key is set and the endpoint answered.
  unavailable  — a key is set but the tier or the network refused. Says why.
  mock         — no key. Synthetic rows so the scanner is buildable and
                 reviewable now, LOUDLY labelled, never mistakable for real
                 prints.

The mock path exists so the table, filters and sort are finished and verified
before the key arrives — not so the panel looks populated. Every row carries
`mock: true` and the payload's `source` says so.
"""
import datetime as dt
import random

import config

# Sentiment is inferred from where the trade printed in the spread, which is
# the standard read: lifting the offer is aggressive buying, hitting the bid is
# aggressive selling. Anything in between is genuinely ambiguous and is left
# NEUTRAL rather than guessed.
SIDE_ASK, SIDE_BID, SIDE_MID = "ask", "bid", "mid"


def _sentiment(right: str, side: str) -> str:
    if side == SIDE_MID:
        return "NEUTRAL"
    aggressive_buy = side == SIDE_ASK
    if right == "C":
        return "BULLISH" if aggressive_buy else "BEARISH"
    return "BEARISH" if aggressive_buy else "BULLISH"


def _row(**kw) -> dict:
    """One scanner row. Shape is the contract the frontend table renders."""
    return {
        "at": kw["at"],
        "ticker": kw["ticker"],
        "right": kw["right"],                # "C" | "P"
        "spot": kw["spot"],
        "strike": kw["strike"],
        "expiry": kw["expiry"],
        "dte": kw["dte"],
        "size": kw["size"],
        "price": kw["price"],
        "premium": kw["premium"],
        "trade_type": kw["trade_type"],      # SWEEP | BLOCK | SPLIT
        "side": kw["side"],
        "sentiment": _sentiment(kw["right"], kw["side"]),
        "volume": kw.get("volume"),
        "open_interest": kw.get("open_interest"),
        "vol_oi": kw.get("vol_oi"),
        "mock": kw.get("mock", False),
    }


# ---------------------------------------------------------------------------
# mock
# ---------------------------------------------------------------------------
def _mock_flow(ticker: str, spot: float, n: int = 60) -> list:
    """
    Deterministic synthetic prints, seeded per ticker so the table doesn't
    reshuffle on every poll while you're reading it.
    """
    rng = random.Random(hash(("flow", ticker)) & 0xFFFFFFFF)
    now = dt.datetime.now(config.TZ)
    rows = []
    for i in range(n):
        right = "C" if rng.random() < 0.55 else "P"
        step = max(1.0, round(spot * 0.0025))
        strike = round(spot + rng.randint(-12, 12) * step)
        dte = rng.choice([0, 0, 0, 1, 2, 7, 14, 30])
        size = rng.choice([25, 50, 100, 250, 500, 1000, 2500])
        price = round(max(0.05, abs(rng.gauss(2.4, 1.6))), 2)
        side = rng.choices([SIDE_ASK, SIDE_BID, SIDE_MID], weights=[45, 40, 15])[0]
        oi = rng.randint(200, 20000)
        vol = rng.randint(size, size * 12)
        rows.append(_row(
            at=(now - dt.timedelta(seconds=rng.randint(0, 5 * 3600))).isoformat(),
            ticker=ticker, right=right, spot=spot, strike=float(strike),
            expiry=(now.date() + dt.timedelta(days=dte)).isoformat(), dte=dte,
            size=size, price=price, premium=round(size * price * 100, 2),
            trade_type=rng.choices(["SWEEP", "BLOCK", "SPLIT"], weights=[50, 30, 20])[0],
            side=side, volume=vol, open_interest=oi,
            vol_oi=round(vol / oi, 2) if oi else None,
            mock=True,
        ))
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# live
# ---------------------------------------------------------------------------
def _uw_flow(ticker: str, spot: float, limit: int) -> list:
    """
    Map UW flow alerts onto the scanner's row shape.

    UW returns numbers as JSON strings on many endpoints, so every numeric is
    coerced here. A row that fails to parse is SKIPPED rather than emitted with
    zeros — a $0 premium sorts to the bottom of a premium filter and quietly
    disappears, which is worse than being absent.
    """
    from data import unusual_whales as uw

    alerts = uw.flow_alerts(ticker, limit=limit) or []
    today = config.today()          # exchange day: this feeds DTE
    rows = []
    for a in alerts:
        try:
            strike = float(a.get("strike"))
            size = int(float(a.get("total_size") or 0))
            premium = float(a.get("total_premium") or 0)
            expiry = str(a.get("expiry") or "")
            dte = (dt.date.fromisoformat(expiry) - today).days if expiry else None
        except (TypeError, ValueError):
            continue

        right = (a.get("type") or "").upper()[:1]
        if right not in ("C", "P"):
            right = "C" if "call" in str(a.get("type", "")).lower() else "P"

        # UW exposes ask/bid-side volume; infer aggression from which dominates.
        ask_v = float(a.get("ask_side_volume") or 0)
        bid_v = float(a.get("bid_side_volume") or 0)
        side = SIDE_MID
        if ask_v or bid_v:
            total = ask_v + bid_v
            if total and ask_v / total >= 0.6:
                side = SIDE_ASK
            elif total and bid_v / total >= 0.6:
                side = SIDE_BID

        oi = int(float(a.get("open_interest") or 0))
        vol = int(float(a.get("volume") or 0))
        rows.append(_row(
            at=a.get("created_at") or "",
            ticker=a.get("ticker") or ticker, right=right, spot=spot,
            strike=strike, expiry=expiry, dte=dte, size=size,
            price=round(premium / (size * 100), 2) if size else 0.0,
            premium=premium,
            trade_type="SWEEP" if a.get("has_sweep") else "BLOCK",
            side=side, volume=vol, open_interest=oi,
            vol_oi=round(vol / oi, 2) if oi else None,
        ))
    return rows


def get_flow(ticker: str, spot: float, limit: int = 100) -> dict:
    """
    Returns {"rows", "source", "note", "available"}.

    Never raises for a data problem. `available: False` with a reason is a
    renderable state; an exception is a blank section.
    """
    ticker = (ticker or config.PRIMARY_TICKER).upper()

    if config.USE_MOCK_DATA or not config.UW_API_KEY:
        why = ("Mock mode." if config.USE_MOCK_DATA
               else "No UW_API_KEY set — flow needs a paid feed.")
        return {
            "rows": _mock_flow(ticker, spot, n=min(limit, 60)),
            "source": "mock",
            "available": False,
            "note": f"{why} These rows are SYNTHETIC — shape only, not real prints. "
                    f"Probe your tier with: python -m data.unusual_whales probe {ticker}",
        }

    try:
        rows = _uw_flow(ticker, spot, limit)
    except Exception as e:
        return {"rows": [], "source": "unusual_whales", "available": False,
                "note": f"Unusual Whales flow unavailable ({type(e).__name__}: {e}). "
                        f"A 403 here means your tier lacks the endpoint, not that "
                        f"the key is wrong."}

    return {"rows": rows, "source": "unusual_whales", "available": True,
            "note": "" if rows else "Endpoint answered with no alerts for this ticker."}
