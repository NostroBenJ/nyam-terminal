"""
verify_brief.py  --  Checks on the written brief.

The bug this exists to prevent already happened: the system prompt hardcoded
"for a Nasdaq (MNQ) trader" and the ticker was missing from the payload
entirely, so a SPY brief was headed "MNQ Pre-Market Bias". A brief naming the
wrong instrument is worse than no brief — you could read it and act on the
belief it describes something you are not looking at.

The offline checks need no key. The live check runs only when one is present
and is skipped cleanly otherwise, so this suite never costs money by surprise.

    python verify_brief.py
"""
import config
import claude_brief as cb

FAILS = []
SKIPPED = []


def check(name: str, ok: bool, detail: str = ""):
    # Detail is shown only on FAILURE here. These details are phrased as the
    # reason a check would fail, so printing them beside PASS reads as though
    # the failure occurred — "[PASS] does not hardcode 'MNQ' — found in prompt".
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if (not ok and detail) else ""))
    if not ok:
        FAILS.append(name)


def skip(name: str, why: str):
    print(f"  [SKIP] {name} — {why}")
    SKIPPED.append(name)


GEX = {"spot": 745.0, "regime": "positive", "gamma_flip": 742.46,
       "call_wall": 757.0, "put_wall": 733.0, "control_node": 757.0,
       "put_call_ratio": 0.99, "call_oi_change": 100, "put_oi_change": 200,
       "net_gex": 2.6e8, "atm_iv": 0.16}
LEVELS = {"prior_day_high": 746.27, "prior_day_low": 740.23, "prior_close": 744.03,
          "overnight_high": 747.23, "overnight_low": 741.87, "spot": 745.0}
SMT = {"signal": "bearish_divergence", "lean": -1, "note": "SPY made a new high, QQQ didn't."}
NEWS = {"high_impact": True, "headline": "10:00 ET — ISM Services PMI", "items": []}
BIAS = {"label": "SHORT LEAN", "score": -2.0, "conviction": "reduced",
        "summary": "Net positioning leans short.",
        "signals": [{"name": "Gamma Regime", "lean": 0, "weight": 2.0,
                     "reason": "Positive gamma."}],
        "scenarios": []}


def main():
    print("[1] the system prompt names no instrument")
    # The whole class of bug: a hardcoded symbol in the prompt overrides reality.
    for bad in ("MNQ", "NQ", "Nasdaq", "ES", "SPX futures"):
        check(f"prompt does not hardcode {bad!r}", bad not in cb.SYSTEM,
              "found in SYSTEM prompt")
    check("prompt tells the model to use the given ticker",
          "ticker" in cb.SYSTEM.lower(), "no mention of the ticker field")

    print("[2] the ticker actually reaches the payload")
    # Verified by inspecting what would be sent, not by trusting the prompt.
    captured = {}

    class _FakeMsg:
        content = [type("B", (), {"type": "text", "text": "ok"})()]

    class _FakeClient:
        def __init__(self, **kw):
            pass

        class messages:
            @staticmethod
            def create(**kw):
                captured.update(kw)
                return _FakeMsg()

    import sys
    import types
    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda **kw: _FakeClient(**kw)
    real = sys.modules.get("anthropic")
    sys.modules["anthropic"] = fake
    saved_key = config.ANTHROPIC_API_KEY
    config.ANTHROPIC_API_KEY = "test-key-not-real"
    try:
        def sent_text() -> str:
            # Read the message content directly rather than str()-ing the whole
            # structure: repr escaping turns 'ticker' into \'ticker\' and makes
            # a literal substring match fail against a payload that is correct.
            msgs = captured.get("messages") or []
            return " ".join(str(m.get("content", "")) for m in msgs)

        cb._claude_brief(BIAS, GEX, LEVELS, SMT, NEWS, "SPY")
        body = sent_text()
        check("payload contains a ticker field", "ticker" in body, body[:200])
        check("payload carries SPY", "SPY" in body, body[:200])
        check("system prompt is sent", bool(captured.get("system")))

        captured.clear()
        cb._claude_brief(BIAS, GEX, LEVELS, SMT, NEWS, "nvda")
        body = sent_text()
        check("ticker is upper-cased", "NVDA" in body, body[:200])
        check("lower-case form is not sent", "'nvda'" not in body, body[:200])
    finally:
        config.ANTHROPIC_API_KEY = saved_key
        if real is not None:
            sys.modules["anthropic"] = real
        else:
            sys.modules.pop("anthropic", None)

    print("[3] source metadata is honest")
    saved = config.ANTHROPIC_API_KEY
    try:
        config.ANTHROPIC_API_KEY = ""
        m = cb.generate_brief_meta(BIAS, GEX, LEVELS, SMT, NEWS, "SPY")
        check("no key -> source template", m["source"] == "template", m["source"])
        check("no key -> error explains why", "ANTHROPIC" in (m["error"] or ""), str(m["error"]))
        check("no key -> model is None", m["model"] is None, str(m["model"]))
        check("text is still produced", bool(m["text"]), "empty brief")
    finally:
        config.ANTHROPIC_API_KEY = saved

    print("[4] the templated brief names the real levels")
    t = cb._template_brief(BIAS, GEX, LEVELS, SMT, NEWS)
    for token in ("745", "742.46", "757", "733"):
        check(f"template mentions {token}", token in t)
    check("template never says MNQ", "MNQ" not in t)

    print("[5] live call names the right instrument")
    if not config.ANTHROPIC_API_KEY:
        skip("live brief", "no ANTHROPIC_API_KEY — offline checks still ran")
    else:
        m = cb.generate_brief_meta(BIAS, GEX, LEVELS, SMT, NEWS, "SPY")
        check("source is claude", m["source"] == "claude", f'{m["source"]} / {m["error"]}')
        text = m["text"]
        check("brief mentions SPY", "SPY" in text, text[:120])
        # The exact failure that shipped.
        for wrong in ("MNQ", "Nasdaq 100 futures"):
            check(f"brief does not say {wrong!r}", wrong not in text,
                  text[:160])

    print()
    if SKIPPED:
        print(f"skipped: {', '.join(SKIPPED)}")
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS[:6])}")
        raise SystemExit(1)
    print("all brief checks passed")


if __name__ == "__main__":
    main()
