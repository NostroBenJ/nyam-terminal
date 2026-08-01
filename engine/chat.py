"""
chat.py  --  The in-app Claude chat, wired to the live snapshot.

The point of this over a browser tab: the model can see the *current* GEX,
levels, bias, and track record for whatever ticker you're looking at, so you
can ask "why is this short?" or "what invalidates the long here?" and get an
answer grounded in the numbers on screen rather than a generic explanation.

Two design decisions worth knowing:

  1. THE API KEY NEVER LEAVES THE SERVER. The browser posts to /api/chat and
     this module holds the key. Never ship the key to the frontend, even for a
     localhost app -- anything the page can read, a stray extension can read.

  2. THE SNAPSHOT IS RE-INJECTED FRESH EVERY TURN, as the last user-turn
     content, and old snapshots are stripped from the history. Market data goes
     stale in minutes; a snapshot pinned in turn 1 would have the model
     reasoning about a spot price from half an hour ago for the rest of the
     conversation. Only the conversation persists, not the data it was about.
"""
import json

import config


SYSTEM = """You are a trading assistant embedded in the NYAM Bias Engine, a
pre-market options-positioning dashboard. The user is an options trader working
mostly in SPY and other liquid names, executing on Robinhood.

You are looking at the same live snapshot they are. Ground every answer in the
specific numbers you're given -- cite the actual levels, the actual net GEX, the
actual bias score. If a number you'd need isn't in the snapshot, say so instead
of estimating it.

How to be useful here:
- Explain what the positioning implies, and what would invalidate it. The
  "what would change my mind" half is the part that's usually missing.
- Be concrete about levels. "Watch the put wall" is useless; "below 733 the
  dealer hedging flips to amplifying, so bounces there are lower quality" is not.
- Distinguish what the data says from what you're inferring. The GEX numbers are
  computed from a real chain; the bias score is a weighted heuristic with a
  50%-baseline track record. Treat them accordingly.
- Keep it tight. This is a trading screen, not an essay. Lead with the answer.

Hard rules:
- This is positioning analysis, NOT financial advice, and NOT a prediction.
- Never tell the user what to buy, what strike, or what size. If asked, explain
  the tradeoffs of the structures involved and let them decide.
- Never claim to know direction. The whole tool is a probabilistic lean.
- If the track record sample is small (under ~30 graded days), say so when the
  user leans on it. A hot streak of six is noise.
"""


class ChatUnavailable(RuntimeError):
    """Raised when the chat can't run — no key, or SDK missing."""


def _snapshot_context(snap: dict) -> str:
    """
    Flatten the snapshot into the subset worth spending tokens on.

    Deliberately NOT the whole dict: the raw `profile` is hundreds of strikes
    and would dominate the prompt while adding nothing the aggregates don't
    already say.
    """
    if not snap:
        return "No snapshot loaded yet."
    g, b = snap.get("gex", {}), snap.get("bias", {})
    em, t = snap.get("expected_move") or {}, snap.get("track") or {}
    lv = snap.get("levels", {})

    ctx = {
        "ticker": snap.get("ticker"),
        "generated_at": snap.get("generated_at"),
        "is_mock_data": snap.get("mock"),
        "expiries_loaded": snap.get("expiries_loaded"),
        "spot": g.get("spot"),
        "net_gex": g.get("net_gex"),
        "gamma_regime": g.get("regime"),
        "gamma_flip": g.get("gamma_flip"),
        "call_wall": g.get("call_wall"),
        "put_wall": g.get("put_wall"),
        "control_node": g.get("control_node"),
        "atm_iv": g.get("atm_iv"),
        "put_call_ratio": round(g.get("put_call_ratio", 0), 3),
        "expected_move": {"dollars": em.get("dollars"), "pct": em.get("pct"),
                          "low": em.get("low"), "high": em.get("high")},
        "session_levels": lv,
        "smt": snap.get("smt"),
        "bias": {"label": b.get("label"), "score": b.get("score"),
                 "conviction": b.get("conviction"), "summary": b.get("summary")},
        "signals": [{"name": s["name"], "lean": s["lean"], "reason": s["reason"]}
                    for s in b.get("signals", [])],
        "confluences": snap.get("confluences"),
        "expiry_confluence": snap.get("expiry_confluence"),
        "neg_gamma_zone": snap.get("neg_zone"),
        "news": snap.get("news", {}).get("items"),
        "track_record": {"graded_days": t.get("n"), "directional_hit_rate": t.get("dir_hit_rate"),
                         "overall_hit_rate": t.get("hit_rate"), "by_bias_type": t.get("by_type")},
    }
    return json.dumps(ctx, indent=1, default=str)


def build_messages(history: list, user_msg: str, snap: dict) -> list:
    """
    History + the new turn, with a fresh snapshot attached to the new turn.

    Any snapshot block from an earlier turn is dropped: keeping several would
    both waste tokens and invite the model to reason off whichever one it
    happened to latch onto.
    """
    out = []
    for m in history[-config.CHAT_MAX_TURNS * 2:]:
        role, content = m.get("role"), m.get("content", "")
        if role not in ("user", "assistant") or not content:
            continue
        if role == "user":
            content = content.split("\n\n<live_snapshot>")[0]
        out.append({"role": role, "content": content})

    out.append({
        "role": "user",
        "content": f"{user_msg}\n\n<live_snapshot>\n{_snapshot_context(snap)}\n</live_snapshot>",
    })
    return out


def stream_reply(history: list, user_msg: str, snap: dict):
    """
    Yield text chunks from Claude. Raises ChatUnavailable if not configured.

    Streams because a grounded answer over a full snapshot can run long enough
    to look hung otherwise.
    """
    if not config.ANTHROPIC_API_KEY:
        raise ChatUnavailable(
            "No ANTHROPIC_API_KEY set. Add it to your environment and restart "
            "to enable chat. (The dashboard itself works without it.)")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise ChatUnavailable(f"anthropic SDK not installed: pip install anthropic ({e})")

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    # The system prompt is byte-stable across turns, so it caches; the snapshot
    # rides in the messages after it and changes freely without invalidating it.
    with client.messages.stream(
        model=config.CLAUDE_MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=build_messages(history, user_msg, snap),
    ) as stream:
        for text in stream.text_stream:
            yield text
