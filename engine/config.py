"""
Central configuration for the NYAM Bias Engine.

Everything you'll want to tweak lives here so you don't have to dig through
the code. Read the comments — they explain WHY each setting exists.
"""
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo


def _base_dir() -> str:
    """
    The directory the engine should read config from and write data to.

    `os.path.dirname(__file__)` is WRONG once frozen: PyInstaller resolves it
    to the bundle's internal archive path, not the folder the executable lives
    in. That silently broke two things in the packaged build — `.env` was never
    found (so chat reported "no key" with a key sitting right there), and
    STORE_DIR pointed inside the app's internals, meaning the track record and
    the daily OI snapshots were being written somewhere that gets replaced on
    every reinstall. OI history cannot be re-fetched, so that one loses data
    permanently.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()


def _load_dotenv():
    """
    Read `engine/.env` into the environment, if it exists.

    Secrets live in a gitignored file next to the engine rather than in a
    global user environment variable. A machine-wide variable is readable by
    every process the user runs, which is a large blast radius for a key that
    only one program needs. It also travels with the deployed app folder, so
    the packaged build picks it up without a separate setup step.

    A REAL environment variable always wins — this only fills in what isn't
    already set, so `set ANTHROPIC_API_KEY=... && nyam-terminal.exe` still
    overrides the file for a one-off.
    """
    path = os.path.join(BASE_DIR, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except FileNotFoundError:
        pass
    except OSError:
        # An unreadable .env must not take the engine down; the app runs fine
        # without a key and the UI already reports chat as disabled.
        pass


_load_dotenv()

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
# THIS IS A MODELLING CHOICE, NOT A TUNING KNOB. Measured against a live SPY
# chain — the window does not adjust the answer, it selects it:
#
#     dte    flip     put wall    net gex
#       3   769.92      765.0       834M
#       7   764.52      763.0      3.75B     <- current
#      14   763.90      755.0      6.36B
#
# Going 7 -> 14 moves the put wall EIGHT POINTS and quadruples net gamma. That
# is where a stop goes. Changing this changes every level, therefore every
# call, therefore the track record: bump bias_engine.MIX_VERSION alongside it
# or the hit rate silently averages two different systems.
#
# 7 is defensible — 0DTE and near-dated dominate dealer hedging into the open,
# which is the window these calls are made for — but it is a decision, and it
# is the reason our put wall sits further from spot than UW's full-chain scan.
GEX_MAX_DTE = 7            # include expiries within this many days

# NOT load-bearing, and now measured rather than asserted. Across r from 0.00
# to 0.08 — far wider than short rates will move — the flip shifts 0.7 points
# in total and the call wall, put wall and magnet do not move at all. Net gamma
# drifts ~5%.
#
# So this being a stale snapshot of a rate that changes does NOT matter, which
# is worth knowing precisely because it looks like the sort of thing that would.
# verify_constants.py pins the insensitivity, so if the model ever changes in a
# way that makes r matter, that fails rather than passing quietly.
RISK_FREE_RATE = 0.043     # ~short rate; verified low-impact, see above

# ----------------------------------------------------------------------------
# SCHEDULE (all times America/New_York)
# ----------------------------------------------------------------------------
TZ = ZoneInfo("America/New_York")


def today() -> dt.date:
    """
    The current TRADING day, in exchange time. Use this, never date.today().

    date.today() returns the MACHINE'S local date, and the engine had both
    forms scattered through it: capture folders named from ET, the missed-days
    audit comparing those folders against a local date, grading deciding what
    counts as "past" locally, and — worst — chain_to_expiries computing DTE
    locally.

    On a machine sitting in ET the two agree and nothing shows. On a UTC host
    they diverge every evening after 8pm ET, and a DTE wrong by one day changes
    t_years, which changes gamma, which changes every level on the board.
    Silently, and only outside the timezone it was written in.

    A server was already under discussion, so this is not hypothetical.
    """
    return dt.datetime.now(TZ).date()


def now_et() -> dt.datetime:
    """Timezone-aware current time in exchange time."""
    return dt.datetime.now(TZ)


PREMARKET_START = "07:00"   # begin auto-refreshing
MARKET_OPEN = "09:30"       # the moment your bias is for
REFRESH_SECONDS = 60        # frontend polls /api/bias this often

# The scheduler used to stop at 09:59 — it only ever covered pre-market. That
# left the two hours you actually trade (09:30–12:00) with no auto-refresh at
# all: the board froze on its 10:00 snapshot while the age chip quietly climbed.
# Auto-refresh now runs through the window the bias is graded over.
SESSION_REFRESH_UNTIL = "12:00"

# How often to re-pull the option CHAIN, as opposed to the spot price.
#
# One refresh is 9 Yahoo requests, 5 of them option_chain calls — and Yahoo
# updates open interest ONCE A DAY. Re-pulling all of it every 60s across a
# six-hour window is 3,240 requests/morning for data that changes once. Spot
# moves constantly and genuinely changes GEX (gamma is spot-dependent), so the
# quote is refreshed every cycle and the chain is reused between pulls.
CHAIN_REFRESH_SECONDS = 300
SNAPSHOT_AND_LOG_AT = "09:25"  # auto-write the Obsidian note 5 min before open

# ----------------------------------------------------------------------------
# OBSIDIAN
# ----------------------------------------------------------------------------
# Point this at your vault. The logger writes one markdown note per morning.
# Leave as-is to write into ./obsidian_out for testing.
OBSIDIAN_VAULT = os.getenv("NYAM_VAULT", os.path.join(BASE_DIR, "obsidian_out"))
OBSIDIAN_SUBFOLDER = "NYAM Bias"   # notes land in <vault>/<subfolder>/YYYY-MM-DD.md

# ----------------------------------------------------------------------------
# TRACK RECORD
# ----------------------------------------------------------------------------
# Where predictions + outcomes are stored (one JSON file you can open & inspect).
STORE_DIR = os.path.join(BASE_DIR, "data_store")

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
