"""
data/loader.py
--------------
Loads historical options data and serves MarketSnapshot objects to the engine.

Supports:
  1. Real minute-level CSV (nifty_options.csv format)
  2. Synthetic GBM data (fallback for missing expiries)

Your CSV columns:
  strike_price, option_type, expiry, timestamp, ltp, volume, oi,
  underlying_spot_price, strike_spot_diff, time_to_expiry, tte_years,
  IAO, pcr, flag, iv, delta, gamma, theta, vega, rho

Key fix: expiry dates are extracted ONLY from real data that exists in the file.
         No synthetic expiries are added unless --allow-synthetic flag is set.
"""

import os
import logging
import random
import math
from datetime import datetime, timedelta
from typing import List, Optional, Set

import pandas as pd
import numpy as np

from backtester.models import MarketSnapshot, OptionContract, OptionType
from utils.nse_utils import get_expiries_in_range, get_atm_strike, get_strike_interval
import config

logger = logging.getLogger(__name__)

REAL_DATA_PATH = os.path.join(config.DATA_RAW_DIR, "nifty_options.csv")


class DataLoader:
    """
    Loads data and serves MarketSnapshot objects per expiry.
    Real data is lazy-loaded once, then cached in memory.
    """

    def __init__(self, symbol: str, allow_synthetic: bool = False):
        self.symbol = symbol
        self.allow_synthetic = allow_synthetic  # if True, fills missing expiries with synthetic
        self._df: Optional[pd.DataFrame] = None
        self._real_expiry_dates: Optional[Set] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_expiry_dates(self, start: datetime, end: datetime) -> List[datetime]:
        """
        Returns expiry dates:
          - From real data if file exists (only dates actually in the CSV)
          - From NSE Thursday calendar if no real data (synthetic mode)
        """
        if self._real_data_available():
            return self._expiries_from_real_data(start, end)
        logger.info("No real data file found — using synthetic data mode")
        return get_expiries_in_range(start, end, frequency="weekly", symbol=self.symbol)

    def get_snapshots_for_expiry(self, expiry: datetime) -> List[MarketSnapshot]:
        """
        Returns minute-level MarketSnapshot list for one expiry day.
        Uses real data if available for that date, else synthetic (if allowed).
        """
        if self._real_data_available():
            snaps = self._load_real_snapshots(expiry)
            if snaps:
                return snaps
            if self.allow_synthetic:
                logger.debug(f"No real data for {expiry.date()}, using synthetic fallback")
                return self._generate_synthetic(expiry)
            logger.warning(f"No data for {expiry.date()} — skipping")
            return []
        return self._generate_synthetic(expiry)

    # ── Real data ─────────────────────────────────────────────────────────────

    def _real_data_available(self) -> bool:
        return os.path.exists(REAL_DATA_PATH)

    def _load_df(self) -> pd.DataFrame:
        """
        Lazy-load the full CSV once. Filters to expiry-day rows only
        to keep memory usage manageable even on 6GB files.
        """
        if self._df is not None:
            return self._df

        logger.info(f"Loading real data: {REAL_DATA_PATH}")
        logger.info("Large file — first load takes ~60-120 seconds, cached after that...")

        dtype_map = {
            "strike_price": "float32",
            "option_type": "category",
            "ltp": "float32",
            "volume": "float32",
            "oi": "float32",
            "underlying_spot_price": "float32",
            "iv": "float32",
            "delta": "float32",
            "gamma": "float32",
            "theta": "float32",
            "vega": "float32",
        }

        chunks = []
        rows_read = 0

        for chunk in pd.read_csv(
            REAL_DATA_PATH,
            chunksize=500_000,
            parse_dates=["expiry", "timestamp"],
            dtype={k: v for k, v in dtype_map.items()
                   if k not in ["expiry", "timestamp"]},
            low_memory=False,
        ):
            chunk.columns = [c.strip().lower() for c in chunk.columns]
            rows_read += len(chunk)

            # Keep only rows where timestamp date == expiry date (expiry day data)
            exp_date = chunk["expiry"].dt.date
            ts_date = chunk["timestamp"].dt.date
            chunk = chunk[ts_date == exp_date].copy()

            if not chunk.empty:
                chunk["option_type"] = chunk["option_type"].astype(str).str.upper().str.strip()
                chunks.append(chunk)

            if rows_read % 2_000_000 == 0:
                logger.info(f"  Read {rows_read:,} rows...")

        if not chunks:
            logger.warning("No expiry-day rows found in CSV!")
            self._df = pd.DataFrame()
            return self._df

        self._df = pd.concat(chunks, ignore_index=True)

        # Cache set of available expiry dates for fast lookup
        self._real_expiry_dates = set(self._df["expiry"].dt.date.unique())

        logger.info(
            f"Data loaded: {len(self._df):,} expiry-day rows | "
            f"{len(self._real_expiry_dates)} unique expiry dates | "
            f"Range: {min(self._real_expiry_dates)} → {max(self._real_expiry_dates)}"
        )
        return self._df

    def _expiries_from_real_data(self, start: datetime, end: datetime) -> List[datetime]:
        """Return sorted unique expiry datetimes within range from real data."""
        df = self._load_df()
        if df.empty:
            return []

        start_d, end_d = start.date(), end.date()

        # Get unique expiry datetimes (keeping time = 15:30:00 as in data)
        unique_expiries = (
            df[["expiry"]]
            .drop_duplicates()
            .copy()
        )
        unique_expiries["expiry_date"] = unique_expiries["expiry"].dt.date
        unique_expiries = unique_expiries[
            (unique_expiries["expiry_date"] >= start_d) &
            (unique_expiries["expiry_date"] <= end_d)
        ]
        unique_expiries = unique_expiries.sort_values("expiry_date")

        result = unique_expiries["expiry"].dt.to_pydatetime().tolist()
        logger.info(f"Expiries from real data in range: {[e.date() for e in result]}")
        return result

    def _load_real_snapshots(self, expiry: datetime) -> List[MarketSnapshot]:
        """
        Build per-minute MarketSnapshots from real data for one expiry date.
        """
        df = self._load_df()
        if df.empty:
            return []

        expiry_date = expiry.date()

        # Fast check — is this expiry in the file at all?
        if self._real_expiry_dates and expiry_date not in self._real_expiry_dates:
            return []

        # Filter to this expiry date's rows
        day_df = df[df["expiry"].dt.date == expiry_date].copy()
        if day_df.empty:
            return []

        snapshots = []

        for ts, group in day_df.groupby("timestamp"):
            spot_vals = group["underlying_spot_price"].dropna()
            if spot_vals.empty:
                continue
            spot = float(spot_vals.iloc[0])

            contracts = []
            for _, row in group.iterrows():
                raw_type = str(row["option_type"]).upper().strip()
                opt_type = OptionType.CE if raw_type == "CE" else OptionType.PE

                ltp = float(row["ltp"]) if pd.notna(row["ltp"]) else 0.05
                ltp = max(round(ltp, 2), 0.05)

                contracts.append(OptionContract(
                    symbol=self.symbol,
                    expiry=expiry,
                    strike=float(row["strike_price"]),
                    option_type=opt_type,
                    timestamp=ts,
                    open=ltp, high=ltp, low=ltp, close=ltp,
                    ltp=ltp,
                    iv=float(row["iv"]) if pd.notna(row.get("iv", float("nan"))) else None,
                    oi=float(row["oi"]) if pd.notna(row.get("oi", float("nan"))) else None,
                    volume=float(row["volume"]) if pd.notna(row.get("volume", float("nan"))) else None,
                    underlying_price=spot,
                ))

            if contracts:
                snapshots.append(MarketSnapshot(
                    timestamp=ts,
                    expiry=expiry,
                    underlying_price=spot,
                    contracts=contracts,
                ))

        snapshots.sort(key=lambda s: s.timestamp)
        logger.info(
            f"Real data: expiry {expiry_date} → "
            f"{len(snapshots)} minute-snapshots, "
            f"spot range: {min(s.underlying_price for s in snapshots):.0f}"
            f" → {max(s.underlying_price for s in snapshots):.0f}"
        )
        return snapshots

    # ── Synthetic fallback ────────────────────────────────────────────────────

    def _generate_synthetic(self, expiry: datetime) -> List[MarketSnapshot]:
        """GBM-based synthetic intraday data. Used when real data is unavailable."""
        from utils.options_math import price as bs_price
        from utils.nse_utils import time_to_expiry_years

        seed = int(expiry.timestamp()) % 10000
        rng = random.Random(seed)
        base_spot, daily_vol, risk_free = 18500.0, 0.01, 0.065

        timestamps, t = [], expiry.replace(hour=9, minute=15, second=0, microsecond=0)
        while t <= expiry.replace(hour=15, minute=30):
            timestamps.append(t)
            t += timedelta(minutes=5)

        spots = [base_spot]
        for _ in range(len(timestamps) - 1):
            spots.append(spots[-1] * math.exp(rng.gauss(0, 1) * daily_vol / math.sqrt(75)))

        interval = get_strike_interval(self.symbol)
        atm = get_atm_strike(base_spot, self.symbol)
        strikes = [atm + interval * i for i in range(-8, 9)]

        snapshots = []
        for ts, spot in zip(timestamps, spots):
            T = time_to_expiry_years(ts, expiry.replace(hour=15, minute=30))
            contracts = []
            for strike in strikes:
                for opt_type in [OptionType.CE, OptionType.PE]:
                    iv = 0.16 + 0.05 * abs(strike - spot) / spot
                    theo = bs_price(spot, strike, T, risk_free, iv, opt_type.value)
                    ltp = max(round(theo + rng.gauss(0, theo * 0.02), 2), 0.05)
                    contracts.append(OptionContract(
                        symbol=self.symbol, expiry=expiry, strike=strike,
                        option_type=opt_type, timestamp=ts,
                        open=ltp, high=ltp * 1.05, low=ltp * 0.95, close=ltp,
                        ltp=ltp, iv=round(iv, 4), underlying_price=round(spot, 2),
                    ))
            snapshots.append(MarketSnapshot(
                timestamp=ts, expiry=expiry,
                underlying_price=round(spot, 2), contracts=contracts,
            ))
        return snapshots