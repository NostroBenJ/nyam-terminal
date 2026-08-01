"""
verify_news_risk.py  --  Checks on the news -> bias risk path.

This path was dead before: bias_engine reduced conviction on
`news["high_impact"]`, and nothing live ever set it true. These checks exist so
it can't quietly go dead again — a signal that never fires looks identical to a
market with no event risk.

    python verify_news_risk.py
"""
from analysis import news_risk as nr
from analysis import bias_engine

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def item(title, tier="primary", age=2.0, source="BLS", summary=""):
    return {"title": title, "summary": summary, "tier": tier,
            "age_hours": age, "source": source}


def main():
    print("[1] a primary-source CPI release is HIGH impact")
    r = nr.assess([item("Consumer Price Index — June 2026")])
    check("high_impact true", r["high_impact"] is True)
    check("level high", r["level"] == "high", r["level"])
    check("event named", r["drivers"][0]["event"] == "CPI", str(r["drivers"][:1]))
    check("headline non-empty", bool(r["headline"]), r["headline"])

    print("[2] the same wording from the PRESS is only medium")
    # Commentary mentions CPI all day. Treating every mention as an event keeps
    # conviction permanently reduced, which is the same as no signal at all.
    r = nr.assess([item("What the CPI print means for your portfolio",
                        tier="market", source="CNBC")])
    check("not high_impact", r["high_impact"] is False)
    check("level medium", r["level"] == "medium", r["level"])

    print("[3] stale releases don't count")
    r = nr.assess([item("Employment Situation", age=48.0)])
    check("48h-old excluded", r["level"] == "none", r["level"])
    r = nr.assess([item("Employment Situation", age=3.0)])
    check("3h-old counted", r["high_impact"] is True)

    print("[4] undated items are excluded, not assumed fresh")
    # Assuming recency here would cut conviction off a headline of unknown age.
    r = nr.assess([item("FOMC statement", age=None)])
    check("undated ignored", r["level"] == "none", r["level"])

    print("[5] no macro news -> explicit none, not a crash")
    for empty in ([], None, [item("Local bakery opens")]):
        r = nr.assess(empty)
        check(f"{type(empty).__name__} -> none", r["level"] == "none" and not r["high_impact"])
    check("why explains the window", "last" in r["why"].lower(), r["why"])

    print("[6] word boundaries — no substring false positives")
    # "ppi" inside "shipping", "gdp" inside "gdpr", "ism" inside "mechanism".
    for bad in ("New shipping rules take effect",
                "GDPR compliance deadline nears",
                "The mechanism behind the rally"):
        r = nr.assess([item(bad)])
        check(f"no match: {bad[:28]}", r["level"] == "none", r["level"])

    print("[7] duplicates collapse to one driver")
    r = nr.assess([item("CPI report released"), item("CPI report released again"),
                   item("CPI data out now")])
    check("one CPI driver", len(r["drivers"]) == 1, str(len(r["drivers"])))

    print("[8] high outranks medium in the headline")
    r = nr.assess([item("Jobless Claims", age=1.0), item("FOMC decision", age=5.0)])
    check("FOMC leads despite being older", r["drivers"][0]["event"] == "FOMC",
          r["drivers"][0]["event"])
    check("both retained", len(r["drivers"]) == 2, str(len(r["drivers"])))

    print("[9] it is labelled as landed news, not a forward calendar")
    # These call for opposite trades; blurring them would be worse than useless.
    r = nr.assess([item("CPI released")])
    check("kind == landed", r["kind"] == "landed", r["kind"])

    print("[10] bias_engine actually consumes it")
    gex = {"spot": 745.0, "regime": "positive", "gamma_flip": 742.0,
           "call_wall": 757.0, "put_wall": 733.0, "control_node": 750.0,
           "put_call_ratio": 1.0, "call_oi_change": 0, "put_oi_change": 0,
           "net_gex": 1e8, "atm_iv": 0.16}
    levels = {"prior_high": 746.0, "prior_low": 740.0, "prior_close": 744.0,
              "overnight_high": 747.0, "overnight_low": 741.0, "spot": 745.0}
    smt = {"signal": "none", "lean": 0, "note": "no divergence"}

    quiet = nr.assess([])
    loud = nr.assess([item("Consumer Price Index — June 2026")])

    b_quiet = bias_engine.build_bias(gex, levels, smt, quiet)
    b_loud = bias_engine.build_bias(gex, levels, smt, loud)
    check("quiet -> normal conviction", b_quiet["conviction"] == "normal",
          b_quiet["conviction"])
    check("release -> reduced conviction", b_loud["conviction"] == "reduced",
          b_loud["conviction"])
    check("News Risk signal appears only when it fires",
          any(s["name"] == "News Risk" for s in b_loud["signals"])
          and not any(s["name"] == "News Risk" for s in b_quiet["signals"]))
    check("summary mentions the reduction",
          "conviction" in b_loud["summary"].lower(), b_loud["summary"])
    # The whole point: direction must be unchanged, only confidence.
    check("score unchanged by news", b_quiet["score"] == b_loud["score"],
          f"{b_quiet['score']} vs {b_loud['score']}")

    print()
    if FAILS:
        print(f"{len(FAILS)} CHECK(S) FAILED: {', '.join(FAILS)}")
        raise SystemExit(1)
    print("all news-risk checks passed")


if __name__ == "__main__":
    main()
