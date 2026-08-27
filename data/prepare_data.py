"""
data/prepare_data.py

One-time preprocessing script.

Converts:

    data/raw/nifty_options.csv

into expiry-specific Parquet files:

    data/processed/nifty_options_expiry/

Example:

    2024-10-03.parquet
    2024-10-10.parquet
    2024-10-17.parquet
    ...

Only expiry-day rows are retained because the current strategy
backtests expiry-day ATM straddles.

Run from project root:

    python -m data.prepare_data
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

import config


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(
        logging,
        config.LOG_LEVEL.upper(),
        logging.INFO,
    ),
    format="%(asctime)s %(levelname)s %(message)s",
)

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


# ── Columns required by the backtester ───────────────────────────────────────

USECOLS = [
    "strike_price",
    "option_type",
    "expiry",
    "timestamp",
    "ltp",
    "volume",
    "oi",
    "underlying_spot_price",
    "iv",
]


# Use float32 where possible to reduce memory usage.

DTYPES = {
    "strike_price": "float32",
    "option_type": "string",
    "ltp": "float32",
    "volume": "float32",
    "oi": "float32",
    "underlying_spot_price": "float32",
    "iv": "float32",
}


def prepare_data() -> None:

    # ── Validate source file ───────────────────────────────────────────────

    if not RAW_PATH.exists():
        raise FileNotFoundError(
            f"Raw CSV not found: {RAW_PATH}"
        )

    PROCESSED_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger.info(
        "Raw CSV: %s",
        RAW_PATH,
    )

    logger.info(
        "Processed output: %s",
        PROCESSED_DIR,
    )

    logger.info(
        "Starting CSV -> Parquet preprocessing..."
    )


    # ── Remove old generated partitions ───────────────────────────────────

    # This prevents old/incomplete partitions from being mixed
    # with a fresh conversion.

    for old_file in PROCESSED_DIR.glob(
        "*.parquet"
    ):
        old_file.unlink()


    # ── Counters ──────────────────────────────────────────────────────────

    rows_read = 0

    expiry_rows = 0

    expiry_dates: set[str] = set()


    # ── Stream the huge CSV ────────────────────────────────────────────────

    reader = pd.read_csv(
        RAW_PATH,

        chunksize=500_000,

        usecols=USECOLS,

        parse_dates=[
            "expiry",
            "timestamp",
        ],

        dtype=DTYPES,

        low_memory=True,
    )


    for chunk_number, chunk in enumerate(
        reader,
        start=1,
    ):

        rows_read += len(chunk)


        # Normalize column names.

        chunk.columns = [
            c.strip().lower()
            for c in chunk.columns
        ]


        # ── Keep expiry-day rows only ─────────────────────────────────────

        expiry_mask = (
            chunk["timestamp"].dt.date
            ==
            chunk["expiry"].dt.date
        )

        chunk = chunk.loc[
            expiry_mask
        ].copy()


        if chunk.empty:

            if rows_read % 2_000_000 == 0:

                logger.info(
                    "Read %s rows...",
                    f"{rows_read:,}",
                )

            continue


        # Normalize option type.

        chunk["option_type"] = (
            chunk["option_type"]
            .astype("string")
            .str.upper()
            .str.strip()
        )


        expiry_rows += len(chunk)


        # Temporary helper column.

        chunk["_expiry_date"] = (
            chunk["expiry"].dt.date
        )


        # ── Split chunk by expiry ─────────────────────────────────────────

        for expiry_date, part in chunk.groupby(
            "_expiry_date",
            sort=False,
        ):

            expiry_string = (
                expiry_date.isoformat()
            )

            expiry_dates.add(
                expiry_string
            )


            output_path = (
                PROCESSED_DIR
                / f"{expiry_string}.parquet"
            )


            part = part.drop(
                columns=["_expiry_date"]
            )


            # The same expiry may span multiple CSV chunks.
            #
            # If a partition already exists, merge the previous
            # portion with the new portion.

            if output_path.exists():

                existing = pd.read_parquet(
                    output_path
                )

                part = pd.concat(
                    [
                        existing,
                        part,
                    ],
                    ignore_index=True,
                )


            # ── Save Parquet ──────────────────────────────────────────────

            part.to_parquet(
                output_path,

                index=False,

                engine="pyarrow",

                compression="snappy",
            )


        # ── Progress logging ──────────────────────────────────────────────

        if (
            rows_read % 2_000_000 == 0
            or chunk_number == 1
        ):

            logger.info(
                "Read %s rows | "
                "kept %s expiry-day rows | "
                "%d expiries",
                f"{rows_read:,}",
                f"{expiry_rows:,}",
                len(expiry_dates),
            )


    # ── Create manifest ───────────────────────────────────────────────────

    sorted_expiries = sorted(
        expiry_dates
    )


    manifest = {

        "source": str(
            RAW_PATH.resolve()
        ),

        "processed_dir": str(
            PROCESSED_DIR.resolve()
        ),

        "source_rows": rows_read,

        "expiry_day_rows": expiry_rows,

        "expiry_count": len(
            sorted_expiries
        ),

        "expiry_dates": sorted_expiries,
    }


    MANIFEST_PATH.write_text(
        json.dumps(
            manifest,
            indent=2,
        ),
        encoding="utf-8",
    )


    # ── Summary ───────────────────────────────────────────────────────────

    logger.info(
        "Preprocessing complete."
    )

    logger.info(
        "Source rows: %s",
        f"{rows_read:,}",
    )

    logger.info(
        "Expiry-day rows: %s",
        f"{expiry_rows:,}",
    )

    logger.info(
        "Expiry dates: %d",
        len(sorted_expiries),
    )


    if sorted_expiries:

        logger.info(
            "Range: %s -> %s",
            sorted_expiries[0],
            sorted_expiries[-1],
        )


    logger.info(
        "Manifest: %s",
        MANIFEST_PATH,
    )


if __name__ == "__main__":
    prepare_data()