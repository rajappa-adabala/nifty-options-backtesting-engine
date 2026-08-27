# NSE Options Backtesting Engine

A backtesting engine for Indian options strategies, built from scratch in Python — no Backtrader, no Zipline. Tests strategy logic against real minute-level NIFTY options data with realistic cost modeling, exposes the engine as a REST API, persists results across PostgreSQL and MongoDB, and parallelizes execution across CPU cores.

Backtested on **4.86M rows of real NSE options data** spanning 78 weekly expiries (Oct 2024 – Mar 2026). Sell ATM Straddle on expiry day: **+₹86,358 net P&L, 56.4% win rate**, on actual market premiums and IV.

---

## Real Backtest Results

```
==============================================================
   BACKTEST SUMMARY — ATM STRADDLE (NIFTY)
==============================================================
  Period         : 2024-10-01 → 2026-03-31
  Total Expiries : 78
  Trades Taken   : 78
  Wins           : 44     (56.4%)
  Losses         : 34     (43.6%)
--------------------------------------------------------------
  Gross P&L      : ₹    98,963.43
  Total Costs    : ₹    12,605.60   (brokerage + slippage)
  Net P&L        : ₹    86,357.83
--------------------------------------------------------------
  Max Drawdown   : ₹    15,414.70
  Avg P&L/Trade  : ₹     1,107.15
  Best Trade     : ₹    19,260.77  (03-Feb-2026)
  Worst Trade    : ₹    -6,802.25  (05-Dec-2024)
==============================================================
```

Config: 50% stop-loss on premium received, 0.5% slippage per leg, ₹20 flat brokerage per order, NIFTY lot size 50. Full per-trade CSV saved automatically to `results/` on every CLI run.

---

## What this is

- Loads a **6GB minute-level options dataset** (4.86M rows after expiry-day filtering) via chunked pandas reads — no out-of-memory errors
- Reconstructs **per-minute market snapshots** (every strike, both CE/PE, real LTP/IV/OI) for each of 78 real expiry dates
- Runs a configurable, pluggable strategy engine tick by tick against those snapshots
- Applies a realistic cost model: per-leg slippage, flat brokerage, stop-loss on combined premium
- Falls back to GBM-simulated synthetic data when no real CSV is present, so it runs out of the box with zero setup
- Exposes the engine as a **REST API** (FastAPI) — async job submission, polling, paginated results
- Persists structured trades to **PostgreSQL** and high-volume raw snapshots to **MongoDB**
- **Parallelizes** backtests across CPU cores via multiprocessing for large date ranges
- CI on every push via **GitHub Actions** (38 unit tests + API integration tests)

---

## Architecture

```
                    ┌─────────────────┐
                    │   REST API      │  FastAPI — POST /backtest, GET /backtest/{id}
                    │  (api/main.py)  │  async job submission + polling
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
              ┌─────┤  Engine Layer   ├─────┐
              │     │ sequential  or  │     │
              │     │ parallel (mp)   │     │
              │     └────────┬────────┘     │
              │              │              │
      ┌───────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐
      │  Strategy    │ │ Portfolio  │ │ DataLoader │
      │ (pluggable)  │ │ (P&L, SL)  │ │(CSV/synth) │
      └──────────────┘ └─────┬──────┘ └────────────┘
                              │
                ┌─────────────┴─────────────┐
                │                           │
        ┌───────▼────────┐         ┌────────▼────────┐
        │   PostgreSQL    │         │    MongoDB      │
        │  trades, legs   │         │ raw snapshots   │
        │ (relational,    │         │(high-volume,    │
        │  low-volume)    │         │ schema-flexible)│
        └─────────────────┘         └─────────────────┘
```

**Why two databases:** trade records are structured, relational, and low-volume — a natural fit for Postgres with proper joins between trades and legs. Market snapshots are the opposite: ~375 documents per expiry day, each with a variable-length embedded contracts array (strikes get added/removed across expiries) — the shape Mongo is built for. Forcing both into one engine would mean either a rigid schema on high-volume tick data, or losing relational integrity on the trade ledger.

---

## Project Structure

```
options_backtester/
│
├── api/
│   ├── main.py             # FastAPI app: /backtest, /backtest/{id}, /backtest/{id}/trades
│   ├── schemas.py           # Pydantic request/response models, validation
│   └── jobs.py              # In-memory job store (thread-safe) for async job tracking
│
├── backtester/
│   ├── engine.py            # Sequential engine: iterate expiries → snapshots → entry/SL/exit
│   ├── parallel_engine.py   # Multiprocessing wrapper — fans expiries across CPU cores
│   ├── portfolio.py         # Position tracking, cost application, stop-loss checks
│   └── models.py            # Trade, Leg, OptionContract, MarketSnapshot dataclasses
│
├── strategies/
│   ├── base.py              # Abstract strategy interface (3 methods to implement)
│   └── atm_straddle.py      # Sell ATM CE + PE on expiry day, hold to EOD
│
├── data/
│   ├── raw/                 # Place your options CSV here (nifty_options.csv)
│   └── loader.py            # Real data loader (chunked) + synthetic GBM fallback
│
├── storage/
│   └── mongo_store.py        # MongoDB layer: snapshot bulk writes, job persistence
│
├── utils/
│   ├── options_math.py      # Black-Scholes: Delta, Gamma, Theta, Vega, IV solver
│   ├── nse_utils.py          # Expiry calendar (Thursday/Wednesday), ATM strike finder
│   └── db.py                 # PostgreSQL persistence (trades + legs, auto-creates schema)
│
├── scripts/
│   ├── run_backtest.py       # CLI entrypoint
│   └── download_data.py      # NSE bhavcopy downloader (best-effort)
│
├── tests/
│   ├── test_engine.py        # 38 unit tests — Black-Scholes, NSE utils, models, portfolio
│   └── test_api.py            # FastAPI integration tests (TestClient, full job lifecycle)
│
├── .github/workflows/ci.yml   # GitHub Actions: run tests on every push/PR
├── results/                    # Trade log CSVs land here after each CLI run
├── config.py                   # All tunables: Postgres, Mongo, slippage, concurrency
└── requirements.txt
```

---

## Quickstart

```bash
git clone https://github.com/YOUR_USERNAME/options-backtester.git
cd options-backtester
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Option A — CLI (synthetic data, zero setup)

```bash
python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY --from 2023-01-01 --to 2023-12-31 --stoploss 50
```

### Option B — CLI with real data

Drop a minute-level options CSV at `data/raw/nifty_options.csv` with columns:
```
strike_price, option_type, expiry, timestamp, ltp, volume, oi,
underlying_spot_price, iv, delta, gamma, theta, vega, rho
```

```bash
python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY --from 2024-10-01 --to 2026-03-31 --stoploss 50 --slippage 0.5
```

First run takes 60–120s to load and cache the file (chunked at 500k rows/chunk).

### Option C — REST API

```bash
uvicorn api.main:app --reload --port 8000
```

Open `http://localhost:8000/docs` for interactive Swagger UI, or call it directly:

```bash
# Submit a backtest
curl -X POST http://localhost:8000/backtest \
  -H "Content-Type: application/json" \
  -d '{
    "strategy": "atm_straddle",
    "symbol": "NIFTY",
    "from_date": "2024-10-01",
    "to_date": "2024-12-31",
    "stoploss_pct": 50,
    "slippage_pct": 0.5,
    "parallel": true
  }'
# -> {"job_id": "a1b2c3...", "status": "pending", "message": "..."}

# Poll for status / result
curl http://localhost:8000/backtest/a1b2c3...

# Get paginated trade log
curl "http://localhost:8000/backtest/a1b2c3.../trades?page=1&page_size=20"
```

### Option D — Parallel backtest (Python)

```python
from datetime import datetime
from backtester.parallel_engine import ParallelBacktestEngine
from strategies.atm_straddle import ATMStraddleStrategy

engine = ParallelBacktestEngine(
    strategy_cls=ATMStraddleStrategy,
    symbol="NIFTY",
    start_date=datetime(2024, 10, 1),
    end_date=datetime(2026, 3, 31),
    num_workers=4,
)
portfolio = engine.run()
print(portfolio.summary())
```

On the full 78-expiry real dataset, 4 workers cuts wall-clock time from ~4.5 minutes (sequential) to roughly 75–90 seconds, since each expiry's backtest is fully independent and embarrassingly parallel.

### Enable PostgreSQL + MongoDB

In `config.py`:
```python
USE_DB = True
DB_URL = "postgresql://user:pass@localhost:5432/options_bt"

USE_MONGO = True
MONGO_URL = "mongodb://localhost:27017"
```

Tables/indexes auto-create on first use — no manual migration step.

### Run tests

```bash
# Unit tests (engine, math, models — 38 tests)
pytest tests/test_engine.py -v

# API integration tests (full job lifecycle via TestClient)
pytest tests/test_api.py -v

# Everything
pytest tests/ -v
```

CI runs both suites on every push via `.github/workflows/ci.yml`.

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/backtest` | Submit a job (202 Accepted, returns `job_id`) |
| `GET` | `/backtest/{job_id}` | Status + summary P&L |
| `GET` | `/backtest/{job_id}/trades` | Paginated trade-level log |
| `DELETE` | `/backtest/{job_id}` | Remove job from store |

Jobs run as FastAPI `BackgroundTasks` so `POST /backtest` returns immediately rather than blocking for minutes on large date ranges. Request validation (date ranges, stop-loss bounds, strategy enum) happens via Pydantic before a job is even created.

---

## CLI Reference

| Flag | Description | Default |
|---|---|---|
| `--strategy` | Strategy name (`atm_straddle`) | required |
| `--symbol` | `NIFTY` or `BANKNIFTY` | `NIFTY` |
| `--from` / `--to` | Date range (`YYYY-MM-DD`) | required |
| `--stoploss` | Stop-loss % on premium received | disabled |
| `--slippage` | Slippage % per leg | `0.5` |
| `--lot-size` | Override lot size | `50` |
| `--save-db` | Persist trades to PostgreSQL | off |
| `--verbose` | DEBUG-level logs | off |

---

## Strategy: Sell ATM Straddle on Expiry Day

1. On expiry morning, find the strike closest to spot (ATM)
2. Sell 1 lot ATM CE + 1 lot ATM PE
3. Hold until EOD, or until combined premium rises past the stop-loss threshold
4. Buy back (or let expire) at close
5. Net P&L = premium collected − premium paid back − brokerage − slippage

A theta-decay play: on expiry day, time value collapses fastest and IV crush accelerates. Profits when the underlying stays range-bound; loses on large directional moves, which the stop-loss caps.

### Adding a new strategy

```python
from strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    def should_enter(self, snapshot) -> bool: ...
    def get_legs(self, snapshot, lot_size) -> list: ...
    def should_exit(self, trade, snapshot) -> bool: ...
```

Register it in `strategies/__init__.py`'s `STRATEGY_REGISTRY`, add it to `api/schemas.py`'s `StrategyName` enum, and it's immediately available via both CLI (`--strategy`) and API.

---

## Known limitations (documented honestly, not hidden)

- **Data anomaly**: one trade (26-Dec-2024) shows an inflated premium caused by a stale far-OTM row briefly matching the ATM lookup at market open. A production fix would constrain ATM search to strikes within ~2% of spot.
- **Config mutation in concurrent API jobs**: `_run_backtest_job` currently mutates the shared `config` module for stop-loss/slippage overrides, which would race if two jobs with different parameters ran concurrently. A production version would thread these as explicit engine constructor arguments instead.
- **Job store is in-memory**: fine for a single-process deployment; horizontally scaling the API would require moving job state to Redis so all replicas see the same status.
- **Parallel engine re-reads data per worker**: each process loads its own slice of the CSV rather than sharing one in-memory DataFrame, trading some I/O redundancy for simpler process isolation.

---

## Tech Stack

Python 3.10+, FastAPI, Pydantic, pandas, numpy, scipy (Black-Scholes), PostgreSQL (psycopg2), MongoDB (pymongo), multiprocessing, pytest, GitHub Actions.

## License

MIT