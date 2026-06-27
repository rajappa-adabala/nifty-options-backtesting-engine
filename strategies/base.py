"""
strategies/base.py
------------------
Abstract base class for all strategies.
All strategies must implement these three methods.
"""

from abc import ABC, abstractmethod
from typing import List

from backtester.models import Leg, MarketSnapshot, Trade


class BaseStrategy(ABC):
    """
    Every strategy must implement:
      - should_enter(snapshot)  → bool: should we open a position now?
      - get_legs(snapshot)      → List[Leg]: what legs to trade
      - should_exit(trade, snapshot) → bool: should we close before EOD?

    The engine calls these in order on each snapshot.
    Entry happens once per expiry day; after that only exit is checked.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    @abstractmethod
    def should_enter(self, snapshot: MarketSnapshot) -> bool:
        """
        Return True to open a position on this snapshot.
        Called on each snapshot until a trade is opened.
        """
        ...

    @abstractmethod
    def get_legs(self, snapshot: MarketSnapshot, lot_size: int) -> List[Leg]:
        """
        Return the list of Leg objects to execute.
        Called immediately after should_enter returns True.
        Entry prices are filled from snapshot.ltp here.
        """
        ...

    @abstractmethod
    def should_exit(self, trade: Trade, snapshot: MarketSnapshot) -> bool:
        """
        Return True to close the trade before EOD.
        Called on each snapshot after a trade is open.
        Return False to let the engine close at EOD.
        """
        ...