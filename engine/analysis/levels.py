"""
levels.py  --  Prior-session key levels and confluences.

Maps directly onto your ICT session framework: prior day high/low/mid,
the overnight (Globex) range, and which GEX levels line up with them.
A "confluence" is when two independent things point at the same price --
e.g. the prior day high sitting right on the call wall. Those are the
levels that actually matter at the open.
"""


def session_levels(price_data: dict) -> dict:
    """
    `price_data` carries the raw OHLC we need:
        {
          "prior_high", "prior_low", "prior_close",
          "on_high", "on_low",      # overnight / Globex range
          "spot"                    # current/pre-market price
        }
    """
    pdh = price_data["prior_high"]
    pdl = price_data["prior_low"]
    pdm = round((pdh + pdl) / 2, 2)
    return {
        "prior_day_high": pdh,
        "prior_day_low": pdl,
        "prior_day_mid": pdm,
        "prior_close": price_data["prior_close"],
        "overnight_high": price_data["on_high"],
        "overnight_low": price_data["on_low"],
        "spot": price_data["spot"],
    }


def find_confluences(levels: dict, gex: dict, tolerance_pct: float = 0.0015) -> list:
    """
    Flag where a session level lines up with a GEX level (within tolerance).
    These stacked levels are your highest-conviction reaction points.
    """
    confluences = []
    session_points = {
        "Prior Day High": levels["prior_day_high"],
        "Prior Day Low": levels["prior_day_low"],
        "Prior Day Mid": levels["prior_day_mid"],
        "Overnight High": levels["overnight_high"],
        "Overnight Low": levels["overnight_low"],
    }
    gex_points = {
        "Call Wall": gex.get("call_wall"),
        "Put Wall": gex.get("put_wall"),
        "Gamma Flip": gex.get("gamma_flip"),
    }
    for s_name, s_val in session_points.items():
        for g_name, g_val in gex_points.items():
            if s_val and g_val and abs(s_val - g_val) / s_val <= tolerance_pct:
                confluences.append({
                    "price": round((s_val + g_val) / 2, 2),
                    "label": f"{s_name} + {g_name}",
                })
    return confluences
