"""
scripts/run_backtest.py
-----------------------
CLI entrypoint for running backtests.

Usage:
    python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY \
        --from 2023-01-01 --to 2023-12-31

    python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY \
        --from 2023-01-01 --to 2023-12-31 --stoploss 50 --slippage 1.0

    python scripts/run_backtest.py --strategy atm_straddle --symbol NIFTY \
        --from 2023-01-01 --to 2023-12-31 --save-db
"""

import argparse
import csv
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from backtester.engine import BacktestEngine
from strategies import STRATEGY_REGISTRY
from utils.db import init_db, save_trade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Options Strategy Backtester — NSE Derivatives",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--strategy", required=True,
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategy to backtest",
    )
    parser.add_argument("--symbol", default="NIFTY", help="Underlying: NIFTY | BANKNIFTY")
    parser.add_argument("--from", dest="from_date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--stoploss", type=float, default=None,
        help="Stop-loss %% on premium received (e.g. 50 = exit if premium doubles)"
    )
    parser.add_argument(
        "--slippage", type=float, default=config.SLIPPAGE_PCT,
        help="Slippage %% per leg"
    )
    parser.add_argument(
        "--lot-size", type=int, default=config.LOT_SIZE,
        help="Lot size (NIFTY=50, BANKNIFTY=15)"
    )
    parser.add_argument(
        "--save-db", action="store_true",
        help="Save results to PostgreSQL (requires config.DB_URL)"
    )
    parser.add_argument(
        "--output-dir", default=config.RESULTS_DIR,
        help="Directory to save CSV trade log"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show DEBUG-level logs"
    )
    return parser.parse_args()


def apply_cli_overrides(args):
    """Override config values from CLI args."""
    if args.stoploss is not None:
        config.STOPLOSS_PCT = args.stoploss
    config.SLIPPAGE_PCT = args.slippage
    config.LOT_SIZE = args.lot_size
    if args.save_db:
        config.USE_DB = True
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)


def save_results_csv(portfolio, output_dir: str, strategy_name: str) -> str:
    """Save all closed trades to a CSV file. Returns path."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"trade_log_{strategy_name}_{ts}.csv"
    path = os.path.join(output_dir, filename)

    trades = portfolio.closed_trades
    if not trades:
        logger.warning("No trades to save.")
        return ""

    rows = [t.to_dict() for t in trades]
    fieldnames = list(rows[0].keys())

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path


def print_summary(portfolio, strategy_name: str, symbol: str, start: datetime, end: datetime):
    """Print a formatted P&L summary to stdout."""
    s = portfolio.summary()
    if not s:
        print("\nNo trades executed in the given date range.")
        return

    wins = s["wins"]
    losses = s["losses"]
    total = s["total_trades"]
    win_rate = s["win_rate_pct"]

    sep = "=" * 62
    thin = "-" * 62

    print(f"\n{sep}")
    print(f"   BACKTEST SUMMARY — {strategy_name.replace('_', ' ').upper()} ({symbol})")
    print(sep)
    print(f"  Period         : {start.date()} → {end.date()}")
    print(f"  Total Expiries : {total}")
    print(f"  Trades Taken   : {total}")
    print(f"  Wins           : {wins:<5}  ({win_rate:.1f}%)")
    print(f"  Losses         : {losses:<5}  ({100 - win_rate:.1f}%)")
    print(thin)
    print(f"  Gross P&L      : ₹ {s['gross_pnl']:>12,.2f}")
    print(f"  Total Costs    : ₹ {s['total_costs']:>12,.2f}  (brokerage + slippage)")
    print(f"  Net P&L        : ₹ {s['net_pnl']:>12,.2f}")
    print(thin)
    print(f"  Max Drawdown   : ₹ {s['max_drawdown']:>12,.2f}")
    print(f"  Avg P&L/Trade  : ₹ {s['avg_pnl_per_trade']:>12,.2f}")
    print(f"  Best Trade     : ₹ {s['best_trade']:>12,.2f}")
    print(f"  Worst Trade    : ₹ {s['worst_trade']:>12,.2f}")
    print(sep)

    # Config used
    print(f"\n  Config used:")
    print(f"    Stop-loss    : {config.STOPLOSS_PCT}% of premium received" if config.STOPLOSS_PCT else "    Stop-loss    : Disabled")
    print(f"    Slippage     : {config.SLIPPAGE_PCT}% per leg")
    print(f"    Lot size     : {config.LOT_SIZE}")
    print(f"    Brokerage    : ₹{config.BROKERAGE_PER_ORDER} flat per order")
    print()


def print_trade_table(portfolio):
    """Print per-trade breakdown."""
    trades = portfolio.closed_trades
    if not trades:
        return

    print("\n  TRADE-LEVEL BREAKDOWN:")
    print(f"  {'#':<4} {'Expiry':<12} {'Status':<18} {'Recv ₹':<10} {'Paid ₹':<10} {'Net ₹':<10}")
    print("  " + "-" * 64)

    running_pnl = 0.0
    for i, t in enumerate(trades, 1):
        running_pnl += t.net_pnl
        status = t.status.value.replace("CLOSED_", "")
        sign = "+" if t.net_pnl >= 0 else ""
        print(
            f"  {i:<4} {t.expiry.strftime('%d-%b-%Y'):<12} {status:<18} "
            f"{t.total_premium_received:<10.0f} {t.total_premium_paid_back:<10.0f} "
            f"{sign}{t.net_pnl:<10.0f}"
        )
    print("  " + "-" * 64)
    sign = "+" if running_pnl >= 0 else ""
    print(f"  {'TOTAL':>38}  {sign}{running_pnl:,.0f}")
    print()


def main():
    args = parse_args()
    apply_cli_overrides(args)

    start = datetime.strptime(args.from_date, "%Y-%m-%d")
    end = datetime.strptime(args.to_date, "%Y-%m-%d")

    strategy_cls = STRATEGY_REGISTRY[args.strategy]

    # ── DB setup ──────────────────────────────────────────────────────────────
    if config.USE_DB:
        try:
            init_db()
        except Exception as e:
            logger.error(f"DB init failed: {e}. Continuing without DB.")
            config.USE_DB = False

    # ── Run ───────────────────────────────────────────────────────────────────
    engine = BacktestEngine(
        strategy_cls=strategy_cls,
        symbol=args.symbol,
        start_date=start,
        end_date=end,
    )

    portfolio = engine.run()

    # ── Persist to DB ─────────────────────────────────────────────────────────
    if config.USE_DB:
        for trade in portfolio.closed_trades:
            try:
                save_trade(trade)
            except Exception as e:
                logger.warning(f"Could not save trade {trade.trade_id} to DB: {e}")

    # ── Save CSV ──────────────────────────────────────────────────────────────
    csv_path = save_results_csv(portfolio, args.output_dir, args.strategy)
    if csv_path:
        logger.info(f"Trade log saved → {csv_path}")

    # ── Print results ─────────────────────────────────────────────────────────
    print_summary(portfolio, args.strategy, args.symbol, start, end)
    print_trade_table(portfolio)

    if csv_path:
        print(f"  CSV saved to: {csv_path}\n")


if __name__ == "__main__":
    main()