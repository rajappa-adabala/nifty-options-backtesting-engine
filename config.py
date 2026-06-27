"""
config.py — Central configuration for the options backtester.
Edit values here; all modules import from this file.
"""

# ── Instrument ────────────────────────────────────────────────────────────────
SYMBOL = "NIFTY"          # NIFTY | BANKNIFTY
LOT_SIZE = 50             # NIFTY = 50, BANKNIFTY = 15

# ── Cost model ────────────────────────────────────────────────────────────────
SLIPPAGE_PCT = 0.5        # % of option premium, applied on each leg entry/exit
BROKERAGE_PER_ORDER = 20  # Flat ₹20 per order (Zerodha-style)
STT_SELL_PCT = 0.0625     # STT on sell side (options) as % of premium
SEBI_CHARGES = 0.0001     # 0.01% of turnover

# ── Risk management ───────────────────────────────────────────────────────────
# Set to None to disable stop-loss; or a float like 50.0 for 50% SL
# meaning: exit if combined premium rises 50% above received premium
STOPLOSS_PCT = 50.0

# ── Data paths ────────────────────────────────────────────────────────────────
DATA_RAW_DIR = "data/raw"
DATA_PROCESSED_DIR = "data/processed"
RESULTS_DIR = "results"

# ── Database (optional) ───────────────────────────────────────────────────────
USE_DB = False
DB_URL = "postgresql://user:password@localhost:5432/options_bt"
# Tables will be auto-created on first run if USE_DB = True

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"   # DEBUG | INFO | WARNING | ERROR