"""
backtester/engine.py
---------------------
Core backtesting engine.

Flow per expiry day:
  1. Load all market snapshots for that expiry date (intraday ticks or EOD)
  2. On first snapshot → ask strategy if we should enter
  3. Each subsequent snapshot → check SL, check strategy exit signal
  4. Final snapshot → force-close any open positions (EOD exit)
  5. Record trade, move to next expiry
"""

import logging
import uuid
from datetime import datetime
from typing import List, Type

import pandas as pd

from backtester.models import Trade, TradeStatus
from backtester.portfolio import Portfolio
from strategies.base import BaseStrategy
from data.loader import DataLoader
import config

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Drives the backtesting loop over a date range.

    Parameters
    ----------
    strategy_cls : BaseStrategy subclass
    symbol       : Underlying symbol (NIFTY, BANKNIFTY)
    start_date   : Backtest start date
    end_date     : Backtest end date
    """

    def __init__(
        self,
        strategy_cls: Type[BaseStrategy],
        symbol: str,
        start_date: datetime,
        end_date: datetime,
    ):
        self.strategy = strategy_cls()
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.portfolio = Portfolio()
        self.loader = DataLoader(symbol)

    def run(self) -> Portfolio:
        """
        Entry point — runs the full backtest.
        Returns the completed Portfolio object.
        """
        logger.info(
            f"Starting backtest: {self.strategy.name} | {self.symbol} | "
            f"{self.start_date.date()} → {self.end_date.date()}"
        )

        expiry_dates = self.loader.get_expiry_dates(self.start_date, self.end_date)
        logger.info(f"Found {len(expiry_dates)} expiry dates to process")

        for expiry in expiry_dates:
            # Reset stateful strategy between expiries
            if hasattr(self.strategy, "reset"):
                self.strategy.reset()
            self._process_expiry(expiry)

        logger.info(
            f"Backtest complete. "
            f"Trades: {len(self.portfolio.closed_trades)} | "
            f"Net P&L: ₹{self.portfolio.total_net_pnl:,.0f}"
        )
        return self.portfolio

    def _process_expiry(self, expiry: datetime) -> None:
        """
        Process all snapshots for a single expiry day.
        """
        snapshots = self.loader.get_snapshots_for_expiry(expiry)
        if not snapshots:
            logger.debug(f"No data for expiry {expiry.date()}, skipping.")
            return

        active_trade: Trade | None = None

        for i, snapshot in enumerate(snapshots):
            is_last = i == len(snapshots) - 1

            # ── Entry ────────────────────────────────────────────────────────
            if active_trade is None and self.strategy.should_enter(snapshot):
                legs = self.strategy.get_legs(snapshot, config.LOT_SIZE)
                if not legs:
                    continue

                trade = Trade(
                    trade_id=str(uuid.uuid4())[:8].upper(),
                    strategy_name=self.strategy.name,
                    symbol=self.symbol,
                    expiry=expiry,
                    entry_time=snapshot.timestamp,
                    legs=legs,
                )
                self.portfolio.add_trade(trade)
                active_trade = trade

            # ── Stop-loss check ───────────────────────────────────────────────
            if active_trade is not None:
                sl_trades = self.portfolio.check_stoploss(snapshot)
                if active_trade in sl_trades:
                    self.portfolio.close_trade(
                        active_trade, snapshot, TradeStatus.CLOSED_STOPLOSS
                    )
                    active_trade = None
                    continue  # No more action for this expiry after SL

            # ── Strategy exit signal ─────────────────────────────────────────
            if active_trade is not None and self.strategy.should_exit(active_trade, snapshot):
                self.portfolio.close_trade(
                    active_trade, snapshot, TradeStatus.CLOSED_EOD
                )
                active_trade = None

            # ── Force-close at last snapshot (EOD) ───────────────────────────
            if active_trade is not None and is_last:
                self.portfolio.close_trade(
                    active_trade, snapshot, TradeStatus.CLOSED_EOD
                )
                active_trade = None

        if active_trade is not None:
            # Safety net: trade opened but never closed (shouldn't happen)
            logger.warning(
                f"Trade {active_trade.trade_id} still open after expiry loop — force-closing"
            )
            