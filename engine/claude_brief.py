"""
claude_brief.py  --  Turns the structured signals into a readable morning brief.

If ANTHROPIC_API_KEY is set, it calls the Claude API. If not, it builds a
clean templated brief from the same signals, so the app always works and
costs nothing until you choose to plug in a key.

Cost note: a brief like this is a small prompt + short output — fractions of
a cent on Haiku, a couple cents on Sonnet. The model is set in config.py.
"""
import config


SYSTEM = (
    "You are a disciplined trading assistant writing a PRE-MARKET BIAS brief from "
    "options positioning data (GEX, walls, put/call, SMT divergence, session levels).\n\n"
    "THE INSTRUMENT IS GIVEN TO YOU as the `ticker` field. Use exactly that symbol "
    "and never name a different one. Do not assume futures, and do not substitute a "
    "correlated instrument for the one you were given.\n\n"
    "Rules: this is a PROBABILISTIC lean, never a prediction or a guarantee. Always "
    "frame as 'if price does X, lean Y'. Be concise and concrete, reference the actual "
    "levels given, and explain the WHY behind each point. End with a one-line risk "
    "reminder. Do not give position-sizing or financial advice."
)
# The instrument used to be hardcoded here as "a Nasdaq (MNQ) trader" — a leftover
# from when this tool was a QQQ->MNQ futures product. The ticker was also missing
# from the payload entirely, so the model could not have known better: it dutifully
# headed a SPY brief "MNQ Pre-Market Bias". A brief that names the wrong instrument
# is worse than no brief, so the symbol is now passed explicitly and pinned above.


# ---------------------------------------------------------------------------
# COST CONTROL
# ---------------------------------------------------------------------------
# The scheduler refreshes every 60s through the pre-market window, which fires
# 180 times a morning. Regenerating the brief on every one of those cost a
# MEASURED $105/month on Opus — for a written read that only changes a handful
# of times a session. Everything below exists to stop paying for that.
#
# The brief is regenerated when the READ changes, not when the price ticks:
# spot moves every minute, but "positive gamma, above the flip, boxed between
# the walls" does not. The signature below captures the former and ignores the
# latter.
import datetime as _dt
import hashlib
import time as _time

#: Regenerate at least this often even if nothing changed, so the prose can't
#: describe a session that has moved on.
MAX_AGE_S = 1800

#: Hard stop per ticker per day. A runaway loop is the failure mode that turns
#: a $5 month into a $500 one, and it should hit a wall rather than a bill.
MAX_CALLS_PER_DAY = 40

#: Spot bucket, as a fraction of price. Ticks smaller than this don't change
#: the read; anything bigger is a real move and earns a fresh brief.
SPOT_BUCKET = 0.0025

_cache: dict = {}                                  # ticker -> entry
_calls: dict = {"date": None, "n": {}}


def _signature(bias, gex, levels, smt, news, ticker) -> str:
    """
    What makes this brief different from the last one.

    Deliberately EXCLUDES raw spot and includes which side of each level spot
    sits on. A 5-cent tick doesn't change the analysis; crossing the gamma flip
    changes all of it.
    """
    spot = gex.get("spot") or 0.0

    def side(level):
        if level is None:
            return "-"
        return "above" if spot >= level else "below"

    # Bucket size must come from a STABLE anchor, not from spot itself.
    # `spot / (spot * SPOT_BUCKET)` looks like a bucket and is algebraically
    # 1/SPOT_BUCKET — a constant. Spot dropped out of the signature entirely,
    # and float noise flipped the constant between 399 and 400, so it both
    # ignored real moves and regenerated on no move at all.
    anchor = gex.get("gamma_flip") or gex.get("control_node") or spot or 1.0
    bucket = max(anchor * SPOT_BUCKET, 0.01)

    parts = [
        ticker.upper(),
        bias.get("label", ""), str(bias.get("score")), bias.get("conviction", ""),
        gex.get("regime", ""),
        # Levels themselves, and where price sits relative to each.
        f"{gex.get('gamma_flip')}:{side(gex.get('gamma_flip'))}",
        f"{gex.get('call_wall')}:{side(gex.get('call_wall'))}",
        f"{gex.get('put_wall')}:{side(gex.get('put_wall'))}",
        f"{gex.get('control_node')}:{side(gex.get('control_node'))}",
        # Coarse spot bucket so a genuinely large move still refreshes it.
        str(int(spot // bucket)),
        # Direction of each signal — the reasoning, not its exact weight.
        "|".join(f"{s.get('name')}:{s.get('lean')}" for s in bias.get("signals", [])),
        smt.get("signal", ""),
        news.get("headline", ""), str(news.get("high_impact")),
    ]
    return hashlib.sha256("~".join(parts).encode()).hexdigest()[:16]


def _budget_ok(ticker: str) -> bool:
    """Per-ticker daily call ceiling. Resets on date change."""
    today = _dt.date.today().isoformat()
    if _calls["date"] != today:
        _calls["date"] = today
        _calls["n"] = {}
    return _calls["n"].get(ticker, 0) < MAX_CALLS_PER_DAY


def _budget_spend(ticker: str) -> None:
    _calls["n"][ticker] = _calls["n"].get(ticker, 0) + 1


def calls_today(ticker: str = None) -> int:
    if ticker:
        return _calls["n"].get(ticker.upper(), 0)
    return sum(_calls["n"].values())


def generate_brief(bias: dict, gex: dict, levels: dict, smt: dict, news: dict,
                   ticker: str = "") -> str:
    """Backwards-compatible string form. Prefer generate_brief_meta."""
    return generate_brief_meta(bias, gex, levels, smt, news, ticker)["text"]


def generate_brief_meta(bias: dict, gex: dict, levels: dict, smt: dict,
                        news: dict, ticker: str = "") -> dict:
    """
    The brief, plus WHO WROTE IT.

    A model-written brief and a templated one read almost identically — both are
    fluent prose about the same levels — but they are different claims. One is a
    language model's reading; the other is string formatting. The UI must be
    able to label which, and it cannot infer that from the text, so the source
    travels alongside rather than being guessed downstream.

    Returns {text, source: "claude"|"template", model, error}.
    """
    t = (ticker or config.PRIMARY_TICKER).upper()

    if not config.ANTHROPIC_API_KEY:
        return {"text": _template_brief(bias, gex, levels, smt, news),
                "source": "template", "model": None, "cached": False,
                "calls_today": calls_today(t),
                "error": "No ANTHROPIC_API_KEY set."}

    sig = _signature(bias, gex, levels, smt, news, t)
    now = _time.time()
    hit = _cache.get(t)
    if hit and hit["sig"] == sig and now - hit["at"] < MAX_AGE_S:
        # The read hasn't changed. Reusing the prose is not a shortcut — a
        # second call on identical inputs buys nothing but a different wording.
        return {**hit["payload"], "cached": True,
                "age_s": int(now - hit["at"]), "calls_today": calls_today(t)}

    if not _budget_ok(t):
        # Ceiling reached. Serve the last brief if there is one, and SAY the
        # budget stopped a refresh — silently going stale would be worse.
        note = (f"Daily brief budget reached ({MAX_CALLS_PER_DAY} calls). "
                f"Showing the last generated brief.")
        if hit:
            return {**hit["payload"], "cached": True, "budget_capped": True,
                    "age_s": int(now - hit["at"]), "calls_today": calls_today(t),
                    "error": note}
        return {"text": _template_brief(bias, gex, levels, smt, news),
                "source": "template", "model": None, "cached": False,
                "budget_capped": True, "calls_today": calls_today(t), "error": note}

    try:
        payload = {"text": _claude_brief(bias, gex, levels, smt, news, t),
                   "source": "claude", "model": config.CLAUDE_MODEL, "error": None}
        _budget_spend(t)
        _cache[t] = {"sig": sig, "at": now, "payload": payload}
        return {**payload, "cached": False, "age_s": 0, "calls_today": calls_today(t)}
    except Exception as e:
        # Degrade to the template but SAY the call failed. Silently serving a
        # templated brief while the user believes a model wrote it would
        # misrepresent where the reasoning came from.
        return {"text": _template_brief(bias, gex, levels, smt, news),
                "source": "template", "model": None, "cached": False,
                "calls_today": calls_today(t),
                "error": f"{type(e).__name__}: {e}"}


def _claude_brief(bias, gex, levels, smt, news, ticker: str = "") -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    payload = {
        # First field on purpose: the instrument was previously absent entirely,
        # so the model named whatever the system prompt implied.
        "ticker": (ticker or config.PRIMARY_TICKER).upper(),
        "bias": bias["label"], "score": bias["score"], "conviction": bias["conviction"],
        "spot": gex["spot"], "gamma_flip": gex["gamma_flip"], "regime": gex["regime"],
        "call_wall": gex["call_wall"], "put_wall": gex["put_wall"],
        "put_call_ratio": round(gex["put_call_ratio"], 2),
        "session_levels": levels, "smt": smt["note"],
        "signals": [{"name": s["name"], "reason": s["reason"]} for s in bias["signals"]],
        "news": news.get("headline", ""),
    }
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=700,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": "Write today's pre-market bias brief from this data. "
                       "4-6 short paragraphs max.\n\n" + str(payload),
        }],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def _template_brief(bias, gex, levels, smt, news) -> str:
    lines = [
        f"**{bias['label']}**  (score {bias['score']}, {bias['conviction']} conviction)",
        "",
        bias["summary"],
        "",
        f"Spot {gex['spot']} is in a **{gex['regime']}-gamma** regime relative to the "
        f"gamma flip at {gex['gamma_flip']}. Call wall {gex['call_wall']} caps the upside; "
        f"put wall {gex['put_wall']} backstops the downside. {smt['note']}",
        "",
        "**Why:**",
    ]
    lines += [f"- {s['name']}: {s['reason']}" for s in bias["signals"]]
    lines += ["", "**Scenarios:**"]
    lines += [f"- {s}" for s in bias["scenarios"]]
    if news.get("high_impact"):
        lines += ["", f"⚠️ News risk today: {news['headline']}. Reduce conviction around the release."]
    lines += ["", "_Probabilistic lean from positioning data — not a prediction, not financial advice._"]
    return "\n".join(lines)
