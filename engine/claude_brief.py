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
    if not config.ANTHROPIC_API_KEY:
        return {"text": _template_brief(bias, gex, levels, smt, news),
                "source": "template", "model": None,
                "error": "No ANTHROPIC_API_KEY set."}
    try:
        return {"text": _claude_brief(bias, gex, levels, smt, news, ticker),
                "source": "claude", "model": config.CLAUDE_MODEL, "error": None}
    except Exception as e:
        # Degrade to the template but SAY the call failed. Silently serving a
        # templated brief while the user believes a model wrote it would
        # misrepresent where the reasoning came from.
        return {"text": _template_brief(bias, gex, levels, smt, news),
                "source": "template", "model": None,
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
