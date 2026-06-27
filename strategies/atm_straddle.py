"""
strategies/atm_straddle.py
--------------------------
Strategy: Sell ATM Straddle on Expiry Day

Logic:
  1. On expiry morning (first snapshot), identify ATM strike.
  2. Sell 1 lot of ATM CE + 1 lot of ATM PE at market open.
  3. Hold till EOD (or stop-loss hits — handled by Portfolio).
  4. Engine auto-closes at last snapshot of the day.

This is the bread-and-butter theta-decay play: on expiry day,
time value collapses fast. If spot doesn't move much, both legs
expire near worthless and we keep the premium.

Realistic edge cases handled:
  - If a leg has zero/missing LTP → skip that expiry
  - Enter only once per expiry (first snapshot)
"""

import logging
from typing import List

from strategies.base import BaseStrategy
from backtester.models import (
    Leg, MarketSnapshot, Trade,
    OptionType, TradeAction, OptionContract,
)

logger = logging.getLogger(__name__)


class ATMStraddleStrategy(BaseStrategy):
    """
    Sell ATM Straddle on Expiry Day.
    Enters on first snapshot of expiry day, exits at EOD.
    Stop-loss is handled externally by the Portfolio class.
    """

    def __init__(self, entry_snapshot_index: int = 0):
        """
        entry_snapshot_index: which snapshot to enter on (0 = first/open).
        Set to e.g. 1 to skip the first snapshot and enter on second.
        """
        self._entered = False
        self._entry_snapshot_index = entry_snapshot_index
        self._snapshot_count = 0

    @property
    def name(self) -> str:
        return "ATM_Straddle_Expiry"

    def should_enter(self, snapshot: MarketSnapshot) -> bool:
        """Enter on the configured snapshot (default: first one)."""
        if self._entered:
            return False
        result = self._snapshot_count >= self._entry_snapshot_index
        self._snapshot_count += 1
        return result

    def get_legs(self, snapshot: MarketSnapshot, lot_size: int) -> List[Leg]:
        """
        Build CE + PE SELL legs at ATM strike.
        Returns empty list if either leg has bad data.
        """
        atm_strike = snapshot.get_atm_strike()
        if atm_strike == 0.0:
            logger.warning(f"No contracts found in snapshot at {snapshot.timestamp}")
            return []

        ce_contract = snapshot.get_contract(atm_strike, OptionType.CE)
        pe_contract = snapshot.get_contract(atm_strike, OptionType.PE)

        if not ce_contract or not pe_contract:
            logger.warning(
                f"ATM strike {atm_strike} missing CE or PE at {snapshot.timestamp}. Skipping."
            )
            return []

        if ce_contract.ltp <= 0 or pe_contract.ltp <= 0:
            logger.warning(
                f"Zero LTP on ATM strike {atm_strike} at {snapshot.timestamp}. Skipping."
            )
            return []

        ce_leg = Leg(
            contract=ce_contract,
            action=TradeAction.SELL,
            qty=1,
            lot_size=lot_size,
            entry_price=ce_contract.ltp,
        )
        pe_leg = Leg(
            contract=pe_contract,
            action=TradeAction.SELL,
            qty=1,
            lot_size=lot_size,
            entry_price=pe_contract.ltp,
        )

        self._entered = True
        logger.info(
            f"Straddle entry: SELL {atm_strike} CE @ {ce_contract.ltp:.2f} "
            f"+ SELL {atm_strike} PE @ {pe_contract.ltp:.2f} "
            f"| Spot: {snapshot.underlying_price:.2f}"
        )
        return [ce_leg, pe_leg]

    def should_exit(self, trade: Trade, snapshot: MarketSnapshot) -> bool:
        """
        Default: no mid-day exit (let engine EOD-close or SL trigger).
        Override this for intraday exit strategies.
        """
        return False

    def reset(self):
        """Called between expiry runs by the engine."""
        self._entered = False
        self._snapshot_count = 0