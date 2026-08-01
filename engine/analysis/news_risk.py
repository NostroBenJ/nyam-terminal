"""
news_risk.py  --  Turn the live news feed into the bias engine's risk input.

WHY THIS EXISTS
`bias_engine.build_bias` reduces conviction when `news["high_impact"]` is true,
but the only producer of that flag was mock data (hardcoded True) and
`_live_news` (hardcoded False). So the signal fired exactly never on live data
— the panel rendered, the reason text was written, and the whole path was dead.

WHAT THIS IS, AND IS NOT
This detects high-impact macro news that has ALREADY LANDED, by reading the
primary-tier feeds (Fed, BLS, SEC) that publish releases as they happen. A CPI
print hitting the wire at 08:30 is exactly the thing that should cut conviction
on a 09:30 bias.

It is NOT a forward calendar. It cannot tell you that CPI is due at 08:30
tomorrow — RSS carries what was published, not what is scheduled. That is a
separate source and it is not built. The distinction is preserved in the
output (`kind: "landed"`) rather than blurred, because "an event happened" and
"an event is coming" call for opposite trades.
"""
import re

# Events that reprice the whole index, not one name. Ordered by how violently
# they tend to move the tape on release.
HIGH_IMPACT = [
    (r"\bfomc\b", "FOMC"),
    (r"\bfederal open market committee\b", "FOMC"),
    (r"\brate (decision|cut|hike)\b", "Rate decision"),
    (r"\bconsumer price index\b|\bcpi\b", "CPI"),
    (r"\bproducer price index\b|\bppi\b", "PPI"),
    (r"\bpersonal consumption expenditures\b|\bpce\b", "PCE"),
    (r"\bemployment situation\b", "Employment Situation"),
    (r"\bnonfarm payroll|\bnon-farm payroll", "Nonfarm Payrolls"),
    (r"\bjobs report\b", "Jobs report"),
    (r"\bgross domestic product\b|\bgdp\b", "GDP"),
]

MEDIUM_IMPACT = [
    (r"\bjobless claims\b|\bunemployment insurance weekly\b", "Jobless Claims"),
    (r"\bism\b", "ISM"),
    (r"\bretail sales\b", "Retail Sales"),
    (r"\bconsumer confidence\b|\bconsumer sentiment\b", "Consumer Sentiment"),
    (r"\bdurable goods\b", "Durable Goods"),
    (r"\bbeige book\b", "Beige Book"),
]

#: How far back a release still counts as "today's risk". A CPI print from four
#: hours ago is still driving the tape at the open; one from three days ago is
#: history and the market has already traded it.
DEFAULT_WINDOW_H = 10.0


def _match(text: str, patterns) -> str | None:
    for rx, label in patterns:
        if re.search(rx, text, re.IGNORECASE):
            return label
    return None


def assess(items: list, window_hours: float = DEFAULT_WINDOW_H) -> dict:
    """
    Classify recent news into a risk read the bias engine can consume.

    Returns the shape `build_bias` already expects — {high_impact, headline,
    items} — plus the detail needed to explain itself on screen.

    Tier matters as much as wording. "Inflation" in a market-commentary
    headline is chatter; the same word on a BLS release is the event. A
    high-impact term from a primary source is HIGH; the same term from the
    financial press is MEDIUM, because the press writes about releases all day
    and treating every mention as an event would keep conviction permanently
    reduced — which is the same as having no signal at all.
    """
    drivers, seen = [], set()

    for it in items or []:
        age = it.get("age_hours")
        # Undated items are excluded rather than assumed recent. Treating one
        # as fresh could cut conviction off a headline of unknown vintage.
        if age is None or age > window_hours:
            continue

        text = f"{it.get('title', '')} {it.get('summary', '')}"
        primary = it.get("tier") == "primary"

        label = _match(text, HIGH_IMPACT)
        level = None
        if label:
            level = "high" if primary else "medium"
        else:
            label = _match(text, MEDIUM_IMPACT)
            if label:
                level = "medium" if primary else "low"

        if not level or level == "low":
            continue
        key = (label, level)
        if key in seen:
            continue
        seen.add(key)
        drivers.append({
            "event": label,
            "impact": level,
            "source": it.get("source", ""),
            "title": it.get("title", ""),
            "age_hours": round(age, 1),
            "primary": primary,
        })

    drivers.sort(key=lambda d: (d["impact"] != "high", d["age_hours"]))
    high = [d for d in drivers if d["impact"] == "high"]

    if high:
        top = high[0]
        headline = f"{top['event']} — {top['source']} ({top['age_hours']:.0f}h ago)"
        why = (f"{top['event']} landed {top['age_hours']:.0f}h ago via {top['source']}. "
               f"Positioning built before a release is stale after it — size down "
               f"or wait for the reaction to settle.")
    elif drivers:
        top = drivers[0]
        headline = f"{top['event']} — {top['source']} ({top['age_hours']:.0f}h ago)"
        why = (f"{top['event']} landed {top['age_hours']:.0f}h ago. Secondary "
               f"driver — worth knowing, not enough to stand down.")
    else:
        headline = ""
        why = f"No high-impact macro release in the last {window_hours:.0f}h."

    return {
        # what bias_engine reads
        "high_impact": bool(high),
        "headline": headline,
        # what the UI renders
        "items": [{"time": f"{d['age_hours']:.0f}h ago", "event": d["event"],
                   "impact": d["impact"]} for d in drivers],
        # provenance
        "kind": "landed",
        "level": "high" if high else ("medium" if drivers else "none"),
        "why": why,
        "window_hours": window_hours,
        "drivers": drivers,
    }
