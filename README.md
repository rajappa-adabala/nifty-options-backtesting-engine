# Options Backtesting Engine

A clean, production-style Python backtesting engine for **NSE options strategies** — built specifically for Indian derivatives markets.

Supports strategy-level backtesting on historical NIFTY/BANKNIFTY options data with realistic P&L calculation, slippage modeling, stop-loss handling, and trade logging.

---

## Features

- **Sell ATM Straddle on Expiry Day** strategy (and extensible to any)
- Realistic slippage + brokerage model (Zerodha-style flat ₹20/order)
- Stop-loss support (strategy-level and leg-level)
- Detailed trade log exported to CSV
- Summary P&L report per expiry
- Modular strategy interface — plug in your own strategies
- Uses **free NSE historical data** (no API key needed)
- PostgreSQL storage for snapshots (optional, togglable)

---

## Project Structure

```
options_backtester/
│
├── backtester/
│   ├── engine.py          # Core backtesting loop
│   ├── portfolio.py       # Position & P&L tracking
│   └── models.py          # Trade, Position, OrderResult dataclasses
│
├── strategies/
│   ├── base.py            # Abstract strategy interface
│   └── atm_straddle.py    # ATM Straddle on expiry day
│
├── data/
│   ├── raw/               # Place downloaded CSV files here
│   ├── processed/         # Auto-generated cleaned data
│   └── loader.py          # Data loading & preprocessing
│
├── utils/
│   ├── options_math.py    # Greeks: Delta, Gamma, Vega, Theta (Black-Scholes)
│   ├── nse_utils.py       # Expiry calendars, ATM strike finder
│   └── db.py              # PostgreSQL storage (optional)
│
├── scripts/
│   ├── download_data.py   # Download historical data from NSE/Unofficed
│   └── run_backtest.py    # CLI entrypoint
│
├── tests/
│   └── test_engine.py     # Unit tests
│
├── results/               # CSV trade logs auto-saved here
├── requirements.txt
├── config.py              # All config in one place
└── README.md
```

---

## Quickstart

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/options-backtester.git
cd options-backtester
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Download historical data

```bash
python scripts/download_data.py --symbol NIFTY --year 2023
```

This downloads free NSE options data from [Unofficed](https://www.unofficed.com/) or uses bundled sample data.

### 3. Run a backtest

```bash
# Basic run
python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY --from 2023-01-01 --to 2023-12-31

# With stop-loss
python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY --from 2023-01-01 --to 2023-12-31 --stoploss 50

# With slippage
python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY --from 2023-01-01 --to 2023-12-31 --slippage 2.0
```

### 4. View results

Results are saved to `results/trade_log_YYYYMMDD_HHMMSS.csv`. The terminal also prints a full summary:

```
============================================================
         BACKTEST SUMMARY — ATM Straddle (NIFTY)
============================================================
Period         : 2023-01-01 → 2023-12-31
Total Expiries : 48
Trades Taken   : 48
Wins           : 31   (64.6%)
Losses         : 17   (35.4%)
------------------------------------------------------------
Gross P&L      : ₹ 1,24,350
Brokerage      : ₹   1,920
Net P&L        : ₹ 1,22,430
Max Drawdown   : ₹  18,200
Avg P&L/Trade  : ₹   2,550
Best Trade     : ₹  12,400
Worst Trade    : ₹  -9,800
============================================================
```

---

## Data Sources (Free, No API Key)

| Source | What | URL |
|--------|------|-----|
| NSE India | Official options bhavcopy (EOD) | https://www.nseindia.com/market-data/historical-data |
| Unofficed | Minute-level options data (limited free) | https://www.unofficed.com |
| Sample CSV | Bundled 3-month NIFTY data (2023 Q1) | `data/raw/sample_nifty_2023_Q1.csv` |

---

## Configuration (`config.py`)

```python
SYMBOL = "NIFTY"
LOT_SIZE = 50          # NIFTY lot size
SLIPPAGE_PCT = 0.5     # % slippage on each leg
BROKERAGE_PER_ORDER = 20   # Zerodha-style flat ₹20
STOPLOSS_PCT = None    # Set to e.g. 50 for 50% SL on premium received
USE_DB = False         # Set True + fill DB_URL to persist to PostgreSQL
DB_URL = "postgresql://user:pass@localhost:5432/options_bt"
```

---

## Strategy: ATM Straddle on Expiry Day

**Logic:**
1. On every weekly/monthly expiry morning, identify the ATM strike (closest to spot)
2. Sell 1 lot of ATM CE + 1 lot of ATM PE
3. Hold till EOD (or stop-loss hit)
4. Buy back at closing price
5. P&L = Premium collected − Premium paid back − Brokerage − Slippage

**Why this works (historically):** On expiry day, theta decay is maximal. IV crush accelerates. Most expiries close near ATM, making straddles profitable.

---

## Adding Your Own Strategy

```python
# strategies/my_strategy.py
from strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    def should_enter(self, snapshot) -> bool:
        # your entry logic
        return True

    def get_legs(self, snapshot) -> list:
        # return list of Leg objects to trade
        return [...]

    def should_exit(self, position, current_snapshot) -> bool:
        # your exit logic
        return False
```

Register it in `scripts/run_backtest.py` and pass `--strategy my_strategy`.

---

## Tech Stack

- **Python 3.10+**
- **pandas** — data manipulation
- **numpy** — numerical ops
- **scipy** — Black-Scholes Greeks
- **psycopg2** — PostgreSQL (optional)
- **argparse** — CLI
- **pytest** — testing

---

## License

MIT