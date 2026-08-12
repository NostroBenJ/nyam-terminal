"""
risk.py  --  how many, and whether at all.

The playbook says what the board implies. This says whether you can afford it
and whether now is the moment — and it is the layer that says NO for reasons
that have nothing to do with the read being good.

TWO NUMBERS FOR THE SAME TRADE, and the gap between them is the point:

  MAX LOSS is the premium. A long option can go to zero, and on 0-2 DTE it
  routinely does. This is what you can actually lose.

  LOSS AT STOP is delta-estimated: how much premium evaporates when the
  underlying reaches the stop. It is smaller, and it is an ESTIMATE — it
  ignores theta, which on a 1DTE contract is not a rounding error, and it
  assumes you get out at the stop, which a gap does not permit.

Sizing uses loss-at-stop because sizing on full premium makes every position
one contract and wastes the stop entirely. But MAX LOSS is checked against a
separate hard ceiling, so the estimate being optimistic cannot quietly become a
position that hurts. When the two disagree, the smaller size wins.

Nothing here reaches a broker.
"""
import datetime as dt

import config

#: Delta-estimated loss is optimistic by construction — it prices the move and
#: ignores the clock. Inflate it before sizing so a 1DTE contract, where theta
#: through a session can exceed the directional loss, is not sized as though
#: only direction mattered.
THETA_HAIRCUT = 1.35

#: Ceiling on premium at risk in one position, as a fraction of the account.
#: Separate from RISK_PCT_PER_TRADE: that governs the expected loss, this
#: governs the loss if the estimate is wrong and the thing goes to zero.
MAX_PREMIUM_PCT = 0.02


def _no(reason: str, **extra) -> dict:
    return {"ok": False, "contracts": 0, "reason": reason, **extra}


def size(intent: dict, *, account: float = None) -> dict:
    """
    Contracts for this intent, or a refusal with a reason.

    Requires a named contract — an intent with no contract is a thesis, and a
    thesis cannot be sized.
    """
    account = account or config.ACCOUNT_VALUE
    c = intent.get("contract")
    if not c:
        return _no("No contract on the intent — a thesis cannot be sized.")
    mid, delta = c.get("mid"), abs(c.get("delta") or 0.0)
    entry, stop = intent.get("entry"), intent.get("stop")
    if not mid or mid <= 0:
        return _no("No usable mid price.")
    if not delta:
        return _no("No delta — cannot estimate the loss at the stop.")
    if entry is None or stop is None:
        return _no("No stop — an unbounded position cannot be sized.")

    stop_points = abs(float(entry) - float(stop))
    if stop_points <= 0:
        return _no("Stop is at the entry — no room, no size.")

    premium = mid * 100.0                       # one contract, dollars
    est_loss = min(delta * stop_points * 100.0 * THETA_HAIRCUT, premium)
    if est_loss <= 0:
        return _no("Estimated loss at the stop is zero — refusing to divide by it.")

    risk_budget = account * config.RISK_PCT_PER_TRADE
    premium_cap = account * MAX_PREMIUM_PCT

    by_risk = int(risk_budget // est_loss)
    by_premium = int(premium_cap // premium)    # the estimate being wrong
    n = max(0, min(by_risk, by_premium, config.MAX_CONTRACTS))

    if n < 1:
        return _no(
            f"One contract already exceeds the budget: ${est_loss:,.0f} "
            f"estimated at the stop and ${premium:,.0f} of premium, against a "
            f"${risk_budget:,.0f} risk budget and a ${premium_cap:,.0f} "
            f"premium cap.",
            est_loss_per_contract=round(est_loss, 2),
            premium_per_contract=round(premium, 2))

    binding = "risk budget" if by_risk <= by_premium else "premium ceiling"
    if n == config.MAX_CONTRACTS and config.MAX_CONTRACTS <= min(by_risk, by_premium):
        binding = "hard contract cap"
    return {
        "ok": True,
        "contracts": n,
        "binding_constraint": binding,
        "premium_per_contract": round(premium, 2),
        "premium_total": round(premium * n, 2),
        "est_loss_at_stop": round(est_loss * n, 2),
        "max_loss": round(premium * n, 2),
        "stop_points": round(stop_points, 2),
        "risk_budget": round(risk_budget, 2),
        "note": ("Loss at the stop is delta-estimated with a "
                 f"{THETA_HAIRCUT:.2f}x theta haircut. It assumes you get out "
                 "AT the stop; a gap does not offer that. Max loss is the "
                 "premium and is the number that is actually guaranteed."),
    }


def gate(intent: dict, *, snap: dict, now: dt.datetime = None,
         day_pnl: float = 0.0, open_positions: int = 0,
         account: float = None) -> dict:
    """
    Everything that says no for reasons unrelated to the read.

    Returns {"ok", "blocks": [...], "warnings": [...]}. `blocks` is what stops
    the trade; `warnings` ride along on the record so a decision that was
    marginal does not read afterwards as though it was clean.
    """
    account = account or config.ACCOUNT_VALUE
    now = now or dt.datetime.now(config.TZ)
    blocks, warns = [], []

    # --- the day's damage ---------------------------------------------------
    # Measured against ACCOUNT_VALUE, not against the day's peak. A cap that
    # loosens because you were up earlier is a cap that funds tilt.
    cap = account * config.MAX_DAILY_LOSS_PCT
    if day_pnl <= -cap:
        blocks.append(f"Daily loss cap hit: {day_pnl:,.0f} against a "
                      f"-{cap:,.0f} limit. The day is done.")
    elif day_pnl <= -cap * 0.6:
        warns.append(f"{abs(day_pnl):,.0f} down against a {cap:,.0f} cap — "
                     f"one more loser ends the day.")

    # --- concurrency --------------------------------------------------------
    if open_positions >= config.MAX_CONCURRENT_POSITIONS:
        blocks.append(f"{open_positions} position(s) already open, limit is "
                      f"{config.MAX_CONCURRENT_POSITIONS}. Two correlated SPY "
                      f"options are one position wearing a disguise.")

    # --- the session --------------------------------------------------------
    open_h, open_m = map(int, config.MARKET_OPEN.split(":"))
    cut_h, cut_m = map(int, config.NO_ENTRY_AFTER.split(":"))
    mins = now.hour * 60 + now.minute
    if mins < open_h * 60 + open_m:
        blocks.append(f"Before the open — nothing can be held until "
                      f"{config.MARKET_OPEN}.")
    if mins >= cut_h * 60 + cut_m:
        blocks.append(f"After {config.NO_ENTRY_AFTER} — late-day decay is "
                      f"brutal and the closing auction is not a price you can "
                      f"count on getting out at.")

    # --- the calendar -------------------------------------------------------
    # The board already knows what prints today. This is what makes it act on
    # it: positioning signals are least reliable exactly across a release.
    ev = _next_event(snap, now)
    if ev:
        mins_to = ev["minutes"]
        if 0 <= mins_to <= config.NO_ENTRY_BEFORE_EVENT_MIN:
            blocks.append(f"{ev['title']} in {mins_to} min — inside the "
                          f"{config.NO_ENTRY_BEFORE_EVENT_MIN}-minute window "
                          f"before a high-impact release.")
        elif 0 <= mins_to <= 60:
            warns.append(f"{ev['title']} in {mins_to} min.")

    # --- the read itself ----------------------------------------------------
    if (snap.get("bias") or {}).get("conviction") == "reduced":
        warns.append("Conviction is reduced — the board has already discounted "
                     "itself today.")
    rr = intent.get("reward_points"), intent.get("risk_points")
    if all(rr) and rr[1] > 0 and rr[0] / rr[1] < 1.0:
        warns.append(f"Reward {rr[0]} against risk {rr[1]} — under 1:1 before "
                     f"any friction is paid.")

    return {"ok": not blocks, "blocks": blocks, "warnings": warns}


def _next_event(snap: dict, now: dt.datetime) -> dict | None:
    """The nearest high-impact calendar event still ahead, in minutes."""
    cal = snap.get("calendar") or {}
    best = None
    for e in (cal.get("high_impact") or []):
        raw = e.get("at")
        if not raw:
            continue
        try:
            when = dt.datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=config.TZ)
        mins = int((when - now).total_seconds() // 60)
        if mins < 0:
            continue
        if best is None or mins < best["minutes"]:
            best = {"title": e.get("title") or "high-impact event",
                    "minutes": mins, "at": str(raw)}
    return best
