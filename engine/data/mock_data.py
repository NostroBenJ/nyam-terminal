"""
mock_data.py  --  Realistic synthetic data so the app runs with no keys, no
network, no cost. Now serves MULTIPLE expirations so the multi-expiry
confluence feature has something to chew on.
"""
import random

random.seed(7)


def _build_chain(spot: float, dte: int, wall_bias: float = 0.0):
    """Synthetic calls/puts for one expiration."""
    calls, puts = [], []
    step = round(spot * 0.0025, 0) or 1
    t_years = max(dte, 0.5) / 365.0
    for i in range(-25, 26):
        strike = round(spot + i * step, 0)
        moneyness = abs(strike - spot) / spot
        iv = 0.16 + moneyness * 1.5
        call_oi = max(0, int(9000 * (1 - (i - wall_bias) / 30)) + random.randint(-600, 600)) if i >= -5 else random.randint(200, 1500)
        put_oi = max(0, int(9000 * (1 + (i + wall_bias) / 30)) + random.randint(-600, 600)) if i <= 5 else random.randint(200, 1500)
        calls.append({"strike": strike, "oi": call_oi, "iv": iv, "t_years": t_years, "oi_change": random.randint(-400, 900)})
        puts.append({"strike": strike, "oi": put_oi, "iv": iv, "t_years": t_years, "oi_change": random.randint(-400, 1200)})
    return {"calls": calls, "puts": puts}


# Rough reference prices so mock mode looks plausible per ticker. Only used
# offline for layout/demo work — never treat these as quotes.
_MOCK_SPOT = {
    "SPY": 745.00, "QQQ": 707.00, "IWM": 248.00, "NVDA": 182.00, "TSLA": 425.00,
    "AAPL": 268.00, "AMZN": 238.00, "META": 735.00, "MSFT": 512.00, "GOOGL": 205.00,
}


def _mock_spot(ticker: str) -> float:
    return _MOCK_SPOT.get(ticker.upper(), 250.00)


def mock_market(ticker: str = None):
    import config
    random.seed(7)
    ticker = (ticker or config.PRIMARY_TICKER).upper()
    confirmer = config.confirmer_for(ticker)
    spot = _mock_spot(ticker)
    conf_spot = _mock_spot(confirmer)
    # 5 expirations; walls mostly agree (high confluence) with slight variation
    expiry_specs = [
        ("Fri Jun 5", 0, 0.0),
        ("Mon Jun 8", 3, 0.0),
        ("Wed Jun 10", 5, 1.0),
        ("Fri Jun 12", 7, 0.0),
        ("Fri Jun 19", 14, -1.0),
    ]
    expiries = []
    for label, dte, bias in expiry_specs:
        ch = _build_chain(spot, dte, bias)
        expiries.append({"label": label, "dte": dte, "calls": ch["calls"], "puts": ch["puts"]})

    def _session(px, hi_mult, lo_mult, close_mult, onhi_mult, onlo_mult):
        return {"prior_high": round(px * hi_mult, 2), "prior_low": round(px * lo_mult, 2),
                "prior_close": round(px * close_mult, 2), "on_high": round(px * onhi_mult, 2),
                "on_low": round(px * onlo_mult, 2), "on_is_real": True}

    return {
        "primary": {
            "ticker": ticker,
            "spot": spot,
            "expiries": expiries,
            **_session(spot, 1.0017, 0.9936, 0.9987, 1.0030, 0.9958),
        },
        "secondary": {
            "ticker": confirmer,
            "spot": conf_spot,
            # deliberately does NOT make a new overnight high -> demo SMT signal
            **_session(conf_spot, 1.0020, 0.9960, 1.0001, 1.0012, 0.9973),
        },
        "news": {
            "high_impact": True,
            "headline": "10:00 ET — ISM Services PMI",
            "items": [
                {"time": "08:30", "event": "Initial Jobless Claims", "impact": "medium"},
                {"time": "10:00", "event": "ISM Services PMI", "impact": "high"},
            ],
        },
    }
