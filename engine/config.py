"""
Central configuration for the NYAM Bias Engine.

Everything you'll want to tweak lives here so you don't have to dig through
the code. Read the comments — they explain WHY each setting exists.
"""
import os
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------------------
# DATA MODE
# ----------------------------------------------------------------------------
# Start in mock mode so the whole app runs with ZERO keys and ZERO cost.
# Flip to False once you've installed yfinance and want real option chains.
USE_MOCK_DATA = os.getenv("NYAM_MOCK", "1") == "1"

# Live data provider (ignored in mock mode):
#   "yahoo"  free, ~15-min delayed chains, no OI change, no flow, no dark pool
#   "uw"     Unusual Whales — real-time chains, day-over-day OI, flow alerts,
#            dark pool prints. Needs UW_API_KEY. Falls back to Yahoo per-piece
#            for anything your tier doesn't cover, rather than failing the load.
# Verify your key first:  python -m data.unusual_whales probe SPY
PROVIDER = os.getenv("NYAM_PROVIDER", "yahoo").lower()
UW_API_KEY = os.getenv("UW_API_KEY", "")

# If you ever want to use Claude for the written brief, set this env var:
#   export ANTHROPIC_API_KEY=sk-ant-...
# Without it, the app falls back to a templated brief built from the signals,
# so it still runs and shows you the layout. (No key = no cost.)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Model for the morning brief and the in-app chat.
# claude-opus-5 is the current flagship. claude-sonnet-5 is cheaper and still
# strong; claude-haiku-4-5 is the cheapest if you only want the templated-brief
# upgrade. A morning brief is a small prompt and a short output either way.
CLAUDE_MODEL = os.getenv("NYAM_CLAUDE_MODEL", "claude-opus-5")

# How many turns of chat history to keep. The snapshot is re-injected fresh on
# every turn, so old turns only carry the conversation, not stale market data.
CHAT_MAX_TURNS = 20

# ----------------------------------------------------------------------------
# MARKET / TICKERS
# ----------------------------------------------------------------------------
# The default underlying the dashboard opens on. Changed at runtime from the
# ticker selector in the top-left; this is only the starting value.
PRIMARY_TICKER = os.getenv("NYAM_TICKER", "SPY")

# What the selector offers. Add anything with a liquid option chain.
TICKERS = ["SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "AMZN", "META", "MSFT", "GOOGL"]

# SMT divergence needs a CONFIRMER: a correlated instrument that should be
# making the same highs/lows. When one index makes a new overnight extreme and
# the other doesn't, that non-confirmation often front-runs a reversal.
# SPY<->QQQ is the ES/NQ proxy pair. Single names confirm against their index.
SMT_CONFIRMER = {"SPY": "QQQ", "QQQ": "SPY", "IWM": "SPY"}
SMT_DEFAULT_CONFIRMER = "SPY"   # single names (NVDA, TSLA, ...) confirm vs SPY


def confirmer_for(ticker: str) -> str:
    """The SMT partner for `ticker`. Never returns the ticker itself."""
    c = SMT_CONFIRMER.get(ticker.upper(), SMT_DEFAULT_CONFIRMER)
    return "QQQ" if c == ticker.upper() else c


# How many expirations to roll into the GEX aggregation.
# 0DTE + near-dated dominate dealer hedging into the open, so keep this tight.
GEX_MAX_DTE = 7            # include expiries within this many days
RISK_FREE_RATE = 0.043     # ~current short rate; only affects gamma slightly

# ----------------------------------------------------------------------------
# SCHEDULE (all times America/New_York)
# ----------------------------------------------------------------------------
TZ = ZoneInfo("America/New_York")
PREMARKET_START = "07:00"   # begin auto-refreshing
MARKET_OPEN = "09:30"       # the moment your bias is for
REFRESH_SECONDS = 60        # frontend polls /api/bias this often
SNAPSHOT_AND_LOG_AT = "09:25"  # auto-write the Obsidian note 5 min before open

# ----------------------------------------------------------------------------
# OBSIDIAN
# ----------------------------------------------------------------------------
# Point this at your vault. The logger writes one markdown note per morning.
# Leave as-is to write into ./obsidian_out for testing.
OBSIDIAN_VAULT = os.getenv("NYAM_VAULT", os.path.join(os.path.dirname(__file__), "obsidian_out"))
OBSIDIAN_SUBFOLDER = "NYAM Bias"   # notes land in <vault>/<subfolder>/YYYY-MM-DD.md

# ----------------------------------------------------------------------------
# TRACK RECORD
# ----------------------------------------------------------------------------
# Where predictions + outcomes are stored (one JSON file you can open & inspect).
STORE_DIR = os.path.join(os.path.dirname(__file__), "data_store")

# THE GRADING WINDOW — grade the bias over the hours you actually trade.
# You trade the open until roughly noon, so grading open->close was scoring the
# call over ~4 hours you aren't in the market. A lean that's right at 12:00 and
# wrong by 16:00 was being marked a loss you never took.
GRADE_EXIT_TIME = "12:00"      # America/New_York; set to "16:00" for open->close

# A move smaller than this (%) counts as a flat/range day, which is how a
# NEUTRAL lean gets graded correct.
#
# MEASURED, not guessed — but on a thin sample, so treat it as a starting
# point. Over 60 SPY sessions the open->12:00 move is ~0.79x the size of the
# open->close move (median absolute), and 0.175% is the band that leaves the
# same share of days flat (~27%) as 0.15% did on the full-day window. That
# keeps NEUTRAL exactly as hard to score as it was before.
#
# The 0.79 ratio is NOT stable: split those 60 sessions in half and it moves
# 0.65 -> 0.92 (and QQQ runs 0.87 -> 0.67 the other way). Re-derive it on your
# own data once you have more:  python measure_grade_band.py
GRADE_BAND_PCT = 0.175

# ONE BAND DOES NOT FIT EVERY TICKER. A fixed 0.175% leaves ~27% of SPY
# sessions flat but ~40% of QQQ's, because QQQ simply moves more (median
# open->12:00 of 0.46% vs SPY's 0.28%). That would make NEUTRAL substantially
# easier to score on QQQ than on SPY and quietly break any comparison between
# their hit rates — which matters now that the track record is per ticker.
# Values below are measured; anything not listed falls back to GRADE_BAND_PCT.
GRADE_BAND_BY_TICKER = {
    "SPY": 0.175,
    "QQQ": 0.10,
}


def grade_band_for(ticker: str) -> float:
    return GRADE_BAND_BY_TICKER.get((ticker or "").upper(), GRADE_BAND_PCT)


def grade_rule_for(ticker: str) -> str:
    """Stamped onto every graded record so a later change to the window or the
    band can't silently blend incomparable results into one hit rate."""
    return f"open->{GRADE_EXIT_TIME}@{grade_band_for(ticker)}"


GRADE_RULE = f"open->{GRADE_EXIT_TIME}@{GRADE_BAND_PCT}"

GRADE_TIME = "16:15"   # when to run grading (America/New_York)
