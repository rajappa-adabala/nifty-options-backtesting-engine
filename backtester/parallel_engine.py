"""
backtester/parallel_engine.py

Memory-safe parallel backtesting across expiry dates.

Architecture:

    manifest.json
        ↓
    list required expiries
        ↓
    multiprocessing.Pool
        ↓
    Worker 1 -> 2024-10-03.parquet
    Worker 2 -> 2024-10-10.parquet
    Worker 3 -> 2024-10-17.parquet
    Worker 4 -> 2024-10-24.parquet
        ↓
    merge completed trades
        ↓
    Portfolio

Unlike the old implementation, workers do NOT scan or load the
35.9M-row raw CSV. DataLoader loads only the Parquet partition
for the expiry assigned to that worker.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import uuid

from datetime import datetime
from typing import List, Tuple, Type

from backtester.models import Trade, TradeStatus
from backtester.portfolio import Portfolio
from strategies.base import BaseStrategy
from data.loader import DataLoader

import config


logger = logging.getLogger(__name__)


# =============================================================================
# Worker
# =============================================================================

def _process_single_expiry(
    args: Tuple,
) -> List[Trade]:
    """
    Process one expiry in a worker process.

    This function must remain at module level because Windows
    multiprocessing needs to pickle the worker function.

    Each worker creates:
        - its own strategy
        - its own DataLoader
        - its own Portfolio

    DataLoader then loads only:

        data/processed/nifty_options_expiry/YYYY-MM-DD.parquet

    instead of loading the entire raw CSV.
    """

    (
        strategy_cls,
        symbol,
        expiry,
        lot_size,
    ) = args

    # Fresh objects inside each process.
    strategy = strategy_cls()

    loader = DataLoader(
        symbol
    )

    portfolio = Portfolio()

    logger.info(
        "Worker processing expiry %s",
        expiry.date(),
    )

    # This now loads ONE Parquet partition only.
    snapshots = (
        loader.get_snapshots_for_expiry(
            expiry
        )
    )

    if not snapshots:

        logger.warning(
            "Worker found no snapshots for expiry %s",
            expiry.date(),
        )

        return []

    active_trade: Trade | None = None

    for index, snapshot in enumerate(
        snapshots
    ):

        is_last = (
            index
            == len(snapshots) - 1
        )

        # ── Entry ─────────────────────────────────────────────────────────

        if (
            active_trade is None
            and strategy.should_enter(
                snapshot
            )
        ):

            legs = strategy.get_legs(
                snapshot,
                lot_size,
            )

            if legs:

                active_trade = Trade(
                    trade_id=str(
                        uuid.uuid4()
                    )[:8].upper(),

                    strategy_name=(
                        strategy.name
                    ),

                    symbol=symbol,

                    expiry=expiry,

                    entry_time=(
                        snapshot.timestamp
                    ),

                    legs=legs,
                )

                portfolio.add_trade(
                    active_trade
                )

        # ── Stop-loss ─────────────────────────────────────────────────────

        if active_trade is not None:

            sl_trades = (
                portfolio.check_stoploss(
                    snapshot
                )
            )

            if active_trade in sl_trades:

                portfolio.close_trade(
                    active_trade,
                    snapshot,
                    TradeStatus.CLOSED_STOPLOSS,
                )

                active_trade = None

                # No additional processing for this snapshot.
                continue

        # ── Strategy exit ─────────────────────────────────────────────────

        if (
            active_trade is not None
            and strategy.should_exit(
                active_trade,
                snapshot,
            )
        ):

            portfolio.close_trade(
                active_trade,
                snapshot,
                TradeStatus.CLOSED_EOD,
            )

            active_trade = None

        # ── End-of-day force close ────────────────────────────────────────

        if (
            active_trade is not None
            and is_last
        ):

            portfolio.close_trade(
                active_trade,
                snapshot,
                TradeStatus.CLOSED_EOD,
            )

            active_trade = None

    # Safety net.
    if active_trade is not None:

        logger.warning(
            "Trade %s still open after expiry %s",
            active_trade.trade_id,
            expiry.date(),
        )

        # We have snapshots, so close using final snapshot.
        portfolio.close_trade(
            active_trade,
            snapshots[-1],
            TradeStatus.CLOSED_EOD,
        )

    logger.info(
        "Worker completed expiry %s | "
        "Trades: %d | Net P&L: ₹%.0f",
        expiry.date(),
        len(
            portfolio.closed_trades
        ),
        portfolio.total_net_pnl,
    )

    return portfolio.closed_trades


# =============================================================================
# Parallel Engine
# =============================================================================

class ParallelBacktestEngine:
    """
    Parallel replacement for BacktestEngine.

    Expiry dates are independent, making them suitable for
    multiprocessing.

    The public API remains:

        engine.run() -> Portfolio
    """

    def __init__(
        self,
        strategy_cls: Type[BaseStrategy],
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        num_workers: int | None = None,
        lot_size: int | None = None,
    ):
        self.strategy_cls = strategy_cls

        self.symbol = symbol

        self.start_date = start_date

        self.end_date = end_date

        self.num_workers = (
            num_workers
            if num_workers is not None
            else config.PARALLEL_WORKERS
        )

        self.lot_size = (
            lot_size
            if lot_size is not None
            else config.LOT_SIZE
        )

    def run(
        self,
    ) -> Portfolio:
        """
        Run the backtest across multiple worker processes.
        """

        # Main process only reads manifest to discover expiries.
        loader = DataLoader(
            self.symbol
        )

        expiry_dates = (
            loader.get_expiry_dates(
                self.start_date,
                self.end_date,
            )
        )

        logger.info(
            "Parallel backtest: %d expiries across %d workers",
            len(expiry_dates),
            self.num_workers,
        )

        # ── No data ───────────────────────────────────────────────────────

        if not expiry_dates:

            logger.warning(
                "No expiry dates found between %s and %s",
                self.start_date.date(),
                self.end_date.date(),
            )

            return Portfolio()

        # ── Sequential fallback ───────────────────────────────────────────

        if (
            self.num_workers <= 1
            or len(expiry_dates) <= 1
        ):

            logger.info(
                "Using sequential BacktestEngine"
            )

            from backtester.engine import (
                BacktestEngine
            )

            engine = BacktestEngine(
                strategy_cls=self.strategy_cls,
                symbol=self.symbol,
                start_date=self.start_date,
                end_date=self.end_date,
                lot_size=self.lot_size,
            )

            return engine.run()

        # Never start more workers than expiries.
        worker_count = min(
            self.num_workers,
            len(expiry_dates),
        )

        logger.info(
            "Starting multiprocessing pool with %d workers",
            worker_count,
        )

        # One task per expiry.
        tasks = [
            (
                self.strategy_cls,
                self.symbol,
                expiry,
                self.lot_size,
            )
            for expiry in expiry_dates
        ]

        # ── Windows multiprocessing ───────────────────────────────────────

        # "spawn" is explicit because this project runs on Windows.
        # It gives every worker a clean Python process.
        ctx = mp.get_context(
            "spawn"
        )

        with ctx.Pool(
            processes=worker_count
        ) as pool:

            results = pool.map(
                _process_single_expiry,
                tasks,
                chunksize=1,
            )

        # ── Merge results ─────────────────────────────────────────────────

        merged_portfolio = Portfolio()

        for trades in results:

            for trade in trades:

                merged_portfolio.closed_trades.append(
                    trade
                )

        # Keep trades chronologically ordered.
        merged_portfolio.closed_trades.sort(
            key=lambda trade: (
                trade.expiry,
                trade.entry_time,
            )
        )

        logger.info(
            "Parallel backtest complete. "
            "Trades: %d | "
            "Net P&L: ₹%.0f",
            len(
                merged_portfolio.closed_trades
            ),
            merged_portfolio.total_net_pnl,
        )

        return merged_portfolio