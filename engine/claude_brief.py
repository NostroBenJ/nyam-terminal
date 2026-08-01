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
    "You are a disciplined futures-trading assistant writing a PRE-MARKET BIAS brief "
    "for a Nasdaq (MNQ) trader, built from options positioning data (GEX, walls, "
    "put/call, SMT divergence, session levels). Rules: this is a PROBABILISTIC lean, "
    "never a prediction or a guarantee. Always frame as 'if price does X, lean Y'. "
    "Be concise and concrete, reference the actual levels given, and explain the WHY "
    "behind each point. End with a one-line risk reminder. Do not give position-sizing "
    "or financial advice."
)


def generate_brief(bias: dict, gex: dict, levels: dict, smt: dict, news: dict) -> str:
    if not config.ANTHROPIC_API_KEY:
        return _template_brief(bias, gex, levels, smt, news)
    try:
        return _claude_brief(bias, gex, levels, smt, news)
    except Exception as e:
        return _template_brief(bias, gex, levels, smt, news) + f"\n\n_(Claude API unavailable: {e} — showing templated brief.)_"


def _claude_brief(bias, gex, levels, smt, news) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    payload = {
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
