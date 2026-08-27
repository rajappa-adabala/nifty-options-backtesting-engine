"""
backtester/engine.py

Sequential backtesting engine.

The trading logic is unchanged.
The main improvement is support for request-level lot_size so
sequential and parallel engines use the same configuration.
"""

from __future__ import annotations

import logging
import uuid

from datetime import datetime
from typing import Type

from backtester.models import Trade, TradeStatus
from backtester.portfolio import Portfolio
from strategies.base import BaseStrategy
from data.loader import DataLoader

import config


logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Sequential backtest engine.

    Flow:
        1. Get expiry dates
        2. Load one expiry partition
        3. Enter strategy
        4. Check stop loss
        5. Check normal exit
        6. Force-close at end of day
    """

    def __init__(
        self,
        strategy_cls: Type[BaseStrategy],
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        lot_size: int | None = None,
    ):
        self.strategy = strategy_cls()

        self.symbol = symbol

        self.start_date = start_date

        self.end_date = end_date

        self.lot_size = (
            lot_size
            if lot_size is not None
            else config.LOT_SIZE
        )

        self.portfolio = Portfolio()

        self.loader = DataLoader(
            symbol
        )

    def run(
        self,
    ) -> Portfolio:
        """
        Run the complete sequential backtest.
        """

        logger.info(
            "Starting backtest: %s | %s | %s -> %s",
            self.strategy.name,
            self.symbol,
            self.start_date.date(),
            self.end_date.date(),
        )

        expiry_dates = (
            self.loader.get_expiry_dates(
                self.start_date,
                self.end_date,
            )
        )

        logger.info(
            "Found %d expiry dates to process",
            len(expiry_dates),
        )

        for expiry in expiry_dates:

            # Reset stateful strategies between expiries.
            if hasattr(
                self.strategy,
                "reset",
            ):
                self.strategy.reset()

            self._process_expiry(
                expiry
            )

        logger.info(
            "Backtest complete. "
            "Trades: %d | "
            "Net P&L: ₹%.0f",
            len(
                self.portfolio.closed_trades
            ),
            self.portfolio.total_net_pnl,
        )

        return self.portfolio

    def _process_expiry(
        self,
        expiry: datetime,
    ) -> None:
        """
        Process one expiry-day partition.
        """

        snapshots = (
            self.loader.get_snapshots_for_expiry(
                expiry
            )
        )

        if not snapshots:

            logger.debug(
                "No data for expiry %s, skipping",
                expiry.date(),
            )

            return

        active_trade: Trade | None = None

        for index, snapshot in enumerate(
            snapshots
        ):
            is_last = (
                index
                == len(snapshots) - 1
            )

            # ── Entry ────────────────────────────────────────────────────

            if (
                active_trade is None
                and self.strategy.should_enter(
                    snapshot
                )
            ):

                legs = (
                    self.strategy.get_legs(
                        snapshot,
                        self.lot_size,
                    )
                )

                if not legs:
                    continue

                active_trade = Trade(
                    trade_id=str(
                        uuid.uuid4()
                    )[:8].upper(),

                    strategy_name=(
                        self.strategy.name
                    ),

                    symbol=self.symbol,

                    expiry=expiry,

                    entry_time=(
                        snapshot.timestamp
                    ),

                    legs=legs,
                )

                self.portfolio.add_trade(
                    active_trade
                )

            # ── Stop loss ────────────────────────────────────────────────

            if active_trade is not None:

                sl_trades = (
                    self.portfolio.check_stoploss(
                        snapshot
                    )
                )

                if active_trade in sl_trades:

                    self.portfolio.close_trade(
                        active_trade,
                        snapshot,
                        TradeStatus.CLOSED_STOPLOSS,
                    )

                    active_trade = None

                    continue

            # ── Strategy exit ────────────────────────────────────────────

            if (
                active_trade is not None
                and self.strategy.should_exit(
                    active_trade,
                    snapshot,
                )
            ):

                self.portfolio.close_trade(
                    active_trade,
                    snapshot,
                    TradeStatus.CLOSED_EOD,
                )

                active_trade = None

            # ── Final snapshot force-close ───────────────────────────────

            if (
                active_trade is not None
                and is_last
            ):

                self.portfolio.close_trade(
                    active_trade,
                    snapshot,
                    TradeStatus.CLOSED_EOD,
                )

                active_trade = None

        if active_trade is not None:

            logger.warning(
                "Trade %s still open after expiry loop",
                active_trade.trade_id,
            )