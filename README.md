# NIFTY Options Backtesting Engine

A production-style options backtesting engine for Indian index options, built from scratch in Python.

The project processes large minute-level NIFTY options datasets, converts raw CSV market data into expiry-partitioned Parquet files, reconstructs intraday option-chain snapshots, executes configurable strategies, models realistic trading costs and stop-losses, supports sequential and multiprocessing execution, and exposes the backtester through an asynchronous FastAPI REST API.

No Backtrader. No Zipline. The backtesting engine, portfolio logic, data pipeline, strategy framework, API, and multiprocessing layer are implemented directly in Python.

---

## Real Dataset

The engine has been tested against a large historical NIFTY options dataset:

| Metric | Value |
|---|---:|
| Raw CSV rows | 35,991,045 |
| Expiry-day rows retained | 4,864,052 |
| Weekly expiries | 78 |
| Data range | 03-Oct-2024 → 24-Mar-2026 |
| Raw format | CSV |
| Processed format | Parquet |
| Market frequency | 1 minute |
| Snapshots per expiry | ~375 |
| Instrument | NIFTY Options |

Instead of loading the full CSV every time a backtest runs, the dataset is preprocessed once into one Parquet partition per expiry date.

Example:

```text
data/processed/nifty_options_expiry/
├── 2024-10-03.parquet
├── 2024-10-10.parquet
├── 2024-10-17.parquet
├── 2024-10-24.parquet
├── 2024-10-31.parquet
├── ...
├── 2026-03-24.parquet
└── manifest.json
```

This dramatically reduces both memory consumption and backtest startup time.

> Raw CSV and generated Parquet files are intentionally excluded from Git because they are large/generated datasets. The preprocessing and loading code is included in the repository.

---

## Real Backtest Results

Full historical ATM Straddle backtest:

```text
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
  Total Costs    : ₹    12,605.60
  Net P&L        : ₹    86,357.83
--------------------------------------------------------------
  Max Drawdown   : ₹    15,414.70
  Avg P&L/Trade  : ₹     1,107.15
  Best Trade     : ₹    19,260.77
  Worst Trade    : ₹    -6,802.25
==============================================================
```

Configuration:

```text
Strategy       : ATM Straddle
Instrument     : NIFTY
Lot size       : 50
Stop-loss      : 50% of premium received
Slippage       : 0.5%
Brokerage      : ₹20 per order
Execution data : Real minute-level option premiums
```

These numbers are historical backtest results and should not be interpreted as expected future returns.

---

# What the Project Does

The engine currently supports:

- Real minute-level NIFTY options data
- 35M+ row raw CSV preprocessing
- Expiry-day filtering
- Parquet partition generation
- Lazy loading of individual expiry partitions
- Minute-by-minute option-chain reconstruction
- ATM strike selection
- CE + PE strategy execution
- Combined-premium stop-loss
- Slippage modeling
- Brokerage and transaction-cost modeling
- Portfolio P&L tracking
- Maximum drawdown calculation
- Sequential backtesting
- Multiprocessing backtesting
- Asynchronous REST API jobs
- Job status polling
- Paginated trade results
- Swagger/OpenAPI documentation
- Optional PostgreSQL integration
- Optional MongoDB integration
- Automated tests

---

# Architecture

```text
                         ┌───────────────────────┐
                         │       Client          │
                         │ Swagger / curl / App  │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │      FastAPI API      │
                         │      api/main.py      │
                         └───────────┬───────────┘
                                     │
                           Background Job
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
        ┌─────────────────────┐           ┌─────────────────────┐
        │ Sequential Engine   │           │  Parallel Engine    │
        │ backtester/engine   │           │ multiprocessing     │
        └──────────┬──────────┘           └──────────┬──────────┘
                   │                                 │
                   └────────────────┬────────────────┘
                                    │
                                    ▼
                         ┌───────────────────────┐
                         │      DataLoader       │
                         │    data/loader.py     │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │ Expiry Parquet Files  │
                         │ one file per expiry   │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │   MarketSnapshot      │
                         │ spot + option chain   │
                         └───────────┬───────────┘
                                     │
                     ┌───────────────┴───────────────┐
                     │                               │
                     ▼                               ▼
            ┌─────────────────┐             ┌─────────────────┐
            │    Strategy     │             │    Portfolio    │
            │ ATM Straddle    │             │ P&L / SL / Cost │
            └────────┬────────┘             └────────┬────────┘
                     │                               │
                     └───────────────┬───────────────┘
                                     │
                                     ▼
                            ┌─────────────────┐
                            │ Trade Results   │
                            └─────────────────┘
```

---

# Data Pipeline

One of the main engineering problems in this project was handling the raw dataset efficiently.

The source CSV contains:

```text
35,991,045 rows
```

Loading this CSV independently inside multiple multiprocessing workers caused memory exhaustion.

Typical failures included:

```text
numpy._core._exceptions._ArrayMemoryError
```

and:

```text
pandas.errors.ParserError:
Error tokenizing data. C error: out of memory
```

The solution was to move expensive CSV parsing out of the runtime backtesting path.

## Stage 1 — Raw CSV

```text
data/raw/nifty_options.csv
```

The raw dataset contains fields such as:

```text
strike_price
option_type
expiry
timestamp
ltp
volume
oi
underlying_spot_price
strike_spot_diff
time_to_expiry
tte_years
IAO
pcr
flag
iv
delta
gamma
theta
vega
rho
```

---

## Stage 2 — Preprocessing

Run:

```bash
python -m data.prepare_data
```

The preprocessing pipeline:

1. Reads the raw CSV in chunks.
2. Parses timestamps and expiry dates.
3. Keeps only expiry-day records.
4. Normalizes option types.
5. Uses memory-efficient data types.
6. Groups rows by expiry.
7. Writes one Parquet file per expiry.
8. Generates a manifest describing available expiries.

Actual preprocessing result:

```text
Source rows:      35,991,045
Expiry-day rows:   4,864,052
Expiry dates:             78
Range:             2024-10-03 -> 2026-03-24
```

---

## Stage 3 — Partitioned Storage

Instead of repeatedly scanning:

```text
6 GB CSV
```

the runtime loader can directly access:

```text
data/processed/nifty_options_expiry/2024-10-03.parquet
```

for a specific expiry.

Therefore a one-month backtest requiring five expiries loads only five relevant partitions.

---

## Stage 4 — Market Snapshot Reconstruction

Each expiry partition is converted into approximately:

```text
375 minute snapshots
```

A `MarketSnapshot` contains:

```text
timestamp
expiry
underlying_price
contracts[]
```

Each option contract contains information such as:

```text
strike
option_type
ltp
iv
oi
volume
underlying_price
```

The strategy therefore sees the option chain as it existed at each minute of the trading session.

---

# Parallel Backtesting

Expiry dates are independent backtesting units.

For example:

```text
03-Oct
10-Oct
17-Oct
24-Oct
31-Oct
```

can be processed independently.

The parallel engine distributes them across worker processes:

```text
                 Main Process
                      │
       ┌──────────────┼──────────────┐
       │              │              │
       ▼              ▼              ▼
   Worker 1       Worker 2       Worker 3       Worker 4
   Oct-03         Oct-10         Oct-17         Oct-24
                                                   │
                                                   ▼
                                                Oct-31
```

Configuration:

```python
PARALLEL_WORKERS = 4
```

The API can select the execution mode using:

```json
"parallel": true
```

or:

```json
"parallel": false
```

---

# Performance Improvement

Before Parquet preprocessing, multiprocessing workers could independently scan the huge CSV and eventually exhaust system memory.

After expiry-partitioned Parquet preprocessing, the same October 2024 test completed successfully in both execution modes.

### Sequential

```text
Starting backtest: 13:22:19
Completed:         13:22:30

Approx runtime: ~12 seconds
Trades: 5
Net P&L: ₹4,106.54
```

### Parallel — 4 Workers

```text
Starting backtest: 13:23:53
Completed:         13:24:04

Approx runtime: ~11 seconds
Trades: 5
Net P&L: ₹4,106.54
```

For only five expiries, multiprocessing overhead means the difference is small.

The important result is that both execution paths produce the same financial result:

```text
Sequential : ₹4,106.54
Parallel   : ₹4,106.54
```

while the parallel implementation no longer requires every worker to repeatedly parse the entire raw CSV.

Parallel execution becomes more useful as the number of expiry dates increases.

---

# Project Structure

```text
nifty-options-backtesting-engine/
│
├── api/
│   ├── __init__.py
│   ├── main.py
│   ├── schemas.py
│   └── jobs.py
│
├── backtester/
│   ├── engine.py
│   ├── parallel_engine.py
│   ├── portfolio.py
│   └── models.py
│
├── strategies/
│   ├── __init__.py
│   ├── base.py
│   └── atm_straddle.py
│
├── data/
│   ├── raw/
│   │   └── nifty_options.csv          # ignored by Git
│   │
│   ├── processed/
│   │   └── nifty_options_expiry/
│   │       ├── *.parquet              # generated / ignored
│   │       └── manifest.json
│   │
│   ├── loader.py
│   ├── prepare_data.py
│   └── preprocess.py
│
├── storage/
│   └── ...
│
├── utils/
│   ├── options_math.py
│   ├── nse_utils.py
│   └── db.py
│
├── scripts/
│   ├── run_backtest.py
│   └── download_data.py
│
├── tests/
│   ├── test_engine.py
│   └── test_api.py
│
├── results/                           # generated / ignored
│
├── config.py
├── requirements.txt
├── .gitignore
└── README.md
```

---

# Installation

## 1. Clone the repository

```bash
git clone https://github.com/rajappa-adabala/nifty-options-backtesting-engine.git
cd nifty-options-backtesting-engine
```

## 2. Create virtual environment

Windows:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
python -m venv venv
source venv/bin/activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

# Preparing Real Market Data

Place the source dataset at:

```text
data/raw/nifty_options.csv
```

Then run:

```bash
python -m data.prepare_data
```

Expected output resembles:

```text
INFO Raw CSV: data\raw\nifty_options.csv
INFO Processed output: data\processed\nifty_options_expiry
INFO Starting CSV -> Parquet preprocessing...

INFO Read 2,000,000 rows...
INFO Read 10,000,000 rows...
INFO Read 20,000,000 rows...
INFO Read 30,000,000 rows...

INFO Preprocessing complete.
INFO Source rows: 35,991,045
INFO Expiry-day rows: 4,864,052
INFO Expiry dates: 78
INFO Range: 2024-10-03 -> 2026-03-24
```

This preprocessing step only needs to be repeated when the source market dataset changes.

---

# Running the REST API

Start the server from the project root:

```bash
uvicorn api.main:app --reload --port 8000
```

Expected startup:

```text
INFO: Uvicorn running on http://127.0.0.1:8000
```

Open Swagger UI:

```text
http://127.0.0.1:8000/docs
```

Swagger provides an interactive interface for submitting and inspecting backtests.

---

# API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | API health check |
| `POST` | `/backtest` | Submit asynchronous backtest |
| `GET` | `/backtest/{job_id}` | Poll status and retrieve summary |
| `GET` | `/backtest/{job_id}/trades` | Paginated trade results |
| `DELETE` | `/backtest/{job_id}` | Delete job from job store |

---

# Health Check

Request:

```http
GET /health
```

Response:

```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

---

# Submit a Backtest

Request:

```http
POST /backtest
```

Example body:

```json
{
  "strategy": "atm_straddle",
  "symbol": "NIFTY",
  "from_date": "2024-10-01",
  "to_date": "2024-10-31",
  "stoploss_pct": 50,
  "slippage_pct": 0.5,
  "lot_size": 50,
  "parallel": true
}
```

Response:

```json
{
  "job_id": "36fd5e7d-40ee-44a6-8003-4d22ae137874",
  "status": "pending",
  "message": "Backtest submitted. Poll GET /backtest/{job_id} for status."
}
```

HTTP status:

```text
202 Accepted
```

The backtest runs asynchronously.

---

# Poll Backtest Status

Use the returned job ID:

```http
GET /backtest/36fd5e7d-40ee-44a6-8003-4d22ae137874
```

While running:

```json
{
  "job_id": "36fd5e7d-40ee-44a6-8003-4d22ae137874",
  "status": "running",
  "summary": null,
  "error": null
}
```

When completed:

```json
{
  "job_id": "36fd5e7d-40ee-44a6-8003-4d22ae137874",
  "status": "completed",
  "config": {
    "strategy": "atm_straddle",
    "symbol": "NIFTY",
    "from_date": "2024-10-01",
    "to_date": "2024-10-31",
    "stoploss_pct": 50,
    "slippage_pct": 0.5,
    "lot_size": 50,
    "parallel": true
  },
  "summary": {
    "total_trades": 5,
    "wins": 3,
    "losses": 2,
    "win_rate_pct": 60,
    "gross_pnl": 4857.21,
    "total_costs": 750.67,
    "net_pnl": 4106.54,
    "avg_pnl_per_trade": 821.31,
    "best_trade": 5158.04,
    "worst_trade": -5029.24,
    "max_drawdown": 5029.24
  },
  "error": null
}
```

---

# Trade-Level Results

Request:

```http
GET /backtest/{job_id}/trades?page=1&page_size=20
```

The endpoint provides paginated individual trade records including:

```text
trade_id
expiry
entry_time
exit_time
status
premium_received
premium_paid_back
gross_pnl
total_costs
net_pnl
```

This makes the API usable by a frontend without requiring the complete trade history to be returned in the summary endpoint.

---

# Async Job Architecture

Backtests can take seconds or minutes depending on the requested range.

The API therefore does not hold the POST connection open until the calculation finishes.

Instead:

```text
POST /backtest
      │
      ▼
 Create Job
      │
      ├──────────────► return HTTP 202 + job_id
      │
      ▼
Background Backtest
      │
      ▼
 pending
      │
      ▼
 running
      │
      ├──────── failure ──────► failed
      │
      ▼
 completed
```

The client polls:

```text
GET /backtest/{job_id}
```

until:

```json
"status": "completed"
```

---

# Strategy — ATM Straddle

The currently implemented strategy is an expiry-day ATM short straddle.

At entry:

```text
Find ATM strike closest to NIFTY spot
             │
             ├── SELL ATM CE
             │
             └── SELL ATM PE
```

Example from real data:

```text
Expiry: 03-Oct-2024
Spot: 25545.35

SELL 25550 CE @ ₹92.20
SELL 25550 PE @ ₹86.55
```

Combined premium:

```text
₹92.20 + ₹86.55 = ₹178.75
```

For a lot size of 50:

```text
₹178.75 × 50 = ₹8,937.50
```

The strategy then monitors the combined option premium minute by minute.

---

# Stop-Loss Logic

Current configuration:

```python
STOPLOSS_PCT = 50.0
```

For premium received `P`:

```text
SL threshold = P × 1.50
```

Example:

```text
Premium received = ₹8,938
50% SL threshold = ₹13,406
```

If the current combined premium reaches or exceeds that threshold:

```text
CLOSED_STOPLOSS
```

Otherwise the position is closed at the end of the trading session:

```text
CLOSED_EOD
```

---

# Trading Cost Model

The engine includes trading costs rather than reporting theoretical gross P&L only.

Current configuration:

```python
SLIPPAGE_PCT = 0.5
BROKERAGE_PER_ORDER = 20
STT_SELL_PCT = 0.0625
SEBI_CHARGES = 0.0001
```

The portfolio tracks:

```text
Gross P&L
    -
Slippage
    -
Brokerage
    -
Applicable charges
    =
Net P&L
```

This is why the API reports both:

```json
"gross_pnl": 4857.21,
"total_costs": 750.67,
"net_pnl": 4106.54
```

---

# October 2024 Validation Run

The processed-data pipeline was validated against five real expiry dates:

```text
03-Oct-2024
10-Oct-2024
17-Oct-2024
24-Oct-2024
31-Oct-2024
```

Results:

| Metric | Result |
|---|---:|
| Trades | 5 |
| Wins | 3 |
| Losses | 2 |
| Win rate | 60% |
| Gross P&L | ₹4,857.21 |
| Costs | ₹750.67 |
| Net P&L | ₹4,106.54 |
| Average/trade | ₹821.31 |
| Best trade | ₹5,158.04 |
| Worst trade | -₹5,029.24 |
| Max drawdown | ₹5,029.24 |

Individual outcomes observed during the run included:

```text
03-Oct-2024  STOP LOSS    -₹5,029
10-Oct-2024  EOD           +₹5,093
17-Oct-2024  STOP LOSS    -₹3,385
24-Oct-2024  EOD           +₹5,158
31-Oct-2024  EOD           +₹2,270
```

---

# Sequential vs Parallel Validation

Both engines were tested on the same five-expiry period.

### Sequential

```json
{
  "parallel": false,
  "net_pnl": 4106.54
}
```

### Multiprocessing

```json
{
  "parallel": true,
  "net_pnl": 4106.54
}
```

Result:

```text
Sequential P&L == Parallel P&L
```

This is an important correctness check: optimization changed execution architecture without changing strategy results.

---

# Configuration

Central configuration lives in:

```text
config.py
```

Important options:

```python
SYMBOL = "NIFTY"
LOT_SIZE = 50

SLIPPAGE_PCT = 0.5
BROKERAGE_PER_ORDER = 20
STT_SELL_PCT = 0.0625
SEBI_CHARGES = 0.0001

STOPLOSS_PCT = 50.0

DATA_RAW_DIR = "data/raw"
DATA_PROCESSED_DIR = "data/processed"
RESULTS_DIR = "results"

PARALLEL_WORKERS = 4
```

Set:

```python
PARALLEL_WORKERS = 1
```

to effectively disable multiprocessing.

---

# Optional Databases

The project includes configuration for two different persistence workloads.

## PostgreSQL

Designed for structured relational data such as:

```text
backtests
trades
trade legs
P&L
strategy metadata
```

Configuration:

```python
USE_DB = False
DB_URL = "postgresql://user:password@localhost:5432/options_bt"
```

## MongoDB

Designed for high-volume/semi-structured market snapshot storage.

Configuration:

```python
USE_MONGO = False
MONGO_URL = "mongodb://localhost:27017"
MONGO_DB_NAME = "options_bt"
```

Both are optional for local backtesting.

---

# Adding a New Strategy

Strategies implement the common `BaseStrategy` interface.

Example:

```python
from strategies.base import BaseStrategy


class MyStrategy(BaseStrategy):

    def should_enter(self, snapshot) -> bool:
        ...

    def get_legs(self, snapshot, lot_size):
        ...

    def should_exit(self, trade, snapshot) -> bool:
        ...
```

Register the strategy in:

```text
strategies/__init__.py
```

and expose the strategy name through the API schema.

This keeps the execution engine independent from individual strategy logic.

---

# Testing

Run all tests:

```bash
pytest tests/ -v
```

API tests:

```bash
pytest tests/test_api.py -v
```

Engine tests:

```bash
pytest tests/test_engine.py -v
```

The API test suite should validate workflows such as:

```text
POST /backtest
      ↓
202 Accepted
      ↓
job_id
      ↓
GET /backtest/{job_id}
      ↓
completed
      ↓
GET /backtest/{job_id}/trades
```

---

# Git and Large Dataset Handling

Large market datasets are deliberately not committed.

Recommended `.gitignore` rules:

```gitignore
# Raw market data
nifty_options.csv
data/raw/*.csv
data/raw/*.zip

# Generated market data
*.parquet
data/processed/

# Runtime output
storage/
results/
*.log

# Python
__pycache__/
*.py[cod]

# Virtual environments
venv/
.venv/

# Secrets
.env
.env.*

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

The repository contains:

```text
CSV → Parquet preprocessing code     ✓
Parquet loading code                 ✓
Backtesting engine                   ✓
Parallel execution code              ✓
FastAPI                              ✓
Strategies                           ✓
Tests                                ✓
Configuration                        ✓
```

but not:

```text
35M-row source CSV                    ✗
Generated Parquet partitions         ✗
Virtual environment                  ✗
Runtime results                      ✗
Secrets                              ✗
```

A developer cloning the repository can provide their own compatible market dataset and generate the Parquet partitions locally.

---

# Why Parquet?

CSV is useful as an exchange/import format but inefficient for repeatedly querying a large historical options dataset.

The initial implementation repeatedly scanned the huge CSV.

That worked sequentially but became problematic when multiple worker processes loaded the dataset simultaneously.

The current pipeline uses:

```text
35,991,045-row CSV
        │
        │ one-time preprocessing
        ▼
4,864,052 expiry-day rows
        │
        │ partition by expiry
        ▼
78 Parquet files
        │
        │ lazy load
        ▼
requested expiries only
```

Benefits:

- Lower runtime memory requirements
- No repeated full-CSV parsing
- Faster startup
- Efficient expiry lookup
- Better multiprocessing behavior
- Smaller I/O workload per worker
- Easier scaling to larger datasets

---

# Engineering Problems Solved

This project is not only a strategy script. It addresses several backend/data-engineering problems.

### Large dataset processing

Chunked processing allows a ~36-million-row source file to be transformed without requiring the entire source dataset in memory at once.

### Memory-safe multiprocessing

The original parallel implementation could exhaust RAM because multiple processes parsed the huge CSV independently.

Expiry-partitioned Parquet files allow workers to read only the data they actually need.

### Deterministic parallel execution

Sequential and parallel engines were validated to produce the same P&L for identical strategy configurations.

### Long-running API workloads

Backtests are submitted asynchronously instead of keeping HTTP requests blocked until execution finishes.

### API result pagination

Individual trade records are exposed separately through a paginated endpoint.

### Strategy abstraction

Trading rules are separated from the engine, allowing new strategies to reuse the same execution, portfolio, data, and API infrastructure.

---

# Known Limitations

The current implementation still has areas that would need improvement before production trading use.

### In-memory API job store

API jobs are currently stored in application memory.

Restarting the server removes previous job state.

A production deployment could use:

```text
Redis
PostgreSQL
```

for persistent/distributed job state.

### FastAPI BackgroundTasks

The current API uses FastAPI background execution.

For large-scale deployments, a dedicated task queue would be more appropriate, for example:

```text
Celery + Redis
RQ
Dramatiq
```

### Process-global configuration

Some request-level settings are currently applied through the shared configuration module.

Concurrent jobs with different configurations should eventually pass these settings explicitly into the engine/portfolio instead.

### Historical backtest ≠ live execution

The engine works against historical minute data and does not currently model every live-market execution effect, such as:

```text
bid/ask spread
partial fills
market impact
queue position
liquidity constraints
latency
```

### Single primary implemented strategy

The architecture is pluggable, but ATM Straddle is currently the main implemented and validated strategy.

---

# Future Roadmap

Planned extensions include:

- Redis-backed persistent job queue
- Celery/RQ workers
- Docker deployment
- PostgreSQL-backed backtest history
- Strategy parameter optimization
- Equity curve endpoint
- Drawdown series
- Daily/monthly P&L analytics
- Sharpe ratio
- Sortino ratio
- Profit factor
- CAGR / return-on-capital analysis
- Multiple strategies
- Iron Condor
- Short Strangle
- Calendar spreads
- Dynamic stop-loss
- Trailing stop-loss
- BANKNIFTY support
- Web dashboard
- Interactive equity curve
- Trade visualization
- Cloud deployment
- Benchmark suite

---

# Tech Stack

### Backend

```text
Python
FastAPI
Uvicorn
Pydantic
```

### Data Engineering

```text
pandas
NumPy
Parquet
PyArrow
```

### Quant / Options

```text
Black-Scholes
Greeks
Implied Volatility
ATM strike selection
Options P&L modeling
```

### Concurrency

```text
Python multiprocessing
FastAPI BackgroundTasks
```

### Persistence

```text
PostgreSQL
MongoDB
```

### Testing

```text
pytest
FastAPI TestClient
```

### Development

```text
Git
GitHub
GitHub Actions
```

---

# Example End-to-End Workflow

```text
Clone repository
      │
      ▼
Install requirements
      │
      ▼
Place nifty_options.csv
      │
      ▼
python -m data.prepare_data
      │
      ▼
78 expiry Parquet partitions
      │
      ▼
uvicorn api.main:app --reload --port 8000
      │
      ▼
Swagger UI
      │
      ▼
POST /backtest
      │
      ▼
job_id
      │
      ▼
Parallel / Sequential Engine
      │
      ▼
Parquet expiry partitions
      │
      ▼
Minute MarketSnapshots
      │
      ▼
ATM Straddle Strategy
      │
      ▼
Portfolio + Stop Loss + Costs
      │
      ▼
Backtest Summary
      │
      ▼
GET /backtest/{job_id}
      │
      ▼
GET /backtest/{job_id}/trades
```

---

# Disclaimer

This project is intended for educational, research, software-engineering, and quantitative-analysis purposes.

Historical backtest performance does not guarantee future performance. Transaction costs, liquidity, bid/ask spreads, execution latency, market impact, taxes, regulatory changes, and other real-world factors can materially affect live trading results.

This repository does not provide investment advice.

---

# License

MIT License

---

# Author

**Rajappa Adabala**

GitHub: `rajappa-adabala`

Project:

`nifty-options-backtesting-engine`

Built as an end-to-end quantitative engineering project covering large-scale market-data preprocessing, options strategy simulation, multiprocessing, REST API design, and backend architecture.
