"""
measure_cost.py  --  What the Claude integration actually costs, measured.

Runs ONE real brief call and reads token usage off the response, then multiplies
by how often the scheduler fires. Estimating tokens by eye is how you end up
budgeting for the wrong number by an order of magnitude.

    python measure_cost.py
"""
import datetime as dt

import config

# Claude Opus 5, per the published rates: $5 / 1M input, $25 / 1M output.
PRICE = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def scheduler_fires_per_morning() -> int:
    """
    Count how many times the pre-market cron actually fires.

    Derived from the same config the scheduler reads, rather than assumed —
    the cron builds its minute/second fields conditionally and the firing rate
    is not obvious from REFRESH_SECONDS alone.
    """
    start_h = int(config.PREMARKET_START.split(":")[0])
    open_h = int(config.MARKET_OPEN.split(":")[0])
    every = max(config.REFRESH_SECONDS, 30)
    hours = open_h - start_h + 1          # cron "7-9" is inclusive: 7, 8, 9
    if every < 60:
        per_hour = 60 * (60 // every)     # every N seconds, every minute
    else:
        per_hour = 60 // max(1, every // 60)
    return hours * per_hour


def main():
    from analysis import bias_engine, gex as gex_mod, derived, levels as levels_mod
    from pipeline import build_snapshot

    print("[1] how often does the brief regenerate?")
    fires = scheduler_fires_per_morning()
    print(f"  premarket window : {config.PREMARKET_START}–{config.MARKET_OPEN} ET, weekdays")
    print(f"  refresh interval : {config.REFRESH_SECONDS}s")
    print(f"  scheduler fires  : {fires} times per morning")
    print(f"  -> brief calls   : {fires} per morning (one per refresh, no caching)")

    if not config.ANTHROPIC_API_KEY:
        print("\n  no API key — cannot measure real token usage")
        raise SystemExit(0)

    print("\n[2] measuring ONE real brief call")
    snap = build_snapshot("SPY")

    import claude_brief as cb
    from anthropic import Anthropic

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    payload = {
        "ticker": "SPY",
        "bias": snap["bias"]["label"], "score": snap["bias"]["score"],
        "conviction": snap["bias"]["conviction"],
        "spot": snap["gex"]["spot"], "gamma_flip": snap["gex"]["gamma_flip"],
        "regime": snap["gex"]["regime"], "call_wall": snap["gex"]["call_wall"],
        "put_wall": snap["gex"]["put_wall"],
        "put_call_ratio": round(snap["gex"]["put_call_ratio"], 2),
        "session_levels": snap["levels"], "smt": snap["smt"]["note"],
        "signals": [{"name": s["name"], "reason": s["reason"]}
                    for s in snap["bias"]["signals"]],
        "news": snap["news"].get("headline", ""),
    }
    t0 = dt.datetime.now()
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        system=cb.SYSTEM,
        messages=[{"role": "user",
                   "content": "Write today's pre-market bias brief from this data. "
                              "4-6 short paragraphs max.\n\n" + str(payload)}],
    )
    secs = (dt.datetime.now() - t0).total_seconds()

    u = msg.usage
    inp, out = u.input_tokens, u.output_tokens
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    in_rate, out_rate = PRICE.get(config.CLAUDE_MODEL, (5.00, 25.00))
    per_call = (inp / 1e6) * in_rate + (out / 1e6) * out_rate

    print(f"  model            : {config.CLAUDE_MODEL}")
    print(f"  latency          : {secs:.1f}s")
    print(f"  input tokens     : {inp:,}  (cache read {cache_read:,})")
    print(f"  output tokens    : {out:,}")
    print(f"  cost per call    : ${per_call:.4f}")

    print("\n[3] what that scales to")
    per_morning = per_call * fires
    per_month = per_morning * 21
    print(f"  {fires} calls/morning        ${per_morning:.2f}")
    print(f"  x21 trading days       ${per_month:.2f} / month")

    print("\n[4] with the brief cached on a change-signature")
    for regen in (4, 8, 15):
        m = per_call * regen * 21
        print(f"  ~{regen:>2} regenerations/morning  ${per_call * regen:.2f}/morning"
              f"   ${m:.2f}/month")

    print("\n[5] one chat turn, for comparison")
    print(f"  a chat turn re-injects the snapshot: roughly the same input size")
    print(f"  ~${per_call:.4f}/turn -> 20 turns/day is ~${per_call * 20 * 21:.2f}/month")


if __name__ == "__main__":
    main()
