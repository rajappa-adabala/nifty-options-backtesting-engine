"""
data/loader.py

Memory-safe options data loader.

Preferred flow:

    data/raw/nifty_options.csv
        ↓ one-time preprocessing
    data/processed/nifty_options_expiry/
        ↓
    one Parquet file per expiry
        ↓
    load only requested expiry

This prevents every multiprocessing worker from loading
the full ~34M-row CSV.
"""

from __future__ import annotations

import json
import logging
import math
import random

from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import pandas as pd

from backtester.models import (
    MarketSnapshot,
    OptionContract,
    OptionType,
)

from utils.nse_utils import (
    get_atm_strike,
    get_expiries_in_range,
    get_strike_interval,
)

import config


logger = logging.getLogger(__name__)


# ── Paths ─────────────────────────────────────────────────────────────────────

RAW_PATH = (
    Path(config.DATA_RAW_DIR)
    / "nifty_options.csv"
)

PROCESSED_DIR = (
    Path(config.DATA_PROCESSED_DIR)
    / "nifty_options_expiry"
)

MANIFEST_PATH = (
    PROCESSED_DIR
    / "manifest.json"
)


# ── Synthetic cache ────────────────────────────────────────────────────────────

_SYNTHETIC_CACHE: dict = {}


class DataLoader:
    """
    Loads historical options data.

    Real-data mode uses one Parquet partition per expiry.

    Synthetic mode is used only when real processed data does not
    exist, or when allow_synthetic=True and an expiry is missing.
    """

    def __init__(
        self,
        symbol: str,
        allow_synthetic: bool = False,
    ):
        self.symbol = symbol
        self.allow_synthetic = allow_synthetic

        # Per-process snapshot cache.
        self._snapshot_cache: dict = {}

    # =========================================================================
    # Public API
    # =========================================================================

    def get_expiry_dates(
        self,
        start: datetime,
        end: datetime,
    ) -> List[datetime]:
        """
        Return available expiry dates between start and end.

        Preferred source:
            processed Parquet manifest

        If raw CSV exists but processed data is missing,
        raise an error instead of silently scanning the whole CSV.
        """

        if self._processed_data_available():
            return self._expiries_from_processed(
                start,
                end,
            )

        if RAW_PATH.exists():
            raise RuntimeError(
                "Raw CSV exists, but processed Parquet data is missing.\n"
                "Run this once from project root:\n"
                "python -m data.prepare_data"
            )

        logger.info(
            "No real data found — using synthetic expiry calendar"
        )

        return get_expiries_in_range(
            start,
            end,
            frequency="weekly",
            symbol=self.symbol,
        )

    def get_snapshots_for_expiry(
        self,
        expiry: datetime,
    ) -> List[MarketSnapshot]:
        """
        Load snapshots for exactly one expiry.
        """

        expiry_date = expiry.date()

        # ── Cache hit ─────────────────────────────────────────────────────

        if expiry_date in self._snapshot_cache:
            return self._snapshot_cache[
                expiry_date
            ]

        # ── Real processed data ───────────────────────────────────────────

        if self._processed_data_available():

            snapshots = (
                self._load_processed_expiry(
                    expiry
                )
            )

            if snapshots:

                self._snapshot_cache[
                    expiry_date
                ] = snapshots

                return snapshots


            # Missing expiry partition.

            if not self.allow_synthetic:

                logger.warning(
                    "No processed data for expiry %s",
                    expiry_date,
                )

                return []


        # ── Synthetic fallback ────────────────────────────────────────────

        snapshots = (
            self._generate_synthetic(
                expiry
            )
        )

        self._snapshot_cache[
            expiry_date
        ] = snapshots

        return snapshots


    # =========================================================================
    # Processed Parquet
    # =========================================================================

    def _processed_data_available(
        self,
    ) -> bool:
        """
        True only if processed directory and manifest exist.
        """

        return (
            PROCESSED_DIR.is_dir()
            and MANIFEST_PATH.exists()
        )


    def _load_manifest(
        self,
    ) -> dict:
        """
        Read processed-data manifest.
        """

        if not MANIFEST_PATH.exists():
            return {}

        return json.loads(
            MANIFEST_PATH.read_text(
                encoding="utf-8"
            )
        )


    def _expiries_from_processed(
        self,
        start: datetime,
        end: datetime,
    ) -> List[datetime]:
        """
        Return expiry dates from manifest without touching the raw CSV.
        """

        manifest = self._load_manifest()

        start_date = start.date()
        end_date = end.date()

        dates = []

        for value in manifest.get(
            "expiry_dates",
            [],
        ):

            try:
                expiry_date = (
                    pd.Timestamp(value).date()
                )

            except Exception:
                continue


            if (
                start_date
                <= expiry_date
                <= end_date
            ):
                dates.append(
                    expiry_date
                )


        dates.sort()


        logger.info(
            "Expiries from processed data in range: %s",
            dates,
        )


        return [
            datetime.combine(
                date_value,
                datetime.min.time(),
            )
            for date_value in dates
        ]


    def _expiry_path(
        self,
        expiry: datetime,
    ) -> Path:
        """
        Example:

        data/processed/nifty_options_expiry/
            2024-10-03.parquet
        """

        return (
            PROCESSED_DIR
            / f"{expiry.date().isoformat()}.parquet"
        )


    def _load_processed_expiry(
        self,
        expiry: datetime,
    ) -> List[MarketSnapshot]:
        """
        Load exactly one expiry partition.
        """

        path = self._expiry_path(
            expiry
        )


        if not path.exists():

            logger.warning(
                "Expiry partition missing: %s",
                path,
            )

            return []


        logger.info(
            "Loading expiry partition: %s",
            path,
        )


        df = pd.read_parquet(
            path,

            engine="pyarrow",

            columns=[
                "strike_price",
                "option_type",
                "expiry",
                "timestamp",
                "ltp",
                "volume",
                "oi",
                "underlying_spot_price",
                "iv",
            ],
        )


        if df.empty:
            return []


        # Ensure datetime columns are proper pandas timestamps.

        df["timestamp"] = pd.to_datetime(
            df["timestamp"]
        )

        df["expiry"] = pd.to_datetime(
            df["expiry"]
        )


        return self._dataframe_to_snapshots(
            df,
            expiry,
        )


    # =========================================================================
    # DataFrame → MarketSnapshot
    # =========================================================================

    def _dataframe_to_snapshots(
        self,
        df: pd.DataFrame,
        expiry: datetime,
    ) -> List[MarketSnapshot]:
        """
        Convert rows for one expiry into minute-level snapshots.
        """

        snapshots: List[
            MarketSnapshot
        ] = []


        for ts, group in df.groupby(
            "timestamp",
            sort=True,
        ):

            # ── Spot ──────────────────────────────────────────────────────

            spot_values = (
                group[
                    "underlying_spot_price"
                ]
                .dropna()
            )


            if spot_values.empty:
                continue


            spot = float(
                spot_values.iloc[0]
            )


            contracts = []


            # itertuples is faster than iterrows.

            for row in group.itertuples(
                index=False
            ):

                raw_type = (
                    str(row.option_type)
                    .upper()
                    .strip()
                )


                if raw_type == "CE":

                    option_type = (
                        OptionType.CE
                    )

                elif raw_type == "PE":

                    option_type = (
                        OptionType.PE
                    )

                else:

                    continue


                # ── LTP ──────────────────────────────────────────────────

                if pd.notna(row.ltp):

                    ltp = float(
                        row.ltp
                    )

                else:

                    ltp = 0.05


                ltp = max(
                    round(
                        ltp,
                        2,
                    ),
                    0.05,
                )


                # ── Optional values ───────────────────────────────────────

                iv = (
                    float(row.iv)
                    if pd.notna(row.iv)
                    else None
                )


                oi = (
                    float(row.oi)
                    if pd.notna(row.oi)
                    else None
                )


                volume = (
                    float(row.volume)
                    if pd.notna(row.volume)
                    else None
                )


                # ── Contract ─────────────────────────────────────────────

                contracts.append(

                    OptionContract(

                        symbol=self.symbol,

                        expiry=expiry,

                        strike=float(
                            row.strike_price
                        ),

                        option_type=option_type,

                        timestamp=ts,

                        open=ltp,

                        high=ltp,

                        low=ltp,

                        close=ltp,

                        ltp=ltp,

                        iv=iv,

                        oi=oi,

                        volume=volume,

                        underlying_price=spot,
                    )
                )


            # ── Snapshot ─────────────────────────────────────────────────

            if contracts:

                snapshots.append(

                    MarketSnapshot(

                        timestamp=ts,

                        expiry=expiry,

                        underlying_price=spot,

                        contracts=contracts,
                    )
                )


        snapshots.sort(
            key=lambda snapshot:
                snapshot.timestamp
        )


        if snapshots:

            logger.info(

                "Real data: expiry %s -> "
                "%d minute-snapshots, "
                "spot range: %.0f -> %.0f",

                expiry.date(),

                len(snapshots),

                min(
                    s.underlying_price
                    for s in snapshots
                ),

                max(
                    s.underlying_price
                    for s in snapshots
                ),
            )


        return snapshots


    # =========================================================================
    # Synthetic fallback
    # =========================================================================

    def _generate_synthetic(
        self,
        expiry: datetime,
    ) -> List[MarketSnapshot]:
        """
        Cached synthetic-data wrapper.
        """

        cache_key = (
            self.symbol,
            expiry,
        )


        if cache_key in _SYNTHETIC_CACHE:

            return _SYNTHETIC_CACHE[
                cache_key
            ]


        snapshots = (
            self._generate_synthetic_uncached(
                expiry
            )
        )


        _SYNTHETIC_CACHE[
            cache_key
        ] = snapshots


        return snapshots


    def _generate_synthetic_uncached(
        self,
        expiry: datetime,
    ) -> List[MarketSnapshot]:
        """
        GBM-based synthetic intraday options data.
        """

        from utils.options_math import (
            price as bs_price
        )

        from utils.nse_utils import (
            time_to_expiry_years
        )


        seed = (
            int(expiry.timestamp())
            % 10000
        )


        rng = random.Random(
            seed
        )


        base_spot = 18500.0

        daily_vol = 0.01

        risk_free = 0.065


        start_time = expiry.replace(
            hour=9,
            minute=15,
            second=0,
            microsecond=0,
        )


        end_time = expiry.replace(
            hour=15,
            minute=30,
            second=0,
            microsecond=0,
        )


        # ── Intraday timestamps ───────────────────────────────────────────

        timestamps = []

        t = start_time


        while t <= end_time:

            timestamps.append(
                t
            )

            t += timedelta(
                minutes=5
            )


        # ── Simulated spot path ───────────────────────────────────────────

        spots = [
            base_spot
        ]


        for _ in range(
            len(timestamps) - 1
        ):

            spots.append(

                spots[-1]

                * math.exp(

                    rng.gauss(
                        0,
                        1,
                    )

                    * daily_vol

                    / math.sqrt(
                        75
                    )
                )
            )


        interval = (
            get_strike_interval(
                self.symbol
            )
        )


        atm = get_atm_strike(
            base_spot,
            self.symbol,
        )


        strikes = [

            atm + interval * i

            for i in range(
                -8,
                9,
            )
        ]


        snapshots = []


        # ── Build option chain snapshots ─────────────────────────────────

        for ts, spot in zip(
            timestamps,
            spots,
        ):

            T = time_to_expiry_years(
                ts,
                end_time,
            )


            contracts = []


            for strike in strikes:


                for option_type in (
                    OptionType.CE,
                    OptionType.PE,
                ):


                    iv = (

                        0.16

                        + 0.05

                        * abs(
                            strike - spot
                        )

                        / spot
                    )


                    theoretical = (
                        bs_price(

                            spot,

                            strike,

                            T,

                            risk_free,

                            iv,

                            option_type.value,
                        )
                    )


                    noise = rng.gauss(

                        0,

                        max(
                            theoretical
                            * 0.02,
                            0.0001,
                        ),
                    )


                    ltp = max(

                        round(

                            theoretical
                            + noise,

                            2,
                        ),

                        0.05,
                    )


                    contracts.append(

                        OptionContract(

                            symbol=self.symbol,

                            expiry=expiry,

                            strike=strike,

                            option_type=option_type,

                            timestamp=ts,

                            open=ltp,

                            high=ltp * 1.05,

                            low=ltp * 0.95,

                            close=ltp,

                            ltp=ltp,

                            iv=round(
                                iv,
                                4,
                            ),

                            underlying_price=round(
                                spot,
                                2,
                            ),
                        )
                    )


            snapshots.append(

                MarketSnapshot(

                    timestamp=ts,

                    expiry=expiry,

                    underlying_price=round(
                        spot,
                        2,
                    ),

                    contracts=contracts,
                )
            )


        return snapshots